"""Start ChannelRecipe Studio the way a creator's launcher does, and check it works (the Windows CI runs this).

    python tests/studio_smoke/smoke.py [--report studio-smoke.md] [--artifacts DIR] [--port 7860] [--python PY]

1. Builds a Studio folder like the download: Studio's own files (the release builder's own list and branding), its
   Claude Code files, the provider catalogue and the download's requirements, plus a small stand-in for the private
   engine (engine/), which never goes into this public repository. The folder sits in "Zoé Profile/Downloads", with a
   space and an accent, like many creators' folders.
2. Starts it with the real "Start on Windows.cmd" (elsewhere: the same steps by hand): a fresh .venv from the
   requirements, the graphics browser, then cr_studio.py. No account keys. Claude Code is npm's claude.cmd in the
   profile's AppData\\Roaming\\npm, a stand-in that is signed in.
3. Checks /cr-setup (served exactly as its UTF-8 file), /cr-status (Python, FFmpeg, Node, the graphics browser that
   cr_studio.graphics_ready() launches, and Claude Code's sign-in check through claude.cmd) and /cr-diagnostics, then
   loads the workspace and a 3D recipe's Create page in Chromium and asks /cr-3d for a 3D project's status.
4. Makes a spoken narration and times it with Whisper in a worker started as Studio starts video workers
   (narration_worker.py): base model, int8, CPU, KMP_DUPLICATE_LIB_OK.
Studio runs with PYTHONWARNDEFAULTENCODING=1: any Studio code that leaves a text encoding to the computer (cp1252 on
Windows) fails the run, as does any traceback in Studio's window."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
WINDOWS = os.name == "nt"
CLAUDE_VERSION = "2.1.300"
NARRATION = ("ChannelRecipe Studio is running on this computer. These captions follow the narration word by word, "
             "and the timing comes from Whisper.")
SECRET = re.compile(r"(_KEY|_TOKEN|_SECRET|_PASSWORD)$|^(ANTHROPIC_|CLAUDE|OPENAI_|GH_|GITHUB_TOKEN|ACTIONS_)", re.I)


class Report:
    def __init__(self):
        self.rows, self.notes = [], []

    def check(self, name, ok, detail=""):
        self.rows.append((name, bool(ok), str(detail)))
        print(("PASS " if ok else "FAIL ") + name + (": " + str(detail) if detail else ""), flush=True)
        return bool(ok)

    @property
    def ok(self):
        return bool(self.rows) and all(ok for _, ok, _ in self.rows)

    def markdown(self):
        cell = lambda text: text.replace("|", "\\|").replace("\n", "<br>")
        lines = ["### Studio on %s: %s" % ("Windows" if WINDOWS else sys.platform, "passed" if self.ok else "FAILED"), "",
                 "| | Check | Detail |", "|---|---|---|"]
        lines += ["| %s | %s | %s |" % ("✅" if ok else "❌", cell(name), cell(detail)) for name, ok, detail in self.rows]
        return "\n".join(lines + [""] + self.notes) + "\n"


def build_folder(studio: Path, port: int):
    """The download's Studio files, as scripts/build_frontier_admin.py packs them, with the stand-in engine."""
    from scripts import build_frontier_admin as builder, studio_branding

    def write(relative, data: bytes):
        name, data, _ = studio_branding.transform_resource(relative, data, brand_png=None)
        if port != 7860 and Path(name).suffix in {".py", ".html", ".js", ".json", ".md", ".cmd", ".command"}:
            data = data.replace(b"7860", str(port).encode())          # a second Studio beside a running one
        target = studio / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    for path, relative in builder.runtime_resources(ROOT):
        write(relative, path.read_bytes())
    for relative, data in builder.claude_code_files().items():
        write(relative, data)
    write("cr-providers.json", (ROOT / "webapp/frontier/providers.json").read_bytes())
    write("requirements.txt", (ROOT / "local_runtime/studio-requirements.txt").read_bytes())
    write("cr-release.json", json.dumps({"version": builder.VERSION, "access": "subscriber",
                                         "workflow_version": "channelrecipe-5.3-claudecode.1",
                                         "preferred_brain": "claudecode"}, indent=2).encode())
    write("cr-files.json", json.dumps({"schema": 1, "version": builder.VERSION, "files": {}, "retired": {}}).encode())
    for path in sorted((HERE / "engine").glob("*.py")):
        write(path.name, path.read_bytes())
    (studio / "output").mkdir(exist_ok=True)


