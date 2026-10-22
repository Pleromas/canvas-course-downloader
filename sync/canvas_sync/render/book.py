"""PDF book per run: the Markdown parts printed by Chromium, lecture PDFs spliced
in as original pages, outline and printed table of contents (pypdf)."""
from __future__ import annotations

import html as htmlmod
import json
import posixpath
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import unquote

from ..materialize import _safe_dir
from ..store import Store
from . import pdftools
from .md_out import _strip_frontmatter, run_stamp
from .pdftools import Capabilities
from .tree import build_tree

MARK = "\u27e6PART {n}\u27e7"   # printed in near-invisible text so pypdf can find part starts
_MARK_RE = re.compile(r"\u27e6\s*PART\s*(\d+)\s*\u27e7")  # extractors may pad with spaces

CSS = """
@page { size: Letter; margin: 18mm 16mm 20mm 16mm; }
html { font-family: -apple-system, "Segoe UI", Roboto, "Noto Sans", "DejaVu Sans", sans-serif; font-size: 11pt; line-height: 1.45; color: #1f2937; }
body { margin: 0; }
h1 { font-size: 22pt; margin: 0 0 10pt; }
h2 { font-size: 16pt; margin: 18pt 0 6pt; border-bottom: 1px solid #e5e7eb; padding-bottom: 3pt; }
h3 { font-size: 13pt; margin: 14pt 0 4pt; }
p { margin: 0 0 7pt; }
ul, ol { margin: 0 0 7pt 1.2em; padding: 0; }
li { margin: 2pt 0; }
code { font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace; font-size: 9.5pt; background: #f3f4f6; padding: 1px 3px; border-radius: 3px; }
pre { background: #f3f4f6; padding: 8pt; border-radius: 4px; white-space: pre-wrap; word-break: break-word; font-size: 9pt; }
pre code { background: none; padding: 0; }
table { border-collapse: collapse; margin: 6pt 0 10pt; font-size: 9.5pt; }
th, td { border: 1px solid #d1d5db; padding: 3pt 6pt; vertical-align: top; }
th { background: #f3f4f6; text-align: left; }
img { max-width: 100%; max-height: 180mm; }
blockquote { border-left: 3px solid #d1d5db; margin: 0 0 7pt; padding: 2pt 10pt; color: #4b5563; }
hr { border: 0; border-top: 1px solid #e5e7eb; margin: 10pt 0; }
a { color: #1d4ed8; text-decoration: none; }
.part { page-break-before: always; }
.mark { color: #ffffff; font-size: 2pt; line-height: 2pt; margin: 0; }
.cover { display: flex; flex-direction: column; justify-content: center; height: 200mm; }
.cover h1 { font-size: 34pt; margin-bottom: 6pt; }
.cover .sub { font-size: 13pt; color: #4b5563; }
.meta { color: #6b7280; font-size: 9.5pt; margin-bottom: 8pt; }
.toc li { margin: 3pt 0; display: flex; justify-content: space-between; }
.toc .mod { font-weight: 600; margin-top: 8pt; }
.toc .pg { color: #6b7280; }
.stub { color: #4b5563; }
"""

# ---------------------------------------------------------------------------
# Markdown subset -> HTML (only the constructs md_out emits)
# ---------------------------------------------------------------------------

_INLINE_RE = re.compile(
    r"(!\[(?P<ialt>[^\]]*)\]\((?P<isrc>[^)\s]+)\))"
    r"|(\[(?P<ltext>[^\]]+)\]\((?P<lhref>[^)\s]+)\))"
    r"|(\*\*(?P<b>.+?)\*\*)"
    r"|(\*(?P<i>[^*\n]+?)\*)"
    r"|(`(?P<c>[^`\n]+)`)"
)


def _inline(text: str, src_map) -> str:
    out, pos = [], 0
    for m in _INLINE_RE.finditer(text):
        out.append(htmlmod.escape(text[pos:m.start()]))
        if m.group("isrc") is not None:
            out.append(f'<img src="{htmlmod.escape(src_map(m.group("isrc")))}" alt="{htmlmod.escape(m.group("ialt"))}">')
        elif m.group("lhref") is not None:
            href = m.group("lhref")
            target = href if re.match(r"^[a-z][a-z0-9+.-]*:", href, re.I) or href.startswith("#") else src_map(href)
            out.append(f'<a href="{htmlmod.escape(target)}">{_inline(m.group("ltext"), src_map)}</a>')
        elif m.group("b") is not None:
            out.append(f"<strong>{_inline(m.group('b'), src_map)}</strong>")
        elif m.group("i") is not None:
            out.append(f"<em>{_inline(m.group('i'), src_map)}</em>")
        else:
            out.append(f"<code>{htmlmod.escape(m.group('c'))}</code>")
        pos = m.end()
    out.append(htmlmod.escape(text[pos:]))
    return "".join(out)


