"""aggregate_cram_sizes.py のテスト。

総バイト数が 2**53 を超えても狂わないこと、.cram / .crai / その他の分類、
bytes列とftp列の要素数不一致の扱いを固定する。
"""

import gzip
import tempfile
import unittest
from pathlib import Path

from aggregate_cram_sizes import aggregate, classify

HEADER = "run_accession\tsubmitted_bytes\tsubmitted_ftp\n"


def write_gz(path, rows):
    with gzip.open(path, "wt") as fh:
        fh.write(HEADER)
        for row in rows:
            fh.write("\t".join(row) + "\n")
    return str(path)


class ClassifyTest(unittest.TestCase):
    def test_classifies_cram_extension(self):
        self.assertEqual(classify("host/vol1/run/ERR100/ERR1/44614_3#4.cram"), "cram")

    def test_classifies_crai_extension(self):
        self.assertEqual(classify("host/vol1/run/ERR100/ERR1/44614_3#4.cram.crai"), "crai")

    def test_classifies_anything_else_as_other(self):
        self.assertEqual(classify("host/vol1/run/ERR100/ERR1/readme.txt"), "other")


class AggregateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_splits_cram_and_crai_by_filename_position(self):
        path = write_gz(self.tmp_path / "cram_2016.tsv.gz", [
            ("ERR1", "100;7", "h/a.cram;h/a.cram.crai"),
            ("ERR2", "200", "h/b.cram"),
        ])

        n_rows, mismatches, cnt, tot = aggregate([path])

        self.assertEqual(n_rows, 2)
        self.assertEqual(mismatches, 0)
        self.assertEqual((cnt["cram"], tot["cram"]), (2, 300))
        self.assertEqual((cnt["crai"], tot["crai"]), (1, 7))
        self.assertEqual((cnt["other"], tot["other"]), (0, 0))

    def test_sums_beyond_double_precision_exactly(self):
        big = 2**53  # 9,007,199,254,740,992
        path = write_gz(self.tmp_path / "cram_2021.tsv.gz", [
            ("ERR1", str(big), "h/a.cram"),
            ("ERR2", "1", "h/b.cram"),
        ])

        _, _, _, tot = aggregate([path])

        self.assertEqual(tot["cram"], big + 1)

    def test_counts_row_as_mismatch_when_list_lengths_differ(self):
        path = write_gz(self.tmp_path / "cram_2020.tsv.gz", [
            ("ERR1", "100;7", "h/a.cram"),
            ("ERR2", "200", "h/b.cram"),
        ])

        n_rows, mismatches, cnt, tot = aggregate([path])

        self.assertEqual(n_rows, 2)
        self.assertEqual(mismatches, 1)
        self.assertEqual((cnt["cram"], tot["cram"]), (1, 200))

    def test_rejects_unexpected_header(self):
        path = self.tmp_path / "cram_bad.tsv.gz"
        with gzip.open(path, "wt") as fh:
            fh.write("accession\tbytes\n")

        with self.assertRaises(SystemExit):
            aggregate([str(path)])


if __name__ == "__main__":
    unittest.main()
