"""Setup's Fix its for real, with the network (the Windows CI runs this; it works on a Mac too):

    python tests/studio_smoke/fixits_check.py [--claude] [--tries 3] [--report fixits.md]

(.github/workflows/setup-fixits.yml runs it on Windows: on pull requests that change the installer code, weekly and by
hand.)

1. FFmpeg: cr_installs.fix_ffmpeg downloads this computer's pinned build, checks its SHA-256, runs FFmpeg's own
   checks (captions with libass, H.264, AAC, MP3) and moves it into a fresh Studio folder's tools/ffmpeg, in a
   folder with a space and an accent like many creators' folders. Then a real caption burn by bare name, through
   the PATH use_studio_tools() set.
2. Node.js: fix_node checks nodejs.org's SHASUMS256.txt and the pin, takes the node program, runs it.
3. With --claude (CI only, never on a computer that already has Claude Code): fix_claude runs Anthropic's official
   installer and Studio must then find the new claude and run `claude --version`. No sign-in.
The unit tests (tests/test_studio_installs.py) fake every download; this is the one place the real files are
fetched, so a moved or changed upstream file shows up here before a customer presses Fix it."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "local_runtime"))
import cr_installs  # noqa: E402

# The Windows runner's console is cp1252; the installers print characters it can't encode ('‼'), which crashed the
# report itself and hid the install's real outcome (2026-10-26).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 640
PlayResY: 360

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,3,0,2,10,10,30,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,Captions café
"""


def check(rows, name, ok, detail=""):
    rows.append((name, bool(ok), str(detail)))
    print(("PASS " if ok else "FAIL ") + name + (": " + str(detail) if detail else ""), flush=True)
    return bool(ok)


def burn(work: Path) -> str:
    """A 2-second H.264 + AAC video with burned ASS captions, then an MP3, by the bare names Studio's engine uses."""
    (work / "t.ass").write_text(ASS, encoding="utf-8")
    steps = [["ffmpeg", "-hide_banner", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=640x360:d=2",
              "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-vf", "subtitles=t.ass", "-c:v", "libx264",
              "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", "out.mp4"],
             ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", "out.mp4", "-c:a", "libmp3lame", "out.mp3"],
             ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0", "out.mp4"]]
    output = ""
    for command in steps:
        result = subprocess.run(command, cwd=str(work), capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=120)
        if result.returncode:
            return f"{command[0]} failed: {(result.stderr or result.stdout)[-300:]}"
        output = result.stdout
    codecs = sorted(output.split())
    return "" if codecs == ["aac", "h264"] and (work / "out.mp3").stat().st_size > 1000 else f"made {codecs}"


def attempt(run, name, tries) -> dict:
    """Run one fix, again after a minute when it failed (an upstream hiccup), up to `tries` times."""
    for n in range(1, max(1, tries) + 1):
        run()
        state = cr_installs.status(name)
        if state["state"] == "done" or n == tries:
            return state
        print(f"  {name} try {n} failed ({state['message'][:200]}); trying again in a minute", flush=True)
        time.sleep(60)
    return state


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--claude", action="store_true", help="also run Anthropic's installer (CI runners only)")
    parser.add_argument("--tries", type=int, default=1, help="tries per fix, a minute apart (an upstream hiccup)")
    parser.add_argument("--report", default="")
    args = parser.parse_args()
    rows = []
    key = cr_installs.platform_key()
    base = Path(tempfile.mkdtemp(prefix="cr-fixits-"))
    home = base / "Zoé Profile" / "ChannelRecipe studio"
    home.mkdir(parents=True)
    print(f"Studio folder: {home} ({key or 'no pinned builds'})", flush=True)
    if not check(rows, "This computer has pinned builds", key in cr_installs.FFMPEG, key):
        return 1
    for name, fix, label in (("ffmpeg", cr_installs.fix_ffmpeg, "FFmpeg"), ("node", cr_installs.fix_node, "Node.js")):
        started = time.monotonic()
        state = attempt(lambda: fix(home=home, background=False), name, args.tries)
        check(rows, f"{label}: downloaded, SHA-256 checked, run and moved into tools/{name}", state["state"] == "done",
              f"{state['message']} ({time.monotonic() - started:.0f} s){' | ' + state.get('tail', '') if state.get('tail') else ''}")
    folders = [str(p) for p in cr_installs.tool_dirs(home)]
    check(rows, "Studio's tool folders are first on PATH", os.environ["PATH"].split(os.pathsep)[:2] == folders, folders)
    check(rows, "FFmpeg by bare name burns captions and makes H.264, AAC and MP3 (Studio's check)",
          cr_installs.video_tools_problem() == "", cr_installs.video_tools_problem())
    work = base / "burn"
    work.mkdir()
    problem = burn(work)
    check(rows, "A real caption burn with the installed FFmpeg", not problem, problem)
    node = cr_installs.node_status()
    check(rows, f"Node.js {cr_installs.NODE_MINIMUM}+ by bare name (yt-dlp's YouTube helper)", node["ready"], node)
    if args.claude:
        import cr_claude
        before = cr_claude.find_executable()
        if before:
            check(rows, "Claude Code installer (skipped: claude is already here)", True, before)
        else:
            started = time.monotonic()
            state = attempt(lambda: cr_installs.fix_claude(background=False), "claude", args.tries)
            found = cr_claude.find_executable()
            check(rows, "Anthropic's installer ran and Studio found the new claude", state["state"] == "done" and found,
                  f"{found} ({time.monotonic() - started:.0f} s) {state['message'][:200]} {state.get('tail', '')[-300:]}")
            if found:
                version = subprocess.run([found, "--version"], capture_output=True, text=True, encoding="utf-8",
                                         errors="replace", timeout=120, env=cr_claude.clean_environment())
                check(rows, "The installed claude runs", version.returncode == 0, version.stdout.strip()[:80])
                status = cr_claude.status(found)
                check(rows, "Studio's Claude check sees it installed (not signed in, as expected)",
                      status["installed"] and not status["authenticated"], status.get("message", ""))
    if args.report:
        lines = ["### Setup Fix its for real (%s)" % key, "", "| | Check | Detail |", "|---|---|---|"]
        lines += ["| %s | %s | %s |" % ("✅" if ok else "❌", name, detail.replace("|", "\\|").replace("\n", " "))
                  for name, ok, detail in rows]
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if all(ok for _, ok, _ in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
