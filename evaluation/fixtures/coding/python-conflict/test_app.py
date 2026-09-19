import unittest

from app import score


class ScoreTests(unittest.TestCase):
    def test_score(self):
        self.assertEqual(score(10, 3), 7)


if __name__ == "__main__":
    unittest.main()
