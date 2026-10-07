import tempfile, unittest
from pathlib import Path
from canvas_sync.store import Store


class TempStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "archive"
        self.store = Store(self.root)

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

import json, zipfile


def base_manifest(exported_at="2026-10-06T20:31:00.000Z", complete=True, items=None, course_id="133044",
                  course_name="XLS5C202609"):
    return {
        "schema": 2,
        "course": {"id": course_id, "name": course_name, "domain": "https://canvas.usask.ca"},
        "exportedAt": exported_at,
        "extensionVersion": "2.11.0",
        "role": "student",
        "complete": complete,
        "failedPaths": [],
        "counts": {"total": len(items or [])},
        "items": items or [],
    }


def doc_item(type_, canvas_id, path, title, meta=None, updated_at=None):
    return {"type": type_, "canvasId": canvas_id, "path": path, "title": title,
            "updatedAt": updated_at, "size": None, "meta": meta or {}, "sourceCourseId": None}


def file_item(canvas_id, path, size, meta=None, source_course_id=None):
    return {"type": "file", "canvasId": canvas_id, "path": path, "title": path.rsplit("/", 1)[-1],
            "updatedAt": None, "size": size, "meta": meta or {}, "sourceCourseId": source_course_id}


def synthetic_item(key, path, title=None):
    return {"type": "synthetic", "key": key, "path": path, "title": title or path,
            "updatedAt": None, "size": None, "meta": {}, "sourceCourseId": None}


def make_zip(dest, manifest, files):
    """Write a ZIP at `dest` containing manifest.json plus {path: bytes}."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for path, data in files.items():
            zf.writestr(path, data)
    return dest
