"""survey_catalog.py の純粋ロジックのテスト。

外部サービスへ接続しない。DuckDBもParquetも要らない。
    python3 -m unittest discover -s scale/ncbi -v
"""

import datetime
import unittest

from survey_catalog import (
    MIB,
    Checkpoint,
    SurveyTotals,
    build_record,
    catalog_query,
    normalize_filetypes,
    estimate_lite_bytes,
    has_filetype,
    format_decimal_bytes,
    format_binary_bytes,
    mib_to_bytes,
    normalize_prefixes,
    restore_totals,
    resume_predicate,
    validate_accession,
)


class ValidateAccessionTest(unittest.TestCase):
    def test_accepts_plain_srr_accession(self):
        self.assertEqual(validate_accession("SRR39793838"), "SRR39793838")

    def test_accepts_zero_padded_accession(self):
        """zero paddingは有意なので、そのまま通す。"""
        self.assertEqual(validate_accession("SRR000100"), "SRR000100")

    def test_accepts_run_accessions_of_all_three_archives(self):
        for value in ("SRR000001", "ERR000001", "DRR000001"):
            self.assertEqual(validate_accession(value), value)

    def test_accepts_seven_digit_drr_accession(self):
        self.assertEqual(validate_accession("DRR1091692"), "DRR1091692")

    def test_rejects_non_run_accessions(self):
        for value in ("SRX123", "ERS123", "DRA000001", "XRR123"):
            with self.assertRaises(ValueError):
                validate_accession(value)

    def test_rejects_sql_injection_attempt(self):
        with self.assertRaises(ValueError):
            validate_accession("SRR1' OR '1'='1")

    def test_rejects_empty_and_none(self):
        for value in ("", None):
            with self.assertRaises(ValueError):
                validate_accession(value)


class MibToBytesTest(unittest.TestCase):
    def test_converts_using_binary_megabyte(self):
        """Parquetのmbytesは MB ではなく MiB である。"""
        self.assertEqual(mib_to_bytes(1), MIB)
        self.assertEqual(mib_to_bytes(298), 298 * 1024 * 1024)

    def test_treats_none_as_zero(self):
        self.assertEqual(mib_to_bytes(None), 0)

    def test_rejects_negative(self):
        with self.assertRaises(ValueError):
            mib_to_bytes(-1)


class ResumePredicateTest(unittest.TestCase):
    def test_returns_empty_string_when_starting_fresh(self):
        self.assertEqual(resume_predicate(None), "")

    def test_builds_lexicographic_greater_than_clause(self):
        self.assertEqual(resume_predicate("SRR001196"), " AND acc > 'SRR001196'")

    def test_validates_the_accession_before_interpolating(self):
        with self.assertRaises(ValueError):
            resume_predicate("SRR1'; DROP TABLE x; --")


class HasFiletypeTest(unittest.TestCase):
    """datastore_filetype には末尾空白の異体が混じる。比較前にstripする。"""

    def test_finds_the_value(self):
        self.assertTrue(has_filetype(["sra", "run.zq"], "run.zq"))

    def test_strips_whitespace_variants(self):
        self.assertTrue(has_filetype(["run.zq "], "run.zq"))

    def test_returns_false_when_absent(self):
        self.assertFalse(has_filetype(["sra", "fastq"], "run.zq"))

    def test_treats_none_as_empty(self):
        self.assertFalse(has_filetype(None, "run.zq"))


class NormalizePrefixesTest(unittest.TestCase):
    def test_accepts_a_single_archive(self):
        self.assertEqual(normalize_prefixes(["SRR"]), ("SRR",))

    def test_sorts_in_the_same_order_as_the_snapshot(self):
        """snapshotはaccessionの辞書順なので、DRR・ERR・SRRの順に並ぶ。"""
        self.assertEqual(normalize_prefixes(["SRR", "DRR", "ERR"]), ("DRR", "ERR", "SRR"))

    def test_drops_duplicates(self):
        self.assertEqual(normalize_prefixes(["SRR", "SRR"]), ("SRR",))

    def test_rejects_unknown_prefix_because_it_is_embedded_in_sql(self):
        for value in (["SRX"], ["SRR'; --"], ["srr"]):
            with self.assertRaises(ValueError):
                normalize_prefixes(value)

    def test_rejects_empty_selection(self):
        with self.assertRaises(ValueError):
            normalize_prefixes([])


