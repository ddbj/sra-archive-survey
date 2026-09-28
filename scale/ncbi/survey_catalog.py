"""公式Parquetカタログから public Run の一覧と SRA Normalized の規模を出す。

規模調査の最初の段で、snapshot_diff.py と scale_report.py がこの出力を読む。
NCBI の API は叩かず、公式Parquetだけを1スキャンして次を得る。
対象は `--prefix` で選ぶ。NCBIのカタログには SRR だけでなく、EBI由来の ERR と
DDBJ由来の DRR も入っている。三極合計なら `--prefix SRR ERR DRR`、SRRだけなら
`--prefix SRR` とする。既定値は置かない。前回と違う対象でsnapshotを取ると、
差分で対象の差がそのまま増減に見えるので、毎回明示させる。

  - public Run の accession 一覧（snapshot。次回との差分の元になる）
  - SRA Normalized の Run 数・総容量・coverage

Parquetの `mbytes` 列は SRA Normalized のサイズであり、**単位はMiBである**。
代表6 Runで確認済み（SRR000001: 312,527,083 B / 2^20 = 298.05 → mbytes 298）。

SRA Lite の**有無**は `datastore_filetype` の `run.zq` で判定できる。1,834万件を
実測と照合し、不一致7件（0.00004%）であることを確認済み。したがって
coverage はこのscriptだけで出せる。ただし**容量の実測値は出せない**（mbytes は
SRA Normalized のサイズのみ）。Lite の容量は、Normalized の容量に係数を掛けた推定で出す。

再開について:
  出力は accession の辞書順で書く。checkpoint に最後に書いた accession を残し、
  再実行時は `acc > <last>` を足して続きから取得する。途中で落ちても最初からやり直さない。

使い方:
  python3 survey_catalog.py --prefix SRR ERR DRR --output-directory snapshots --dry-run
  python3 survey_catalog.py --prefix SRR --output-directory snapshots
"""

import argparse
import dataclasses
import gzip
import json
import os
import re
import sys
import time

MIB = 1024 * 1024
# datastore_filetype の値。1,834万件の実測との照合で一致することを確認済み
# （run.zq は不一致7件、sra は不一致98件でいずれも lite_only 由来）。
NORMALIZED_FILETYPE = "sra"
LITE_FILETYPE = "run.zq"
# SRA Lite の容量はParquetに無いため、係数で推定する。
# 両形式のサイズが確定している17,544,315 Run（Normalized 17.321 PB / Lite 11.049 PB）の比。
# 元になったのはSRRだけである。ERR・DRRで同じ比になるかは確かめていないが、
# 別の係数を出す手段も無いので、同じ係数を当てて出力にその旨を書く。
LITE_TO_NORMALIZED_RATIO = 0.6379
LITE_RATIO_BASIS = "SRR"
PARQUET_GLOB = "s3://sra-pub-metadata-us-east-1/sra/metadata/*"
ARCHIVE_PREFIXES = ("DRR", "ERR", "SRR")
ACCESSION_PATTERN = re.compile(r"^(DRR|ERR|SRR)[0-9]+$")
CHECKPOINT_INTERVAL = 100_000


def validate_accession(value):
    """SQLへ埋め込む前に必ず通す。想定外の文字列を弾く。"""
    if not isinstance(value, str) or not ACCESSION_PATTERN.match(value):
        raise ValueError(f"Run accessionとして不正: {value!r}")
    return value


def normalize_prefixes(values):
    """対象prefixを検証し、snapshotと同じ辞書順の組にする。SQLへ埋め込むので必ず通す。"""
    unknown = [value for value in values if value not in ARCHIVE_PREFIXES]
    if unknown:
        raise ValueError(f"prefixは {', '.join(ARCHIVE_PREFIXES)} から選ぶ: {unknown!r}")
    if not values:
        raise ValueError("prefixを1つ以上指定する")
    return tuple(sorted(set(values)))


def accession_prefix(accession):
    return validate_accession(accession)[:3]


def mib_to_bytes(mbytes):
    """Parquetの mbytes（MiB単位）を byte へ直す。"""
    if mbytes is None:
        return 0
    if mbytes < 0:
        raise ValueError(f"mbytesが負: {mbytes}")
    return int(mbytes) * MIB


