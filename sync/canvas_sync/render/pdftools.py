"""Optional external tools behind feature detection.

Nothing here is required: every function reports absence (None/False) instead of
raising, so a machine without poppler, Chromium or pypdf still gets the parts
of the render that do not need them.
"""
from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

_ALIASES = {
    "chromium": ["chromium-browser", "chromium", "google-chrome", "google-chrome-stable", "chrome"],
}


def find_tool(name: str) -> str | None:
    for candidate in _ALIASES.get(name, [name]):
        p = shutil.which(candidate)
        if p:
            return p
    return None


def pypdf_module():
    try:
        return importlib.import_module("pypdf")
    except ImportError:
        return None


def have_pypdf() -> bool:
    return pypdf_module() is not None


@dataclass
class Capabilities:
    pdftotext: str | None
    pdftoppm: str | None
    pdfinfo: str | None
    chromium: str | None
    pypdf: bool

    def summary(self) -> str:
        rows = [
            ("pdftotext (PDF text)", self.pdftotext or "missing"),
            ("pdftoppm (PDF page images)", self.pdftoppm or "missing"),
            ("pdfinfo", self.pdfinfo or "missing"),
            ("chromium (HTML to PDF for the book)", self.chromium or "missing"),
            ("pypdf (splice lecture PDFs, outline)", "ok" if self.pypdf else "missing"),
        ]
        return "\n".join(f"{k:<40} {v}" for k, v in rows)


def detect() -> Capabilities:
    return Capabilities(find_tool("pdftotext"), find_tool("pdftoppm"), find_tool("pdfinfo"),
                        find_tool("chromium"), have_pypdf())


def _run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def pdf_page_count(path: Path | str) -> int | None:
    mod = pypdf_module()
    if mod is not None:
        try:
            return len(mod.PdfReader(str(path)).pages)
        except Exception:
            pass
    exe = find_tool("pdfinfo")
    if exe:
        r = _run([exe, str(path)])
        if r and r.returncode == 0:
            for line in r.stdout.decode("utf-8", "replace").splitlines():
                if line.startswith("Pages:"):
                    try:
                        return int(line.split(":", 1)[1].strip())
                    except ValueError:
                        return None
    return None


def pdf_text_pages(path: Path | str) -> list[str] | None:
    """Text of every page via `pdftotext -layout`; pages are separated by form feeds."""
    exe = find_tool("pdftotext")
    if not exe:
        return None
    r = _run([exe, "-layout", "-enc", "UTF-8", str(path), "-"])
    if not r or r.returncode != 0:
        return None
    text = r.stdout.decode("utf-8", "replace")
    pages = text.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    return pages


def pdf_page_png(path: Path | str, page: int, out_png: Path, dpi: int = 110) -> bool:
    exe = find_tool("pdftoppm")
    if not exe:
        return False
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    stem = out_png.with_suffix("")
    r = _run([exe, "-f", str(page), "-l", str(page), "-png", "-r", str(dpi), "-singlefile", str(path), str(stem)])
    return bool(r and r.returncode == 0 and out_png.exists())


def html_to_pdf(html_path: Path | str, out_pdf: Path | str) -> bool:
    exe = find_tool("chromium")
    if not exe:
        return False
    html_path, out_pdf = Path(html_path).resolve(), Path(out_pdf).resolve()
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="canvas-vault-chromium-") as profile:
        r = _run([exe, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
                  f"--user-data-dir={profile}", "--no-pdf-header-footer", "--run-all-compositor-stages-before-draw",
                  "--virtual-time-budget=10000", f"--print-to-pdf={out_pdf}", html_path.as_uri()], timeout=600)
    return bool(r and r.returncode == 0 and out_pdf.exists() and out_pdf.stat().st_size > 0)
