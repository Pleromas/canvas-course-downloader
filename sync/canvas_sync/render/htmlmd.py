"""Exported Canvas HTML to Markdown, stdlib only.

Scope is the HTML the extension emits (cleaned Canvas RCE content plus the
export wrapper), not the open web. The parser builds a small tree, then block
and inline emitters produce Markdown with no leftover tags; a construct with no
Markdown form becomes a one-line link or is dropped.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Callable

VOID = {"br", "img", "hr", "meta", "link", "input", "source", "wbr", "col", "area", "base", "embed", "track"}
DROP = {"script", "style", "noscript", "head", "title", "link", "meta", "template", "svg", "button", "input", "select", "textarea", "form", "nav"}
BLOCK = {"p", "div", "section", "article", "main", "header", "footer", "aside", "figure", "figcaption",
         "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "blockquote", "pre", "table", "thead", "tbody",
         "tfoot", "tr", "td", "th", "hr", "iframe", "video", "audio", "details", "summary", "dl", "dt", "dd", "center"}
MEDIA = {"iframe", "video", "audio", "embed", "object"}

_WS = re.compile(r"[ \t\r\n\f\v ]+")


class Text:
    __slots__ = ("text", "raw")

    def __init__(self, text, raw=False):
        self.text, self.raw = text, raw


class El:
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag, attrs):
        self.tag, self.attrs, self.children = tag, dict(attrs), []


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = El("root", {})
        self.stack = [self.root]
        self.pre = 0

    def handle_starttag(self, tag, attrs):
        el = El(tag, attrs)
        self.stack[-1].children.append(el)
        if tag in VOID:
            return
        if tag == "pre":
            self.pre += 1
        self.stack.append(el)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(El(tag, attrs))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                if tag == "pre":
                    self.pre = max(0, self.pre - 1)
                del self.stack[i:]
                return

    def handle_data(self, data):
        if data:
            self.stack[-1].children.append(Text(data, raw=self.pre > 0))


def _find(el, tag):
    for c in el.children:
        if isinstance(c, El):
            if c.tag == tag:
                return c
            found = _find(c, tag)
            if found is not None:
                return found
    return None


def _plain(node) -> str:
    if isinstance(node, Text):
        return node.text
    return "".join(_plain(c) for c in node.children)


def _norm(s: str) -> str:
    return _WS.sub(" ", s)


class _Conv:
    def __init__(self, link_map, image_map):
        self.link_map = link_map or (lambda h: None)
        self.image_map = image_map or (lambda s: None)

    # ---- inline -----------------------------------------------------------
    def inline(self, nodes) -> str:
        out = []
        for n in nodes:
            if isinstance(n, Text):
                out.append(n.text if n.raw else _norm(n.text))
                continue
            t = n.tag
            if t in DROP:
                continue
            if t == "br":
                out.append("\n")
            elif t in ("strong", "b"):
                inner = self.inline(n.children).strip()
                out.append(f"**{inner}**" if inner else "")
            elif t in ("em", "i"):
                inner = self.inline(n.children).strip()
                out.append(f"*{inner}*" if inner else "")
            elif t == "code":
                inner = _plain(n).replace("\n", " ").strip()
                out.append(f"`{inner}`" if inner else "")
            elif t == "a":
                inner = self.inline(n.children).strip()
                href = n.attrs.get("href") or ""
                target = self.link_map(href) or href
                if not inner:
                    inner = target
                out.append(f"[{inner}]({target})" if target else inner)
            elif t == "img":
                src = n.attrs.get("src") or ""
                target = self.image_map(src) or src
                alt = _norm(n.attrs.get("alt") or n.attrs.get("title") or "").strip() or "image"
                out.append(f"![{alt}]({target})" if target else "")
            elif t in ("s", "del", "strike"):
                inner = self.inline(n.children).strip()
                out.append(f"~~{inner}~~" if inner else "")
            elif t in BLOCK:
                # Block inside an inline run (e.g. <p> inside <li>): flatten.
                out.append("\n" + "\n\n".join(self.blocks([n])) + "\n")
            else:
                out.append(self.inline(n.children))
        return "".join(out)

    def para(self, nodes) -> str:
        text = self.inline(nodes)
        lines = [l.strip() for l in text.split("\n")]
        text = "\n".join(l for l in lines if l)
        return text.strip()

    # ---- blocks -------------------------------------------------------------
    def blocks(self, nodes) -> list[str]:
        out: list[str] = []
        run: list = []

        def flush():
            if run:
                p = self.para(run)
                if p:
                    out.append(p)
                run.clear()

        for n in nodes:
            if isinstance(n, Text):
                if n.text.strip():
                    run.append(n)
                continue
            t = n.tag
            if t in DROP:
                continue
            if t not in BLOCK:
                run.append(n)
                continue
            flush()
            out.extend(self.block(n))
        flush()
        return out

    def block(self, n) -> list[str]:
        t = n.tag
        if t in ("div", "section", "article", "main", "header", "footer", "aside", "figure", "details", "center", "dl"):
            return self.blocks(n.children)
        if t[0] == "h" and len(t) == 2 and t[1].isdigit():
            level = max(2, min(6, int(t[1])))
            text = self.para(n.children).replace("\n", " ")
            return [f"{'#' * level} {text}"] if text else []
        if t in ("p", "figcaption", "summary", "dt", "dd"):
            p = self.para(n.children)
            return [p] if p else []
        if t in ("ul", "ol"):
            return [self.list(n, 0)]
        if t == "blockquote":
            inner = "\n\n".join(self.blocks(n.children))
            return ["\n".join(f"> {l}" if l else ">" for l in inner.split("\n"))] if inner else []
        if t == "pre":
            code = _plain(n).strip("\n")
            return [f"```\n{code}\n```"]
        if t == "hr":
            return ["---"]
        if t == "table":
            return self.table(n)
        if t in MEDIA:
            src = n.attrs.get("src") or n.attrs.get("data") or ""
            label = _norm(n.attrs.get("title") or "").strip()
            kind = "Audio" if t == "audio" else "Video"
            text = f"{kind}: {label}" if label else kind
            return [f"[{text}]({src})"] if src else []
        if t == "li":
            return [self.list_item(n, 0, "-")]
        if t in ("thead", "tbody", "tfoot", "tr", "td", "th"):
            return self.blocks(n.children)
        return self.blocks(n.children)

    # ---- lists --------------------------------------------------------------
    def list(self, n, depth) -> str:
        ordered = n.tag == "ol"
        lines = []
        i = 0
        for c in n.children:
            if isinstance(c, El) and c.tag == "li":
                i += 1
                marker = f"{i}." if ordered else "-"
                lines.append(self.list_item(c, depth, marker))
        return "\n".join(l for l in lines if l)

    def list_item(self, li, depth, marker) -> str:
        indent = "  " * depth
        own = [c for c in li.children if not (isinstance(c, El) and c.tag in ("ul", "ol"))]
        nested = [c for c in li.children if isinstance(c, El) and c.tag in ("ul", "ol")]
        text = self.para(own).replace("\n", f"\n{indent}  ")
        text = _quiz_mark(text, li.attrs.get("style") or "")
        lines = [f"{indent}{marker} {text}".rstrip()]
        for sub in nested:
            lines.append(self.list(sub, depth + 1))
        return "\n".join(l for l in lines if l)

    # ---- tables -------------------------------------------------------------
    def table(self, n) -> list[str]:
        rows = []
        ragged = False
        header_rows = 0

        def walk(el, in_head):
            nonlocal ragged, header_rows
            for c in el.children:
                if not isinstance(c, El):
                    continue
                if c.tag == "tr":
                    cells = []
                    for cell in c.children:
                        if isinstance(cell, El) and cell.tag in ("td", "th"):
                            if cell.attrs.get("colspan") or cell.attrs.get("rowspan"):
                                ragged = True
                            cells.append(self.para(cell.children).replace("\n", " ").replace("|", "\\|"))
                    rows.append(cells)
                    if in_head or all(isinstance(x, El) and x.tag == "th" for x in c.children if isinstance(x, El)):
                        if len(rows) == 1:
                            header_rows = 1
                elif c.tag in ("thead", "tbody", "tfoot"):
                    walk(c, c.tag == "thead")
                elif c.tag != "caption":
                    walk(c, in_head)

        walk(n, False)
        rows = [r for r in rows if any(x.strip() for x in r)]
        if not rows:
            return []
        width = len(rows[0])
        if ragged or any(len(r) != width for r in rows) or width == 0:
            return ["\n".join(f"- {' | '.join(x for x in r if x.strip())}" for r in rows)]
        head = rows[0] if header_rows else [""] * width
        body = rows[1:] if header_rows else rows
        lines = ["| " + " | ".join(head) + " |", "| " + " | ".join(["---"] * width) + " |"]
        lines += ["| " + " | ".join(r) + " |" for r in body]
        return ["\n".join(lines)]


_MARK_PREFIX = {"✓ ": "✅ ", "✗ ": "❌ ", "• ": "▶ ", "◇ ": "✔ "}


def _quiz_mark(text: str, style: str) -> str:
    """Quiz answer markers from the export become emoji prefixes."""
    for old, new in _MARK_PREFIX.items():
        if text.startswith(old):
            return new + text[len(old):]
    m = re.search(r"\s*\*\((correct|incorrect)\)\*\s*$", text)
    if m:
        prefix = "✅ " if m.group(1) == "correct" else "❌ "
        return prefix + text[:m.start()].rstrip()
    if "font-weight:600" in style.replace(" ", "") and not any(text.startswith(p) for p in _MARK_PREFIX.values()):
        return "▶ " + text
    return text


def html_to_markdown(html: str, link_map: Callable[[str], str | None] | None = None,
                     image_map: Callable[[str], str | None] | None = None) -> str:
    b = _Builder()
    b.feed(html or "")
    b.close()
    root = b.root
    body = _find(root, "body") or root
    nodes = list(body.children)
    # Export wrapper: drop the leading <h1> that repeats the <title>.
    title_el = _find(root, "title")
    first = next((c for c in nodes if not (isinstance(c, Text) and not c.text.strip())), None)
    if title_el is not None and isinstance(first, El) and first.tag == "h1" \
            and _norm(_plain(first)).strip() == _norm(_plain(title_el)).strip():
        nodes.remove(first)
    conv = _Conv(link_map, image_map)
    return "\n\n".join(conv.blocks(nodes)).strip()
