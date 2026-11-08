import unittest

from tool import greet, main


class ToolTests(unittest.TestCase):
    def test_greet(self):
        self.assertEqual(greet("Sam"), "Hello, Sam.")
        self.assertEqual(greet("  "), "Hello, there.")

    def test_main_runs(self):
        self.assertEqual(main(["Sam"]), 0)


if __name__ == "__main__":
    unittest.main()
