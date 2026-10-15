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
VERSION = "5.3-admin.60"
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
    """Package hash-bound style reference frames shown on recipe cards.

    These are labelled references for each recipe's look (with the source
    channel credited), not Studio output. The retired engine sample frames
    are never packaged.
    """
    folder=root / "data/frontier/recipe-references"
    if not folder.exists():
        return []
    manifest=folder / "manifest.json"
    if folder.is_symlink() or not folder.is_dir() or manifest.is_symlink() or not manifest.is_file():
        raise ValueError("Style reference frames must be ordinary owned files.")
    data=json.loads(manifest.read_text())
    if data.get("schema")!=2 or data.get("kind")!="style_reference" or not isinstance(data.get("frames"),dict):
        raise ValueError("Style reference frames need a schema 2 manifest.")
    resources=[(manifest,"cr_recipe_frames/manifest.json")]
    for style,row in data["frames"].items():
        if (not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,139}",style) or not isinstance(row,dict)
                or row.get("file")!=style+".jpg" or row.get("kind") not in ("style_reference","studio_sample")
                or (row.get("kind")=="style_reference" and not str(row.get("channel") or "").strip())):
            raise ValueError("Invalid style reference identity.")
        path=folder / row["file"]
        if path.is_symlink() or not path.is_file() or path.stat().st_size>1024*1024 or hashlib.sha256(path.read_bytes()).hexdigest()!=row.get("sha256"):
            raise ValueError("Style reference frame changed.")
        resources.append((path,"cr_recipe_frames/"+path.name))
    return resources


def private_sound_resources(root: Path):
    """ChannelRecipe's recorded effects library (generated once, owned), shipped under assets/sfx/cr/."""
    folder = root / "data/frontier/sfx-library"
    if not folder.exists():
        return []
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("The effects library must be an ordinary owned folder.")
    resources = []
    for path in sorted(folder.iterdir()):
        if path.name == "library.json" or (path.suffix == ".wav" and re.fullmatch(r"[a-z_]+_\d+\.wav", path.name)):
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("Effects library files must be small ordinary files.")
            resources.append((path, "assets/sfx/cr/" + path.name))
    # the sound of each place (cr_ambience): seamless loops, one folder deeper
    places = folder / "ambience"
    if places.exists():
        if places.is_symlink() or not places.is_dir():
            raise ValueError("The ambience library must be an ordinary owned folder.")
        for path in sorted(places.iterdir()):
            if path.name == "library.json" or re.fullmatch(r"[a-z]+_\d+\.mp3", path.name):
                if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
                    raise ValueError("Ambience library files must be small ordinary files.")
                resources.append((path, "assets/sfx/cr/ambience/" + path.name))
    return resources


# Engine sample films from the retired brand; recipe cards use style references.
RETIRED_SAMPLES = {"assets/brand/1965 Lotus 38 - Documentary sample.mp4",
                   "assets/brand/POV Paid-Off Civic - 2D Stories sample.mp4",
                   "assets/brand/The Shadow You Keep Feeding Without Knowing It.mp4"}


# Reviewed fixes to the pinned engine for customers' computers. Its machine
# checks were tuned for a dedicated render box: a Mac with under 20 GB free
# disk skipped the style study's real-browser test (so the study stopped at the
# end) and made Level-Up Stories wait forever. A few test frames need far less.
# Every patch must match exactly once, so a new engine can't silently skip one.
ENGINE_PATCHES = {
    "reflearn.py": [
        ('need_lv, need_gb = _envf("REFLEARN_MIN_MEM_LEVEL", 40), _envf("REFLEARN_MIN_DISK_GB", 20)',
         'need_lv, need_gb = _envf("REFLEARN_MIN_MEM_LEVEL", 20), _envf("REFLEARN_MIN_DISK_GB", 3)'),
    ],
    # The engine's own guide says "python app.py", which started the bare engine
    # (no setup page, keys in .env). Starting it that way now opens Studio.
    "app.py": [
        ('if __name__ == "__main__":\n    OUT.mkdir(exist_ok=True)\n',
         'if __name__ == "__main__":\n'
         '    # ChannelRecipe Studio runs this engine inside its own app (setup, accounts and\n'
         '    # workspace). Starting the engine on its own hands over to Studio.\n'
         '    import subprocess\n'
         '    print("Starting ChannelRecipe Studio...", flush=True)\n'
         '    raise SystemExit(subprocess.call([sys.executable, str(HERE / "cr_studio.py")], cwd=str(HERE)))\n'
         'if False:\n    OUT.mkdir(exist_ok=True)\n'),
    ],
    "quest_hud.py": [
        ("def _wait_machine(log=print, min_level: int = 40, min_free_gb: float = 20.0) -> None:",
         "def _wait_machine(log=print, min_level: int = 20, min_free_gb: float = 3.0) -> None:"),
        ("    lvl = 45 if light else 40\n", "    lvl = 25 if light else 20\n"),
    ],
}


def patch_engine(relative: str, contents: bytes) -> bytes:
    patches = ENGINE_PATCHES.get(relative)
    if not patches:
        return contents
    text = contents.decode("utf-8")
    for old, new in patches:
        if text.count(old) != 1:
            raise ValueError("Engine patch no longer matches " + relative + ". Review it against the new source.")
        text = text.replace(old, new)
    return text.encode("utf-8")


