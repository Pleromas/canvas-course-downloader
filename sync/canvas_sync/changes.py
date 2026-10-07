"""Compute what changed in a run versus the item's previous version (spec Part D)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .store import Store
from .textdiff import grades_diff, html_to_text, unified

TEXT_SUFFIXES = (".html", ".htm", ".md", ".txt", ".csv", ".srt", ".json", ".css")


@dataclass
class ItemChange:
    kind: str                     # new | changed | removed
    type: str
    key: str
    title: str
    path: str | None
    meta_diff: list[tuple[str, object, object]] = field(default_factory=list)
    body_diff: str = ""
    body_added: int = 0
    body_removed: int = 0
    size_change: tuple[int | None, int | None] | None = None
    grade_changes: list[tuple[str, str, str]] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


@dataclass
class ChangeSet:
    course_id: str
    course_name: str
    run_id: int
    exported_at: str
    prev_run_id: int | None
    prev_exported_at: str | None
    new: list[ItemChange] = field(default_factory=list)
    changed: list[ItemChange] = field(default_factory=list)
    removed: list[ItemChange] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.new) + len(self.changed) + len(self.removed)


def _module_items_label(items) -> str:
    return ", ".join(str((i or {}).get("title") or (i or {}).get("id") or "?") for i in items or [])


def meta_diff(old: dict, new: dict) -> list[tuple[str, object, object]]:
    out = []
    for k in sorted(set(old) | set(new)):
        o, n = old.get(k), new.get(k)
        if o == n:
            continue
        if k == "items" and isinstance(o, list) and isinstance(n, list):
            same_members = sorted(json.dumps(x, sort_keys=True) for x in o) == sorted(json.dumps(x, sort_keys=True) for x in n)
            if same_members:
                out.append((k, "order changed", f"{_module_items_label(o)} → {_module_items_label(n)}"))
            else:
                out.append((k, _module_items_label(o), _module_items_label(n)))
            continue
        out.append((k, o, n))
    return out


def _is_text(path: str | None) -> bool:
    return bool(path) and path.lower().endswith(TEXT_SUFFIXES)


def _is_html(path: str | None) -> bool:
    return bool(path) and path.lower().endswith((".html", ".htm"))


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _change_for(store: Store, item: dict, cur: dict, prev: dict | None) -> ItemChange:
    ch = ItemChange("new" if prev is None else "changed", item["type"], item["key"], cur["title"], cur["path"],
                    meta=json.loads(cur["meta_json"]))
    if prev is None:
        return ch
    old_meta, new_meta = json.loads(prev["meta_json"]), json.loads(cur["meta_json"])
    ch.meta_diff = meta_diff(old_meta, new_meta)
    if prev["title"] != cur["title"]:
        ch.meta_diff.insert(0, ("title", prev["title"], cur["title"]))
    if prev["sha256"] != cur["sha256"] and cur["sha256"] and prev["sha256"]:
        old_b, new_b = store.read_blob(prev["sha256"]), store.read_blob(cur["sha256"])
        if item["type"] == "synthetic" and item["key"].endswith("grades.csv"):
            ch.grade_changes = grades_diff(_decode(old_b), _decode(new_b))
        elif _is_text(cur["path"]):
            to_text = html_to_text if _is_html(cur["path"]) else (lambda s: s)
            ch.body_diff, ch.body_added, ch.body_removed = unified(
                to_text(_decode(old_b)), to_text(_decode(new_b)), "previous", "current")
        else:
            ch.size_change = (prev["size"], cur["size"])
    return ch


def compute_changes(store: Store, run_id: int) -> ChangeSet:
    db = store.db
    run = db.execute("select r.*, c.name as course_name from runs r join courses c on c.id=r.course_id where r.id=?",
                     (run_id,)).fetchone()
    if run is None:
        raise ValueError(f"run {run_id} not found")
    prev = db.execute("select id, exported_at from runs where course_id=? and id<? order by id desc limit 1",
                      (run["course_id"], run_id)).fetchone()
    cs = ChangeSet(run["course_id"], run["course_name"], run_id, run["exported_at"],
                   prev["id"] if prev else None, prev["exported_at"] if prev else None)

    rows = db.execute(
        "select v.*, i.type, i.key from versions v join items i on i.id=v.item_id "
        "where v.run_id=? order by i.type, v.title", (run_id,)).fetchall()
    for v in rows:
        before = db.execute("select * from versions where item_id=? and id<? order by id desc limit 1",
                            (v["item_id"], v["id"])).fetchone()
        ch = _change_for(store, {"type": v["type"], "key": v["key"]}, dict(v), dict(before) if before else None)
        (cs.new if ch.kind == "new" else cs.changed).append(ch)

    removed = db.execute(
        "select i.type, i.key, v.title, v.path, v.meta_json from items i "
        "join versions v on v.id = (select id from versions where item_id=i.id order by id desc limit 1) "
        "where i.removed_run=? order by i.type, v.title", (run_id,)).fetchall()
    for r in removed:
        cs.removed.append(ItemChange("removed", r["type"], r["key"], r["title"], r["path"], meta=json.loads(r["meta_json"])))
    return cs
