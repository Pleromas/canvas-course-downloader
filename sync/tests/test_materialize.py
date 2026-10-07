import json, os
from canvas_sync.ingest import ingest_zip
from canvas_sync.materialize import materialize_run, rebuild_latest
from tests.helpers import TempStore, base_manifest, doc_item, file_item, make_zip

A1 = doc_item("assignment", "456", "Assignments/A1.html", "A1")
PDF = file_item("9001", "Extracted_Files/lec.pdf", 3)
V1 = {"Assignments/A1.html": b"<h1>A1</h1>", "Extracted_Files/lec.pdf": b"pdf"}


class MaterializeTests(TempStore):
    def test_checkout_writes_tree(self):
        res = ingest_zip(self.store, make_zip(self.root / "in/a.zip", base_manifest(items=[A1, PDF]), V1))
        dest = self.root / "out"
        n = materialize_run(self.store, res.run_id, dest)
        self.assertEqual(n, 2)
        self.assertEqual((dest / "Assignments/A1.html").read_bytes(), b"<h1>A1</h1>")
        self.assertEqual((dest / "Extracted_Files/lec.pdf").read_bytes(), b"pdf")

    def test_latest_follows_newest_run_and_drops_old_paths(self):
        ingest_zip(self.store, make_zip(self.root / "in/a.zip", base_manifest(items=[A1, PDF]), V1))
        self.assertTrue((self.store.latest / "XLS5C202609" / "Extracted_Files/lec.pdf").exists())
        ingest_zip(self.store, make_zip(self.root / "in/b.zip",
                                        base_manifest(exported_at="2026-10-07T00:00:00Z", items=[A1]),
                                        {"Assignments/A1.html": b"<h1>A1 v2</h1>"}))
        tree = self.store.latest / "XLS5C202609"
        self.assertFalse((tree / "Extracted_Files/lec.pdf").exists())
        self.assertEqual((tree / "Assignments/A1.html").read_bytes(), b"<h1>A1 v2</h1>")

    def test_course_rename_moves_latest(self):
        ingest_zip(self.store, make_zip(self.root / "in/a.zip", base_manifest(items=[A1]), V1))
        ingest_zip(self.store, make_zip(self.root / "in/b.zip",
                                        base_manifest(exported_at="2026-10-07T00:00:00Z", items=[A1], course_name="CMPT 487"), V1))
        self.assertFalse((self.store.latest / "XLS5C202609").exists())
        self.assertTrue((self.store.latest / "CMPT 487" / "Assignments/A1.html").exists())

    def test_hardlink_when_possible(self):
        res = ingest_zip(self.store, make_zip(self.root / "in/a.zip", base_manifest(items=[PDF]), V1))
        f = self.store.latest / "XLS5C202609" / "Extracted_Files/lec.pdf"
        snap_path = self.store.db.execute("select snapshot_path from runs").fetchone()[0]
        sha = json.loads(open(snap_path).read())["files"]["Extracted_Files/lec.pdf"]
        self.assertEqual(os.stat(f).st_ino, os.stat(self.store.blob_path(sha)).st_ino)
