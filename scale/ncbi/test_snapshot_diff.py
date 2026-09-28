"""snapshot_diff.py の純粋ロジックのテスト。

外部サービスへ接続しない。DuckDBもParquetも要らない。
    python3 -m unittest discover -s scale/ncbi -v
"""

import unittest

from snapshot_diff import (
    ADDED_RUNS_SUFFIX,
    CHANGED_RUNS_HEADER,
    CHANGED_RUNS_SUFFIX,
    REMOVED_RUNS_SUFFIX,
    COMPARED_FIELDS,
    DiffTotals,
    apply_reverse_diff,
    changed_fields,
    check_same_prefixes,
    diff_snapshots,
    format_changed_row,
    reverse_operation,
    track_prefixes,
)


def record(accession, mbytes=10, normalized=True, lite=True, version=1,
           releasedate="2026-01-01", filetypes=None):
    if filetypes is None:
        filetypes = ([NORMALIZED for NORMALIZED in ["sra"] if normalized]
                     + ["run.zq"] * (1 if lite else 0))
    return {
        "accession": accession,
        "sra_normalized_mbytes": mbytes,
        "has_sra_normalized": normalized,
        "has_sra_lite": lite,
        "datastore_filetype": sorted(filetypes),
        "run_file_version": version,
        "releasedate": releasedate,
    }


class ChangedFieldsTest(unittest.TestCase):
    def test_reports_nothing_for_identical_records(self):
        self.assertEqual(changed_fields(record("SRR1"), record("SRR1")), ())

    def test_detects_size_change(self):
        before, after = record("SRR1", mbytes=10), record("SRR1", mbytes=20)
        self.assertEqual(changed_fields(before, after), ("sra_normalized_mbytes",))

    def test_detects_lite_appearing_later(self):
        """LiteはNCBIが後から生成する。既存Runにあとから付く。配列も同時に変わる。"""
        before, after = record("SRR1", lite=False), record("SRR1", lite=True)
        self.assertEqual(set(changed_fields(before, after)),
                         {"has_sra_lite", "datastore_filetype"})

    def test_detects_filetype_added_without_presence_flag_change(self):
        """fastqやbamの増減は有無フラグに出ない。配列を持つことで初めて追える。"""
        before = record("SRR1", filetypes=["run.zq", "sra"])
        after = record("SRR1", filetypes=["fastq", "run.zq", "sra"])
        self.assertEqual(changed_fields(before, after), ("datastore_filetype",))

    def test_ignores_order_differences_in_the_filetype_array(self):
        before = record("SRR1", filetypes=["run.zq", "sra"])
        after = record("SRR1", filetypes=["run.zq", "sra"])
        self.assertEqual(changed_fields(before, after), ())

    def test_detects_regenerated_files_via_version(self):
        before, after = record("SRR1", version=1), record("SRR1", version=2)
        self.assertEqual(changed_fields(before, after), ("run_file_version",))

    def test_reports_every_changed_field(self):
        before = record("SRR1", mbytes=10, version=1)
        after = record("SRR1", mbytes=20, version=2)
        self.assertEqual(set(changed_fields(before, after)),
                         {"sra_normalized_mbytes", "run_file_version"})

    def test_ignores_fields_outside_the_compared_set(self):
        """比較対象を絞る。取得時刻など毎回変わる列で全件modified扱いにしない。"""
        before, after = record("SRR1"), record("SRR1")
        after["fetched_at"] = "2026-08-10T00:00:00"
        self.assertEqual(changed_fields(before, after), ())

    def test_treats_missing_field_as_absent_for_old_snapshots(self):
        """旧形式のsnapshotは releasedate を持たない。欠損同士は差分にしない。"""
        before = {"accession": "SRR1", "sra_normalized_mbytes": 10,
                  "has_sra_normalized": True, "has_sra_lite": True}
        after = dict(before)
        self.assertEqual(changed_fields(before, after), ())

    def test_skips_field_absent_from_one_side_to_survive_schema_growth(self):
        """snapshotへ列を足した週に全件modified扱いになるのを防ぐ。

        旧snapshotは releasedate と run_file_version を持たない。欠損を「値なし」と
        して新snapshotの実値と比べると、変化していないRunまで modified になる。
        両方が持つフィールドだけを比べる。
        """
        before = {"accession": "SRR1", "sra_normalized_mbytes": 10}
        after = {"accession": "SRR1", "sra_normalized_mbytes": 10,
                 "releasedate": "2026-01-01", "run_file_version": 1}
        self.assertEqual(changed_fields(before, after), ())

    def test_still_detects_change_in_fields_present_on_both_sides(self):
        before = {"accession": "SRR1", "sra_normalized_mbytes": 10}
        after = {"accession": "SRR1", "sra_normalized_mbytes": 20, "releasedate": "2026-01-01"}
        self.assertEqual(changed_fields(before, after), ("sra_normalized_mbytes",))

    def test_compared_fields_exclude_the_accession_key(self):
        self.assertNotIn("accession", COMPARED_FIELDS)