class SurveyTotalsTest(unittest.TestCase):
    def setUp(self):
        self.totals = SurveyTotals(("SRR",))

    def add(self, accession, mbytes, filetypes=("sra",)):
        self.totals.add(accession, mbytes, list(filetypes))

    def test_counts_runs_with_and_without_normalized(self):
        self.add("SRR000001", 298)
        self.add("SRR000002", None, filetypes=[])
        self.add("SRR000003", 0, filetypes=[])
        report = self.totals.report()
        self.assertEqual(report["public_run_total"], 3)
        self.assertEqual(report["sra_normalized_run_count"], 1)

    def test_sums_bytes_from_mib(self):
        self.add("SRR000001", 100)
        self.add("SRR000002", 200)
        self.assertEqual(self.totals.report()["sra_normalized_bytes_total"], 300 * MIB)

    def test_normalized_presence_comes_from_filetype_not_size(self):
        """1 MiB未満のRunは mbytes=0 に丸められる。sizeで有無を判定しない。"""
        self.add("SRR000001", 0, filetypes=["sra"])
        report = self.totals.report()
        self.assertEqual(report["sra_normalized_run_count"], 1)
        self.assertEqual(report["sra_normalized_bytes_total"], 0)

    def test_counts_lite_from_run_zq(self):
        """run.zq が SRA Lite を指すことは1,834万件の照合で確認済み（不一致7件）。"""
        self.add("SRR000001", 10, filetypes=["sra", "run.zq"])
        self.add("SRR000002", 10, filetypes=["sra"])
        report = self.totals.report()
        self.assertEqual(report["sra_lite_run_count"], 1)
        self.assertAlmostEqual(report["sra_lite_coverage"], 0.5)

    def test_lite_can_exist_without_normalized(self):
        """Normalizedがsuppressされ Lite だけ残るRunがある（SRR5752847の類型）。"""
        self.add("SRR000001", 24, filetypes=["run.zq"])
        report = self.totals.report()
        self.assertEqual(report["sra_lite_run_count"], 1)
        self.assertEqual(report["sra_normalized_run_count"], 0)

    def test_coverage_is_ratio_of_runs_having_normalized(self):
        for index in range(4):
            self.add(f"SRR00000{index}", 10, filetypes=["sra"] if index < 3 else [])
        self.assertAlmostEqual(self.totals.report()["sra_normalized_coverage"], 0.75)

    def test_coverage_is_zero_when_no_runs(self):
        self.assertEqual(self.totals.report()["sra_normalized_coverage"], 0.0)
        self.assertEqual(self.totals.report()["sra_lite_coverage"], 0.0)

    def test_tracks_last_accession_for_checkpointing(self):
        self.add("SRR000001", 1)
        self.add("SRR000002", 1)
        self.assertEqual(self.totals.last_accession, "SRR000002")

    def test_merges_another_totals_for_resume(self):
        """再開時は、既存出力から復元したtotalsへ続きを足す。"""
        earlier = SurveyTotals(("SRR",))
        earlier.add("SRR000001", 10, ["sra", "run.zq"])
        later = SurveyTotals(("SRR",))
        later.add("SRR000002", 20, ["sra"])
        earlier.merge(later)
        report = earlier.report()
        self.assertEqual(report["public_run_total"], 2)
        self.assertEqual(report["sra_normalized_bytes_total"], 30 * MIB)
        self.assertEqual(report["sra_lite_run_count"], 1)
        self.assertEqual(earlier.last_accession, "SRR000002")


