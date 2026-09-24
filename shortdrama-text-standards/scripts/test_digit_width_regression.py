"""Pure typography regression tests; never edits production files."""
import re
import unittest

DIGITS = str.maketrans('０１２３４５６７８９', '0123456789')

def normalize_digit_width(text):
    return text.translate(DIGITS)

class DigitWidthTests(unittest.TestCase):
    def test_ep10_counting(self):
        self.assertEqual(normalize_digit_width('１ ２ ３'), '1 2 3')
    def test_ep10_mixed(self):
        self.assertEqual(normalize_digit_width('って４ ５ ６...\n10?'), 'って4 5 6...\n10?')
    def test_adjacent_digit(self):
        self.assertEqual(normalize_digit_width('セーフハウス４'), 'セーフハウス4')
    def test_multi_digit(self):
        self.assertEqual(normalize_digit_width('１２'), '12')
    def test_non_digit_preservation(self):
        text = '一人 二度目 ゾンビ ｢引用｣ ・ ... ? !'
        self.assertEqual(normalize_digit_width(text), text)
    def test_numeric_value_change_rejected(self):
        self.assertNotEqual(normalize_digit_width('１'), normalize_digit_width('２'))
    def test_body_only_selection(self):
        source = '26\n00:01:00,320 --> 00:01:01,560\n１ ２ ３'
        lines = source.splitlines()
        result = '\n'.join(lines[:2] + [normalize_digit_width(x) for x in lines[2:]])
        self.assertEqual(result.splitlines()[:2], lines[:2])
        self.assertFalse(re.search('[０-９]', '\n'.join(result.splitlines()[2:])))
    def test_all_digit_codepoints(self):
        self.assertEqual(normalize_digit_width('０１２３４５６７８９'), '0123456789')

if __name__ == '__main__':
    unittest.main(verbosity=2)
