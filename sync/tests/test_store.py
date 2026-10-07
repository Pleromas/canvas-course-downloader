import hashlib, os
from tests.helpers import TempStore


class StoreTests(TempStore):
    def test_creates_layout(self):
        for d in ("objects", "objects/tmp", "snapshots", "latest", "reports", "processed"):
            self.assertTrue((self.root / d).is_dir(), d)
        self.assertTrue((self.root / "store.sqlite").is_file())

    def test_put_blob_is_content_addressed_and_idempotent(self):
        data = b"hello"
        sha = self.store.put_blob(data)
        self.assertEqual(sha, hashlib.sha256(data).hexdigest())
        self.assertEqual(self.store.blob_path(sha), self.root / "objects" / sha[:2] / sha)
        self.assertTrue(self.store.has_blob(sha))
        mtime = os.stat(self.store.blob_path(sha)).st_mtime_ns
        self.assertEqual(self.store.put_blob(data), sha)
        self.assertEqual(os.stat(self.store.blob_path(sha)).st_mtime_ns, mtime)  # not rewritten
        self.assertEqual(self.store.read_blob(sha), data)
        self.assertEqual(list((self.root / "objects" / "tmp").iterdir()), [])

    def test_schema_tables_exist(self):
        names = {r[0] for r in self.store.db.execute("select name from sqlite_master where type='table'")}
        self.assertTrue({"courses", "runs", "items", "versions", "blobs"} <= names)

    def test_default_root_env(self):
        os.environ["CANVAS_SYNC_ROOT"] = str(self.root / "x")
        try:
            from canvas_sync.store import Store
            self.assertEqual(Store.default_root(), self.root / "x")
        finally:
            del os.environ["CANVAS_SYNC_ROOT"]
