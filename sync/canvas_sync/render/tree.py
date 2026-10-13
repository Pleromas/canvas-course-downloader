"""Reading order for a run: modules first (Canvas position), then everything the
modules did not reference, grouped by type."""
from __future__ import annotations

from dataclasses import dataclass, field

# Module item `type` (Canvas) -> item type in the snapshot.
_MODULE_TYPE = {"Assignment": "assignment", "Quiz": "quiz", "File": "file",
                "Page": "page", "Discussion": "discussion"}

# Reading order for items no module placed, and their group titles.
GROUP_ORDER = [
    ("assignment", "Assignments"), ("quiz", "Quizzes"), ("announcement", "Announcements"),
    ("discussion", "Discussions"), ("page", "Pages"), ("file", "Files"),
    ("submission", "Submissions"), ("media", "Media"), ("synthetic", "Other"),
]
EXCLUDED_SYNTHETIC = {"styles.css", "_inaccessible_links.csv"}


@dataclass
class Node:
    item: dict
    children: list["Node"] = field(default_factory=list)
    kind: str = "item"  # module | group | item


def _index(items):
    by_key = {}
    by_slug = {}
    for it in items:
        by_key[(it["type"], str(it["key"]))] = it
        slug = (it.get("meta") or {}).get("slug")
        if it["type"] == "page" and slug:
            by_slug[str(slug)] = it
    return by_key, by_slug


def build_tree(snapshot: dict) -> list[Node]:
    items = [i for i in snapshot.get("items", [])
             if not (i["type"] == "synthetic" and str(i["key"]).lower() in EXCLUDED_SYNTHETIC)]
    by_key, by_slug = _index(items)
    placed: set[tuple[str, str]] = set()
    nodes: list[Node] = []

    modules = sorted((i for i in items if i["type"] == "module"),
                     key=lambda m: ((m.get("meta") or {}).get("position") or 0, m["title"]))
    for m in modules:
        node = Node(m, kind="module")
        for mi in (m.get("meta") or {}).get("items") or []:
            t = _MODULE_TYPE.get(mi.get("type") or "")
            cid = mi.get("contentId")
            target = None
            if t == "page" and cid:
                target = by_slug.get(str(cid)) or by_key.get(("page", str(cid)))
            elif t and cid is not None:
                target = by_key.get((t, str(cid)))
            if target is not None:
                node.children.append(Node(target))
                placed.add((target["type"], str(target["key"])))
            else:
                node.children.append(Node({"type": "external", "key": str(mi.get("id")),
                                           "path": None, "title": mi.get("title") or "",
                                           "meta": {"module_item_type": mi.get("type")}}))
        nodes.append(node)

    for type_, title in GROUP_ORDER:
        rest = [i for i in items if i["type"] == type_ and (type_, str(i["key"])) not in placed]
        if rest:
            rest.sort(key=lambda i: (i.get("title") or "", str(i["key"])))
            nodes.append(Node({"type": "group", "key": type_, "path": None, "title": title, "meta": {}},
                              [Node(i) for i in rest], kind="group"))
    return nodes


def flatten(nodes: list[Node]) -> list[dict]:
    out = []
    for n in nodes:
        if n.kind == "item" and n.item["type"] != "external":
            out.append(n.item)
        out.extend(flatten(n.children))
    return out
