"""The Studio page keeps its place through the site's background account refresh (tests/js/test_studio_refresh.js)."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _node_path():
    for folder in (ROOT / "node_modules", ROOT.parent / "headstartvideos" / "node_modules"):
        if (folder / "jsdom").is_dir():
            return str(folder)
    return None


def test_the_account_refresh_never_rebuilds_the_studio_page():
    node, modules = shutil.which("node"), _node_path()
    if not node or not modules:
        pytest.skip("needs node and jsdom")
    env = {**os.environ, "NODE_PATH": modules}
    result = subprocess.run([node, str(ROOT / "tests" / "js" / "test_studio_refresh.js")], env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
