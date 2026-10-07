from canvas_sync.ingest import ingest_zip
from canvas_sync.changes import compute_changes
from tests.helpers import TempStore, base_manifest, doc_item, file_item, synthetic_item, make_zip

A1 = doc_item("assignment", "456", "Assignments/A1.html", "A1", {"due_at": "2026-10-10", "points_possible": 10})
A2 = doc_item("assignment", "457", "Assignments/A2.html", "A2", {"due_at": "2026-10-20", "points_possible": 15})
PDF = file_item("9001", "Extracted_Files/lec.pdf", 3)
GRADES = synthetic_item("grades.csv", "Grades.csv")
MOD = {"type": "module", "canvasId": "77", "path": None, "title": "Week 1", "updatedAt": None, "size": None,
       "meta": {"position": 1, "items": [{"id": "1", "type": "Page", "title": "a", "contentId": "a"},
                                         {"id": "2", "type": "Page", "title": "b", "contentId": "b"}]}, "sourceCourseId": None}
V1 = {"Assignments/A1.html": b"<h1>A1</h1><p>Due Friday</p>", "Extracted_Files/lec.pdf": b"pdf",
      "Grades.csv": b"Assignment,Score\nA1,\n"}


class ChangeTests(TempStore):
    def run_two(self, items1, files1, items2, files2):
        r1 = ingest_zip(self.store, make_zip(self.root / "in/a.zip", base_manifest(items=items1), files1))
        r2 = ingest_zip(self.store, make_zip(self.root / "in/b.zip", base_manifest(exported_at="2026-10-07T00:00:00Z", items=items2), files2))
        return r1.run_id, r2.run_id

    def test_first_run_everything_new(self):
        r1, _ = self.run_two([A1, PDF], V1, [A1, PDF], V1)
        cs = compute_changes(self.store, r1)
        self.assertIsNone(cs.prev_run_id)
        self.assertEqual(sorted(c.key for c in cs.new), ["456", "9001"])
        self.assertEqual(cs.changed, [])

    def test_meta_and_body_and_binary_and_removed_and_new(self):
        a1b = dict(A1, meta={"due_at": "2026-10-12", "points_possible": 10})
        v2 = {"Assignments/A1.html": b"<h1>A1</h1><p>Due Monday</p><p>Bring laptop</p>",
              "Extracted_Files/lec.pdf": b"pdf-v2!", "Assignments/A2.html": b"<h1>A2</h1>"}
        _, r2 = self.run_two([A1, PDF, GRADES], V1, [a1b, PDF, A2], v2)
        cs = compute_changes(self.store, r2)
        self.assertEqual([c.key for c in cs.new], ["457"])
        self.assertEqual([c.key for c in cs.removed], ["grades.csv"])
        by_key = {c.key: c for c in cs.changed}
        a1 = by_key["456"]
        self.assertEqual(a1.meta_diff, [("due_at", "2026-10-10", "2026-10-12")])
        self.assertEqual((a1.body_added, a1.body_removed), (2, 1))
        self.assertIn("+Due Monday", a1.body_diff)
        pdf = by_key["9001"]
        self.assertEqual(pdf.size_change, (3, 7))
        self.assertEqual(pdf.body_diff, "")

    def test_grades_changes_are_per_assignment(self):
        v2 = dict(V1, **{"Grades.csv": b"Assignment,Score\nA1,9\n"})
        _, r2 = self.run_two([A1, GRADES], V1, [A1, GRADES], v2)
        cs = compute_changes(self.store, r2)
        g = cs.changed[0]
        self.assertEqual(g.key, "grades.csv")
        self.assertEqual(g.grade_changes, [("A1", "—", "9")])
        self.assertEqual(g.body_diff, "")

    def test_module_reorder_reported(self):
        mod2 = dict(MOD, meta={"position": 1, "items": list(reversed(MOD["meta"]["items"]))})
        _, r2 = self.run_two([MOD], {}, [mod2], {})
        cs = compute_changes(self.store, r2)
        self.assertEqual(cs.changed[0].meta_diff, [("items", "order changed", "a, b → b, a")])

    def test_title_change_is_meta_change(self):
        a1b = dict(A1, title="A1 (updated)", path="Assignments/A1 (updated).html")
        v2 = {"Assignments/A1 (updated).html": V1["Assignments/A1.html"]}
        _, r2 = self.run_two([A1], V1, [a1b], v2)
        cs = compute_changes(self.store, r2)
        self.assertEqual(cs.new, [])
        self.assertEqual(cs.removed, [])
        self.assertEqual(cs.changed[0].meta_diff, [("title", "A1", "A1 (updated)")])
