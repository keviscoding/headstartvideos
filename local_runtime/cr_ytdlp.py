"""Studio's yt-dlp: the same yt-dlp, trusting this computer's certificates as the browser does (cr_trust).

yt-dlp runs as a process of its own, so trusting the computer's store inside Studio doesn't reach it: behind an
antivirus that scans secure connections every YouTube search and download failed its certificate check. Studio, the
engine and the Shorts pipelines run this file in place of the venv's yt-dlp (cr_trust.ytdlp_command)."""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def js_runtime(argv: list) -> list:
    """`--js-runtimes node` (or bun) when this computer has no Deno and the command names no runtime of its own.
    yt-dlp solves YouTube's JavaScript challenge with Deno only unless told, and without a runtime it asks YouTube
    through one client alone (visionos), so one bot check stopped every download of a style clone's references
    (Lee, 2026-11-01). With a runtime it tries tv_samsung, visionos and web in turn. Studio installs Node.js; the
    engine's own youtube.ytdlp() already adds it, and its commands keep theirs."""
    if shutil.which("deno") or any(a in ("--js-runtimes", "--no-js-runtimes") or a.startswith("--js-runtimes=")
                                   for a in argv):
        return []
    for name in ("node", "bun"):
        if shutil.which(name):
            return ["--js-runtimes", name]
    return []


def main():
    try:
        import cr_trust
        cr_trust.install()
    except Exception:                                          # noqa: BLE001 - yt-dlp runs as it always did
        pass
    sys.argv[1:] = js_runtime(sys.argv[1:]) + sys.argv[1:]
    # yt-dlp imports what `python -m yt_dlp` would: never one of the engine's files in this folder by accident
    sys.path[:] = [p for p in sys.path if os.path.normcase(os.path.abspath(p or os.curdir)) != os.path.normcase(HERE)]
    import yt_dlp
    return yt_dlp.main()


if __name__ == "__main__":
    sys.exit(main())
