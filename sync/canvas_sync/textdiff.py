"""HTML to text, unified diffs, and Grades.csv comparison. Stdlib only."""
from __future__ import annotations

import csv
import difflib
import io
from html.parser import HTMLParser

_BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table",
          "blockquote", "pre", "hr", "section", "article", "header", "footer", "details", "summary"}
_SKIP = {"script", "style", "head", "title", "noscript"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip += 1
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP and self._skip:
            self._skip -= 1
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _Text()
    p.feed(html or "")
    p.close()
    lines = [" ".join(seg.split()) for seg in "".join(p.parts).split("\n")]
    return "\n".join(line for line in lines if line)


def unified(old: str, new: str, label_old: str = "old", label_new: str = "new") -> tuple[str, int, int]:
    if old == new:
        return "", 0, 0
    a, b = old.splitlines(), new.splitlines()
    out = list(difflib.unified_diff(a, b, label_old, label_new, lineterm="", n=2))
    added = sum(1 for l in out[2:] if l.startswith("+"))
    removed = sum(1 for l in out[2:] if l.startswith("-"))
    return "\n".join(out), added, removed


def _grades_rows(text: str) -> dict[str, str]:
    rows = list(csv.DictReader(io.StringIO(text)))
    out: dict[str, str] = {}
    for r in rows:
        name = (r.get("Assignment") or "").strip()
        if not name:
            continue
        score = (r.get("Score") or "").strip()
        out[name] = score if score else "—"
    return out


def grades_diff(old_csv: str, new_csv: str) -> list[tuple[str, str, str]]:
    old, new = _grades_rows(old_csv), _grades_rows(new_csv)
    changes = []
    for name in list(new) + [n for n in old if n not in new]:
        o, n = old.get(name, "—"), new.get(name, "—")
        if o != n:
            changes.append((name, o, n))
    return changes
