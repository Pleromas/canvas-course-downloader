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
