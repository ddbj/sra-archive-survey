"""scale_report.py のテスト。外部サービスへ接続しない。"""

import unittest

from scale_report import build_scale_report, format_scale_report

MIB = 1024 * 1024


def catalog(**overrides):
    base = {
        "label": "2026-08-10",
        "public_run_total": 100,
        "sra_normalized_run_count": 99,
        "sra_normalized_coverage": 0.99,
        "sra_normalized_bytes_total": 1000 * MIB,
        "sra_lite_run_count": 94,
        "sra_lite_coverage": 0.94,
        "lite_bearing_normalized_bytes_total": 900 * MIB,
        "sra_lite_bytes_estimated": 574 * MIB,
        "lite_ratio_used": 0.6379,
        "parquet_glob": "s3://bucket/*",
        "elapsed_seconds": 400.0,
    }
    base.update(overrides)
    return base


def diff(**overrides):
    base = {
        "label": "2026-08-10",
        "added_run_count": 8,
        "added_normalized_bytes": 80 * MIB,
        "added_lite_run_count": 7,
        "added_lite_bearing_normalized_bytes": 70 * MIB,
        "added_lite_bytes_estimated": 44 * MIB,
        "removed_run_count": 1,
        "removed_normalized_bytes": 5 * MIB,
        "removed_lite_run_count": 1,
        "removed_lite_bytes_estimated": 3 * MIB,
        "gained_lite_bytes_estimated": 1 * MIB,
        "modified_run_count": 3,
        "modified_by_field": {"run_file_version": 2, "has_sra_lite": 1},
        "existing_run_normalized_bytes_delta": 20 * MIB,
        "runs_gaining_lite": 1,
        "runs_losing_lite": 0,
        "runs_with_regenerated_files": 2,
        "lite_ratio_used": 0.6379,
        "old_snapshot": "2026-08-02-runs.jsonl.gz",
        "new_snapshot": "2026-08-10-runs.jsonl.gz",
    }
    base.update(overrides)
    return base


class BuildWeeklyReportTest(unittest.TestCase):
    def test_records_the_snapshot_boundary(self):
        """Parquetは常に更新される。どの時点を測ったかを残さないと数字が再現しない。"""
        report = build_scale_report(catalog())
        self.assertEqual(report["snapshot"]["label"], "2026-08-10")
        self.assertEqual(report["snapshot"]["parquet_glob"], "s3://bucket/*")

    def test_marks_normalized_size_as_measured(self):
        report = build_scale_report(catalog())
        self.assertEqual(report["scale"]["sra_normalized"]["basis"], "measured")

    def test_marks_lite_size_as_estimated_with_its_ratio(self):
        """実測と推定を混ぜない。Lite の容量はParquetに無い。"""
        report = build_scale_report(catalog())
        lite = report["scale"]["sra_lite"]
        self.assertEqual(lite["basis"], "estimated")
        self.assertEqual(lite["ratio"], 0.6379)

    def test_lite_run_count_stays_measured_even_though_size_is_estimated(self):
        """有無は datastore_filetype の run.zq による実測。件数と容量で根拠が違う。"""
        report = build_scale_report(catalog())
        self.assertEqual(report["scale"]["sra_lite"]["run_count_basis"], "measured")

    def test_omits_growth_section_without_a_previous_snapshot(self):
        self.assertIsNone(build_scale_report(catalog())["growth"])

    def test_reports_growth_from_new_runs(self):
        report = build_scale_report(catalog(), diff())
        self.assertEqual(report["growth"]["added_run_count"], 8)
        self.assertEqual(report["growth"]["added_normalized_bytes"], 80 * MIB)

    def test_does_not_combine_added_and_removed_into_a_net_delta(self):
        """新規で増えた分と消失で減った分は別々に扱う。合算した純増は出さない。"""
        growth = build_scale_report(catalog(), diff())["growth"]
        self.assertNotIn("normalized_bytes_delta_total", growth)

    def test_reports_removed_bytes_on_their_own(self):
        growth = build_scale_report(catalog(), diff())["growth"]
        self.assertEqual(growth["removed_normalized_bytes"], 5 * MIB)
        self.assertEqual(growth["removed_lite_bytes_estimated"], 3 * MIB)

    def test_reports_lite_gained_by_existing_runs_on_its_own(self):
        growth = build_scale_report(catalog(), diff())["growth"]
        self.assertEqual(growth["gained_lite_bytes_estimated"], 1 * MIB)

    def test_keeps_existing_run_growth_visible_separately(self):
        report = build_scale_report(catalog(), diff())
        self.assertEqual(report["growth"]["existing_run_normalized_bytes_delta"], 20 * MIB)

    def test_counts_runs_needing_download_as_added_plus_changed(self):
        """取得し直しが要るのは新規Runと内容が変わったRunの両方。"""
        report = build_scale_report(catalog(), diff())
        self.assertEqual(report["growth"]["runs_to_fetch"], 11)


