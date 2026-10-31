import unittest

from cogs.economy import format_coins


class WalletFormattingRegressionTests(unittest.TestCase):
    def test_numeric_database_strings_are_formatted_as_numbers(self):
        self.assertEqual(format_coins("1234567"), "1,234,567")
        self.assertEqual(format_coins("0"), "0")
        self.assertEqual(format_coins("-1234"), "-1,234")

    def test_positive_and_negative_infinity_are_formatted(self):
        self.assertEqual(format_coins("inf"), "∞（無限）")
        self.assertEqual(format_coins("-inf"), "-∞（負無限）")

    def test_extremely_large_balances_use_scientific_notation(self):
        self.assertEqual(format_coins("1" + "0" * 1200), "1×10^1200")
        self.assertEqual(format_coins("-" + "9" * 21), "-9.99999×10^20")


if __name__ == "__main__":
    unittest.main()
