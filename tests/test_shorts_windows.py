"""The 3D and photo Shorts pipelines on Windows (their private source ships in data/frontier/shorts, so these run where
the release is built). A customer's 3D Explainer Short stopped at its director with [WinError 2]: the pipelines started
Claude Code as a bare "claude", which can't start npm's claude.cmd on Windows."""
import re
import subprocess
import sys
from pathlib import Path

import pytest

SHORTS = Path(__file__).resolve().parents[1] / "data" / "frontier" / "shorts"
pytestmark = pytest.mark.skipif(not (SHORTS / "hoodie" / "lib" / "shortsenv.py").is_file(),
                                reason="the private Shorts source is not in this checkout")


@pytest.fixture
def shortsenv(monkeypatch):
    monkeypatch.syspath_prepend(str(SHORTS / "hoodie" / "lib"))
    sys.modules.pop("shortsenv", None)
    import shortsenv
    return shortsenv


def test_claude_starts_by_its_full_path_with_the_prompt_on_stdin(shortsenv, monkeypatch):
    seen = {}
    monkeypatch.setattr(shortsenv, "claude", lambda: r"C:\Users\gab\AppData\Roaming\npm\claude.cmd")
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: seen.update(args=args, **kw) or "done")
    prompt = "Plan the Short.\nTopic: why dogs never feel full — café ✓"
    out = shortsenv.run_claude(["claude", "-p", prompt, "--model", "claude-opus-5-5", "--output-format", "json"],
                               capture_output=True, text=True, timeout=900, cwd="C:/work")
    assert out == "done"
    assert seen["args"] == [r"C:\Users\gab\AppData\Roaming\npm\claude.cmd", "-p", "--model", "claude-opus-5-5",
                            "--output-format", "json"], "no prompt on the command line (cmd.exe cuts it at a newline)"
    assert seen["input"] == prompt and seen["encoding"] == "utf-8" and seen["errors"] == "replace"
    assert "text" not in seen and seen["cwd"] == "C:/work" and seen["timeout"] == 900
    assert "ANTHROPIC_API_KEY" not in seen["env"], "the creator's own Claude login, never an inherited key"


def test_bytes_mode_and_given_input_are_left_as_they_are(shortsenv, monkeypatch):
    seen = {}
    monkeypatch.setattr(shortsenv, "claude", lambda: "/usr/local/bin/claude")
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: seen.update(args=args, **kw))
    shortsenv.run_claude(["claude", "-p", "hi", "--model", "m"], capture_output=True)
    assert seen["input"] == b"hi" and "encoding" not in seen
    shortsenv.run_claude(["claude", "-p", "--model", "m"], input="from stdin", text=True)
    assert seen["args"] == ["/usr/local/bin/claude", "-p", "--model", "m"] and seen["input"] == "from stdin"


def test_a_windows_path_in_an_ffmpeg_filter_keeps_its_drive_and_slashes(shortsenv):
    assert shortsenv.filter_path(r"C:\Users\gab\ChannelRecipe studio\output\x\captions.ass") == \
        r"C\:/Users/gab/ChannelRecipe studio/output/x/captions.ass"
    assert shortsenv.filter_path("/Users/me/ChannelRecipe studio/captions.ass") == "/Users/me/ChannelRecipe studio/captions.ass"


def test_no_pipeline_starts_claude_by_its_bare_name():
    bare = re.compile(r"subprocess\.(run|Popen|call|check_output)\(\s*\[\s*[\"']claude[\"']")
    for path in list((SHORTS / "zack").glob("*.py")) + list((SHORTS / "hoodie").rglob("*.py")) + list((SHORTS / "roblox").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert not bare.search(text), f"{path.name} starts a bare claude"
        for m in re.finditer(r"args = \[\s*[\"']claude[\"']", text):
            rest = text[m.end():m.end() + 600]
            assert "run_claude(args" in rest and "subprocess.run(args" not in rest.split("run_claude(args")[0], path.name
    zack = (SHORTS / "zack" / "make_zack.py").read_text(encoding="utf-8")
    assert "ass='{shortsenv.filter_path(captions)}':fontsdir='{shortsenv.filter_path(FONT_DIR)}'" in zack


def test_the_roblox_pipeline_starts_claude_and_burns_captions_the_windows_way():
    path = SHORTS / "roblox" / "make_roblox.py"
    if not path.is_file():
        pytest.skip("the Roblox Story Shorts source is not in this checkout")
    text = path.read_text(encoding="utf-8")
    assert "safe_run" not in text and "clean_env" not in text, "the dev harness's helpers stay out of the Studio"
    assert text.count("shortsenv.run_claude(") == 1 and "def claude(" in text, "every Claude call goes through one helper"
    assert "ass='{shortsenv.filter_path(captions)}':fontsdir='{shortsenv.filter_path(font_dir)}'" in text
    assert "/System/Library" not in text and "/opt/homebrew" not in text and '+ ":" +' not in text
