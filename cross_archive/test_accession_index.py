"""accession_index.py のテスト。外部サービスへ接続しない。"""

import unittest

from accession_index import (
    classify_missing,
    drx_prefix_dir,
    normalize_accession,
    numeric_part,
    parse_listing,
)


class NumericPartTest(unittest.TestCase):
    """accessionは桁が増える。文字列順で比べると新しいものを取りこぼす。"""

    def test_extracts_digits_after_the_prefix(self):
        self.assertEqual(numeric_part("DRR000001"), 1)
        self.assertEqual(numeric_part("DRR1090836"), 1090836)

    def test_orders_across_digit_widths(self):
        """DRR1090836 は文字列順では DRR999999 より小さい。"""
        self.assertLess(numeric_part("DRR999999"), numeric_part("DRR1090836"))

    def test_rejects_malformed_accession(self):
        for bad in ("", "DRR", "XX123", "DRRabc"):
            with self.assertRaises(ValueError):
                numeric_part(bad)


class NormalizeAccessionTest(unittest.TestCase):
    def test_keeps_zero_padding_verbatim(self):
        """zero paddingは有意。DRR100 と DRR000100 は別物である。"""
        self.assertEqual(normalize_accession(" DRR000100 "), "DRR000100")

    def test_rejects_unknown_prefix(self):
        with self.assertRaises(ValueError):
            normalize_accession("XXX000001")

    def test_accepts_three_archives(self):
        for acc in ("SRR000001", "ERR000001", "DRR000001", "DRX000001"):
            self.assertEqual(normalize_accession(acc), acc)


class DrxPrefixDirTest(unittest.TestCase):
    """DDBJ の FTP は DRX の上位3桁でディレクトリを切る。"""

    def test_takes_first_three_digits(self):
        self.assertEqual(drx_prefix_dir("DRX976617"), "DRX976")

    def test_pads_short_accessions(self):
        self.assertEqual(drx_prefix_dir("DRX009126"), "DRX009")

    def test_rejects_non_drx(self):
        with self.assertRaises(ValueError):
            drx_prefix_dir("DRR000001")


class ParseListingTest(unittest.TestCase):
    """Apache の autoindex から accession だけを拾う。"""

    HTML = '''<html><body>
    <a href="?C=N;O=D">Name</a>
    <a href="/public/ddbj_database/">Parent Directory</a>
    <a href="DRX976617/">DRX976617/</a>
    <a href="DRX976618/">DRX976618/</a>
    </body></html>'''

    def test_extracts_directory_entries(self):
        self.assertEqual(parse_listing(self.HTML, "DRX"), ["DRX976617", "DRX976618"])

    def test_ignores_sort_links_and_parent(self):
        self.assertNotIn("?C=N;O=D", parse_listing(self.HTML, "DRX"))

    def test_returns_empty_for_empty_listing(self):
        self.assertEqual(parse_listing("<html></html>", "DRX"), [])

    def test_filters_by_prefix(self):
        html = '<a href="DRR000001/">x</a><a href="DRX000001/">y</a>'
        self.assertEqual(parse_listing(html, "DRR"), ["DRR000001"])


class ClassifyMissingTest(unittest.TestCase):
    """本家にあって NCBI に無いものを、なぜ無いかで分ける。"""

    def test_flags_accession_absent_from_catalog(self):
        self.assertEqual(classify_missing(None, None), "not_in_ncbi_catalog")

    def test_flags_row_without_filetype_info(self):
        """datastore_filetype が NULL の行がある。has_sra の判定から漏れる。"""
        self.assertEqual(classify_missing({"acc": "X"}, None), "in_catalog_no_filetype_info")

    def test_flags_row_lacking_sra(self):
        self.assertEqual(classify_missing({"acc": "X"}, ["fastq"]), "in_catalog_no_sra")

    def test_returns_none_when_sra_is_present(self):
        self.assertIsNone(classify_missing({"acc": "X"}, ["sra", "fastq"]))

    def test_treats_empty_filetype_list_as_no_sra(self):
        self.assertEqual(classify_missing({"acc": "X"}, []), "in_catalog_no_sra")


if __name__ == "__main__":
    unittest.main()
