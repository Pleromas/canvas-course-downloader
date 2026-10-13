import unittest
from canvas_sync.render.tree import build_tree, flatten


def mod(key, title, position, items):
    return {"type": "module", "key": key, "path": None, "title": title,
            "meta": {"position": position, "items": items}}


def item(type_, key, path, title, meta=None):
    return {"type": type_, "key": key, "path": path, "title": title, "meta": meta or {}}


SNAP = {"items": [
    mod("m2", "Week 1", 2, [{"id": "1", "type": "Assignment", "title": "A1", "contentId": "a1"},
                            {"id": "2", "type": "Page", "title": "Intro", "contentId": "intro-slug"},
                            {"id": "3", "type": "ExternalUrl", "title": "Zoom", "contentId": None}]),
    mod("m1", "Week 0", 1, [{"id": "4", "type": "Quiz", "title": "Q0", "contentId": "q0"},
                            {"id": "5", "type": "File", "title": "lec0.pdf", "contentId": "f0"}]),
    item("assignment", "a1", "Assignments/A1.html", "A1"),
    item("assignment", "a2", "Assignments/A2.html", "A2"),
    item("quiz", "q0", "Quizzes/Q0/Q0.html", "Q0"),
    item("page", "88", "Pages/intro-slug.html", "Intro", {"slug": "intro-slug"}),
    item("file", "f0", "Extracted_Files/lec0.pdf", "lec0.pdf"),
    item("file", "f1", "Extracted_Files/extra.pdf", "extra.pdf"),
    item("synthetic", "styles.css", "styles.css", "styles.css"),
    item("synthetic", "grades.csv", "Grades.csv", "Grades.csv"),
    item("announcement", "n1", "Announcements/N.html", "N"),
]}


class TreeTests(unittest.TestCase):
    def test_modules_in_position_order_with_resolved_children(self):
        tree = build_tree(SNAP)
        mods = [n for n in tree if n.kind == "module"]
        self.assertEqual([n.item["title"] for n in mods], ["Week 0", "Week 1"])
        self.assertEqual([c.item["key"] for c in mods[0].children], ["q0", "f0"])

    def test_module_page_by_slug(self):
        tree = build_tree(SNAP)
        week1 = [n for n in tree if n.kind == "module"][1]
        self.assertEqual(week1.children[1].item["key"], "88")

    def test_external_module_item_kept_as_placeholder(self):
        tree = build_tree(SNAP)
        week1 = [n for n in tree if n.kind == "module"][1]
        ext = week1.children[2].item
        self.assertEqual(ext["type"], "external")
        self.assertEqual(ext["title"], "Zoom")

    def test_unreferenced_items_grouped_by_type_in_fixed_order(self):
        tree = build_tree(SNAP)
        groups = [n for n in tree if n.kind == "group"]
        self.assertEqual([g.item["title"] for g in groups], ["Assignments", "Announcements", "Files", "Other"])
        self.assertEqual([c.item["key"] for c in groups[0].children], ["a2"])
        self.assertEqual([c.item["key"] for c in groups[2].children], ["f1"])
        self.assertEqual([c.item["key"] for c in groups[3].children], ["grades.csv"])

    def test_styles_and_inaccessible_excluded_and_flatten_order(self):
        keys = [i["key"] for i in flatten(build_tree(SNAP))]
        self.assertNotIn("styles.css", keys)
        self.assertEqual(keys, ["q0", "f0", "a1", "88", "a2", "n1", "f1", "grades.csv"])
