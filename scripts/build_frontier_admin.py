"""Build the OWNER-ONLY preview from a private source archive; never commit it.

Usage: python scripts/build_frontier_admin.py --source /path/to/Frontier-5.3-FULL.zip
       [--upload]  # private Spaces object, not a public/CDN media upload
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlsplit

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SOURCE_SHA256 = "e379dc601672282040a2adbf30f7bf7f7d34fcdf38154e5f6a24ec6c2528cadf"
from scripts import studio_branding
VERSION = "5.3-admin.43"
PRIVATE_WORKFLOWS = {
    "cr-style-delivery-prompt.txt": ROOT / "data/frontier/reference2d-compiler.txt",
    "cr-style-study-brief.txt": ROOT / "data/frontier/reference2d-study.txt",
}
RUNTIME_SUFFIXES = {".py", ".html", ".md", ".command", ".cmd", ".js", ".css"}


def runtime_resources(root: Path):
    """Include owned browser assets without collecting caches or local secrets."""
    runtime = root / "local_runtime"
    for path in sorted(runtime.iterdir()):
        if path.suffix not in RUNTIME_SUFFIXES or not path.is_file():
            continue
        if path.is_symlink():
            raise ValueError("Runtime resources must be ordinary owned files.")
        yield path, path.name
    art = runtime / "workspace_art"
    if art.exists():
        if art.is_symlink() or not art.is_dir():
            raise ValueError("Workspace artwork must be an owned directory.")
        for path in sorted(art.iterdir()):
            if path.suffix != ".svg" or not path.is_file():
                continue
            if path.is_symlink():
                raise ValueError("Workspace artwork must be ordinary owned files.")
            yield path, "workspace_art/" + path.name


def private_preview_resources(root: Path):
    """Package only hash-bound real sample screenshots from the private build."""
    folder=root / "data/frontier/recipe-previews"
    if not folder.exists():
        return []
    manifest=folder / "manifest.json"
    if folder.is_symlink() or not folder.is_dir() or manifest.is_symlink() or not manifest.is_file():
        raise ValueError("Private video previews must be ordinary owned files.")
    data=json.loads(manifest.read_text())
    if data.get("schema")!=1 or data.get("source_archive_sha256")!=SOURCE_SHA256 or not isinstance(data.get("frames"),dict):
        raise ValueError("Private video previews must match the pinned source.")
    resources=[(manifest,"cr_recipe_frames/manifest.json")]
    for style,row in data["frames"].items():
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,139}",style) or not isinstance(row,dict) or row.get("file")!=style+".jpg" or row.get("kind")!="video_frame":
            raise ValueError("Invalid private video preview identity.")
        path=folder / row["file"]
        if path.is_symlink() or not path.is_file() or path.stat().st_size>1024*1024 or hashlib.sha256(path.read_bytes()).hexdigest()!=row.get("sha256") or not re.fullmatch(r"[a-f0-9]{64}",str(row.get("source_sha256",""))):
            raise ValueError("Private video preview changed.")
        resources.append((path,"cr_recipe_frames/"+path.name))
    return resources


def compose_claude_instructions(original: bytes, overrides: bytes) -> bytes:
    """Restore private interactive creative context without publishing its text."""
    introduction = (
        "# ChannelRecipe private studio instructions\n\n"
        "The private creative guide follows. Use it for creative direction, "
        "style customization, specialist modules, visual verification and editing.\n\n"
        "The ChannelRecipe operational overrides at the end govern setup, account/key handling, "
        "billing, launch commands, MCP authorization and recovery whenever the older guide differs. "
        "Use local setup forms for keys; never collect or reveal them in chat. Launch production "
        "through CR's maintained tools and project/stage checks. Do not follow older standalone "
        "generation commands or API fallback instructions that bypass those checks.\n\n"
        "## ChannelRecipe creative guide\n\n"
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
    if any(not path.is_file() for path in PRIVATE_WORKFLOWS.values()):
        raise ValueError("The private reference animation workflow resources are required for this admin build.")
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
    # Validate the complete resource inventory before replacing an existing ZIP.
    resources = list(runtime_resources(ROOT))
    preview_resources = private_preview_resources(ROOT)
    target.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    transformed_files = []
    logo = ROOT / "scripts/assets/channelrecipe.png"
    brand_png = logo.read_bytes() if logo.is_file() and not logo.is_symlink() else None
    # A rejected unknown/binary branding hit must not destroy a prior download.
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".cr-build-", suffix=".zip", delete=False) as temporary:
        staged = Path(temporary.name)
    try:
        with zipfile.ZipFile(source) as original, zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as result:
            written = set()

            def write_resource(relative, contents, *, executable=False):
                branded, data, record = studio_branding.transform_resource(relative, contents, brand_png=brand_png)
                name = "ChannelRecipe studio/" + branded
                if name in written:
                    raise ValueError("Duplicate branded studio resource: " + name)
                written.add(name)
                if record:
                    transformed_files.append(record)
                info = zipfile.ZipInfo(name)
                info.external_attr = (0o100755 if executable else 0o100644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                result.writestr(info, data)

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
                # Preserve the source inventory and source hash; record every
                # owned branding transformation instead of claiming byte parity.
                relative = "CREATIVE_GUIDE.md" if str(rel) == "CLAUDE.md" else str(rel)
                write_resource(relative, original.read(info), executable=bool(info.external_attr >> 16 & 0o111))
                count += 1
            for path, relative in resources:
                contents = compose_claude_instructions(private_guide, operational_overrides) if path.name == "CLAUDE.md" else path.read_bytes()
                write_resource(relative, contents, executable=path.suffix == ".command")
            write_resource("cr-providers.json", (ROOT / "webapp/frontier/providers.json").read_bytes())
            for name, path in PRIVATE_WORKFLOWS.items():
                write_resource(name, path.read_bytes())
            for path, relative in preview_resources:
                write_resource(relative, path.read_bytes())
            write_resource("cr-release.json", json.dumps({
                "version": VERSION, "access": "admin_only", "source_sha256": SOURCE_SHA256,
                "public_release_allowed": False, "engine_files": count,
                "workflow_version": "channelrecipe-5.3-claudecode.1", "preferred_brain": "claudecode",
                "branding_version": studio_branding.BRANDING_VERSION,
                "note": "Owner preview contains private source. Public runtime split remains required."}, indent=2).encode())
            write_resource("cr-branding.json", json.dumps({
                "schema": 1, "branding_version": studio_branding.BRANDING_VERSION,
                "source_archive_sha256": SOURCE_SHA256, "source_engine_files": count,
                "transformed_files": transformed_files,
            }, indent=2).encode())
        with zipfile.ZipFile(staged) as archive:
            branding_report = studio_branding.assert_clean_archive(archive)
        staged.replace(target)
    finally:
        staged.unlink(missing_ok=True)
    with target.open("rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    return {"version": VERSION, "bytes": target.stat().st_size, "sha256": digest,
            "engine_files": count, "transformed_files": len(transformed_files),
            "branding": branding_report}


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
