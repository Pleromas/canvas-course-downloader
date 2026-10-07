import unittest
from canvas_sync.manifest import parse_manifest, ManifestError, canonical_meta
from tests.helpers import base_manifest, doc_item, synthetic_item


class ManifestTests(unittest.TestCase):
    def test_parses_items_and_course(self):
        m = parse_manifest(base_manifest(items=[
            doc_item("assignment", "456", "Assignments/A1.html", "A1", {"points_possible": 10}),
            synthetic_item("grades.csv", "Grades.csv"),
            {"type": "module", "canvasId": "77", "path": None, "title": "Week 1", "updatedAt": None,
             "size": None, "meta": {"position": 1, "items": []}, "sourceCourseId": None},
        ]))
        self.assertEqual(m.course_id, "133044")
        self.assertEqual(m.course_name, "XLS5C202609")
        self.assertTrue(m.complete)
        self.assertEqual([i.key for i in m.items], ["456", "grades.csv", "77"])
        self.assertEqual(m.items[0].meta["points_possible"], 10)
        self.assertIsNone(m.items[2].path)

    def test_rejects_old_schema(self):
        data = base_manifest()
        del data["schema"]
        with self.assertRaises(ManifestError) as cm:
            parse_manifest(data)
        self.assertIn("schema 2", str(cm.exception))

    def test_rejects_item_without_identity(self):
        bad = base_manifest(items=[{"type": "file", "path": "x.pdf", "title": "x"}])
        with self.assertRaises(ManifestError):
            parse_manifest(bad)

    def test_canonical_meta_is_key_sorted(self):
        self.assertEqual(canonical_meta({"b": 1, "a": [1, 2]}), '{"a": [1, 2], "b": 1}')
        self.assertEqual(canonical_meta({}), "{}")
        self.assertEqual(canonical_meta(None), "{}")