class DiffSnapshotsTest(unittest.TestCase):
    """snapshotは accession 昇順。両者を同時に読み進めて突き合わせる。"""

    def diff(self, old, new):
        return list(diff_snapshots(iter(old), iter(new)))

    def test_reports_added_run_present_only_in_new(self):
        changes = self.diff([record("SRR1")], [record("SRR1"), record("SRR2")])
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["change"], "added")
        self.assertEqual(changes[0]["accession"], "SRR2")

    def test_reports_removed_run_present_only_in_old(self):
        changes = self.diff([record("SRR1"), record("SRR2")], [record("SRR1")])
        self.assertEqual(changes[0]["change"], "removed")
        self.assertEqual(changes[0]["accession"], "SRR2")

    def test_reports_modified_run_with_field_names(self):
        changes = self.diff([record("SRR1", mbytes=10)], [record("SRR1", mbytes=20)])
        self.assertEqual(changes[0]["change"], "modified")
        self.assertEqual(changes[0]["fields"], ["sra_normalized_mbytes"])
        self.assertEqual(changes[0]["before"]["sra_normalized_mbytes"], 10)
        self.assertEqual(changes[0]["after"]["sra_normalized_mbytes"], 20)

    def test_emits_nothing_when_snapshots_match(self):
        self.assertEqual(self.diff([record("SRR1"), record("SRR2")],
                                   [record("SRR1"), record("SRR2")]), [])

    def test_handles_empty_old_snapshot(self):
        changes = self.diff([], [record("SRR1")])
        self.assertEqual([c["change"] for c in changes], ["added"])

    def test_handles_empty_new_snapshot(self):
        changes = self.diff([record("SRR1")], [])
        self.assertEqual([c["change"] for c in changes], ["removed"])

    def test_keeps_accession_order_across_mixed_changes(self):
        old = [record("SRR1"), record("SRR2"), record("SRR4")]
        new = [record("SRR2", mbytes=99), record("SRR3"), record("SRR4")]
        changes = self.diff(old, new)
        self.assertEqual([(c["accession"], c["change"]) for c in changes],
                         [("SRR1", "removed"), ("SRR2", "modified"), ("SRR3", "added")])

    def test_orders_by_string_not_numeric_value(self):
        """accessionは文字列順で並ぶ。zero paddingがあるため数値順とは違う。"""
        old = [record("SRR000100"), record("SRR2")]
        new = [record("SRR000100"), record("SRR2")]
        self.assertEqual(self.diff(old, new), [])

    def test_rejects_unsorted_input_instead_of_silently_missing_changes(self):
        """順序が崩れたsnapshotを黙って処理すると差分を取りこぼす。"""
        with self.assertRaises(ValueError):
            self.diff([record("SRR2"), record("SRR1")], [record("SRR1")])


class ChangedRowTest(unittest.TestCase):
    """変化リストは accession だけでは何が起きたか分からない。種別を列に持つ。"""

    def test_joins_changed_fields_with_comma(self):
        change = {"change": "modified", "accession": "SRR1",
                  "fields": ["sra_normalized_mbytes", "has_sra_lite"]}
        self.assertEqual(format_changed_row(change),
                         "SRR1\tsra_normalized_mbytes,has_sra_lite")

    def test_header_names_both_columns(self):
        self.assertEqual(CHANGED_RUNS_HEADER, "accession\tchanged_fields")

    def test_extension_reflects_whether_the_file_has_columns(self):
        """1列だけの一覧は .txt、列を持つものは .tsv。拡張子で中身の形が分かる。"""
        self.assertTrue(CHANGED_RUNS_SUFFIX.endswith(".tsv"))
        self.assertTrue(ADDED_RUNS_SUFFIX.endswith(".txt"))
        self.assertTrue(REMOVED_RUNS_SUFFIX.endswith(".txt"))


class ReverseOperationTest(unittest.TestCase):
    """rdiff は「新しいsnapshotへ適用すると古いsnapshotになる」操作列である。"""

    def test_added_run_is_removed_when_going_back(self):
        change = {"change": "added", "accession": "SRR2", "record": record("SRR2")}
        self.assertEqual(reverse_operation(change), {"op": "remove", "accession": "SRR2"})

    def test_removed_run_is_inserted_when_going_back(self):
        original = record("SRR2")
        change = {"change": "removed", "accession": "SRR2", "record": original}
        self.assertEqual(reverse_operation(change), {"op": "insert", "record": original})

    def test_modified_run_is_restored_to_its_earlier_value(self):
        before, after = record("SRR1", mbytes=10), record("SRR1", mbytes=20)
        change = {"change": "modified", "accession": "SRR1", "fields": ["sra_normalized_mbytes"],
                  "before": before, "after": after}
        self.assertEqual(reverse_operation(change), {"op": "replace", "record": before})

    def test_rejects_unknown_change_kind(self):
        with self.assertRaises(ValueError):
            reverse_operation({"change": "wat", "accession": "SRR1"})


