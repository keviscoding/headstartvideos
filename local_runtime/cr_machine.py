"""What this computer has for making videos: memory, processor cores and free disk space.

Videos render on the creator's own computer, and nothing said what that computer needs: a disk that filled up halfway
through a long video stopped it with no reason anyone could read (the failure census, 2026-10-25: most failed videos
were local failures with no reason, and long videos failed most). Setup now shows what this computer has, with a note
when it is short of what Studio uses, and a video that can't fit on the disk stops before it starts, not halfway.

The numbers were measured on the owner's Mac (2026-10-25 and the review's 2026-10-26, the output folders of finished
videos):
- a video's working folder stays on the disk at 0.2-0.35 GB a minute, and its finished files add 25-45 MB a minute;
  3D scenes add about 1 GB a minute (their rendered frames). A video stops before it starts only when even that can't
  fit (NEED_*); below the comfortable margin (BASE_GB, PER_MINUTE_GB) it goes ahead with a warning. A batch is made one
  video at a time, so only the first video's need stops it; the rest are a warning;
- while it renders, Studio runs up to 4 graphics-browser renders (about 1.1 GB together), up to 6 video encoders
  (about 260 MB each) and a final mux (about 600 MB), beside the engine and Claude Code: up to about 4 GB.
The site's "What your computer needs" (webapp/static/landing.html) says the same numbers; a test holds them together."""
from __future__ import annotations

import math
import os
import shutil
import sys
from pathlib import Path

NEED_BASE_GB = 0.5              # the least Studio needs on top of a video: caches, downloads
NEED_PER_MINUTE_GB = 0.35       # a video's working folder and finished files, a minute of video, as measured
BASE_GB = 1.0                   # comfortable: Studio's own room with a margin...
PER_MINUTE_GB = 0.5             # ...and a minute of video with a margin
THREED_PER_MINUTE_GB = 1.0      # what 3D scenes add, a minute of video (their rendered frames, as measured)
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


def _existing(path):
    """The folder itself, or the nearest folder above it that exists (a disk is measured through a folder on it)."""
    path = Path(path).expanduser()
    for folder in (path, *path.parents):
        if folder.is_dir():
            return folder
    return None


def where(home, root=None) -> Path:
    """The folder whose disk a video fills: the engine's output folder (`root`, its OUTPUT_ROOT: CHANNELRECIPE_OUTPUT
    when that is set and can be written, an external drive say), else CHANNELRECIPE_OUTPUT when it is a folder, else
    Studio's output folder, or Studio's own folder before the first video."""
    home = Path(home)
    if root:
        found = _existing(root)
        if found is not None:
            return found
    moved = (os.environ.get("CHANNELRECIPE_OUTPUT") or "").strip()
    if moved and Path(moved).expanduser().is_dir():
        return Path(moved).expanduser()
    return home / "output" if (home / "output").is_dir() else home


def _mac_available(path):
    """The space Finder says is available on a Mac (it counts purgeable space, which macOS frees when it's needed:
    tens of GB with iCloud's Optimize Storage), in bytes, or None. CoreFoundation's own call, through ctypes."""
    try:
        import ctypes
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        cf.CFURLCreateFromFileSystemRepresentation.restype = ctypes.c_void_p
        cf.CFURLCreateFromFileSystemRepresentation.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long,
                                                               ctypes.c_ubyte]
        cf.CFURLCopyResourcePropertyForKey.restype = ctypes.c_ubyte
        cf.CFURLCopyResourcePropertyForKey.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                                       ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
        cf.CFNumberGetValue.restype = ctypes.c_ubyte
        cf.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p]
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        key = ctypes.c_void_p.in_dll(cf, "kCFURLVolumeAvailableCapacityForImportantUsageKey")
        raw = os.fsencode(str(path))
        url = cf.CFURLCreateFromFileSystemRepresentation(None, raw, len(raw), 1)
        if not url:
            return None
        try:
            value = ctypes.c_void_p()
            if not cf.CFURLCopyResourcePropertyForKey(url, key, ctypes.byref(value), None) or not value.value:
                return None
            number = ctypes.c_longlong()
            got = cf.CFNumberGetValue(value, 4, ctypes.byref(number))    # kCFNumberSInt64Type
            cf.CFRelease(value)
            return number.value if got and number.value > 0 else None
        finally:
            cf.CFRelease(url)
    except (AttributeError, OSError, ValueError):
        return None