def compose_claude_instructions(original: bytes, overrides: bytes) -> bytes:
    """Restore private interactive creative context without publishing its text."""
    introduction = (
        "# ChannelRecipe studio instructions\n\n"
        "If the customer asks you to install, set up or start ChannelRecipe Studio, go straight to "
        "\"If they ask you to install or set up Studio\" in the operational overrides at the end "
        "of this file and follow those steps.\n\n"
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


def previous_release(folder: Path) -> Path | None:
    """The newest earlier release ZIP built on this machine, if any."""
    current = int(VERSION.rsplit(".", 1)[1])
    found = []
    for path in folder.glob("channelrecipe-5.3-admin.*.zip"):
        build_number = path.name[len("channelrecipe-5.3-admin."):-len(".zip")]
        if build_number.isdigit() and int(build_number) < current:
            found.append((int(build_number), path))
    return max(found)[1] if found else None


def retired_files(previous: Path | None, shipped: set) -> dict:
    """Files earlier releases shipped that this one doesn't, with every hash they shipped under.

    Installed Studios remove these on update/start only while the file still
    has one of those hashes, so creator edits are never deleted.
    """
    if previous is None:
        return {}
    with zipfile.ZipFile(previous) as archive:
        try:
            listing = json.loads(archive.read("ChannelRecipe studio/cr-files.json"))
        except KeyError:
            return {}
    retired = {}
    for path, hashes in (listing.get("retired") or {}).items():
        retired.setdefault(path, set()).update(hashes)
    for path, digest in (listing.get("files") or {}).items():
        retired.setdefault(path, set()).add(digest)
    return {path: sorted(hashes) for path, hashes in sorted(retired.items()) if path not in shipped}


def build(source: Path, target: Path, previous: Path | None = None):
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
    preview_resources = private_preview_resources(ROOT) + private_sound_resources(ROOT)
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
            shipped = {}

            def write_resource(relative, contents, *, executable=False):
                branded, data, record = studio_branding.transform_resource(relative, contents, brand_png=brand_png)
                name = "ChannelRecipe studio/" + branded
                if name in written:
                    raise ValueError("Duplicate branded studio resource: " + name)
                written.add(name)
                shipped[branded] = hashlib.sha256(data).hexdigest()
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
                if str(rel) in RETIRED_SAMPLES:
                    continue
                # Preserve the source inventory and source hash; record every
                # owned branding transformation instead of claiming byte parity.
                relative = "CREATIVE_GUIDE.md" if str(rel) == "CLAUDE.md" else str(rel)
                write_resource(relative, patch_engine(str(rel), original.read(info)),
                               executable=bool(info.external_attr >> 16 & 0o111))
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
                "version": VERSION, "access": "subscriber", "source_sha256": SOURCE_SHA256,
                "public_release_allowed": True, "engine_files": count,
                "workflow_version": "channelrecipe-5.3-claudecode.1", "preferred_brain": "claudecode",
                "branding_version": studio_branding.BRANDING_VERSION,
                "note": "ChannelRecipe Studio for Creator, Pro and Max subscribers."}, indent=2).encode())
            write_resource("cr-branding.json", json.dumps({
                "schema": 1, "branding_version": studio_branding.BRANDING_VERSION,
                "source_archive_sha256": SOURCE_SHA256, "source_engine_files": count,
                "transformed_files": transformed_files,
            }, indent=2).encode())
            # Every shipped file's hash, so the in-place updater can tell which
            # installed files a creator edited and keep them, plus the files
            # earlier releases shipped that should now be removed.
            retired = retired_files(previous, set(shipped) | {"cr-files.json"})
            write_resource("cr-files.json", json.dumps({
                "schema": 1, "version": VERSION, "files": dict(sorted(shipped.items())),
                "retired": retired,
            }, indent=1).encode())
        with zipfile.ZipFile(staged) as archive:
            branding_report = studio_branding.assert_clean_archive(archive)
        staged.replace(target)
    finally:
        staged.unlink(missing_ok=True)
    with target.open("rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    return {"version": VERSION, "bytes": target.stat().st_size, "sha256": digest,
            "engine_files": count, "transformed_files": len(transformed_files), "retired_files": len(retired),
            "branding": branding_report}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "data/frontier" / ("channelrecipe-" + VERSION + ".zip"))
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--previous", type=Path, help="Earlier release ZIP whose dropped files to retire "
                        "(default: newest earlier release in the output folder)")
    args = parser.parse_args()
    previous = args.previous or previous_release(args.output.parent)
    manifest = build(args.source, args.output, previous)
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
            from boto3.s3.transfer import TransferConfig
            # Few parallel parts: large single bursts were dropped by the storage endpoint.
            transfer = TransferConfig(multipart_threshold=16 * 1024 * 1024, multipart_chunksize=16 * 1024 * 1024,
                                      max_concurrency=2)
            client.upload_file(str(args.output), bucket, RELEASE_KEY, Config=transfer,
                               ExtraArgs={"ACL": "private", "ContentType": "application/zip",
                                          "CacheControl": "private, no-store", "Metadata": {"sha256": manifest["sha256"]}})
            verify_private(client, bucket, RELEASE_KEY, endpoint, config.SPACES_CDN_ENDPOINT)
        except Exception:
            client.delete_object(Bucket=bucket, Key=RELEASE_KEY)
            raise
        print("Private release uploaded; object ACL and anonymous access checked.")
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
