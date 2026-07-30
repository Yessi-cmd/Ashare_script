import unittest

from performance_benchmark import percentile


class PerformanceBenchmarkTests(unittest.TestCase):
    def test_percentile_uses_nearest_rank(self):
        self.assertEqual(percentile([5, 1, 4, 2, 3], 0.5), 3)
        self.assertEqual(percentile([5, 1, 4, 2, 3], 0.95), 5)


if __name__ == "__main__":
    unittest.main()
