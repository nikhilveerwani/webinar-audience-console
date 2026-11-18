"""Unit tests for the rules that are easy to get wrong."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pipeline"))
from transform import mean_ci, merge_sessions, minute_grid, paired_p  # noqa: E402


class MergeSessions(unittest.TestCase):
    def test_rejoin_with_gap_stays_two_sessions(self):
        self.assertEqual(merge_sessions([(0, 10), (12, 20)]), [[0, 10], [12, 20]])

    def test_overlapping_devices_merge_into_one(self):
        self.assertEqual(merge_sessions([(0, 10), (5, 15)]), [[0, 15]])

    def test_order_does_not_matter(self):
        self.assertEqual(merge_sessions([(12, 20), (0, 10)]), [[0, 10], [12, 20]])


class MinuteGrid(unittest.TestCase):
    def test_partial_minutes_count(self):
        # present 2.5 -> 4.2 touches minutes 2, 3 and 4
        self.assertEqual(minute_grid([[2.5, 4.2]], -30, 100), {2, 3, 4})

    def test_short_rejoin_gap_inside_one_minute_counts_once(self):
        # dropped at 5.2, back at 5.7: still one person in minute 5
        self.assertEqual(minute_grid([[3, 5.2], [5.7, 7]], -30, 100), {3, 4, 5, 6})

    def test_clipped_to_window(self):
        self.assertEqual(minute_grid([[-40, -28]], -30, 100), {-30, -29})


class Stats(unittest.TestCase):
    def test_mean_ci_brackets_the_mean(self):
        m, lo, hi = mean_ci([10, 12, 14, 16, 18] * 8)
        self.assertAlmostEqual(m, 14); self.assertTrue(lo < 14 < hi)

    def test_no_difference_is_not_significant(self):
        self.assertGreater(paired_p([1, -1, 2, -2, 0.5, -0.5] * 6), 0.5)

    def test_consistent_gap_is_significant(self):
        self.assertLess(paired_p([24, 26, 25, 27, 23, 25] * 6), 0.001)


if __name__ == "__main__":
    unittest.main()
