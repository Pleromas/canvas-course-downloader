/**
 * Canvas Course Downloader — minimal ZIP writer
 *
 * Store-only (no compression) ZIP archive builder that works inside a Firefox
 * content script. client-zip could not be used there: it produces its output
 * as a `ReadableStream`, and Firefox refuses to let the page-realm stream
 * machinery read objects created in the content-script sandbox
 * ("Permission denied to access property 'autoAllocateChunkSize'").
 *
 * This writer touches no streams at all: every entry is a Uint8Array, the
 * archive is assembled from byte parts and returned as a Blob. Course files
 * are already compressed formats (PDF, ZIP, MP4, PNG), so storing them is
 * the right trade anyway. No ZIP64: callers cap total size at
 * ZIP_MAX_TOTAL_BYTES (1.5 GB) well below the 4 GB limit.
 *
 * Pure functions, no DOM; the only globals used are TextEncoder and Blob.
 */

const _CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

/** CRC-32 (IEEE 802.3) of a byte array, as an unsigned 32-bit integer. */
function crc32(bytes, seed = 0) {
  let c = ~seed >>> 0;
  for (let i = 0; i < bytes.length; i++) c = _CRC_TABLE[(c ^ bytes[i]) & 0xFF] ^ (c >>> 8);
  return (~c) >>> 0;
}

/** MS-DOS date/time pair used in ZIP headers. */
function _dosDateTime(d) {
  const date = d instanceof Date && !isNaN(d) ? d : new Date();
  const year = Math.max(1980, date.getFullYear());
  const time = (date.getHours() << 11) | (date.getMinutes() << 5) | (date.getSeconds() >> 1);
  const day = ((year - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate();
  return { time: time & 0xFFFF, date: day & 0xFFFF };
}

/**
 * Creates a writer. `add(name, bytes, date)` appends one entry; `finish()`
 * returns the archive as a Blob. Names use forward slashes; a leading slash
 * is dropped. UTF-8 names are flagged (general purpose bit 11).
 */
function createZipWriter() {
  const encoder = new TextEncoder();
  const parts = [];      // Uint8Array pieces of the archive, in order
  const central = [];    // central directory records, built as entries are added
  const names = [];
  let offset = 0;

  const header = (size) => {
    const buf = new Uint8Array(size);
    const view = new DataView(buf.buffer);
    return { buf, view };
  };

  function add(name, bytes, date) {
    let clean = String(name || "").replace(/\\/g, "/").replace(/^\/+/, "");
    if (!clean) throw new Error("ZIP entry must have a name");
    if (!(bytes instanceof Uint8Array)) bytes = new Uint8Array(bytes);
    const nameBytes = encoder.encode(clean);
    const crc = crc32(bytes);
    const { time, date: dosDate } = _dosDateTime(date);
    const FLAG_UTF8 = 0x0800;

    const local = header(30);
    local.view.setUint32(0, 0x04034b50, true);   // local file header signature
    local.view.setUint16(4, 20, true);           // version needed: 2.0
    local.view.setUint16(6, FLAG_UTF8, true);
    local.view.setUint16(8, 0, true);            // method: store
    local.view.setUint16(10, time, true);
    local.view.setUint16(12, dosDate, true);
    local.view.setUint32(14, crc, true);
    local.view.setUint32(18, bytes.length, true);
    local.view.setUint32(22, bytes.length, true);
    local.view.setUint16(26, nameBytes.length, true);
    local.view.setUint16(28, 0, true);           // extra length

    const cd = header(46);
    cd.view.setUint32(0, 0x02014b50, true);      // central directory signature
    cd.view.setUint16(4, 0x0314, true);          // made by: UNIX, 2.0
    cd.view.setUint16(6, 20, true);
    cd.view.setUint16(8, FLAG_UTF8, true);
    cd.view.setUint16(10, 0, true);
    cd.view.setUint16(12, time, true);
    cd.view.setUint16(14, dosDate, true);
    cd.view.setUint32(16, crc, true);
    cd.view.setUint32(20, bytes.length, true);
    cd.view.setUint32(24, bytes.length, true);
    cd.view.setUint16(28, nameBytes.length, true);
    cd.view.setUint16(30, 0, true);              // extra
    cd.view.setUint16(32, 0, true);              // comment
    cd.view.setUint16(34, 0, true);              // disk
    cd.view.setUint16(36, 0, true);              // internal attrs
    cd.view.setUint32(38, 0o100644 << 16, true); // external attrs: -rw-r--r--
    cd.view.setUint32(42, offset, true);         // local header offset

    parts.push(local.buf, nameBytes, bytes);
    central.push(cd.buf, nameBytes);
    names.push(clean);
    offset += local.buf.length + nameBytes.length + bytes.length;
    if (offset > 0xFFFFFFFF) throw new Error("Archive exceeds 4 GB; ZIP64 is not supported");
  }

  function finish() {
    const cdOffset = offset;
    let cdSize = 0;
    for (const p of central) cdSize += p.length;
    const eocd = header(22);
    eocd.view.setUint32(0, 0x06054b50, true);
    eocd.view.setUint16(4, 0, true);
    eocd.view.setUint16(6, 0, true);
    eocd.view.setUint16(8, names.length, true);
    eocd.view.setUint16(10, names.length, true);
    eocd.view.setUint32(12, cdSize, true);
    eocd.view.setUint32(16, cdOffset, true);
    eocd.view.setUint16(20, 0, true);
    return new Blob([...parts, ...central, eocd.buf], { type: "application/zip" });
  }

  return { add, finish, names: () => names.slice() };
}
