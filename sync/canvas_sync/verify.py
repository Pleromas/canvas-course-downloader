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
