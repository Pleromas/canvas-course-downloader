import io, contextlib
from unittest import mock
from canvas_sync.cli import main
from canvas_sync.ingest import ingest_zip
from canvas_sync.render import render_run, RenderOptions, pdftools
from canvas_sync.render.pdftools import Capabilities
from tests.helpers import TempStore, base_manifest, doc_item, make_zip

A1 = doc_item("assignment", "456", "Assignments/A1.html", "A1")
NOCAPS = Capabilities(None, None, None, None, False)


class RenderRunTests(TempStore):
    def test_render_run_without_tools_gives_md_and_warnings(self):
        from canvas_sync import ingest as ingest_mod
        with mock.patch.object(pdftools, "detect", return_value=NOCAPS), \
             mock.patch.object(ingest_mod, "RENDER_AFTER_INGEST", True):
            res = ingest_zip(self.store, make_zip(self.root / "in/a.zip", base_manifest(items=[A1]), {"Assignments/A1.html": b"<p>x</p>"}))
            self.assertIn("md:", res.summary)
            r = render_run(self.store, res.run_id, RenderOptions(md=True, book=True))
        self.assertTrue((r.md_dir / "Assignments/A1.md").exists())
        self.assertIsNone(r.book_path)
        self.assertTrue(any("chromium" in w for w in r.warnings))

    def test_cli_render_and_doctor(self):
        with mock.patch.object(pdftools, "detect", return_value=NOCAPS):
            ingest_zip(self.store, make_zip(self.root / "in/a.zip", base_manifest(items=[A1]), {"Assignments/A1.html": b"<p>x</p>"}))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = main(["--root", str(self.root), "render", "133044", "--md-only"])
            self.assertEqual(code, 0)
            self.assertIn("md:", buf.getvalue())
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(main(["--root", str(self.root), "doctor"]), 0)
            self.assertIn("pdftotext", buf.getvalue())
