"""2つのsnapshotの差分を出し、逆向き差分を積む。

survey_catalog.py が取った snapshot 2つを突き合わせる。通信はしない。

snapshotは accession の辞書順で書かれているので、2つを同時に読み進めれば全体を
メモリへ載せずに突き合わせられる。public Run は3,000万件あり、2つ分を辞書へ載せる
実装は計算ノードでも現実的でない。

差分の3種:
  added     新しいsnapshotにだけあるRun。新規に公開されたもの
  removed   古いsnapshotにだけあるRun。カタログから消えたことだけを表し、
            取り下げか、一時的にカタログに載らなかっただけかは区別しない
  modified  両方にあるが内容が変わったRun。サイズ変化、Liteの後付け、実体の作り直し

**前回からの増加は新規Runだけでは測れない。** NCBIは既にあるRunのファイルを作り直す
ことがあり、公開Runの約9%（277万件）が `run_file_version` 2以上である。また SRA Lite は
NCBIが後から生成するため、既存Runにあとから付く。この2つを modified として拾う。

対象prefix:
  survey_catalog.py は `--prefix` で対象（SRRだけ、三極など）を選ぶ。対象の違う
  snapshot同士を比べると、対象の差がそのまま added・removed に出るので、
  両方に現れたprefixの集合が一致しないときは結果を残さずに止める。

逆向き差分:
  最新の完全な一覧を実体として持ち、過去へ遡る差分を積む。最新の参照が常にO(1)で済む。
  rdiff は「新しいsnapshotへ適用すると古いsnapshotになる」操作列である。

使い方:
  python3 snapshot_diff.py --old 2026-08-02-runs.jsonl.gz --new 2026-08-09-runs.jsonl.gz \\
      --output-directory snapshots --dry-run
"""

import argparse
import gzip
import json
import os
import sys
import time

from survey_catalog import (
    LITE_TO_NORMALIZED_RATIO,
    estimate_lite_bytes,
    format_binary_bytes,
    format_decimal_bytes,
    mib_to_bytes,
)

# 比較対象を絞る。取得時刻のような毎回変わる列を入れると全件modified扱いになる。
COMPARED_FIELDS = (
    "sra_normalized_mbytes",
    "has_sra_normalized",
    "has_sra_lite",
    # 形式の配列。有無フラグに出ない fastq や bam の増減はここでしか追えない。
    # survey_catalog 側で trim・重複除去・ソート済みなので、順序差は差分にならない。
    "datastore_filetype",
    "run_file_version",
    "releasedate",
)

# 1列だけの一覧は .txt、列を持つものは .tsv とする。拡張子で中身の形が分かる。
ADDED_RUNS_SUFFIX = "-added-runs.txt"
REMOVED_RUNS_SUFFIX = "-removed-runs.txt"
CHANGED_RUNS_SUFFIX = "-changed-runs.tsv"
CHANGED_RUNS_HEADER = "accession\tchanged_fields"


def changed_fields(before, after):
    """変化したフィールド名を返す。

    **両方が持つフィールドだけを比べる。** snapshotへ列を足した週は、旧snapshotに
    その列が無い。欠損を「値なし」として実値と比べると、変化していないRunまで
    modified になり、その週の差分が全件で埋まる。
    """
    return tuple(field for field in COMPARED_FIELDS
                 if field in before and field in after and before[field] != after[field])


def format_changed_row(change):
    """変化リストの1行。accessionだけでは何が起きたか分からないので種別を持たせる。"""
    return f"{change['accession']}\t{','.join(change['fields'])}"


def _advance(records, previous):
    """次のレコードを取る。昇順が崩れていたら止める。

    順序が崩れたsnapshotをそのまま処理すると、突き合わせが噛み合わず差分を
    取りこぼす。黙って間違った差分を出すより落とす。
    """
    record = next(records, None)
    if record is None:
        return None
    if previous is not None and record["accession"] <= previous["accession"]:
        raise ValueError("snapshotがaccession昇順でない: "
                         f"{previous['accession']} の次に {record['accession']}")
    return record