def free_disk_gb(home, root=None):
    """Free space on the disk Studio's videos go on, in GB (1 decimal), or None when it can't be read. On a Mac it is
    what Finder says is available (purgeable space included)."""
    folder = where(home, root)
    try:
        free = shutil.disk_usage(folder).free
    except OSError:
        return None
    if sys.platform == "darwin":
        free = max(free, _mac_available(folder) or 0)
    return round(free / _GB, 1)


def folder_gb(path) -> float:
    """What a folder holds on the disk, in GB (a saved video's working folder); 0.0 when it can't be read."""
    total = 0
    try:
        for top, dirs, files in os.walk(path):
            for name in files:
                try:
                    st = os.lstat(os.path.join(top, name))
                except OSError:
                    continue
                total += st.st_size
    except OSError:
        return 0.0
    return total / _GB


def _minutes(minutes) -> float:
    try:
        return max(1.0, float(minutes or 1))
    except (TypeError, ValueError):
        return 1.0


def need_gb(minutes, videos=1, threed=False) -> float:
    """The least free disk space `videos` videos of `minutes` minutes need while they're made, as measured (each keeps
    its working folder and finished files on the disk): below it, the first one can't finish."""
    per_minute = NEED_PER_MINUTE_GB + (THREED_PER_MINUTE_GB if threed else 0)
    return round(NEED_BASE_GB + max(1, int(videos or 1)) * _minutes(minutes) * per_minute, 1)


def comfortable_gb(minutes, videos=1, threed=False) -> float:
    """need_gb with a margin: what they're comfortable with."""
    per_minute = PER_MINUTE_GB + (THREED_PER_MINUTE_GB if threed else 0)
    return round(BASE_GB + max(1, int(videos or 1)) * _minutes(minutes) * per_minute, 1)


def _gb(value) -> str:
    return f"{value:g}" if float(value).is_integer() else f"{value:.1f}"


def _empty_the_bin(platform=None) -> str:
    return "empty the Recycle Bin" if (platform or sys.platform) == "win32" else "empty the Bin"


def status(home, *, root=None, memory=..., cpu=..., free=...) -> dict:
    """Setup's "This computer" row: what it has and, in `notes`, where it is short of what Studio uses. Always a note,
    never a stop: a video checks the disk itself before it starts (disk_check). root: the engine's output folder."""
    memory = memory_gb() if memory is ... else memory
    cpu = cores() if cpu is ... else cpu
    free = free_disk_gb(home, root) if free is ... else free
    yardstick, yardstick_3d = need_gb(YARDSTICK_MINUTES), need_gb(YARDSTICK_MINUTES, threed=True)
    comfortable, comfortable_3d = comfortable_gb(YARDSTICK_MINUTES), comfortable_gb(YARDSTICK_MINUTES, threed=True)
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
    if free is not None and free < comfortable:
        notes.append(f"{_gb(free)} GB is free on the disk Studio's videos go on. A {YARDSTICK_MINUTES}-minute video "
                     f"needs about {_gb(yardstick)} GB while it's made ({_gb(yardstick_3d)} GB with 3D scenes), and "
                     f"{_gb(comfortable)} GB ({_gb(comfortable_3d)} GB) to be comfortable: free up some space "
                     f"({_empty_the_bin()}, delete old downloads) before a long video. Studio checks the room before "
                     f"each video.")
        short.append("disk")
    return {"ready": True, "memory_gb": memory, "cores": cpu, "free_disk_gb": free,
            "ten_minute_video_gb": yardstick, "ten_minute_3d_video_gb": yardstick_3d,
            "ten_minute_comfortable_gb": comfortable, "ten_minute_3d_comfortable_gb": comfortable_3d,
            "short": short, "notes": notes, "message": " ".join(notes)}


