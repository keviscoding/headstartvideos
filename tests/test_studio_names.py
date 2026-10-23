"""Every name Studio's modules use is defined. Studio 94 shipped a voice hook calling a function that a refactor had
moved (voiceover), which only failed when a video reached that hook."""
import subprocess
import sys
from pathlib import Path

import pytest

RUNTIME = Path(__file__).resolve().parents[1] / "local_runtime"


def test_no_studio_module_uses_an_undefined_name():
    pytest.importorskip("pyflakes")
    files = sorted(str(p) for p in RUNTIME.glob("cr_*.py"))
    out = subprocess.run([sys.executable, "-m", "pyflakes", *files], capture_output=True, text=True).stdout
    undefined = [line for line in out.splitlines() if "undefined name" in line]
    assert not undefined, "\n".join(undefined)
