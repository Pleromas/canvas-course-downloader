from canvas_sync.changes import ChangeSet, ItemChange
from canvas_sync.report import render_report, one_line, write_report
from canvas_sync.ingest import ingest_zip
from tests.helpers import TempStore, base_manifest, doc_item, make_zip


def sample():
    cs = ChangeSet("133044", "XLS5C202609", 2, "2026-10-06T20:31:00Z", 1, "2026-10-06T14:41:00Z")
    cs.new.append(ItemChange("new", "assignment", "457", "Assignment 2", "Assignments/Assignment 2.html",
                             meta={"due_at": "2026-10-20T06:59:00Z", "points_possible": 15}))
    cs.changed.append(ItemChange("changed", "assignment", "456", "Assignment 1", "Assignments/Assignment 1.html",
                                 meta_diff=[("due_at", "2026-10-10T06:59:00Z", "2026-10-12T06:59:00Z")],
                                 body_diff="--- previous\n+++ current\n@@ -1 +1,2 @@\n-Due Friday\n+Due Monday\n+Bring laptop",
                                 body_added=2, body_removed=1))
    cs.changed.append(ItemChange("changed", "synthetic", "grades.csv", "Grades.csv", "Grades.csv",
                                 grade_changes=[("Assignment 0", "—", "9")]))
    cs.changed.append(ItemChange("changed", "file", "9001", "lec.pdf", "Extracted_Files/lec.pdf", size_change=(3, 7)))
    return cs


class ReportTests(TempStore):
    def test_render(self):
        md = render_report(sample())
        self.assertTrue(md.startswith("# XLS5C202609 — 2026-10-06 20:31 (vs 2026-10-06 14:41)"))
        self.assertIn("## New (1)\n- assignment  Assignment 2 — due 2026-10-20, 15 pts", md)
        self.assertIn("- assignment  Assignment 1 — due_at 2026-10-10 → 2026-10-12; body +2 −1 lines", md)
        self.assertIn("- grades      Assignment 0 — score — → 9", md)
        self.assertIn("- file        lec.pdf — content changed (3 B → 7 B)", md)
        self.assertIn("## Removed (0)", md)
        self.assertIn("<details><summary>Assignment 1 body diff</summary>", md)
        self.assertIn("```diff\n--- previous", md)

    def test_first_run_note(self):
        cs = ChangeSet("1", "C", 1, "2026-10-06T20:31:00Z", None, None)
        md = render_report(cs)
        self.assertIn("(first export, no baseline)", md)

    def test_one_line(self):
        self.assertEqual(one_line(sample()), "XLS5C202609: 1 new, 3 changed, 0 removed")

    def test_write_report_records_path(self):
        res = ingest_zip(self.store, make_zip(self.root / "in/a.zip", base_manifest(items=[
            doc_item("assignment", "1", "Assignments/A.html", "A")]), {"Assignments/A.html": b"x"}))
        row = self.store.db.execute("select report_path from runs where id=?", (res.run_id,)).fetchone()
        self.assertIsNotNone(row["report_path"])
        self.assertTrue(row["report_path"].endswith(".md"))
        self.assertIn("1 new", res.summary)
