"""NCBI と DDBJ の突き合わせ結果を、DDBJ 側の状態と DRR 付きの1枚にまとめる。

compare_archives.py は DRX 単位で比べ、fetch_ddbj_runs.py が DDBJ にしか無い DRX の
DRR を引く。ここではそれらを DRR 単位の行に広げ、status ファイルの状態を添える。

  ddbj_status  status ファイルの EXPERIMENT 行の Status（public / suppressed / withdrawn）。
               status ファイルに無ければ not_in_status
  source       DRR をどこから得たか
                 search_api        DDBJ Search API で引けた
                 status_file_only  API が返さなかった（suppressed / withdrawn は 404 になる）。
                                   DRR は分からないので空欄
                 ncbi_parquet      NCBI カタログにある DRX（NCBI 側の対応をそのまま使う）

使い方:
  python3 label_ddbj_results.py --status 20260927.dra.status.txt \\
      --ddbj-only ddbj-only.tsv --runs ddbj-only-runs.tsv --not-found ddbj-only-not-found.txt \\
      --ncbi-only ncbi-only.tsv \\
      --output-ddbj-only ddbj-only-drr.tsv --output-ncbi-only ncbi-only-drr.tsv
"""

import argparse
import sys

from compare_archives import write_tsv
from ddbj_client import parse_status_line

HEADER = ["drx", "drr", "ddbj_status", "source"]
NOT_IN_STATUS = "not_in_status"


def load_experiment_status(lines, wanted):
    """status ファイルは約260 MB あるので、対象の DRX だけを拾う。"""
    status = {}
    for line in lines:
        parsed = parse_status_line(line)
        if parsed is None:
            continue
        accession, kind, state, _visibility = parsed
        if kind == "EXPERIMENT" and accession in wanted:
            status[accession] = state
    return status


def label_ddbj_only(targets, runs, not_found, status):
    targets = set(targets)
    not_found = set(not_found) & targets
    rows = [[drx, drr, status.get(drx, NOT_IN_STATUS), "search_api"]
            for drx, drr in runs if drx in targets]
    rows += [[drx, "", status.get(drx, NOT_IN_STATUS), "status_file_only"] for drx in not_found]
    unresolved = targets - {row[0] for row in rows}
    if unresolved:
        sample = ", ".join(sorted(unresolved)[:5])
        raise ValueError(f"DRR を引いていない DRX が {len(unresolved):,} 件ある（{sample} など）。"
                         "fetch_ddbj_runs.py を再実行してから、まとめ直す。")
    return sorted(rows, key=lambda row: (row[0], row[1]))


def label_ncbi_only(pairs, status):
    return [[drx, drr, status.get(drx, NOT_IN_STATUS), "ncbi_parquet"]
            for drx, drr in sorted(pairs)]


def read_rows(path, columns):
    """ヘッダ付きTSVの先頭 columns 列を読む。"""
    with open(path) as handle:
        handle.readline()
        return [tuple(line.rstrip("\n").split("\t")[:columns]) for line in handle if line.strip()]


def read_lines(path):
    with open(path) as handle:
        return [line.strip() for line in handle if line.strip()]


def run(arguments):
    targets = [row[0] for row in read_rows(arguments.ddbj_only, 1)]
    runs = read_rows(arguments.runs, 2)
    not_found = read_lines(arguments.not_found)
    ncbi_pairs = read_rows(arguments.ncbi_only, 2)
    wanted = set(targets) | {drx for drx, _ in ncbi_pairs}
    with open(arguments.status) as handle:
        status = load_experiment_status(handle, wanted)

    try:
        ddbj_rows = label_ddbj_only(targets, runs, not_found, status)
    except ValueError as error:
        raise SystemExit(str(error))
    ncbi_rows = label_ncbi_only(ncbi_pairs, status)
    write_tsv(arguments.output_ddbj_only, HEADER, ddbj_rows)
    write_tsv(arguments.output_ncbi_only, HEADER, ncbi_rows)

    for name, rows, path in (("DDBJ にしか無い", ddbj_rows, arguments.output_ddbj_only),
                             ("NCBI にしか無い", ncbi_rows, arguments.output_ncbi_only)):
        counts = {}
        for row in rows:
            counts[(row[2], row[3])] = counts.get((row[2], row[3]), 0) + 1
        print(f"=== {name}（{len(rows):,} 行） → {path}")
        for (state, source), count in sorted(counts.items(), key=lambda item: -item[1]):
            print(f"  {count:>10,}  {state} / {source}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--status", required=True, help="fetch_ddbj_status.py が取った status ファイル")
    parser.add_argument("--ddbj-only", required=True,
                        help="compare_archives.py --archive ddbj の --output")
    parser.add_argument("--runs", required=True, help="fetch_ddbj_runs.py の --output")
    parser.add_argument("--not-found", required=True, help="fetch_ddbj_runs.py の --missing")
    parser.add_argument("--ncbi-only", required=True,
                        help="compare_archives.py --archive ddbj の --ncbi-only-output")
    parser.add_argument("--output-ddbj-only", required=True)
    parser.add_argument("--output-ncbi-only", required=True)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