def stand_in_claude(npm: Path):
    """npm's claude.cmd (claude elsewhere) for a stand-in Claude Code that's signed in; each call is written down."""
    package = npm / "node_modules" / "@anthropic-ai" / "claude-code"
    package.mkdir(parents=True)
    (package / "cli.js").write_text("""// A stand-in for Claude Code (tests/studio_smoke): signed in, it answers Studio's sign-in check.
const fs = require("fs"), path = require("path");
const args = process.argv.slice(2);
fs.appendFileSync(path.join(__dirname, "calls.log"), JSON.stringify(args) + "\\n");
if (args.includes("--version")) {
  console.log("%s (Claude Code)");
} else if (args.includes("auth") && args.includes("status")) {
  console.log(JSON.stringify({loggedIn: true, authMethod: "claude.ai", apiProvider: "firstParty",
                              email: "zoé@example.com", subscriptionType: "max"}));
} else {
  console.error("This stand-in Claude Code only answers the sign-in check: " + args.join(" "));
  process.exit(2);
}
""" % CLAUDE_VERSION, encoding="utf-8")
    if WINDOWS:
        # the shim npm writes (cmd-shim), which Windows can only start by its full path
        (npm / "claude.cmd").write_text("""@ECHO off
GOTO start
:find_dp0
SET dp0=%~dp0
EXIT /b
:start
SETLOCAL
CALL :find_dp0

IF EXIST "%dp0%\\node.exe" (
  SET "_prog=%dp0%\\node.exe"
) ELSE (
  SET "_prog=node"
  SET PATHEXT=%PATHEXT:;.JS;=;%
)

endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  "%dp0%\\node_modules\\@anthropic-ai\\claude-code\\cli.js" %*
""".replace("\n", "\r\n"), encoding="ascii", newline="")
    else:
        shim = npm / "claude"
        shim.write_text('#!/bin/sh\nexec node "$(dirname "$0")/node_modules/@anthropic-ai/claude-code/cli.js" "$@"\n',
                        encoding="utf-8")
        shim.chmod(0o755)
    return package / "calls.log"


def studio_environment(work: Path, profile: Path, npm: Path, isolate_home: bool):
    env = {k: v for k, v in os.environ.items() if not SECRET.search(k)}
    for name in ("PYTHONUTF8", "PYTHONIOENCODING", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(name, None)
    env["PATH"] = str(npm) + os.pathsep + env.get("PATH", "")
    env["PYTHONWARNDEFAULTENCODING"] = "1"      # Studio code that leaves an encoding to the computer warns
    env["CR_NOTIFY"] = "0"                      # no desktop notifications
    browser = work / ("no-browser.cmd" if WINDOWS else "no-browser")   # Studio opens its setup page: not here
    if WINDOWS:
        browser.write_text("@exit /b 0\r\n", encoding="ascii")
    else:
        browser.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        browser.chmod(0o755)
    env["BROWSER"] = str(browser)
    if isolate_home:
        # On a developer's own computer Studio must not touch their real ~/.channelrecipe or Claude Code settings;
        # the downloads it caches stay shared.
        home = Path.home()
        env.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(home / ("Library/Caches/ms-playwright" if sys.platform == "darwin"
                                                                else ".cache/ms-playwright")))
        env.setdefault("PIP_CACHE_DIR", str(home / ("Library/Caches/pip" if sys.platform == "darwin" else ".cache/pip")))
        env.setdefault("HF_HOME", str(home / ".cache" / "huggingface"))
        env["HOME"] = env["USERPROFILE"] = str(profile)
    return env


