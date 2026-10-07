"""canvas-sync command line."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__
from .changes import _change_for
from .ingest import ingest_zip
from .manifest import ManifestError
from .materialize import materialize_run
from .store import Store
from .verify import gc, verify

DEFAULT_INBOX = Path.home() / "Downloads" / "CanvasExports"


def _notify_error(msg: str) -> None:
    exe = shutil.which("notify-send")
    if exe:
        subprocess.run([exe, "--urgency=critical", "Canvas sync failed", msg], check=False)


def _course_rows(store: Store):
    return store.db.execute(
        "select c.id, c.name, count(r.id) as runs, max(r.exported_at) as last "
        "from courses c left join runs r on r.course_id=c.id group by c.id order by c.name").fetchall()


def _ingest_paths(store: Store, paths, delete: bool, notify: bool) -> int:
    code = 0
    for p in paths:
        p = Path(p)
        try:
            res = ingest_zip(store, p, keep=not delete, notify=notify)
        except Exception as e:  # one broken ZIP must not block the others (systemd oneshot)
            kind = "manifest" if isinstance(e, ManifestError) else type(e).__name__
            msg = f"{p.name}: {kind}: {e}"
            print(msg)
            if notify:
                _notify_error(msg)
            code = 2
            continue
        print(res.summary if not res.skipped else f"{p.name}: skipped — {res.reason}")
        if res.report_path:
            print(f"  report: {res.report_path}")
    return code


def cmd_ingest(store: Store, a) -> int:
    return _ingest_paths(store, a.paths, a.delete, a.notify)


def cmd_ingest_all(store: Store, a) -> int:
    d = Path(a.dir).expanduser()
    if not d.is_dir():
        print(f"{d}: not a directory")
        return 2
    return _ingest_paths(store, sorted(d.glob("*.zip")), a.delete, a.notify)


def cmd_status(store: Store, a) -> int:
    rows = _course_rows(store)
    if not rows:
        print("no courses ingested yet")
    for r in rows:
        print(f"{r['name']} ({r['id']}) — runs: {r['runs']}, last export: {r['last'] or '-'}")
    inbox = Path(a.inbox).expanduser()
    pending = sorted(inbox.glob("*.zip")) if inbox.is_dir() else []
    print(f"pending in {inbox}: {len(pending)}")
    return 0


def _course_id(store: Store, ref: str) -> str:
    row = store.db.execute("select id from courses where id=? or name=?", (ref, ref)).fetchone()
    if row is None:
        raise SystemExit(f"unknown course {ref!r}")
    return row["id"]


def cmd_log(store: Store, a) -> int:
    cid = _course_id(store, a.course)
    if a.item:
        type_, _, key = a.item.partition(":")
        rows = store.db.execute(
            "select v.id, v.run_id, r.exported_at, v.path, v.title, v.sha256, v.meta_json from versions v "
            "join items i on i.id=v.item_id join runs r on r.id=v.run_id "
            "where i.course_id=? and i.type=? and i.key=? order by v.id", (cid, type_, key)).fetchall()
        for v in rows:
            print(f"run {v['run_id']}  {v['exported_at']}  {v['path']}  {(v['sha256'] or '-')[:12]}  {v['meta_json']}")
        return 0
    for r in store.db.execute("select * from runs where course_id=? order by id", (cid,)):
        flag = "" if r["complete"] else "  (incomplete)"
        print(f"run {r['id']}  exported {r['exported_at']}  ingested {r['ingested_at']}{flag}  report: {r['report_path'] or '-'}")
    return 0


def cmd_diff(store: Store, a) -> int:
    cid = _course_id(store, a.course)
    type_, _, key = a.item.partition(":")
    item = store.db.execute("select id, type, key from items where course_id=? and type=? and key=?",
                            (cid, type_, key)).fetchone()
    if item is None:
        print(f"no such item {a.item}")
        return 2

    def version_at(run_id: int):
        return store.db.execute("select * from versions where item_id=? and run_id<=? order by id desc limit 1",
                                (item["id"], run_id)).fetchone()

    va, vb = version_at(int(a.run_a)), version_at(int(a.run_b))
    if va is None or vb is None:
        print("item has no version at one of those runs")
        return 2
    ch = _change_for(store, {"type": item["type"], "key": item["key"]}, dict(vb), dict(va))
    for k, o, n in ch.meta_diff:
        print(f"{k}: {o} → {n}")
    for name, o, n in ch.grade_changes:
        print(f"{name}: {o} → {n}")
    if ch.size_change:
        print(f"content changed ({ch.size_change[0]} → {ch.size_change[1]} bytes)")
    if ch.body_diff:
        print(ch.body_diff)
    if not (ch.meta_diff or ch.grade_changes or ch.size_change or ch.body_diff):
        print("no difference")
    return 0


def cmd_checkout(store: Store, a) -> int:
    cid = _course_id(store, a.course)
    run = store.db.execute("select id from runs where course_id=? and id=?", (cid, int(a.run))).fetchone()
    if run is None:
        print(f"run {a.run} not found for course {a.course}")
        return 2
    n = materialize_run(store, run["id"], Path(a.dir).expanduser(), clean=False)
    print(f"wrote {n} files to {a.dir}")
    return 0


def cmd_verify(store: Store, a) -> int:
    problems = verify(store)
    for p in problems:
        print(p)
    print("ok" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


def cmd_gc(store: Store, a) -> int:
    print(f"removed {gc(store)} unreferenced blob(s)")
    return 0


def cmd_install_units(store: Store, a) -> int:
    src = Path(__file__).resolve().parent.parent / "systemd"
    dest = Path.home() / ".config" / "systemd" / "user"
    dest.mkdir(parents=True, exist_ok=True)
    exe = shutil.which("canvas-sync") or f"{sys.executable} -m canvas_sync"
    for name in ("canvas-sync.path", "canvas-sync.service"):
        text = (src / name).read_text().replace("@CANVAS_SYNC@", exe)
        (dest / name).write_text(text)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    subprocess.run(["systemctl", "--user", "enable", "--now", "canvas-sync.path"], check=False)
    print(f"installed units to {dest}; watching {DEFAULT_INBOX}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="canvas-sync", description="Versioned archive for Canvas course exports")
    p.add_argument("--root", default=None, help="store root (default ~/CanvasArchive or $CANVAS_SYNC_ROOT)")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("ingest", help="ingest one or more ZIP exports")
    s.add_argument("paths", nargs="+")
    s.add_argument("--delete", action="store_true", help="delete ZIP after ingest instead of moving to processed/")
    s.add_argument("--notify", action="store_true", help="desktop notification via notify-send")
    s.set_defaults(fn=cmd_ingest)

    s = sub.add_parser("ingest-all", help="ingest every ZIP in a directory")
    s.add_argument("dir", nargs="?", default=str(DEFAULT_INBOX))
    s.add_argument("--delete", action="store_true")
    s.add_argument("--notify", action="store_true")
    s.set_defaults(fn=cmd_ingest_all)

    s = sub.add_parser("status", help="courses, runs, pending ZIPs")
    s.add_argument("--inbox", default=str(DEFAULT_INBOX))
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("log", help="runs of a course, or versions of one item")
    s.add_argument("course")
    s.add_argument("--item", help="TYPE:KEY, e.g. assignment:456")
    s.set_defaults(fn=cmd_log)

    s = sub.add_parser("diff", help="diff one item between two runs")
    s.add_argument("course")
    s.add_argument("item", help="TYPE:KEY")
    s.add_argument("run_a")
    s.add_argument("run_b")
    s.set_defaults(fn=cmd_diff)

    s = sub.add_parser("checkout", help="materialise a run into a directory")
    s.add_argument("course")
    s.add_argument("run")
    s.add_argument("dir")
    s.set_defaults(fn=cmd_checkout)

    sub.add_parser("verify", help="rehash every referenced blob").set_defaults(fn=cmd_verify)
    sub.add_parser("gc", help="delete unreferenced blobs").set_defaults(fn=cmd_gc)
    sub.add_parser("install-units", help="install and enable the systemd user path unit").set_defaults(fn=cmd_install_units)
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    store = Store(a.root or Store.default_root())
    try:
        return a.fn(store, a)
    finally:
        store.close()
