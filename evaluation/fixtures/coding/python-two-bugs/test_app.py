import unittest
from datetime import timezone

from app import add, stamp


class TwoBugTests(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(2, 3), 5)

    def test_stamp_aware(self):
        value = stamp()
        self.assertIs(value.tzinfo, timezone.utc)


if __name__ == "__main__":
    unittest.main()