def start(studio: Path, env, log, python):
    if WINDOWS:
        launcher = studio / "Start on Windows.cmd"
        command = f'"{os.environ["ComSpec"]}" /d /s /c ""{launcher}" --cr-window"'
        return subprocess.Popen(command, cwd=studio, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    # what the launchers do: a fresh environment, the libraries, the graphics browser, then Studio
    script = ('set -e; "$CR_SMOKE_PYTHON" -m venv .venv; '
              '.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt; '
              '.venv/bin/python -m playwright install --only-shell chromium; exec .venv/bin/python cr_studio.py')
    return subprocess.Popen(["/bin/sh", "-c", script], cwd=studio, env={**env, "CR_SMOKE_PYTHON": python},
                            stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)


def stop(process):
    if process.poll() is not None:
        return
    if WINDOWS:
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except OSError:
            pass
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()


def get(url, headers=None, timeout=30):
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.headers.get("Content-Type", ""), response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("Content-Type", ""), error.read()


def tail(path: Path, lines=40):
    """The last lines of a log, without download progress bars."""
    try:
        rows = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join([row for row in rows if "\u25a0" not in row and not re.search(r"\d+% of [\d.]+ ?[KMG]i?B", row)][-lines:])


def wait_until_ready(url, process, log_path, minutes):
    deadline, said = time.monotonic() + minutes * 60, 0.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False, "the launcher stopped (exit %s) before Studio answered" % process.returncode
        try:
            get(url + "/cr-setup", timeout=5)           # any answer, even an error page: Studio is up (the checks judge it)
            return True, ""
        except (OSError, urllib.error.URLError):
            pass
        if time.monotonic() - said > 30:
            said = time.monotonic()
            last = tail(log_path, 1)
            print("... waiting for Studio: " + last[-160:], flush=True)
        time.sleep(2)
    return False, "Studio didn't answer within %d minutes" % minutes


def speak(text, wav: Path):
    """A spoken narration from the computer's own voice (Windows SAPI, macOS say); None when it has none."""
    try:
        if WINDOWS:
            script = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                      "$s.SetOutputToWaveFile($env:CR_SMOKE_WAV); $s.Speak($env:CR_SMOKE_TEXT); $s.Dispose()")
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], check=True, timeout=120,
                           capture_output=True, env={**os.environ, "CR_SMOKE_WAV": str(wav), "CR_SMOKE_TEXT": text})
        elif sys.platform == "darwin":
            wav = wav.with_suffix(".aiff")
            subprocess.run(["say", "-o", str(wav), text], check=True, timeout=120, capture_output=True)
        else:
            return None
        return wav if wav.is_file() and wav.stat().st_size > 10000 else None
    except (OSError, subprocess.SubprocessError):
        return None


def parse_srt(text):
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        rows = block.splitlines()
        if len(rows) >= 3:
            start, end = (sum(float(p.replace(",", ".")) * f for p, f in zip(t.strip().split(":"), (3600, 60, 1)))
                          for t in rows[1].split("-->"))
            cues.append((start, end, " ".join(rows[2:])))
    return cues


