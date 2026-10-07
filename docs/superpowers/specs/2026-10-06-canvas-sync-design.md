# canvas-sync: versioned archive for Canvas course exports

Date: 2026-10-06
Status: approved design, v1 scope

## Purpose

Answer one question reliably: *what changed in my course since the last export?*
Keep every version of every item so any past state can be inspected or restored.
The LLM-friendly conversion and the viewer are later stages that read the same store;
they are out of scope here.

The user exports a course with the browser extension (ZIP mode). The pipeline ingests
the ZIP automatically, stores content by hash, tracks items by Canvas id, and writes a
Markdown change report plus a desktop notification. No manual commits, no messages.

## Decisions already made

- Primary job is change detection and history (not LLM analysis, not permanent
  archival polish). Those come later on top of this store.
- No external versioning system (git, restic, ostree, DVC, changedetection.io were
  evaluated and rejected: path-based identity, opaque stores, no semantic metadata
  diff, or they scrape Canvas themselves). Own store, stdlib-only Python.
- Handoff artifact is the extension's ZIP, one per export. Loose per-file downloads
  are not supported by the pipeline.
- Identity is the Canvas id from the manifest, never the filename. Filenames stay
  human-readable.
- Hashing happens in the pipeline, not the extension. The extension only emits richer
  metadata.
- Images and other binaries are first-class items (stored, hashed, diffed by hash).

## Non-goals (v1)

- Diffing inside PDFs or other binaries (hash change only).
- HTML to Markdown conversion, PDF to text.
- Any GUI or web viewer.
- Multi-user, remote storage, encryption.
- Teacher-role data (works, but not tested or reported specially).

## Part A: extension changes

### A1. Fix ZIP folder-prefix bug

`downloadAsZip` builds `filename = "<prefix>/<course>.zip"`; the background sanitizer
turns the slash into `-`. Fix: send `path: "<prefix>/"` and `filename: "<course>.zip"`.
With `folderPrefix = CanvasExports`, ZIPs land in `~/Downloads/CanvasExports/`.

### A2. Verify ZIP mode on Firefox

The user's existing export is a loose folder although `zipMode` defaults to true.
Either the setting is off or ZIP generation failed and fell back. Confirm with a real
export before building on it. If blob URLs from the content script are refused by
Firefox `downloads.download`, route the ZIP through the background's data/blob path.

### A3. Manifest schema 2

`manifest.json` inside the ZIP becomes:

```json
{
  "schema": 2,
  "course": { "id": "133044", "name": "XLS5C202609", "domain": "https://canvas.usask.ca" },
  "exportedAt": "2026-10-06T20:31:00.000Z",
  "extensionVersion": "2.11.0",
  "role": "student",
  "complete": true,
  "counts": { "...existing counts..." : 0 },
  "items": [
    {
      "type": "assignment",
      "canvasId": "456",
      "path": "Assignments/Assignment 1.html",
      "title": "Assignment 1",
      "updatedAt": "2026-09-30T12:00:00Z",
      "meta": { "due_at": "...", "lock_at": null, "unlock_at": null,
                "points_possible": 10, "submission_types": ["online_upload"] }
    },
    { "type": "file", "canvasId": "9275770", "path": "Extracted_Files/noiseeq.png",
      "title": "noiseeq.png", "size": 1234, "updatedAt": "...",
      "meta": { "folder": "", "source": "Quiz: Topic 4 Quiz" }, "sourceCourseId": null },
    { "type": "module", "canvasId": "77", "path": null, "title": "Week 1",
      "meta": { "position": 1, "items": [ { "id": "901", "type": "Page", "title": "...", "contentId": "..." } ] } },
    { "type": "synthetic", "key": "grades.csv", "path": "Grades.csv", "title": "Grades" }
  ]
}
```

Item types and their `meta`:

| type | canvasId | meta |
|---|---|---|
| assignment | assignment id | due_at, lock_at, unlock_at, points_possible, submission_types, published |
| quiz | quiz id | due_at, points_possible, question_count, time_limit, allowed_attempts, quiz_type, assignment_id |
| page | `page_id` (slug kept in `meta.slug`; Canvas regenerates the slug on rename) | updated_at, front_page, slug |
| announcement | topic id | posted_at |
| discussion | topic id | posted_at, assignment_id |
| module | module id | position, items (ordered list of {id,type,title,contentId}) |
| file | file id | size, folder, source (where linked from), content_type |
| media | media id | title, source |
| submission | assignment id (student export) or `assignment_id:user_id` (teacher export, one doc per student) | score, grade, graded_at, submitted_at, attempt, comment_count |
| synthetic | `key` instead | none (Grades.csv, Modules.html, Syllabus.html, styles.css, _inaccessible_links.csv) |

