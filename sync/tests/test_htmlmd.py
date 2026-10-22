import unittest
from canvas_sync.render.htmlmd import html_to_markdown as md

WRAP = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>T</title>'
        '<link rel="stylesheet" href="../styles.css"></head><body><h1>T</h1>{body}</body></html>')


class HtmlMdTests(unittest.TestCase):
    def test_wrapper_stripped_and_headings(self):
        out = md(WRAP.format(body="<h2>Intro</h2><p>Hello <strong>world</strong>.</p><h1>Inner</h1><h3>Deep</h3>"))
        self.assertEqual(out, "## Intro\n\nHello **world**.\n\n## Inner\n\n### Deep")

    def test_nested_lists(self):
        out = md("<ul><li>one<ul><li>one.a</li><li>one.b</li></ul></li><li>two</li></ul><ol><li>first</li><li>second</li></ol>")
        self.assertEqual(out, "- one\n  - one.a\n  - one.b\n- two\n\n1. first\n2. second")

    def test_rectangular_table(self):
        out = md("<table><thead><tr><th>A</th><th>B</th></tr></thead><tbody><tr><td>1</td><td>2</td></tr><tr><td>3</td><td>4</td></tr></tbody></table>")
        self.assertEqual(out, "| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |")

    def test_ragged_table_falls_back(self):
        out = md('<table><tr><td colspan="2">wide</td></tr><tr><td>a</td><td>b</td></tr></table>')
        self.assertEqual(out, "- wide\n- a | b")

    def test_code_and_pre(self):
        out = md("<p>Use <code>x = 1</code>.</p><pre><code>for i in range(3):\n    print(i)</code></pre>")
        self.assertEqual(out, "Use `x = 1`.\n\n```\nfor i in range(3):\n    print(i)\n```")

    def test_links_and_images_through_maps(self):
        out = md('<p><a href="../Assignments/A2.html">A2</a> and <a href="https://x.y/z">ext</a></p>'
                 '<p><img src="../Extracted_Files/fig.png" alt="fig" width="2"></p>',
                 link_map=lambda h: "Assignments/A2.md" if h.endswith("A2.html") else None,
                 image_map=lambda s: "images/abc.png" if s.endswith("fig.png") else None)
        self.assertEqual(out, "[A2](Assignments/A2.md) and [ext](https://x.y/z)\n\n![fig](images/abc.png)")

    def test_quiz_marks(self):
        out = md('<ul><li>A <em>(correct)</em></li><li>B <em>(incorrect)</em></li>'
                 '<li style="font-weight:600">C</li><li>D</li></ul>'
                 '<ul><li style="color:#16a34a;font-weight:600">✓ E</li><li style="color:#dc2626;font-weight:600">✗ F</li>'
                 '<li style="font-weight:600">• G</li><li style="color:#16a34a">◇ H</li></ul>')
        self.assertEqual(out, "- ✅ A\n- ❌ B\n- ▶ C\n- D\n\n- ✅ E\n- ❌ F\n- ▶ G\n- ✔ H")

    def test_junk_removed_and_media_linked(self):
        out = md('<script>x()</script><style>p{}</style><p>Text&nbsp;here &amp; there</p>'
                 '<iframe src="https://v.example/1" title="Lecture 1"></iframe><video src="../Media/l.mp4"></video><hr>')
        self.assertEqual(out, "Text here & there\n\n[Video: Lecture 1](https://v.example/1)\n\n[Video](../Media/l.mp4)\n\n---")

    def test_blockquote_and_br_and_emphasis(self):
        out = md("<blockquote><p>quoted<br>line</p></blockquote><p><em>it</em> <i>al</i> <b>bo</b></p>")
        self.assertEqual(out, "> quoted\n> line\n\n*it* *al* **bo**")

    def test_whitespace_collapsed_and_empty_paragraphs_dropped(self):
        out = md("<div>\n  <p>  a   b </p>\n <p></p><p>c</p></div>")
        self.assertEqual(out, "a b\n\nc")


class FormControlTests(unittest.TestCase):
    def test_select_shows_chosen_option_and_inputs_show_values(self):
        out = md('<p>Pick: <select><option>A</option><option selected>B</option></select></p>'
                 '<p>N = <input type="text" value="2.2"></p>'
                 '<p>Empty: <select><option>A</option></select></p>')
        self.assertEqual(out, "Pick: **B**\n\nN = **2.2**\n\nEmpty: *(no selection)*")

    def test_textarea_and_blockquote_feedback(self):
        out = md('<textarea>my essay</textarea><blockquote><strong>Feedback:</strong> good</blockquote>')
        self.assertEqual(out, "my essay\n\n> **Feedback:** good")
