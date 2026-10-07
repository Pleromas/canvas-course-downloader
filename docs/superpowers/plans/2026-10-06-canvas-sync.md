# canvas-sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest the extension's course ZIP exports into a content-addressed, Canvas-id-keyed version store and report what changed since the previous export.

**Architecture:** The browser extension (plain JS, no build) gains a schema-2 `manifest.json` listing every exported item with its Canvas id and metadata, and emits one ZIP per course into `~/Downloads/CanvasExports/`. A stdlib-only Python package `canvas_sync` ingests each ZIP: blobs by sha256 under `objects/`, metadata in SQLite, one snapshot JSON per run, a Markdown change report, a browsable `latest/` tree. A systemd user path unit triggers ingest when a ZIP lands.

**Tech Stack:** Extension: ES2020 browser JS, `node --test` for pure helpers. Pipeline: Python 3.11+, stdlib only (`sqlite3`, `zipfile`, `hashlib`, `difflib`, `html.parser`, `csv`, `json`, `argparse`, `unittest`). systemd user units. `notify-send` optional.

**Spec:** `docs/superpowers/specs/2026-10-06-canvas-sync-design.md`

## Global Constraints

- Extension stays plain JS, no bundler, no build step; Chrome compatibility kept via feature detection, never user-agent sniffing.
- Extension never writes to Canvas, adds no hosts, no telemetry.
- Pipeline: Python 3.11+, **zero third-party dependencies**, runtime and tests.
- Identity key is `(course_id, type, key)` where `key` is the Canvas id, or the synthetic key for generated files. Never the filename.
- Store root default `~/CanvasArchive/`, override by env `CANVAS_SYNC_ROOT` or `--root`.
- Manifest schema number is `2`; schema < 2 is rejected with a message naming the extension update.
- A new `versions` row only when `sha256` or canonical `meta_json` changes.
- `removed_run` set only on runs with `complete = 1`.
- All blob writes atomic: temp file in `objects/tmp/`, fsync, rename.
- Commit after every task with the Co-Authored-By trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Pipeline code lives in `sync/`; tests run with `cd sync && python -m unittest discover -s tests -v`.

## Review Focus

1. **Firefox still writing the ZIP** (`.part` file, or final name but truncated): ingest must skip `.part`, leave an unreadable ZIP in place without recording anything, and succeed on the next trigger. Test in Task 8 (`test_corrupt_zip_left_in_place`).
2. **Two runs of the same course in the same minute**: `exported_at` collision must not overwrite a snapshot or report; filenames use full ISO seconds plus run id suffix. Test in Task 8 (`test_same_second_exports_do_not_collide`).
3. **Course renamed between exports** (instructor edits course name): identity is `course.id`, `latest/<name>/` must move to the new name and the old tree removed. Test in Task 11 (`test_course_rename_moves_latest`).
4. **Item path changes but content does not** (title edit): must be reported as a meta change (`title`) not as removed+new, and `latest/` must show the new path. Test in Task 9 (`test_title_change_is_meta_change`).
5. **Grades.csv with quoted commas in assignment names**: CSV-aware parsing, never split on commas. Test in Task 7 (`test_grades_diff_handles_quoted_commas`).

---

## File Structure

Extension (modify):
- `downloader.js` — ZIP prefix fix; attach `meta` to entries; build schema-2 manifest after dedupe/rewrite.
- `helpers.js` — add pure `buildManifestItems(entries, modules)` used by downloader and testable in node.
- `tests/manifest.test.js` — node test loading `helpers.js` via `vm`.

Pipeline (create under `sync/`):
- `pyproject.toml` — package metadata and console script, no deps.
- `canvas_sync/__init__.py` — version string.
- `canvas_sync/store.py` — `Store`: paths, schema, blob I/O, run/item/version queries.
- `canvas_sync/manifest.py` — dataclasses `Item`, `Manifest`, `parse_manifest`, `ManifestError`.
- `canvas_sync/textdiff.py` — `html_to_text`, `unified`, `grades_diff`.
- `canvas_sync/changes.py` — `ItemChange`, `ChangeSet`, `compute_changes`.
- `canvas_sync/report.py` — `render_report`.
- `canvas_sync/ingest.py` — `ingest_zip`, `ingest_dir`, `IngestResult`.
- `canvas_sync/materialize.py` — `materialize_run`, `rebuild_latest`.
- `canvas_sync/verify.py` — `verify`, `gc`.
- `canvas_sync/cli.py` — argparse entry point, `main(argv)`.
- `canvas_sync/__main__.py` — `python -m canvas_sync`.
- `tests/helpers.py` — `make_zip`, `base_manifest`, `TempStore` mixin.
- `tests/test_*.py` — one per module.
- `systemd/canvas-sync.path`, `systemd/canvas-sync.service`.

---

### Task 1: Fix ZIP folder-prefix flattening (extension)

**Files:**
- Modify: `downloader.js:333-345` (inside `downloadAsZip`)

**Interfaces:**
- Consumes: `settings.folderPrefix`, background `START_DOWNLOAD` payload `{files:[{url, filename, path}], courseName, conflictAction, throttleMs, folderPrefix}`.
- Produces: ZIP saved at `<prefix>/<course>.zip` instead of `<prefix>-<course>.zip`.

- [ ] **Step 1: Read the current code**

```bash
sed -n 330,347p downloader.js
```

Expected: `const filename = \`${prefix}${safeName}.zip\`;` and payload `files: [{ url, filename, path: "" }]`.

- [ ] **Step 2: Apply the fix**

Replace lines 334–339 with:

```js
  const prefix = settings.folderPrefix ? `${sanitizeFilename(settings.folderPrefix)}/` : "";
  const filename = `${safeName}.zip`;

  await new Promise((resolve, reject) => {
    chrome.runtime.sendMessage(
      // `path` carries the folder: the background sanitizer replaces "/" inside
      // `filename`, so a prefix embedded there used to collapse into "prefix-course.zip".
      { type: "START_DOWNLOAD", payload: { files: [{ url, filename, path: prefix }], courseName: "", conflictAction: settings.conflictAction, throttleMs: 0, folderPrefix: "" } },
```

Background computes `path = \`${""}${""}/${prefix}\`.replace(/\/+/g, "/")` = `/CanvasExports/`, then strips the leading slash. Verified by reading `background.js:258-266`.

- [ ] **Step 3: Syntax check**

```bash
node --check downloader.js
```

Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add downloader.js
git commit -m "fix: keep folder prefix as a directory for ZIP downloads

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Verify ZIP mode works on Firefox (manual gate)

**Files:** none (manual check; outcome decides whether Task 2b is needed)

- [ ] **Step 1: Set extension options**

In Firefox: extension Settings → ZIP mode **on**, Folder prefix `CanvasExports`, Conflict action Overwrite, Incremental **off** (for this test). Save.

- [ ] **Step 2: Reload temporary add-on**

`about:debugging#/runtime/this-firefox` → Reload. Open `https://canvas.usask.ca/courses/133044`.

- [ ] **Step 3: Export**

Click "Download course content". Expected: panel shows "Generating ZIP file (streaming)...", then one download `~/Downloads/CanvasExports/XLS5C202609.zip`.

- [ ] **Step 4: Record outcome**

```bash
ls -la ~/Downloads/CanvasExports/ && unzip -l ~/Downloads/CanvasExports/XLS5C202609.zip | tail -3
```

If the ZIP exists and lists ~60 entries: Task 2b is **skipped**. If Firefox refused the blob URL (background console shows an error from `downloads.download`), do Task 2b.

- [ ] **Step 5: Note result in CLAUDE.md "Known issues"**

Add one line: `ZIP mode on Firefox: verified working YYYY-MM-DD` or the exact error text. Commit:

```bash
git add CLAUDE.md
git commit -m "docs: record Firefox ZIP mode check

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 2b (conditional): Route ZIP blob through the background on Firefox

Only if Step 4 above failed.

**Files:**
- Modify: `downloader.js:333` (where `URL.createObjectURL(blob)` is called)
- Modify: `background.js` message handler (`START_DOWNLOAD` branch)

- [ ] **Step 1: Send the Blob itself when the runtime supports it**

Firefox structured-clones `Blob` in `runtime.sendMessage`; Chrome does not. In `downloadAsZip`, replace `const url = URL.createObjectURL(blob);` with:

```js
  // Firefox refuses blob: URLs minted by a content script in downloads.download().
  // Firefox can structured-clone a Blob through runtime messaging, so hand the
  // Blob to the background and let it mint the URL in its own context. Chrome
  // cannot clone Blobs; it keeps the content-script blob: URL, which it accepts.
  const canSendBlob = typeof browser !== "undefined" && typeof browser.runtime?.getBrowserInfo === "function";
  const url = canSendBlob ? null : URL.createObjectURL(blob);
  const fileEntry = canSendBlob ? { blob, filename, path: prefix } : { url, filename, path: prefix };