def md_to_html(md: str, src_map=lambda t: t) -> str:
    lines = md.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)

    def para_lines(buf):
        if buf:
            out.append("<p>" + "<br>".join(_inline(l, src_map) for l in buf) + "</p>")
            buf.clear()

    buf: list[str] = []
    while i < n:
        line = lines[i]
        s = line.strip()
        if not s:
            para_lines(buf); i += 1; continue
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            para_lines(buf)
            out.append(f"<h{len(m.group(1))}>{_inline(m.group(2), src_map)}</h{len(m.group(1))}>")
            i += 1; continue
        if s.startswith("```"):
            para_lines(buf)
            j = i + 1
            code = []
            while j < n and not lines[j].strip().startswith("```"):
                code.append(lines[j]); j += 1
            out.append("<pre><code>" + htmlmod.escape("\n".join(code)) + "</code></pre>")
            i = j + 1; continue
        if s in ("---", "***"):
            para_lines(buf); out.append("<hr>"); i += 1; continue
        if s.startswith(">"):
            para_lines(buf)
            q = []
            while i < n and lines[i].strip().startswith(">"):
                q.append(lines[i].strip()[1:].strip()); i += 1
            out.append("<blockquote><p>" + "\n".join(_inline(x, src_map) for x in q) + "</p></blockquote>")
            continue
        if s.startswith("|"):
            para_lines(buf)
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")]); i += 1
            if len(rows) >= 2 and all(re.fullmatch(r":?-+:?", c) for c in rows[1]):
                head, body = rows[0], rows[2:]
            else:
                head, body = None, rows
            t = ["<table>"]
            if head is not None:
                t.append("<tr>" + "".join(f"<th>{_inline(c, src_map)}</th>" for c in head) + "</tr>")
            for r in body:
                t.append("<tr>" + "".join(f"<td>{_inline(c, src_map)}</td>" for c in r) + "</tr>")
            t.append("</table>")
            out.append("".join(t)); continue
        if re.match(r"^(\s*)([-*]|\d+\.)\s+", line):
            para_lines(buf)
            i = _list(lines, i, out, src_map)
            continue
        if s.startswith("<!--") or s.startswith("<a id="):
            i += 1; continue
        buf.append(s); i += 1
    para_lines(buf)
    return "\n".join(out)


def _list(lines, i, out, src_map):
    """Parse a (possibly nested) list starting at line i; returns next index."""
    item_re = re.compile(r"^(\s*)([-*]|\d+\.)\s+(.*)$")
    stack: list[tuple[int, str]] = []  # (indent, tag)
    n = len(lines)
    html: list[str] = []
    open_li = [False]

    def close_to(indent):
        while stack and stack[-1][0] > indent:
            html.append("</li></" + stack.pop()[1] + ">")
            open_li[0] = True

    while i < n:
        m = item_re.match(lines[i])
        if not m:
            break
        indent = len(m.group(1).replace("\t", "    "))
        tag = "ol" if m.group(2)[0].isdigit() else "ul"
        if not stack or indent > stack[-1][0]:
            stack.append((indent, tag)); html.append(f"<{tag}>")
        else:
            close_to(indent)
            html.append("</li>")
        html.append("<li>" + _inline(m.group(3), src_map))
        i += 1
    while stack:
        html.append("</li></" + stack.pop()[1] + ">")
    out.append("".join(html))
    return i


# ---------------------------------------------------------------------------
# book assembly
# ---------------------------------------------------------------------------

def _outline_titles(reader) -> list[str]:
    titles = []

    def walk(items):
        for it in items:
            if isinstance(it, list):
                walk(it)
            else:
                titles.append(str(it.title))
    try:
        walk(reader.outline)
    except Exception:
        pass
    return titles


