"""Build the OWNER-ONLY preview from a private source archive; never commit it.

Usage: python scripts/build_frontier_admin.py --source /path/to/Frontier-5.3-FULL.zip
       [--upload]  # private Spaces object, not a public/CDN media upload
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlsplit

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SOURCE_SHA256 = "e379dc601672282040a2adbf30f7bf7f7d34fcdf38154e5f6a24ec6c2528cadf"
VERSION = "5.3-admin.35"


def compose_claude_instructions(original: bytes, overrides: bytes) -> bytes:
    """Restore private interactive creative context without publishing its text."""
    introduction = (
        "# ChannelRecipe private studio instructions\n\n"
        "The original Frontier creative guide follows unchanged. Use it for creative direction, "
        "style customization, specialist modules, visual verification and editing.\n\n"
        "The ChannelRecipe operational overrides at the end govern setup, account/key handling, "
        "billing, launch commands, MCP authorization and recovery whenever the older guide differs. "
        "Use local setup forms for keys; never collect or reveal them in chat. Launch production "
        "through CR's maintained tools and project/stage checks. Do not follow older standalone "
        "generation commands or API fallback instructions that bypass those checks.\n\n"
        "## Original Frontier creative guide\n\n"
    ).encode("utf-8")
    return introduction + original + b"\n\n---\n\n# ChannelRecipe operational overrides\n\n" + overrides


def verify_private(client, bucket, key, endpoint, cdn=""):
    acl = client.get_object_acl(Bucket=bucket, Key=key)
    if any(g.get("Grantee", {}).get("URI", "").endswith(("/AllUsers", "/AuthenticatedUsers")) for g in acl.get("Grants", [])):
        raise RuntimeError("Release ACL must be private.")
    host = urlsplit(endpoint).netloc
    urls = ["https://" + bucket + "." + host + "/" + quote(key)]
    if cdn:
        urls.append(cdn.rstrip("/") + "/" + quote(key))
    # Check anonymously too: bucket policy/CDN can override an object ACL.
    for url in urls:
        response = requests.head(url, timeout=20, allow_redirects=True)
        if response.status_code not in (403, 404):
            raise RuntimeError("Anonymous access was not denied. Do not publish this bundle.")


def build(source: Path, target: Path):
    with source.open("rb") as f:
        actual = hashlib.file_digest(f, "sha256").hexdigest()
    if actual != SOURCE_SHA256:
        raise ValueError("Source changed. Review and pin its new manifest before building a release.")
    with zipfile.ZipFile(source) as original:
        try:
            private_guide = original.read("Frontier/CLAUDE.md")
        except KeyError:
            raise ValueError("The pinned source must include its original Claude creative guide.") from None
    operational_overrides = (ROOT / "local_runtime/CLAUDE.md").read_bytes()
    target.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as result:
        for info in original.infolist():
            parts = PurePosixPath(info.filename).parts
            if not parts or parts[0] != "Frontier" or ".." in parts or info.is_dir():
                continue
            rel = PurePosixPath(*parts[1:])
            if not rel.parts:
                continue
            if rel.parts[0] in {"samples", ".frontier", "output", "presets", "__MACOSX"}:
                continue
            if rel.name in {".env", ".env.example", ".DS_Store"} or rel.suffix == ".pyc":
                continue
            # The source and templates remain admin-only for parity testing.
            name = "ChannelRecipe studio/" + ("FRONTIER_GUIDE.md" if str(rel)=="CLAUDE.md" else str(rel))
            result.writestr(name, original.read(info))
            count += 1
        for path in sorted((ROOT / "local_runtime").iterdir()):
            if not path.is_file() or path.suffix not in {".py", ".html", ".md", ".command", ".cmd"}:
                continue
            info = zipfile.ZipInfo("ChannelRecipe studio/" + path.name)
            info.external_attr = (0o100755 if path.suffix == ".command" else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            contents = compose_claude_instructions(private_guide, operational_overrides) if path.name == "CLAUDE.md" else path.read_bytes()
            result.writestr(info, contents)
        result.writestr("ChannelRecipe studio/cr-providers.json", (ROOT / "webapp/frontier/providers.json").read_bytes())
        result.writestr("ChannelRecipe studio/cr-release.json", json.dumps({
            "version": VERSION, "access": "admin_only", "source_sha256": SOURCE_SHA256,
            "public_release_allowed": False, "engine_files": count,
            "workflow_version": "frontier-5.3-claudecode.1", "preferred_brain": "claudecode",
            "note": "Owner preview contains original source. Public runtime split remains required."}, indent=2))
    with target.open("rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    return {"version": VERSION, "bytes": target.stat().st_size, "sha256": digest, "engine_files": count}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "data/frontier" / ("channelrecipe-" + VERSION + ".zip"))
    parser.add_argument("--upload", action="store_true")
    args = parser.parse_args()
    manifest = build(args.source, args.output)
    (args.output.parent / "manifest.json").write_text(json.dumps(manifest, indent=2))
    if args.upload:
        from webapp import storage
        from webapp.frontier_studio import RELEASE_KEY
        import config
        client = storage._get_client()
        _, _, bucket, _, endpoint = storage._spaces_creds()
        canary = RELEASE_KEY + ".privacy-check"
        try:
            client.put_object(Bucket=bucket, Key=canary, Body=b"private access check", ACL="private")
            verify_private(client, bucket, canary, endpoint, config.SPACES_CDN_ENDPOINT)
        finally:
            client.delete_object(Bucket=bucket, Key=canary)
        try:
            client.upload_file(str(args.output), bucket, RELEASE_KEY,
                               ExtraArgs={"ACL": "private", "ContentType": "application/zip",
                                          "CacheControl": "private, no-store", "Metadata": {"sha256": manifest["sha256"]}})
            verify_private(client, bucket, RELEASE_KEY, endpoint, config.SPACES_CDN_ENDPOINT)
        except Exception:
            client.delete_object(Bucket=bucket, Key=RELEASE_KEY)
            raise
        print("Private admin release uploaded; object ACL and anonymous access checked.")
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