def estimate_lite_bytes(lite_bearing_normalized_bytes, ratio=LITE_TO_NORMALIZED_RATIO):
    """SRA Lite の容量を、Liteを持つRunのNormalized容量から推定する。

    掛ける相手は「Liteを持つRunのNormalized容量」であって全Normalized容量ではない。
    Liteを持たないRun（1,652,382件）の容量を分母へ含めると比の意味が変わる。

    この推定は過小へ倒れる可能性がある。比はaccession帯で0.39〜0.69とばらつき、
    新しい帯ほど高い一方、係数は古いaccession帯に偏った実測から出している。
    """
    if ratio < 0:
        raise ValueError(f"係数が負: {ratio}")
    return int(lite_bearing_normalized_bytes * ratio)


def has_filetype(filetypes, wanted):
    """datastore_filetype に目的の値があるか。末尾空白の異体があるのでstripする。"""
    if not filetypes:
        return False
    return any(str(x or "").strip() == wanted for x in filetypes)


def normalize_filetypes(filetypes):
    """datastore_filetype をsnapshotへ保存できる形へ整える。

    実データには末尾空白の異体（`fastq` と `fastq `）が混在し（fastq 16件・bam 13件・
    sff 2件）、配列の順序にも保証がない。そのまま保存すると、中身が同じRunが
    週次差分で変化と判定される。
    """
    if not filetypes:
        return []
    cleaned = {str(value or "").strip() for value in filetypes}
    cleaned.discard("")
    return sorted(cleaned)


def build_record(accession, normalized_mbytes, filetypes, releasedate, run_file_version):
    """snapshotの1行を組み立てる。

    週次差分で「何が変わったか」を判定するために、有無とサイズだけでなく
    releasedate・run_file_version・datastore_filetype も残す。形式の配列を持つことで、
    どの形式が増えたか減ったかを後から出せる。run_file_version は実体が作り直された
    回数で、公開Runの約9%（277万件）が2以上である。これが上がったRunは、
    accessionが同じままファイルが差し替わったことを意味する。
    """
    return {
        "accession": validate_accession(accession),
        "sra_normalized_mbytes": normalized_mbytes,
        "has_sra_normalized": has_filetype(filetypes, NORMALIZED_FILETYPE),
        "has_sra_lite": has_filetype(filetypes, LITE_FILETYPE),
        "datastore_filetype": normalize_filetypes(filetypes),
        "releasedate": releasedate.isoformat() if releasedate is not None else None,
        "run_file_version": run_file_version,
    }


def resume_predicate(last_accession):
    """再開用のSQL断片。accessionは検証済みのものだけを埋め込む。"""
    if last_accession is None:
        return ""
    return f" AND acc > '{validate_accession(last_accession)}'"


def format_decimal_bytes(total):
    return f"{total / 10**15:.3f} PB"


def format_binary_bytes(total):
    return f"{total / 2**50:.3f} PiB"


@dataclasses.dataclass
class Checkpoint:
    last_accession: str
    rows_written: int
    prefixes: tuple

    def serialize(self):
        return (f"{validate_accession(self.last_accession)}\t{self.rows_written}\t"
                f"{','.join(normalize_prefixes(self.prefixes))}\n")

    @classmethod
    def parse(cls, text):
        text = (text or "").strip()
        if not text:
            return None
        parts = text.split("\t")
        if len(parts) != 3 or not parts[1].isdigit():
            raise ValueError(f"checkpointの形式が不正: {text!r}")
        return cls(last_accession=validate_accession(parts[0]), rows_written=int(parts[1]),
                   prefixes=normalize_prefixes(parts[2].split(",")))