def _parts(store: Store, run_id: int, md_dir: Path):
    """Reading order as (kind, item, md_rel|None, pdf_rel|None, module_title|None)."""
    row = store.db.execute("select snapshot_path from runs where id=?", (run_id,)).fetchone()
    snap = json.loads(Path(row["snapshot_path"]).read_text())
    from .md_out import _plan_paths
    plan = _plan_paths(snap["items"])
    parts = []

    def add(node, module_title):
        it = node.item
        p = it.get("path")
        entry = plan.get(p) if p else None
        if it["type"] == "external":
            parts.append(("external", it, None, None, module_title))
        elif entry and entry["kind"] == "pdf" and (md_dir / entry["file"]).exists():
            parts.append(("pdf", it, entry["md"], entry["file"], module_title))
        elif entry and entry.get("md") and (md_dir / entry["md"]).exists():
            parts.append(("doc", it, entry["md"], None, module_title))
        elif entry and entry.get("file"):
            parts.append(("file", it, None, entry["file"], module_title))

    from .md_out import attachments_for

    def add_tree(node, module_title):
        if (node.item.get("path") or "").lower() == "modules.html":
            return  # the printed contents page replaces the module index
        add(node, module_title)
        for c in node.children:
            add_tree(c, module_title)

    for node in build_tree(snap, attachments_for(store, snap)):
        if node.kind in ("module", "group"):
            parts.append(("section", node.item, None, None, None))
            for c in node.children:
                add_tree(c, node.item["title"])
        else:
            add_tree(node, None)
    return snap, parts


def _build_html(snap, parts, md_dir: Path, run_id: int) -> str:
    course = snap["course"]
    n_items = sum(1 for p in parts if p[0] != "section")
    n_pdf = sum(1 for p in parts if p[0] == "pdf")
    h = ["<!doctype html><html><head><meta charset='utf-8'><style>", CSS, "</style></head><body>"]
    h.append(f"<div class='cover'><h1>{htmlmod.escape(course['name'])}</h1>"
             f"<div class='sub'>Course export, {htmlmod.escape(snap['exported_at'][:16].replace('T', ' '))} (run {run_id})</div>"
             f"<div class='sub'>{n_items} items, {n_pdf} PDF documents · {htmlmod.escape(course['domain'])}/courses/{htmlmod.escape(course['id'])}</div></div>")
    h.append("<div class='toc-placeholder'></div>")
    idx = 0
    for kind, it, md_rel, file_rel, module_title in parts:
        if kind == "section":
            idx += 1
            h.append(f"<section class='part'><p class='mark'>{MARK.format(n=idx)}</p><h1>{htmlmod.escape(it['title'])}</h1></section>")
            continue
        idx += 1
        title = htmlmod.escape(it.get("title") or (md_rel or file_rel or ""))
        h.append(f"<section class='part'><p class='mark'>{MARK.format(n=idx)}</p>")
        meta = it.get("meta") or {}
        bits = [it["type"]]
        if meta.get("due_at"):
            bits.append(f"due {str(meta['due_at'])[:10]}")
        if meta.get("points_possible") is not None:
            bits.append(f"{meta['points_possible']} pts")
        if module_title:
            bits.append(f"module: {module_title}")
        if kind == "doc":
            text = _strip_frontmatter((md_dir / md_rel).read_text())
            base = md_dir / posixpath.dirname(md_rel)
            def src_map(t, _b=base):
                return (_b / unquote(t)).resolve().as_uri()
            body = md_to_html(text, src_map)
            h.append(f"<div class='meta'>{htmlmod.escape(' · '.join(bits))}</div>{body}")
        elif kind == "pdf":
            count = pdftools.pdf_page_count(md_dir / file_rel) or "?"
            h.append(f"<h1>{title}</h1><div class='meta'>{htmlmod.escape(' · '.join(bits))}</div>"
                     f"<p class='stub'>Original PDF, {count} pages, follows on the next page.</p>")
        elif kind == "file":
            size = it.get("size")
            h.append(f"<h1>{title}</h1><div class='meta'>{htmlmod.escape(' · '.join(bits))}</div>"
                     f"<p class='stub'>File not rendered in the book: {htmlmod.escape(file_rel)}"
                     f"{f' ({size} bytes)' if size else ''}. Available in the Markdown tree.</p>")
        else:
            h.append(f"<h1>{title}</h1><p class='stub'>External module item ({htmlmod.escape(str(meta.get('module_item_type') or ''))}).</p>")
        h.append("</section>")
    h.append("</body></html>")
    return "\n".join(h)


