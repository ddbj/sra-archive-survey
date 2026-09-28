#!/usr/bin/env python3
"""ERR の2回分のスナップショットを比較し、ファイル件数とサイズの増加を出す。

なぜ差分なのか:
  ENA は過去に公開された Run へ後からファイルを足す。/vol1/err/ERR350/000/ の BAM は
  2024-01-09 付だが、対応する Run の first_public は 2019-09-25 である。しかも
  last_updated も更新されない（2019年公開でBAMを持つ 1,363件のうち 849件は
  last_updated が2018〜2019年、すなわちBAM生成開始前のままだった）。
  したがって first_public でも last_updated でも増加を追えず、全件スナップショットの
  差分だけが確実な手段になる。

整数で数えること:
  総容量は 10**16 台に達し、倍精度の整数精度限界 2**53 を超える。float を使うと
  合計が静かにずれる。Python の int は多倍長なのでそのまま使える。
"""

import gzip
import sys
from dataclasses import dataclass, field

FAMILIES = ("fastq", "submitted", "sra", "bam")


@dataclass
class Snapshot:
    """accession -> (fastq_bytes, submitted_bytes, sra_bytes, bam_bytes) の生文字列。

    値は ';' 区切りで複数ファイルのサイズが入る。空文字はその系統を持たないこと。
    """

    rows: dict


@dataclass
class DiffResult:
    new_runs: list = field(default_factory=list)
    removed_runs: list = field(default_factory=list)
    changed_runs: list = field(default_factory=list)
    added_files: int = 0
    added_bytes: int = 0
    added_by_family: dict = field(default_factory=dict)


def _parse(raw):
    """';' 区切りのサイズ列を (ファイル数, バイト合計) にする。"""
    if not raw:
        return 0, 0
    count = 0
    total = 0
    for part in raw.split(";"):
        if part:
            count += 1
            total += int(part)
    return count, total


def _measure(cols):
    return [_parse(cols[i]) for i in range(len(FAMILIES))]


def diff_snapshots(before, after):
    result = DiffResult(added_by_family={name: (0, 0) for name in FAMILIES})
    per_family = {name: [0, 0] for name in FAMILIES}

    empty = ("",) * len(FAMILIES)
    for acc in sorted(set(before.rows) | set(after.rows)):
        old = before.rows.get(acc)
        new = after.rows.get(acc)

        if old is None:
            result.new_runs.append(acc)
        elif new is None:
            result.removed_runs.append(acc)
        elif old != new:
            result.changed_runs.append(acc)
        else:
            continue

        for i, name in enumerate(FAMILIES):
            old_c, old_b = _parse((old or empty)[i])
            new_c, new_b = _parse((new or empty)[i])
            per_family[name][0] += new_c - old_c
            per_family[name][1] += new_b - old_b

    for name in FAMILIES:
        count, total = per_family[name]
        result.added_by_family[name] = (count, total)
        result.added_files += count
        result.added_bytes += total

    return result


def load_snapshot(paths):
    """survey_sizes.sh が残す年次TSV（gzip）を読み込む。

    列は run_accession, fastq_bytes, submitted_bytes, sra_bytes, bam_bytes。
    """
    rows = {}
    for path in paths:
        with gzip.open(path, "rt") as fh:
            header = fh.readline()
            if not header.startswith("run_accession"):
                raise SystemExit(f"予期しないヘッダ: {path}")
            for line in fh:
                cols = line.rstrip("\n").split("\t")
                cols += [""] * (5 - len(cols))
                rows[cols[0]] = tuple(cols[1:5])
    return Snapshot(rows)


def main():
    if len(sys.argv) < 3 or "--" not in sys.argv:
        raise SystemExit(
            "使い方: scale_diff.py <前回のtsv.gz...> -- <今回のtsv.gz...>"
        )
    sep = sys.argv.index("--")
    before = load_snapshot(sys.argv[1:sep])
    after = load_snapshot(sys.argv[sep + 1 :])

    r = diff_snapshots(before, after)
    print(f"前回 {len(before.rows):,} Run / 今回 {len(after.rows):,} Run")
    print(f"新規Run     : {len(r.new_runs):,}")
    print(f"消滅Run     : {len(r.removed_runs):,}")
    print(f"内容変化Run : {len(r.changed_runs):,}")
    print(f"{'系統':<11}{'ファイル増減':>14}{'バイト増減':>22}{'TB':>10}")
    for name in FAMILIES:
        count, total = r.added_by_family[name]
        print(f"{name:<11}{count:>14,}{total:>22,}{total/1e12:>10.3f}")
    print(f"\n合計 {r.added_files:,} ファイル / {r.added_bytes:,} bytes")


if __name__ == "__main__":
    main()