class _Counts:
    """有無の判定は datastore_filetype で行い、サイズでは行わない。mbytes は MiB へ
    丸められるため、1 MiB未満のRunは 0 になり「Normalizedなし」と誤判定される。
    """

    def __init__(self):
        self.public_run_total = 0
        self.sra_normalized_run_count = 0
        self.sra_normalized_bytes_total = 0
        self.sra_lite_run_count = 0
        self.lite_bearing_normalized_bytes_total = 0

    def add(self, normalized_bytes, has_normalized, has_lite):
        self.public_run_total += 1
        if has_normalized:
            self.sra_normalized_run_count += 1
            self.sra_normalized_bytes_total += normalized_bytes
        if has_lite:
            self.sra_lite_run_count += 1
            if has_normalized:
                self.lite_bearing_normalized_bytes_total += normalized_bytes

    def merge(self, other):
        self.public_run_total += other.public_run_total
        self.sra_normalized_run_count += other.sra_normalized_run_count
        self.sra_normalized_bytes_total += other.sra_normalized_bytes_total
        self.sra_lite_run_count += other.sra_lite_run_count
        self.lite_bearing_normalized_bytes_total += other.lite_bearing_normalized_bytes_total

    def report(self, lite_ratio):
        total = self.public_run_total
        return {
            "public_run_total": total,
            "sra_normalized_run_count": self.sra_normalized_run_count,
            "sra_normalized_coverage": (self.sra_normalized_run_count / total) if total else 0.0,
            "sra_normalized_bytes_total": self.sra_normalized_bytes_total,
            "sra_lite_run_count": self.sra_lite_run_count,
            "sra_lite_coverage": (self.sra_lite_run_count / total) if total else 0.0,
            "lite_bearing_normalized_bytes_total": self.lite_bearing_normalized_bytes_total,
            "sra_lite_bytes_estimated": estimate_lite_bytes(
                self.lite_bearing_normalized_bytes_total, lite_ratio),
        }


class SurveyTotals:
    """走査しながら、合計とprefixごとの内訳を集計する。全行をメモリに載せない。"""

    def __init__(self, prefixes):
        self.prefixes = normalize_prefixes(prefixes)
        self.total = _Counts()
        self.by_prefix = {prefix: _Counts() for prefix in self.prefixes}
        self.last_accession = None

    def add(self, accession, normalized_mbytes, filetypes=None):
        prefix = accession_prefix(accession)
        if prefix not in self.by_prefix:
            raise ValueError(f"対象外のprefixのRun: {accession}（対象 {self.prefixes}）")
        normalized_bytes = mib_to_bytes(normalized_mbytes)
        has_normalized = has_filetype(filetypes, NORMALIZED_FILETYPE)
        has_lite = has_filetype(filetypes, LITE_FILETYPE)
        self.total.add(normalized_bytes, has_normalized, has_lite)
        self.by_prefix[prefix].add(normalized_bytes, has_normalized, has_lite)
        self.last_accession = accession

    def merge(self, other):
        self.total.merge(other.total)
        for prefix, counts in other.by_prefix.items():
            self.by_prefix.setdefault(prefix, _Counts()).merge(counts)
        if other.last_accession is not None:
            self.last_accession = other.last_accession

    def report(self, lite_ratio=LITE_TO_NORMALIZED_RATIO):
        report = self.total.report(lite_ratio)
        report["lite_ratio_used"] = lite_ratio
        report["lite_ratio_basis"] = LITE_RATIO_BASIS
        report["prefixes"] = list(self.prefixes)
        report["by_prefix"] = {prefix: counts.report(lite_ratio)
                               for prefix, counts in sorted(self.by_prefix.items())}
        return report


def connect_duckdb():
    import duckdb

    connection = duckdb.connect()
    connection.execute("INSTALL httpfs; LOAD httpfs;")
    connection.execute("SET s3_region='us-east-1';")
    connection.execute("SET s3_access_key_id=''; SET s3_secret_access_key='';")
    return connection


def prefix_predicate(prefixes):
    return " OR ".join(f"starts_with(acc, '{prefix}')" for prefix in normalize_prefixes(prefixes))


def catalog_query(parquet_glob, prefixes, last_accession):
    return f"""
        SELECT acc, mbytes, datastore_filetype, releasedate, run_file_version
        FROM read_parquet('{parquet_glob}')
        WHERE ({prefix_predicate(prefixes)}) AND consent = 'public'{resume_predicate(last_accession)}
        ORDER BY acc
    """


