#!/usr/bin/env python3
"""ERR のファイル系統別に、保有Run数・ファイル数・総バイト数を厳密に集計する。

awk を使ってはならない。総バイト数は 10**16 のオーダーに達し、倍精度の
整数精度限界 2**53 (≒9.007e15) を超えるため、合計値が静かに狂う。
Python の int は多倍長なので誤差が出ない。

入力は survey_sizes.sh が残した年次の生TSV（gzip）。
列は run_accession, fastq_bytes, submitted_bytes, sra_bytes, bam_bytes で、
1つの列に ';' 区切りで複数ファイルのサイズが入る。
"""

import gzip
import sys

FAMILIES = ["fastq", "submitted", "sra", "bam"]


def aggregate(paths):
    runs = [0] * 4
    files = [0] * 4
    total = [0] * 4
    n_rows = 0

    for path in paths:
        with gzip.open(path, "rt") as fh:
            header = fh.readline()
            if not header.startswith("run_accession"):
                raise SystemExit(f"予期しないヘッダ: {path}: {header!r}")
            for line in fh:
                cols = line.rstrip("\n").split("\t")
                n_rows += 1
                for i in range(4):
                    raw = cols[i + 1] if len(cols) > i + 1 else ""
                    if not raw:
                        continue
                    present = False
                    for part in raw.split(";"):
                        if part:
                            files[i] += 1
                            total[i] += int(part)
                            present = True
                    if present:
                        runs[i] += 1

    return n_rows, runs, files, total


def per_year(paths):
    """ファイルごとに集計して per_year.tsv 相当を標準出力へ出す。

    raw さえ残っていれば、ネットワーク無しでここから per_year.tsv を作り直せる。
    """
    print("year\trows\tfastq_files\tfastq_bytes\tsubmitted_files\tsubmitted_bytes"
          "\tsra_files\tsra_bytes\tbam_files\tbam_bytes")
    for path in paths:
        label = path.split("err_")[-1].replace(".tsv.gz", "")
        n_rows, runs, files, total = aggregate([path])
        cols = [str(n_rows)]
        for i in range(4):
            cols += [str(files[i]), str(total[i])]
        print(label + "\t" + "\t".join(cols))


# バイト数は 1.1e19（20桁、カンマ込みで26文字）に届く。10**21 まで収まる幅を取る。
BYTES_WIDTH = 32


def format_family_row(name, runs, coverage, files, total):
    return (f"{name:<11}{runs:>14,}{coverage:>9.2f}%{files:>14,}"
            f"{total:>{BYTES_WIDTH},}{total / 1e15:>10.3f}")


def main():
    args = [a for a in sys.argv[1:] if a != "--per-year"]
    paths = sorted(args)
    if not paths:
        raise SystemExit("入力を指定する。使い方: aggregate_sizes.py <survey_sizes.sh の出力先>/raw/err_*.tsv.gz")

    if "--per-year" in sys.argv:
        per_year(paths)
        return

    n_rows, runs, files, total = aggregate(paths)

    print(f"対象ファイル {len(paths)} 個 / 対象Run {n_rows:,}")
    print(f"{'系統':<11}{'保有Run数':>14}{'coverage':>10}{'ファイル数':>14}"
          f"{'バイト数':>{BYTES_WIDTH}}{'PB':>10}")
    for i, name in enumerate(FAMILIES):
        cov = 100 * runs[i] / n_rows if n_rows else 0
        print(format_family_row(name, runs[i], cov, files[i], total[i]))

    grand = sum(total)
    print(f"\n合計バイト数 {grand:,} = {grand / 1e15:.3f} PB")


if __name__ == "__main__":
    main()