```

and use `files: [fileEntry]` in the payload. Guard the later `URL.revokeObjectURL(url)` with `if (url)`.

- [ ] **Step 2: Accept `blob` in the background**

In `background.js` `START_DOWNLOAD` branch, when mapping `files`:

```js
      const newJobs = files.map((file) => ({
        id: nextJobId++,
        url: file.url || (file.blob && typeof URL.createObjectURL === "function" ? URL.createObjectURL(file.blob) : ""),
        ...
```

Blob jobs cannot be persisted to `storage.session` (Blob is not JSON); set `url` immediately as above so only the string is stored.

- [ ] **Step 3: Repeat Task 2 Steps 2–4**, then commit:

```bash
git add downloader.js background.js
git commit -m "fix(firefox): hand ZIP blob to background for download

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Pure manifest builder in helpers.js (extension, TDD with node)

**Files:**
- Modify: `helpers.js` (append at end)
- Create: `tests/manifest.test.js`

**Interfaces:**
- Produces: `buildManifestItems(entries, modules) -> Array<Item>` where an entry is a `filesToDownload` element and `modules` is `[{ id, name, position, items: [...] }]`. Item shape: `{ type, canvasId|key, path, title, updatedAt, size, meta, sourceCourseId }`.

- [ ] **Step 1: Write the failing test**

`tests/manifest.test.js`:

```js
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

// helpers.js is a browser script with top-level function declarations. Evaluate it
// in a sandbox that stubs the DOM bits its unrelated functions touch at call time.
const src = fs.readFileSync(path.join(__dirname, "..", "helpers.js"), "utf8");
const sandbox = { document: {}, getComputedStyle: () => ({ getPropertyValue: () => "" }), console };
vm.createContext(sandbox);
vm.runInContext(src + "\nthis.buildManifestItems = buildManifestItems;", sandbox);
const { buildManifestItems } = sandbox;

test("doc entries become typed items keyed by resourceId", () => {
  const items = buildManifestItems([
    { resourceType: "assignment", resourceId: "456", path: "Assignments/", filename: "Assignment 1.html",
      title: "Assignment 1", meta: { due_at: "2026-10-10T06:59:00Z", points_possible: 10 } },
  ], []);
  assert.deepEqual(items, [{
    type: "assignment", canvasId: "456", path: "Assignments/Assignment 1.html", title: "Assignment 1",
    updatedAt: null, size: null, meta: { due_at: "2026-10-10T06:59:00Z", points_possible: 10 }, sourceCourseId: null,
  }]);
});

test("real files are type file keyed by canvasId, media by mediaId", () => {
  const items = buildManifestItems([
    { canvasId: "9275770", path: "Extracted_Files/", filename: "noiseeq.png", size: 1234,
      updatedAt: "2026-09-01T00:00:00Z", contentType: "image/png", meta: { source: "Quiz: Topic 4 Quiz" } },
    { mediaId: "m-abc", mediaKeys: [], path: "Media/", filename: "lecture.mp4", size: 0 },
  ], []);
  assert.equal(items[0].type, "file");
  assert.equal(items[0].canvasId, "9275770");
  assert.equal(items[0].meta.content_type, "image/png");
  assert.equal(items[0].meta.source, "Quiz: Topic 4 Quiz");
  assert.equal(items[1].type, "media");
  assert.equal(items[1].canvasId, "m-abc");
});

test("generated files without a Canvas id are synthetic with a path key", () => {
  const items = buildManifestItems([
    { url: "data:text/csv,...", path: "", filename: "Grades.csv" },
    { url: "data:text/css,...", path: "", filename: "styles.css" },
  ], []);
  assert.deepEqual(items.map((i) => [i.type, i.key]), [["synthetic", "grades.csv"], ["synthetic", "styles.css"]]);
});

test("cross-course linked files keep sourceCourseId", () => {
  const [item] = buildManifestItems([
    { canvasId: "1", path: "Extracted_Files/", filename: "a.pdf", sourceCourseId: "152979" },
  ], []);
  assert.equal(item.sourceCourseId, "152979");
});

test("modules are emitted as pathless items with ordered children", () => {
  const items = buildManifestItems([], [
    { id: "77", name: "Week 1", position: 2, items: [{ id: "901", type: "Page", title: "Intro", content_id: null, page_url: "intro" }] },
  ]);
  assert.deepEqual(items, [{
    type: "module", canvasId: "77", path: null, title: "Week 1", updatedAt: null, size: null,
    meta: { position: 2, items: [{ id: "901", type: "Page", title: "Intro", contentId: "intro" }] }, sourceCourseId: null,
  }]);
});

test("the manifest entry itself is excluded", () => {
  const items = buildManifestItems([{ url: "data:application/json,{}", path: "", filename: "manifest.json" }], []);
  assert.deepEqual(items, []);
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
node --test tests/manifest.test.js
```

Expected: FAIL, `buildManifestItems is not defined`.

- [ ] **Step 3: Implement in helpers.js**

Append to `helpers.js`:

```js
// ---------------------------------------------------------------------------
// Export manifest (schema 2)
// ---------------------------------------------------------------------------

/**
 * Builds the `items` array for the schema-2 export manifest from the final
 * `filesToDownload` entries plus the fetched module list. Identity rules:
 *   - generated documents: `resourceType` + `resourceId` (assignment, quiz, page,
 *     announcement, discussion, submission); module-index/syllabus/grading-weights
 *     fall through to synthetic
 *   - real course files: `canvasId`; Canvas media: `mediaId`
 *   - anything else generated (CSV, styles): synthetic, keyed by lowercased path
 *   - modules have no file of their own and are listed from `modules`
 * The manifest file itself is excluded. Pure function, no DOM.
 */
function buildManifestItems(entries, modules) {
  const TYPED = new Set(["assignment", "quiz", "page", "announcement", "discussion", "submission"]);
  const items = [];
  for (const e of entries || []) {
    const fullPath = `${e.path || ""}${e.filename || ""}`;
    if (fullPath === "manifest.json") continue;
    const base = {
      path: fullPath,
      title: e.title || e.filename || "",
      updatedAt: e.updatedAt || null,
      size: typeof e.size === "number" && e.size > 0 ? e.size : null,
      meta: { ...(e.meta || {}) },
      sourceCourseId: e.sourceCourseId || null,
    };
    if (TYPED.has(e.resourceType) && e.resourceId) {
      items.push({ type: e.resourceType, canvasId: String(e.resourceId), ...base });
    } else if (e.canvasId) {
      if (e.contentType) base.meta.content_type = e.contentType;
      items.push({ type: "file", canvasId: String(e.canvasId), ...base });
    } else if (e.mediaId) {
      if (e.contentType) base.meta.content_type = e.contentType;
      items.push({ type: "media", canvasId: String(e.mediaId), ...base });
    } else {
      items.push({ type: "synthetic", key: fullPath.toLowerCase(), ...base });
    }
  }
  for (const m of modules || []) {
    items.push({
      type: "module",
      canvasId: String(m.id),
      path: null,
      title: m.name || "",
      updatedAt: null,
      size: null,
      meta: {
        position: m.position ?? null,
        items: (m.items || []).map((it) => ({
          id: String(it.id),
          type: it.type || "",
          title: it.title || "",
          contentId: it.page_url || (it.content_id != null ? String(it.content_id) : null),
        })),
      },
      sourceCourseId: null,
    });
  }
  return items;
}
```

- [ ] **Step 4: Run tests**

```bash
node --test tests/manifest.test.js
```

Expected: 6 pass. Also open `tests/test-helpers.html` in a browser once to confirm the existing helper tests still pass (the file now has one more function, nothing else changed).

- [ ] **Step 5: Commit**

```bash
git add helpers.js tests/manifest.test.js
git commit -m "feat: pure buildManifestItems for schema-2 export manifest

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Attach metadata to entries and emit schema-2 manifest (extension)

**Files:**
- Modify: `downloader.js` at the push sites listed below and the manifest block (`downloader.js:1588-1619`), the rewrite pass (`downloader.js:1708-1720`), `downloadAsZip` (`downloader.js:254-290` for `failedPaths`).

**Interfaces:**
- Consumes: `buildManifestItems(entries, modules)` from Task 3.
- Produces: `manifest.json` in every export with `schema: 2` and the fields in the spec. `downloadAsZip` returns `failedKeys` (already) which become `failedPaths`.

- [ ] **Step 1: Add `meta` at each entry push**

Each edit is a small addition to an existing object literal. Use `grep -n` to find the exact line; the anchors below are the literal text.

Assignments (anchor `buildDocEntry(a.name, body, safeName, "Assignments/", "assignment", String(a.id))`):

```js
      filesToDownload.push(Object.assign(
        buildDocEntry(a.name, body, safeName, "Assignments/", "assignment", String(a.id)),
        { updatedAt: a.updated_at || null, meta: {
          due_at: a.due_at || null, lock_at: a.lock_at || null, unlock_at: a.unlock_at || null,
          points_possible: a.points_possible ?? null, submission_types: a.submission_types || [],
          published: a.published ?? null,
        } }
      ));
```

Quizzes (anchor `buildDocEntry(quiz.title, body, safeQuiz, quizPath, "quiz", String(quiz.id))`, already wrapped in `Object.assign` with `assignmentId`): extend the second argument object:

```js
        Object.assign(
          quiz.assignment_id ? { assignmentId: String(quiz.assignment_id) } : {},
          { updatedAt: quiz.updated_at || null, meta: {
            due_at: quiz.due_at || null, points_possible: quiz.points_possible ?? null,
            question_count: quiz.question_count ?? null, time_limit: quiz.time_limit ?? null,
            allowed_attempts: quiz.allowed_attempts ?? null, quiz_type: quiz.quiz_type || null,
            assignment_id: quiz.assignment_id != null ? String(quiz.assignment_id) : null,
          } }
        )
```

Pages (anchor `sanitizeFilename(page.url).substring(0, 100),` inside `buildDocEntry(` for pages): wrap the push:

```js
          filesToDownload.push(Object.assign(
            buildDocEntry(page.title, cleanCanvasHtml(page.body || ""), sanitizeFilename(page.url).substring(0, 100), "Pages/", "page", page.url),
            { updatedAt: page.updated_at || null, meta: { updated_at: page.updated_at || null, front_page: !!page.front_page } }
          ));
```

Announcements (anchor `"Announcements/", "announcement", String(a.id)`):

```js
      filesToDownload.push(Object.assign(
        buildDocEntry(a.title, body, safeName, "Announcements/", "announcement", String(a.id)),
        { updatedAt: a.posted_at || null, meta: { posted_at: a.posted_at || null } }
      ));
```

Discussions (anchor `"Discussions/", "discussion", String(d.id)`, already `Object.assign`): add to the second object `updatedAt: d.last_reply_at || d.posted_at || null, meta: { posted_at: d.posted_at || null, assignment_id: d.assignment_id != null ? String(d.assignment_id) : null }`.

Submissions (anchor `buildDocEntry(\`${a.name} — ${studentName}\`, body, stem, folder, "submission", null)`): change `null` to `String(a.id)` and wrap:

```js
    filesToDownload.push(Object.assign(
      buildDocEntry(`${a.name} — ${studentName}`, body, stem, folder, "submission", String(a.id)),
      { updatedAt: s.graded_at || s.submitted_at || null, meta: {
        score: s.score ?? null, grade: s.grade ?? null, graded_at: s.graded_at || null,
        submitted_at: s.submitted_at || null, attempt: s.attempt ?? null,
        comment_count: (s.submission_comments || []).length,
      } }
    ));
```

Course files (anchor `path: \`Files/${folder}\`, size: file.size || 0`): add `meta: { folder, source: "Files" }` to that object.

Linked files in `extractLinkedFiles` (anchor `path: "Extracted_Files/",`): add `meta: { folder: "", source }` (the `source` parameter is in scope).

Module files (anchor `path: \`Modules/${safeModName}/\`,`): add `meta: { folder: safeModName, source: \`Module: ${mod.name}\` }`.

Submission and discussion attachments (anchors `path: folder,` in `renderSubmission` and `path: attachmentPath,` in `renderEntries`): add `meta: { folder: "", source: "Submission" }` and `meta: { folder: "", source: \`Discussion: ${topicTitle}\` }` respectively.

Media entries (anchor `mediaKeys: mapKeys,`): add `meta: { source, media_type: info.media_type || null }`.

- [ ] **Step 2: Capture module items for the manifest**

In the modules block, after `const items = await fetchAllPages(api(\`modules/${mod.id}/items?per_page=100\`));` add:

```js
      mod.items = items; // kept for the schema-2 manifest (module order and membership)
```

- [ ] **Step 3: Move manifest generation after dedupe and link rewrite**

Delete the whole `// --- Export manifest` block (`const manifest = {` … `path: "",\n  });`) from its current position before path truncation. In the rewrite pass remove these three lines so identity survives to manifest time:

```js
    delete f.title;
    delete f.resourceType;
    delete f.resourceId;
```

(keep `delete f.rawBody;` and `delete f.assignmentId;`). Then insert, immediately after the rewrite pass loop and before `// --- ZIP mode or individual download handoff`:

```js
  // --- Export manifest (schema 2) ------------------------------------------
  // Built last so every path is final (truncated, de-duplicated, rewritten).
  // `items` is what the canvas-sync pipeline keys versions on; `counts` stays
  // for older consumers.
  const manifestItems = buildManifestItems(filesToDownload, modules);
  const manifest = {
    schema: 2,
    course: { id: String(courseId), name: courseName, domain },
    exportedAt: new Date().toISOString(),
    extensionVersion: chrome.runtime.getManifest().version,
    role: isTeacher ? "teacher" : "student",
    complete: true,
    failedPaths: [],
    // legacy fields
    courseId,
    sourceUrl: `${domain}/courses/${courseId}`,
    counts: {
      files: files.length,
      pages: exportedPagesCount,
      assignments: assignments.length,
      announcements: announcements.length,
      discussions: discussions.length,
      discussionReplies: discussionReplyCount,
      studentSubmissions: studentSubmissionCount,
      gradebookStudents: gradebookStudentCount,
      quizzes: quizCount,
      modules: modules.length,
      extractedFiles: filesToDownload.filter((f) => f.path === "Extracted_Files/").length,
      inaccessibleLinkedFiles: inaccessibleLinks.length,
      skippedIncremental: skippedCount,
      skippedFilters: filteredOutCount,
      total: filesToDownload.length,
    },
    items: manifestItems,
  };
  const manifestEntry = {
    url: "",
    filename: "manifest.json",
    path: "",
    conflictAction: "overwrite",
  };
  const encodeManifest = () => {
    manifestEntry.url = `data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(manifest, null, 2))}`;
  };
  encodeManifest();
  filesToDownload.push(manifestEntry);
```

- [ ] **Step 4: Mark incomplete exports**

`downloadAsZip` already collects `failedKeys` (path+filename). The manifest entry is a `data:` URL consumed by the generator *when its turn comes*, which is last because it was pushed last. So before the ZIP generator reaches it, update it: in `downloadAsZip`'s `fileSource()` generator, at the top of the `for (const file of files)` loop body add:

```js
      if (file.filename === "manifest.json" && file.path === "" && typeof file.finalize === "function") {
        file.finalize(failedKeys);
      }
```

and in `downloadCourse`, right after `filesToDownload.push(manifestEntry);` add:

```js
  manifestEntry.finalize = (failedPaths) => {
    manifest.complete = failedPaths.length === 0;
    manifest.failedPaths = failedPaths.slice();
    encodeManifest();
  };
```

Per-file (non-ZIP) mode cannot know failures ahead of time; `complete` stays `true` there, and the pipeline only supports ZIPs anyway.

- [ ] **Step 5: Syntax check and node tests**

```bash
node --check downloader.js && node --test tests/manifest.test.js
```

Expected: no errors, 6 pass.

- [ ] **Step 6: Live check**

Reload add-on, export course 133044, then:

```bash
cd ~/Downloads/CanvasExports && unzip -p XLS5C202609.zip manifest.json | python3 -c "import json,sys; m=json.load(sys.stdin); print(m['schema'], m['complete'], len(m['items'])); print(sorted({i['type'] for i in m['items']}))"
```

Expected: `2 True <about 60>` and types including `assignment file module quiz submission synthetic announcement`.

- [ ] **Step 7: Commit**

```bash
git add downloader.js
git commit -m "feat: schema-2 export manifest with Canvas ids and metadata

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Pipeline skeleton, store paths and blob I/O

**Files:**
- Create: `sync/pyproject.toml`, `sync/canvas_sync/__init__.py`, `sync/canvas_sync/store.py`, `sync/tests/__init__.py`, `sync/tests/helpers.py`, `sync/tests/test_store.py`

**Interfaces:**
- Produces: `Store(root: Path)`, `Store.default_root() -> Path`, `store.put_blob(data: bytes) -> str`, `store.has_blob(sha) -> bool`, `store.blob_path(sha) -> Path`, `store.read_blob(sha) -> bytes`, `store.db` (sqlite3.Connection with Row factory), `store.close()`. Directories `objects/`, `objects/tmp/`, `snapshots/`, `latest/`, `reports/`, `processed/`.

- [ ] **Step 1: Write the failing test**

`sync/tests/helpers.py`:

```python
import tempfile, unittest
from pathlib import Path
from canvas_sync.store import Store


class TempStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "archive"
        self.store = Store(self.root)

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()
```

`sync/tests/test_store.py`:

```python
import hashlib, os
from tests.helpers import TempStore


class StoreTests(TempStore):
    def test_creates_layout(self):
        for d in ("objects", "objects/tmp", "snapshots", "latest", "reports", "processed"):
            self.assertTrue((self.root / d).is_dir(), d)
        self.assertTrue((self.root / "store.sqlite").is_file())

    def test_put_blob_is_content_addressed_and_idempotent(self):
        data = b"hello"
        sha = self.store.put_blob(data)
        self.assertEqual(sha, hashlib.sha256(data).hexdigest())
        self.assertEqual(self.store.blob_path(sha), self.root / "objects" / sha[:2] / sha)
        self.assertTrue(self.store.has_blob(sha))
        mtime = os.stat(self.store.blob_path(sha)).st_mtime_ns
        self.assertEqual(self.store.put_blob(data), sha)
        self.assertEqual(os.stat(self.store.blob_path(sha)).st_mtime_ns, mtime)  # not rewritten
        self.assertEqual(self.store.read_blob(sha), data)
        self.assertEqual(list((self.root / "objects" / "tmp").iterdir()), [])

    def test_schema_tables_exist(self):
        names = {r[0] for r in self.store.db.execute("select name from sqlite_master where type='table'")}
        self.assertTrue({"courses", "runs", "items", "versions", "blobs"} <= names)

    def test_default_root_env(self):
        os.environ["CANVAS_SYNC_ROOT"] = str(self.root / "x")
        try:
            from canvas_sync.store import Store
            self.assertEqual(Store.default_root(), self.root / "x")
        finally:
            del os.environ["CANVAS_SYNC_ROOT"]
```

- [ ] **Step 2: Run to verify failure**

```bash
cd sync && python -m unittest discover -s tests -v
```

Expected: `ModuleNotFoundError: No module named 'canvas_sync'`.

- [ ] **Step 3: Create package files**

`sync/pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "canvas-sync"
version = "0.1.0"
description = "Versioned archive and change reports for Canvas course exports"
requires-python = ">=3.11"
dependencies = []

[project.scripts]
canvas-sync = "canvas_sync.cli:main"

[tool.setuptools.packages.find]
include = ["canvas_sync*"]
```

`sync/canvas_sync/__init__.py`:

```python
__version__ = "0.1.0"
```

`sync/tests/__init__.py`: empty file.

`sync/canvas_sync/store.py`:

```python
"""Store layout, SQLite schema and content-addressed blob I/O."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import uuid
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS courses (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, domain TEXT NOT NULL,
  first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY, course_id TEXT NOT NULL REFERENCES courses(id),
  exported_at TEXT NOT NULL, ingested_at TEXT NOT NULL,
  zip_sha256 TEXT NOT NULL UNIQUE, complete INTEGER NOT NULL,
  extension_version TEXT, counts_json TEXT, snapshot_path TEXT, report_path TEXT);
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY, course_id TEXT NOT NULL REFERENCES courses(id),
  type TEXT NOT NULL, key TEXT NOT NULL,
  first_run INTEGER NOT NULL, last_run INTEGER NOT NULL, removed_run INTEGER,
  UNIQUE(course_id, type, key));
CREATE TABLE IF NOT EXISTS versions (
  id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES items(id),
  run_id INTEGER NOT NULL REFERENCES runs(id),
  sha256 TEXT, size INTEGER, path TEXT, title TEXT NOT NULL, meta_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS versions_item ON versions(item_id, id);
CREATE TABLE IF NOT EXISTS blobs (
  sha256 TEXT PRIMARY KEY, size INTEGER NOT NULL, mime TEXT, first_seen TEXT NOT NULL);
"""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Store:
    def __init__(self, root: Path | str):
        self.root = Path(root).expanduser()
        self.objects = self.root / "objects"
        self.tmp = self.objects / "tmp"
        self.snapshots = self.root / "snapshots"
        self.latest = self.root / "latest"
        self.reports = self.root / "reports"
        self.processed = self.root / "processed"
        for d in (self.objects, self.tmp, self.snapshots, self.latest, self.reports, self.processed):
            d.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "store.sqlite")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)

    @staticmethod
    def default_root() -> Path:
        env = os.environ.get("CANVAS_SYNC_ROOT")
        return Path(env).expanduser() if env else Path.home() / "CanvasArchive"

    def close(self) -> None:
        self.db.close()

    # -- blobs ---------------------------------------------------------------
    def blob_path(self, sha: str) -> Path:
        return self.objects / sha[:2] / sha

    def has_blob(self, sha: str) -> bool:
        return self.blob_path(sha).is_file()

    def put_blob(self, data: bytes) -> str:
        """Write `data` once under its sha256. Atomic: tmp file, fsync, rename."""
        sha = sha256_bytes(data)
        dest = self.blob_path(sha)
        if dest.exists():
            return sha
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.tmp / uuid.uuid4().hex
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, dest)
        return sha

    def read_blob(self, sha: str) -> bytes:
        return self.blob_path(sha).read_bytes()
