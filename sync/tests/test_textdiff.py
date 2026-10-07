import unittest
from canvas_sync.textdiff import html_to_text, unified, grades_diff

DOC = ('<!doctype html><html><head><title>A1</title><link rel="stylesheet" href="styles.css">'
       '<style>p{}</style></head><body><h1>A1</h1><p>Due <strong>Friday</strong>.</p>'
       '<script>alert(1)</script><ul><li>one</li><li>two</li></ul></body></html>')


class TextDiffTests(unittest.TestCase):
    def test_html_to_text_strips_markup_and_head(self):
        self.assertEqual(html_to_text(DOC), "A1\nDue Friday.\none\ntwo")

    def test_unified_counts(self):
        diff, add, rem = unified("a\nb\nc", "a\nB\nc\nd")
        self.assertEqual((add, rem), (2, 1))
        self.assertIn("-b", diff)
        self.assertIn("+B", diff)
        self.assertIn("+d", diff)

    def test_unified_identical_is_empty(self):
        self.assertEqual(unified("x", "x"), ("", 0, 0))

    def test_grades_diff_handles_quoted_commas(self):
        old = 'Assignment,Due Date,Points Possible,Score,Grade\n"Lab 1, part a","2026-10-01",10,,""\n"Quiz",,5,4,"4"'
        new = 'Assignment,Due Date,Points Possible,Score,Grade\n"Lab 1, part a","2026-10-01",10,9,"9"\n"Quiz",,5,4,"4"\n"New",,1,1,"1"'
        self.assertEqual(grades_diff(old, new), [("Lab 1, part a", "—", "9"), ("New", "—", "1")])
