import json, zipfile
from pathlib import Path
from canvas_sync.ingest import ingest_zip, ingest_dir, IngestError
from canvas_sync.manifest import ManifestError
from tests.helpers import TempStore, base_manifest, doc_item, file_item, synthetic_item, make_zip

A1 = doc_item("assignment", "456", "Assignments/A1.html", "A1", {"due_at": "2026-10-10", "points_possible": 10})
PDF = file_item("9001", "Extracted_Files/lec.pdf", 3)
GRADES = synthetic_item("grades.csv", "Grades.csv")
FILES = {"Assignments/A1.html": b"<h1>A1</h1><p>v1</p>", "Extracted_Files/lec.pdf": b"pdf", "Grades.csv": b"Assignment,Score\nA1,"}


class IngestTests(TempStore):
    def zip(self, name, manifest, files=FILES):
        return make_zip(self.root / "inbox" / name, manifest, files)

    def q(self, sql, *args):
        return [dict(r) for r in self.store.db.execute(sql, args)]

    def test_first_run_creates_everything(self):
        res = ingest_zip(self.store, self.zip("a.zip", base_manifest(items=[A1, PDF, GRADES])))
        self.assertFalse(res.skipped)
        self.assertEqual(self.q("select id,name from courses"), [{"id": "133044", "name": "XLS5C202609"}])
        self.assertEqual(len(self.q("select * from runs")), 1)
        self.assertEqual(len(self.q("select * from items")), 3)
        self.assertEqual(len(self.q("select * from versions")), 3)
        self.assertEqual(len(self.q("select * from blobs")), 3)
        run = self.q("select * from runs")[0]
        snap = json.loads(Path(run["snapshot_path"]).read_text())
        self.assertEqual(set(snap["files"]), set(FILES))
        self.assertTrue((self.store.processed / "a.zip").exists())
        self.assertFalse((self.root / "inbox" / "a.zip").exists())

    def test_unchanged_rerun_adds_no_versions(self):
        ingest_zip(self.store, self.zip("a.zip", base_manifest(items=[A1, PDF, GRADES])))
        ingest_zip(self.store, self.zip("b.zip", base_manifest(exported_at="2026-10-07T00:00:00Z", items=[A1, PDF, GRADES])))
        self.assertEqual(len(self.q("select * from runs")), 2)
        self.assertEqual(len(self.q("select * from versions")), 3)
        self.assertEqual(self.q("select distinct last_run from items"), [{"last_run": 2}])

    def test_meta_only_change_adds_version(self):
        ingest_zip(self.store, self.zip("a.zip", base_manifest(items=[A1])))
        a1b = dict(A1, meta={"due_at": "2026-10-12", "points_possible": 10})
        ingest_zip(self.store, self.zip("b.zip", base_manifest(exported_at="2026-10-07T00:00:00Z", items=[a1b])))
        vs = self.q("select meta_json from versions order by id")
        self.assertEqual(len(vs), 2)
        self.assertIn('"due_at": "2026-10-12"', vs[1]["meta_json"])

    def test_body_change_adds_version_and_blob(self):
        ingest_zip(self.store, self.zip("a.zip", base_manifest(items=[A1])))
        ingest_zip(self.store, self.zip("b.zip", base_manifest(exported_at="2026-10-07T00:00:00Z", items=[A1]),
                                        {"Assignments/A1.html": b"<h1>A1</h1><p>v2</p>"}))
        self.assertEqual(len(self.q("select * from versions")), 2)
        self.assertEqual(len(self.q("select * from blobs")), 2)

    def test_removed_only_on_complete_run(self):
        ingest_zip(self.store, self.zip("a.zip", base_manifest(items=[A1, PDF])))
        ingest_zip(self.store, self.zip("b.zip", base_manifest(exported_at="2026-10-07T00:00:00Z", complete=False, items=[A1])))
        self.assertEqual(self.q("select removed_run from items where key='9001'"), [{"removed_run": None}])
        ingest_zip(self.store, self.zip("c.zip", base_manifest(exported_at="2026-10-08T00:00:00Z", items=[A1])))
        self.assertEqual(self.q("select removed_run from items where key='9001'"), [{"removed_run": 3}])
        ingest_zip(self.store, self.zip("d.zip", base_manifest(exported_at="2026-10-09T00:00:00Z", items=[A1, PDF])))
        self.assertEqual(self.q("select removed_run from items where key='9001'"), [{"removed_run": None}])

    def test_duplicate_zip_is_skipped(self):
        z = self.zip("a.zip", base_manifest(items=[A1]))
        ingest_zip(self.store, z)
        res = ingest_zip(self.store, self.zip("again.zip", base_manifest(items=[A1])))
        self.assertTrue(res.skipped)
        self.assertEqual(len(self.q("select * from runs")), 1)
        self.assertTrue((self.store.processed / "again.zip").exists())

    def test_corrupt_zip_left_in_place(self):
        bad = self.root / "inbox" / "bad.zip"
        bad.parent.mkdir(parents=True)
        bad.write_bytes(b"not a zip")
        res = ingest_zip(self.store, bad)
        self.assertTrue(res.skipped)
        self.assertIn("not a valid ZIP", res.reason)
        self.assertTrue(bad.exists())
        self.assertEqual(self.q("select * from runs"), [])

    def test_schema1_rejected(self):
        legacy = {"course": "X", "courseId": "1", "counts": {}}
        z = self.zip("old.zip", legacy, {})
        with self.assertRaises(ManifestError):
            ingest_zip(self.store, z)
        self.assertTrue(z.exists())

    def test_same_second_exports_do_not_collide(self):
        ingest_zip(self.store, self.zip("a.zip", base_manifest(items=[A1])))
        ingest_zip(self.store, self.zip("b.zip", base_manifest(items=[A1]), {"Assignments/A1.html": b"x"}))
        paths = [r["snapshot_path"] for r in self.q("select snapshot_path from runs")]
        self.assertEqual(len(set(paths)), 2)
        self.assertTrue(all(Path(p).exists() for p in paths))

    def test_missing_file_in_complete_run_is_warning_not_removal(self):
        ingest_zip(self.store, self.zip("a.zip", base_manifest(items=[A1, PDF])))
        res = ingest_zip(self.store, self.zip("b.zip", base_manifest(exported_at="2026-10-07T00:00:00Z", items=[A1, PDF]),
                                              {"Assignments/A1.html": FILES["Assignments/A1.html"]}))
        self.assertIn("missing from ZIP", res.summary)
        self.assertEqual(self.q("select removed_run from items where key='9001'"), [{"removed_run": None}])

    def test_ingest_dir_skips_part_files(self):
        self.zip("a.zip", base_manifest(items=[A1]))
        (self.root / "inbox" / "b.zip.part").write_bytes(b"partial")
        results = ingest_dir(self.store, self.root / "inbox")
        self.assertEqual(len(results), 1)
        self.assertTrue((self.root / "inbox" / "b.zip.part").exists())
