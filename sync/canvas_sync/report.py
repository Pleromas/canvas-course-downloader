"""Markdown change report (spec Part D)."""
from __future__ import annotations

from pathlib import Path

from .changes import ChangeSet, ItemChange
from .store import Store

_TYPE_LABEL = {"synthetic": "generated"}
_COL = 12


def _stamp(iso: str | None) -> str:
    if not iso:
        return "?"
    return iso.replace("T", " ")[:16]


def _date(v) -> str:
    return str(v)[:10] if isinstance(v, str) and len(v) >= 10 and v[4] == "-" else str(v)


def _label(ch: ItemChange) -> str:
    t = "grades" if ch.key.endswith("grades.csv") else _TYPE_LABEL.get(ch.type, ch.type)
    return t.ljust(_COL)


def _size(n) -> str:
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def _new_line(ch: ItemChange) -> str:
    bits = []
    if ch.meta.get("due_at"):
        bits.append(f"due {_date(ch.meta['due_at'])}")
    if ch.meta.get("points_possible") is not None:
        bits.append(f"{ch.meta['points_possible']} pts")
    tail = f" — {', '.join(bits)}" if bits else ""
    return f"- {_label(ch)}{ch.title}{tail}"


def _changed_lines(ch: ItemChange) -> list[str]:
    if ch.grade_changes:
        return [f"- {_label(ch)}{name} — score {o} → {n}" for name, o, n in ch.grade_changes]
    parts = [f"{k} {_date(o)} → {_date(n)}" for k, o, n in ch.meta_diff]
    if ch.body_added or ch.body_removed:
        parts.append(f"body +{ch.body_added} −{ch.body_removed} lines")
    if ch.size_change:
        parts.append(f"content changed ({_size(ch.size_change[0])} → {_size(ch.size_change[1])})")
    if not parts:
        parts.append("content changed")
    return [f"- {_label(ch)}{ch.title} — {'; '.join(parts)}"]


def render_report(cs: ChangeSet) -> str:
    vs = f"(vs {_stamp(cs.prev_exported_at)})" if cs.prev_run_id else "(first export, no baseline)"
    out = [f"# {cs.course_name} — {_stamp(cs.exported_at)} {vs}", ""]
    out.append(f"## New ({len(cs.new)})")
    out += [_new_line(c) for c in cs.new]
    out.append(f"## Changed ({len(cs.changed)})")
    for c in cs.changed:
        out += _changed_lines(c)
    out.append(f"## Removed ({len(cs.removed)})")
    out += [f"- {_label(c)}{c.title}" for c in cs.removed]
    diffs = [c for c in cs.changed if c.body_diff]
    if diffs:
        out.append("")
        for c in diffs:
            out += [f"<details><summary>{c.title} body diff</summary>", "", "```diff", c.body_diff, "```", "</details>", ""]
    return "\n".join(out).rstrip() + "\n"


def one_line(cs: ChangeSet) -> str:
    return f"{cs.course_name}: {len(cs.new)} new, {len(cs.changed)} changed, {len(cs.removed)} removed"


def write_report(store: Store, cs: ChangeSet) -> Path:
    stamp = cs.exported_at.replace(":", "").replace("-", "").replace(".", "_")
    path = store.reports / cs.course_id / f"{stamp}_run{cs.run_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(cs))
    store.db.execute("update runs set report_path=? where id=?", (str(path), cs.run_id))
    store.db.commit()
    return path
