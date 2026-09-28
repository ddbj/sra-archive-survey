"""NCBI のカタログと本家（EBI / DDBJ）の保有状況を突き合わせる。

同じ Run でも、どのアーカイブが実体を持つかは揃っていない。NCBI の公式Parquetは
三極すべての Run を収めるが、本家にあって NCBI に無いもの、その逆がどちらにもある。
このscriptは両者の差を出し、本家にしか無い accession を理由つきで一覧にする。

比較の単位は対象で変わる。

  EBI  : ERR 単位。EBIのFTP実測（accession と bytes のTSV）と突き合わせる
  DDBJ : DRX 単位。DDBJのFTPから得た DRX 一覧と突き合わせ、DRR は別途 API で引く

NCBI カタログの全件を読むため、EBI で約20分、DDBJ で約25分かかる。
本家側の一覧が部分集合のときは「NCBI にあって本家に無い」が意味を持たない
（渡さなかった分が全部そこへ数えられる）。

**NCBI 側で `datastore_filetype` が NULL の行がある。** `list_contains(NULL,'sra')`
は `false` ではなく `NULL` を返すため、`NOT has_sra` だけで分類すると黙って漏れる。
理由の区分はそこを分けてある（accession_index.classify_missing を参照）。

使い方:
  python3 compare_archives.py --archive ebi --peer ebi_sizes.tsv \\
      --output ebi-only.tsv --dry-run
  python3 compare_archives.py --archive ddbj --peer ddbj_drx.txt --output ddbj-only.tsv
"""

import argparse
import csv
import datetime
import os
import sys
import time

from accession_index import classify_missing

PARQUET_GLOB = "s3://sra-pub-metadata-us-east-1/sra/metadata/*"
ARCHIVES = {
    "ebi": {"run_prefix": "ERR", "key": "acc", "peer_has_size": True,
            "key_column": "accession", "size_column": "ebi_bytes"},
    "ddbj": {"run_prefix": "DRR", "key": "experiment", "peer_has_size": False,
             "key_column": "experiment", "size_column": None},
}


def connect_duckdb():
    import duckdb

    connection = duckdb.connect()
    connection.execute("INSTALL httpfs; LOAD httpfs;")
    connection.execute("SET s3_region='us-east-1';")
    connection.execute("SET s3_access_key_id=''; SET s3_secret_access_key='';")
    return connection


def catalog_query(run_prefix, parquet_glob):
    """NCBI側の行を取る。

    `mbytes` は INTEGER なので、byte へ直す前に BIGINT へ寄せる。掛け算のまま
    扱うと 2 GiB を超える Run でオーバーフローする。
    """
    return f"""
        SELECT acc,
               experiment,
               CAST(mbytes AS BIGINT) AS mbytes,
               datastore_filetype AS filetypes,
               consent,
               releasedate
        FROM read_parquet('{parquet_glob}')
        WHERE starts_with(acc, '{run_prefix}') AND consent = 'public'
    """


def load_peer(paths, has_size):
    """本家側の一覧を読む。1列（accession）か2列（accession, bytes）を受ける。

    複数ファイルを受けるのは、EBIの実測が本走査と再試行の2ファイルに分かれて
    出るためである。同じ accession が異なるサイズで現れたら止める。後勝ちで
    上書きすると、どちらが正しいか分からないまま集計が進む。
    """
    peer = {}
    for path in paths:
        with open(path) as handle:
            for line in handle:
                parts = line.rstrip("\n").split("\t")
                key = parts[0].strip()
                if not key or key in ("accession", "drx"):
                    continue
                size = None
                if has_size and len(parts) > 1 and parts[1].strip().isdigit():
                    size = int(parts[1])
                if key in peer and peer[key] != size:
                    raise ValueError(f"{key} のサイズが食い違う: {peer[key]} と {size}（{path}）")
                peer[key] = size
    return peer


def _has_sra(filetypes):
    return filetypes is not None and "sra" in [str(f or "").strip() for f in filetypes]


def split_catalog(rows, peer, key_field):
    """カタログ行を、本家側に現れるものと NCBI にしか無いものへ分ける。

    `rows` は (acc, experiment, mbytes, filetypes, consent, releasedate) の並び。`key_field` は比較の
    単位で、ERR なら "acc"、DRR なら "experiment"（DRX）を使う。

    NCBI にしか無いものは (key, acc) で返す。DRX 単位で比べても、1 DRX に
    ぶら下がる複数の DRR を全部残すためである。
    """
    catalog = {}
    ncbi_only = []
    for acc, experiment, _mbytes, filetypes, consent, releasedate in rows:
        key = acc if key_field == "acc" else experiment
        if key is None:
            continue
        has_sra = _has_sra(filetypes)
        if key in peer:
            previous = catalog.get(key)
            if previous is None or (not previous[2] and has_sra):
                catalog[key] = (acc, filetypes, has_sra, consent, releasedate)
        elif has_sra:
            ncbi_only.append((key, acc))
    return catalog, ncbi_only


