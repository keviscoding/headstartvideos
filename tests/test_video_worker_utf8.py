"""The video worker runs the private engine in Python's UTF-8 mode (sweep 2026-10-25)."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_video_cancel


def test_the_video_worker_runs_the_engine_in_utf8_mode(tmp_path):
    """The private engine reads, writes and decodes tool output without naming an encoding in 81 places (cp1252 on
    Windows: a curly quote can't be read, an arrow, a Japanese title or an emoji can't be written). The video worker
    starts in Python's UTF-8 mode, as the Shorts do, so those behave as on a Mac."""
    options = cr_video_cancel.worker_options(tmp_path)
    assert options["env"]["PYTHONUTF8"] == "1" and options["env"]["KMP_DUPLICATE_LIB_OK"] == "TRUE"
    child = ("import sys; open('t.txt', 'w').write('→ ” の 👍'); "
             "print(sys.flags.utf8_mode, open('t.txt').read() == '→ ” の 👍', open('t.txt', 'rb').read() == '→ ” の 👍'.encode('utf-8'))")
    done = subprocess.run([sys.executable, "-c", child], stdout=subprocess.PIPE, **options, timeout=60)
    assert done.stdout.decode("utf-8").split() == ["1", "True", "True"]
