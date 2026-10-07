# Canvas Course Downloader — project brief for Claude Code

This repo is a fork of https://github.com/jasp-nerd/canvas-course-downloader (MIT, v2.11.0),
a browser extension that exports everything a student can see in a Canvas LMS course
(files, pages, assignments, quizzes, announcements, discussions, modules, syllabus,
grades CSV, own submissions) using the Canvas REST API via the user's existing login
session. No API token is involved. Goal of this fork: make it reliable on Firefox/Linux
first, then extend it (see Roadmap).

## Environment

- OS: Fedora Linux. Browser: Firefox. Repo lives at `~/Documents/Projects/Canvas-Downloader` (moved from `~/canvas-course-downloader` on 2026-10-06).
- Firefox loads the extension as a **temporary add-on** via
  `about:debugging#/runtime/this-firefox` → "Load Temporary Add-on…" → `manifest.json`.
  It is forgotten on browser restart; after any code change click **Reload** there.
- Canvas instance: `https://canvas.usask.ca` (University of Saskatchewan). Students
  cannot create API access tokens there, which is why the session-cookie approach of this
  extension is the only workable route. Example course id seen: 133044.
- Debugging: background-script console = `about:debugging` → **Inspect** next to the
  extension. Content-script logs (prefixed `[Canvas Downloader]`) = F12 console on the
  Canvas tab. The download panel's "Failed files" list shows `job.error` strings from
  `background.js`.

## Architecture (as of upstream main)

- `manifest.json` — MV3. Upstream declares `background.service_worker` only.
- `background.js` — download queue. One `chrome.downloads.download({url, filename,
  conflictAction})` call (~line 189). Tracks jobs, persists state, throttles
  (default 250 ms), handles retry/cancel, `onChanged` listener marks completion.
- `downloader.js` — content script. Fetches Canvas API, builds the file list, generates
  synthetic files (HTML pages, `styles.css`, `Grades.csv`, `manifest.json`,
  `_inaccessible_links.csv`) as `data:` URLs. Has a ZIP mode (`zipMode: true` by default
  in settings) using client-zip that fetches everything in-page and emits one
  `blob:` URL per course; it falls back to loose per-file downloads above
  `ZIP_MAX_TOTAL_BYTES` (1.5 GB) or when ZIP is disabled.
- `content.js`, `popup.*`, `options.*` — UI.
- `manifest.json` inside each export is **schema 2**: `items[]` with Canvas id, type, path,
  title, `meta` (due dates, points, module order…), plus `complete`/`failedPaths`. Built by
  `buildManifestItems()` in `helpers.js` (node test: `node --test tests/manifest.test.js`).
- `sync/` — `canvas-sync`, stdlib-only Python 3.11+ CLI. Ingests ZIP exports into
  `~/CanvasArchive/` (sha256 blobs + SQLite + per-run snapshot), writes a Markdown change
  report, rebuilds `latest/<course>/`, notifies via `notify-send`. systemd user path unit
  watches `~/Downloads/CanvasExports/`. Tests: `cd sync && python -m unittest discover -s tests`.
  Spec: `docs/superpowers/specs/2026-10-06-canvas-sync-design.md`. Settings presets: Full Archive etc.;
  Incremental mode skips already-downloaded items; filters can exclude videos.
- All network requests go only to the Canvas origin the page is on. Nothing is ever
  POSTed to Canvas; the extension is read-only against the account.

## Local changes already made (keep these; they are not upstream)

1. **`manifest.json`** — added `"scripts": ["background.js"]` next to
   `"service_worker"` under `background`. Firefox refuses to load MV3 service workers
   by default; with `scripts` it runs the background as an event page. Chrome ignores
   `scripts` and uses the worker.
2. **`background.js` ~line 189** — Firefox's `downloads.download()` rejects `data:`
   URLs from extensions ("Access denied for URL data:…"). Before the call we now
   convert `data:` → `blob:` with `fetch(url).then(r=>r.blob())` +
   `URL.createObjectURL`, guarded by `typeof URL.createObjectURL === "function"` so it
   is a no-op in Chrome's service worker. Blob URL is stored on `nextJob.blobUrl`
   (not yet revoked — TODO: `URL.revokeObjectURL` in the `onChanged` complete handler).
3. **`background.js` sanitizer** — `sanitizedName` now also maps exotic Unicode
   spaces (U+00A0, U+2000–200B, U+202F, U+205F, U+3000) to a normal space, strips
   control/invisible characters (U+0000–001F, U+007F–009F, U+200C–200F,
   U+2028–202E, U+2060–206F, U+FEFF), then applies the original
   `[/\\?%*:|"<>]` → `-` replacement, then `.trim()`. Cause: macOS screenshot
   filenames contain U+202F before "AM/PM" and Firefox rejects them as "illegal
   characters".

Result so far: a full course export on Firefox goes from 100/172 failures → 0.

## Known issues / next fixes

- ZIP mode on Firefox: **not yet verified** with the schema-2 manifest (2026-10-06). Export
  course 133044 once with ZIP mode on and folder prefix `CanvasExports`; if Firefox rejects
  the content-script `blob:` URL, apply Task 2b of
  `docs/superpowers/plans/2026-10-06-canvas-sync.md` (hand the Blob to the background).

- Sanitizer is applied only to the filename, not to each `path` segment. A module or
  folder name with trailing whitespace or U+202F will still fail. Apply the same
  sanitization per path component (split on `/`, sanitize, rejoin).
- Revoke blob URLs after download completes/fails to avoid leaking memory on big runs.
- `git pull` from upstream will conflict with changes 1–3; keep them in a branch
  (`firefox-support`) and rebase.
- Panopto-hosted lecture videos and other LTI/external-tool content are not reachable
  via the Canvas API and are not exported.
- Files linked from *other* courses the user can't access show up in
  `_inaccessible_links.csv` with HTTP 403 — expected, not a bug.

## Conventions

- Plain JS, no build step, no bundler. Keep it that way so the temporary-add-on
  workflow stays zero-config.
- Any change must remain Chrome-compatible; guard Firefox-specific behavior with
  feature detection, not user-agent sniffing.
- Never add telemetry, external hosts, or any write operation against Canvas.
- Prefer small, upstreamable commits with clear messages; the first three local
  changes are a good candidate PR titled "Firefox support".

## Roadmap (owner's ambitions — refine with the owner)

- Rock-solid Firefox support, then submit upstream PR.
- Package a signed Firefox XPI so it survives restarts (AMO unlisted self-distribution
  via `web-ext sign`).
- Scheduled / one-click "sync all active courses" with incremental diffs.
- Post-processing: unzip into a stable local tree, convert exported HTML to Markdown,
  build an index/search over everything (Obsidian-friendly vault layout).
- Better capture of own submissions + instructor feedback/comments + rubric scores.
- Optional CLI companion (Node/Python) that reads the exported manifest.json files.
