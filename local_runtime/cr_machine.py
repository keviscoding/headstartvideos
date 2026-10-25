"""What this computer has for making videos: memory, processor cores and free disk space.

Videos render on the creator's own computer, and nothing said what that computer needs: a disk that filled up halfway
through a long video stopped it with no reason anyone could read (the failure census, 2026-10-25: most failed videos
were local failures with no reason, and long videos failed most). Setup now shows what this computer has, with a note
when it is short of what Studio uses, and a video that can't fit on the disk stops before it starts, not halfway.

The numbers were measured on the owner's Mac (2026-10-25, the output folders of finished videos):
- a video's working folder stays on the disk at 0.2-0.3 GB a minute, and its finished files add 25-45 MB a minute;
  3D scenes add about 1 GB a minute (their rendered frames);
- while it renders, Studio runs up to 4 graphics-browser renders (about 1.1 GB together), up to 6 video encoders
  (about 260 MB each) and a final mux (about 600 MB), beside the engine and Claude Code: up to about 4 GB.
The site's "What your computer needs" (webapp/static/landing.html) says the same numbers; a test holds them together."""
from __future__ import annotations

import math
import os
import shutil
import sys
from pathlib import Path

BASE_GB = 1.0                   # Studio's own room on top of any video: caches, downloads, a margin
PER_MINUTE_GB = 0.5             # a video's working folder and finished files, a minute of video, with a margin
THREED_PER_MINUTE_GB = 1.0      # what 3D scenes add, a minute of video
STUDIO_MEMORY_GB = 4            # what Studio uses at most while it renders
MIN_MEMORY_GB = 8               # below this, other apps and a render compete for memory
GOOD_MEMORY_GB = 16             # comfortable for long and 3D videos
MIN_CORES = 4                   # below this, videos render, just more slowly
YARDSTICK_MINUTES = 10          # setup's note measures the disk against a 10-minute video
_GIB = 1024 ** 3                # memory as computers label it (a "16 GB" computer has 16 GiB)
_GB = 1000 ** 3                 # disk space as Finder and the site say it (the same unit cr_style_clone uses)


def memory_gb():
    """The computer's installed memory in GB (1 decimal), or None when it can't be read."""
    try:
        if os.name == "nt":
            import ctypes

            class MemoryStatus(ctypes.Structure):          # MEMORYSTATUSEX
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(MemoryStatus)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
            total = status.ullTotalPhys
        else:                                              # macOS and Linux
            total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        return None
    return round(total / _GIB, 1) if total and total > 0 else None


def cores():
    """Processor cores (logical, as the system counts them), or None."""
    return os.cpu_count() or None


def where(home) -> Path:
    """The folder whose disk a video fills: Studio's output folder, or Studio's own folder before the first video."""
    home = Path(home)
    return home / "output" if (home / "output").is_dir() else home


def free_disk_gb(home):
    """Free space on the disk Studio's videos go on, in GB (1 decimal), or None when it can't be read."""
    try:
        return round(shutil.disk_usage(where(home)).free / _GB, 1)
    except OSError:
        return None


def need_gb(minutes, videos=1, threed=False) -> float:
    """The free disk space `videos` videos of `minutes` minutes need while they're made (each keeps its working folder
    and finished files on the disk)."""
    try:
        minutes = max(1.0, float(minutes or 1))
    except (TypeError, ValueError):
        minutes = 1.0
    per_minute = PER_MINUTE_GB + (THREED_PER_MINUTE_GB if threed else 0)
    return round(BASE_GB + max(1, int(videos or 1)) * minutes * per_minute, 1)


def _gb(value) -> str:
    return f"{value:g}" if float(value).is_integer() else f"{value:.1f}"


def _empty_the_bin(platform=None) -> str:
    return "empty the Recycle Bin" if (platform or sys.platform) == "win32" else "empty the Bin"


def status(home, *, memory=..., cpu=..., free=...) -> dict:
    """Setup's "This computer" row: what it has and, in `notes`, where it is short of what Studio uses. Always a note,
    never a stop: a video checks the disk itself before it starts (disk_problem)."""
    memory = memory_gb() if memory is ... else memory
    cpu = cores() if cpu is ... else cpu
    free = free_disk_gb(home) if free is ... else free
    yardstick, yardstick_3d = need_gb(YARDSTICK_MINUTES), need_gb(YARDSTICK_MINUTES, threed=True)
    notes, short = [], []
    if memory is not None and memory < MIN_MEMORY_GB - 0.5:   # a little under 8 is an 8 GB computer (shared graphics)
        notes.append(f"This computer has {round(memory):d} GB of memory. Studio uses up to about {STUDIO_MEMORY_GB} GB "
                     f"while it renders a video, so {MIN_MEMORY_GB} GB or more works best ({GOOD_MEMORY_GB} GB for long "
                     f"or 3D videos). Close other apps while a video renders.")
        short.append("memory")
    if cpu is not None and cpu < MIN_CORES:
        notes.append(f"This computer has {cpu} processor cores. Videos still render, just more slowly than on "
                     f"{MIN_CORES} or more.")
        short.append("cores")
    if free is not None and free < yardstick:
        notes.append(f"{_gb(free)} GB is free on the disk Studio is on. A {YARDSTICK_MINUTES}-minute video needs about "
                     f"{_gb(yardstick)} GB while it's made ({_gb(yardstick_3d)} GB with 3D scenes): free up some space "
                     f"({_empty_the_bin()}, delete old downloads) before a long video. Studio checks the room before "
                     f"each video.")
        short.append("disk")
    return {"ready": True, "memory_gb": memory, "cores": cpu, "free_disk_gb": free,
            "ten_minute_video_gb": yardstick, "ten_minute_3d_video_gb": yardstick_3d,
            "short": short, "notes": notes, "message": " ".join(notes)}


def disk_problem(free, minutes, videos=1, threed=False, platform=None) -> str:
    """"" when `videos` videos of `minutes` minutes fit in `free` GB, else what to do, in plain words. Unknown free
    space never stops a video."""
    if free is None:
        return ""
    need = need_gb(minutes, videos, threed)
    if free >= need:
        return ""
    try:
        length = max(1, round(float(minutes or 1)))
    except (TypeError, ValueError):
        length = 1
    videos = max(1, int(videos or 1))
    what = (f"This {length}-minute video" if videos == 1 else f"These {videos} videos of {length} minutes")
    them = "it" if videos == 1 else "them"
    return (f"{what}{' with 3D scenes' if threed else ''} {'needs' if videos == 1 else 'need'} about {_gb(need)} GB "
            f"free on the disk Studio is on while {'it is' if videos == 1 else 'they are'} made, and {_gb(free)} GB is "
            f"free. Free up at least {_gb(math.ceil((need - free) * 10) / 10)} GB ({_empty_the_bin(platform)}, delete old "
            f"downloads, or move finished videos you've uploaded somewhere else), or make {them} shorter"
            f"{'' if videos == 1 else ' or fewer'}. Nothing has started or been paid for.")


def diagnostics_line(result) -> str:
    if not result:
        return ""
    show = lambda value, unit: (_gb(value) + unit) if value is not None else "unknown"   # noqa: E731
    return "This computer: memory %s · %s cores · %s free on Studio's disk%s" % (
        show(result.get("memory_gb"), " GB"), result.get("cores") or "unknown", show(result.get("free_disk_gb"), " GB"),
        " (short of: %s)" % ", ".join(result["short"]) if result.get("short") else "")
