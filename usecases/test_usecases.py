"""usecases/ のラッパーのテスト。

ラッパー自身の判断（対象の決め方、前回の探し方、再開、止まる条件）だけを確かめる。
呼び出す先のスクリプトは各ディレクトリのテストで確かめている。

python3 は呼ばれたら失敗する偽物に差し替え、curl は PATH から外す。ラッパーの誤りで
本物の Parquet・EBI・DDBJ へ要求が飛ばないようにするためである。
"""

import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = ("bash", "gzip", "awk", "sort", "comm", "date", "mkdir", "cat", "head", "tail",
         "wc", "grep", "tee", "ls", "rm", "touch", "dirname", "basename", "printf", "mv",
         "cut", "tr", "seq")
FAKE_PYTHON = "#!/bin/sh\necho \"偽のpython3が呼ばれた: $*\" >&2\nexit 99\n"


def isolated_path(directory):
    bin_dir = os.path.join(directory, "bin")
    os.makedirs(bin_dir)
    for tool in TOOLS:
        found = shutil.which(tool)
        if found:
            os.symlink(found, os.path.join(bin_dir, tool))
    fake = os.path.join(bin_dir, "python3")
    with open(fake, "w") as handle:
        handle.write(FAKE_PYTHON)
    os.chmod(fake, 0o755)
    return bin_dir