def diff_snapshots(old_records, new_records):
    """2つのsnapshotを突き合わせ、変化をaccession順に流す。"""
    old = _advance(old_records, None)
    new = _advance(new_records, None)
    while old is not None or new is not None:
        if new is None or (old is not None and old["accession"] < new["accession"]):
            yield {"change": "removed", "accession": old["accession"], "record": old}
            old = _advance(old_records, old)
        elif old is None or new["accession"] < old["accession"]:
            yield {"change": "added", "accession": new["accession"], "record": new}
            new = _advance(new_records, new)
        else:
            fields = changed_fields(old, new)
            if fields:
                yield {"change": "modified", "accession": new["accession"],
                       "fields": list(fields), "before": old, "after": new}
            old = _advance(old_records, old)
            new = _advance(new_records, new)


def reverse_operation(change):
    """変化1件を、新から旧へ戻す操作へ直す。"""
    kind = change.get("change")
    if kind == "added":
        return {"op": "remove", "accession": change["accession"]}
    if kind == "removed":
        return {"op": "insert", "record": change["record"]}
    if kind == "modified":
        return {"op": "replace", "record": change["before"]}
    raise ValueError(f"未知の変化種別: {kind!r}")


def operation_accession(operation):
    if operation["op"] == "remove":
        return operation["accession"]
    return operation["record"]["accession"]


def apply_reverse_diff(new_records, operations):
    """新しいsnapshotへ逆向き差分を適用し、古いsnapshotを復元する。"""
    record = next(new_records, None)
    operation = next(operations, None)
    while record is not None or operation is not None:
        if operation is None:
            yield record
            record = next(new_records, None)
            continue
        accession = operation_accession(operation)
        if record is None or accession < record["accession"]:
            if operation["op"] == "insert":
                yield operation["record"]
            operation = next(operations, None)
            continue
        if accession > record["accession"]:
            yield record
            record = next(new_records, None)
            continue
        if operation["op"] == "replace":
            yield operation["record"]
        elif operation["op"] == "insert":
            yield operation["record"]
        record = next(new_records, None)
        operation = next(operations, None)


class DiffTotals:
    """週あたりの変化を集計する。全変化をメモリに載せない。

    新規Runの容量と既存Runの容量変化を混ぜない。混ぜると「週にどれだけ増えたか」の
    内訳が消え、新規公開が増えたのか既存の作り直しが増えたのか判別できなくなる。
    """

    def __init__(self):
        self.added_run_count = 0
        self.added_normalized_bytes = 0
        self.added_lite_run_count = 0
        self.added_lite_bearing_normalized_bytes = 0
        self.removed_run_count = 0
        self.removed_normalized_bytes = 0
        self.removed_lite_run_count = 0
        self.removed_lite_bearing_normalized_bytes = 0
        self.gained_lite_bearing_normalized_bytes = 0
        self.modified_run_count = 0
        self.modified_by_field = {}
        self.existing_run_normalized_bytes_delta = 0
        self.runs_gaining_lite = 0
        self.runs_losing_lite = 0
        self.runs_with_regenerated_files = 0

    def add(self, change):
        kind = change["change"]
        if kind == "added":
            self._add_new_run(change["record"])
        elif kind == "removed":
            self._add_removed_run(change["record"])
        elif kind == "modified":
            self._add_modified_run(change)
        else:
            raise ValueError(f"未知の変化種別: {kind!r}")

    def _add_new_run(self, record):
        self.added_run_count += 1
        size = mib_to_bytes(record.get("sra_normalized_mbytes"))
        has_normalized = bool(record.get("has_sra_normalized"))
        if has_normalized:
            self.added_normalized_bytes += size
        if record.get("has_sra_lite"):
            self.added_lite_run_count += 1
            if has_normalized:
                self.added_lite_bearing_normalized_bytes += size

    def _add_removed_run(self, record):
        self.removed_run_count += 1
        size = mib_to_bytes(record.get("sra_normalized_mbytes"))
        has_normalized = bool(record.get("has_sra_normalized"))
        if has_normalized:
            self.removed_normalized_bytes += size
        if record.get("has_sra_lite"):
            self.removed_lite_run_count += 1
            if has_normalized:
                self.removed_lite_bearing_normalized_bytes += size

    def _add_modified_run(self, change):
        self.modified_run_count += 1
        before, after = change["before"], change["after"]
        for field in change["fields"]:
            self.modified_by_field[field] = self.modified_by_field.get(field, 0) + 1
        if "sra_normalized_mbytes" in change["fields"]:
            self.existing_run_normalized_bytes_delta += (
                mib_to_bytes(after.get("sra_normalized_mbytes"))
                - mib_to_bytes(before.get("sra_normalized_mbytes")))
        if "has_sra_lite" in change["fields"]:
            if after.get("has_sra_lite"):
                self.runs_gaining_lite += 1
                if after.get("has_sra_normalized"):
                    self.gained_lite_bearing_normalized_bytes += mib_to_bytes(
                        after.get("sra_normalized_mbytes"))
            else:
                self.runs_losing_lite += 1
        if "run_file_version" in change["fields"]:
            self.runs_with_regenerated_files += 1

    def report(self, lite_ratio=LITE_TO_NORMALIZED_RATIO):
        return {
            "added_run_count": self.added_run_count,
            "added_normalized_bytes": self.added_normalized_bytes,
            "added_lite_run_count": self.added_lite_run_count,
            "added_lite_bearing_normalized_bytes": self.added_lite_bearing_normalized_bytes,
            "added_lite_bytes_estimated": estimate_lite_bytes(
                self.added_lite_bearing_normalized_bytes, lite_ratio),
            "removed_run_count": self.removed_run_count,
            "removed_normalized_bytes": self.removed_normalized_bytes,
            "removed_lite_run_count": self.removed_lite_run_count,
            "removed_lite_bearing_normalized_bytes": self.removed_lite_bearing_normalized_bytes,
            "removed_lite_bytes_estimated": estimate_lite_bytes(
                self.removed_lite_bearing_normalized_bytes, lite_ratio),
            "gained_lite_bearing_normalized_bytes": self.gained_lite_bearing_normalized_bytes,
            "gained_lite_bytes_estimated": estimate_lite_bytes(
                self.gained_lite_bearing_normalized_bytes, lite_ratio),
            "modified_run_count": self.modified_run_count,
            "modified_by_field": dict(self.modified_by_field),
            "existing_run_normalized_bytes_delta": self.existing_run_normalized_bytes_delta,
            "runs_gaining_lite": self.runs_gaining_lite,
            "runs_losing_lite": self.runs_losing_lite,
            "runs_with_regenerated_files": self.runs_with_regenerated_files,
            "lite_ratio_used": lite_ratio,
        }