def check_workspace(report, studio, url):
    """Studio's own pages in a real browser: the workspace, a 3D recipe's Create page (3D scenes on, Fast mode
    offered), and a 3D project's /cr-3d status. A script error or a failing request on them fails the run."""
    from playwright.sync_api import sync_playwright
    problems, loaded = [], {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for name, address in (("workspace", url + "/"), ("3D recipe", url + "/?cr-preset=smoke-3d")):
                page = browser.new_page()
                page.on("pageerror", lambda error, n=name: problems.append(f"{n}: script error {error}"[:300]))
                page.on("console", lambda message, n=name: problems.append(f"{n}: {message.text}"[:300])
                        if message.type == "error" else None)
                page.on("response", lambda response, n=name: problems.append(
                    f"{n}: HTTP {response.status} {response.url.replace(url, '')}") if response.status >= 400 else None)
                page.goto(address, wait_until="networkidle", timeout=90000)
                page.wait_for_timeout(1500)
                loaded[name] = {"title": page.title(), "threed": page.evaluate(
                    "() => !!document.querySelector('#crw-features input[data-feature=threed]:checked')"),
                    "fast": page.is_visible("#crw-fast-section")}
                page.close()
        finally:
            browser.close()
    workspace, threed = loaded.get("workspace", {}), loaded.get("3D recipe", {})
    report.check("Workspace page loads in Chromium with no script errors or failing requests",
                 workspace.get("title", "").startswith("ChannelRecipe") and not problems,
                 "; ".join(problems[:4]) or workspace.get("title", ""))
    report.check("A 3D recipe's Create page loads: 3D scenes on, Fast mode offered", threed.get("threed")
                 and threed.get("fast"), "3D scenes %s, Fast mode section %s" % (threed.get("threed"), threed.get("fast")))
    job = studio / "output" / "smoke-3d" / "_work" / "a-3d-test-video"
    (job / "threed").mkdir(parents=True, exist_ok=True)
    answers = {}
    for slug in (job.name, "no-such-video"):
        status, _, body = get(url + "/cr-3d/" + slug, timeout=60)
        try:
            answers[slug] = (status, json.loads(body))
        except ValueError:
            answers[slug] = (status, {})
    report.check("A 3D project's scene status (/cr-3d/<slug>) answers", all(
        status == 200 and isinstance(data, dict) and "available" in data and "state" in data
        for status, data in answers.values()), "; ".join("%s: HTTP %s %s" % (slug, status, {k: data.get(k) for k in (
            "available", "state")}) for slug, (status, data) in answers.items()))


def check_speech_model(report, url, minutes=15):
    """Studio downloads the speech-timing model when it starts and loads it once in its own process (cr_whisper);
    setup shows the answer."""
    started, state = time.monotonic(), {}
    while time.monotonic() - started < minutes * 60:
        status, _, body = get(url + "/cr-setup/fix-speech-model", timeout=30)
        try:
            state = json.loads(body) if status == 200 else {}
        except ValueError:
            state = {}
        # downloading and checking are on the way; broken is final; missing or unchecked after a minute means
        # Studio didn't start (or finish) the download by itself
        if state.get("ready") or state.get("state") == "broken" or (
                state.get("state") in ("missing", "unchecked") and time.monotonic() - started > 60):
            break
        time.sleep(3)
    report.check("Setup's speech-timing model downloads and loads (Whisper base, int8, in its own process)",
                 state.get("ready"), "%.0f s" % (time.monotonic() - started) if state.get("ready")
                 else "%s: %s" % (state.get("state"), state.get("reason") or state.get("tail")))


def check_narration(report, studio, env, artifacts):
    folder = studio / "output" / "cr-smoke"
    folder.mkdir(parents=True, exist_ok=True)
    spoken = speak(NARRATION, folder / "narration.wav")
    mp3, srt = folder / "narration.mp3", folder / "narration.srt"
    source = ["-i", str(spoken)] if spoken else ["-f", "lavfi", "-i", "sine=frequency=330:duration=6"]
    subprocess.run(["ffmpeg", "-y", "-v", "error", *source, "-ac", "1", "-ar", "44100", "-c:a", "libmp3lame",
                    "-b:a", "128k", str(mp3)], check=True, timeout=120)
    if not spoken:
        report.notes.append("This computer has no speech voice, so the narration was a tone: Whisper had no words.")
    python = studio / ".venv" / ("Scripts/python.exe" if WINDOWS else "bin/python")
    started = time.monotonic()
    run = subprocess.run([str(python), str(HERE / "narration_worker.py"), str(mp3), str(srt), NARRATION], cwd=studio,
                         env=env, capture_output=True, encoding="utf-8", errors="replace", timeout=1200)
    seconds = time.monotonic() - started
    worker_log = folder / "worker.log"
    if worker_log.is_file():
        shutil.copy2(worker_log, artifacts / "narration-worker.log")
    try:
        result = json.loads(run.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        result = {"ok": False, "why": (run.stdout + run.stderr)[-600:]}
    log = tail(worker_log, 200)
    report.check("Video worker loads Whisper (base, int8, CPU) and times the narration", result.get("ok"),
                 "%.0f s" % seconds if result.get("ok") else result.get("why", ""))
    report.check("Worker started with KMP_DUPLICATE_LIB_OK (two OpenMP copies abort it on Windows)",
                 "KMP_DUPLICATE_LIB_OK=TRUE" in log)
    loaded = re.search(r"whisper: loading model '([^']*)'", log)
    report.check("The worker loads the model setup downloaded, not by name from huggingface.co",
                 loaded and loaded.group(1) != "base", loaded.group(1) if loaded else "no model load in the worker log")
    if not result.get("ok"):
        return
    try:
        cues = parse_srt(srt.read_text(encoding="utf-8"))           # strict: captions are UTF-8
    except (OSError, UnicodeDecodeError, ValueError) as error:
        report.check("Captions saved as UTF-8 with timings", False, "%s: %s" % (type(error).__name__, error))
        return
    words = [w for _, _, line in cues for w in re.findall(r"[a-z']+", line.lower())]
    ordered = all(a <= b <= c for (a, b, _), (c, _, _) in zip(cues, cues[1:])) and all(s <= e for s, e, _ in cues)
    duration = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                     str(mp3)], capture_output=True, encoding="utf-8", timeout=30).stdout.strip() or 0)
    heard = set(re.findall(r"[a-z']+", NARRATION.lower()))
    report.check("Captions saved as UTF-8 with timings in order, inside the narration",
                 cues and ordered and cues[-1][1] <= duration + 0.5,
                 "%d captions, %d words, %.1f s of %.1f s" % (len(cues), len(words), cues[-1][1] if cues else 0, duration))
    if spoken:
        found = sum(1 for w in set(words) if w in heard)
        report.check("Whisper heard the narration's words", found >= len(heard) // 2,
                     "%d of %d words: %s" % (found, len(heard), " ".join(words)[:200]))


