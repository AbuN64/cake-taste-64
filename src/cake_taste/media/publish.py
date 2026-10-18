"""Publish validated captures and render a dated static gallery."""

import fcntl
import hashlib
import html
import json
import re
import struct
import zlib
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from cake_taste.files import atomic_bytes, sha256_file

from .store import MEDIA_SUFFIXES

SOURCE_SUFFIXES = MEDIA_SUFFIXES | {".ppm"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".ppm"}


def png_from_ppm(data):
    position = 0

    def token():
        nonlocal position
        while position < len(data):
            if data[position] == 35:
                position = data.find(b"\n", position)
                if position < 0:
                    raise ValueError("Unterminated PPM comment")
            elif data[position] in b" \t\r\n":
                position += 1
            else:
                break
        match = re.match(rb"[^\s#]+", data[position:])
        if not match:
            raise ValueError("Invalid PPM header")
        position += len(match.group())
        return match.group()

    if token() != b"P6":
        raise ValueError("Only binary P6 PPM is supported")
    width, height, maximum = map(int, (token(), token(), token()))
    if not (0 < width <= 10000 and 0 < height <= 10000 and maximum == 255):
        raise ValueError("Unsupported PPM dimensions or color depth")
    if data[position : position + 1] not in (b"\n", b" ", b"\t", b"\r"):
        raise ValueError("Missing PPM pixel separator")
    pixels = data[position + 1 :]
    stride = width * 3
    if len(pixels) != stride * height:
        raise ValueError("PPM pixel count does not match header")
    rows = b"".join(b"\0" + pixels[y * stride : (y + 1) * stride] for y in range(height))

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, 6))
        + chunk(b"IEND", b"")
    )


def validate_media(data, suffix):
    valid = {
        ".png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        ".jpg": data.startswith(b"\xff\xd8"),
        ".jpeg": data.startswith(b"\xff\xd8"),
        ".mp4": len(data) >= 12 and data[4:8] == b"ftyp",
        ".webm": data.startswith(b"\x1a\x45\xdf\xa3"),
    }
    if not valid[suffix]:
        raise ValueError(f"Content is not a {suffix} media file")


def source_paths(inputs):
    found = []
    for value in inputs:
        path = Path(value).expanduser().resolve(strict=True)
        if path.is_file():
            if path.suffix.lower() not in SOURCE_SUFFIXES:
                raise ValueError(f"Unsupported media type: {path}")
            found.append(path)
        elif path.is_dir():
            found.extend(p.resolve() for p in path.rglob("*") if p.is_file() and p.suffix.lower() in SOURCE_SUFFIXES)
        else:
            raise ValueError(f"Not a file or directory: {path}")
    return sorted(set(found))


def render_html(items, page_title, viewer_url="", timezone_name="UTC"):
    groups = {}
    for item in items:
        groups.setdefault(item["date"], []).append(item)
    sections = []
    for day in sorted(groups, reverse=True):
        cards = []
        for item in sorted(groups[day], key=lambda value: (value["captured_at"], value["id"]), reverse=True):
            path = quote(item["file"], safe="/")
            title = html.escape(item["title"])
            description = html.escape(item.get("description", ""))
            timestamp = html.escape(item["captured_at"])
            revision = html.escape(item.get("revision", "unknown"))
            media = (
                f'<video controls preload="metadata" src="{path}"></video>'
                if item["type"] == "video"
                else f'<a href="{path}"><img loading="lazy" src="{path}" alt="{title}"></a>'
            )
            cards.append(
                f'<article class="card">{media}<div class="details"><h3>{title}</h3>'
                f'<p>{description}</p><small><time datetime="{timestamp}">{timestamp}</time>'
                f" · {revision}</small>"
                f'<a class="original" href="{path}" download>Original file ↗</a></div></article>'
            )
        sections.append(f'<section><h2>{html.escape(day)}</h2><div class="grid">{"".join(cards)}</div></section>')
    viewer = f'<a href="{html.escape(viewer_url, quote=True)}">Watch live ↗</a>' if viewer_url else ""
    title = html.escape(page_title)
    zone = html.escape(timezone_name)
    style = """<style>
:root{color-scheme:dark;font:16px/1.5 system-ui,sans-serif;background:#12171d;color:#edf2f6}
*{box-sizing:border-box}body{margin:0}
header{padding:2rem max(1rem,calc((100vw - 1300px)/2));background:#1c2631;border-bottom:1px solid #34475a}
h1{margin:0;font-size:clamp(1.8rem,4vw,3rem)}header p{margin:.4rem 0 0;color:#bfccd8}
header a,.original{color:#8dd4ff}main{max-width:1300px;margin:auto;padding:1rem}
h2{font-size:1.35rem;margin:2.4rem 0 1rem}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,360px),1fr));gap:1.2rem}
.card{background:#202a35;border:1px solid #354554;border-radius:12px;overflow:hidden}
img,video{display:block;width:100%;aspect-ratio:16/9;object-fit:contain;background:#080b0e}
.details{padding:1rem}h3{margin:0 0 .35rem;font-size:1.1rem}
.details p{margin:.3rem 0;color:#c4d0da}small{display:block;color:#9aabba;overflow-wrap:anywhere}
.original{display:inline-block;margin-top:.7rem}a:focus-visible{outline:3px solid #8dd4ff;outline-offset:2px}
</style>"""
    body = "".join(sections) if sections else "<p>No captures published yet.</p>"
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>{style}'
        f"</head><body><header><h1>{title}</h1><p>{len(items)} captures, grouped by capture date · {zone}</p>"
        f"{viewer}</header><main>{body}</main></body></html>"
    )