```

- [ ] **Step 4: Run tests**

```bash
cd sync && python -m unittest discover -s tests -v
```

Expected: 4 pass.

- [ ] **Step 5: Add `.gitignore` entries and commit**

Append to repo `.gitignore`: `sync/**/__pycache__/`, `sync/*.egg-info/`, `sync/build/`.

```bash
git add .gitignore sync/pyproject.toml sync/canvas_sync/__init__.py sync/canvas_sync/store.py sync/tests/__init__.py sync/tests/helpers.py sync/tests/test_store.py
git commit -m "feat(sync): store layout, schema and atomic blob writes

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Manifest parsing

**Files:**
- Create: `sync/canvas_sync/manifest.py`, `sync/tests/test_manifest.py`
- Modify: `sync/tests/helpers.py` (add `base_manifest`, `make_zip`)

**Interfaces:**
- Produces: `@dataclass Item(type, key, path, title, updated_at, size, meta: dict, source_course_id)`; `@dataclass Manifest(schema, course_id, course_name, domain, exported_at, extension_version, role, complete, failed_paths: list[str], counts: dict, items: list[Item])`; `parse_manifest(data: dict) -> Manifest`; `class ManifestError(ValueError)`; `canonical_meta(meta: dict) -> str` (sorted-keys JSON, used for comparison everywhere).

- [ ] **Step 1: Extend test helpers**

Append to `sync/tests/helpers.py`:

```python
import io, json, zipfile


def base_manifest(exported_at="2026-10-06T20:31:00.000Z", complete=True, items=None, course_id="133044",
                  course_name="XLS5C202609"):
    return {
        "schema": 2,
        "course": {"id": course_id, "name": course_name, "domain": "https://canvas.usask.ca"},
        "exportedAt": exported_at,
        "extensionVersion": "2.11.0",
        "role": "student",
        "complete": complete,
        "failedPaths": [],
        "counts": {"total": len(items or [])},
        "items": items or [],
    }


def doc_item(type_, canvas_id, path, title, meta=None, updated_at=None):
    return {"type": type_, "canvasId": canvas_id, "path": path, "title": title,
            "updatedAt": updated_at, "size": None, "meta": meta or {}, "sourceCourseId": None}


def file_item(canvas_id, path, size, meta=None, source_course_id=None):
    return {"type": "file", "canvasId": canvas_id, "path": path, "title": path.rsplit("/", 1)[-1],
            "updatedAt": None, "size": size, "meta": meta or {}, "sourceCourseId": source_course_id}


def synthetic_item(key, path, title=None):
    return {"type": "synthetic", "key": key, "path": path, "title": title or path,
            "updatedAt": None, "size": None, "meta": {}, "sourceCourseId": None}


def make_zip(dest, manifest, files):
    """Write a ZIP at `dest` containing manifest.json plus {path: bytes}."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for path, data in files.items():
            zf.writestr(path, data)
    return dest
```

- [ ] **Step 2: Write the failing test**

`sync/tests/test_manifest.py`:

```python
import unittest
from canvas_sync.manifest import parse_manifest, ManifestError, canonical_meta
from tests.helpers import base_manifest, doc_item, synthetic_item


class ManifestTests(unittest.TestCase):
    def test_parses_items_and_course(self):
        m = parse_manifest(base_manifest(items=[
            doc_item("assignment", "456", "Assignments/A1.html", "A1", {"points_possible": 10}),
            synthetic_item("grades.csv", "Grades.csv"),
            {"type": "module", "canvasId": "77", "path": None, "title": "Week 1", "updatedAt": None,
             "size": None, "meta": {"position": 1, "items": []}, "sourceCourseId": None},
        ]))
        self.assertEqual(m.course_id, "133044")
        self.assertEqual(m.course_name, "XLS5C202609")
        self.assertTrue(m.complete)
        self.assertEqual([i.key for i in m.items], ["456", "grades.csv", "77"])
        self.assertEqual(m.items[0].meta["points_possible"], 10)
        self.assertIsNone(m.items[2].path)

    def test_rejects_old_schema(self):
        data = base_manifest()
        del data["schema"]
        with self.assertRaises(ManifestError) as cm:
            parse_manifest(data)
        self.assertIn("schema 2", str(cm.exception))

    def test_rejects_item_without_identity(self):
        bad = base_manifest(items=[{"type": "file", "path": "x.pdf", "title": "x"}])
        with self.assertRaises(ManifestError):
            parse_manifest(bad)

    def test_canonical_meta_is_key_sorted(self):
        self.assertEqual(canonical_meta({"b": 1, "a": [1, 2]}), '{"a": [1, 2], "b": 1}')
        self.assertEqual(canonical_meta({}), "{}")
        self.assertEqual(canonical_meta(None), "{}")
```

- [ ] **Step 3: Run to verify failure**

```bash
cd sync && python -m unittest tests.test_manifest -v
```

Expected: `ModuleNotFoundError: canvas_sync.manifest`.

- [ ] **Step 4: Implement**

`sync/canvas_sync/manifest.py`:

```python
"""Schema-2 export manifest parsing."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

SUPPORTED_SCHEMA = 2


class ManifestError(ValueError):
    pass


def canonical_meta(meta: dict | None) -> str:
    return json.dumps(meta or {}, sort_keys=True, separators=(", ", ": "))


@dataclass
class Item:
    type: str
    key: str
    path: str | None
    title: str
    updated_at: str | None
    size: int | None
    meta: dict = field(default_factory=dict)
    source_course_id: str | None = None


@dataclass
class Manifest:
    schema: int
    course_id: str
    course_name: str
    domain: str
    exported_at: str
    extension_version: str
    role: str
    complete: bool
    failed_paths: list[str]
    counts: dict
    items: list[Item]


def _item(raw: dict, index: int) -> Item:
    type_ = raw.get("type")
    key = raw.get("canvasId") if type_ != "synthetic" else raw.get("key")
    if not type_ or key in (None, ""):
        raise ManifestError(f"item {index} has no identity (type={type_!r})")
    size = raw.get("size")
    return Item(
        type=str(type_),
        key=str(key),
        path=raw.get("path") or None,
        title=str(raw.get("title") or raw.get("path") or key),
        updated_at=raw.get("updatedAt") or None,
        size=int(size) if isinstance(size, (int, float)) and size > 0 else None,
        meta=dict(raw.get("meta") or {}),
        source_course_id=str(raw["sourceCourseId"]) if raw.get("sourceCourseId") else None,
    )


def parse_manifest(data: dict) -> Manifest:
    schema = data.get("schema")
    if schema != SUPPORTED_SCHEMA:
        raise ManifestError(
            f"manifest schema {schema!r} not supported; this pipeline needs schema 2. "
            "Update the Canvas Course Downloader extension and export again."
        )
    course = data.get("course") or {}
    for k in ("id", "name", "domain"):
        if not course.get(k):
            raise ManifestError(f"manifest course.{k} missing")
    if not data.get("exportedAt"):
        raise ManifestError("manifest exportedAt missing")
    items = [_item(raw, i) for i, raw in enumerate(data.get("items") or [])]
    return Manifest(
        schema=schema,
        course_id=str(course["id"]),
        course_name=str(course["name"]),
        domain=str(course["domain"]),
        exported_at=str(data["exportedAt"]),
        extension_version=str(data.get("extensionVersion") or ""),
        role=str(data.get("role") or "student"),
        complete=bool(data.get("complete", True)),
        failed_paths=list(data.get("failedPaths") or []),
        counts=dict(data.get("counts") or {}),
        items=items,
    )
```

- [ ] **Step 5: Run tests, commit**

```bash
cd sync && python -m unittest discover -s tests -v
git add sync/canvas_sync/manifest.py sync/tests/test_manifest.py sync/tests/helpers.py
git commit -m "feat(sync): schema-2 manifest parsing

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Text diff helpers

**Files:**
- Create: `sync/canvas_sync/textdiff.py`, `sync/tests/test_textdiff.py`

**Interfaces:**
- Produces: `html_to_text(html: str) -> str`; `unified(old: str, new: str, label_old="old", label_new="new") -> tuple[str, int, int]` (diff text, added lines, removed lines); `grades_diff(old_csv: str, new_csv: str) -> list[tuple[str, str, str]]` as `(assignment, old_score, new_score)` with `"—"` for missing.

- [ ] **Step 1: Write the failing test**

`sync/tests/test_textdiff.py`:

```python
import unittest
from canvas_sync.textdiff import html_to_text, unified, grades_diff

DOC = ('<!doctype html><html><head><title>A1</title><link rel="stylesheet" href="styles.css">'
       '<style>p{}</style></head><body><h1>A1</h1><p>Due <strong>Friday</strong>.</p>'
       '<script>alert(1)</script><ul><li>one</li><li>two</li></ul></body></html>')


class TextDiffTests(unittest.TestCase):
    def test_html_to_text_strips_markup_and_head(self):
        self.assertEqual(html_to_text(DOC), "A1\nDue Friday.\none\ntwo")

    def test_unified_counts(self):
        diff, add, rem = unified("a\nb\nc", "a\nB\nc\nd")
        self.assertEqual((add, rem), (2, 1))
        self.assertIn("-b", diff)
        self.assertIn("+B", diff)
        self.assertIn("+d", diff)

    def test_unified_identical_is_empty(self):
        self.assertEqual(unified("x", "x"), ("", 0, 0))

    def test_grades_diff_handles_quoted_commas(self):
        old = 'Assignment,Due Date,Points Possible,Score,Grade\n"Lab 1, part a","2026-10-01",10,,""\n"Quiz",,5,4,"4"'
        new = 'Assignment,Due Date,Points Possible,Score,Grade\n"Lab 1, part a","2026-10-01",10,9,"9"\n"Quiz",,5,4,"4"\n"New",,1,1,"1"'
        self.assertEqual(grades_diff(old, new), [("Lab 1, part a", "—", "9"), ("New", "—", "1")])
```

- [ ] **Step 2: Run to verify failure**

```bash
cd sync && python -m unittest tests.test_textdiff -v
```

Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement**

`sync/canvas_sync/textdiff.py`:

```python
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
```

- [ ] **Step 4: Run tests, commit**

```bash
cd sync && python -m unittest discover -s tests -v
git add sync/canvas_sync/textdiff.py sync/tests/test_textdiff.py
git commit -m "feat(sync): html-to-text, unified diff and grades diff helpers

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Ingest core (runs, items, versions, blobs, snapshot)

**Files:**
- Create: `sync/canvas_sync/ingest.py`, `sync/tests/test_ingest.py`

**Interfaces:**
- Consumes: `Store`, `parse_manifest`, `canonical_meta`.
- Produces: `@dataclass IngestResult(run_id: int | None, course_id, course_name, exported_at, skipped: bool, reason: str, report_path: Path | None, summary: str)`; `ingest_zip(store, zip_path: Path, keep: bool = True, notify: bool = False) -> IngestResult`; `ingest_dir(store, directory: Path, keep=True, notify=False) -> list[IngestResult]`; `class IngestError(Exception)`.
- Internal helpers used by Task 10/12: `store.db` rows in `runs`, `items`, `versions`; `runs.snapshot_path` points at JSON `{"manifest": {...}, "files": {path: sha}}`.

This task does steps C1–C8 and C11 of the spec; report and `latest/` (C9, C10) are wired in Tasks 11 and 12 via hooks left here.

- [ ] **Step 1: Write the failing tests**

`sync/tests/test_ingest.py`:

```python
import json, zipfile
from pathlib import Path
from canvas_sync.ingest import ingest_zip, ingest_dir, IngestError
from canvas_sync.manifest import ManifestError
from tests.helpers import TempStore, base_manifest, doc_item, file_item, synthetic_item, make_zip

A1 = doc_item("assignment", "456", "Assignments/A1.html", "A1", {"due_at": "2026-10-10", "points_possible": 10})
PDF = file_item("9001", "Extracted_Files/lec.pdf", 3)
GRADES = synthetic_item("grades.csv", "Grades.csv")
FILES = {"Assignments/A1.html": b"<h1>A1</h1><p>v1</p>", "Extracted_Files/lec.pdf": b"pdf", "Grades.csv": b"Assignment,Score\nA1,"}


class IngestTests(TempStore):
    def zip(self, name, manifest, files=FILES):
        return make_zip(self.root / "inbox" / name, manifest, files)

    def q(self, sql, *args):
        return [dict(r) for r in self.store.db.execute(sql, args)]

    def test_first_run_creates_everything(self):
        res = ingest_zip(self.store, self.zip("a.zip", base_manifest(items=[A1, PDF, GRADES])))
        self.assertFalse(res.skipped)
        self.assertEqual(self.q("select id,name from courses"), [{"id": "133044", "name": "XLS5C202609"}])
        self.assertEqual(len(self.q("select * from runs")), 1)
        self.assertEqual(len(self.q("select * from items")), 3)
        self.assertEqual(len(self.q("select * from versions")), 3)
        self.assertEqual(len(self.q("select * from blobs")), 3)
        run = self.q("select * from runs")[0]
        snap = json.loads(Path(run["snapshot_path"]).read_text())
        self.assertEqual(set(snap["files"]), set(FILES))
        self.assertTrue((self.store.processed / "a.zip").exists())
        self.assertFalse((self.root / "inbox" / "a.zip").exists())

    def test_unchanged_rerun_adds_no_versions(self):
        ingest_zip(self.store, self.zip("a.zip", base_manifest(items=[A1, PDF, GRADES])))
        ingest_zip(self.store, self.zip("b.zip", base_manifest(exported_at="2026-10-07T00:00:00Z", items=[A1, PDF, GRADES])))
        self.assertEqual(len(self.q("select * from runs")), 2)
        self.assertEqual(len(self.q("select * from versions")), 3)
        self.assertEqual(self.q("select distinct last_run from items"), [{"last_run": 2}])

    def test_meta_only_change_adds_version(self):
        ingest_zip(self.store, self.zip("a.zip", base_manifest(items=[A1])))
        a1b = dict(A1, meta={"due_at": "2026-10-12", "points_possible": 10})
        ingest_zip(self.store, self.zip("b.zip", base_manifest(exported_at="2026-10-07T00:00:00Z", items=[a1b])))
        vs = self.q("select meta_json from versions order by id")
        self.assertEqual(len(vs), 2)
        self.assertIn('"due_at": "2026-10-12"', vs[1]["meta_json"])

    def test_body_change_adds_version_and_blob(self):
        ingest_zip(self.store, self.zip("a.zip", base_manifest(items=[A1])))
        ingest_zip(self.store, self.zip("b.zip", base_manifest(exported_at="2026-10-07T00:00:00Z", items=[A1]),
                                        {"Assignments/A1.html": b"<h1>A1</h1><p>v2</p>"}))
        self.assertEqual(len(self.q("select * from versions")), 2)
        self.assertEqual(len(self.q("select * from blobs")), 2)

    def test_removed_only_on_complete_run(self):
        ingest_zip(self.store, self.zip("a.zip", base_manifest(items=[A1, PDF])))
        ingest_zip(self.store, self.zip("b.zip", base_manifest(exported_at="2026-10-07T00:00:00Z", complete=False, items=[A1])))
        self.assertEqual(self.q("select removed_run from items where key='9001'"), [{"removed_run": None}])
        ingest_zip(self.store, self.zip("c.zip", base_manifest(exported_at="2026-10-08T00:00:00Z", items=[A1])))
        self.assertEqual(self.q("select removed_run from items where key='9001'"), [{"removed_run": 3}])
        ingest_zip(self.store, self.zip("d.zip", base_manifest(exported_at="2026-10-09T00:00:00Z", items=[A1, PDF])))
        self.assertEqual(self.q("select removed_run from items where key='9001'"), [{"removed_run": None}])

    def test_duplicate_zip_is_skipped(self):
        z = self.zip("a.zip", base_manifest(items=[A1]))
        ingest_zip(self.store, z)
        res = ingest_zip(self.store, self.zip("again.zip", base_manifest(items=[A1])))
        self.assertTrue(res.skipped)
        self.assertEqual(len(self.q("select * from runs")), 1)
        self.assertTrue((self.store.processed / "again.zip").exists())

    def test_corrupt_zip_left_in_place(self):
        bad = self.root / "inbox" / "bad.zip"
        bad.parent.mkdir(parents=True)
        bad.write_bytes(b"not a zip")
        res = ingest_zip(self.store, bad)
        self.assertTrue(res.skipped)
        self.assertIn("not a valid ZIP", res.reason)
        self.assertTrue(bad.exists())
        self.assertEqual(self.q("select * from runs"), [])

    def test_schema1_rejected(self):
        legacy = {"course": "X", "courseId": "1", "counts": {}}
        z = self.zip("old.zip", legacy, {})
        with self.assertRaises(ManifestError):
            ingest_zip(self.store, z)
        self.assertTrue(z.exists())

    def test_same_second_exports_do_not_collide(self):
        ingest_zip(self.store, self.zip("a.zip", base_manifest(items=[A1])))
        ingest_zip(self.store, self.zip("b.zip", base_manifest(items=[A1]), {"Assignments/A1.html": b"x"}))
        paths = [r["snapshot_path"] for r in self.q("select snapshot_path from runs")]
        self.assertEqual(len(set(paths)), 2)
        self.assertTrue(all(Path(p).exists() for p in paths))

    def test_missing_file_in_complete_run_is_warning_not_removal(self):
        ingest_zip(self.store, self.zip("a.zip", base_manifest(items=[A1, PDF])))
        res = ingest_zip(self.store, self.zip("b.zip", base_manifest(exported_at="2026-10-07T00:00:00Z", items=[A1, PDF]),
                                              {"Assignments/A1.html": FILES["Assignments/A1.html"]}))
        self.assertIn("missing from ZIP", res.summary)
        self.assertEqual(self.q("select removed_run from items where key='9001'"), [{"removed_run": None}])

    def test_ingest_dir_skips_part_files(self):
        self.zip("a.zip", base_manifest(items=[A1]))
        (self.root / "inbox" / "b.zip.part").write_bytes(b"partial")
        results = ingest_dir(self.store, self.root / "inbox")
        self.assertEqual(len(results), 1)
        self.assertTrue((self.root / "inbox" / "b.zip.part").exists())
```

- [ ] **Step 2: Run to verify failure**

```bash
cd sync && python -m unittest tests.test_ingest -v
```

Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement**

`sync/canvas_sync/ingest.py`:

```python
"""Ingest one extension ZIP into the store (spec Part C)."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .manifest import Manifest, ManifestError, canonical_meta, parse_manifest
from .store import Store


class IngestError(Exception):
    pass


@dataclass
class IngestResult:
    run_id: int | None
    course_id: str | None
    course_name: str | None
    exported_at: str | None
    skipped: bool
    reason: str = ""
    report_path: Path | None = None
    summary: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_stamp(exported_at: str) -> str:
    return exported_at.replace(":", "").replace("-", "").replace(".", "_")


def _move_processed(store: Store, zip_path: Path, keep: bool) -> None:
    if not keep:
        zip_path.unlink(missing_ok=True)
        return
    dest = store.processed / zip_path.name
    n = 1
    while dest.exists():
        dest = store.processed / f"{zip_path.stem}.{n}{zip_path.suffix}"
        n += 1
    shutil.move(str(zip_path), dest)


def _notify(summary: str) -> None:
    exe = shutil.which("notify-send")
    if exe:
        subprocess.run([exe, "Canvas sync", summary], check=False)


# Hooks filled in by later tasks (report rendering, latest/ rebuild). Kept as
# module attributes so ingest stays testable before those modules exist.
def _after_commit(store: Store, run_id: int, warnings: list[str]) -> tuple[Path | None, str]:
    return None, ""


def ingest_zip(store: Store, zip_path: Path, keep: bool = True, notify: bool = False) -> IngestResult:
    zip_path = Path(zip_path)
    if zip_path.name.endswith(".part"):
        return IngestResult(None, None, None, None, True, "still downloading (.part)")
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as e:
        return IngestResult(None, None, None, None, True, f"not a valid ZIP yet ({e}); left in place")

    with zf:
        zip_sha = _file_sha(zip_path)
        if store.db.execute("select 1 from runs where zip_sha256=?", (zip_sha,)).fetchone():
            _move_processed(store, zip_path, keep)
            return IngestResult(None, None, None, None, True, "already ingested (same ZIP hash)")

        try:
            manifest = parse_manifest(json.loads(zf.read("manifest.json")))
        except KeyError:
            raise ManifestError(f"{zip_path.name}: no manifest.json in ZIP")

        names = set(zf.namelist())
        warnings: list[str] = []
        blobs: dict[str, tuple[str, int]] = {}  # path -> (sha, size)
        for item in manifest.items:
            if not item.path:
                continue
            if item.path not in names:
                if item.path in manifest.failed_paths:
                    continue
                warnings.append(f"{item.path} missing from ZIP")
                continue
            data = zf.read(item.path)
            sha = store.put_blob(data)
            blobs[item.path] = (sha, len(data))
            mime = item.meta.get("content_type") or mimetypes.guess_type(item.path)[0]
            store.db.execute(
                "insert or ignore into blobs(sha256,size,mime,first_seen) values(?,?,?,?)",
                (sha, len(data), mime, _now()))

        run_id = _record_run(store, manifest, zip_sha, blobs, warnings)
        snapshot = store.snapshots / manifest.course_id / f"{_safe_stamp(manifest.exported_at)}_run{run_id}.json"
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(json.dumps({
            "run_id": run_id,
            "course": {"id": manifest.course_id, "name": manifest.course_name, "domain": manifest.domain},
            "exported_at": manifest.exported_at,
            "complete": manifest.complete,
            "files": {p: s for p, (s, _) in blobs.items()},
            "items": [{"type": i.type, "key": i.key, "path": i.path, "title": i.title, "meta": i.meta} for i in manifest.items],
        }, indent=1))
        store.db.execute("update runs set snapshot_path=? where id=?", (str(snapshot), run_id))
        store.db.commit()

    report_path, summary = _after_commit(store, run_id, warnings)
    if not summary:
        summary = f"{manifest.course_name}: run {run_id} ingested"
    if warnings:
        summary += "; " + "; ".join(warnings)
    _move_processed(store, zip_path, keep)
    if notify:
        _notify(summary)
    return IngestResult(run_id, manifest.course_id, manifest.course_name, manifest.exported_at, False,
                        "", report_path, summary)


def _record_run(store: Store, m: Manifest, zip_sha: str, blobs: dict, warnings: list[str]) -> int:
    db = store.db
    now = _now()
    db.execute("begin")
    db.execute(
        "insert into courses(id,name,domain,first_seen,last_seen) values(?,?,?,?,?) "
        "on conflict(id) do update set name=excluded.name, domain=excluded.domain, last_seen=excluded.last_seen",
        (m.course_id, m.course_name, m.domain, now, now))
    cur = db.execute(
        "insert into runs(course_id,exported_at,ingested_at,zip_sha256,complete,extension_version,counts_json) "
        "values(?,?,?,?,?,?,?)",
        (m.course_id, m.exported_at, now, zip_sha, 1 if m.complete else 0, m.extension_version, json.dumps(m.counts)))
    run_id = cur.lastrowid

    seen_item_ids: list[int] = []
    for it in m.items:
        has_file = it.path is not None
        row = db.execute("select id from items where course_id=? and type=? and key=?",
                         (m.course_id, it.type, it.key)).fetchone()
        if has_file and it.path not in blobs:
            # Listed but not fetched (failed or missing from the ZIP): the item still
            # exists on Canvas, so it must not count as removed; it just gets no new
            # version this run.
            if row:
                seen_item_ids.append(row["id"])
            continue
        sha, size = blobs[it.path] if has_file else (None, None)
        meta_json = canonical_meta(it.meta)
        if row:
            item_id = row["id"]
            db.execute("update items set last_run=?, removed_run=NULL where id=?", (run_id, item_id))
        else:
            item_id = db.execute(
                "insert into items(course_id,type,key,first_run,last_run,removed_run) values(?,?,?,?,?,NULL)",
                (m.course_id, it.type, it.key, run_id, run_id)).lastrowid
        seen_item_ids.append(item_id)
        latest = db.execute("select sha256, meta_json, path, title from versions where item_id=? order by id desc limit 1",
                            (item_id,)).fetchone()
        changed = (latest is None or latest["sha256"] != sha or latest["meta_json"] != meta_json
                   or latest["path"] != it.path or latest["title"] != it.title)
        if changed:
            db.execute(
                "insert into versions(item_id,run_id,sha256,size,path,title,meta_json) values(?,?,?,?,?,?,?)",
                (item_id, run_id, sha, size, it.path, it.title, meta_json))

    if m.complete:
        placeholders = ",".join("?" * len(seen_item_ids)) or "NULL"
        db.execute(
            f"update items set removed_run=? where course_id=? and removed_run is NULL and id not in ({placeholders})",
            (run_id, m.course_id, *seen_item_ids))
    db.commit()
    return run_id


def ingest_dir(store: Store, directory: Path, keep: bool = True, notify: bool = False) -> list[IngestResult]:
    directory = Path(directory).expanduser()
    results = []
    for p in sorted(directory.glob("*.zip")):
        results.append(ingest_zip(store, p, keep=keep, notify=notify))
    return results
```

Note on `_record_run`: title or path change also creates a version row (Review Focus 4). `changed` compares `path` and `title` too, which the spec's "sha or meta" wording implies since the materialised tree must follow renames.

- [ ] **Step 4: Run tests**

```bash
cd sync && python -m unittest discover -s tests -v
```

Expected: all pass (store 4, manifest 4, textdiff 4, ingest 11).

- [ ] **Step 5: Commit**

```bash
git add sync/canvas_sync/ingest.py sync/tests/test_ingest.py
git commit -m "feat(sync): ingest ZIP into runs, items, versions and snapshots

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Change set computation

**Files:**
- Create: `sync/canvas_sync/changes.py`, `sync/tests/test_changes.py`

**Interfaces:**
- Consumes: `store.db` tables from Task 8; `textdiff.html_to_text`, `unified`, `grades_diff`.
- Produces: `@dataclass ItemChange(kind: str, type: str, key: str, title: str, path: str | None, meta_diff: list[tuple[str, object, object]], body_diff: str, body_added: int, body_removed: int, size_change: tuple[int | None, int | None] | None, grade_changes: list[tuple[str, str, str]])`; `@dataclass ChangeSet(course_id, course_name, run_id, exported_at, prev_run_id: int | None, prev_exported_at: str | None, new: list[ItemChange], changed: list[ItemChange], removed: list[ItemChange])`; `compute_changes(store, run_id) -> ChangeSet`.

- [ ] **Step 1: Write the failing test**

`sync/tests/test_changes.py`:

```python
from canvas_sync.ingest import ingest_zip
from canvas_sync.changes import compute_changes
from tests.helpers import TempStore, base_manifest, doc_item, file_item, synthetic_item, make_zip

A1 = doc_item("assignment", "456", "Assignments/A1.html", "A1", {"due_at": "2026-10-10", "points_possible": 10})
A2 = doc_item("assignment", "457", "Assignments/A2.html", "A2", {"due_at": "2026-10-20", "points_possible": 15})
PDF = file_item("9001", "Extracted_Files/lec.pdf", 3)
GRADES = synthetic_item("grades.csv", "Grades.csv")
MOD = {"type": "module", "canvasId": "77", "path": None, "title": "Week 1", "updatedAt": None, "size": None,
       "meta": {"position": 1, "items": [{"id": "1", "type": "Page", "title": "a", "contentId": "a"},
                                         {"id": "2", "type": "Page", "title": "b", "contentId": "b"}]}, "sourceCourseId": None}
V1 = {"Assignments/A1.html": b"<h1>A1</h1><p>Due Friday</p>", "Extracted_Files/lec.pdf": b"pdf",
      "Grades.csv": b"Assignment,Score\nA1,\n"}


class ChangeTests(TempStore):
    def run_two(self, items1, files1, items2, files2):
        r1 = ingest_zip(self.store, make_zip(self.root / "in/a.zip", base_manifest(items=items1), files1))
        r2 = ingest_zip(self.store, make_zip(self.root / "in/b.zip", base_manifest(exported_at="2026-10-07T00:00:00Z", items=items2), files2))
        return r1.run_id, r2.run_id

    def test_first_run_everything_new(self):
        r1, _ = self.run_two([A1, PDF], V1, [A1, PDF], V1)
        cs = compute_changes(self.store, r1)
        self.assertIsNone(cs.prev_run_id)
        self.assertEqual(sorted(c.key for c in cs.new), ["456", "9001"])
        self.assertEqual(cs.changed, [])

    def test_meta_and_body_and_binary_and_removed_and_new(self):
        a1b = dict(A1, meta={"due_at": "2026-10-12", "points_possible": 10})
        v2 = {"Assignments/A1.html": b"<h1>A1</h1><p>Due Monday</p><p>Bring laptop</p>",
              "Extracted_Files/lec.pdf": b"pdf-v2!", "Assignments/A2.html": b"<h1>A2</h1>"}
        _, r2 = self.run_two([A1, PDF, GRADES], V1, [a1b, PDF, A2], v2)
        cs = compute_changes(self.store, r2)
        self.assertEqual([c.key for c in cs.new], ["457"])
        self.assertEqual([c.key for c in cs.removed], ["grades.csv"])
        by_key = {c.key: c for c in cs.changed}
        a1 = by_key["456"]
        self.assertEqual(a1.meta_diff, [("due_at", "2026-10-10", "2026-10-12")])
        self.assertEqual((a1.body_added, a1.body_removed), (2, 1))
        self.assertIn("+Due Monday", a1.body_diff)
        pdf = by_key["9001"]
        self.assertEqual(pdf.size_change, (3, 7))
        self.assertEqual(pdf.body_diff, "")

    def test_grades_changes_are_per_assignment(self):
        v2 = dict(V1, **{"Grades.csv": b"Assignment,Score\nA1,9\n"})
        _, r2 = self.run_two([A1, GRADES], V1, [A1, GRADES], v2)
        cs = compute_changes(self.store, r2)
        g = cs.changed[0]
        self.assertEqual(g.key, "grades.csv")
        self.assertEqual(g.grade_changes, [("A1", "—", "9")])
        self.assertEqual(g.body_diff, "")

    def test_module_reorder_reported(self):
        mod2 = dict(MOD, meta={"position": 1, "items": list(reversed(MOD["meta"]["items"]))})
        _, r2 = self.run_two([MOD], {}, [mod2], {})
        cs = compute_changes(self.store, r2)
        self.assertEqual(cs.changed[0].meta_diff, [("items", "order changed", "a, b → b, a")])

    def test_title_change_is_meta_change(self):
        a1b = dict(A1, title="A1 (updated)", path="Assignments/A1 (updated).html")
        v2 = {"Assignments/A1 (updated).html": V1["Assignments/A1.html"]}
        _, r2 = self.run_two([A1], V1, [a1b], v2)
        cs = compute_changes(self.store, r2)
        self.assertEqual(cs.new, [])
        self.assertEqual(cs.removed, [])
        self.assertEqual(cs.changed[0].meta_diff, [("title", "A1", "A1 (updated)")])
```

- [ ] **Step 2: Run to verify failure**

```bash
cd sync && python -m unittest tests.test_changes -v
```

Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement**

`sync/canvas_sync/changes.py`:

```python
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
            ch.body_diff, ch.body_added, ch.body_removed = unified(
                html_to_text(_decode(old_b)) if cur["path"].lower().endswith((".html", ".htm")) else _decode(old_b),
                html_to_text(_decode(new_b)) if cur["path"].lower().endswith((".html", ".htm")) else _decode(new_b),
                "previous", "current")
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
```

- [ ] **Step 4: Run tests, commit**

```bash
cd sync && python -m unittest discover -s tests -v
git add sync/canvas_sync/changes.py sync/tests/test_changes.py
git commit -m "feat(sync): compute new/changed/removed items with meta, body and grade diffs

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Markdown report and notification summary

**Files:**
- Create: `sync/canvas_sync/report.py`, `sync/tests/test_report.py`
- Modify: `sync/canvas_sync/ingest.py` (`_after_commit` hook)

**Interfaces:**
- Consumes: `ChangeSet`, `ItemChange`, `compute_changes`.
- Produces: `render_report(cs: ChangeSet) -> str`; `one_line(cs: ChangeSet) -> str` (e.g. `XLS5C202609: 1 new, 2 changed, 0 removed`); `write_report(store, cs) -> Path` saving to `reports/<course_id>/<stamp>_run<id>.md` and recording `runs.report_path`.

- [ ] **Step 1: Write the failing test**

`sync/tests/test_report.py`:

```python
from canvas_sync.changes import ChangeSet, ItemChange
from canvas_sync.report import render_report, one_line, write_report
from canvas_sync.ingest import ingest_zip
from tests.helpers import TempStore, base_manifest, doc_item, make_zip


def sample():
    cs = ChangeSet("133044", "XLS5C202609", 2, "2026-10-06T20:31:00Z", 1, "2026-10-06T14:41:00Z")
    cs.new.append(ItemChange("new", "assignment", "457", "Assignment 2", "Assignments/Assignment 2.html",
                             meta={"due_at": "2026-10-20T06:59:00Z", "points_possible": 15}))
    cs.changed.append(ItemChange("changed", "assignment", "456", "Assignment 1", "Assignments/Assignment 1.html",
                                 meta_diff=[("due_at", "2026-10-10T06:59:00Z", "2026-10-12T06:59:00Z")],
                                 body_diff="--- previous\n+++ current\n@@ -1 +1,2 @@\n-Due Friday\n+Due Monday\n+Bring laptop",
                                 body_added=2, body_removed=1))
    cs.changed.append(ItemChange("changed", "synthetic", "grades.csv", "Grades.csv", "Grades.csv",
                                 grade_changes=[("Assignment 0", "—", "9")]))
    cs.changed.append(ItemChange("changed", "file", "9001", "lec.pdf", "Extracted_Files/lec.pdf", size_change=(3, 7)))
    return cs


class ReportTests(TempStore):
    def test_render(self):
        md = render_report(sample())
        self.assertTrue(md.startswith("# XLS5C202609 — 2026-10-06 20:31 (vs 2026-10-06 14:41)"))
        self.assertIn("## New (1)\n- assignment  Assignment 2 — due 2026-10-20, 15 pts", md)
        self.assertIn("- assignment  Assignment 1 — due_at 2026-10-10 → 2026-10-12; body +2 −1 lines", md)
        self.assertIn("- grades      Assignment 0 — score — → 9", md)
        self.assertIn("- file        lec.pdf — content changed (3 B → 7 B)", md)
        self.assertIn("## Removed (0)", md)
        self.assertIn("<details><summary>Assignment 1 body diff</summary>", md)
        self.assertIn("```diff\n--- previous", md)

    def test_first_run_note(self):
        cs = ChangeSet("1", "C", 1, "2026-10-06T20:31:00Z", None, None)
        md = render_report(cs)
        self.assertIn("(first export, no baseline)", md)

    def test_one_line(self):
        self.assertEqual(one_line(sample()), "XLS5C202609: 1 new, 3 changed, 0 removed")

    def test_write_report_records_path(self):
        res = ingest_zip(self.store, make_zip(self.root / "in/a.zip", base_manifest(items=[
            doc_item("assignment", "1", "Assignments/A.html", "A")]), {"Assignments/A.html": b"x"}))
        row = self.store.db.execute("select report_path from runs where id=?", (res.run_id,)).fetchone()
        self.assertIsNotNone(row["report_path"])
        self.assertTrue(row["report_path"].endswith(".md"))
        self.assertIn("1 new", res.summary)
```

- [ ] **Step 2: Run to verify failure**

```bash
cd sync && python -m unittest tests.test_report -v
```

Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement**

`sync/canvas_sync/report.py`:

```python
"""Markdown change report (spec Part D)."""
from __future__ import annotations

from pathlib import Path

from .changes import ChangeSet, ItemChange
from .store import Store

_TYPE_LABEL = {"synthetic": "generated"}
_COL = 11


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
```

Then wire the hook in `sync/canvas_sync/ingest.py`: replace the `_after_commit` stub body with:

```python
def _after_commit(store: Store, run_id: int, warnings: list[str]) -> tuple[Path | None, str]:
    from .changes import compute_changes
    from .report import one_line, write_report
    cs = compute_changes(store, run_id)
    path = write_report(store, cs)
    return path, one_line(cs)
```

- [ ] **Step 4: Run tests, commit**

```bash
cd sync && python -m unittest discover -s tests -v
git add sync/canvas_sync/report.py sync/tests/test_report.py sync/canvas_sync/ingest.py
git commit -m "feat(sync): markdown change report and ingest summary

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: Materialize `latest/` and `checkout`

**Files:**
- Create: `sync/canvas_sync/materialize.py`, `sync/tests/test_materialize.py`
- Modify: `sync/canvas_sync/ingest.py` (`_after_commit`)

**Interfaces:**
- Consumes: `runs.snapshot_path` JSON `{"files": {path: sha}}`, `Store.blob_path`.
- Produces: `materialize_run(store, run_id: int, dest: Path, clean: bool = True) -> int` (files written); `rebuild_latest(store, course_id: str) -> Path` which materialises the course's latest run into `latest/<safe course name>/`, removing any previous `latest/` directory of that course (tracked in a small `latest/.index.json` mapping course_id → dir name).

- [ ] **Step 1: Write the failing test**

`sync/tests/test_materialize.py`:

```python
import json, os
from canvas_sync.ingest import ingest_zip
from canvas_sync.materialize import materialize_run, rebuild_latest
from tests.helpers import TempStore, base_manifest, doc_item, file_item, make_zip

A1 = doc_item("assignment", "456", "Assignments/A1.html", "A1")
PDF = file_item("9001", "Extracted_Files/lec.pdf", 3)
V1 = {"Assignments/A1.html": b"<h1>A1</h1>", "Extracted_Files/lec.pdf": b"pdf"}


class MaterializeTests(TempStore):
    def test_checkout_writes_tree(self):
        res = ingest_zip(self.store, make_zip(self.root / "in/a.zip", base_manifest(items=[A1, PDF]), V1))
        dest = self.root / "out"
        n = materialize_run(self.store, res.run_id, dest)
        self.assertEqual(n, 2)
        self.assertEqual((dest / "Assignments/A1.html").read_bytes(), b"<h1>A1</h1>")
        self.assertEqual((dest / "Extracted_Files/lec.pdf").read_bytes(), b"pdf")

    def test_latest_follows_newest_run_and_drops_old_paths(self):
        ingest_zip(self.store, make_zip(self.root / "in/a.zip", base_manifest(items=[A1, PDF]), V1))
        self.assertTrue((self.store.latest / "XLS5C202609" / "Extracted_Files/lec.pdf").exists())
        ingest_zip(self.store, make_zip(self.root / "in/b.zip",
                                        base_manifest(exported_at="2026-10-07T00:00:00Z", items=[A1]),
                                        {"Assignments/A1.html": b"<h1>A1 v2</h1>"}))
        tree = self.store.latest / "XLS5C202609"
        self.assertFalse((tree / "Extracted_Files/lec.pdf").exists())
        self.assertEqual((tree / "Assignments/A1.html").read_bytes(), b"<h1>A1 v2</h1>")

    def test_course_rename_moves_latest(self):
        ingest_zip(self.store, make_zip(self.root / "in/a.zip", base_manifest(items=[A1]), V1))
        ingest_zip(self.store, make_zip(self.root / "in/b.zip",
                                        base_manifest(exported_at="2026-10-07T00:00:00Z", items=[A1], course_name="CMPT 487"), V1))
        self.assertFalse((self.store.latest / "XLS5C202609").exists())
        self.assertTrue((self.store.latest / "CMPT 487" / "Assignments/A1.html").exists())

    def test_hardlink_when_possible(self):
        res = ingest_zip(self.store, make_zip(self.root / "in/a.zip", base_manifest(items=[PDF]), V1))
        f = self.store.latest / "XLS5C202609" / "Extracted_Files/lec.pdf"
        sha = json.loads(open(self.store.db.execute("select snapshot_path from runs").fetchone()[0]).read())["files"]["Extracted_Files/lec.pdf"]
        self.assertEqual(os.stat(f).st_ino, os.stat(self.store.blob_path(sha)).st_ino)
```

- [ ] **Step 2: Run to verify failure**

```bash
cd sync && python -m unittest tests.test_materialize -v
```

Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement**

`sync/canvas_sync/materialize.py`:

```python
"""Materialise a run's tree from blobs: `latest/<course>/` and `checkout` (spec Part B/E)."""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from .store import Store

_INDEX = ".index.json"


def _safe_dir(name: str) -> str:
    cleaned = re.sub(r'[/\\?%*:|"<>\x00-\x1f]', "-", name).strip(" .")
    return cleaned or "course"


def _link_or_copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() or dest.is_symlink():
        dest.unlink()
    try:
        os.link(src, dest)
    except OSError:
        shutil.copy2(src, dest)


def materialize_run(store: Store, run_id: int, dest: Path, clean: bool = True) -> int:
    row = store.db.execute("select snapshot_path from runs where id=?", (run_id,)).fetchone()
    if row is None or not row["snapshot_path"]:
        raise ValueError(f"run {run_id} has no snapshot")
    files: dict[str, str] = json.loads(Path(row["snapshot_path"]).read_text())["files"]
    dest = Path(dest)
    if clean and dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for rel, sha in files.items():
        target = (dest / rel).resolve()
        if dest.resolve() not in target.parents:
            continue  # refuse paths escaping dest
        _link_or_copy(store.blob_path(sha), target)
    return len(files)


def _load_index(store: Store) -> dict:
    p = store.latest / _INDEX
    return json.loads(p.read_text()) if p.exists() else {}


def _save_index(store: Store, idx: dict) -> None:
    (store.latest / _INDEX).write_text(json.dumps(idx, indent=1))


def rebuild_latest(store: Store, course_id: str) -> Path:
    run = store.db.execute(
        "select r.id, c.name from runs r join courses c on c.id=r.course_id where r.course_id=? order by r.id desc limit 1",
        (course_id,)).fetchone()
    if run is None:
        raise ValueError(f"no runs for course {course_id}")
    idx = _load_index(store)
    new_dir = _safe_dir(run["name"])
    old_dir = idx.get(course_id)
    if old_dir and old_dir != new_dir and (store.latest / old_dir).exists():
        shutil.rmtree(store.latest / old_dir)
    dest = store.latest / new_dir
    materialize_run(store, run["id"], dest, clean=True)
    idx[course_id] = new_dir
    _save_index(store, idx)
    return dest
```

Wire into `ingest.py` `_after_commit`, after `write_report`:

```python
    from .materialize import rebuild_latest
    rebuild_latest(store, cs.course_id)
```

- [ ] **Step 4: Run tests, commit**

```bash
cd sync && python -m unittest discover -s tests -v
git add sync/canvas_sync/materialize.py sync/tests/test_materialize.py sync/canvas_sync/ingest.py
git commit -m "feat(sync): materialize latest/ tree and checkout any run

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 12: verify and gc

**Files:**
- Create: `sync/canvas_sync/verify.py`, `sync/tests/test_verify.py`

**Interfaces:**
- Produces: `verify(store) -> list[str]` (problem descriptions, empty when clean); `gc(store) -> int` (blobs deleted).

- [ ] **Step 1: Write the failing test**

`sync/tests/test_verify.py`:

```python
from canvas_sync.ingest import ingest_zip
from canvas_sync.verify import verify, gc
from tests.helpers import TempStore, base_manifest, doc_item, make_zip

A1 = doc_item("assignment", "456", "Assignments/A1.html", "A1")


class VerifyTests(TempStore):
    def test_clean_store_verifies(self):
        ingest_zip(self.store, make_zip(self.root / "in/a.zip", base_manifest(items=[A1]), {"Assignments/A1.html": b"x"}))
        self.assertEqual(verify(self.store), [])

    def test_corrupt_blob_detected(self):
        ingest_zip(self.store, make_zip(self.root / "in/a.zip", base_manifest(items=[A1]), {"Assignments/A1.html": b"x"}))
        sha = self.store.db.execute("select sha256 from versions").fetchone()[0]
        self.store.blob_path(sha).write_bytes(b"corrupted")
        problems = verify(self.store)
        self.assertEqual(len(problems), 1)
        self.assertIn("hash mismatch", problems[0])

    def test_missing_blob_detected(self):
        ingest_zip(self.store, make_zip(self.root / "in/a.zip", base_manifest(items=[A1]), {"Assignments/A1.html": b"x"}))
        sha = self.store.db.execute("select sha256 from versions").fetchone()[0]
        self.store.blob_path(sha).unlink()
        self.assertTrue(any("missing blob" in p for p in verify(self.store)))

    def test_gc_removes_unreferenced(self):
        ingest_zip(self.store, make_zip(self.root / "in/a.zip", base_manifest(items=[A1]), {"Assignments/A1.html": b"x"}))
        orphan = self.store.put_blob(b"orphan")
        self.assertEqual(gc(self.store), 1)
        self.assertFalse(self.store.has_blob(orphan))
        self.assertEqual(verify(self.store), [])
```

- [ ] **Step 2: Run to verify failure**

```bash
cd sync && python -m unittest tests.test_verify -v
```

- [ ] **Step 3: Implement**

`sync/canvas_sync/verify.py`:

```python
"""Integrity check and garbage collection (spec Part E)."""
from __future__ import annotations

from .store import Store, sha256_bytes


def verify(store: Store) -> list[str]:
    problems: list[str] = []
    referenced = {r[0] for r in store.db.execute("select distinct sha256 from versions where sha256 is not null")}
    for sha in sorted(referenced):
        p = store.blob_path(sha)
        if not p.is_file():
            problems.append(f"missing blob {sha}")
            continue
        actual = sha256_bytes(p.read_bytes())
        if actual != sha:
            problems.append(f"hash mismatch {sha}: on disk {actual}")
    for sub in store.objects.iterdir():
        if sub.name == "tmp" or not sub.is_dir():
            continue
        for p in sub.iterdir():
            if p.name not in referenced and not store.db.execute(
                    "select 1 from blobs where sha256=?", (p.name,)).fetchone():
                problems.append(f"untracked file {p}")
    return problems


def gc(store: Store) -> int:
    referenced = {r[0] for r in store.db.execute("select distinct sha256 from versions where sha256 is not null")}
    removed = 0
    for sub in store.objects.iterdir():
        if sub.name == "tmp" or not sub.is_dir():
            continue
        for p in list(sub.iterdir()):
            if p.name not in referenced:
                p.unlink()
                store.db.execute("delete from blobs where sha256=?", (p.name,))
                removed += 1
    for p in store.tmp.iterdir():
        p.unlink()
    store.db.commit()
    return removed
```

- [ ] **Step 4: Run tests, commit**

```bash
cd sync && python -m unittest discover -s tests -v
git add sync/canvas_sync/verify.py sync/tests/test_verify.py
git commit -m "feat(sync): verify blob integrity and gc orphans

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 13: CLI

**Files:**
- Create: `sync/canvas_sync/cli.py`, `sync/canvas_sync/__main__.py`, `sync/tests/test_cli.py`

**Interfaces:**
- Produces: `main(argv: list[str] | None = None) -> int`. Subcommands: `ingest <path>...`, `ingest-all [dir]`, `status`, `log <course> [--item TYPE:KEY]`, `diff <course> <TYPE:KEY> <runA> <runB>`, `checkout <course> <run> <dir>`, `verify`, `gc`, `install-units`. Global `--root`. `ingest`/`ingest-all` take `--delete` (do not keep ZIP) and `--notify`.

- [ ] **Step 1: Write the failing test**

`sync/tests/test_cli.py`:

```python
import io, contextlib
from canvas_sync.cli import main
from tests.helpers import TempStore, base_manifest, doc_item, make_zip

A1 = doc_item("assignment", "456", "Assignments/A1.html", "A1", {"due_at": "2026-10-10"})


class CliTests(TempStore):
    def run_cli(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["--root", str(self.root), *args])
        return code, buf.getvalue()

    def test_ingest_and_status(self):
        make_zip(self.root / "in/a.zip", base_manifest(items=[A1]), {"Assignments/A1.html": b"x"})
        code, out = self.run_cli("ingest", str(self.root / "in/a.zip"))
        self.assertEqual(code, 0)
        self.assertIn("XLS5C202609: 1 new, 0 changed, 0 removed", out)
        code, out = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("XLS5C202609", out)
        self.assertIn("runs: 1", out)

    def test_log_and_diff(self):
        make_zip(self.root / "in/a.zip", base_manifest(items=[A1]), {"Assignments/A1.html": b"<p>one</p>"})
        self.run_cli("ingest", str(self.root / "in/a.zip"))
        make_zip(self.root / "in/b.zip", base_manifest(exported_at="2026-10-07T00:00:00Z", items=[A1]), {"Assignments/A1.html": b"<p>two</p>"})
        self.run_cli("ingest", str(self.root / "in/b.zip"))
        code, out = self.run_cli("log", "133044")
        self.assertIn("run 1", out); self.assertIn("run 2", out)
        code, out = self.run_cli("log", "133044", "--item", "assignment:456")
        self.assertEqual(out.count("Assignments/A1.html"), 2)
        code, out = self.run_cli("diff", "133044", "assignment:456", "1", "2")
        self.assertEqual(code, 0)
        self.assertIn("-one", out); self.assertIn("+two", out)

    def test_checkout_verify_gc(self):
        make_zip(self.root / "in/a.zip", base_manifest(items=[A1]), {"Assignments/A1.html": b"x"})
        self.run_cli("ingest", str(self.root / "in/a.zip"))
        code, _ = self.run_cli("checkout", "133044", "1", str(self.root / "co"))
        self.assertEqual(code, 0)
        self.assertTrue((self.root / "co/Assignments/A1.html").exists())
        self.assertEqual(self.run_cli("verify")[0], 0)
        code, out = self.run_cli("gc")
        self.assertIn("removed 0", out)

    def test_verify_nonzero_on_problem(self):
        make_zip(self.root / "in/a.zip", base_manifest(items=[A1]), {"Assignments/A1.html": b"x"})
        self.run_cli("ingest", str(self.root / "in/a.zip"))
        sha = self.store.db.execute("select sha256 from versions").fetchone()[0]
        self.store.blob_path(sha).write_bytes(b"bad")
        code, out = self.run_cli("verify")
        self.assertEqual(code, 1)
        self.assertIn("hash mismatch", out)

    def test_bad_manifest_is_reported_not_traceback(self):
        make_zip(self.root / "in/old.zip", {"course": "X"}, {})
        code, out = self.run_cli("ingest", str(self.root / "in/old.zip"))
        self.assertEqual(code, 2)
        self.assertIn("schema 2", out)
```

- [ ] **Step 2: Run to verify failure**

```bash
cd sync && python -m unittest tests.test_cli -v
```

- [ ] **Step 3: Implement**

`sync/canvas_sync/__main__.py`:

```python
import sys
from .cli import main

sys.exit(main())
```

`sync/canvas_sync/cli.py`:

```python
"""canvas-sync command line."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__
from .changes import _change_for, compute_changes
from .ingest import ingest_dir, ingest_zip
from .manifest import ManifestError
from .materialize import materialize_run
from .report import render_report
from .store import Store
from .verify import gc, verify

DEFAULT_INBOX = Path.home() / "Downloads" / "CanvasExports"


def _course_rows(store: Store):
    return store.db.execute(
        "select c.id, c.name, count(r.id) as runs, max(r.exported_at) as last "
        "from courses c left join runs r on r.course_id=c.id group by c.id order by c.name").fetchall()


def cmd_ingest(store: Store, a) -> int:
    code = 0
    for p in a.paths:
        try:
            res = ingest_zip(store, Path(p), keep=not a.delete, notify=a.notify)
        except ManifestError as e:
            print(f"{p}: {e}")
            code = 2
            continue
        print(res.summary if not res.skipped else f"{Path(p).name}: skipped — {res.reason}")
        if res.report_path:
            print(f"  report: {res.report_path}")
    return code


def cmd_ingest_all(store: Store, a) -> int:
    d = Path(a.dir).expanduser()
    if not d.is_dir():
        print(f"{d}: not a directory")
        return 2
    code = 0
    for p in sorted(d.glob("*.zip")):
        try:
            res = ingest_zip(store, p, keep=not a.delete, notify=a.notify)
        except ManifestError as e:
            print(f"{p.name}: {e}")
            code = 2
            continue
        print(res.summary if not res.skipped else f"{p.name}: skipped — {res.reason}")
    return code


def cmd_status(store: Store, a) -> int:
    rows = _course_rows(store)
    if not rows:
        print("no courses ingested yet")
    for r in rows:
        print(f"{r['name']} ({r['id']}) — runs: {r['runs']}, last export: {r['last'] or '-'}")
    inbox = Path(a.inbox).expanduser()
    pending = sorted(inbox.glob("*.zip")) if inbox.is_dir() else []
    print(f"pending in {inbox}: {len(pending)}")
    return 0


def _course_id(store: Store, ref: str) -> str:
    row = store.db.execute("select id from courses where id=? or name=?", (ref, ref)).fetchone()
    if row is None:
        raise SystemExit(f"unknown course {ref!r}")
    return row["id"]


def cmd_log(store: Store, a) -> int:
    cid = _course_id(store, a.course)
    if a.item:
        type_, _, key = a.item.partition(":")
        rows = store.db.execute(
            "select v.id, v.run_id, r.exported_at, v.path, v.title, v.sha256, v.meta_json from versions v "
            "join items i on i.id=v.item_id join runs r on r.id=v.run_id "
            "where i.course_id=? and i.type=? and i.key=? order by v.id", (cid, type_, key)).fetchall()
        for v in rows:
            print(f"run {v['run_id']}  {v['exported_at']}  {v['path']}  {(v['sha256'] or '-')[:12]}  {v['meta_json']}")
        return 0
    for r in store.db.execute("select * from runs where course_id=? order by id", (cid,)):
        flag = "" if r["complete"] else "  (incomplete)"
        print(f"run {r['id']}  exported {r['exported_at']}  ingested {r['ingested_at']}{flag}  report: {r['report_path'] or '-'}")
    return 0


def cmd_diff(store: Store, a) -> int:
    cid = _course_id(store, a.course)
    type_, _, key = a.item.partition(":")
    item = store.db.execute("select id, type, key from items where course_id=? and type=? and key=?",
                            (cid, type_, key)).fetchone()
    if item is None:
        print(f"no such item {a.item}")
        return 2

    def version_at(run_id: int):
        return store.db.execute("select * from versions where item_id=? and run_id<=? order by id desc limit 1",
                                (item["id"], run_id)).fetchone()

    va, vb = version_at(int(a.run_a)), version_at(int(a.run_b))
    if va is None or vb is None:
        print("item has no version at one of those runs")
        return 2
    ch = _change_for(store, {"type": item["type"], "key": item["key"]}, dict(vb), dict(va))
    for k, o, n in ch.meta_diff:
        print(f"{k}: {o} → {n}")
    if ch.grade_changes:
        for name, o, n in ch.grade_changes:
            print(f"{name}: {o} → {n}")
    if ch.size_change:
        print(f"content changed ({ch.size_change[0]} → {ch.size_change[1]} bytes)")
    if ch.body_diff:
        print(ch.body_diff)
    if not (ch.meta_diff or ch.grade_changes or ch.size_change or ch.body_diff):
        print("no difference")
    return 0


def cmd_checkout(store: Store, a) -> int:
    cid = _course_id(store, a.course)
    run = store.db.execute("select id from runs where course_id=? and id=?", (cid, int(a.run))).fetchone()
    if run is None:
        print(f"run {a.run} not found for course {a.course}")
        return 2
    n = materialize_run(store, run["id"], Path(a.dir).expanduser(), clean=False)
    print(f"wrote {n} files to {a.dir}")
    return 0


def cmd_verify(store: Store, a) -> int:
    problems = verify(store)
    for p in problems:
        print(p)
    print("ok" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


def cmd_gc(store: Store, a) -> int:
    print(f"removed {gc(store)} unreferenced blob(s)")
    return 0


def cmd_install_units(store: Store, a) -> int:
    src = Path(__file__).resolve().parent.parent / "systemd"
    dest = Path.home() / ".config" / "systemd" / "user"
    dest.mkdir(parents=True, exist_ok=True)
    exe = shutil.which("canvas-sync") or f"{sys.executable} -m canvas_sync"
    for name in ("canvas-sync.path", "canvas-sync.service"):
        text = (src / name).read_text().replace("@CANVAS_SYNC@", exe)
        (dest / name).write_text(text)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    subprocess.run(["systemctl", "--user", "enable", "--now", "canvas-sync.path"], check=False)
    print(f"installed units to {dest}; watching {DEFAULT_INBOX}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="canvas-sync", description="Versioned archive for Canvas course exports")
    p.add_argument("--root", default=None, help="store root (default ~/CanvasArchive or $CANVAS_SYNC_ROOT)")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("ingest", help="ingest one or more ZIP exports")
    s.add_argument("paths", nargs="+")
    s.add_argument("--delete", action="store_true", help="delete ZIP after ingest instead of moving to processed/")
    s.add_argument("--notify", action="store_true", help="desktop notification via notify-send")
    s.set_defaults(fn=cmd_ingest)

    s = sub.add_parser("ingest-all", help="ingest every ZIP in a directory")
    s.add_argument("dir", nargs="?", default=str(DEFAULT_INBOX))
    s.add_argument("--delete", action="store_true")
    s.add_argument("--notify", action="store_true")
    s.set_defaults(fn=cmd_ingest_all)

    s = sub.add_parser("status"); s.add_argument("--inbox", default=str(DEFAULT_INBOX)); s.set_defaults(fn=cmd_status)
    s = sub.add_parser("log"); s.add_argument("course"); s.add_argument("--item"); s.set_defaults(fn=cmd_log)
    s = sub.add_parser("diff"); s.add_argument("course"); s.add_argument("item"); s.add_argument("run_a"); s.add_argument("run_b"); s.set_defaults(fn=cmd_diff)
    s = sub.add_parser("checkout"); s.add_argument("course"); s.add_argument("run"); s.add_argument("dir"); s.set_defaults(fn=cmd_checkout)
    sub.add_parser("verify").set_defaults(fn=cmd_verify)
    sub.add_parser("gc").set_defaults(fn=cmd_gc)
    sub.add_parser("install-units").set_defaults(fn=cmd_install_units)
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    store = Store(a.root or Store.default_root())
    try:
        return a.fn(store, a)
    finally:
        store.close()
```

- [ ] **Step 4: Run tests**

```bash
cd sync && python -m unittest discover -s tests -v
```

Expected: all pass. Also `cd sync && python -m canvas_sync --help` prints usage.

- [ ] **Step 5: Commit**

```bash
git add sync/canvas_sync/cli.py sync/canvas_sync/__main__.py sync/tests/test_cli.py
git commit -m "feat(sync): canvas-sync CLI (ingest, status, log, diff, checkout, verify, gc)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 14: systemd units and install

**Files:**
- Create: `sync/systemd/canvas-sync.path`, `sync/systemd/canvas-sync.service`
- Modify: `sync/tests/test_cli.py` (unit-file smoke test)

- [ ] **Step 1: Write the failing test**

Append to `sync/tests/test_cli.py`:

```python
from pathlib import Path as _P

class UnitFileTests(TempStore):
    def test_unit_files_present_and_templated(self):
        d = _P(__file__).resolve().parent.parent / "systemd"
        path_unit = (d / "canvas-sync.path").read_text()
        svc = (d / "canvas-sync.service").read_text()
        self.assertIn("PathChanged=%h/Downloads/CanvasExports", path_unit)
        self.assertIn("ExecStart=@CANVAS_SYNC@ ingest-all --notify", svc)
        self.assertIn("Type=oneshot", svc)
```

- [ ] **Step 2: Run to verify failure**

```bash
cd sync && python -m unittest tests.test_cli.UnitFileTests -v
```

Expected: FileNotFoundError.

- [ ] **Step 3: Create units**

`sync/systemd/canvas-sync.path`:

```ini
[Unit]
Description=Watch ~/Downloads/CanvasExports for Canvas course exports

[Path]
PathChanged=%h/Downloads/CanvasExports
MakeDirectory=yes
Unit=canvas-sync.service

[Install]
WantedBy=default.target
```

`sync/systemd/canvas-sync.service`:

```ini
[Unit]
Description=Ingest Canvas course exports into the versioned archive

[Service]
Type=oneshot
# @CANVAS_SYNC@ is replaced by `canvas-sync install-units` with the real executable.
ExecStart=@CANVAS_SYNC@ ingest-all --notify
# Firefox writes <name>.zip.part then renames; PathChanged fires on close, but a
# second event can arrive mid-write. ingest-all leaves unreadable ZIPs in place
# and the next event retries them, so no special handling here.
```

- [ ] **Step 4: Run tests**

```bash
cd sync && python -m unittest discover -s tests -v
```

- [ ] **Step 5: Install for real and check**

```bash
cd sync && pip install --user -e . && canvas-sync install-units && systemctl --user status canvas-sync.path --no-pager
```

Expected: `active (waiting)`.

- [ ] **Step 6: Commit**

```bash
git add sync/systemd sync/tests/test_cli.py
git commit -m "feat(sync): systemd user path unit watching Downloads/CanvasExports

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 15: End-to-end on the real course and docs

**Files:**
- Modify: `README.md` (new section "Versioned archive (canvas-sync)"), `CLAUDE.md` (Architecture: add `sync/`; Known issues: ZIP mode result)

- [ ] **Step 1: Export twice**

In Firefox export course 133044 (ZIP lands in `~/Downloads/CanvasExports/`). Wait for the notification. Then:

```bash
canvas-sync status && canvas-sync log 133044 && cat ~/CanvasArchive/reports/133044/*.md | head -40
```

Expected: 1 run, report lists everything under New, `latest/XLS5C202609/` is browsable and `Quizzes/Topic 4 Quiz/Topic 4 Quiz.html` shows images from `../../Extracted_Files/`.

Export again. Expected second report: `New (0) Changed (0) Removed (0)` unless Canvas changed; notification says `XLS5C202609: 0 new, 0 changed, 0 removed`.

- [ ] **Step 2: Verify store**

```bash
canvas-sync verify && du -sh ~/CanvasArchive/objects
```

Expected: `ok`; objects size ≈ one export (second run added no blobs).

- [ ] **Step 3: Document**

Append to `README.md`:

```markdown
## Versioned archive (canvas-sync)

`sync/` contains an optional, dependency-free Python tool that ingests this extension's
ZIP exports into a content-addressed archive and reports what changed between exports.

    cd sync && pip install --user -e .
    canvas-sync install-units        # watch ~/Downloads/CanvasExports (systemd --user)
    canvas-sync status | log <course> | diff <course> type:id runA runB | checkout <course> run dir | verify | gc

Set the extension to ZIP mode with folder prefix `CanvasExports`. Each export produces one
report under `~/CanvasArchive/reports/<courseId>/` and a browsable tree under
`~/CanvasArchive/latest/<course>/`.
```

Update `CLAUDE.md` Architecture with one bullet for `sync/` and the manifest schema 2, and Known issues with the Task 2 result. Commit:

```bash
git add README.md CLAUDE.md
git commit -m "docs: canvas-sync usage and schema-2 manifest

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review notes

- Spec coverage: A1 → Task 1; A2 → Task 2/2b; A3 → Tasks 3–4; Part B → Task 5 (+ `snapshot_path`, `report_path` columns added to `runs`, needed by E and D); Part C → Task 8 with D/latest hooks in 10–11; Part D → Tasks 7, 9, 10; Part E → Tasks 11, 12, 13; Part F → Task 14; Testing section → each task; end-to-end → Task 15.
- Deviation from spec, deliberate: a `versions` row is also created when `path` or `title` changes (not only sha/meta), so renames show up as a `title` meta change and `latest/` follows the new filename (Review Focus 4). Snapshot and report filenames carry `_run<id>` to avoid same-second collisions (Review Focus 2).
- Review Focus 1 → `test_corrupt_zip_left_in_place`, `test_ingest_dir_skips_part_files` (Task 8). Review Focus 2 → `test_same_second_exports_do_not_collide` (Task 8). Review Focus 3 → `test_course_rename_moves_latest` (Task 11). Review Focus 4 → `test_title_change_is_meta_change` (Task 9). Review Focus 5 → `test_grades_diff_handles_quoted_commas` (Task 7).
- Type consistency: `ItemChange`/`ChangeSet` fields used in `report.py` and `cli.py` match `changes.py`; `_after_commit` signature identical in Tasks 8, 10, 11; `make_zip(dest, manifest, files)` used identically in all tests.