def disk_check(free, minutes, videos=1, threed=False, platform=None, saved=()) -> tuple:
    """(problem, warning) for `videos` videos of `minutes` minutes, made one at a time, with `free` GB free, in plain
    words ("" when there is none). problem (the video stops before it starts): the first video can't fit even at what
    was measured. warning (it goes ahead): it fits, but under the comfortable margin, or the rest of a batch may not.
    saved: GB each video's own working folder already holds (a saved project carrying on): taken off what it still
    needs, and a stop then never says nothing has started. Unknown free space is neither."""
    if free is None:
        return "", ""
    videos = max(1, int(videos or 1))
    try:
        saved = [max(0.0, float(x or 0)) for x in (saved or ())][:videos]
    except (TypeError, ValueError):
        saved = []
    saved += [0.0] * (videos - len(saved))
    try:
        length = max(1, round(float(minutes or 1)))
    except (TypeError, ValueError):
        length = 1
    resume = saved[0] > 0
    first = max(NEED_BASE_GB, need_gb(minutes, 1, threed) - saved[0]) if resume else need_gb(minutes, 1, threed)
    first = round(first, 1)
    room = (f"{_empty_the_bin(platform)}, delete old downloads, or move finished videos you've uploaded somewhere "
            f"else")
    three = " with 3D scenes" if threed else ""
    if free < first:
        free_up = _gb(math.ceil((first - free) * 10) / 10)
        if resume:
            what = (f"This {length}-minute video{three} needs about {_gb(first)} GB more free on the disk Studio's "
                    f"videos go on to finish (its saved work already holds {_gb(round(saved[0], 1))} GB)")
            return (f"{what}, and {_gb(free)} GB is free. Free up at least {free_up} GB ({room}). Its saved work is "
                    f"kept: carry on once there's room.", "")
        what = (f"This {length}-minute video{three} needs" if videos == 1 else
                f"The first of these {videos} videos of {length} minutes{three} (they're made one at a time) needs")
        return (f"{what} about {_gb(first)} GB free on the disk Studio's videos go on while it's made, and "
                f"{_gb(free)} GB is free. Free up at least {free_up} GB ({room}), or make "
                f"{'it' if videos == 1 else 'them'} shorter. Nothing has started or been paid for.", "")
    comfortable = round(max(NEED_BASE_GB, comfortable_gb(minutes, videos, threed) - sum(saved)), 1)
    if free >= comfortable:
        return "", ""
    more = _gb(math.ceil((comfortable - free) * 10) / 10)
    if videos == 1:
        return "", (f"This {length}-minute video{three} fits on the disk Studio's videos go on, with little to spare: "
                    f"{_gb(free)} GB is free and about {_gb(comfortable)} GB is comfortable. Freeing up {more} GB "
                    f"({room}) leaves it room to spare.")
    return "", (f"These {videos} videos of {length} minutes{three} are made one at a time and each keeps its working "
                f"folder: the first fits, but together they need about {_gb(comfortable)} GB and {_gb(free)} GB is "
                f"free, so a later one may stop for room. Free up about {more} GB ({room}) before they're all made, "
                f"or make fewer.")


def disk_problem(free, minutes, videos=1, threed=False, platform=None, saved=()) -> str:
    """disk_check's problem alone: "" when the first video fits (a warning never stops one)."""
    return disk_check(free, minutes, videos, threed, platform, saved)[0]


def diagnostics_line(result) -> str:
    if not result:
        return ""
    show = lambda value, unit: (_gb(value) + unit) if value is not None else "unknown"   # noqa: E731
    return "This computer: memory %s · %s cores · %s free on the videos' disk%s" % (
        show(result.get("memory_gb"), " GB"), result.get("cores") or "unknown", show(result.get("free_disk_gb"), " GB"),
        " (short of: %s)" % ", ".join(result["short"]) if result.get("short") else "")
