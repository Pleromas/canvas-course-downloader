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


# Hook filled in by later tasks (report rendering, latest/ rebuild). Kept as a
# module attribute so ingest stays testable before those modules exist.
def _after_commit(store: Store, run_id: int, warnings: list[str]) -> tuple[Path | None, str]:
    from .changes import compute_changes
    from .report import one_line, write_report
    from .materialize import rebuild_latest
    cs = compute_changes(store, run_id)
    path = write_report(store, cs)
    rebuild_latest(store, cs.course_id)
    return path, one_line(cs)


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
