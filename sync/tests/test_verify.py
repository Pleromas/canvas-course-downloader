from canvas_sync.ingest import ingest_zip
from canvas_sync.verify import verify, gc
from tests.helpers import TempStore, base_manifest, doc_item, make_zip

A1 = doc_item("assignment", "456", "Assignments/A1.html", "A1")


class VerifyTests(TempStore):
    def test_clean_store_verifies(self):
        ingest_zip(self.store, make_zip(self.root / "in/a.zip", base_manifest(items=[A1]), {"Assignments/A1.html": b"x"}))
        self.assertEqual(verify(self.store), [])

    def test_corrupt_blob_detected(self):
        ingest_zip(self.store, make_zip(self.root / "in/a.zip", base_manifest(items=[A1]), {"Assignments/A1.html": b"x"}))
        sha = self.store.db.execute("select sha256 from versions").fetchone()[0]
        self.store.blob_path(sha).chmod(0o644)  # blobs are read-only; simulate external damage
        self.store.blob_path(sha).write_bytes(b"corrupted")
        problems = verify(self.store)
        self.assertEqual(len(problems), 1)
        self.assertIn("hash mismatch", problems[0])

    def test_missing_blob_detected(self):
        ingest_zip(self.store, make_zip(self.root / "in/a.zip", base_manifest(items=[A1]), {"Assignments/A1.html": b"x"}))
        sha = self.store.db.execute("select sha256 from versions").fetchone()[0]
        self.store.blob_path(sha).unlink()
        self.assertTrue(any("missing blob" in p for p in verify(self.store)))

    def test_gc_removes_unreferenced(self):
        ingest_zip(self.store, make_zip(self.root / "in/a.zip", base_manifest(items=[A1]), {"Assignments/A1.html": b"x"}))
        orphan = self.store.put_blob(b"orphan")
        self.assertEqual(gc(self.store), 1)
        self.assertFalse(self.store.has_blob(orphan))
        self.assertEqual(verify(self.store), [])
