"""Carry CR authorization into Frontier's original isolated Python workers."""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import sys
import threading
import weakref

HOME=Path(__file__).resolve().parent
TARGETS={"timeline.py","offer.py","offer_pack.py","reflearn.py","threed.py"}
_original_popen=subprocess.Popen
_installed=False
_processes=weakref.WeakSet()
_lock=threading.Lock()


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
        import re
        if not re.fullmatch(r"crv_[a-f0-9]{24}",project_id or ""):
            raise RuntimeError("Connect CR and start this creative work through the studio first.")
        return list(command[:j])+[str(root/"cr_worker.py"),"--project",project_id,"--",str(target.resolve())]+list(command[j+1:])
    return command


def busy():
    with _lock:
        return any(p.poll() is None for p in list(_processes))


def install():
    global _installed
    if _installed:return
    def popen(command,*args,**kwargs):
        import cr_bridge
        project=cr_bridge._project or {}
        updated=rewrite(command,project.get("id"))
        process=_original_popen(updated,*args,**kwargs)
        if updated != command:
            with _lock:_processes.add(process)
        return process
    subprocess.Popen=popen
    _installed=True


def uninstall():
    global _installed
    if _installed:subprocess.Popen=_original_popen
    _installed=False
