"""規模レポートを1枚に組み立てる。

SRA Lite と SRA Normalized それぞれの Run数・総容量・coverage を、どの時点の
カタログを測ったか（snapshot境界）と前回からの差分とともに1枚にまとめる。
catalog の集計（survey_catalog.py）と差分の集計（snapshot_diff.py）を突き合わせて作る。

**実測と推定を混ぜない。** SRA Normalized の容量は Parquet の `mbytes` による実測、
SRA Lite の容量は係数を掛けた推定である。同じ表に並べる以上、どちらがどちらかを
レポート自身が明示する。SRA Lite の**件数**は `run.zq` による実測なので、同じ
「SRA Lite」の行でも件数と容量で根拠が違う。

使い方:
  python3 scale_report.py --catalog-report 2026-08-10-report.json \\
      --diff-report 2026-08-10-diff-report.json --output 2026-08-10-summary.json
"""

import argparse
import json
import os
import sys

from survey_catalog import LITE_RATIO_BASIS, format_binary_bytes, format_decimal_bytes


def build_scale_report(catalog_report, diff_report=None):
    """catalog と diff の集計から、規模レポートの機械可読な形を作る。"""
    prefixes = catalog_report.get("prefixes")
    if diff_report is not None:
        diff_prefixes = diff_report.get("prefixes")
        if prefixes and diff_prefixes and sorted(prefixes) != sorted(diff_prefixes):
            raise ValueError(f"catalogとdiffの対象prefixが違う: catalog {','.join(prefixes)} / "
                             f"diff {','.join(diff_prefixes)}")
    report = {
        "snapshot": {
            "label": catalog_report.get("label"),
            "prefixes": prefixes,
            "parquet_glob": catalog_report.get("parquet_glob"),
            "elapsed_seconds": catalog_report.get("elapsed_seconds"),
        },
        "scale": {
            "public_run_total": catalog_report.get("public_run_total"),
            "sra_normalized": {
                "run_count": catalog_report.get("sra_normalized_run_count"),
                "coverage": catalog_report.get("sra_normalized_coverage"),
                "bytes_total": catalog_report.get("sra_normalized_bytes_total"),
                "basis": "measured",
            },
            "sra_lite": {
                "run_count": catalog_report.get("sra_lite_run_count"),
                "coverage": catalog_report.get("sra_lite_coverage"),
                "run_count_basis": "measured",
                "bytes_total": catalog_report.get("sra_lite_bytes_estimated"),
                "basis": "estimated",
                "ratio": catalog_report.get("lite_ratio_used"),
                "ratio_basis": catalog_report.get("lite_ratio_basis", LITE_RATIO_BASIS),
                "ratio_source": "両形式のサイズが確定したSRR 17,544,315 Run の実測",
            },
            "by_prefix": catalog_report.get("by_prefix"),
        },
        "growth": None,
    }
    if diff_report is not None:
        report["growth"] = _build_growth(diff_report)
    return report


def _build_growth(diff_report):
    """増えた分と減った分を別々に持つ。

    合算した純増は出さない。消失は「Parquetのカタログから消えた」という事実だけを
    表し、取り下げなのか一時的にカタログに載らなかっただけなのかを区別していない。
    増加から引くと、性質の違う2つの数字が1つにまとまって区別できなくなる。
    """
    return {
        "previous_snapshot": diff_report.get("old_snapshot"),
        "added_run_count": diff_report.get("added_run_count", 0),
        "added_normalized_bytes": diff_report.get("added_normalized_bytes", 0),
        "added_lite_run_count": diff_report.get("added_lite_run_count", 0),
        "added_lite_bytes_estimated": diff_report.get("added_lite_bytes_estimated", 0),
        "removed_run_count": diff_report.get("removed_run_count", 0),
        "removed_normalized_bytes": diff_report.get("removed_normalized_bytes", 0),
        "removed_lite_run_count": diff_report.get("removed_lite_run_count", 0),
        "removed_lite_bytes_estimated": diff_report.get("removed_lite_bytes_estimated", 0),
        "modified_run_count": diff_report.get("modified_run_count", 0),
        "existing_run_normalized_bytes_delta": diff_report.get(
            "existing_run_normalized_bytes_delta", 0),
        "gained_lite_bytes_estimated": diff_report.get("gained_lite_bytes_estimated", 0),
        "runs_gaining_lite": diff_report.get("runs_gaining_lite", 0),
        "runs_with_regenerated_files": diff_report.get("runs_with_regenerated_files", 0),
        # 取得し直しが要るのは新規Runと内容が変わったRunの両方。
        "runs_to_fetch": (diff_report.get("added_run_count", 0)
                          + diff_report.get("modified_run_count", 0)),
    }


def _both_units(total):
    return f"{format_decimal_bytes(total)} / {format_binary_bytes(total)}"