def touch(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write(text)


class WrapperTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.tmp.name, "out")
        self.env = dict(os.environ, PATH=isolated_path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def wrapper(self, name, *arguments):
        return subprocess.run(["bash", os.path.join(HERE, name), *arguments],
                              env=self.env, capture_output=True, text=True, timeout=30)

    def assert_rejected(self, result, message):
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(message, result.stderr)
        self.assertNotIn("偽のpython3", result.stderr)


class CommonOptionTest(WrapperTestCase):
    WRAPPERS = ("ncbi_scale.sh", "ebi_scale.sh", "ebi_cram.sh", "ncbi_vs_ebi.sh",
                "ncbi_vs_ddbj.sh")

    def test_every_wrapper_prints_usage_with_help(self):
        for name in self.WRAPPERS:
            with self.subTest(wrapper=name):
                result = self.wrapper(name, "--help")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("使い方", result.stdout)

    def test_every_wrapper_rejects_a_misspelled_option(self):
        for name in self.WRAPPERS:
            with self.subTest(wrapper=name):
                result = self.wrapper(name, "--dryrun", self.out, "SRR")
                self.assert_rejected(result, "不明なオプション: --dryrun")
                self.assertFalse(os.path.exists(self.out))


class NcbiWeeklyTest(WrapperTestCase):
    def snapshot(self, directory, label, complete=True):
        touch(os.path.join(self.out, directory, f"{label}-runs.jsonl.gz"))
        if complete:
            touch(os.path.join(self.out, directory, f"{label}-report.json"), "{}")

    def dry_run(self, *arguments):
        return self.wrapper("ncbi_scale.sh", "--dry-run", "--label", "2026-09-27",
                            *arguments)

    def test_requires_at_least_one_prefix(self):
        self.assert_rejected(self.wrapper("ncbi_scale.sh", self.out), "PREFIX")

    def test_rejects_an_unknown_prefix(self):
        self.assert_rejected(self.wrapper("ncbi_scale.sh", self.out, "SRX"), "SRX")

    def test_rejects_a_label_that_is_not_a_date(self):
        result = self.wrapper("ncbi_scale.sh", "--label", "week39", self.out, "SRR")
        self.assert_rejected(result, "--label")

    def test_keeps_each_prefix_selection_in_its_own_directory(self):
        result = self.dry_run(self.out, "SRR", "DRR", "ERR")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--prefix DRR ERR SRR", result.stdout)
        self.assertIn(os.path.join(self.out, "DRR-ERR-SRR"), result.stdout)

    def test_writes_the_summary_next_to_the_snapshot(self):
        result = self.dry_run(self.out, "SRR")
        self.assertIn(os.path.join(self.out, "SRR", "2026-09-27-summary.txt"), result.stdout)
        self.assertIn(os.path.join(self.out, "SRR", "2026-09-27-summary.json"), result.stdout)

    def test_reports_without_a_diff_on_the_first_run(self):
        result = self.dry_run(self.out, "SRR")
        self.assertIn("前回の snapshot は無い", result.stdout)
        self.assertNotIn("snapshot_diff.py", result.stdout)
        self.assertIn("scale_report.py", result.stdout)

    def test_compares_with_the_latest_complete_earlier_snapshot(self):
        self.snapshot("SRR", "2026-09-13")
        self.snapshot("SRR", "2026-09-20")
        self.snapshot("SRR", "2026-09-21", complete=False)
        self.snapshot("SRR", "2026-10-04")
        self.snapshot("DRR-ERR-SRR", "2026-09-26")

        result = self.dry_run(self.out, "SRR")

        self.assertIn("--old " + os.path.join(self.out, "SRR", "2026-09-20-runs.jsonl.gz"),
                      result.stdout)
        self.assertIn("--diff-report", result.stdout)

    def test_previous_option_overrides_the_search(self):
        other = os.path.join(self.tmp.name, "elsewhere-runs.jsonl.gz")
        touch(other)
        self.snapshot("SRR", "2026-09-20")

        result = self.dry_run("--previous", other, self.out, "SRR")

        self.assertIn("--old " + other, result.stdout)

    def test_previous_option_must_point_to_an_existing_file(self):
        result = self.dry_run("--previous", "/nonexistent-runs.jsonl.gz", self.out, "SRR")
        self.assert_rejected(result, "/nonexistent-runs.jsonl.gz")

    def test_reuses_a_finished_snapshot_of_the_same_label(self):
        self.snapshot("SRR", "2026-09-27")
        result = self.dry_run(self.out, "SRR")
        self.assertNotIn("survey_catalog.py", result.stdout)
        self.assertIn("取得済み", result.stdout)
        self.assertIn("scale_report.py", result.stdout)

    def test_resumes_an_unfinished_snapshot_of_the_same_label(self):
        self.snapshot("SRR", "2026-09-27", complete=False)
        result = self.dry_run(self.out, "SRR")
        self.assertIn("survey_catalog.py", result.stdout)

    def test_dry_run_calls_nothing(self):
        result = self.dry_run(self.out, "SRR")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("偽のpython3", result.stderr)


def per_year_rows(last_year=2026, **statuses):
    """2008年から last_year までを OK で埋め、statuses で年ごとの行を差し替える。"""
    rows = []
    for year in range(2008, last_year + 1):
        for status in statuses.get(f"y{year}", ["OK"]):
            rows.append(f"{year}\t1\t1\t{status}\n")
    return rows


class EbiWeeklyTest(WrapperTestCase):
    PER_YEAR_HEADER = "year\tcount\trows\tstatus\n"

    def fetched(self, label, rows=None, complete=True):
        directory = os.path.join(self.out, label)
        touch(os.path.join(directory, "raw", "err_2010.tsv.gz"))
        touch(os.path.join(directory, "per_year.tsv"),
              self.PER_YEAR_HEADER + "".join(per_year_rows() if rows is None else rows))
        if complete:
            touch(os.path.join(directory, "total.txt"))
        return directory

    def dry_run(self, *arguments):
        return self.wrapper("ebi_scale.sh", "--dry-run", "--label", "2026-09-27", *arguments)

    def test_fetches_into_a_directory_named_after_the_label(self):
        result = self.dry_run("--sleep", "10", self.out)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("survey_sizes.sh --sleep 10 " + os.path.join(self.out, "2026-09-27"),
                      result.stdout)

    def test_compares_with_the_latest_complete_earlier_fetch(self):
        self.fetched("2026-09-13")
        self.fetched("2026-09-20")
        self.fetched("2026-09-22", complete=False)
        self.fetched("2026-10-04")

        result = self.dry_run(self.out)

        self.assertIn(os.path.join(self.out, "2026-09-20", "raw"), result.stdout)
        self.assertIn("scale_diff_2026-09-20--2026-09-27.txt", result.stdout)

    def test_skips_an_earlier_fetch_with_a_failed_year(self):
        self.fetched("2026-09-13")
        self.fetched("2026-09-20", rows=per_year_rows(y2010=["MISMATCH(http=200,after=1)"]))

        result = self.dry_run(self.out)

        self.assertIn(os.path.join(self.out, "2026-09-13", "raw"), result.stdout)

    def test_skips_an_earlier_fetch_that_covers_only_some_years(self):
        """--from-year で1年だけ取った結果を前回にすると、他の年が全件「新規」に見える。"""
        self.fetched("2026-09-13")
        self.fetched("2026-09-20", rows=["2025\t1\t1\tOK\n"])

        result = self.dry_run(self.out)

        self.assertIn(os.path.join(self.out, "2026-09-13", "raw"), result.stdout)

    def test_expects_years_up_to_the_year_of_each_label(self):
        """年をまたいだ直後でも、前年に取った結果は前回として使える。"""
        self.fetched("2025-12-28", rows=per_year_rows(last_year=2025))
        result = self.wrapper("ebi_scale.sh", "--dry-run", "--label", "2026-01-04", self.out)
        self.assertIn(os.path.join(self.out, "2025-12-28", "raw"), result.stdout)

    def test_stops_before_the_diff_when_years_are_missing(self):
        self.fetched("2026-09-20")
        self.fetched("2026-09-27", rows=["2025\t1\t1\tOK\n", "2026\t1\t1\tOK\n"])

        result = self.wrapper("ebi_scale.sh", "--label", "2026-09-27", self.out)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("2008", result.stderr)
        self.assertNotIn("偽のpython3", result.stderr)

    def test_reuses_a_finished_fetch_of_the_same_label(self):
        self.fetched("2026-09-27")
        result = self.dry_run(self.out)
        self.assertNotIn("survey_sizes.sh", result.stdout)
        self.assertIn("取得済み", result.stdout)

    def test_stops_before_the_diff_when_a_year_failed(self):
        """欠けた年は差分で全件「消滅」に見える。差分を出さずに止める。"""
        self.fetched("2026-09-20")
        self.fetched("2026-09-27", rows=per_year_rows(y2011=["MISMATCH(http=200,after=5)"]))

        result = self.wrapper("ebi_scale.sh", "--label", "2026-09-27", self.out)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("2011", result.stderr)
        self.assertIn("--from-year 2011 --to-year 2011", result.stderr)
        self.assertNotIn("偽のpython3", result.stderr)

    def test_guides_the_retry_when_the_fetch_itself_reports_failed_years(self):
        """survey_sizes.sh が失敗年で終了コード1を返しても、ラッパーの案内まで届かせる。

        curl を外してあるので、どの年も取得に失敗する。通信はしない。
        """
        result = self.wrapper("ebi_scale.sh", "--label", "2026-09-27", "--sleep", "0", self.out)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("このコマンドをもう一度実行する", result.stderr)

    def test_later_success_of_a_failed_year_counts_as_success(self):
        """survey_sizes.sh は per_year.tsv へ追記する。同じ年は最後の行を採る。"""
        self.fetched("2026-09-20")
        self.fetched("2026-09-27", rows=per_year_rows(y2011=["MISMATCH(http=200,after=5)", "OK"]))
        result = self.dry_run(self.out)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("scale_diff.py", result.stdout)


class EbiCramTest(WrapperTestCase):
    def test_forwards_options_to_survey_cram_sizes(self):
        result = self.wrapper("ebi_cram.sh", "--sleep", "x", self.out)
        self.assert_rejected(result, "整数")


class NcbiVsEbiTest(WrapperTestCase):
    def dry_run(self, *arguments):
        return self.wrapper("ncbi_vs_ebi.sh", "--dry-run", *arguments)

    def test_runs_the_three_steps_in_order(self):
        result = self.dry_run(self.out)
        self.assertEqual(result.returncode, 0, result.stderr)
        steps = [line for line in result.stdout.splitlines() if line.startswith("+")]
        self.assertIn("extract_livelist_runs.sh", steps[0])
        self.assertIn("survey_ebi_sra_sizes.sh", steps[1])
        self.assertIn("--dedup " + os.path.join(self.out, "livelist",
                                                "livelist_run_dedup.tsv.gz"), steps[1])
        self.assertIn("compare_archives.py --archive ebi", steps[2])

    def test_reuses_an_extracted_livelist(self):
        touch(os.path.join(self.out, "livelist", "livelist_run_dedup.tsv.gz"))
        result = self.dry_run(self.out)
        self.assertNotIn("extract_livelist_runs.sh", result.stdout)

    def test_forwards_the_local_livelist(self):
        result = self.dry_run("--livelist", "/data/livelist.gz", self.out)
        self.assertIn("--livelist /data/livelist.gz", result.stdout)

    def test_says_the_local_livelist_is_unused_when_already_extracted(self):
        touch(os.path.join(self.out, "livelist", "livelist_run_dedup.tsv.gz"))
        result = self.dry_run("--livelist", "/data/livelist.gz", self.out)
        self.assertIn("--livelist は使わない", result.stdout)


class NcbiVsDdbjTest(WrapperTestCase):
    def test_runs_the_steps_in_order(self):
        result = self.wrapper("ncbi_vs_ddbj.sh", "--dry-run", "--delay", "2", self.out)
        self.assertEqual(result.returncode, 0, result.stderr)
        steps = [line for line in result.stdout.splitlines() if line.startswith("+")]
        scripts = [next((word for word in step.split() if word.endswith(".py")), step)
                   for step in steps]
        self.assertEqual([os.path.basename(script) for script in scripts],
                         ["scan_ddbj_experiments.py", "compare_archives.py", "+ write_drx_list",
                          "fetch_ddbj_runs.py", "fetch_ddbj_status.py", "label_ddbj_results.py"])
        for step in (steps[0], steps[3], steps[4]):
            self.assertIn("--delay 2", step)
        self.assertIn("--output-ddbj-only " + os.path.join(self.out, "ddbj-only-drr.tsv"),
                      steps[5])

    def test_rejects_a_negative_or_non_numeric_delay(self):
        for value in ("-1", "fast"):
            with self.subTest(value=value):
                result = self.wrapper("ncbi_vs_ddbj.sh", "--delay", value, self.out)
                self.assert_rejected(result, "--delay")


if __name__ == "__main__":
    unittest.main()