class SurveyTotalsByPrefixTest(unittest.TestCase):
    """三極をまとめて取ったときも、archiveごとの規模を出せるようにする。"""

    def setUp(self):
        self.totals = SurveyTotals(("DRR", "ERR", "SRR"))
        self.totals.add("DRR000001", 10, ["sra"])
        self.totals.add("ERR000001", 20, ["sra", "run.zq"])
        self.totals.add("ERR000002", 30, [])
        self.totals.add("SRR000001", 40, ["sra", "run.zq"])

    def test_top_level_figures_are_the_total_of_all_prefixes(self):
        report = self.totals.report()
        self.assertEqual(report["public_run_total"], 4)
        self.assertEqual(report["sra_normalized_bytes_total"], 70 * MIB)

    def test_breaks_down_the_same_figures_by_prefix(self):
        erx = self.totals.report()["by_prefix"]["ERR"]
        self.assertEqual(erx["public_run_total"], 2)
        self.assertEqual(erx["sra_normalized_run_count"], 1)
        self.assertAlmostEqual(erx["sra_normalized_coverage"], 0.5)
        self.assertEqual(erx["sra_normalized_bytes_total"], 20 * MIB)
        self.assertEqual(erx["sra_lite_run_count"], 1)

    def test_estimates_lite_bytes_per_prefix(self):
        report = self.totals.report(lite_ratio=0.5)
        self.assertEqual(report["by_prefix"]["SRR"]["sra_lite_bytes_estimated"], 20 * MIB)

    def test_lists_selected_prefixes_even_when_a_prefix_has_no_runs(self):
        """0件だったことも結果として残す。欄が無いと取得漏れと区別できない。"""
        totals = SurveyTotals(("DRR", "SRR"))
        totals.add("SRR000001", 1, ["sra"])
        report = totals.report()
        self.assertEqual(report["prefixes"], ["DRR", "SRR"])
        self.assertEqual(report["by_prefix"]["DRR"]["public_run_total"], 0)

    def test_rejects_runs_outside_the_selected_prefixes(self):
        totals = SurveyTotals(("SRR",))
        with self.assertRaises(ValueError):
            totals.add("ERR000001", 1, ["sra"])

    def test_merge_combines_the_breakdown(self):
        later = SurveyTotals(("DRR", "ERR", "SRR"))
        later.add("DRR000002", 5, ["sra"])
        self.totals.merge(later)
        self.assertEqual(self.totals.report()["by_prefix"]["DRR"]["sra_normalized_bytes_total"],
                         15 * MIB)
        self.assertEqual(self.totals.last_accession, "DRR000002")


class RestoreTotalsTest(unittest.TestCase):
    def test_rebuilds_the_breakdown_from_written_rows(self):
        import gzip, json, os, tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "runs.jsonl.gz")
            with gzip.open(path, "wt") as handle:
                for accession, mbytes in (("ERR000001", 10), ("SRR000001", 20)):
                    handle.write(json.dumps(build_record(accession, mbytes, ["sra"], None, 1)) + "\n")

            totals = restore_totals(path, ("ERR", "SRR"))

        report = totals.report()
        self.assertEqual(report["by_prefix"]["ERR"]["sra_normalized_bytes_total"], 10 * MIB)
        self.assertEqual(totals.last_accession, "SRR000001")


