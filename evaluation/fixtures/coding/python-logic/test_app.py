import unittest

from app import divide


class DivideTests(unittest.TestCase):
    def test_divide(self):
        self.assertEqual(divide(6, 3), 2)


if __name__ == "__main__":
    unittest.main()
