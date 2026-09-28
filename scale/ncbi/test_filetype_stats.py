"""filetype_stats.py のテスト。外部サービスへ接続しない。"""

import unittest

from filetype_stats import count_filetype_changes, count_filetypes

MIB = 1024 * 1024


def record(accession, filetypes, mbytes=10, normalized=True):
    return {
        "accession": accession,
        "sra_normalized_mbytes": mbytes,
        "has_sra_normalized": normalized,
        "datastore_filetype": sorted(filetypes),
    }


class CountFiletypesTest(unittest.TestCase):
    """snapshotに保存した形式配列から、形式ごとの規模を出す。"""

    def test_counts_runs_per_filetype(self):
        stats = count_filetypes([record("SRR1", ["sra", "run.zq"]),
                                 record("SRR2", ["sra", "fastq"])])
        self.assertEqual(stats["sra"]["run_count"], 2)
        self.assertEqual(stats["run.zq"]["run_count"], 1)
        self.assertEqual(stats["fastq"]["run_count"], 1)

    def test_sums_normalized_bytes_per_filetype(self):
        """その形式を持つRunのNormalized容量。形式自体のサイズではない。"""
        stats = count_filetypes([record("SRR1", ["sra", "run.zq"], mbytes=100)])
        self.assertEqual(stats["run.zq"]["normalized_bytes"], 100 * MIB)

    def test_skips_normalized_bytes_when_the_run_has_no_normalized(self):
        stats = count_filetypes([record("SRR1", ["run.zq"], mbytes=24, normalized=False)])
        self.assertEqual(stats["run.zq"]["run_count"], 1)
        self.assertEqual(stats["run.zq"]["normalized_bytes"], 0)

    def test_returns_empty_for_no_records(self):
        self.assertEqual(count_filetypes([]), {})

    def test_tolerates_records_without_the_array(self):
        """列を持たない旧snapshotを読んでも落ちない。"""
        self.assertEqual(count_filetypes([{"accession": "SRR1"}]), {})


class CountFiletypeChangesTest(unittest.TestCase):
    """差分から、形式ごとにどこで増減したかを出す。"""

    def test_counts_filetypes_of_added_runs(self):
        changes = [{"change": "added", "accession": "SRR1",
                    "record": record("SRR1", ["sra", "run.zq"])}]
        stats = count_filetype_changes(changes)
        self.assertEqual(stats["sra"]["added_runs"], 1)
        self.assertEqual(stats["run.zq"]["added_runs"], 1)

    def test_counts_filetypes_of_removed_runs(self):
        changes = [{"change": "removed", "accession": "SRR1",
                    "record": record("SRR1", ["sra"])}]
        self.assertEqual(count_filetype_changes(changes)["sra"]["removed_runs"], 1)

    def test_counts_filetypes_gained_by_existing_runs(self):
        changes = [{"change": "modified", "accession": "SRR1",
                    "fields": ["datastore_filetype"],
                    "before": record("SRR1", ["sra"]),
                    "after": record("SRR1", ["sra", "run.zq"])}]
        stats = count_filetype_changes(changes)
        self.assertEqual(stats["run.zq"]["gained"], 1)
        self.assertNotIn("sra", stats)

    def test_counts_filetypes_lost_by_existing_runs(self):
        changes = [{"change": "modified", "accession": "SRR1",
                    "fields": ["datastore_filetype"],
                    "before": record("SRR1", ["sra", "fastq"]),
                    "after": record("SRR1", ["sra"])}]
        self.assertEqual(count_filetype_changes(changes)["fastq"]["lost"], 1)

    def test_ignores_modifications_that_do_not_touch_the_array(self):
        changes = [{"change": "modified", "accession": "SRR1",
                    "fields": ["sra_normalized_mbytes"],
                    "before": record("SRR1", ["sra"]),
                    "after": record("SRR1", ["sra"])}]
        self.assertEqual(count_filetype_changes(changes), {})

    def test_keeps_the_four_kinds_separate(self):
        """新規Run由来と既存Runへの後付けを混ぜない。"""
        changes = [
            {"change": "added", "accession": "SRR1", "record": record("SRR1", ["run.zq"])},
            {"change": "modified", "accession": "SRR2", "fields": ["datastore_filetype"],
             "before": record("SRR2", ["sra"]), "after": record("SRR2", ["sra", "run.zq"])},
        ]
        stats = count_filetype_changes(changes)
        self.assertEqual(stats["run.zq"]["added_runs"], 1)
        self.assertEqual(stats["run.zq"]["gained"], 1)


if __name__ == "__main__":
    unittest.main()
