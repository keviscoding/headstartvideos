"""Fast mode's sidecar Python has only ManimGX installed.

The setup probe and every scene render run cr_manimgx.py there as a script, so
it must load without the studio's own packages (requests, the Claude bridge).
"""
import subprocess
import sys
from pathlib import Path

MODULE = Path(__file__).parents[1] / "local_runtime" / "cr_manimgx.py"


def test_sidecar_script_loads_without_studio_packages():
    script = ("import runpy, sys\n"
              "for name in ('requests', 'cr_claude', 'cr_mcp', 'cr_bridge'):\n"
              "    sys.modules[name] = None\n"
              "sys.argv = ['cr_manimgx.py', '--unknown']\n"
              f"runpy.run_path({str(MODULE)!r}, run_name='__main__')\n")
    result = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, timeout=60)
    assert "Use ChannelRecipe's optional ManimGX setup" in result.stderr, result.stderr


def test_studio_import_keeps_the_shared_stop_error():
    sys.path.insert(0, str(MODULE.parent))
    try:
        import cr_claude
        import cr_manimgx
    finally:
        sys.path.remove(str(MODULE.parent))
    assert issubclass(cr_manimgx.ManimGXStageStopped, cr_claude.ClaudeStageStopped)
