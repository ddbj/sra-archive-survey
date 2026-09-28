"""compare_archives.py の純粋ロジックのテスト。DuckDB も Parquet も要らない。"""

import datetime
import os
import tempfile
import unittest

from compare_archives import (
    ARCHIVES,
    catalog_query,
    find_missing,
    format_date,
    load_peer,
    missing_header,
    missing_row,
    split_catalog,
    write_tsv,
)


def write(directory, name, text):
    path = os.path.join(directory, name)
    with open(path, "w") as handle:
        handle.write(text)
    return path


class LoadPeerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_accession_and_bytes(self):
        path = write(self.dir, "a.tsv", "accession\tbytes\nERR1\t100\n")
        self.assertEqual(load_peer([path], has_size=True), {"ERR1": 100})

    def test_merges_several_files(self):
        """EBIの実測は本走査と再試行の2ファイルに分かれて出る。"""
        first = write(self.dir, "a.tsv", "accession\tbytes\nERR1\t100\n")
        retry = write(self.dir, "b.tsv", "accession\tbytes\nERR2\t200\n")
        self.assertEqual(load_peer([first, retry], has_size=True), {"ERR1": 100, "ERR2": 200})

    def test_accepts_same_accession_with_same_size_twice(self):
        first = write(self.dir, "a.tsv", "ERR1\t100\n")
        again = write(self.dir, "b.tsv", "ERR1\t100\n")
        self.assertEqual(load_peer([first, again], has_size=True), {"ERR1": 100})

    def test_rejects_same_accession_with_conflicting_size(self):
        """黙って後勝ちにすると、どちらの値が正しいか分からないまま集計が進む。"""
        first = write(self.dir, "a.tsv", "ERR1\t100\n")
        other = write(self.dir, "b.tsv", "ERR1\t999\n")
        with self.assertRaises(ValueError):
            load_peer([first, other], has_size=True)

    def test_ignores_size_column_when_not_expected(self):
        path = write(self.dir, "a.txt", "drx\nDRX1\nDRX2\n")
        self.assertEqual(load_peer([path], has_size=False), {"DRX1": None, "DRX2": None})

    def test_skips_blank_lines(self):
        path = write(self.dir, "a.txt", "DRX1\n\n  \nDRX2\n")
        self.assertEqual(set(load_peer([path], has_size=False)), {"DRX1", "DRX2"})


RELEASED = datetime.date(2011, 3, 18)


def row(acc, experiment, filetypes, consent="public", releasedate=RELEASED):
    return (acc, experiment, 10, filetypes, consent, releasedate)


class SplitCatalogTest(unittest.TestCase):
    """カタログ行を、本家側に現れるものと NCBI にしか無いものへ分ける。"""

    def test_keeps_rows_whose_key_the_peer_has(self):
        catalog, _ = split_catalog([row("ERR1", "ERX1", ["sra"])], {"ERR1": None}, "acc")
        self.assertIn("ERR1", catalog)

    def test_lists_runs_only_ncbi_has_with_sra(self):
        _, ncbi_only = split_catalog([row("ERR2", "ERX2", ["sra"])], {"ERR1": None}, "acc")
        self.assertEqual(ncbi_only, [("ERR2", "ERR2")])

    def test_does_not_list_ncbi_rows_without_sra(self):
        """NCBI側に sra が無ければ、NCBIにしか無い実体とは言えない。"""
        _, ncbi_only = split_catalog([row("ERR2", "ERX2", ["fastq"])], {}, "acc")
        self.assertEqual(ncbi_only, [])

    def test_keeps_every_run_of_an_experiment_only_ncbi_has(self):
        """1 DRX に複数 DRR がぶら下がる。DRX 単位で比べても DRR を全部残す。"""
        rows = [row("DRR1", "DRX1", ["sra"]), row("DRR2", "DRX1", ["sra"])]
        _, ncbi_only = split_catalog(rows, {}, "experiment")
        self.assertEqual(sorted(ncbi_only), [("DRX1", "DRR1"), ("DRX1", "DRR2")])

    def test_prefers_a_row_with_sra_when_an_experiment_has_several(self):
        """同じ DRX の片方だけが sra を持つなら、その DRX は NCBI にあると見なす。"""
        rows = [row("DRR1", "DRX1", ["fastq"]), row("DRR2", "DRX1", ["sra"])]
        catalog, _ = split_catalog(rows, {"DRX1": None}, "experiment")
        self.assertTrue(catalog["DRX1"][2])

    def test_skips_rows_without_the_key(self):
        catalog, ncbi_only = split_catalog([row("DRR1", None, ["sra"])], {}, "experiment")
        self.assertEqual((catalog, ncbi_only), ({}, []))

    def test_treats_null_filetypes_as_lacking_sra(self):
        catalog, _ = split_catalog([row("ERR1", "ERX1", None)], {"ERR1": None}, "acc")
        self.assertFalse(catalog["ERR1"][2])

    def test_keeps_consent_and_releasedate_of_the_catalog_row(self):
        catalog, _ = split_catalog([row("ERR1", "ERX1", ["fastq"])], {"ERR1": None}, "acc")
        self.assertEqual(catalog["ERR1"][3:], ("public", RELEASED))