def restore_totals(rows_path, prefixes):
    """既存出力からtotalsを復元する。checkpointと出力のずれを吸収する。"""
    totals = SurveyTotals(prefixes)
    if not os.path.exists(rows_path):
        return totals
    try:
        with gzip.open(rows_path, "rt") as handle:
            for line in handle:
                record = json.loads(line)
                filetypes = []
                if record.get("has_sra_normalized"):
                    filetypes.append(NORMALIZED_FILETYPE)
                if record.get("has_sra_lite"):
                    filetypes.append(LITE_FILETYPE)
                totals.add(record["accession"], record["sra_normalized_mbytes"], filetypes)
    except (EOFError, gzip.BadGzipFile, json.JSONDecodeError) as error:
        # 壊れた末尾だけを切り詰めて続ける手もあるが、gzip の途中から正しい境界を
        # 見つけるのは確実でない。取り直しても400秒程度なので最初からやり直させる。
        raise SystemExit(f"出力が途中で壊れている（強制終了で gzip の末尾が書かれなかった"
                         f"可能性がある）: {rows_path}\n"
                         f"このファイルと checkpoint を消して最初から取り直す。（{error}）")
    return totals


def announce(arguments, resuming, last_accession):
    print("=== このscriptが行うこと ===")
    print(f"  読む      : {arguments.parquet_glob}")
    print(f"  対象      : {', '.join(arguments.prefix)} の public Run")
    print( "  通信      : 公式Parquetへの匿名S3読み取りのみ。NCBIのAPIは叩かない")
    print(f"  書く      : {arguments.output_directory}/ 配下の rows / report / checkpoint")
    print( "  外部process: 起動しない")
    if resuming:
        print(f"  再開      : {last_accession} の続きから")
    else:
        print( "  再開      : なし（最初から）")
    print(flush=True)