class ApplyReverseDiffTest(unittest.TestCase):
    def apply(self, new, operations):
        return list(apply_reverse_diff(iter(new), iter(operations)))

    def test_round_trips_back_to_the_old_snapshot(self):
        """差分を作り、それを新へ適用すると旧に戻る。これが逆向き差分の要件。"""
        old = [record("SRR1"), record("SRR2", mbytes=10), record("SRR4")]
        new = [record("SRR2", mbytes=99), record("SRR3"), record("SRR4")]
        operations = [reverse_operation(c) for c in diff_snapshots(iter(old), iter(new))]
        self.assertEqual(self.apply(new, operations), old)

    def test_insert_places_record_in_accession_order(self):
        operations = [{"op": "insert", "record": record("SRR1")}]
        self.assertEqual([r["accession"] for r in self.apply([record("SRR2")], operations)],
                         ["SRR1", "SRR2"])

    def test_remove_drops_the_record(self):
        operations = [{"op": "remove", "accession": "SRR2"}]
        result = self.apply([record("SRR1"), record("SRR2")], operations)
        self.assertEqual([r["accession"] for r in result], ["SRR1"])

    def test_replace_swaps_in_the_earlier_record(self):
        operations = [{"op": "replace", "record": record("SRR1", mbytes=10)}]
        result = self.apply([record("SRR1", mbytes=99)], operations)
        self.assertEqual(result[0]["sra_normalized_mbytes"], 10)

    def test_passes_through_untouched_records(self):
        result = self.apply([record("SRR1"), record("SRR2")], [])
        self.assertEqual([r["accession"] for r in result], ["SRR1", "SRR2"])


class DiffTotalsTest(unittest.TestCase):
    """週あたりの増加量。新規Runだけでなく既存Runへの追加も数える。"""

    def setUp(self):
        self.totals = DiffTotals()

    def test_counts_added_runs_and_their_normalized_bytes(self):
        self.totals.add({"change": "added", "accession": "SRR2",
                         "record": record("SRR2", mbytes=10)})
        report = self.totals.report()
        self.assertEqual(report["added_run_count"], 1)
        self.assertEqual(report["added_normalized_bytes"], 10 * 1024 * 1024)

    def test_counts_lite_bearing_bytes_of_added_runs_for_estimation(self):
        self.totals.add({"change": "added", "accession": "SRR2",
                         "record": record("SRR2", mbytes=10, lite=True)})
        self.totals.add({"change": "added", "accession": "SRR3",
                         "record": record("SRR3", mbytes=50, lite=False)})
        self.assertEqual(self.totals.report()["added_lite_bearing_normalized_bytes"],
                         10 * 1024 * 1024)

    def test_counts_removed_runs(self):
        self.totals.add({"change": "removed", "accession": "SRR2", "record": record("SRR2")})
        self.assertEqual(self.totals.report()["removed_run_count"], 1)

    def test_breaks_down_modifications_by_field(self):
        self.totals.add({"change": "modified", "accession": "SRR1",
                         "fields": ["sra_normalized_mbytes"],
                         "before": record("SRR1", mbytes=10), "after": record("SRR1", mbytes=20)})
        self.totals.add({"change": "modified", "accession": "SRR2",
                         "fields": ["has_sra_lite"],
                         "before": record("SRR2", lite=False), "after": record("SRR2", lite=True)})
        report = self.totals.report()
        self.assertEqual(report["modified_run_count"], 2)
        self.assertEqual(report["modified_by_field"]["sra_normalized_mbytes"], 1)
        self.assertEqual(report["modified_by_field"]["has_sra_lite"], 1)

    def test_measures_growth_from_existing_runs_separately(self):
        """既存Runのサイズ増加。新規Runの容量と混ぜると週次増加の内訳が消える。"""
        self.totals.add({"change": "modified", "accession": "SRR1",
                         "fields": ["sra_normalized_mbytes"],
                         "before": record("SRR1", mbytes=10),
                         "after": record("SRR1", mbytes=30)})
        self.assertEqual(self.totals.report()["existing_run_normalized_bytes_delta"],
                         20 * 1024 * 1024)

    def test_size_delta_can_be_negative_when_files_shrink(self):
        self.totals.add({"change": "modified", "accession": "SRR1",
                         "fields": ["sra_normalized_mbytes"],
                         "before": record("SRR1", mbytes=30),
                         "after": record("SRR1", mbytes=10)})
        self.assertEqual(self.totals.report()["existing_run_normalized_bytes_delta"],
                         -20 * 1024 * 1024)

    def test_counts_runs_that_gained_lite_later(self):
        self.totals.add({"change": "modified", "accession": "SRR1", "fields": ["has_sra_lite"],
                         "before": record("SRR1", lite=False), "after": record("SRR1", lite=True)})
        self.assertEqual(self.totals.report()["runs_gaining_lite"], 1)

    def test_counts_regenerated_runs(self):
        self.totals.add({"change": "modified", "accession": "SRR1",
                         "fields": ["run_file_version"],
                         "before": record("SRR1", version=1), "after": record("SRR1", version=2)})
        self.assertEqual(self.totals.report()["runs_with_regenerated_files"], 1)

    def test_counts_lite_of_removed_runs_separately(self):
        """消失分は新規分と合算しない。別々に扱う。"""
        self.totals.add({"change": "removed", "accession": "SRR2",
                         "record": record("SRR2", mbytes=100, lite=True)})
        report = self.totals.report(lite_ratio=0.5)
        self.assertEqual(report["removed_lite_run_count"], 1)
        self.assertEqual(report["removed_lite_bytes_estimated"], 50 * 1024 * 1024)

    def test_ignores_removed_runs_without_lite_in_the_lite_total(self):
        self.totals.add({"change": "removed", "accession": "SRR2",
                         "record": record("SRR2", mbytes=100, lite=False)})
        self.assertEqual(self.totals.report()["removed_lite_bytes_estimated"], 0)

    def test_estimates_lite_bytes_gained_by_existing_runs(self):
        """既存Runへ後から付いたLite。新規Runの分とは分けて数える。"""
        self.totals.add({"change": "modified", "accession": "SRR1",
                         "fields": ["has_sra_lite"],
                         "before": record("SRR1", mbytes=100, lite=False),
                         "after": record("SRR1", mbytes=100, lite=True)})
        report = self.totals.report(lite_ratio=0.5)
        self.assertEqual(report["gained_lite_bytes_estimated"], 50 * 1024 * 1024)

    def test_estimates_lite_bytes_of_the_week(self):
        self.totals.add({"change": "added", "accession": "SRR2",
                         "record": record("SRR2", mbytes=1000, lite=True)})
        report = self.totals.report(lite_ratio=0.5)
        self.assertEqual(report["added_lite_bytes_estimated"], 500 * 1024 * 1024)


