"""Deterministic, audited branding of a private studio distribution.

This module contains no private engine source. It rewrites the owned legacy
product identifier consistently, including Python identifiers and cache names.
External protocol aliases and existing user-state migration are handled by the
server/installer; a new download contains only the current product branding.
"""
from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import PurePosixPath

BRANDING_VERSION = 1
LEGACY = re.compile(r"frontier", re.IGNORECASE)
LEGACY_BYTES = re.compile(b"frontier", re.IGNORECASE)
TEXT_SUFFIXES = {
    ".py", ".html", ".md", ".txt", ".js", ".mjs", ".cjs", ".json",
    ".css", ".svg", ".sh", ".command", ".cmd", ".bat", ".toml",
    ".yaml", ".yml", ".ini", ".cfg", ".xml", ".csv", ".ts",
    ".tsx", ".jsx", ".sql", ".lock", ".gitignore", ".shipignore",
}
BRAND_IMAGES = {
    "assets/brand/frontier.png",
    "assets/brand/frontier banner final.png",
}


def _current_name(match: re.Match) -> str:
    old = match.group()
    if old.isupper():
        return "CHANNELRECIPE"
    if old.islower():
        return "channelrecipe"
    return "ChannelRecipe"


def brand_text(text: str) -> str:
    """Use a single mapping for comments, strings, imports and identifiers."""
    # This guide has a functional role rather than a product-specific name.
    text = re.sub(r"FRONTIER_GUIDE\.md", "CREATIVE_GUIDE.md", text, flags=re.I)
    text = re.sub(r"/api/frontier/", "/api/studio/", text, flags=re.I)
    return LEGACY.sub(_current_name, text)


def brand_path(relative: str) -> str:
    original = PurePosixPath(relative)
    if original.is_absolute() or ".." in original.parts or not original.parts:
        raise ValueError("Studio resource must have a safe relative path.")
    branded = PurePosixPath(brand_text(str(original)))
    if branded.is_absolute() or ".." in branded.parts:
        raise ValueError("Branded studio resource must have a safe relative path.")
    return str(branded)


def transform_resource(relative: str, content: bytes, *, brand_png: bytes | None = None):
    """Return branded identity/content and hash-only provenance when changed.

    Never replace bytes within a binary file: an unexpected binary match is a
    build error. The two known owned logo images are replaced with the existing
    ChannelRecipe app icon, not edited or represented as sample video content.
    """
    name = brand_path(relative)
    kind = "text_branding"
    windows_script = PurePosixPath(relative).suffix.lower() in {".cmd", ".bat"}
    if relative.lower() in BRAND_IMAGES:
        if not brand_png or not brand_png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("The existing ChannelRecipe PNG app icon is required.")
        if LEGACY_BYTES.search(brand_png):
            raise ValueError("ChannelRecipe app icon contains a legacy brand reference.")
        transformed = brand_png
        kind = "owned_brand_asset"
    elif LEGACY_BYTES.search(content) or windows_script:
        path = PurePosixPath(relative)
        is_text = path.suffix.lower() in TEXT_SUFFIXES or not path.suffix
        if not is_text:
            raise ValueError("Legacy branding found in an unsupported binary resource: " + name)
        try:
            source = content.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("Legacy branding found in a non-UTF-8 resource: " + name) from None
        if "\x00" in source:
            raise ValueError("Legacy branding found in a binary resource: " + name)
        branded_source = brand_text(source)
        if windows_script:
            # ZIP preserves bytes. Native Windows launchers must not depend on
            # the release host's newline convention, including unchanged files.
            branded_source = branded_source.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
            if not LEGACY_BYTES.search(content) and name == relative:
                kind = "windows_line_endings"
        transformed = branded_source.encode("utf-8")
    else:
        transformed = content
    if LEGACY.search(name) or LEGACY_BYTES.search(transformed):
        raise ValueError("Studio branding validation failed for resource: " + name)
    provenance = None
    if name != relative or transformed != content:
        provenance = {
            "path": name,
            "source_sha256": hashlib.sha256(content).hexdigest(),
            "packaged_sha256": hashlib.sha256(transformed).hexdigest(),
            "transformation": kind,
        }
    return name, transformed, provenance


def assert_clean_archive(archive: zipfile.ZipFile) -> dict:
    """Inspect every packaged filename and uncompressed member byte stream."""
    seen = set()
    count = 0
    for info in archive.infolist():
        name = info.filename
        if name in seen:
            raise ValueError("Duplicate studio archive resource: " + name)
        seen.add(name)
        if LEGACY.search(name):
            raise ValueError("Legacy brand reference in studio archive path.")
        # Stream media and other large files rather than collecting the package.
        tail = b""
        with archive.open(info) as member:
            while chunk := member.read(1024 * 1024):
                data = tail + chunk
                if LEGACY_BYTES.search(data):
                    raise ValueError("Legacy brand reference in studio archive member: " + name)
                tail = data[-7:]
        count += 1
    return {"branding_version": BRANDING_VERSION, "files_scanned": count,
            "legacy_path_matches": 0, "legacy_byte_matches": 0}
