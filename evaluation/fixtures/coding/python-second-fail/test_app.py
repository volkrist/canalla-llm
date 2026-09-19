import unittest

from app import is_even


class EvenTests(unittest.TestCase):
    def test_even(self):
        self.assertIs(is_even(2), True)

    def test_zero(self):
        self.assertIs(is_even(0), True)


if __name__ == "__main__":
    unittest.main()
