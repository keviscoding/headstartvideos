"""Studio's yt-dlp: the same yt-dlp, trusting this computer's certificates as the browser does (cr_trust).

yt-dlp runs as a process of its own, so trusting the computer's store inside Studio doesn't reach it: behind an
antivirus that scans secure connections every YouTube search and download failed its certificate check. Studio, the
engine and the Shorts pipelines run this file in place of the venv's yt-dlp (cr_trust.ytdlp_command)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    try:
        import cr_trust
        cr_trust.install()
    except Exception:                                          # noqa: BLE001 - yt-dlp runs as it always did
        pass
    # yt-dlp imports what `python -m yt_dlp` would: never one of the engine's files in this folder by accident
    sys.path[:] = [p for p in sys.path if os.path.normcase(os.path.abspath(p or os.curdir)) != os.path.normcase(HERE)]
    import yt_dlp
    return yt_dlp.main()


if __name__ == "__main__":
    sys.exit(main())
