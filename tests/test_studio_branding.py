"""Distribution branding tests use synthetic source, never private prompts."""
import hashlib
import io
import zipfile

import pytest

from scripts import studio_branding as branding


def test_matching_python_names_env_keys_and_asset_paths_remain_consistent():
    source = '''\nFRONTIER_OUTPUT = "test"\ndef frontier_value():\n    return FRONTIER_OUTPUT\nassert frontier_value() == "test"\n'''.encode()
    name, contents, record = branding.transform_resource("frontier_engine.py", source)
    namespace = {}
    exec(compile(contents, name, "exec"), namespace)
    assert namespace["channelrecipe_value"]() == "test"
    assert "FRONTIER_OUTPUT" not in namespace
    assert record["source_sha256"] == hashlib.sha256(source).hexdigest()
    assert record["packaged_sha256"] == hashlib.sha256(contents).hexdigest()
    assert branding.brand_path("assets/Frontier/title.txt") == "assets/ChannelRecipe/title.txt"
    assert branding.brand_path("FRONTIER_GUIDE.md") == "CREATIVE_GUIDE.md"


def test_unrelated_unicode_and_nonmatching_binary_stay_byte_identical():
    for name, data in [("guide.md", "日本語\r\nCreative direction.\r\n".encode()),
                       ("sample.jpg", b"\x00\xffopaque bytes")]:
        assert branding.transform_resource(name, data) == (name, data, None)


@pytest.mark.parametrize("extension", [".cmd", ".bat", ".CMD", ".BAT"])
def test_windows_launchers_use_crlf_even_when_no_branding_is_present(extension):
    source = b"@echo off\nsetlocal\r\nrem Existing ChannelRecipe launcher\r\npause\n"
    name, contents, record = branding.transform_resource("Start" + extension, source)
    assert name == "Start" + extension
    assert contents == b"@echo off\r\nsetlocal\r\nrem Existing ChannelRecipe launcher\r\npause\r\n"
    assert b"\n" not in contents.replace(b"\r\n", b"")
    assert record["transformation"] == "windows_line_endings"
    assert record["source_sha256"] == hashlib.sha256(source).hexdigest()
    assert record["packaged_sha256"] == hashlib.sha256(contents).hexdigest()
    assert branding.transform_resource(name, contents) == (name, contents, None)


def test_windows_launcher_branding_and_newlines_are_both_applied():
    _, contents, record = branding.transform_resource("Start on Windows.cmd", b"@echo off\necho Frontier\npause\n")
    assert contents == b"@echo off\r\necho ChannelRecipe\r\npause\r\n"
    assert record["transformation"] == "text_branding"


@pytest.mark.parametrize("name,content,reason", [
    ("asset.bin", b"Frontier", "unsupported binary"),
    ("source.py", b"\xffFrontier", "non-UTF-8"),
    ("source.py", b"\x00Frontier", "binary resource"),
])
def test_unknown_binary_or_encoding_is_rejected_without_corruption(name, content, reason):
    with pytest.raises(ValueError, match=reason):
        branding.transform_resource(name, content)


def test_owned_old_logo_uses_existing_cr_asset_not_byte_edits():
    png = b"\x89PNG\r\n\x1a\nexisting-cr-icon"
    for old in branding.BRAND_IMAGES:
        name, content, record = branding.transform_resource(old, b"old-logo-image", brand_png=png)
        assert "frontier" not in name.lower()
        assert content == png
        assert record["transformation"] == "owned_brand_asset"
    with pytest.raises(ValueError, match="PNG app icon"):
        branding.transform_resource("assets/brand/frontier.png", b"old-logo")


@pytest.mark.parametrize("relative", ["../escape.py", "/absolute.py", ""])
def test_safe_distribution_paths_only(relative):
    with pytest.raises(ValueError, match="safe relative"):
        branding.brand_path(relative)


@pytest.mark.parametrize("first_chunk_letters", range(1, 8))
def test_final_archive_scan_catches_a_reference_across_stream_chunk_boundary(first_chunk_letters):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("ChannelRecipe studio/media.bin", b"a" * (1024 * 1024 - first_chunk_letters) + b"FrOnTiEr")
    stream.seek(0)
    with zipfile.ZipFile(stream) as archive:
        with pytest.raises(ValueError, match="archive member"):
            branding.assert_clean_archive(archive)


def test_final_archive_scan_rejects_duplicate_and_legacy_filenames():
    for names, reason in [("Frontier/guide.md", "archive path"),
                          (["ChannelRecipe/guide.md", "ChannelRecipe/guide.md"], "Duplicate")]:
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for name in [names] if isinstance(names, str) else names:
                archive.writestr(name, b"content")
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            with pytest.raises(ValueError, match=reason):
                branding.assert_clean_archive(archive)
