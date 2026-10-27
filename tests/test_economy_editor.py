import unittest

from economy_editor_names import _parse_balance


class EconomyEditorBalanceValidationTests(unittest.TestCase):
    def test_accepts_zero_and_positive_balances(self):
        self.assertEqual(_parse_balance("0"), "0")
        self.assertEqual(_parse_balance("00123"), "123")

    def test_accepts_infinite_balance_marker(self):
        self.assertEqual(_parse_balance("inf"), "inf")
        self.assertEqual(_parse_balance(" INF "), "inf")

    def test_rejects_negative_balances(self):
        self.assertIsNone(_parse_balance("-1"))
        self.assertIsNone(_parse_balance("-0"))
        self.assertIsNone(_parse_balance("-" + "9" * 20))

    def test_rejects_empty_non_integer_and_oversized_values(self):
        self.assertIsNone(_parse_balance(""))
        self.assertIsNone(_parse_balance("1.5"))
        self.assertIsNone(_parse_balance("abc"))
        self.assertIsNone(_parse_balance("9" * 2001))


if __name__ == "__main__":
    unittest.main()