def encoding_warnings(text):
    """Studio code (cr_*.py) that left a text encoding to the computer: Python's EncodingWarning lines."""
    return sorted(set(m.group(1) for m in re.finditer(r"(cr_\w+\.py:\d+): EncodingWarning", text)))


def tracebacks(text):
    """Each traceback's error line and its last frame."""
    found = []
    for block in re.findall(r"Traceback \(most recent call last\):\n(?:[ \t].*\n)*.*", text):
        frames = re.findall(r'File "(?:[^"]*[\\/])?([^"\\/]+)", line (\d+)', block)
        found.append(block.strip().splitlines()[-1] + (" (%s:%s)" % frames[-1] if frames else ""))
    return found


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")      # a CI log is a cp1252 pipe on Windows
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--artifacts", type=Path, default=Path("studio-smoke"))
    parser.add_argument("--python", default=sys.executable, help="the Python the launcher steps use (not on Windows)")
    parser.add_argument("--minutes", type=int, default=25, help="how long Studio's first start may take")
    parser.add_argument("--keep", action="store_true", help="keep the Studio folder")
    args = parser.parse_args()
    artifacts = args.artifacts.resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    report = Report()
    base = Path(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir())
    work = Path(tempfile.mkdtemp(prefix="cr-smoke-", dir=base))
    profile = work / "Zoé Profile"
    studio = profile / "Downloads" / "ChannelRecipe studio"
    npm = profile / "AppData" / "Roaming" / "npm"
    url = "http://127.0.0.1:%d" % args.port
    studio.mkdir(parents=True)
    npm.mkdir(parents=True)
    build_folder(studio, args.port)
    calls = stand_in_claude(npm)
    env = studio_environment(work, profile, npm, isolate_home=not os.environ.get("CI"))
    print("Studio folder: %s" % studio, flush=True)
    log_path = artifacts / "studio-window.log"
    process = None
    try:
        with open(log_path, "wb") as log:
            started = time.monotonic()
            process = start(studio, env, log, args.python)
            ready, why = wait_until_ready(url, process, log_path, args.minutes)
            report.check("Studio starts from its launcher: new .venv, libraries, graphics browser, cr_studio.py", ready,
                         "%.0f s" % (time.monotonic() - started) if ready else why + "\n" + tail(log_path, 25))
            if ready:
                check_pages(report, studio, url, calls)
                check_workspace(report, studio, url)
                check_speech_model(report, url)
                check_narration(report, studio, env, artifacts)
    finally:
        if process is not None:
            stop(process)
        window = tail(log_path, 100000)
        warned = encoding_warnings(window + tail(artifacts / "narration-worker.log", 100000))
        report.check("No Studio code leaves a text encoding to the computer (cp1252 on Windows)", not warned,
                     ", ".join(warned))
        crashed = tracebacks(window)
        report.check("No tracebacks in Studio's window", not crashed, "\n".join(crashed[:5]))
        python = re.search(r"Studio Python: (\S+)", window)
        if python:
            report.notes.append("Studio's Python: %s. Folder: `%s`." % (python.group(1), studio))
        if args.keep:
            print("Kept: %s" % work)
        else:
            shutil.rmtree(work, ignore_errors=True)
    text = report.markdown()
    print("\n" + text)
    if args.report:
        args.report.write_text(text, encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write(text)
    return 0 if report.ok else 1


def check_pages(report, studio, url, calls):
    token = json.loads((studio / ".cr-runtime.json").read_text(encoding="utf-8"))["token"]
    status, kind, body = get(url + "/cr-setup")
    page = (studio / "cr_setup.html").read_text(encoding="utf-8").replace("__LOCAL_TOKEN__", token)
    accents = sum(1 for ch in page if ord(ch) > 127)
    same = status == 200 and body.decode("utf-8", "replace") == page
    report.check("/cr-setup serves the setup page exactly as its UTF-8 file", same and accents,
                 "%d non-ASCII characters intact" % accents if same else "HTTP %s, %s, %d bytes differ from the file"
                 % (status, kind, abs(len(body) - len(page.encode()))))
    started = time.monotonic()
    status, _, body = get(url + "/cr-status", timeout=300)
    seconds = time.monotonic() - started
    try:
        checks = json.loads(body)
    except ValueError:
        checks = {}
    report.check("/cr-status answers", status == 200 and checks, "HTTP %s in %.1f s (its first, uncached check)"
                 % (status, seconds))
    report.check("Python 3.10+, FFmpeg with captions (libass, x264, aac, mp3) and Node found",
                 checks.get("python") and checks.get("ffmpeg") and checks.get("node"),
                 "python %s, ffmpeg %s, node %s" % (checks.get("python"), checks.get("ffmpeg"), checks.get("node")))
    report.check("Graphics browser draws, 2D and WebGL2 (cr_studio.graphics_ready)",
                 checks.get("browser") and checks.get("webgl"),
                 checks.get("browser_reason") or "browser %s, webgl %s" % (checks.get("browser"), checks.get("webgl")))
    if WINDOWS:
        paths = checks.get("file_paths") or {}
        report.check("Setup reads Windows' long-path setting and this folder's room (cr_paths)",
                     isinstance(paths.get("long_paths"), bool) and isinstance(paths.get("room"), int),
                     "folder %s characters, videos need up to %s more, long paths %s" % (
                         paths.get("folder_length"), paths.get("deepest"), paths.get("long_paths")))
    claude = checks.get("claude_code") or {}
    try:
        ran = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
    except OSError:
        ran = []
    report.check("Claude Code's sign-in check runs npm's claude.cmd by its full path",
                 claude.get("installed") and claude.get("authenticated") and claude.get("version") == CLAUDE_VERSION
                 and ["--version"] in ran and any(c[-2:] == ["auth", "status"] for c in ran),
                 "installed %s, version %s, signed in %s; Studio ran it %d times: %s" % (
                     claude.get("installed"), claude.get("version"), claude.get("authenticated"), len(ran),
                     "; ".join(" ".join(a or "\"\"" for a in c) for c in ran[:4])) + (
                     "" if claude.get("authenticated") else " | " + str(claude.get("message"))))
    status, kind, body = get(url + "/cr-diagnostics", headers={"X-CR-Local-Token": token}, timeout=300)
    text = body.decode("utf-8", "replace")
    expected = ["ChannelRecipe Studio diagnostics", "graphics browser yes", "FFmpeg yes", "Node yes",
                "Claude Code: installed yes %s · signed in yes" % CLAUDE_VERSION]
    if WINDOWS:
        expected.append("Computer: Windows")
    missing = [line for line in expected if line not in text]
    report.check("/cr-diagnostics (Copy diagnostics) answers with this computer's facts", status == 200 and not missing,
                 "missing: " + ", ".join(missing) if missing else next((l for l in text.splitlines()
                                                                         if l.startswith("Computer:")), ""))


if __name__ == "__main__":
    raise SystemExit(main())
