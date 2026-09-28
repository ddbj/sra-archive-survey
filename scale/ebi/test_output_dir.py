"""出力先を省略したシェルスクリプトが、どこにも書かずに止まることのテスト。

既定の出力先を持たせると、リポジトリの中で実行したときにデータがリポジトリ内へ
書かれ、git add で紛れ込む。Python 側と同じく、出力先は必ず明示させる。
"""

import os
import subprocess
import tempfile
import unittest

from shell_test_support import path_without_curl

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SCRIPTS = [
    os.path.join(HERE, "survey_sizes.sh"),
    os.path.join(HERE, "survey_cram_sizes.sh"),
    os.path.join(HERE, "extract_livelist_runs.sh"),
    os.path.join(ROOT, "cross_archive", "survey_ebi_sra_sizes.sh"),
]


class RequiresOutputDirectoryTest(unittest.TestCase):
    def test_every_script_stops_without_an_output_directory(self):
        for script in SCRIPTS:
            with self.subTest(script=os.path.basename(script)), \
                 tempfile.TemporaryDirectory() as cwd, \
                 tempfile.TemporaryDirectory() as tools:
                env = dict(os.environ, PATH=path_without_curl(tools))
                result = subprocess.run(["bash", script], cwd=cwd, env=env,
                                        capture_output=True, text=True, timeout=30)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("出力先", result.stderr)
                self.assertEqual(os.listdir(cwd), [], "作業ディレクトリに何か作った")


if __name__ == "__main__":
    unittest.main()