def track_prefixes(records, seen):
    for record in records:
        seen.add(record["accession"][:3])
        yield record


def check_same_prefixes(old_prefixes, new_prefixes):
    """集合は全件を読み終えるまで確定しないので、突き合わせの後で確かめる。"""
    if set(old_prefixes) != set(new_prefixes):
        raise ValueError(f"snapshotの対象prefixが違う: 前回 {','.join(sorted(old_prefixes))} / "
                         f"今回 {','.join(sorted(new_prefixes))}")


def iter_jsonl_gz(path):
    with gzip.open(path, "rt") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def announce(arguments):
    print("=== このscriptが行うこと ===")
    print(f"  読む      : {arguments.old}")
    print(f"              {arguments.new}")
    print( "  通信      : しない。既存のsnapshotだけを読む")
    print(f"  書く      : {arguments.output_directory}/ 配下の diff / rdiff / 対象一覧 / report")
    print( "  外部process: 起動しない")
    print(flush=True)


def run(arguments):
    for path in (arguments.old, arguments.new):
        if not os.path.exists(path):
            raise SystemExit(f"snapshotが無い: {path}")
    os.makedirs(arguments.output_directory, exist_ok=True)
    announce(arguments)
    if arguments.dry_run:
        return 0

    label = arguments.label
    directory = arguments.output_directory
    diff_path = os.path.join(directory, f"{label}-diff.jsonl.gz")
    rdiff_path = os.path.join(directory, f"{label}.rdiff.gz")
    added_path = os.path.join(directory, label + ADDED_RUNS_SUFFIX)
    removed_path = os.path.join(directory, label + REMOVED_RUNS_SUFFIX)
    changed_path = os.path.join(directory, label + CHANGED_RUNS_SUFFIX)
    report_path = os.path.join(directory, f"{label}-diff-report.json")

    totals = DiffTotals()
    started = time.monotonic()
    old_prefixes, new_prefixes = set(), set()
    with gzip.open(diff_path, "wt") as diff_handle, \
         gzip.open(rdiff_path, "wt") as rdiff_handle, \
         open(added_path, "w") as added_handle, \
         open(removed_path, "w") as removed_handle, \
         open(changed_path, "w") as changed_handle:
        changed_handle.write(CHANGED_RUNS_HEADER + "\n")
        changes = diff_snapshots(track_prefixes(iter_jsonl_gz(arguments.old), old_prefixes),
                                 track_prefixes(iter_jsonl_gz(arguments.new), new_prefixes))
        for change in changes:
            totals.add(change)
            diff_handle.write(json.dumps(change, ensure_ascii=False) + "\n")
            rdiff_handle.write(json.dumps(reverse_operation(change), ensure_ascii=False) + "\n")
            if change["change"] == "added":
                added_handle.write(change["accession"] + "\n")
            elif change["change"] == "removed":
                removed_handle.write(change["accession"] + "\n")
            elif change["change"] == "modified":
                changed_handle.write(format_changed_row(change) + "\n")

    try:
        check_same_prefixes(old_prefixes, new_prefixes)
    except ValueError as error:
        for path in (diff_path, rdiff_path, added_path, removed_path, changed_path):
            os.remove(path)
        raise SystemExit(f"{error}\n同じ --prefix で取ったsnapshot同士を比べる。")

    report = totals.report(lite_ratio=arguments.lite_ratio)
    report["label"] = label
    report["prefixes"] = sorted(new_prefixes)
    report["old_snapshot"] = arguments.old
    report["new_snapshot"] = arguments.new
    report["elapsed_seconds"] = round(time.monotonic() - started, 1)
    with open(report_path, "w") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

    added_bytes = report["added_normalized_bytes"]
    removed_bytes = report["removed_normalized_bytes"]
    delta = report["existing_run_normalized_bytes_delta"]
    print(f"\n=== 前回からの変化（所要 {report['elapsed_seconds']:.0f}s） ===")
    print(f"  対象                      : {', '.join(report['prefixes'])}")
    print(f"  新規に現れた Run          : {report['added_run_count']:>12,}")
    print(f"    うち SRA Lite を持つ    : {report['added_lite_run_count']:>12,}")
    print(f"    SRA Normalized の容量   : {format_decimal_bytes(added_bytes)} / "
          f"{format_binary_bytes(added_bytes)}")
    print(f"    SRA Lite の容量（推定） : "
          f"{format_decimal_bytes(report['added_lite_bytes_estimated'])}"
          f"  ※係数 {report['lite_ratio_used']} による推定")
    print(f"  消えた Run                : {report['removed_run_count']:>12,}")
    print(f"    うち SRA Lite を持つ    : {report['removed_lite_run_count']:>12,}")
    print(f"    SRA Normalized の容量   : {format_decimal_bytes(removed_bytes)} / "
          f"{format_binary_bytes(removed_bytes)}")
    print(f"    SRA Lite の容量（推定） : "
          f"{format_decimal_bytes(report['removed_lite_bytes_estimated'])}")
    print(f"  内容が変わった Run        : {report['modified_run_count']:>12,}")
    print(f"    Lite が後から付いた     : {report['runs_gaining_lite']:>12,}"
          f"（推定 {format_decimal_bytes(report['gained_lite_bytes_estimated'])}）")
    print(f"    実体が作り直された      : {report['runs_with_regenerated_files']:>12,}")
    print(f"    既存Runの容量増減       : {format_decimal_bytes(delta)}")
    print(f"\n  差分      : {diff_path}")
    print(f"  新規Run一覧: {added_path}")
    print(f"  消失Run一覧: {removed_path}")
    print(f"  逆向き差分: {rdiff_path}")
    print(f"  変化Run一覧: {changed_path}")
    print(f"  report    : {report_path}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--old", required=True, help="前回のsnapshot（jsonl.gz）")
    parser.add_argument("--new", required=True, help="今回のsnapshot（jsonl.gz）")
    parser.add_argument("--output-directory", required=True)
    parser.add_argument("--label", default=time.strftime("%Y-%m-%d"),
                        help="出力ファイル名の接頭辞。既定は実行日")
    parser.add_argument("--lite-ratio", type=float, default=LITE_TO_NORMALIZED_RATIO,
                        help="SRA Lite 容量の推定に使う係数")
    parser.add_argument("--dry-run", action="store_true", help="読み書きせず計画だけ表示する")
    arguments = parser.parse_args(argv)
    try:
        return run(arguments)
    except KeyboardInterrupt:
        print("\n中断した。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
