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
    written = 0
    for rel, sha in files.items():
        target = (dest / rel).resolve()
        if dest.resolve() not in target.parents:
            continue  # refuse paths escaping dest
        _link_or_copy(store.blob_path(sha), target)
        written += 1
    return written


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