class FormatWeeklyReportTest(unittest.TestCase):
    def test_labels_the_lite_size_as_an_estimate_in_the_text(self):
        text = format_scale_report(build_scale_report(catalog()))
        self.assertIn("推定", text)

    def test_shows_both_decimal_and_binary_units(self):
        text = format_scale_report(build_scale_report(catalog()))
        self.assertIn("PB", text)
        self.assertIn("PiB", text)

    def test_titles_the_text_as_a_scale_report(self):
        text = format_scale_report(build_scale_report(catalog()))
        self.assertTrue(text.startswith("=== 規模レポート 2026-08-10 ==="))

    def test_renders_without_a_growth_section(self):
        text = format_scale_report(build_scale_report(catalog()))
        self.assertIn("前回のsnapshotが無い", text)

    def test_renders_growth_section_when_present(self):
        text = format_scale_report(build_scale_report(catalog(), diff()))
        self.assertIn("新規に現れた Run", text)

    def test_shows_added_and_removed_as_separate_lines(self):
        text = format_scale_report(build_scale_report(catalog(), diff()))
        self.assertIn("消えた Run", text)
        self.assertNotIn("容量の増減（合計）", text)


def part(runs, normalized_bytes):
    return {
        "public_run_total": runs,
        "sra_normalized_run_count": runs,
        "sra_normalized_coverage": 1.0,
        "sra_normalized_bytes_total": normalized_bytes,
        "sra_lite_run_count": 0,
        "sra_lite_coverage": 0.0,
        "lite_bearing_normalized_bytes_total": 0,
        "sra_lite_bytes_estimated": 0,
    }


def three_archive_catalog():
    return catalog(prefixes=["DRR", "ERR", "SRR"],
                   by_prefix={"DRR": part(10, 10 * MIB), "ERR": part(30, 30 * MIB),
                              "SRR": part(60, 960 * MIB)})


class PrefixTest(unittest.TestCase):
    """三極合計か一部だけかで数字の意味が変わるので、対象をレポートに必ず残す。"""

    def test_records_the_target_prefixes(self):
        report = build_scale_report(three_archive_catalog())
        self.assertEqual(report["snapshot"]["prefixes"], ["DRR", "ERR", "SRR"])

    def test_leaves_prefixes_unknown_for_reports_that_did_not_record_them(self):
        self.assertIsNone(build_scale_report(catalog())["snapshot"]["prefixes"])

    def test_carries_the_per_prefix_breakdown(self):
        report = build_scale_report(three_archive_catalog())
        self.assertEqual(report["scale"]["by_prefix"]["ERR"]["public_run_total"], 30)

    def test_names_srr_as_the_source_of_the_lite_ratio(self):
        report = build_scale_report(three_archive_catalog())
        self.assertEqual(report["scale"]["sra_lite"]["ratio_basis"], "SRR")

    def test_refuses_a_diff_taken_with_other_prefixes(self):
        with self.assertRaises(ValueError):
            build_scale_report(three_archive_catalog(), diff(prefixes=["SRR"]))

    def test_accepts_a_diff_with_the_same_prefixes(self):
        report = build_scale_report(three_archive_catalog(),
                                     diff(prefixes=["DRR", "ERR", "SRR"]))
        self.assertEqual(report["growth"]["added_run_count"], 8)


class FormatPrefixTest(unittest.TestCase):
    def test_shows_the_target_prefixes(self):
        text = format_scale_report(build_scale_report(three_archive_catalog()))
        self.assertIn("DRR, ERR, SRR", text)

    def test_says_when_the_target_was_not_recorded(self):
        text = format_scale_report(build_scale_report(catalog()))
        self.assertIn("記録なし", text)

    def test_shows_one_breakdown_line_per_prefix(self):
        text = format_scale_report(build_scale_report(three_archive_catalog()))
        breakdown = [line for line in text.splitlines()
                     if line.strip().startswith(("DRR ", "ERR ", "SRR "))]
        self.assertEqual(len(breakdown), 3)

    def test_omits_the_breakdown_for_a_single_prefix(self):
        report = build_scale_report(catalog(prefixes=["SRR"],
                                             by_prefix={"SRR": part(100, 1000 * MIB)}))
        self.assertNotIn("内訳", format_scale_report(report))

    def test_warns_that_the_srr_ratio_is_applied_to_other_prefixes(self):
        text = format_scale_report(build_scale_report(three_archive_catalog()))
        self.assertIn("他のprefixにも同じ係数", text)


if __name__ == "__main__":
    unittest.main()
