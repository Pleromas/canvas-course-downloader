"""Schema-2 export manifest parsing."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

SUPPORTED_SCHEMA = 2


class ManifestError(ValueError):
    pass


def canonical_meta(meta: dict | None) -> str:
    return json.dumps(meta or {}, sort_keys=True, separators=(", ", ": "))


@dataclass
class Item:
    type: str
    key: str
    path: str | None
    title: str
    updated_at: str | None
    size: int | None
    meta: dict = field(default_factory=dict)
    source_course_id: str | None = None


@dataclass
class Manifest:
    schema: int
    course_id: str
    course_name: str
    domain: str
    exported_at: str
    extension_version: str
    role: str
    complete: bool
    failed_paths: list[str]
    counts: dict
    items: list[Item]
    exported_types: list[str] | None = None   # None = everything (legacy / full export)
    warnings: list[str] = field(default_factory=list)


def _item(raw: dict, index: int) -> Item:
    type_ = raw.get("type")
    key = raw.get("canvasId") if type_ != "synthetic" else raw.get("key")
    if not type_ or key in (None, ""):
        raise ManifestError(f"item {index} has no identity (type={type_!r})")
    size = raw.get("size")
    return Item(
        type=str(type_),
        key=str(key),
        path=raw.get("path") or None,
        title=str(raw.get("title") or raw.get("path") or key),
        updated_at=raw.get("updatedAt") or None,
        size=int(size) if isinstance(size, (int, float)) and size > 0 else None,
        meta=dict(raw.get("meta") or {}),
        source_course_id=str(raw["sourceCourseId"]) if raw.get("sourceCourseId") else None,
    )


def parse_manifest(data: dict) -> Manifest:
    schema = data.get("schema")
    if schema != SUPPORTED_SCHEMA:
        raise ManifestError(
            f"manifest schema {schema!r} not supported; this pipeline needs schema 2. "
            "Update the Canvas Course Downloader extension and export again."
        )
    course = data.get("course") or {}
    for k in ("id", "name", "domain"):
        if not course.get(k):
            raise ManifestError(f"manifest course.{k} missing")
    if not data.get("exportedAt"):
        raise ManifestError("manifest exportedAt missing")
    items: list[Item] = []
    warnings: list[str] = []
    seen: set[tuple[str, str]] = set()
    for i, raw in enumerate(data.get("items") or []):
        it = _item(raw, i)
        ident = (it.type, it.key)
        if ident in seen:
            warnings.append(f"duplicate identity {it.type}:{it.key} ({it.path}); kept the first")
            continue
        seen.add(ident)
        items.append(it)
    exported = data.get("exportedTypes")
    return Manifest(
        schema=schema,
        course_id=str(course["id"]),
        course_name=str(course["name"]),
        domain=str(course["domain"]),
        exported_at=str(data["exportedAt"]),
        extension_version=str(data.get("extensionVersion") or ""),
        role=str(data.get("role") or "student"),
        complete=bool(data.get("complete", True)),
        failed_paths=list(data.get("failedPaths") or []),
        counts=dict(data.get("counts") or {}),
        items=items,
        exported_types=[str(t) for t in exported] if isinstance(exported, list) else None,
        warnings=warnings,
    )