def run(arguments):
    os.makedirs(arguments.output_directory, exist_ok=True)
    rows_path = os.path.join(arguments.output_directory, f"{arguments.label}-runs.jsonl.gz")
    checkpoint_path = os.path.join(arguments.output_directory, f"{arguments.label}-checkpoint.tsv")
    report_path = os.path.join(arguments.output_directory, f"{arguments.label}-report.json")

    checkpoint = None
    if os.path.exists(checkpoint_path):
        with open(checkpoint_path) as handle:
            try:
                checkpoint = Checkpoint.parse(handle.read())
            except ValueError as error:
                raise SystemExit(f"checkpointを読めない: {checkpoint_path}（{error}）\n"
                                 f"{checkpoint_path} と {rows_path} を消して最初から取り直す。")
    if not checkpoint and os.path.exists(rows_path):
        raise SystemExit(f"checkpointが無いのに出力がある: {rows_path}\n"
                         "最初のcheckpointより前に止まったので、どこまで書いたか分からない。"
                         "このファイルを消して最初から取り直す。")
    if checkpoint and not os.path.exists(rows_path):
        raise SystemExit(f"checkpointはあるが出力が無い: {rows_path}\n"
                         f"{checkpoint_path} を消してからやり直す。")
    if checkpoint and checkpoint.prefixes != arguments.prefix:
        raise SystemExit(f"checkpointの対象prefix（{','.join(checkpoint.prefixes)}）と"
                         f"今回の --prefix（{','.join(arguments.prefix)}）が違う。\n"
                         "別の --label か --output-directory を指定する。")

    announce(arguments, checkpoint is not None, checkpoint.last_accession if checkpoint else None)
    if arguments.dry_run:
        return 0

    prefixes = arguments.prefix
    totals = restore_totals(rows_path, prefixes) if checkpoint else SurveyTotals(prefixes)
    if checkpoint:
        print(f"  既存出力から {totals.total.public_run_total:,} 行を復元した", flush=True)

    connection = connect_duckdb()
    started = time.monotonic()
    written = 0
    session = SurveyTotals(prefixes)

    try:
        with gzip.open(rows_path, "at") as rows_handle:
            cursor = connection.execute(catalog_query(arguments.parquet_glob, prefixes, totals.last_accession))
            while True:
                batch = cursor.fetchmany(10_000)
                if not batch:
                    break
                for accession, mbytes, filetypes, releasedate, run_file_version in batch:
                    record = build_record(accession, mbytes, filetypes, releasedate, run_file_version)
                    rows_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                    session.add(accession, mbytes, filetypes)
                    written += 1
                    if written % CHECKPOINT_INTERVAL == 0:
                        rows_handle.flush()
                        with open(checkpoint_path, "w") as handle:
                            handle.write(Checkpoint(session.last_accession,
                                                    totals.total.public_run_total + written,
                                                    prefixes).serialize())
                        print(f"  {totals.total.public_run_total + written:,} 行  "
                              f"{time.monotonic() - started:.0f}s", flush=True)
    except KeyboardInterrupt:
        # checkpoint が書かれる前なら、どこまで書いたか分からない出力は再開に使えない。
        if not os.path.exists(checkpoint_path):
            os.remove(rows_path)
        raise

    totals.merge(session)
    report = totals.report()
    report["label"] = arguments.label
    report["parquet_glob"] = arguments.parquet_glob
    report["elapsed_seconds"] = round(time.monotonic() - started, 1)
    with open(report_path, "w") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    if totals.last_accession:
        with open(checkpoint_path, "w") as handle:
            handle.write(Checkpoint(totals.last_accession, totals.total.public_run_total,
                                    prefixes).serialize())

    normalized_bytes = report["sra_normalized_bytes_total"]
    print(f"\n=== 結果（所要 {report['elapsed_seconds']:.0f}s） ===")
    print(f"  対象                         : {', '.join(report['prefixes'])}")
    print(f"  public Run 総数              : {report['public_run_total']:>14,}")
    print(f"  SRA Normalized を持つ Run    : {report['sra_normalized_run_count']:>14,}")
    print(f"  SRA Normalized の coverage   : {report['sra_normalized_coverage']:>14.4%}")
    print(f"  SRA Normalized の合計        : {normalized_bytes:>14,} bytes")
    print(f"                                 {format_decimal_bytes(normalized_bytes)} / "
          f"{format_binary_bytes(normalized_bytes)}")
    print(f"  SRA Lite を持つ Run          : {report['sra_lite_run_count']:>14,}")
    print(f"  SRA Lite の coverage         : {report['sra_lite_coverage']:>14.4%}")
    lite_estimated = report["sra_lite_bytes_estimated"]
    print(f"  SRA Lite の容量（推定）      : {lite_estimated:>14,} bytes")
    print(f"                                 {format_decimal_bytes(lite_estimated)} / "
          f"{format_binary_bytes(lite_estimated)}")
    print(f"    ※ 実測ではない。係数 {report['lite_ratio_used']} を掛けた推定である。"
          "Parquetは Lite のサイズを持たない")
    if set(report["prefixes"]) - {LITE_RATIO_BASIS}:
        print(f"    ※ 係数は {LITE_RATIO_BASIS} の実測から出した値で、他のprefixにも同じ値を当てている")
    if len(report["prefixes"]) > 1:
        print("\n  --- prefixごとの内訳 ---")
        for prefix, part in report["by_prefix"].items():
            print(f"  {prefix}  Run {part['public_run_total']:>12,}  "
                  f"Normalized {part['sra_normalized_coverage']:>8.4%} "
                  f"{format_decimal_bytes(part['sra_normalized_bytes_total'])}  "
                  f"Lite {part['sra_lite_coverage']:>8.4%} "
                  f"推定 {format_decimal_bytes(part['sra_lite_bytes_estimated'])}")
    print(f"\n  Run一覧 : {rows_path}")
    print(f"  report  : {report_path}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prefix", required=True, nargs="+", choices=ARCHIVE_PREFIXES,
                        help="対象のRun accession prefix。三極合計なら SRR ERR DRR")
    parser.add_argument("--output-directory", required=True)
    parser.add_argument("--label", default=time.strftime("%Y-%m-%d"),
                        help="出力ファイル名の接頭辞。既定は実行日")
    parser.add_argument("--parquet-glob", default=PARQUET_GLOB)
    parser.add_argument("--dry-run", action="store_true", help="通信せず計画だけ表示する")
    arguments = parser.parse_args(argv)
    arguments.prefix = normalize_prefixes(arguments.prefix)
    try:
        return run(arguments)
    except KeyboardInterrupt:
        print("\n中断した。同じコマンドで続きから再開できる"
              f"（{CHECKPOINT_INTERVAL:,} 行ごとの checkpoint から）。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
