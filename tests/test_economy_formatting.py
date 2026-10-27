import unittest

from cogs.economy import format_coins


class WalletFormattingRegressionTests(unittest.TestCase):
    def test_numeric_database_strings_are_formatted_as_numbers(self):
        self.assertEqual(format_coins("1234567"), "1,234,567")
        self.assertEqual(format_coins("0"), "0")
        self.assertEqual(format_coins("-1234"), "-1,234")

    def test_infinite_balance_is_not_passed_to_numeric_formatting(self):
        self.assertEqual(format_coins("inf"), "∞（無限）")

    def test_extremely_large_balances_are_bounded(self):
        value = "9" * 1201
        self.assertEqual(format_coins(value), "超大金額（1201 位數）")


if __name__ == "__main__":
    unittest.main()
