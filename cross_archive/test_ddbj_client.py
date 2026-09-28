"""ddbj_client.py のテスト。外部サービスへ接続しない。"""

import unittest

from ddbj_client import (
    DEFAULT_DELAY,
    build_search_url,
    chunked,
    extract_runs,
    listing_url,
    parse_status_line,
    plan,
    validate_delay,
)


class ChunkedTest(unittest.TestCase):
    """Search API は1リクエスト100件が上限なので、必ず分割して投げる。"""

    def test_splits_into_fixed_size_chunks(self):
        self.assertEqual(list(chunked([1, 2, 3, 4, 5], 2)), [[1, 2], [3, 4], [5]])

    def test_returns_nothing_for_empty_input(self):
        self.assertEqual(list(chunked([], 100)), [])

    def test_rejects_non_positive_size(self):
        with self.assertRaises(ValueError):
            list(chunked([1], 0))


class BuildSearchUrlTest(unittest.TestCase):
    def test_joins_accessions_with_comma(self):
        url = build_search_url(["DRX000001", "DRX000002"])
        self.assertIn("keywords=DRX000001%2CDRX000002", url)

    def test_requests_the_maximum_page_size(self):
        self.assertIn("perPage=100", build_search_url(["DRX000001"]))

    def test_drops_properties_to_keep_the_response_small(self):
        self.assertIn("includeProperties=false", build_search_url(["DRX000001"]))

    def test_rejects_more_than_the_api_limit(self):
        with self.assertRaises(ValueError):
            build_search_url([f"DRX{i:06d}" for i in range(101)])


class ListingUrlTest(unittest.TestCase):
    def test_points_at_the_prefix_directory(self):
        self.assertTrue(listing_url("DRX976").endswith("/DRX/DRX976/"))


class ExtractRunsTest(unittest.TestCase):
    """dbXrefs に sra-run として DRR が入る。DDBJ 自身が持つ対応関係である。"""

    def payload(self, items):
        return {"items": items}

    def test_pairs_experiment_with_its_runs(self):
        body = self.payload([{"identifier": "DRX009126",
                              "dbXrefs": [{"identifier": "DRR010000", "type": "sra-run"},
                                          {"identifier": "PRJDB1099", "type": "bioproject"}],
                              "dbXrefsCount": {"sra-run": 1}}])
        self.assertEqual(extract_runs(body), [("DRX009126", "DRR010000", 1)])

    def test_emits_every_run_when_an_experiment_has_several(self):
        body = self.payload([{"identifier": "DRX000620",
                              "dbXrefs": [{"identifier": "DRR000970", "type": "sra-run"},
                                          {"identifier": "DRR000971", "type": "sra-run"}],
                              "dbXrefsCount": {"sra-run": 2}}])
        self.assertEqual([r[1] for r in extract_runs(body)], ["DRR000970", "DRR000971"])

    def test_keeps_experiment_with_no_run_so_the_gap_is_visible(self):
        body = self.payload([{"identifier": "DRX000001", "dbXrefs": [], "dbXrefsCount": {}}])
        self.assertEqual(extract_runs(body), [("DRX000001", "", None)])

    def test_tolerates_missing_dbxrefs_field(self):
        self.assertEqual(extract_runs(self.payload([{"identifier": "DRX1"}])),
                         [("DRX1", "", None)])

    def test_returns_empty_for_empty_items(self):
        self.assertEqual(extract_runs(self.payload([])), [])


class ParseStatusLineTest(unittest.TestCase):
    """status ファイルは Accession/Submission/Status/.../Type/.../Visibility の列を持つ。"""

    HEADER = "Accession\tSubmission\tStatus\tUpdated\tPublished\tType\tCenter\tVisibility\tAlias"

    def test_reads_experiment_row(self):
        line = "DRX012537\tDRA000991\tsuppressed\t2020-01-01\t2019-01-01\tEXPERIMENT\tRIKEN\tpublic\tx"
        self.assertEqual(parse_status_line(line), ("DRX012537", "EXPERIMENT", "suppressed", "public"))

    def test_returns_none_for_header(self):
        self.assertIsNone(parse_status_line(self.HEADER))

    def test_returns_none_for_short_line(self):
        self.assertIsNone(parse_status_line("DRX1\tDRA1"))


if __name__ == "__main__":
    unittest.main()


class PlanTest(unittest.TestCase):
    """再開と件数制限を合わせて、今回投げる対象を決める。"""

    def test_skips_items_already_done(self):
        self.assertEqual(plan(["a", "b", "c"], done={"b"}, limit=None), ["a", "c"])

    def test_caps_at_limit_after_skipping(self):
        """limit は未処理分に掛ける。処理済みを数えると再開時に何も進まない。"""
        self.assertEqual(plan(["a", "b", "c", "d"], done={"a"}, limit=2), ["b", "c"])

    def test_returns_everything_without_limit(self):
        self.assertEqual(plan(["a", "b"], done=set(), limit=None), ["a", "b"])

    def test_rejects_non_positive_limit(self):
        with self.assertRaises(ValueError):
            plan(["a"], done=set(), limit=0)


class ValidateDelayTest(unittest.TestCase):
    def test_accepts_zero_and_positive(self):
        self.assertEqual(validate_delay(0), 0)
        self.assertEqual(validate_delay(1.5), 1.5)

    def test_rejects_negative(self):
        with self.assertRaises(ValueError):
            validate_delay(-0.1)


class DefaultDelayTest(unittest.TestCase):
    def test_defaults_to_one_request_per_second(self):
        """DDBJ の rate limit は公開されていない。既定は控えめに1秒とする。"""
        self.assertEqual(DEFAULT_DELAY, 1.0)
