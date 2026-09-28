"""シェルスクリプトのオプション解析のテスト。

どの確認も通信の前に止まることを前提にしている。止まらずに走り出した場合に
外部サービスへ要求を投げないよう、PATH から curl を外して実行する。
"""

import os
import subprocess
import tempfile
import unittest

from shell_test_support import path_without_curl

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SURVEY_SIZES = os.path.join(HERE, "survey_sizes.sh")
SURVEY_CRAM = os.path.join(HERE, "survey_cram_sizes.sh")
EXTRACT_LIVELIST = os.path.join(HERE, "extract_livelist_runs.sh")
SURVEY_EBI_SRA = os.path.join(ROOT, "cross_archive", "survey_ebi_sra_sizes.sh")
SCRIPTS = [SURVEY_SIZES, SURVEY_CRAM, EXTRACT_LIVELIST, SURVEY_EBI_SRA]


class ShellOptionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.path.join(self.tmp.name, "cwd")
        os.makedirs(self.cwd)
        self.env = dict(os.environ, PATH=path_without_curl(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, script, *arguments):
        return subprocess.run(["bash", script, *arguments], cwd=self.cwd, env=self.env,
                              capture_output=True, text=True, timeout=30)

    def assert_stopped_before_work(self, result, message):
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn(message, result.stderr)
        self.assertEqual(os.listdir(self.cwd), [], "作業ディレクトリに何か作った")

    def test_every_script_rejects_a_misspelled_option(self):
        """環境変数と違い、綴りを間違えたら既定値で走り出さずに止まる。"""
        for script in SCRIPTS:
            with self.subTest(script=os.path.basename(script)):
                result = self.run_script(script, "--slep", "10", "out")
                self.assert_stopped_before_work(result, "不明なオプション: --slep")

    def test_every_script_prints_usage_with_help(self):
        for script in SCRIPTS:
            with self.subTest(script=os.path.basename(script)):
                result = self.run_script(script, "--help")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("使い方", result.stdout)
                self.assertEqual(os.listdir(self.cwd), [])

    def test_numeric_options_reject_non_integers(self):
        cases = [
            (SURVEY_SIZES, ["--sleep", "5s"]),
            (SURVEY_SIZES, ["--from-year", "twenty"]),
            (SURVEY_CRAM, ["--to-year", "2026.5"]),
            (SURVEY_EBI_SRA, ["--dedup", "x.gz", "--batch", "-1"]),
            (SURVEY_EBI_SRA, ["--dedup", "x.gz", "--limit", "two"]),
        ]
        for script, options in cases:
            with self.subTest(script=os.path.basename(script), options=options):
                result = self.run_script(script, *options, "out")
                self.assert_stopped_before_work(result, "整数")

    def test_option_without_value_is_rejected(self):
        for script, option in [(SURVEY_SIZES, "--sleep"), (EXTRACT_LIVELIST, "--livelist"),
                               (SURVEY_EBI_SRA, "--dedup")]:
            with self.subTest(script=os.path.basename(script)):
                result = self.run_script(script, "out", option)
                self.assert_stopped_before_work(result, "値が要る")

    def test_second_output_directory_is_rejected(self):
        result = self.run_script(SURVEY_SIZES, "out1", "out2")
        self.assert_stopped_before_work(result, "出力先は1つ")

    def test_from_year_after_to_year_is_rejected(self):
        result = self.run_script(SURVEY_SIZES, "--from-year", "2020", "--to-year", "2010", "out")
        self.assert_stopped_before_work(result, "--from-year")

    def test_survey_ebi_sra_sizes_requires_dedup(self):
        result = self.run_script(SURVEY_EBI_SRA, "out")
        self.assert_stopped_before_work(result, "--dedup")

    def test_ignores_the_old_environment_variables(self):
        """旧版の環境変数が export されたままでも、黙って効かない。"""
        self.env["DEDUP"] = os.path.join(self.tmp.name, "exists.gz")
        open(self.env["DEDUP"], "w").close()
        result = self.run_script(SURVEY_EBI_SRA, "out")
        self.assert_stopped_before_work(result, "--dedup")


if __name__ == "__main__":
    unittest.main()