def publish(
    root,
    inputs,
    *,
    timezone_name,
    page_title,
    viewer_url="",
    title=None,
    description=None,
    date_override=None,
    revision=None,
):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    zone = ZoneInfo(timezone_name)
    chosen_date = date.fromisoformat(date_override) if date_override else None
    lock_path = root.parent / f".{root.name}.publish.lock"
    paths = source_paths(inputs)
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        catalog_path = root / "catalog.json"
        catalog = json.loads(catalog_path.read_text()) if catalog_path.exists() else {"items": []}
        existing = {item["id"]: item for item in catalog["items"]}
        effective_viewer = viewer_url if viewer_url is not None else catalog.get("viewer_url", "")
        for source in paths:
            original = source.read_bytes()
            source_hash = hashlib.sha256(original).hexdigest()
            suffix = source.suffix.lower()
            output = png_from_ppm(original) if suffix == ".ppm" else original
            output_suffix = ".png" if suffix == ".ppm" else suffix
            validate_media(output, output_suffix)
            output_hash = hashlib.sha256(output).hexdigest()
            relative = f"assets/{output_hash}{output_suffix}"
            destination = root / relative
            if not destination.exists():
                atomic_bytes(destination, output)
            elif sha256_file(destination) != output_hash:
                raise ValueError(f"Published blob changed: {destination}")
            captured = datetime.fromtimestamp(source.stat().st_mtime, zone)
            if chosen_date:
                captured = datetime.combine(chosen_date, captured.timetz(), zone)
            captured_text = captured.isoformat(timespec="seconds")
            identifier = hashlib.sha256(f"{source.name}|{source_hash}|{captured_text}".encode()).hexdigest()[:20]
            old = existing.get(identifier, {})
            default_title = re.sub(r"[-_]", " ", source.stem).strip().capitalize()
            existing[identifier] = {
                "id": identifier,
                "date": captured.date().isoformat(),
                "captured_at": captured_text,
                "title": title if title is not None else old.get("title", default_title),
                "description": description if description is not None else old.get("description", ""),
                "type": "image" if suffix in IMAGE_SUFFIXES else "video",
                "file": relative,
                "source_name": source.name,
                "source_sha256": source_hash,
                "sha256": output_hash,
                "revision": revision if revision is not None else old.get("revision", "unknown"),
                "published_at": old.get("published_at", datetime.now(UTC).isoformat(timespec="seconds")),
            }
        items = sorted(existing.values(), key=lambda item: (item["captured_at"], item["id"]), reverse=True)
        data = {"timezone": timezone_name, "viewer_url": effective_viewer, "items": items}
        atomic_bytes(catalog_path, (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode())
        atomic_bytes(root / "index.html", render_html(items, page_title, effective_viewer, timezone_name).encode())
    return {
        "published": len(paths),
        "items": len(items),
        "root": str(root),
        "catalog_sha256": sha256_file(root / "catalog.json"),
    }