def _toc_html(entries) -> str:
    h = ["<!doctype html><html><head><meta charset='utf-8'><style>", CSS, "</style></head><body><h1>Contents</h1><ul class='toc'>"]
    for title, page, is_section in entries:
        cls = " class='mod'" if is_section else ""
        h.append(f"<li{cls}><span>{htmlmod.escape(title)}</span><span class='pg'>{page}</span></li>")
    h.append("</ul></body></html>")
    return "\n".join(h)


def render_book(store: Store, run_id: int, caps: Capabilities, md_dir: Path) -> Path | None:
    if not caps.chromium:
        return None
    snap, parts = _parts(store, run_id, md_dir)
    course_dir = _safe_dir(snap["course"]["name"])
    out_dir = store.root / "books" / course_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{run_stamp(snap['exported_at'])}_run{run_id}.pdf"

    with tempfile.TemporaryDirectory(prefix="canvas-vault-book-") as tmp:
        tmp = Path(tmp)
        html_path = tmp / "book.html"
        html_path.write_text(_build_html(snap, parts, md_dir, run_id))
        printed = tmp / "printed.pdf"
        if not pdftools.html_to_pdf(html_path, printed):
            return None
        if not caps.pypdf:
            shutil.copyfile(printed, out)
            _latest(out_dir, out)
            return out
        _assemble(parts, md_dir, printed, out, tmp, snap)
    _latest(out_dir, out)
    return out


def _link_target_page(reader, annot) -> int | None:
    """0-based page index a GoTo link annotation points at (for tests/tools)."""
    try:
        dest = annot["/Dest"] if "/Dest" in annot else None
        if dest is None:
            a = annot.get("/A")
            if a is not None and a.get_object().get("/S") == "/GoTo":
                dest = a.get_object().get("/D")
        if dest is None:
            return None
        page_ref = dest[0] if isinstance(dest, list) or hasattr(dest, "__getitem__") else dest
        return reader._get_page_number_by_indirect(page_ref)
    except Exception:
        return None


def _bbox_lines(pdf_path: Path) -> list[list[tuple[float, float, float, float, str]]]:
    """Per page, text lines as (xMin, yMin, xMax, yMax, text) with a top-left
    origin in points, from `pdftotext -bbox-layout`. Empty when poppler is
    missing. Used to place link rectangles over contents lines: Chromium's own
    text matrices are not reliable to read back, poppler's boxes are."""
    exe = pdftools.find_tool("pdftotext")
    if not exe:
        return []
    r = pdftools._run([exe, "-bbox-layout", str(pdf_path), "-"])
    if not r or r.returncode != 0:
        return []
    xml = r.stdout.decode("utf-8", "replace")
    pages: list[list[tuple[float, float, float, float, str]]] = []
    for page_xml in re.findall(r"<page\b.*?</page>", xml, re.S):
        lines = []
        for m in re.finditer(r'<line xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</line>', page_xml, re.S):
            words = re.findall(r">([^<]*)</word>", m.group(5))
            text = htmlmod.unescape(" ".join(w.strip() for w in words if w.strip()))
            lines.append((float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4)), text))
        pages.append(lines)
    return pages


def _add_toc_links(writer, toc_pages: list[int], toc_pdf: Path, entries: list[tuple[str, int, bool]]) -> int:
    """Overlay a GoTo link on every contents line, matched by title text in
    order. The link spans the printable width so the page number is clickable
    too. Returns the number of links added (0 without poppler)."""
    from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject, RectangleObject
    boxes = _bbox_lines(toc_pdf)
    if not boxes:
        return 0
    remaining = list(entries)
    added = 0
    for n, pi in enumerate(toc_pages):
        if n >= len(boxes):
            break
        page = writer.pages[pi]
        width, height = float(page.mediabox.width), float(page.mediabox.height)
        # Group boxes into rows by yMin (title at the left, page number at the right).
        rows: dict[int, list] = {}
        for x0, y0, x1, y1, text in boxes[n]:
            rows.setdefault(round(y0), []).append((x0, y0, x1, y1, text))
        for _, row in sorted(rows.items()):
            row.sort()
            line_text = " ".join(t for *_, t in row).strip()
            if not remaining or not line_text:
                continue
            title = " ".join(remaining[0][0].split())
            if line_text.startswith(title) or (len(title) > 12 and line_text.startswith(title[:12])):
                _, page_no, _ = remaining.pop(0)
                y0 = min(b[1] for b in row); y1 = max(b[3] for b in row)
                rect = (36.0, height - y1 - 2.0, width - 36.0, height - y0 + 2.0)
                target = writer.pages[min(max(page_no - 1, 0), len(writer.pages) - 1)]
                # Explicit page reference in /Dest: pypdf's Link(target_page_index=)
                # leaves a bare integer there, which pdf.js tolerates but other
                # viewers and pypdf itself do not.
                annot = DictionaryObject({
                    NameObject("/Type"): NameObject("/Annot"),
                    NameObject("/Subtype"): NameObject("/Link"),
                    NameObject("/Rect"): RectangleObject(rect),
                    NameObject("/Border"): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(0)]),
                    NameObject("/Dest"): ArrayObject([target.indirect_reference, NameObject("/Fit")]),
                })
                writer.add_annotation(page_number=pi, annotation=annot)
                added += 1
    return added


