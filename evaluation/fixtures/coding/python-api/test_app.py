import inspect
import unittest

from app import now


class ApiTests(unittest.TestCase):
    def test_now_has_offset(self):
        text = now()
        self.assertTrue(text.endswith("+00:00") or text.endswith("Z"))
        source = inspect.getsource(now)
        self.assertIn("timezone.utc", source)


if __name__ == "__main__":
    unittest.main()
