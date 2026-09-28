"""survey_sizes.sh のテスト。偽の curl を使い、EBI へ接続しない。"""

import os
import subprocess
import tempfile
import unittest

from shell_test_support import install_fake_curl, path_without_curl

HERE = os.path.dirname(os.path.abspath(__file__))
# 2**53 + 1。倍精度では表せず、awk で足すと 1 バイト単位で狂う。
ABOVE_DOUBLE_PRECISION = 9_007_199_254_740_993


class SurveySizesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        fixture = os.path.join(self.tmp.name, "search.tsv")
        with open(fixture, "w") as handle:
            handle.write("run_accession\tfastq_bytes\tsubmitted_bytes\tsra_bytes\tbam_bytes\n")
            handle.write(f"ERR000001\t{ABOVE_DOUBLE_PRECISION};1\t\t5\t\n")
            handle.write(f"ERR000002\t{ABOVE_DOUBLE_PRECISION}\t7\t\t\n")
        env = dict(os.environ, PATH=path_without_curl(self.tmp.name))
        install_fake_curl(env["PATH"], fixture, env)
        self.out = os.path.join(self.tmp.name, "out")
        self.result = subprocess.run(
            ["bash", os.path.join(HERE, "survey_sizes.sh"),
             "--from-year", "2010", "--to-year", "2010", "--sleep", "0", self.out],
            env=env, capture_output=True, text=True, timeout=60)
        self.expected_fastq = 2 * ABOVE_DOUBLE_PRECISION + 1

    def tearDown(self):
        self.tmp.cleanup()

    def test_exits_successfully_when_every_year_succeeded(self):
        self.assertEqual(self.result.returncode, 0, self.result.stderr)

    def test_keeps_the_raw_slice(self):
        self.assertTrue(os.path.exists(os.path.join(self.out, "raw", "err_2010.tsv.gz")))

    def test_per_year_bytes_are_exact_beyond_double_precision(self):
        with open(os.path.join(self.out, "per_year.tsv")) as handle:
            header, row = [line.rstrip("\n").split("\t") for line in handle][:2]
        fields = dict(zip(header, row))
        self.assertEqual(fields["status"], "OK")
        self.assertEqual(fields["fastq_files"], "3")
        self.assertEqual(int(fields["fastq_bytes"]), self.expected_fastq)
        self.assertEqual(int(fields["submitted_bytes"]), 7)

    def test_total_is_exact_beyond_double_precision(self):
        with open(os.path.join(self.out, "total.txt")) as handle:
            total = handle.read()
        self.assertIn(f"{self.expected_fastq:,}", total)


class SurveyCramSizesTest(unittest.TestCase):
    def test_total_starts_with_the_fetch_time_like_survey_sizes(self):
        """どの時点の EBI を測ったかが total.txt だけで分かるようにする。"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        fixture = os.path.join(tmp.name, "search.tsv")
        with open(fixture, "w") as handle:
            handle.write("run_accession\tsubmitted_bytes\tsubmitted_ftp\n"
                         "ERR000001\t10;2\ta/ERR000001.cram;a/ERR000001.cram.crai\n")
        env = dict(os.environ, PATH=path_without_curl(tmp.name))
        install_fake_curl(env["PATH"], fixture, env)
        out = os.path.join(tmp.name, "out")

        result = subprocess.run(
            ["bash", os.path.join(HERE, "survey_cram_sizes.sh"),
             "--from-year", "2010", "--to-year", "2010", "--sleep", "0", out],
            env=env, capture_output=True, text=True, timeout=60)

        self.assertEqual(result.returncode, 0, result.stderr)
        with open(os.path.join(out, "total.txt")) as handle:
            self.assertTrue(handle.readline().startswith("取得時刻(UTC): "))


class CountFailureTest(unittest.TestCase):
    """count が一時的に失敗すると、途中で切れた本文を検出する下限が無くなる。"""

    def run_script(self, script, header, extra_env=None, before=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        fixture = os.path.join(tmp.name, "search.tsv")
        with open(fixture, "w") as handle:
            handle.write(header + "ERR000001\t1\t\t\t\nERR000002\t1\t\t\t\n")
        env = dict(os.environ, PATH=path_without_curl(tmp.name))
        install_fake_curl(env["PATH"], fixture, env)
        env.update(extra_env or {})
        out = os.path.join(tmp.name, "out")
        if before:
            before(out)
        result = subprocess.run(
            ["bash", os.path.join(HERE, script),
             "--from-year", "2010", "--to-year", "2010", "--sleep", "0", out],
            env=env, capture_output=True, text=True, timeout=60)
        return result, out, tmp.name

    def status_of_2010(self, out):
        with open(os.path.join(out, "per_year.tsv")) as handle:
            rows = [line.split("\t") for line in handle][1:]
        return [row[3] for row in rows if row[0] == "2010"][-1]

    def test_rejects_a_short_body_when_the_first_count_failed(self):
        for script, header in (
                ("survey_sizes.sh",
                 "run_accession\tfastq_bytes\tsubmitted_bytes\tsra_bytes\tbam_bytes\n"),
                ("survey_cram_sizes.sh", "run_accession\tsubmitted_bytes\tsubmitted_ftp\n")):
            with self.subTest(script=script):
                marker = os.path.join(tempfile.mkdtemp(), "failed-once")
                result, out, _ = self.run_script(
                    script, header,
                    {"FAKE_CURL_COUNT": "3", "FAKE_CURL_FAIL_FIRST_COUNT": marker})
                self.assertTrue(self.status_of_2010(out).startswith("MISMATCH"), result.stdout)

    def test_exits_with_failure_when_a_year_failed(self):
        """合計は取れた年だけで出るので、終了コードで知らせないと欠けに気づけない。"""
        for script, header in (
                ("survey_sizes.sh",
                 "run_accession\tfastq_bytes\tsubmitted_bytes\tsra_bytes\tbam_bytes\n"),
                ("survey_cram_sizes.sh", "run_accession\tsubmitted_bytes\tsubmitted_ftp\n")):
            with self.subTest(script=script):
                result, _, _ = self.run_script(script, header, {"FAKE_CURL_COUNT": "3"})
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("--from-year 2010 --to-year 2010", result.stderr)

    def test_drops_an_earlier_raw_of_a_year_that_now_failed(self):
        """per_year.tsv では失敗なのに、合計にだけ前回の値が入るのを防ぐ。"""
        def stale_raw(out):
            os.makedirs(os.path.join(out, "raw"))
            with open(os.path.join(out, "raw", "err_2010.tsv.gz"), "wb") as handle:
                handle.write(b"stale")

        result, out, _ = self.run_script(
            "survey_sizes.sh",
            "run_accession\tfastq_bytes\tsubmitted_bytes\tsra_bytes\tbam_bytes\n",
            {"FAKE_CURL_COUNT": "3"}, before=stale_raw)

        self.assertFalse(os.path.exists(os.path.join(out, "raw", "err_2010.tsv.gz")))
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