def _assemble(parts, md_dir: Path, printed: Path, out: Path, tmp: Path, snap) -> None:
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(str(printed))
    starts: dict[int, int] = {}
    for pi, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        for m in _MARK_RE.finditer(text):
            starts.setdefault(int(m.group(1)), pi)
    n_parts = len(parts)
    bounds = []
    for idx in range(1, n_parts + 1):
        s = starts.get(idx)
        e = next((starts[j] for j in range(idx + 1, n_parts + 1) if j in starts), len(reader.pages))
        bounds.append((s, e))
    cover_end = starts.get(1, len(reader.pages))

    # Pass 1: compute final page numbers to print the TOC.
    sequence = []  # (kind, item, printed_range or None, spliced_reader or None, title, is_section)
    page_no = cover_end + 1  # pages after the cover; TOC length added below
    toc_entries = []
    plan_rows = []
    for (kind, it, md_rel, file_rel, module_title), (s, e) in zip(parts, bounds):
        extra = None
        if kind == "pdf":
            try:
                extra = PdfReader(str(md_dir / file_rel))
            except Exception:
                extra = None
        plan_rows.append((kind, it, s, e, extra))

    toc_len_guess = 1
    def layout(toc_len):
        rows, entries, p = [], [], cover_end + toc_len + 1
        for kind, it, s, e, extra in plan_rows:
            if s is None:
                continue
            title = it.get("title") or ""
            entries.append((title, p, kind == "section"))
            rows.append((kind, it, s, e, extra, p))
            p += (e - s) + (len(extra.pages) if extra is not None else 0)
        return rows, entries

    rows, entries = layout(toc_len_guess)
    toc_pdf = tmp / "toc.pdf"
    (tmp / "toc.html").write_text(_toc_html(entries))
    toc_len = 0
    if pdftools.html_to_pdf(tmp / "toc.html", toc_pdf):
        toc_len = len(PdfReader(str(toc_pdf)).pages)
        if toc_len != toc_len_guess:
            rows, entries = layout(toc_len)
            (tmp / "toc.html").write_text(_toc_html(entries))
            pdftools.html_to_pdf(tmp / "toc.html", toc_pdf)

    writer = PdfWriter()
    for pi in range(cover_end):
        writer.add_page(reader.pages[pi])
    toc_pages = []
    if toc_len:
        for pg in PdfReader(str(toc_pdf)).pages:
            toc_pages.append(len(writer.pages))
            writer.add_page(pg)
    section_node = None
    for kind, it, s, e, extra, p in rows:
        first = len(writer.pages)
        for pi in range(s, e):
            writer.add_page(reader.pages[pi])
        if extra is not None:
            for pg in extra.pages:
                writer.add_page(pg)
        title = it.get("title") or ""
        if kind == "section":
            section_node = writer.add_outline_item(title, first)
        else:
            writer.add_outline_item(title, first, parent=section_node)
    if toc_pages:
        _add_toc_links(writer, toc_pages, toc_pdf, entries)
    writer.add_metadata({"/Title": f"{snap['course']['name']} — course export", "/Producer": "Canvas Vault"})
    with open(out, "wb") as fh:
        writer.write(fh)


def _latest(out_dir: Path, out: Path) -> None:
    link = out_dir / "latest.pdf"
    try:
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(out.name)
    except OSError:
        shutil.copyfile(out, link)