class PrefixConsistencyTest(unittest.TestCase):
    """対象prefixが違うsnapshot同士を比べると、対象の差がそのまま増減に見える。"""

    def test_collects_prefixes_while_passing_records_through(self):
        seen = set()
        records = [record("DRR000001"), record("SRR000001")]

        passed = list(track_prefixes(records, seen))

        self.assertEqual(passed, records)
        self.assertEqual(seen, {"DRR", "SRR"})

    def test_accepts_snapshots_with_the_same_prefixes(self):
        check_same_prefixes({"ERR", "SRR"}, {"SRR", "ERR"})

    def test_rejects_snapshots_with_different_prefixes(self):
        with self.assertRaises(ValueError) as raised:
            check_same_prefixes({"SRR"}, {"DRR", "ERR", "SRR"})
        self.assertIn("DRR", str(raised.exception))


class CommandLineTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.directory = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.directory)

    def snapshot(self, name, accessions):
        import gzip, json, os
        path = os.path.join(self.directory, name)
        with gzip.open(path, "wt") as handle:
            for accession in accessions:
                handle.write(json.dumps(record(accession)) + "\n")
        return path

    def run_main(self, old, new):
        import contextlib, io
        from snapshot_diff import main
        output = self.directory + "/out"
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(["--old", old, "--new", new, "--output-directory", output,
                         "--label", "t"])
        return code, output

    def test_records_the_prefixes_in_the_report(self):
        import json
        old = self.snapshot("old.jsonl.gz", ["ERR000001", "SRR000001"])
        new = self.snapshot("new.jsonl.gz", ["ERR000001", "SRR000001", "SRR000002"])

        code, output = self.run_main(old, new)

        self.assertEqual(code, 0)
        with open(output + "/t-diff-report.json") as handle:
            self.assertEqual(json.load(handle)["prefixes"], ["ERR", "SRR"])

    def test_refuses_and_leaves_no_output_when_prefixes_differ(self):
        import os
        old = self.snapshot("old.jsonl.gz", ["SRR000001"])
        new = self.snapshot("new.jsonl.gz", ["DRR000001", "ERR000001", "SRR000001"])

        with self.assertRaises(SystemExit) as raised:
            self.run_main(old, new)

        self.assertIn("prefix", str(raised.exception))
        self.assertEqual(os.listdir(self.directory + "/out"), [])


if __name__ == "__main__":
    unittest.main()
