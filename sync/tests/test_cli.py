import io, contextlib
from canvas_sync.cli import main
from tests.helpers import TempStore, base_manifest, doc_item, make_zip

A1 = doc_item("assignment", "456", "Assignments/A1.html", "A1", {"due_at": "2026-10-10"})


class CliTests(TempStore):
    def run_cli(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["--root", str(self.root), *args])
        return code, buf.getvalue()

    def test_ingest_and_status(self):
        make_zip(self.root / "in/a.zip", base_manifest(items=[A1]), {"Assignments/A1.html": b"x"})
        code, out = self.run_cli("ingest", str(self.root / "in/a.zip"))
        self.assertEqual(code, 0)
        self.assertIn("XLS5C202609: 1 new, 0 changed, 0 removed", out)
        code, out = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("XLS5C202609", out)
        self.assertIn("runs: 1", out)

    def test_log_and_diff(self):
        make_zip(self.root / "in/a.zip", base_manifest(items=[A1]), {"Assignments/A1.html": b"<p>one</p>"})
        self.run_cli("ingest", str(self.root / "in/a.zip"))
        make_zip(self.root / "in/b.zip", base_manifest(exported_at="2026-10-07T00:00:00Z", items=[A1]), {"Assignments/A1.html": b"<p>two</p>"})
        self.run_cli("ingest", str(self.root / "in/b.zip"))
        code, out = self.run_cli("log", "133044")
        self.assertIn("run 1", out); self.assertIn("run 2", out)
        code, out = self.run_cli("log", "133044", "--item", "assignment:456")
        self.assertEqual(out.count("Assignments/A1.html"), 2)
        code, out = self.run_cli("diff", "133044", "assignment:456", "1", "2")
        self.assertEqual(code, 0)
        self.assertIn("-one", out); self.assertIn("+two", out)

    def test_checkout_verify_gc(self):
        make_zip(self.root / "in/a.zip", base_manifest(items=[A1]), {"Assignments/A1.html": b"x"})
        self.run_cli("ingest", str(self.root / "in/a.zip"))
        code, _ = self.run_cli("checkout", "133044", "1", str(self.root / "co"))
        self.assertEqual(code, 0)
        self.assertTrue((self.root / "co/Assignments/A1.html").exists())
        self.assertEqual(self.run_cli("verify")[0], 0)
        code, out = self.run_cli("gc")
        self.assertIn("removed 0", out)

    def test_verify_nonzero_on_problem(self):
        make_zip(self.root / "in/a.zip", base_manifest(items=[A1]), {"Assignments/A1.html": b"x"})
        self.run_cli("ingest", str(self.root / "in/a.zip"))
        sha = self.store.db.execute("select sha256 from versions").fetchone()[0]
        self.store.blob_path(sha).chmod(0o644)  # blobs are read-only; simulate external damage
        self.store.blob_path(sha).write_bytes(b"bad")
        code, out = self.run_cli("verify")
        self.assertEqual(code, 1)
        self.assertIn("hash mismatch", out)

    def test_bad_manifest_is_reported_not_traceback(self):
        make_zip(self.root / "in/old.zip", {"course": "X"}, {})
        code, out = self.run_cli("ingest", str(self.root / "in/old.zip"))
        self.assertEqual(code, 2)
        self.assertIn("schema 2", out)

from pathlib import Path as _P


class UnitFileTests(TempStore):
    def test_unit_files_present_and_templated(self):
        d = _P(__file__).resolve().parent.parent / "systemd"
        path_unit = (d / "canvas-sync.path").read_text()
        svc = (d / "canvas-sync.service").read_text()
        self.assertIn("PathChanged=%h/Downloads/CanvasExports", path_unit)
        self.assertIn("ExecStart=@CANVAS_SYNC@ ingest-all --notify", svc)
        self.assertIn("Type=oneshot", svc)
