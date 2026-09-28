#!/usr/bin/env python3
"""submitted_format="CRAM" の ERR について、ファイル単位の件数と総バイト数を厳密に集計する。

Run単位の submitted_bytes 合算では index (.crai) が混入するため、submitted_ftp の
ファイル名と位置対応で突き合わせ、.cram / .crai / その他へ分類する。
総バイト数は 2**53 を超えうるので awk を使わず Python の int で数える
（aggregate_sizes.py と同じ理由）。

入力は survey_cram_sizes.sh が残した年次の生TSV（gzip）。
列は run_accession, submitted_bytes, submitted_ftp で、後2列は ';' 区切りの
複数ファイルが位置で対応する。
"""

import gzip
import sys

KINDS = ["cram", "crai", "other"]


def classify(name):
    if name.endswith(".cram"):
        return "cram"
    if name.endswith(".crai"):
        return "crai"
    return "other"


def aggregate(paths):
    cnt = {k: 0 for k in KINDS}
    tot = {k: 0 for k in KINDS}
    n_rows = 0
    mismatches = 0

    for path in paths:
        with gzip.open(path, "rt") as fh:
            header = fh.readline()
            if not header.startswith("run_accession"):
                raise SystemExit(f"予期しないヘッダ: {path}: {header!r}")
            for line in fh:
                cols = line.rstrip("\n").split("\t")
                n_rows += 1
                sizes = [b for b in cols[1].split(";") if b] if len(cols) > 1 else []
                names = [x for x in cols[2].split(";") if x] if len(cols) > 2 else []
                if len(sizes) != len(names):
                    mismatches += 1
                    continue
                for size, name in zip(sizes, names):
                    kind = classify(name)
                    cnt[kind] += 1
                    tot[kind] += int(size)

    return n_rows, mismatches, cnt, tot


def per_year(paths):
    """ファイルごとに集計して年次表を標準出力へ出す。raw さえあれば作り直せる。"""
    print("year\trows\tmismatches\tcram_files\tcram_bytes\tcrai_files\tcrai_bytes"
          "\tother_files\tother_bytes")
    for path in paths:
        label = path.split("cram_")[-1].replace(".tsv.gz", "")
        n_rows, mismatches, cnt, tot = aggregate([path])
        cols = [label, str(n_rows), str(mismatches)]
        for k in KINDS:
            cols += [str(cnt[k]), str(tot[k])]
        print("\t".join(cols))


def main():
    args = [a for a in sys.argv[1:] if a != "--per-year"]
    paths = sorted(args)
    if not paths:
        raise SystemExit("入力を指定する。使い方: aggregate_cram_sizes.py <survey_cram_sizes.sh の出力先>/raw/cram_*.tsv.gz")

    if "--per-year" in sys.argv:
        per_year(paths)
        return

    n_rows, mismatches, cnt, tot = aggregate(paths)

    print(f"対象ファイル {len(paths)} 個 / 対象Run {n_rows:,} / 位置不一致 {mismatches}")
    print(f"{'種別':<7}{'ファイル数':>14}{'バイト数':>32}{'PB':>10}")
    for k in KINDS:
        print(f"{k:<7}{cnt[k]:>14,}{tot[k]:>32,}{tot[k]/1e15:>10.3f}")

    grand = sum(tot.values())
    print(f"\n合計バイト数 {grand:,} = {grand / 1e15:.3f} PB")


if __name__ == "__main__":
    main()
