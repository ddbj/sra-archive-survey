"""extract_livelist_runs.sh のテスト。ネットワークに接続しない。

実物の livelist.gz は 2.16 GB・約5,500万行で、先頭に EXPERIMENT が1,000万行以上
続く。件数を絞って実物を試すことができないため、実データで確認済みの落とし穴を
再現した小さな gzip を --livelist で渡して確かめる。
"""

import gzip
import os
import subprocess
import tempfile
import unittest

from shell_test_support import path_without_curl

HERE = os.path.dirname(os.path.abspath(__file__))
HEADER = ["ACCESSION", "SUBMISSION", "STATUS", "UPDATED", "TYPE",
          "CENTRE_NAME", "ALIAS", "MD5", "SRA_FILE", "FASTQ_FILE"]


def livelist_row(accession, status, row_type, sra, fastq, alias="a"):
    return [accession, "ERA1", status, "01-JAN-2020", row_type, "C", alias, "m", sra, fastq]


# 実データで確認されている性質をすべて入れる。
FIXTURE_ROWS = [
    livelist_row("ERX000001", "public", "EXPERIMENT", "N", "N"),   # 先頭は RUN ではない
    livelist_row("ERR000003", "public", "RUN", "Y", "Y"),
    livelist_row("ERR000001", "public", "RUN", "Y", "N"),
    # ALIAS にタブを含み、11列になる。SRA_FILE / FASTQ_FILE は行末から取らないとずれる
    livelist_row("ERR000002", "suppressed", "RUN", "N", "Y", alias="left\tright"),
    livelist_row("ERR000001", "public", "RUN", "Y", "N"),          # 隣り合わない重複
    livelist_row("ERS000001", "public", "SAMPLE", "N", "N"),
]


def write_fixture(path):
    with gzip.open(path, "wt") as handle:
        handle.write("\t".join(HEADER) + "\n")
        for row in FIXTURE_ROWS:
            handle.write("\t".join(row) + "\n")


def read_gz(path):
    with gzip.open(path, "rt") as handle:
        return handle.read().splitlines()


class ExtractLivelistRunsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fixture = os.path.join(self.tmp.name, "livelist.gz")
        self.out = os.path.join(self.tmp.name, "out")
        write_fixture(self.fixture)
        env = dict(os.environ, PATH=path_without_curl(self.tmp.name))
        self.result = subprocess.run(
            ["bash", os.path.join(HERE, "extract_livelist_runs.sh"),
             "--livelist", self.fixture, self.out],
            env=env, capture_output=True, text=True, timeout=60)

    def tearDown(self):
        self.tmp.cleanup()

    def test_exits_successfully(self):
        self.assertEqual(self.result.returncode, 0, self.result.stderr)

    def test_keeps_only_run_rows(self):
        rows = read_gz(os.path.join(self.out, "livelist_run.tsv.gz"))[1:]
        self.assertEqual(sorted(r.split("\t")[0] for r in rows),
                         ["ERR000001", "ERR000001", "ERR000002", "ERR000003"])

    def test_reads_flags_from_line_end_even_when_alias_contains_a_tab(self):
        rows = read_gz(os.path.join(self.out, "livelist_run.tsv.gz"))
        shifted = next(r for r in rows if r.startswith("ERR000002"))
        self.assertEqual(shifted, "ERR000002\tsuppressed\t01-JAN-2020\tN\tY")

    def test_removes_duplicates_that_are_not_adjacent(self):
        rows = read_gz(os.path.join(self.out, "livelist_run_dedup.tsv.gz"))
        self.assertEqual([r.split("\t")[0] for r in rows[1:]],
                         ["ERR000001", "ERR000002", "ERR000003"])

    def test_keeps_the_header_first_in_the_dedup_output(self):
        rows = read_gz(os.path.join(self.out, "livelist_run_dedup.tsv.gz"))
        self.assertEqual(rows[0], "accession\tstatus\tupdated\tsra_file\tfastq_file")

    def test_does_not_touch_the_network_when_given_a_local_file(self):
        self.assertNotIn("curl", self.result.stderr)

    def test_leaves_only_the_finished_outputs(self):
        self.assertEqual(sorted(os.listdir(self.out)),
                         ["livelist_run.tsv.gz", "livelist_run_dedup.tsv.gz"])


class BrokenLivelistTest(unittest.TestCase):
    """途中で切れた入力から作った一覧を、完成品と取り違えて後段へ渡さない。"""

    def test_fails_without_leaving_a_dedup_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = os.path.join(tmp, "livelist.gz")
            write_fixture(fixture)
            with open(fixture, "rb") as handle:
                truncated = handle.read()[:-20]
            with open(fixture, "wb") as handle:
                handle.write(truncated)
            out = os.path.join(tmp, "out")
            env = dict(os.environ, PATH=path_without_curl(tmp))

            result = subprocess.run(
                ["bash", os.path.join(HERE, "extract_livelist_runs.sh"),
                 "--livelist", fixture, out],
                env=env, capture_output=True, text=True, timeout=60)

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(os.path.exists(os.path.join(out, "livelist_run_dedup.tsv.gz")))


if __name__ == "__main__":
    unittest.main()