def format_scale_report(report):
    """人が読む形にする。"""
    scale = report["scale"]
    normalized, lite = scale["sra_normalized"], scale["sra_lite"]
    prefixes = report["snapshot"]["prefixes"]
    lines = [
        f"=== 規模レポート {report['snapshot']['label']} ===",
        "",
        f"  対象         : {', '.join(prefixes) if prefixes else '記録なし'}",
        f"  snapshot境界 : {report['snapshot']['parquet_glob']}",
        f"  所要         : {report['snapshot']['elapsed_seconds']}s",
        "",
        f"  public Run 総数            : {scale['public_run_total']:>14,}",
        "",
        "  --- SRA Normalized（実測） ---",
        f"  Run数                      : {normalized['run_count']:>14,}",
        f"  coverage                   : {normalized['coverage']:>14.4%}",
        f"  総容量                     : {_both_units(normalized['bytes_total'])}",
        "",
        "  --- SRA Lite（件数は実測、容量は推定） ---",
        f"  Run数                      : {lite['run_count']:>14,}",
        f"  coverage                   : {lite['coverage']:>14.4%}",
        f"  総容量（推定）             : {_both_units(lite['bytes_total'])}",
        f"    ※ 係数 {lite['ratio']} による推定である。Parquetは SRA Lite のサイズを持たない。",
        f"       根拠は{lite['ratio_source']}。",
    ]
    if prefixes and set(prefixes) - {lite["ratio_basis"]}:
        lines.append(f"       係数は {lite['ratio_basis']} の実測から出した値で、"
                     "他のprefixにも同じ係数を当てている。")
    by_prefix = scale.get("by_prefix") or {}
    if len(by_prefix) > 1:
        lines += ["", "  --- prefixごとの内訳 ---",
                  "  prefix          Run数  Normalized coverage / 総容量"
                  "      Lite coverage / 総容量（推定）"]
        for prefix, part in by_prefix.items():
            lines.append(
                f"  {prefix} {part['public_run_total']:>18,}  "
                f"{part['sra_normalized_coverage']:>9.4%} / "
                f"{format_decimal_bytes(part['sra_normalized_bytes_total']):>10}  "
                f"{part['sra_lite_coverage']:>9.4%} / "
                f"{format_decimal_bytes(part['sra_lite_bytes_estimated']):>10}")
    growth = report.get("growth")
    if growth is None:
        lines += ["", "  前回のsnapshotが無いため、変化は出せない。"]
    else:
        lines += [
            "",
            f"  --- 前回からの変化（{growth['previous_snapshot']} との比較） ---",
            f"  新規に現れた Run           : {growth['added_run_count']:>14,}",
            f"    うち SRA Lite を持つ     : {growth['added_lite_run_count']:>14,}",
            f"    SRA Normalized の容量    : {_both_units(growth['added_normalized_bytes'])}",
            f"    SRA Lite の容量（推定）  : "
            f"{_both_units(growth['added_lite_bytes_estimated'])}",
            f"  消えた Run                 : {growth['removed_run_count']:>14,}",
            f"    うち SRA Lite を持つ     : {growth['removed_lite_run_count']:>14,}",
            f"    SRA Normalized の容量    : {_both_units(growth['removed_normalized_bytes'])}",
            f"    SRA Lite の容量（推定）  : "
            f"{_both_units(growth['removed_lite_bytes_estimated'])}",
            f"  内容が変わった Run         : {growth['modified_run_count']:>14,}",
            f"    Lite が後から付いた      : {growth['runs_gaining_lite']:>14,}"
            f"（推定 {format_decimal_bytes(growth['gained_lite_bytes_estimated'])}）",
            f"    実体が作り直された       : {growth['runs_with_regenerated_files']:>14,}",
            f"    既存Runの容量増減        : "
            f"{_both_units(growth['existing_run_normalized_bytes_delta'])}",
            "",
            f"  取得し直しが要る Run       : {growth['runs_to_fetch']:>14,}",
            "    新規Runと内容が変わったRunの合計。増減の合算ではない。",
        ]
    return "\n".join(lines)


def load_json(path):
    with open(path) as handle:
        return json.load(handle)


def run(arguments):
    for path in [arguments.catalog_report] + ([arguments.diff_report]
                                              if arguments.diff_report else []):
        if not os.path.exists(path):
            raise SystemExit(f"reportが無い: {path}")
    catalog_report = load_json(arguments.catalog_report)
    diff_report = load_json(arguments.diff_report) if arguments.diff_report else None
    try:
        report = build_scale_report(catalog_report, diff_report)
    except ValueError as error:
        raise SystemExit(str(error))
    text = format_scale_report(report)
    print(text)
    if arguments.output:
        with open(arguments.output, "w") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        print(f"\n  規模レポート: {arguments.output}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--catalog-report", required=True,
                        help="survey_catalog.py が出した {label}-report.json")
    parser.add_argument("--diff-report",
                        help="snapshot_diff.py が出した {label}-diff-report.json。"
                             "初回は省略する")
    parser.add_argument("--output", help="機械可読な規模レポートの書き出し先")
    arguments = parser.parse_args(argv)
    return run(arguments)


if __name__ == "__main__":
    sys.exit(main())