`complete` is true when the export covered everything it was asked for: no ZIP fetch
failures (`failedPaths` lists them), no incremental-mode skips, no video/size filter
exclusions. `exportedTypes` lists the item types the content-type settings enabled
(`synthetic` only when every type is on); the pipeline declares an item removed only when
its type is in `exportedTypes` and the run is complete. Duplicate `(type, key)` pairs in a
manifest are collapsed to the first occurrence with a warning. Modules have no file of their own; their
version is the `meta` alone. `path` is relative to the ZIP root and uses the final,
de-duplicated filename.

Manifest construction happens in `downloadCourse` where `filesToDownload` entries are
created. Each entry already carries `canvasId`, `resourceType`, `resourceId`; the change
is to keep a parallel `items` array with the metadata above and emit it instead of the
count-only manifest. Keep `counts` for backward compatibility.

## Part B: store

Root defaults to `~/CanvasArchive/`, overridable by `CANVAS_SYNC_ROOT` or `--root`.

```
~/CanvasArchive/
  store.sqlite
  objects/ab/abcdef0123…           blob named by sha256 hex, no extension
  snapshots/<courseId>/<exportedAt>.json   manifest copy + {path: sha256}
  latest/<courseName>/…            hardlinks (fallback: copies) into objects
  reports/<courseId>/<exportedAt>.md
  processed/<zipname>              ingested ZIPs (config: keep | delete)
```

SQLite schema:

```sql
CREATE TABLE courses (
  id TEXT PRIMARY KEY, name TEXT, domain TEXT, first_seen TEXT, last_seen TEXT);
CREATE TABLE runs (
  id INTEGER PRIMARY KEY, course_id TEXT, exported_at TEXT, ingested_at TEXT,
  zip_sha256 TEXT UNIQUE, complete INTEGER, extension_version TEXT, counts_json TEXT);
CREATE TABLE items (
  id INTEGER PRIMARY KEY, course_id TEXT, type TEXT, key TEXT,   -- key = canvasId or synthetic key
  first_run INTEGER, last_run INTEGER, removed_run INTEGER,
  UNIQUE(course_id, type, key));
CREATE TABLE versions (
  id INTEGER PRIMARY KEY, item_id INTEGER, run_id INTEGER,
  sha256 TEXT, size INTEGER, path TEXT, title TEXT, meta_json TEXT);
CREATE TABLE blobs (sha256 TEXT PRIMARY KEY, size INTEGER, mime TEXT, first_seen TEXT);
```

A new `versions` row is inserted only when `sha256` or canonicalised `meta_json`
differs from the item's latest version. Otherwise only `items.last_run` advances.
`removed_run` is set when an item is absent from a run with `complete = 1` and cleared
if it reappears.

Blob writes are atomic: write to `objects/tmp/<uuid>`, fsync, rename, then `chmod 0444`.
Existing blob with same hash is never rewritten. `latest/` and checkouts are hardlinks
into `objects/`, so they are read-only views: editing them in place is refused by the
filesystem rather than silently corrupting history. Two courses with the same name get
`latest/<name>/` and `latest/<name> (<courseId>)/`.

## Part C: ingest

`canvas-sync ingest <zip | dir>` and `canvas-sync ingest-all` (default dir
`~/Downloads/CanvasExports/`):

1. Skip files ending in `.part`. Open ZIP; on `BadZipFile` leave it in place and report
   (Firefox may still be writing).
2. sha256 the ZIP. If present in `runs.zip_sha256`, move to `processed/` and stop.
3. Read `manifest.json`. Reject schema < 2 with a clear message pointing at the
   extension update.
4. For every item with a `path`: read bytes, sha256, write blob, record size and mime
   (from extension, stdlib `mimetypes`).
5. Insert `runs` row; upsert `courses`.
6. For every item: upsert `items`, compare against latest version, insert `versions`
   when changed, update `last_run`, clear `removed_run`.
7. For `complete` runs: items of this course not seen in this run get
   `removed_run = run.id`.
8. Write `snapshots/<course>/<exportedAt>.json`.
9. Compute change set against the previous run of the same course (if any), write
   the report, print the one-line summary, call `notify-send` if available.
10. Rebuild `latest/<courseName>/` from this run's paths (delete and relink).
11. Move the ZIP to `processed/` (or delete, per config).

