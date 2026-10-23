"""Carry CR authorization into Frontier's original isolated Python workers."""
from __future__ import annotations
# asyncio first: on Windows it subclasses subprocess.Popen when it loads (asyncio.windows_utils). install() below
# replaces subprocess.Popen with a function, and subclassing a function fails, which broke Playwright's graphics
# browser inside Studio (found by a customer's Claude Code, 2026-10-23).
import asyncio  # noqa: F401
import os
from pathlib import Path
import subprocess
import sys
import threading
import weakref

HOME=Path(__file__).resolve().parent
TARGETS={"timeline.py","offer.py","offer_pack.py","reflearn.py","threed.py","app.py"}
_original_popen=subprocess.Popen
_installed=False
_processes=weakref.WeakSet()
_lock=threading.Lock()
_contained=False


def contain_children():
    """An isolated video's descendants must stay in its cancellable group."""
    global _contained
    _contained=True


def rewrite(command,project_id,*,home=None,python=None):
    if not isinstance(command,(list,tuple)):
        return command
    root=Path(home or HOME).resolve()
    interpreter=os.path.normcase(os.path.realpath(python or sys.executable))
    for i,arg in enumerate(command):
        if os.path.normcase(os.path.realpath(str(arg))) != interpreter:
            continue
        j=i+1
        while j<len(command) and command[j] in {"-u","-B","-E"}:
            j+=1
        if j>=len(command):return command
        target=Path(str(command[j]))
        if not target.is_absolute():target=root/target
        if target.resolve().parent != root or target.name not in TARGETS:
            return command
        if target.name == "threed.py" and (j+1 >= len(command) or command[j+1] not in {"qa","redo"}):
            return command
        if target.name == "app.py":
            # Ordinary standalone app invocations are not creative workers.
            return command
        import re
        if not re.fullmatch(r"crv_[a-f0-9]{24}",project_id or ""):
            raise RuntimeError("Connect CR and start this creative work through the studio first.")
        return list(command[:j])+[str(root/"cr_worker.py"),"--project",project_id,"--",str(target.resolve())]+list(command[j+1:])
    return command


def busy():
    with _lock:
        return any(p.poll() is None for p in list(_processes))


def windows_media_command(command):
    """Translate only the original Unix media-priority prefix on Windows.

    The native Windows encoder gets the exact same arguments. Shell commands,
    other priority values and Python/creative-worker prefixes are never parsed
    or stripped. The caller excludes shell/executable overrides.
    """
    if (os.name == "nt" and isinstance(command, (list, tuple))
            and len(command) >= 4 and list(command[:3]) == ["nice", "-n", "10"]
            and command[3] in {"ffmpeg", "ffprobe"}):
        return command[3:]
    return command


def install():
    global _installed
    if _installed:return
    def popen(command,*args,**kwargs):
        import cr_bridge
        project=cr_bridge._project or {}
        rewritten=rewrite(command,project.get("id"))
        # Positional Popen options may carry shell/executable overrides. Only
        # ordinary argv+keyword media calls have this compatibility translation.
        updated=(windows_media_command(rewritten) if not args and not kwargs.get("shell")
                 and kwargs.get("executable") is None else rewritten)
        if _contained and os.name != "nt":
            kwargs["start_new_session"] = False
            # This process already owns a fresh session/group. A nested CLI or
            # browser cannot escape cancellation into another one.
            kwargs.pop("process_group", None)
        process=_original_popen(updated,*args,**kwargs)
        if rewritten != command:
            with _lock:_processes.add(process)
        return process
    subprocess.Popen=popen
    _installed=True


def uninstall():
    global _installed
    if _installed:subprocess.Popen=_original_popen
    _installed=False
