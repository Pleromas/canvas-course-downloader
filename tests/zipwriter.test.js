const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { execFileSync } = require("node:child_process");

vm.runInThisContext(fs.readFileSync(path.join(__dirname, "..", "zipwriter.js"), "utf8"), { filename: "zipwriter.js" });
const { createZipWriter, crc32 } = globalThis;

const le32 = (buf, off) => buf.readUInt32LE(off);
const le16 = (buf, off) => buf.readUInt16LE(off);

test("crc32 matches the standard polynomial", () => {
  assert.equal(crc32(new TextEncoder().encode("hello")), 0x3610a686);
  assert.equal(crc32(new Uint8Array(0)), 0);
});

test("writes a valid store-only archive with central directory", async () => {
  const w = createZipWriter();
  const date = new Date(2026, 9, 6, 20, 31, 0);
  w.add("Assignments/A1.html", new TextEncoder().encode("hello"), date);
  w.add("Files/é.pdf", new Uint8Array([1, 2, 3]), date);
  const blob = w.finish();
  const buf = Buffer.from(await blob.arrayBuffer());

  // Local file header for entry 1
  assert.equal(le32(buf, 0), 0x04034b50);
  assert.equal(le16(buf, 6), 0x0800, "UTF-8 flag set, no data descriptor");
  assert.equal(le16(buf, 8), 0, "method store");
  assert.equal(le32(buf, 14), 0x3610a686, "crc of hello");
  assert.equal(le32(buf, 18), 5);
  assert.equal(le32(buf, 22), 5);
  const nameLen = le16(buf, 26);
  assert.equal(buf.subarray(30, 30 + nameLen).toString("utf8"), "Assignments/A1.html");
  assert.equal(buf.subarray(30 + nameLen, 30 + nameLen + 5).toString(), "hello");

  // End of central directory record is the last 22 bytes
  const eocd = buf.length - 22;
  assert.equal(le32(buf, eocd), 0x06054b50);
  assert.equal(le16(buf, eocd + 10), 2, "two entries");
  const cdSize = le32(buf, eocd + 12);
  const cdOffset = le32(buf, eocd + 16);
  assert.equal(le32(buf, cdOffset), 0x02014b50);
  assert.equal(cdOffset + cdSize, eocd);
  assert.equal(blob.type, "application/zip");
});

test("python zipfile can read and verify the archive", async () => {
  const w = createZipWriter();
  w.add("a/b.txt", new TextEncoder().encode("one"), new Date());
  w.add("é.bin", new Uint8Array(70000).fill(7), new Date());
  const buf = Buffer.from(await w.finish().arrayBuffer());
  const tmp = path.join(require("node:os").tmpdir(), `zw-${process.pid}.zip`);
  fs.writeFileSync(tmp, buf);
  try {
    const out = execFileSync("python3", ["-I", "-c",
      "import sys,zipfile;z=zipfile.ZipFile(sys.argv[1]);print(z.testzip());print(sorted(z.namelist()));print(z.read('a/b.txt').decode(), len(z.read('é.bin')))",
      tmp]).toString();
    assert.equal(out.trim().split("\n")[0], "None", "testzip found no bad CRC");
    assert.match(out, /\['a\/b.txt', 'é.bin'\]/);
    assert.match(out, /one 70000/);
  } finally {
    fs.unlinkSync(tmp);
  }
});

test("refuses empty names and strips leading slashes", () => {
  const w = createZipWriter();
  assert.throws(() => w.add("", new Uint8Array(0)), /name/);
  w.add("/leading.txt", new Uint8Array(0));
  assert.deepEqual(w.names(), ["leading.txt"]);
});
