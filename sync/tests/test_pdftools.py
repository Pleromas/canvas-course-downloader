import os, shutil, stat, tempfile, unittest
from pathlib import Path
from unittest import mock
from canvas_sync.render import pdftools as pt


class FindToolTests(unittest.TestCase):
    def test_find_tool_uses_path_and_chromium_aliases(self):
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / "google-chrome"
            exe.write_text("#!/bin/sh\n"); exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
            with mock.patch.dict(os.environ, {"PATH": d}):
                self.assertEqual(pt.find_tool("chromium"), str(exe))
                self.assertIsNone(pt.find_tool("pdftotext"))

    def test_detect_reports_capabilities(self):
        with mock.patch.dict(os.environ, {"PATH": ""}):
            caps = pt.detect()
        self.assertIsNone(caps.pdftotext)
        self.assertIsNone(caps.chromium)
        self.assertIsInstance(caps.pypdf, bool)

    def test_text_pages_none_without_tool(self):
        with mock.patch.object(pt, "find_tool", return_value=None):
            self.assertIsNone(pt.pdf_text_pages("x.pdf"))
            self.assertFalse(pt.pdf_page_png("x.pdf", 1, Path("/tmp/none.png")))
            self.assertFalse(pt.html_to_pdf(Path("/tmp/a.html"), Path("/tmp/a.pdf")))


def _tiny_pdf(path: Path, pages=("Hello page one text words here", "Short")):
    pypdf = pt.pypdf_module()
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in pages:
        w.add_blank_page(width=200, height=200)
    with open(path, "wb") as fh:
        w.write(fh)


@unittest.skipUnless(pt.find_tool("pdftotext") and pt.find_tool("pdftoppm") and pt.have_pypdf(), "poppler/pypdf missing")
class PopplerIntegration(unittest.TestCase):
    def test_pages_and_png(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.pdf"
            _tiny_pdf(p)
            self.assertEqual(pt.pdf_page_count(p), 2)
            pages = pt.pdf_text_pages(p)
            self.assertEqual(len(pages), 2)
            out = Path(d) / "p1.png"
            self.assertTrue(pt.pdf_page_png(p, 1, out))
            self.assertTrue(out.exists() and out.stat().st_size > 0)


@unittest.skipUnless(pt.find_tool("chromium"), "chromium missing")
class ChromiumIntegration(unittest.TestCase):
    def test_html_to_pdf(self):
        with tempfile.TemporaryDirectory() as d:
            h = Path(d) / "a.html"; h.write_text("<h1>Hi</h1><p>there</p>")
            out = Path(d) / "a.pdf"
            self.assertTrue(pt.html_to_pdf(h, out))
            self.assertTrue(out.read_bytes().startswith(b"%PDF"))
