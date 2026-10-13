"""Render stage: Markdown tree for LLMs and a PDF book per course run."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..store import Store
from . import pdftools


@dataclass
class RenderOptions:
    md: bool = True
    book: bool = True
    all_pages: bool = False


@dataclass
class RenderResult:
    md_dir: Path | None = None
    book_path: Path | None = None
    warnings: list[str] = field(default_factory=list)


def load_options(store: Store) -> RenderOptions:
    cfg = store.root / "config.json"
    opts = RenderOptions()
    if cfg.exists():
        try:
            r = (json.loads(cfg.read_text()) or {}).get("render") or {}
            opts.md = bool(r.get("md", opts.md))
            opts.book = bool(r.get("book", opts.book))
            opts.all_pages = bool(r.get("all_pages", opts.all_pages))
        except (ValueError, AttributeError):
            pass
    return opts


def render_run(store: Store, run_id: int, opts: RenderOptions | None = None) -> RenderResult:
    """Never raises for a missing optional tool; each disabled feature adds a warning."""
    opts = opts or load_options(store)
    caps = pdftools.detect()
    res = RenderResult()
    if opts.md:
        from .md_out import render_markdown
        res.md_dir = render_markdown(store, run_id, caps, all_pages=opts.all_pages)
        if not caps.pdftotext:
            res.warnings.append("pdftotext missing: PDF text not extracted")
    if opts.book:
        if not caps.chromium:
            res.warnings.append("chromium missing: PDF book not built")
        else:
            from .book import render_book
            md_dir = res.md_dir
            if md_dir is None:
                from .md_out import render_markdown
                md_dir = render_markdown(store, run_id, caps, all_pages=opts.all_pages)
            res.book_path = render_book(store, run_id, caps, md_dir)
            if res.book_path and not caps.pypdf:
                res.warnings.append("pypdf missing: lecture PDFs not spliced into the book")
    return res
