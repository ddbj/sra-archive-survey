"""aggregate_sizes.py の表示のテスト。"""

import unittest

from aggregate_sizes import format_family_row


class FormatFamilyRowTest(unittest.TestCase):
    """ERR の submitted は 1.1e19 bytes に届く。列が詰まると隣と数字がくっつく。"""

    def test_keeps_file_count_and_bytes_apart_for_the_largest_family(self):
        row = format_family_row("submitted", 10_618_062, 99.94, 19_537_494,
                                11_075_594_831_822_839)
        self.assertIn("19,537,494 ", row)
        self.assertIn(" 11,075,594,831,822,839", row)

    def test_leaves_room_for_values_a_hundred_times_larger(self):
        row = format_family_row("x", 1, 1.0, 1, 10**21)
        self.assertIn(" 1,000,000,000,000,000,000,000", row)

    def test_shows_coverage_with_two_decimals(self):
        self.assertIn("37.60%", format_family_row("bam", 1, 37.6, 1, 1))


if __name__ == "__main__":
    unittest.main()
