import unittest

from app import load_port


class ConfigTests(unittest.TestCase):
    def test_port(self):
        self.assertEqual(load_port(), 9000)


if __name__ == "__main__":
    unittest.main()
