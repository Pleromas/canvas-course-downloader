# canvas-sync render: Markdown for LLMs and a PDF book per course

Date: 2026-10-13
Status: approved design

## Purpose

Turn an ingested run in `~/CanvasArchive` into two independent artifacts per course:

1. A Markdown tree optimised for language models: clean text, stable relative links,
   images and PDF text alongside, one index, one concatenated file for single uploads.
2. A PDF book for reading, searching and uploading whole: course content in module
   order, lecture PDFs spliced in as original pages, navigable outline.

Both are derived from the store (blobs + snapshot), never from Canvas or the ZIP.
They run automatically after every ingest for that run and can be rebuilt for any past
run with `canvas-sync render`.

## Decisions

- Markdown is the primary artifact and is tuned for LLM reading; anything else it is
  good for is incidental.
- The PDF book is for humans and for "give an LLM the whole course in one file".
- No HTML viewer. Canvas already is one.
- Dependencies, all already on the owner's machine and optional at runtime:
  `pypdf` (page splicing, outline), poppler `pdftotext` and `pdftoppm` (PDF text and
  page images), Chromium or Chrome headless (`--print-to-pdf`). Each is feature-detected;
  a missing tool disables that feature with a clear log line and never fails the ingest.
- HTML to Markdown is an own stdlib converter (`html.parser`), no Turndown port, no
  third-party package.
- Slide PDFs: text for every page; page PNGs only for pages with little text (figure
  slides) by default, every page with `--all-pages`.
- Module order drives both outputs; unreferenced items follow grouped by type.
- One output per run, stamped; `latest` symlink points at the newest.

## Non-goals

HTML viewer, editing, re-upload, OCR of scanned PDFs, Office document conversion
(.docx/.pptx are copied as-is and listed), video transcription.

## Inputs

For a run: `runs.snapshot_path` JSON (`files`: path → sha, `items`: type/key/path/
title/meta, `course`), blobs via `Store.blob_path`, and the export's `manifest.json` blob
for `exportedTypes` and module membership (`items` of type `module` carry
`meta.items` with ids, types, titles and content ids).

## Markdown output

Root: `~/CanvasArchive/md/<course_dir>/<stamp>_run<id>/`, plus `md/<course_dir>/latest`
symlink (copy on filesystems without symlinks).

Layout mirrors the export, extension changes to `.md`:

```
index.md
_full.md
Assignments/Assignment 1.md
Quizzes/Topic 4 Quiz.md
Announcements/CSSS Event.md
Modules.md                      (module tree with links, same as index section)
Syllabus.md
Grades.md                       (table from Grades.csv)
Files/01-Representation.pdf.md  (text per page)  + Files/01-Representation.pdf (copy)
images/<sha12>.png              (embedded images, hash-named)
pages/<sha12>/p003.png          (rendered PDF pages, when produced)
```

`index.md`:

```
# XLS5C202609
Exported 2026-10-13 02:54 (run 4). Source: https://canvas.usask.ca/courses/133044

## Modules
### Week 1: Introduction
- [Topic 0: Introduction](Assignments/Topic 0- Introduction.md) — assignment, due 2026-09-03
- [00-Introduction.pdf](Files/00-Introduction.pdf.md) — file, 18.3 MB
...
## Assignments (not in a module)
...
## Quizzes
...
```

Per-item file: YAML frontmatter then `# Title` then body.

```
---
type: assignment
canvas_id: "456"
title: Assignment 1
due_at: 2026-10-03T06:59:00Z
points_possible: 43
source: https://canvas.usask.ca/courses/133044/assignments/456
run: 4
exported_at: 2026-10-13T02:54:12Z
---
```

Body rules (converter):
- Headings h1–h6 → `#`..`######` (document h1 is reserved for the title; body h1 demoted).
- `p`, `br`, `div` → paragraphs; `ul`/`ol` nested lists with `-` / `1.`; `blockquote`.
- `table` → GFM pipe table when rectangular, else list of rows.
- `pre`/`code` → fenced / inline. `strong`/`b`, `em`/`i`, `a`, `img`, `hr`.
- Links to exported items become relative `.md` links; links to exported files become
  relative links to the copied file; everything else keeps its URL.
