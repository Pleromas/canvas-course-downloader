"""Deferred review minors: course id validation, schema version, streaming blobs,
path-only changes, restored items, public change_for."""
import hashlib, io, zipfile
from canvas_sync.changes import compute_changes, change_for
from canvas_sync.ingest import ingest_zip
from canvas_sync.manifest import parse_manifest, ManifestError
from canvas_sync.store import Store, SCHEMA_VERSION
from tests.helpers import TempStore, base_manifest, doc_item, file_item, make_zip

A1 = doc_item("assignment", "456", "Assignments/A1.html", "A1")
PDF = file_item("9001", "Extracted_Files/lec.pdf", 3)


class MinorsTests(TempStore):
    def test_course_id_must_be_digits(self):
        for bad in ("../..", "", "abc/def", "12 34"):
            with self.assertRaises(ManifestError):
                parse_manifest(base_manifest(course_id=bad))
        self.assertEqual(parse_manifest(base_manifest(course_id="133044")).course_id, "133044")

    def test_schema_version_pragma(self):
        v = self.store.db.execute("pragma user_version").fetchone()[0]
        self.assertEqual(v, SCHEMA_VERSION)
        self.assertGreaterEqual(SCHEMA_VERSION, 1)

    def test_put_blob_stream_matches_put_blob(self):
        data = b"x" * (3 * 1024 * 1024) + b"tail"
        sha1 = self.store.put_blob(data)
        store2 = Store(self.root / "two")
        try:
            sha2, size = store2.put_blob_stream(io.BytesIO(data))
            self.assertEqual(sha1, sha2)
            self.assertEqual(size, len(data))
            self.assertEqual(store2.read_blob(sha2), data)
            self.assertEqual(list(store2.tmp.iterdir()), [])
        finally:
            store2.close()

    def test_large_zip_member_is_streamed_not_read_whole(self):
        # ingest must not call ZipFile.read for file members (whole-file read)
        import canvas_sync.ingest as im
        calls = []
        orig = zipfile.ZipFile.read

        def spy(self, name, *a, **k):
            calls.append(name); return orig(self, name, *a, **k)
        with __import__("unittest").mock.patch.object(zipfile.ZipFile, "read", spy):
            ingest_zip(self.store, make_zip(self.root / "in/a.zip", base_manifest(items=[A1, PDF]),
                                            {"Assignments/A1.html": b"<p>x</p>", "Extracted_Files/lec.pdf": b"pdf"}))
        self.assertEqual(calls, ["manifest.json"])

    def test_path_only_change_reported(self):
        ingest_zip(self.store, make_zip(self.root / "in/a.zip", base_manifest(items=[PDF]), {"Extracted_Files/lec.pdf": b"pdf"}))
        moved = dict(PDF, path="Extracted_Files/Week 1/lec.pdf")
        r = ingest_zip(self.store, make_zip(self.root / "in/b.zip", base_manifest(exported_at="2026-10-07T00:00:00Z", items=[moved]),
                                            {"Extracted_Files/Week 1/lec.pdf": b"pdf"}))
        cs = compute_changes(self.store, r.run_id)
        self.assertEqual(cs.changed[0].meta_diff, [("path", "Extracted_Files/lec.pdf", "Extracted_Files/Week 1/lec.pdf")])
        self.assertIsNone(cs.changed[0].size_change)

    def test_restored_item_is_reported(self):
        ingest_zip(self.store, make_zip(self.root / "in/a.zip", base_manifest(items=[A1, PDF]), {"Assignments/A1.html": b"a", "Extracted_Files/lec.pdf": b"pdf"}))
        ingest_zip(self.store, make_zip(self.root / "in/b.zip", base_manifest(exported_at="2026-10-07T00:00:00Z", items=[A1]), {"Assignments/A1.html": b"a"}))
        r = ingest_zip(self.store, make_zip(self.root / "in/c.zip", base_manifest(exported_at="2026-10-08T00:00:00Z", items=[A1, PDF]), {"Assignments/A1.html": b"a", "Extracted_Files/lec.pdf": b"pdf"}))
        cs = compute_changes(self.store, r.run_id)
        self.assertEqual([c.key for c in cs.restored], ["9001"])
        self.assertEqual(cs.new, [])
        from canvas_sync.report import render_report
        self.assertIn("## Restored (1)", render_report(cs))

    def test_change_for_is_public(self):
        self.assertTrue(callable(change_for))
