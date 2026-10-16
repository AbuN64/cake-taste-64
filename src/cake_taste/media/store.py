"""Verify and import a static media store without rewriting its history."""

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath

MEDIA_SUFFIXES = {".png", ".jpg", ".jpeg", ".mp4", ".webm"}


def digest_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def asset_path(root, relative):
    name = PurePosixPath(relative)
    if (
        name.is_absolute()
        or len(name.parts) != 2
        or name.parts[0] != "assets"
        or name.name in (".", "..")
        or name.suffix.lower() not in MEDIA_SUFFIXES
    ):
        raise ValueError(f"Invalid catalog media path: {relative!r}")
    path = root / str(name)
    if path.is_symlink():
        raise ValueError(f"Media symlink is not allowed: {path}")
    return path


def audit(root, require_index=True):
    """Read-only check of catalog IDs, blobs, hashes, and reference closure."""
    root = Path(root)
    catalog_path = root / "catalog.json"
    if catalog_path.is_symlink():
        raise ValueError("Catalog symlink is not allowed")
    raw = catalog_path.read_bytes()
    catalog = json.loads(raw)
    items = catalog["items"]
    ids = set()
    refs = {}
    kinds = {"image": 0, "video": 0}
    for item in items:
        identifier = item["id"]
        if identifier in ids:
            raise ValueError(f"Duplicate capture ID: {identifier}")
        ids.add(identifier)
        relative = item["file"]
        expected = item["sha256"]
        path = asset_path(root, relative)
        if path.stem != expected:
            raise ValueError(f"Blob filename differs from catalog SHA-256: {relative}")
        if relative in refs and refs[relative] != expected:
            raise ValueError(f"Conflicting catalog references: {relative}")
        refs[relative] = expected
        kinds[item["type"]] += 1
    entries = []
    for relative, expected in sorted(refs.items()):
        path = asset_path(root, relative)
        if not path.is_file() or digest_file(path) != expected:
            raise ValueError(f"Missing or changed media blob: {relative}")
        entries.append({"path": relative, "bytes": path.stat().st_size, "sha256": expected})
    assets = root / "assets"
    if assets.is_symlink():
        raise ValueError("Assets directory symlink is not allowed")
    listed = list(assets.iterdir()) if assets.exists() else []
    if any(path.is_symlink() or not path.is_file() for path in listed):
        raise ValueError("Assets directory contains a symlink or nonfile")
    actual = {f"assets/{path.name}" for path in listed}
    if actual != set(refs):
        orphan = sorted(actual - set(refs))
        missing = sorted(set(refs) - actual)
        raise ValueError(f"Media reference closure failed: orphan={orphan}, missing={missing}")
    index = root / "index.html"
    if require_index and (not index.is_file() or index.is_symlink()):
        raise ValueError("Static index.html is missing")
    index_sha = digest_file(index) if index.is_file() else None
    manifest_data = "".join(f"{entry['path']}\t{entry['bytes']}\t{entry['sha256']}\n" for entry in entries).encode()
    return {
        "items": len(items),
        "types": kinds,
        "blobs": len(entries),
        "blob_bytes": sum(entry["bytes"] for entry in entries),
        "catalog_sha256": hashlib.sha256(raw).hexdigest(),
        "index_sha256": index_sha,
        "manifest_sha256": hashlib.sha256(manifest_data).hexdigest(),
        "files": entries,
    }


def import_verified(source, target):
    """Copy a historical store once; preserve catalog bytes, IDs, and mtimes."""
    source = Path(source).resolve(strict=True)
    target = Path(target)
    if target.resolve().is_relative_to(source):
        raise ValueError("Import target cannot be inside the source store")
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"Import target already exists: {target}")
    original = audit(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.import-", dir=target.parent))
    try:
        (staging / "assets").mkdir()
        for name in ("catalog.json", "index.html"):
            shutil.copy2(source / name, staging / name, follow_symlinks=False)
        for entry in original["files"]:
            shutil.copy2(source / entry["path"], staging / entry["path"], follow_symlinks=False)
        copied = audit(staging)
        if copied != original:
            raise ValueError("Imported catalog or media differs from the source manifest")
        os.replace(staging, target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {"source": str(source), "target": str(target), **original}