- Images that are exported files are copied to `images/` and referenced inline with
  alt text; remote images keep their URL.
- `iframe`, `video`, `audio` → a link line "[Video: title](src)".
- Scripts, styles, hidden elements dropped. Whitespace normalised. No HTML left over
  except when a construct has no Markdown form (then a one-line note).

Quizzes: the exported quiz HTML already contains question text, answers and marks
(`(correct)`/`(incorrect)`, selected markers). Converter keeps them; marks render as
`✅` / `❌` prefixes and selected-but-unknown as `▶`.

Grades: `Grades.csv` → `Grades.md` pipe table.

PDFs: for every `file` item with `.pdf`:
- Copy the original next to the `.md`.
- `pdftotext -layout` per page → `## Page N` sections. Empty text pages noted.
- Page image policy: `pdftoppm -png -r 110` for pages whose extracted text has fewer
  than 40 words (figure slides), or all pages with `--all-pages`. Images referenced
  under the page heading: `![Page 3](../pages/<sha12>/p003.png)`.
- If poppler is missing: `.md` says so and only the copy is produced.

`_full.md`: cover block, table of contents (anchors), then every item file's body in
index order with `---` separators and `<!-- file: path -->` markers. PDF page images are
referenced, not inlined.

## PDF book

Path: `~/CanvasArchive/books/<course_dir>/<stamp>_run<id>.pdf`, plus `latest.pdf`
symlink.

Build:
1. Render the Markdown items (same order as `_full.md`) to one HTML document with print
   CSS (page breaks before each item, running header with course name, page numbers,
   fonts system sans). Images embedded via `file://` paths.
2. Chromium/Chrome headless: `--headless --disable-gpu --no-pdf-header-footer
   --print-to-pdf=<tmp> file://<html>`. If neither binary exists: book skipped, log line.
3. Splice: with `pypdf`, walk the item order; where a `file` item is a PDF, insert the
   original pages at that position (the rendered part for that item is just its heading
   page). Non-PDF files get a one-page stub listing name, size, type.
4. Outline: course → module → item bookmarks pointing at the first page of each part.
   Printed TOC on page 2 with page numbers computed from the splice.
5. Cover: course name, export date, run, counts (items, PDFs, pages).

If `pypdf` is missing: the Chromium PDF is emitted without spliced lecture PDFs and
without outline; log line says so.

## CLI

- `canvas-sync render <course> [--run N] [--all-pages] [--md-only|--book-only]`
- `canvas-sync ingest` gains `--no-render`; default renders after each successful ingest
  (both outputs) and prints both paths in the summary.
- Config file `~/CanvasArchive/config.json` (optional): `{"render": {"all_pages": false,
  "book": true, "md": true}}`.

## Code layout

```
sync/canvas_sync/render/
  __init__.py       render_run(store, run_id, opts) -> RenderResult
  tree.py           ordered item tree from snapshot + manifest modules
  htmlmd.py         HTML -> Markdown converter (stdlib)
  pdftools.py       pdftotext/pdftoppm/chromium/pypdf detection and wrappers
  md_out.py         Markdown tree + index + _full.md
  book.py           HTML for print, Chromium print, pypdf splice + outline
  tools_check.py    `canvas-sync doctor` reports which optional tools are present
```

## Testing

- `htmlmd.py`: unit tests on fixtures (headings, nested lists, tables, code, links,
  images, quiz markup, junk removal).
- `tree.py`: ordering with modules, unreferenced items, missing types.
- `md_out.py`: end-to-end on a synthetic run in a temp store (uses test helpers from
  the ingest suite): files exist, frontmatter correct, links relative and resolvable,
  `_full.md` order.
- `pdftools.py`/`book.py`: unit tests with the external tools mocked out; one
  integration test that runs only when `pdftotext`, `pdftoppm`, Chromium and `pypdf`
  are present (skips otherwise), asserting page counts and outline titles.
- Real check: render run 6 of course 133044; open the book, confirm outline and that
  `00-Introduction.pdf` pages are the originals; open `_full.md` size and spot-check a
  quiz.
