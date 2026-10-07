"""Tests pinned from the whole-branch review findings."""
import io, contextlib, json, os, stat
from pathlib import Path
from canvas_sync import ingest as ingest_mod
from canvas_sync.ingest import ingest_zip
from canvas_sync.manifest import parse_manifest
from canvas_sync.materialize import rebuild_latest
from canvas_sync.cli import main
from tests.helpers import TempStore, base_manifest, doc_item, file_item, make_zip

A1 = doc_item("assignment", "456", "Assignments/A1.html", "A1")
X = file_item("1", "Files/x.pdf", 1)
Y = file_item("2", "Files/y.pdf", 1)
V = {"Assignments/A1.html": b"a", "Files/x.pdf": b"x", "Files/y.pdf": b"y"}


class ReviewFixTests(TempStore):
    def q(self, sql, *args):
        return [dict(r) for r in self.store.db.execute(sql, args)]

    # Important 1: failure after the DB commit must be recoverable on retry
    def test_failure_after_commit_is_repaired_on_retry(self):
        z = make_zip(self.root / "in/a.zip", base_manifest(items=[A1]), V)
        original = ingest_mod._after_commit
        calls = {"n": 0}

        def boom(store, run_id, warnings):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("disk full")
            return original(store, run_id, warnings)

        ingest_mod._after_commit = boom
        try:
            with self.assertRaises(OSError):
                ingest_zip(self.store, z)
            self.assertTrue(z.exists(), "zip must stay in inbox after failure")
            run = self.q("select * from runs")[0]
            self.assertIsNotNone(run["snapshot_path"], "snapshot written before commit")
            res = ingest_zip(self.store, z)  # retry: same hash
            self.assertFalse(res.skipped)
            self.assertIsNotNone(self.q("select report_path from runs")[0]["report_path"])
            self.assertFalse(z.exists())
            self.assertEqual(len(self.q("select * from runs")), 1)
        finally:
            ingest_mod._after_commit = original

    # Important 2: removal only for item types the export actually covered
    def test_removal_limited_to_exported_types(self):
        ingest_zip(self.store, make_zip(self.root / "in/a.zip", base_manifest(items=[A1, X]), V))
        m = base_manifest(exported_at="2026-10-07T00:00:00Z", items=[A1])
        m["exportedTypes"] = ["assignment"]
        ingest_zip(self.store, make_zip(self.root / "in/b.zip", m, V))
        self.assertEqual(self.q("select removed_run from items where key='1'"), [{"removed_run": None}])
        # latest/ still shows the file from its last known version
        self.assertTrue((self.store.latest / "XLS5C202609" / "Files/x.pdf").exists())

    # Important 3: listed-but-failed files stay in latest/ from their last version
    def test_failed_file_kept_in_latest(self):
        ingest_zip(self.store, make_zip(self.root / "in/a.zip", base_manifest(items=[X, Y]), V))
        m = base_manifest(exported_at="2026-10-07T00:00:00Z", complete=False, items=[X, Y])
        m["failedPaths"] = ["Files/y.pdf"]
        ingest_zip(self.store, make_zip(self.root / "in/b.zip", m, {"Files/x.pdf": b"x"}))
        tree = self.store.latest / "XLS5C202609"
        self.assertTrue((tree / "Files/y.pdf").exists())
        self.assertEqual((tree / "Files/y.pdf").read_bytes(), b"y")

    # Important 4: blobs are read-only so a hardlinked latest/ cannot be edited in place
    def test_blobs_are_read_only(self):
        sha = self.store.put_blob(b"data")
        mode = stat.S_IMODE(os.stat(self.store.blob_path(sha)).st_mode)
        self.assertEqual(mode & 0o222, 0)
        ingest_zip(self.store, make_zip(self.root / "in/a.zip", base_manifest(items=[X]), V))
        with self.assertRaises(PermissionError):
            with open(self.store.latest / "XLS5C202609" / "Files/x.pdf", "ab") as fh:
                fh.write(b"!")

    # Important 5: duplicate identities in one manifest are deduplicated with a warning
    def test_duplicate_identity_collapsed(self):
        m = parse_manifest(base_manifest(items=[
            doc_item("submission", "9", "Submissions/A/Alice.html", "A — Alice"),
            doc_item("submission", "9", "Submissions/A/Bob.html", "A — Bob"),
        ]))
        self.assertEqual(len(m.items), 1)
        self.assertEqual(len(m.warnings), 1)
        self.assertIn("duplicate", m.warnings[0])

    # Important 7: two courses with the same name get distinct latest/ dirs
    def test_same_course_name_different_ids_do_not_collide(self):
        ingest_zip(self.store, make_zip(self.root / "in/a.zip", base_manifest(items=[A1], course_id="1", course_name="Same"), V))
        ingest_zip(self.store, make_zip(self.root / "in/b.zip", base_manifest(items=[X], course_id="2", course_name="Same"), V))
        self.assertTrue((self.store.latest / "Same" / "Assignments/A1.html").exists())
        self.assertTrue((self.store.latest / "Same (2)" / "Files/x.pdf").exists())

    # Re-graded Important: one bad ZIP must not abort ingest-all, and the error is reported
    def test_ingest_all_continues_past_broken_manifest_json(self):
        bad = self.root / "in/bad.zip"
        import zipfile
        bad.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("manifest.json", "{not json")
        make_zip(self.root / "in/good.zip", base_manifest(items=[A1]), V)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["--root", str(self.root), "ingest-all", str(self.root / "in")])
        self.assertEqual(code, 2)
        self.assertIn("bad.zip", buf.getvalue())
        self.assertIn("1 new", buf.getvalue())
        self.assertEqual(len(self.q("select * from runs")), 1)
