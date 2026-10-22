"""Markdown tree for LLMs: one folder per run with index, per-item files, images,
PDF text and a single concatenated `_full.md` (spec: render design, section
"Markdown output")."""
from __future__ import annotations

import csv
import io
import json
import os
import posixpath
import re
import shutil
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from ..materialize import _safe_dir
from ..store import Store
from . import pdftools
from .htmlmd import html_to_markdown
from .pdftools import Capabilities
from .tree import EXCLUDED_SYNTHETIC, Node, _hidden, build_tree, flatten

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}
LOW_TEXT_WORDS = 40


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def run_stamp(exported_at: str) -> str:
    return exported_at.replace(":", "").replace("-", "").replace(".", "_")


def _yaml_scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_yaml_scalar(x) for x in v) + "]"
    s = str(v)
    if (s == "" or ": " in s or " #" in s or "\n" in s or s[0] in "[{\"'*&!%@`|>-?:" or s.strip() != s
            or re.fullmatch(r"-?\d+(\.\d+)?|true|false|null|yes|no", s, re.I)):
        return json.dumps(s, ensure_ascii=False)
    return s


def frontmatter(fields: dict) -> str:
    lines = ["---"]
    for k, v in fields.items():
        if v is None or isinstance(v, dict):
            continue
        lines.append(f"{k}: {_yaml_scalar(v)}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def _encode_rel(path: str) -> str:
    return quote(path, safe="/")


def _rel(from_dir: str, to_path: str) -> str:
    """Relative POSIX path from a directory (may be "") to a file path."""
    return posixpath.relpath(to_path, from_dir or ".")


def _strip_frontmatter(md: str) -> str:
    if md.startswith("---\n"):
        end = md.find("\n---\n", 4)
        if end != -1:
            return md[end + 5:].lstrip("\n")
    return md


_LINK_RE = re.compile(r"(!?\[[^\]]*\]\()([^)\s]+)(\))")


def rebase_links(md: str, from_dir: str) -> str:
    """Rewrite relative link targets in `md` (written for a file in `from_dir`)
    so they resolve from the tree root."""
    def fix(m):
        target = m.group(2)
        if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith(("/", "#")):
            return m.group(0)
        joined = posixpath.normpath(posixpath.join(from_dir or ".", unquote(target)))
        return m.group(1) + _encode_rel(joined) + m.group(3)
    return _LINK_RE.sub(fix, md)


def source_url(domain: str, course_id: str, item: dict) -> str | None:
    t, k = item["type"], str(item["key"])
    base = f"{domain}/courses/{course_id}"
    meta = item.get("meta") or {}
    if t == "assignment":
        return f"{base}/assignments/{k}"
    if t == "quiz":
        return f"{base}/quizzes/{k}"
    if t == "page":
        return f"{base}/pages/{meta.get('slug') or k}"
    if t in ("announcement", "discussion"):
        return f"{base}/discussion_topics/{k}"
    if t == "file":
        return f"{base}/files/{k}"
    if t == "submission":
        return f"{base}/assignments/{k.split(':')[0]}/submissions"
    return None


def _fmt_size(n) -> str:
    if not n:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


# ---------------------------------------------------------------------------
# PDF items
# ---------------------------------------------------------------------------

def render_pdf_item(store: Store, item: dict, sha: str, out_dir: Path, rel_dir: str, caps: Capabilities,
                    all_pages: bool = False, low_text_words: int = LOW_TEXT_WORDS) -> str:
    """Copy the PDF next to a `<name>.pdf.md` with per-page text and selected
    page images. Returns the .md path relative to `out_dir`."""
    name = posixpath.basename(item["path"])
    pdf_rel = posixpath.join(rel_dir, name) if rel_dir else name
    md_rel = pdf_rel + ".md"
    pdf_abs = out_dir / pdf_rel
    pdf_abs.parent.mkdir(parents=True, exist_ok=True)
    src = store.blob_path(sha)
    if not pdf_abs.exists() or pdf_abs.stat().st_size != src.stat().st_size:
        shutil.copyfile(src, pdf_abs)

    pages = pdftools.pdf_text_pages(pdf_abs) if caps.pdftotext else None
    count = pdftools.pdf_page_count(pdf_abs) if (caps.pypdf or caps.pdfinfo) else (len(pages) if pages else None)
    fields = {"type": "file", "canvas_id": str(item["key"]), "title": name,
              "content_type": "application/pdf", "pages": count, "source_note": (item.get("meta") or {}).get("source")}
    out = [frontmatter(fields), f"# {name}\n\nOriginal: [{name}]({_encode_rel(name)})\n"]
    if pages is None:
        out.append("\n_PDF text not extracted: `pdftotext` (poppler) is not installed on this machine._\n")
    else:
        depth = len([p for p in rel_dir.split("/") if p]) if rel_dir else 0
        up = "../" * depth
        for i, text in enumerate(pages, start=1):
            text = text.rstrip()
            words = len(text.split())
            out.append(f"\n## Page {i}\n")
            if text.strip():
                out.append("\n```text\n" + text + "\n```\n")
            else:
                out.append("\n_(no extractable text)_\n")
            if caps.pdftoppm and (all_pages or words < low_text_words):
                png_rel = f"pages/{sha[:12]}/p{i:03d}.png"
                png_abs = out_dir / png_rel
                if png_abs.exists() or pdftools.pdf_page_png(pdf_abs, i, png_abs):
                    out.append(f"\n![Page {i}]({up}{png_rel})\n")
    (out_dir / md_rel).write_text("".join(out))
    return md_rel


# ---------------------------------------------------------------------------
# whole tree
# ---------------------------------------------------------------------------

def _plan_paths(items: list[dict]) -> dict[str, dict]:
    """For every item with an export path decide its Markdown-tree target.
    Returns export_path -> {"md": path or None, "file": copied path or None,
    "kind": doc|pdf|image|other}."""
    plan: dict[str, dict] = {}
    used: set[str] = set()

    def unique(p: str) -> str:
        base, ext = posixpath.splitext(p)
        n, cand = 2, p
        while cand.lower() in used:
            cand = f"{base} ({n}){ext}"
            n += 1
        used.add(cand.lower())
        return cand

    for it in items:
        p = it.get("path")
        if not p:
            continue
        low = p.lower()
        if _hidden(it):
            continue
        if it["type"] == "synthetic" and low == "grades.csv":
            plan[p] = {"kind": "grades", "md": unique("Grades.md"), "file": None}
        elif it["type"] in ("file", "media"):
            folder = posixpath.dirname(p)
            folder = re.sub(r"^(Extracted_Files|Files)/?", "", folder).strip("/")
            rel_dir = posixpath.join("Files", folder) if folder else "Files"
            name = posixpath.basename(p)
            ext = posixpath.splitext(name)[1].lower()
            if ext == ".pdf":
                target = unique(posixpath.join(rel_dir, name))
                plan[p] = {"kind": "pdf", "md": target + ".md", "file": target, "rel_dir": posixpath.dirname(target)}
            elif ext in IMAGE_EXT:
                plan[p] = {"kind": "image", "md": None, "file": None, "ext": ext}  # file set once sha known
            else:
                plan[p] = {"kind": "other", "md": None, "file": unique(posixpath.join(rel_dir, name))}
        elif low.endswith((".html", ".htm", ".md")):
            plan[p] = {"kind": "doc", "md": unique(posixpath.splitext(p)[0] + ".md"), "file": None}
        else:
            plan[p] = {"kind": "other", "md": None, "file": unique(p)}
    return plan


def _grades_md(csv_text: str) -> str:
    rows = list(csv.reader(io.StringIO(csv_text)))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return "_No grades exported._"
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    esc = lambda c: c.replace("|", "\\|")
    lines = ["| " + " | ".join(esc(c) for c in rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    lines += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def _item_lines(node: Node, plan: dict, depth: int = 0) -> list[str]:
    lines = [("  " * depth) + _item_line(node.item, plan)]
    for c in node.children:
        lines.extend(_item_lines(c, plan, depth + 1))
    return lines


def _item_line(it: dict, plan: dict, link_from: str = "") -> str:
    meta = it.get("meta") or {}
    p = it.get("path")
    entry = plan.get(p) if p else None
    target = (entry or {}).get("md") or (entry or {}).get("file")
    label = it.get("title") or (posixpath.basename(p) if p else "untitled")
    bits = [it["type"]]
    if meta.get("due_at"):
        bits.append(f"due {str(meta['due_at'])[:10]}")
    if meta.get("points_possible") is not None:
        bits.append(f"{meta['points_possible']} pts")
    if it["type"] == "external":
        bits = [str(meta.get("module_item_type") or "external")]
    if it.get("size"):
        bits.append(_fmt_size(it["size"]))
    tail = ", ".join(bits)
    if target:
        return f"- [{label}]({_encode_rel(_rel(link_from, target))}) — {tail}"
    return f"- {label} — {tail}"


_HREF_RE = re.compile(r"""(?:href|src)=["']([^"']+)["']""", re.I)


def attachments_for(store: Store, snap: dict) -> dict[tuple[str, str], list[dict]]:
    """Files each exported document links to (by href/src), resolved through the
    export's relative paths. Used to nest lecture PDFs under the topic that links
    them in both the Markdown index and the book."""
    files = snap["files"]
    by_path = {i["path"]: i for i in snap["items"] if i.get("path")}
    out: dict[tuple[str, str], list[dict]] = {}
    for it in snap["items"]:
        p = it.get("path")
        if not p or not p.lower().endswith((".html", ".htm")) or p not in files or it["type"] in ("synthetic",):
            continue
        try:
            raw = store.read_blob(files[p]).decode("utf-8", "replace")
        except OSError:
            continue
        seen = set()
        for href in _HREF_RE.findall(raw):
            if re.match(r"^[a-z][a-z0-9+.-]*:", href, re.I) or href.startswith("#"):
                continue
            for clean in (unquote(href), unquote(urlsplit(href).path)):
                target = posixpath.normpath(posixpath.join(posixpath.dirname(p), clean))
                f = by_path.get(target)
                if f and f["type"] in ("file", "media") and f["key"] not in seen:
                    ext = posixpath.splitext(target)[1].lower()
                    if ext in IMAGE_EXT:
                        break  # inline images stay inline, not attachments
                    seen.add(f["key"])
                    out.setdefault((it["type"], str(it["key"])), []).append(f)
                    break
    return out


def render_markdown(store: Store, run_id: int, caps: Capabilities, all_pages: bool = False) -> Path:
    row = store.db.execute("select snapshot_path, exported_at, course_id from runs where id=?", (run_id,)).fetchone()
    if row is None or not row["snapshot_path"]:
        raise ValueError(f"run {run_id} has no snapshot")
    snap = json.loads(Path(row["snapshot_path"]).read_text())
    course = snap["course"]
    files: dict[str, str] = snap["files"]
    items = [i for i in snap["items"]]
    course_dir = _safe_dir(course["name"])
    out = store.root / "md" / course_dir / f"{run_stamp(snap['exported_at'])}_run{run_id}"
    out.mkdir(parents=True, exist_ok=True)

    tree = build_tree(snap, attachments_for(store, snap))
    ordered = flatten(tree)
    plan = _plan_paths(items)
    by_path = {i["path"]: i for i in items if i.get("path")}

    # Images: hash-named under images/ (file set now that shas are known).
    for p, entry in plan.items():
        if entry["kind"] == "image" and p in files:
            sha = files[p]
            entry["file"] = f"images/{sha[:12]}{entry['ext']}"
            dest = out / entry["file"]
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(store.blob_path(sha), dest)
        elif entry["kind"] == "other" and p in files:
            dest = out / entry["file"]
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(store.blob_path(files[p]), dest)

    def resolve(href: str, from_export_dir: str) -> str | None:
        """Export-relative href from a document -> tree-relative target (encoded) or None."""
        if not href or re.match(r"^[a-z][a-z0-9+.-]*:", href, re.I) or href.startswith("#"):
            return None
        # Export hrefs are raw file names (may contain spaces and '#'), so try the
        # literal path first and only then the URL-parsed one.
        for clean in (unquote(href), unquote(urlsplit(href).path)):
            export_path = posixpath.normpath(posixpath.join(from_export_dir or ".", clean))
            entry = plan.get(export_path)
            if entry:
                return entry.get("md") or entry.get("file")
        return None

    written: dict[str, str] = {}   # md tree path -> markdown text (with frontmatter)

    # PDFs first (their .md paths must exist for links; cheap to do in order anyway).
    for it in ordered:
        p = it.get("path")
        entry = plan.get(p) if p else None
        if not entry or p not in files:
            continue
        if entry["kind"] == "pdf":
            md_rel = render_pdf_item(store, it, files[p], out, entry["rel_dir"], caps, all_pages=all_pages)
            written[md_rel] = (out / md_rel).read_text()

    for it in ordered:
        p = it.get("path")
        entry = plan.get(p) if p else None
        if not entry or p not in files or entry["kind"] not in ("doc", "grades"):
            continue
        md_rel = entry["md"]
        md_dir = posixpath.dirname(md_rel)
        from_export_dir = posixpath.dirname(p)
        raw = store.read_blob(files[p]).decode("utf-8", "replace")
        meta = {k: v for k, v in (it.get("meta") or {}).items() if k != "items"}
        fields = {"type": it["type"], "canvas_id": str(it["key"]), "title": it.get("title") or "",
                  "source": source_url(course["domain"], course["id"], it), "run": run_id,
                  "exported_at": snap["exported_at"], **meta}
        if entry["kind"] == "grades":
            body = _grades_md(raw)
            fields["type"] = "grades"
        else:
            def link_map(href, _d=md_dir, _e=from_export_dir):
                t = resolve(href, _e)
                return _encode_rel(_rel(_d, t)) if t else None

            def image_map(src, _d=md_dir, _e=from_export_dir):
                t = resolve(src, _e)
                return _encode_rel(_rel(_d, t)) if t else None
            body = html_to_markdown(raw, link_map=link_map, image_map=image_map)
        text = frontmatter(fields) + f"# {it.get('title') or posixpath.basename(p)}\n\n{body}\n"
        dest = out / md_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
        written[md_rel] = text

    # index.md
    idx = [f"# {course['name']}\n", f"Exported {snap['exported_at'][:16].replace('T', ' ')} (run {run_id}). "
           f"Source: {course['domain']}/courses/{course['id']}\n"]
    modules = [n for n in tree if n.kind == "module"]
    front = [n for n in tree if n.kind == "item"]
    if front:
        idx.append("")
        for n in front:
            idx.extend(_item_lines(n, plan))
    if modules:
        idx.append("\n## Modules\n")
        for m in modules:
            idx.append(f"\n### {m.item['title']}\n")
            for c in m.children:
                idx.extend(_item_lines(c, plan))
    for g in (n for n in tree if n.kind == "group"):
        idx.append(f"\n## {g.item['title']}" + (" (not in a module)" if modules else "") + "\n")
        for c in g.children:
            idx.extend(_item_lines(c, plan))
    (out / "index.md").write_text("\n".join(idx).rstrip() + "\n")

    # _full.md: everything in reading order, links rebased to the tree root.
    full = [f"# {course['name']} — full export\n",
            f"Exported {snap['exported_at'][:16].replace('T', ' ')}, run {run_id}, {len(ordered)} items. "
            f"Source: {course['domain']}/courses/{course['id']}\n", "\n## Contents\n"]
    parts = []
    for it in ordered:
        p = it.get("path")
        entry = plan.get(p) if p else None
        md_rel = (entry or {}).get("md")
        if md_rel and md_rel in written:
            parts.append((it, md_rel))
            full.append(f"- [{it.get('title') or md_rel}](#{_anchor(md_rel)})")
    for it, md_rel in parts:
        body = _strip_frontmatter(written[md_rel])
        body = rebase_links(body, posixpath.dirname(md_rel))
        full.append(f"\n---\n\n<!-- file: {md_rel} -->\n<a id=\"{_anchor(md_rel)}\"></a>\n\n{body.rstrip()}\n")
    (out / "_full.md").write_text("\n".join(full).rstrip() + "\n")

    _point_latest(store.root / "md" / course_dir, out.name)
    return out


def _anchor(path: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-")


def _point_latest(parent: Path, target_name: str) -> None:
    link = parent / "latest"
    try:
        if link.is_symlink() or link.exists():
            if link.is_dir() and not link.is_symlink():
                shutil.rmtree(link)
            else:
                link.unlink()
        os.symlink(target_name, link)
    except OSError:
        (parent / "latest.txt").write_text(target_name + "\n")
