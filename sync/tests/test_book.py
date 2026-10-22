import tempfile, unittest
from pathlib import Path
from unittest import mock
from canvas_sync.ingest import ingest_zip
from canvas_sync.render import book, pdftools, md_out
from canvas_sync.render.pdftools import Capabilities
from tests.helpers import TempStore, base_manifest, doc_item, file_item, make_zip


class MdHtmlTests(unittest.TestCase):
    def test_subset(self):
        md = ("# T\n\n## H2\n\nPara **b** *i* `c` [l](x.md) ![a](img.png)\n\n- one\n  - two\n- three\n\n1. a\n2. b\n\n"
              "| A | B |\n| --- | --- |\n| 1 | 2 |\n\n```text\nx < y\n```\n\n> q\n> r\n\n---\n")
        html = book.md_to_html(md, lambda t: "file:///abs/" + t)
        for frag in ["<h1>T</h1>", "<h2>H2</h2>", "<strong>b</strong>", "<em>i</em>", "<code>c</code>",
                     '<a href="file:///abs/x.md">l</a>', '<img src="file:///abs/img.png" alt="a">',
                     "<ul><li>one<ul><li>two</li></ul></li><li>three</li></ul>", "<ol><li>a</li><li>b</li></ol>",
                     "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>",
                     "<pre><code>x &lt; y</code></pre>", "<blockquote><p>q\nr</p></blockquote>", "<hr>"]:
            self.assertIn(frag, html, frag)


A1 = doc_item("assignment", "456", "Assignments/A1.html", "A1", {"points_possible": 43})
PDF = file_item("9401", "Extracted_Files/lec.pdf", 10, {"content_type": "application/pdf"})
MOD = {"type": "module", "canvasId": "77", "path": None, "title": "Week 1", "updatedAt": None, "size": None,
       "meta": {"position": 1, "items": [{"id": "1", "type": "Assignment", "title": "A1", "contentId": "456"},
                                         {"id": "2", "type": "File", "title": "lec.pdf", "contentId": "9401"}]}, "sourceCourseId": None}


def tiny_pdf(pages=2) -> bytes:
    import io
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=300, height=200)
    buf = io.BytesIO(); w.write(buf); return buf.getvalue()


class BookTests(TempStore):
    def ingest(self):
        files = {"Assignments/A1.html": b"<h1>A1</h1><p>hello</p>", "Extracted_Files/lec.pdf": tiny_pdf(2) if pdftools.have_pypdf() else b"%PDF-1.4 x"}
        return ingest_zip(self.store, make_zip(self.root / "in/a.zip", base_manifest(items=[A1, PDF, MOD]), files)).run_id

    def test_book_without_chromium(self):
        caps = Capabilities(None, None, None, None, False)
        run = self.ingest()
        md_dir = md_out.render_markdown(self.store, run, caps)
        self.assertIsNone(book.render_book(self.store, run, caps, md_dir))

    def test_book_without_pypdf(self):
        caps = Capabilities(None, None, None, "/x/chromium", False)
        run = self.ingest()
        md_dir = md_out.render_markdown(self.store, run, caps)

        def fake_print(html, out):
            Path(out).write_bytes(b"%PDF-1.4 printed"); return True
        with mock.patch.object(pdftools, "html_to_pdf", side_effect=fake_print):
            path = book.render_book(self.store, run, caps, md_dir)
        self.assertTrue(path and path.read_bytes() == b"%PDF-1.4 printed")
        self.assertTrue((path.parent / "latest.pdf").exists())

    @unittest.skipUnless(pdftools.find_tool("chromium") and pdftools.have_pypdf(), "chromium/pypdf missing")
    def test_book_integration_outline_and_splice(self):
        caps = pdftools.detect()
        run = self.ingest()
        md_dir = md_out.render_markdown(self.store, run, caps)
        path = book.render_book(self.store, run, caps, md_dir)
        from pypdf import PdfReader
        r = PdfReader(str(path))
        titles = book._outline_titles(r)
        self.assertIn("Week 1", titles)
        self.assertIn("A1", titles)
        self.assertIn("lec.pdf", titles)
        self.assertGreaterEqual(len(r.pages), 2 + 2 + 2)  # cover+toc, parts, spliced pages
        # contents page carries one clickable link per entry
        links = []
        for pi, page in enumerate(r.pages[:3]):
            for a in page.get("/Annots") or []:
                a = a.get_object()
                if a.get("/Subtype") == "/Link":
                    links.append((pi, a))
        self.assertGreaterEqual(len(links), 3, "Week 1, A1, lec.pdf should be linked")
        targets = {book._link_target_page(r, a) for _, a in links}
        targets.discard(None)
        self.assertIn(r.get_destination_page_number(next(o for o in r.outline if not isinstance(o, list))), targets)