def find_missing(peer, catalog):
    """本家にあって NCBI に無いものを、理由つきで accession 順に返す。

    カタログに行があるものは consent と releasedate も添える。
    """
    rows = []
    for key, size in peer.items():
        entry = catalog.get(key)
        reason = classify_missing(None if entry is None else {"acc": entry[0]},
                                  None if entry is None else entry[1])
        if reason is not None:
            consent, releasedate = (None, None) if entry is None else entry[3:5]
            rows.append((key, size, reason, consent, releasedate))
    rows.sort(key=lambda missing: missing[0])
    return rows


def format_date(value):
    if value is None:
        return ""
    if isinstance(value, datetime.datetime):
        value = value.date()
    return value.isoformat()


def missing_header(spec):
    size = [spec["size_column"]] if spec["size_column"] else []
    return [spec["key_column"], *size, "reason", "ncbi_consent", "ncbi_releasedate"]


def missing_row(spec, missing):
    key, size, reason, consent, releasedate = missing
    sizes = ["" if size is None else size] if spec["size_column"] else []
    return [key, *sizes, reason, consent or "", format_date(releasedate)]


def write_tsv(path, header, rows):
    """TSV を LF 改行で書く。

    csv.writer の既定は CRLF で、最終列に \r が残る。公開データを cut や comm で
    扱ったとき、見た目が同じ accession が一致しなくなる。
    """
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def announce(arguments, peer_count):
    print("=== このscriptが行うこと ===")
    print(f"  読む      : {arguments.parquet_glob}")
    print(f"              {', '.join(arguments.peer)}（{peer_count:,} 件）")
    print( "  通信      : 公式Parquetへの匿名S3読み取りのみ。本家へは接続しない")
    print(f"  書く      : {arguments.output}")
    if arguments.ncbi_only_output:
        print(f"              {arguments.ncbi_only_output}（NCBIにしか無いもの）")
    print( "  外部process: 起動しない")
    print(flush=True)


def run(arguments):
    spec = ARCHIVES[arguments.archive]
    for path in arguments.peer:
        if not os.path.exists(path):
            raise SystemExit(f"本家側の一覧が無い: {path}")
    peer = load_peer(arguments.peer, spec["peer_has_size"])
    announce(arguments, len(peer))
    if arguments.dry_run:
        return 0

    started = time.monotonic()
    connection = connect_duckdb()
    cursor = connection.execute(catalog_query(spec["run_prefix"], arguments.parquet_glob))

    def rows():
        while True:
            batch = cursor.fetchmany(50_000)
            if not batch:
                return
            yield from batch

    catalog, ncbi_only = split_catalog(rows(), peer, spec["key"])
    missing = find_missing(peer, catalog)

    write_tsv(arguments.output, missing_header(spec),
              (missing_row(spec, row) for row in missing))

    if arguments.ncbi_only_output:
        if spec["key"] == "experiment":
            write_tsv(arguments.ncbi_only_output, ["drx", "drr"], sorted(ncbi_only))
        else:
            write_tsv(arguments.ncbi_only_output, ["accession"],
                      ([acc] for _, acc in sorted(ncbi_only)))

    by_reason = {}
    total_bytes = 0
    for _, size, reason, _consent, _releasedate in missing:
        by_reason[reason] = by_reason.get(reason, 0) + 1
        if isinstance(size, int):
            total_bytes += size

    print(f"\n=== 結果（所要 {time.monotonic() - started:.0f}s） ===")
    print(f"  本家にあって NCBI に無い : {len(missing):,}")
    for reason, count in sorted(by_reason.items(), key=lambda kv: -kv[1]):
        print(f"    {reason:<30} {count:>8,}")
    if total_bytes:
        print(f"  本家側の容量             : {total_bytes:,} bytes "
              f"= {total_bytes / 10**12:.2f} TB")
    print(f"  NCBI にあって本家に無い  : {len(ncbi_only):,}")
    print( "    ※ 本家側へ完全な一覧を渡したときだけ意味を持つ。部分集合を渡すと"
           "残り全部がここに数えられる")
    print(f"  出力                     : {arguments.output}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--archive", required=True, choices=sorted(ARCHIVES),
                        help="突き合わせる本家")
    parser.add_argument("--peer", required=True, nargs="+",
                        help="本家側の一覧。複数可。ebi は accession<TAB>bytes、ddbj は DRX 一覧")
    parser.add_argument("--ncbi-only-output",
                        help="NCBIにしか無いものの書き出し先。ERRでは約870万行になる")
    parser.add_argument("--output", required=True, help="差分の書き出し先（TSV）")
    parser.add_argument("--parquet-glob", default=PARQUET_GLOB)
    parser.add_argument("--dry-run", action="store_true", help="通信せず計画だけ表示する")
    arguments = parser.parse_args(argv)
    try:
        return run(arguments)
    except KeyboardInterrupt:
        print("\n中断した。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