Steps 5 to 8 run in one SQLite transaction committed only after the snapshot file is on
disk; a crash before that leaves orphan blobs only (`gc` removes them). If the report or
`latest/` rebuild fails after the commit, the ZIP stays in the inbox and the next ingest
of the same ZIP hash finishes those steps instead of reporting "already ingested". Items
listed in the manifest but not fetched (failed, or of a type not exported) keep their
last known file in the snapshot so `latest/` never loses a file over one bad run.

## Part D: change detection and report

Change set per item, comparing previous version to current:

- **new**: item has no earlier version.
- **removed**: `removed_run` set in this run.
- **changed**: sha or meta differs. Sub-detail:
  - meta: flat key diff, `key: old → new`. Lists (submission_types, module items)
    compared as ordered sequences; module item reorder is reported as "order changed".
  - body (HTML/Markdown docs): strip the export wrapper (`<h1>` and stylesheet link),
    convert to plain text with `html.parser`, `difflib.unified_diff`; report
    `+N −M lines` in the summary and include the diff in the report under a
    collapsible section.
  - binary (file, media): `content changed (old size → new size)`.
  - Grades.csv: parsed as rows keyed by assignment name; report per-assignment
    score changes instead of a text diff.

Report file format:

```
# XLS5C202609 — 2026-10-06 20:31 (vs 2026-10-06 14:41)

## New (1)
- assignment  Assignment 2 — due 2026-10-20, 15 pts
## Changed (2)
- assignment  Assignment 1 — due_at 2026-10-10 → 2026-10-12; body +3 −1 lines
- grades      Assignment 0 — score — → 9/10
## Removed (0)

<details><summary>Assignment 1 body diff</summary>

```diff
...
```
</details>
```

First run of a course produces a report listing everything as new, with a note that
there is no baseline.

## Part E: other commands

- `status` — courses, last run, pending ZIPs in inbox.
- `log <course> [--item TYPE:KEY]` — runs, or version history of one item.
- `diff <course> <TYPE:KEY> <runA> <runB>` — same renderer as the report, one item.
- `checkout <course> <run> <dir>` — materialise that run's tree into `<dir>`.
- `verify` — rehash every blob, report mismatches; check every version's sha has a blob.
- `gc` — delete blobs no version references.

## Part F: trigger

systemd user units in `sync/systemd/`:

- `canvas-sync.path`: `PathChanged=%h/Downloads/CanvasExports`
- `canvas-sync.service`: `ExecStart=canvas-sync ingest-all`, `Type=oneshot`

`canvas-sync install-units` copies them to `~/.config/systemd/user/` and enables the
path unit. Manual `ingest-all` always works without systemd.

## Code layout

```
sync/
  canvas_sync/
    __init__.py
    cli.py          argparse entry point
    store.py        paths, sqlite schema, blob write/read
    manifest.py     schema 2 parsing and validation
    ingest.py       steps C1–C11
    changes.py      change set computation
    report.py       Markdown rendering
    textdiff.py     HTML to text, unified diff, grades CSV diff
    materialize.py  latest/ and checkout
    verify.py       verify, gc
  tests/            unittest, synthetic ZIP fixtures built in code
  systemd/
  pyproject.toml    console script only, no dependencies
```

Python 3.11+, stdlib only. `pip install -e sync/` or run as `python -m canvas_sync`.

## Testing

- Unit tests build tiny ZIPs in a temp dir with hand-written schema-2 manifests:
  first run, unchanged rerun (no new versions), meta-only change, body change, binary
  change, item removed in complete run, item missing in incomplete run (not removed),
  duplicate ZIP (skipped), corrupt ZIP (left in place), schema-1 manifest (rejected).
- `verify` test: corrupt one blob on disk, expect mismatch.
- Extension side: load in Firefox, export course 133044 twice, confirm second ingest
  reports zero changes except Grades if any.

## Build order

1. A1 zip prefix fix, A2 ZIP mode check on Firefox, A3 manifest schema 2.
2. B store + C ingest + D report, with tests.
3. E commands.
4. F systemd units.

## Open risks

- Firefox may reject content-script blob URLs in `downloads.download`; A2 decides the
  route before any pipeline work depends on it.
- Canvas `updated_at` is unreliable on some endpoints; versioning never depends on it,
  only on content hash and meta.
- Runtime messages carrying the whole file list are large for big courses; the
  manifest adds metadata but not file bodies, so growth is modest.