class FindMissingTest(unittest.TestCase):
    """本家にあって NCBI に無いものを、理由つきで並べる。"""

    def test_reports_accession_absent_from_catalog(self):
        self.assertEqual(find_missing({"ERR1": 100}, {}),
                         [("ERR1", 100, "not_in_ncbi_catalog", None, None)])

    def test_omits_accession_ncbi_has_with_sra(self):
        catalog = {"ERR1": ("ERR1", ["sra"], True, "public", RELEASED)}
        self.assertEqual(find_missing({"ERR1": 100}, catalog), [])

    def test_carries_consent_and_releasedate_of_runs_in_the_catalog(self):
        """カタログに行はあるのに sra が無い Run は、いつ公開されたかで事情が変わる。"""
        catalog = {"ERR1": ("ERR1", ["fastq"], False, "public", RELEASED)}
        self.assertEqual(find_missing({"ERR1": 100}, catalog),
                         [("ERR1", 100, "in_catalog_no_sra", "public", RELEASED)])

    def test_reports_row_without_filetype_info(self):
        catalog = {"ERR1": ("ERR1", None, False, "public", None)}
        self.assertEqual(find_missing({"ERR1": 100}, catalog)[0][2],
                         "in_catalog_no_filetype_info")

    def test_sorts_by_accession(self):
        missing = find_missing({"ERR2": 1, "ERR1": 1}, {})
        self.assertEqual([m[0] for m in missing], ["ERR1", "ERR2"])


if __name__ == "__main__":
    unittest.main()


class MissingOutputTest(unittest.TestCase):
    def test_names_the_ebi_size_column_after_the_archive(self):
        self.assertEqual(missing_header(ARCHIVES["ebi"]),
                         ["accession", "ebi_bytes", "reason", "ncbi_consent", "ncbi_releasedate"])

    def test_omits_the_size_column_when_the_peer_has_no_size(self):
        self.assertEqual(missing_header(ARCHIVES["ddbj"]),
                         ["experiment", "reason", "ncbi_consent", "ncbi_releasedate"])

    def test_leaves_catalog_columns_blank_for_runs_absent_from_the_catalog(self):
        row_out = missing_row(ARCHIVES["ebi"], ("ERR1", 100, "not_in_ncbi_catalog", None, None))
        self.assertEqual(row_out, ["ERR1", 100, "not_in_ncbi_catalog", "", ""])

    def test_formats_the_ddbj_row_without_size(self):
        row_out = missing_row(ARCHIVES["ddbj"],
                              ("DRX1", None, "in_catalog_no_sra", "public", RELEASED))
        self.assertEqual(row_out, ["DRX1", "in_catalog_no_sra", "public", "2011-03-18"])


class FormatDateTest(unittest.TestCase):
    def test_writes_a_date_as_iso(self):
        self.assertEqual(format_date(RELEASED), "2011-03-18")

    def test_drops_the_time_of_a_timestamp(self):
        self.assertEqual(format_date(datetime.datetime(2011, 3, 18, 12, 30)), "2011-03-18")

    def test_writes_nothing_for_null(self):
        self.assertEqual(format_date(None), "")


class CatalogQueryTest(unittest.TestCase):
    def test_selects_consent_and_releasedate(self):
        query = catalog_query("ERR", "s3://bucket/*")
        self.assertIn("consent", query.split("FROM")[0])
        self.assertIn("releasedate", query.split("FROM")[0])


class WriteTsvTest(unittest.TestCase):
    """csv.writer は既定で行末に CRLF を書く。cut や comm で扱うと最終列に \\r が残る。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "out.tsv")

    def tearDown(self):
        self.tmp.cleanup()

    def test_writes_lf_line_endings(self):
        write_tsv(self.path, ["drx", "drr"], [("DRX1", "DRR1")])
        with open(self.path, "rb") as handle:
            self.assertNotIn(b"\r", handle.read())

    def test_writes_header_then_rows(self):
        write_tsv(self.path, ["a", "b"], [("1", "2"), ("3", "4")])
        with open(self.path) as handle:
            self.assertEqual(handle.read(), "a\tb\n1\t2\n3\t4\n")
