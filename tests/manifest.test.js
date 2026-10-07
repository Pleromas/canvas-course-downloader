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

test("manifestKey overrides resourceId for identity (pages keyed by page_id, slug stays in meta)", () => {
  const [item] = buildManifestItems([
    { resourceType: "page", resourceId: "week-1-intro", manifestKey: "88123", path: "Pages/", filename: "week-1-intro.html",
      title: "Week 1 Intro", meta: { slug: "week-1-intro" } },
  ], []);
  assert.equal(item.canvasId, "88123");
  assert.equal(item.meta.slug, "week-1-intro");
});

test("exportedItemTypes maps enabled content types to manifest item types", () => {
  const exportedItemTypes = globalThis.exportedItemTypes;
  const all = { files: true, pages: true, assignments: true, submissions: true, discussions: true,
    announcements: true, modules: true, syllabus: true, grades: true, quizzes: true, linkedFiles: true };
  assert.deepEqual(exportedItemTypes(all).sort(),
    ["announcement", "assignment", "discussion", "file", "media", "module", "page", "quiz", "submission", "synthetic"]);
  const some = { ...all, quizzes: false, files: false, linkedFiles: false };
  const t = exportedItemTypes(some);
  assert.ok(!t.includes("quiz"));
  assert.ok(!t.includes("file"));
  assert.ok(!t.includes("media"));
  assert.ok(!t.includes("synthetic"), "synthetic docs only count as fully covered when every type is on");
  // files on, linkedFiles off still exports files
  assert.ok(exportedItemTypes({ ...all, linkedFiles: false }).includes("file"));
});
