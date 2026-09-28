"""snapshotと差分から、形式ごとの規模と増減を出す。

`datastore_filetype` はそのRunが持つ全形式の配列である。survey_catalog.py が
snapshotへ配列を保存しているので、後から形式単位で数え直せる。規模レポートの
定型集計には含めず、必要になったときにこのscriptで出す。

  形式の例: sra（SRA Normalized）、run.zq（SRA Lite）、fastq / bam / sff（投稿形式）、
            realign / activ_sars2_vcf / wgmlst_sig（NCBIが作る派生物）

使い方:
  python3 filetype_stats.py --snapshot 2026-08-22-runs.jsonl.gz
  python3 filetype_stats.py --diff 2026-08-22-diff.jsonl.gz
"""

import argparse
import gzip
import json
import os
import sys

from survey_catalog import format_decimal_bytes, mib_to_bytes


def _filetypes(record):
    return record.get("datastore_filetype") or []


def count_filetypes(records):
    """形式ごとの Run数と、その形式を持つRunのNormalized容量を数える。

    容量はあくまで「その形式を持つRunの SRA Normalized のサイズ」であって、
    その形式自体のサイズではない。Parquetは SRA Normalized のサイズしか持たない。
    """
    stats = {}
    for record in records:
        size = mib_to_bytes(record.get("sra_normalized_mbytes"))
        has_normalized = bool(record.get("has_sra_normalized"))
        for filetype in _filetypes(record):
            entry = stats.setdefault(filetype, {"run_count": 0, "normalized_bytes": 0})
            entry["run_count"] += 1
            if has_normalized:
                entry["normalized_bytes"] += size
    return stats


def count_filetype_changes(changes):
    """形式ごとに、どこで増減したかを数える。

    新規Run由来（added_runs）と既存Runへの後付け（gained）を混ぜない。前者は
    Runごと増えた分、後者はNCBIが既存Runへ後から作った分で、意味が違う。
    """
    stats = {}

    def entry(filetype):
        return stats.setdefault(
            filetype, {"added_runs": 0, "removed_runs": 0, "gained": 0, "lost": 0})

    for change in changes:
        kind = change["change"]
        if kind == "added":
            for filetype in _filetypes(change["record"]):
                entry(filetype)["added_runs"] += 1
        elif kind == "removed":
            for filetype in _filetypes(change["record"]):
                entry(filetype)["removed_runs"] += 1
        elif kind == "modified" and "datastore_filetype" in change.get("fields", []):
            before = set(_filetypes(change["before"]))
            after = set(_filetypes(change["after"]))
            for filetype in after - before:
                entry(filetype)["gained"] += 1
            for filetype in before - after:
                entry(filetype)["lost"] += 1
    return stats


def iter_jsonl_gz(path):
    with gzip.open(path, "rt") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def print_snapshot_stats(stats):
    print(f"{'filetype':<24}{'runs':>14}{'normalized':>16}")
    for filetype, entry in sorted(stats.items(), key=lambda item: -item[1]["run_count"]):
        print(f"{filetype:<24}{entry['run_count']:>14,}"
              f"{format_decimal_bytes(entry['normalized_bytes']):>16}")


def print_change_stats(stats):
    print(f"{'filetype':<24}{'新規Run':>12}{'消失Run':>12}{'後付け':>10}{'消滅':>10}")
    for filetype, entry in sorted(stats.items(), key=lambda item: -item[1]["added_runs"]):
        print(f"{filetype:<24}{entry['added_runs']:>12,}{entry['removed_runs']:>12,}"
              f"{entry['gained']:>10,}{entry['lost']:>10,}")


def run(arguments):
    path = arguments.snapshot or arguments.diff
    if not os.path.exists(path):
        raise SystemExit(f"ファイルが無い: {path}")
    if arguments.snapshot:
        print_snapshot_stats(count_filetypes(iter_jsonl_gz(path)))
    else:
        print_change_stats(count_filetype_changes(iter_jsonl_gz(path)))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--snapshot", help="{label}-runs.jsonl.gz")
    group.add_argument("--diff", help="{label}-diff.jsonl.gz")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