class RestoreTruncatedRowsTest(unittest.TestCase):
    def test_stops_with_guidance_when_the_rows_file_was_cut_off(self):
        """SIGKILL や walltime 超過で止まると gzip の末尾が書かれない。"""
        import gzip, os, tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "runs.jsonl.gz")
            with gzip.open(path, "wt") as handle:
                for index in range(1000):
                    handle.write(json_line(f"SRR{index:06d}"))
            with open(path, "rb") as handle:
                data = handle.read()
            with open(path, "wb") as handle:
                handle.write(data[:len(data) // 2])

            with self.assertRaises(SystemExit) as raised:
                restore_totals(path, ("SRR",))

        self.assertIn("壊れて", str(raised.exception))


def json_line(accession):
    import json
    return json.dumps(build_record(accession, 1, ["sra"], None, 1)) + "\n"


class EstimateLiteBytesTest(unittest.TestCase):
    """SRA Lite の容量はParquetに無いため、Normalized容量へ係数を掛けて推定する。

    係数は両形式のサイズが確定している17,544,315 Run（Normalized 17.321 PB /
    Lite 11.049 PB）から得た。掛ける相手は「Liteを持つRunのNormalized容量」で
    あって全Normalized容量ではない。Liteを持たないRunを分母に入れると比の意味が
    変わり、推定が小さく出る。
    """

    def test_multiplies_by_the_given_ratio(self):
        self.assertEqual(estimate_lite_bytes(1000, ratio=0.5), 500)

    def test_returns_zero_for_zero_bytes(self):
        self.assertEqual(estimate_lite_bytes(0, ratio=0.6379), 0)

    def test_rejects_negative_ratio(self):
        with self.assertRaises(ValueError):
            estimate_lite_bytes(1000, ratio=-0.1)

    def test_reproduces_the_measured_lite_total(self):
        """走査済み実測（Normalized 17.321 PB → Lite 11.049 PB）を再現する。"""
        measured_normalized = 17_321_000_000_000_000
        estimated = estimate_lite_bytes(measured_normalized, ratio=0.6379)
        self.assertAlmostEqual(estimated / 10**15, 11.049, places=2)


class LiteBearingBytesTest(unittest.TestCase):
    def setUp(self):
        self.totals = SurveyTotals(("SRR",))

    def test_sums_normalized_bytes_only_for_runs_having_lite(self):
        self.totals.add("SRR000001", 100, ["sra", "run.zq"])
        self.totals.add("SRR000002", 300, ["sra"])
        report = self.totals.report()
        self.assertEqual(report["lite_bearing_normalized_bytes_total"], 100 * MIB)
        self.assertEqual(report["sra_normalized_bytes_total"], 400 * MIB)

    def test_skips_lite_only_runs_because_they_have_no_normalized_size(self):
        """Normalizedがsuppressされ Lite だけ残るRunは、掛ける元の容量を持たない。"""
        self.totals.add("SRR5752847", 24, ["run.zq"])
        self.assertEqual(self.totals.report()["lite_bearing_normalized_bytes_total"], 0)

    def test_report_estimates_lite_bytes_from_lite_bearing_bytes(self):
        self.totals.add("SRR000001", 1000, ["sra", "run.zq"])
        report = self.totals.report(lite_ratio=0.5)
        self.assertEqual(report["sra_lite_bytes_estimated"], 500 * MIB)
        self.assertEqual(report["lite_ratio_used"], 0.5)

    def test_merge_keeps_lite_bearing_bytes(self):
        later = SurveyTotals(("SRR",))
        later.add("SRR000002", 200, ["sra", "run.zq"])
        self.totals.add("SRR000001", 100, ["sra", "run.zq"])
        self.totals.merge(later)
        self.assertEqual(self.totals.report()["lite_bearing_normalized_bytes_total"], 300 * MIB)


class BuildRecordTest(unittest.TestCase):
    """snapshotの1行。週次差分で「何が変わったか」を判定できるだけの列を持たせる。"""

    def test_keeps_accession_string_verbatim(self):
        """zero paddingは有意。数値から再構成しない。"""
        record = build_record("SRR000100", 10, ["sra"], None, 1)
        self.assertEqual(record["accession"], "SRR000100")

    def test_serializes_releasedate_as_iso_string(self):
        record = build_record("SRR000001", 10, ["sra"], datetime.date(2015, 4, 8), 1)
        self.assertEqual(record["releasedate"], "2015-04-08")

    def test_allows_missing_releasedate(self):
        self.assertIsNone(build_record("SRR000001", 10, ["sra"], None, 1)["releasedate"])

    def test_keeps_run_file_version_for_detecting_regenerated_files(self):
        """version>1は実体が作り直されたRun。公開Runの約9%が該当する。"""
        self.assertEqual(build_record("SRR000001", 10, ["sra"], None, 3)["run_file_version"], 3)

    def test_stores_the_normalized_filetype_array(self):
        """形式レベルの増減を追うため、配列そのものを残す。"""
        record = build_record("SRR000001", 10, ["sff", "sra", "run.zq "], None, 1)
        self.assertEqual(record["datastore_filetype"], ["run.zq", "sff", "sra"])

    def test_keeps_presence_flags_alongside_the_array(self):
        """配列から導出できるが、集計の主役なので残す。"""
        record = build_record("SRR000001", 10, ["sra", "run.zq"], None, 1)
        self.assertTrue(record["has_sra_normalized"])
        self.assertTrue(record["has_sra_lite"])
        self.assertIn("datastore_filetype", record)

    def test_derives_presence_flags_from_filetypes(self):
        record = build_record("SRR000001", 10, ["sra", "run.zq"], None, 1)
        self.assertTrue(record["has_sra_normalized"])
        self.assertTrue(record["has_sra_lite"])


class NormalizeFiletypesTest(unittest.TestCase):
    """datastore_filetype はsnapshotへ保存する前に整える。

    実データには末尾空白の異体（`fastq` と `fastq `）が混在し、配列の順序も保証が
    ない。そのまま保存すると、中身が同じRunが週次差分で変化と判定される。
    """

    def test_strips_trailing_space_variants(self):
        self.assertEqual(normalize_filetypes(["fastq "]), ["fastq"])

    def test_sorts_so_that_order_changes_are_not_diffs(self):
        self.assertEqual(normalize_filetypes(["sra", "run.zq"]), ["run.zq", "sra"])
        self.assertEqual(normalize_filetypes(["run.zq", "sra"]), ["run.zq", "sra"])

    def test_removes_duplicates_created_by_stripping(self):
        self.assertEqual(normalize_filetypes(["fastq", "fastq "]), ["fastq"])

    def test_drops_empty_values(self):
        self.assertEqual(normalize_filetypes(["sra", "", None, "   "]), ["sra"])

    def test_returns_empty_list_for_missing_input(self):
        self.assertEqual(normalize_filetypes(None), [])
        self.assertEqual(normalize_filetypes([]), [])


class CatalogQueryTest(unittest.TestCase):
    def test_selects_columns_needed_for_weekly_diff(self):
        query = catalog_query("s3://bucket/*", ("SRR",), None)
        for column in ("acc", "mbytes", "datastore_filetype", "releasedate", "run_file_version"):
            self.assertIn(column, query)

    def test_orders_by_accession_so_snapshots_can_be_merge_scanned(self):
        self.assertIn("ORDER BY acc", catalog_query("s3://bucket/*", ("SRR",), None))

    def test_restricts_to_public_runs(self):
        self.assertIn("consent = 'public'", catalog_query("s3://bucket/*", ("SRR",), None))

    def test_restricts_to_a_single_selected_prefix(self):
        query = catalog_query("s3://bucket/*", ("SRR",), None)
        self.assertIn("starts_with(acc, 'SRR')", query)
        self.assertNotIn("'ERR'", query)

    def test_selects_every_listed_prefix(self):
        query = catalog_query("s3://bucket/*", ("DRR", "ERR", "SRR"), None)
        for prefix in ("DRR", "ERR", "SRR"):
            self.assertIn(f"starts_with(acc, '{prefix}')", query)

    def test_validates_prefixes_before_interpolating(self):
        with self.assertRaises(ValueError):
            catalog_query("s3://bucket/*", ("SRR') OR (1=1",), None)

    def test_appends_resume_predicate(self):
        self.assertIn("acc > 'SRR000100'", catalog_query("s3://bucket/*", ("SRR",), "SRR000100"))


class FormatBytesTest(unittest.TestCase):
    def test_decimal_uses_powers_of_1000(self):
        self.assertEqual(format_decimal_bytes(0), "0.000 PB")
        self.assertEqual(format_decimal_bytes(2 * 10**15), "2.000 PB")

    def test_binary_uses_powers_of_1024(self):
        self.assertEqual(format_binary_bytes(2 * 2**50), "2.000 PiB")


class CheckpointTest(unittest.TestCase):
    def test_round_trips_through_text(self):
        original = Checkpoint(last_accession="SRR001196", rows_written=1234,
                              prefixes=("DRR", "ERR", "SRR"))
        restored = Checkpoint.parse(original.serialize())
        self.assertEqual(restored.last_accession, "SRR001196")
        self.assertEqual(restored.rows_written, 1234)
        self.assertEqual(restored.prefixes, ("DRR", "ERR", "SRR"))

    def test_records_prefixes_so_a_resume_with_other_prefixes_can_be_refused(self):
        text = Checkpoint("SRR000001", 1, ("ERR", "SRR")).serialize()
        self.assertEqual(text, "SRR000001\t1\tERR,SRR\n")

    def test_parse_returns_none_for_empty_text(self):
        self.assertIsNone(Checkpoint.parse(""))

    def test_parse_rejects_malformed_text(self):
        with self.assertRaises(ValueError):
            Checkpoint.parse("garbage-without-tab")

    def test_parse_rejects_checkpoint_without_prefixes(self):
        with self.assertRaises(ValueError):
            Checkpoint.parse("SRR000001\t5")

    def test_parse_validates_the_accession(self):
        with self.assertRaises(ValueError):
            Checkpoint.parse("NOTANACC\t5\tSRR")

    def test_parse_validates_the_prefixes(self):
        with self.assertRaises(ValueError):
            Checkpoint.parse("SRR000001\t5\tSRX")


class CommandLineTest(unittest.TestCase):
    """通信しない --dry-run で、引数と再開の扱いだけを確かめる。"""

    def setUp(self):
        import tempfile
        self.directory = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.directory)

    def run_main(self, *arguments):
        import contextlib, io
        from survey_catalog import main
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(["--output-directory", self.directory, "--label", "t", "--dry-run",
                         *arguments])

    def write_checkpoint(self, text):
        import gzip, os
        with open(os.path.join(self.directory, "t-checkpoint.tsv"), "w") as handle:
            handle.write(text)
        gzip.open(os.path.join(self.directory, "t-runs.jsonl.gz"), "wt").close()

    def test_requires_prefix_so_that_the_target_is_always_explicit(self):
        with self.assertRaises(SystemExit) as raised:
            self.run_main()
        self.assertNotEqual(raised.exception.code, 0)

    def test_accepts_several_prefixes(self):
        self.assertEqual(self.run_main("--prefix", "SRR", "ERR", "DRR"), 0)

    def test_rejects_unknown_prefix(self):
        with self.assertRaises(SystemExit):
            self.run_main("--prefix", "SRX")

    def test_resumes_when_prefixes_match_the_checkpoint(self):
        self.write_checkpoint("ERR000001\t1\tERR,SRR\n")
        self.assertEqual(self.run_main("--prefix", "SRR", "ERR"), 0)

    def test_refuses_to_append_to_rows_left_without_a_checkpoint(self):
        """最初の checkpoint より前に止まると、先頭から取り直した行が既存の行へ重なる。"""
        import gzip, os
        gzip.open(os.path.join(self.directory, "t-runs.jsonl.gz"), "wt").close()
        with self.assertRaises(SystemExit) as raised:
            self.run_main("--prefix", "SRR")
        self.assertIn("t-runs.jsonl.gz", str(raised.exception))

    def test_explains_an_unreadable_checkpoint_instead_of_a_traceback(self):
        self.write_checkpoint("SRR000001\t5\n")
        with self.assertRaises(SystemExit) as raised:
            self.run_main("--prefix", "SRR")
        self.assertIn("checkpoint", str(raised.exception))

    def test_refuses_to_resume_with_different_prefixes(self):
        """続きを書くと、別の対象の行が同じsnapshotへ混ざる。"""
        self.write_checkpoint("SRR000001\t1\tSRR\n")
        with self.assertRaises(SystemExit) as raised:
            self.run_main("--prefix", "DRR", "ERR", "SRR")
        self.assertIn("prefix", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
