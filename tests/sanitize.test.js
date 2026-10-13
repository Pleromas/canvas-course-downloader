const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

vm.runInThisContext(fs.readFileSync(path.join(__dirname, "..", "sanitize.js"), "utf8"), { filename: "sanitize.js" });
vm.runInThisContext(fs.readFileSync(path.join(__dirname, "..", "helpers.js"), "utf8"), { filename: "helpers.js" });
const { sanitizePathSegment, sanitizeDownloadPath, sanitizeFilename, escapeHtml } = globalThis;

test("sanitizePathSegment maps exotic spaces, strips invisibles, replaces reserved chars, trims", () => {
  assert.equal(sanitizePathSegment("Screen Shot 1:2?.png "), "Screen Shot 1-2-.png");
  assert.equal(sanitizePathSegment("a​b\u0007c"), "abc");
  assert.equal(sanitizePathSegment("..hidden.."), "hidden");
  assert.equal(sanitizePathSegment("CON.txt"), "_CON.txt");
  assert.equal(sanitizePathSegment(""), "untitled");
  assert.equal(sanitizePathSegment("   "), "untitled");
});

test("sanitizeDownloadPath sanitizes every segment, drops empty ones, keeps structure", () => {
  assert.equal(sanitizeDownloadPath("CanvasExports/Course: A/Files/Week 1 /lec.pdf"), "CanvasExports/Course- A/Files/Week 1/lec.pdf");
  assert.equal(sanitizeDownloadPath("/leading//double/x.txt"), "leading/double/x.txt");
  assert.equal(sanitizeDownloadPath("a\\b\\c.md"), "a/b/c.md");
  assert.equal(sanitizeDownloadPath("../../etc/passwd"), "etc/passwd");
});

test("sanitizeFilename in helpers now shares the segment rules", () => {
  assert.equal(sanitizeFilename("Lab 2: notes."), "Lab 2- notes");
  assert.equal(sanitizeFilename(null), "untitled");
});

test("escapeHtml escapes the five HTML metacharacters", () => {
  assert.equal(escapeHtml(`<a href="x">Tom & Jerry's</a>`), "&lt;a href=&quot;x&quot;&gt;Tom &amp; Jerry&#39;s&lt;/a&gt;");
  assert.equal(escapeHtml(null), "");
  assert.equal(escapeHtml(42), "42");
});
