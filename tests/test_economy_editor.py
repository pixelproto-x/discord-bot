import unittest

from economy_editor_names import _parse_balance


class EconomyEditorBalanceValidationTests(unittest.TestCase):
    def test_accepts_zero_and_positive_balances(self):
        self.assertEqual(_parse_balance("0"), "0")
        self.assertEqual(_parse_balance("00123"), "123")

    def test_accepts_positive_and_negative_infinity(self):
        self.assertEqual(_parse_balance("inf"), "inf")
        self.assertEqual(_parse_balance(" INF "), "inf")
        self.assertEqual(_parse_balance("-inf"), "-inf")

    def test_accepts_negative_balances(self):
        self.assertEqual(_parse_balance("-1"), "-1")
        self.assertEqual(_parse_balance("-0"), "0")
        self.assertEqual(_parse_balance("-" + "9" * 20), "-" + "9" * 20)

    def test_evaluates_integer_arithmetic_before_saving(self):
        self.assertEqual(_parse_balance("1+1"), "2")
        self.assertEqual(_parse_balance("(10 + 5) * 2"), "30")
        self.assertEqual(_parse_balance("2 ** 10"), "1024")
        self.assertEqual(_parse_balance("-5 + 2"), "-3")
        self.assertEqual(_parse_balance("8 / 2"), "4")

    def test_rejects_unsafe_or_non_integer_expressions(self):
        for value in ("", "1.5", "abc", "__import__('os')", "1 / 2", "1 / 0", "2 ** 999999"):
            with self.subTest(value=value):
                self.assertIsNone(_parse_balance(value))
        self.assertIsNone(_parse_balance("9" * 2001))


if __name__ == "__main__":
    unittest.main()
