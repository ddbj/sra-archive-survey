#!/usr/bin/env python3
"""scale_diff.diff_snapshots の振る舞いを固定する。

差分で捉えるべきものは3種類ある。新規Run、既存Runへのファイル後付け、サイズの変化。
後付けは first_public でも last_updated でも検出できないことを実測で確かめたため、
差分そのものが唯一の検出手段になる。ここが壊れると増加の数字が静かに間違う。
"""

import unittest

from scale_diff import Snapshot, diff_snapshots


def snap(rows):
    """{accession: (fastq, submitted, sra, bam)} からスナップショットを作る。"""
    return Snapshot({acc: tuple(cols) for acc, cols in rows.items()})


class DiffSnapshotsTest(unittest.TestCase):
    def test_reports_no_change_for_identical_snapshots(self):
        s = snap({"ERR1": ("100;200", "300", "", "")})

        result = diff_snapshots(s, s)

        self.assertEqual(result.new_runs, [])
        self.assertEqual(result.changed_runs, [])
        self.assertEqual(result.added_files, 0)
        self.assertEqual(result.added_bytes, 0)

    def test_counts_a_newly_published_run(self):
        before = snap({"ERR1": ("100", "", "", "")})
        after = snap({"ERR1": ("100", "", "", ""), "ERR2": ("500;600", "700", "", "")})

        result = diff_snapshots(before, after)

        self.assertEqual(result.new_runs, ["ERR2"])
        self.assertEqual(result.added_files, 3)
        self.assertEqual(result.added_bytes, 1800)

    def test_counts_a_file_added_to_an_existing_run(self):
        """後付け。first_public は動かないので、差分でしか見えない。"""
        before = snap({"ERR1": ("100;200", "", "", "")})
        after = snap({"ERR1": ("100;200", "", "", "9000")})

        result = diff_snapshots(before, after)

        self.assertEqual(result.new_runs, [])
        self.assertEqual(result.changed_runs, ["ERR1"])
        self.assertEqual(result.added_files, 1)
        self.assertEqual(result.added_bytes, 9000)
        self.assertEqual(result.added_by_family["bam"], (1, 9000))

    def test_reports_a_size_change_without_double_counting_the_file(self):
        before = snap({"ERR1": ("100", "", "", "")})
        after = snap({"ERR1": ("150", "", "", "")})

        result = diff_snapshots(before, after)

        self.assertEqual(result.changed_runs, ["ERR1"])
        self.assertEqual(result.added_files, 0)
        self.assertEqual(result.added_bytes, 50)

    def test_reports_removals_as_negative_deltas(self):
        before = snap({"ERR1": ("100", "", "", "400")})
        after = snap({"ERR1": ("100", "", "", "")})

        result = diff_snapshots(before, after)

        self.assertEqual(result.removed_runs, [])
        self.assertEqual(result.changed_runs, ["ERR1"])
        self.assertEqual(result.added_files, -1)
        self.assertEqual(result.added_bytes, -400)

    def test_lists_a_run_that_disappeared(self):
        before = snap({"ERR1": ("100", "", "", ""), "ERR2": ("200", "", "", "")})
        after = snap({"ERR1": ("100", "", "", "")})

        result = diff_snapshots(before, after)

        self.assertEqual(result.removed_runs, ["ERR2"])
        self.assertEqual(result.added_files, -1)
        self.assertEqual(result.added_bytes, -200)

    def test_sums_beyond_double_precision_exactly(self):
        """総容量は 10**16 台に達する。float で数えると静かにずれる。"""
        big = 9 * 10**15
        before = snap({"ERR1": (str(big), "", "", "")})
        after = snap({"ERR1": (str(big + 1), "", "", "")})

        result = diff_snapshots(before, after)

        self.assertEqual(result.added_bytes, 1)

    def test_ignores_empty_segments_in_semicolon_lists(self):
        before = snap({"ERR1": ("", "", "", "")})
        after = snap({"ERR1": ("100;;200", "", "", "")})

        result = diff_snapshots(before, after)

        self.assertEqual(result.added_files, 2)
        self.assertEqual(result.added_bytes, 300)


if __name__ == "__main__":
    unittest.main()
