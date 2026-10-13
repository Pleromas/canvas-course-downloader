import json, os, tempfile, unittest
from pathlib import Path
from unittest import mock
from canvas_sync.ingest import ingest_zip
from canvas_sync.render import md_out, pdftools
from canvas_sync.render.pdftools import Capabilities
from tests.helpers import TempStore, base_manifest, doc_item, file_item, synthetic_item, make_zip

NOCAPS = Capabilities(None, None, None, None, False)


def _item(type_, key, path, title, meta=None):
    return {"type": type_, "key": key, "path": path, "title": title, "meta": meta or {}}


class PdfItemTests(TempStore):
    def test_pdf_text_without_poppler(self):
        sha = self.store.put_blob(b"%PDF-1.4 fake")
        out = self.root / "md"
        rel = md_out.render_pdf_item(self.store, _item("file", "9", "Extracted_Files/lec.pdf", "lec.pdf"), sha, out, "Files", NOCAPS)
        self.assertEqual(rel, "Files/lec.pdf.md")
        self.assertEqual((out / "Files/lec.pdf").read_bytes(), b"%PDF-1.4 fake")
        text = (out / "Files/lec.pdf.md").read_text()
        self.assertIn("type: file", text)
        self.assertIn("pdftotext", text)  # explains what is missing

    def test_selective_page_images(self):
        sha = self.store.put_blob(b"%PDF-1.4 fake")
        out = self.root / "md"
        caps = Capabilities("/x/pdftotext", "/x/pdftoppm", None, None, False)
        pages = ["a " * 60, "short", "b " * 60]
        calls = []

        def fake_png(path, page, out_png, dpi=110):
            calls.append(page); Path(out_png).parent.mkdir(parents=True, exist_ok=True); Path(out_png).write_bytes(b"png"); return True

        with mock.patch.object(pdftools, "pdf_text_pages", return_value=pages), \
             mock.patch.object(pdftools, "pdf_page_png", side_effect=fake_png), \
             mock.patch.object(pdftools, "pdf_page_count", return_value=3):
            md_out.render_pdf_item(self.store, _item("file", "9", "Extracted_Files/lec.pdf", "lec.pdf"), sha, out, "Files", caps)
            self.assertEqual(calls, [2])
            text = (out / "Files/lec.pdf.md").read_text()
            self.assertIn("## Page 2", text)
            self.assertIn(f"![Page 2](../pages/{sha[:12]}/p002.png)", text)
            self.assertNotIn("p001.png", text)
            calls.clear()
            md_out.render_pdf_item(self.store, _item("file", "9", "Extracted_Files/lec.pdf", "lec.pdf"), sha, self.root / "md2", "Files", caps, all_pages=True)
            self.assertEqual(calls, [1, 2, 3])


A1 = doc_item("assignment", "456", "Assignments/A1.html", "A1", {"due_at": "2026-10-03T06:59:00Z", "points_possible": 43})
A2 = doc_item("assignment", "457", "Assignments/A 2 #b.html", "A 2 #b", {"points_possible": 5})
Q1 = doc_item("quiz", "9", "Quizzes/Q1/Q1.html", "Q1", {"points_possible": 4})
IMG = file_item("9275770", "Extracted_Files/noiseeq.png", 5, {"content_type": "image/png"})
PDF = file_item("9401", "Extracted_Files/lec.pdf", 10, {"content_type": "application/pdf"})
GRADES = synthetic_item("grades.csv", "Grades.csv")
MOD = {"type": "module", "canvasId": "77", "path": None, "title": "Week 1", "updatedAt": None, "size": None,
       "meta": {"position": 1, "items": [{"id": "1", "type": "Assignment", "title": "A1", "contentId": "456"},
                                         {"id": "2", "type": "File", "title": "lec.pdf", "contentId": "9401"}]}, "sourceCourseId": None}
WRAP = '<!doctype html><html><head><title>{t}</title><link rel="stylesheet" href="{css}"></head><body><h1>{t}</h1>{b}</body></html>'
FILES = {
    "Assignments/A1.html": WRAP.format(t="A1", css="../styles.css", b='<p>See <a href="A 2 #b.html">A2</a> and <img src="../Extracted_Files/noiseeq.png" alt="eq"> and <a href="../Extracted_Files/lec.pdf">slides</a>.</p>').encode(),
    "Assignments/A 2 #b.html": WRAP.format(t="A 2 #b", css="../styles.css", b="<p>Two</p>").encode(),
    "Quizzes/Q1/Q1.html": WRAP.format(t="Q1", css="../../styles.css", b="<h3>Your answers</h3><ul><li>x <em>(correct)</em></li></ul>").encode(),
    "Extracted_Files/noiseeq.png": b"\x89PNG\r\n\x1a\nfake",
    "Extracted_Files/lec.pdf": b"%PDF-1.4 fake",
    "Grades.csv": b'Assignment,Due Date,Points Possible,Score,Grade\n"A1","2026-10-02",43,40,"40"\n',
}


class MarkdownTreeTests(TempStore):
    def ingest(self):
        return ingest_zip(self.store, make_zip(self.root / "in/a.zip", base_manifest(items=[A1, A2, Q1, IMG, PDF, GRADES, MOD]), FILES)).run_id

    def test_tree_links_images_index_full_and_determinism(self):
        run = self.ingest()
        d = md_out.render_markdown(self.store, run, NOCAPS)
        self.assertTrue((d / "Assignments/A1.md").exists())
        a1 = (d / "Assignments/A1.md").read_text()
        self.assertTrue(a1.startswith("---\ntype: assignment\ncanvas_id: \"456\"\n"))
        self.assertIn("source: https://canvas.usask.ca/courses/133044/assignments/456", a1)
        self.assertIn("points_possible: 43", a1)
        self.assertIn("[A2](A%202%20%23b.md)", a1)            # relative, URL-encoded
        self.assertIn("[slides](../Files/lec.pdf.md)", a1)   # PDF link goes to its .md
        sha12 = __import__("hashlib").sha256(FILES["Extracted_Files/noiseeq.png"]).hexdigest()[:12]
        self.assertIn(f"![eq](../images/{sha12}.png)", a1)
        self.assertTrue((d / f"images/{sha12}.png").exists())
        q = (d / "Quizzes/Q1/Q1.md").read_text()
        self.assertIn("- ✅ x", q)
        g = (d / "Grades.md").read_text()
        self.assertIn("| A1 |", g)
        idx = (d / "index.md").read_text()
        self.assertIn("## Modules", idx)
        self.assertLess(idx.index("### Week 1"), idx.index("[A1](Assignments/A1.md)"))
        self.assertLess(idx.index("[A1](Assignments/A1.md)"), idx.index("[lec.pdf](Files/lec.pdf.md)"))
        self.assertIn("## Assignments", idx)   # A2 not in a module
        full = (d / "_full.md").read_text()
        self.assertEqual(full.count("<!-- file: Assignments/A1.md -->"), 1)
        self.assertLess(full.index("Assignments/A1.md"), full.index("Assignments/A 2 #b.md"))
        latest = self.store.root / "md" / "XLS5C202609" / "latest"
        self.assertTrue(latest.is_symlink() or latest.is_dir())
        before = {p: p.read_bytes() for p in d.rglob("*") if p.is_file()}
        md_out.render_markdown(self.store, run, NOCAPS)
        after = {p: p.read_bytes() for p in d.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
