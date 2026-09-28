"""label_ddbj_results.py と fetch_ddbj_status.py の純粋ロジックのテスト。通信しない。"""

import datetime
import unittest

from fetch_ddbj_status import candidate_dates
from label_ddbj_results import label_ddbj_only, label_ncbi_only, load_experiment_status

STATUS_HEADER = ("Accession\tSubmission\tStatus\tUpdated\tPublished\tType\tCenter\t"
                 "Visibility\tAlias\n")


def status_line(accession, status, kind="EXPERIMENT"):
    return f"{accession}\tDRA1\t{status}\t2020\t2020\t{kind}\tC\tpublic\ta\n"


class CandidateDatesTest(unittest.TestCase):
    def test_goes_back_one_day_at_a_time_from_today(self):
        dates = candidate_dates(datetime.date(2026, 9, 1), days=3)
        self.assertEqual(dates, ["20260901", "20260831", "20260830"])


class LoadExperimentStatusTest(unittest.TestCase):
    def test_keeps_only_wanted_experiments(self):
        lines = [STATUS_HEADER, status_line("DRX1", "public"), status_line("DRX2", "suppressed"),
                 status_line("DRR1", "public", kind="RUN")]
        self.assertEqual(load_experiment_status(lines, {"DRX2", "DRR1"}), {"DRX2": "suppressed"})


class LabelDdbjOnlyTest(unittest.TestCase):
    """DDBJ にしか無い DRX を、DRR 単位の1行へ広げる。"""

    def test_labels_runs_found_by_the_search_api(self):
        rows = label_ddbj_only(["DRX1"], [("DRX1", "DRR1"), ("DRX1", "DRR2")], [],
                               {"DRX1": "public"})
        self.assertEqual(rows, [["DRX1", "DRR1", "public", "search_api"],
                                ["DRX1", "DRR2", "public", "search_api"]])

    def test_keeps_experiments_the_api_did_not_return_with_a_blank_run(self):
        """suppressed / withdrawn は API が 404 を返す。DRR は分からないが DRX は残す。"""
        rows = label_ddbj_only(["DRX1"], [], ["DRX1"], {"DRX1": "suppressed"})
        self.assertEqual(rows, [["DRX1", "", "suppressed", "status_file_only"]])

    def test_marks_experiments_missing_from_the_status_file(self):
        rows = label_ddbj_only(["DRX1"], [("DRX1", "DRR1")], [], {})
        self.assertEqual(rows[0][2], "not_in_status")

    def test_sorts_by_experiment_then_run(self):
        rows = label_ddbj_only(["DRX2", "DRX1"], [("DRX2", "DRR3"), ("DRX1", "DRR1")], [], {})
        self.assertEqual([row[1] for row in rows], ["DRR1", "DRR3"])

    def test_ignores_runs_of_experiments_outside_the_target(self):
        """再開で前回分の行が残っていても、今回の対象だけを出す。"""
        rows = label_ddbj_only(["DRX1"], [("DRX1", "DRR1"), ("DRX9", "DRR9")], [], {})
        self.assertEqual([row[0] for row in rows], ["DRX1"])

    def test_refuses_when_some_experiments_were_never_looked_up(self):
        """引き当てが途中で止まったまま出すと、DDBJ にしか無いものを取りこぼす。"""
        with self.assertRaises(ValueError) as raised:
            label_ddbj_only(["DRX1", "DRX2"], [("DRX1", "DRR1")], [], {})
        self.assertIn("DRX2", str(raised.exception))


class LabelNcbiOnlyTest(unittest.TestCase):
    def test_labels_runs_with_ddbj_status_and_source(self):
        rows = label_ncbi_only([("DRX1", "DRR1"), ("DRX2", "DRR2")], {"DRX1": "public"})
        self.assertEqual(rows, [["DRX1", "DRR1", "public", "ncbi_parquet"],
                                ["DRX2", "DRR2", "not_in_status", "ncbi_parquet"]])


if __name__ == "__main__":
    unittest.main()
