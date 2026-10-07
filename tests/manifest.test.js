const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

// helpers.js is a browser script with top-level function declarations. Evaluate it
// in this context (not a separate vm context) so the arrays/objects it returns share
// our realm and strict deepEqual works. Its DOM-touching functions are never called here.
const src = fs.readFileSync(path.join(__dirname, "..", "helpers.js"), "utf8");
vm.runInThisContext(src, { filename: "helpers.js" });
const buildManifestItems = globalThis.buildManifestItems;

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
