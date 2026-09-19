import unittest

from app import greet


class GreetTests(unittest.TestCase):
    def test_default(self):
        self.assertEqual(greet(None), "Hello, world!")

    def test_empty(self):
        self.assertEqual(greet(""), "Hello, world!")

    def test_named(self):
        self.assertEqual(greet("Alex"), "Hello, Alex!")


if __name__ == "__main__":
    unittest.main()
