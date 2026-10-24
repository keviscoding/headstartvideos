"""Shared test settings."""
import io
import pathlib
import sys
import warnings
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
RUNTIME = TESTS.parent / "local_runtime"
_DEFAULT_ENCODING = set()
_read_text, _write_text = pathlib.Path.read_text, pathlib.Path.write_text


def _from_a_test():
    """Was the read_text()/write_text() that called this made by a test file itself?"""
    return Path(sys._getframe(2).f_code.co_filename).parent == TESTS


# The tests' own reads and writes of the repository's files are UTF-8, as the files are, and a test's file holds the
# bytes it wrote, as on a Mac; on Windows a bare read_text()/write_text() would use cp1252 and turn "\n" into "\r\n".
# Only calls made from a test file get this. Studio's own code never does: it must say encoding="utf-8" itself
# (tests/test_windows_portability.py), and Python's EncodingWarning still names its line.
def _test_read_text(self, encoding=None, errors=None, newline=None):
    if encoding is None:
        encoding = "utf-8" if _from_a_test() else io.text_encoding(None, 2)
    return _read_text(self, encoding, errors, newline)


def _test_write_text(self, data, encoding=None, errors=None, newline=None):
    if encoding is None or newline is None:
        test = _from_a_test()
        if encoding is None:
            encoding = "utf-8" if test else io.text_encoding(None, 2)
        if newline is None and test:
            newline = "\n"
    return _write_text(self, data, encoding, errors, newline)


pathlib.Path.read_text, pathlib.Path.write_text = _test_read_text, _test_write_text


@pytest.fixture(autouse=True)
def _a_computer_with_room_for_a_style_study(monkeypatch):
    """Style studies check this computer's free disk and memory before starting (cr_style_clone.room_to_check); the
    tests stand in a computer with room, so they never depend on the machine running them."""
    clone = sys.modules.get("cr_style_clone")
    if clone is not None and hasattr(clone, "_room"):
        monkeypatch.setattr(clone, "_room", lambda: "")


def pytest_configure(config):
    config.addinivalue_line("markers", "real_setup_checks: run setup's network and speech-model checks for real "
                                       "(their own tests), instead of the stand-ins every other test gets")
    if sys.flags.warn_default_encoding:
        # Only Studio's own modules (cr_*) matter here; the tests' and libraries' own warnings would bury them.
        warnings.filterwarnings("ignore", category=EncodingWarning)
        warnings.filterwarnings("default", category=EncodingWarning, module=r"(local_runtime\.)?cr_")


@pytest.fixture(autouse=True)
def _a_reachable_network_and_a_ready_speech_model(request, monkeypatch):
    """Setup checks the network (TLS handshakes to the hosts Studio uses) and the speech-timing model (downloaded and
    loaded once). Other tests stand in a computer where both pass, so they never touch the network or depend on this
    machine's model cache."""
    if request.node.get_closest_marker("real_setup_checks"):
        return
    network, whisper = sys.modules.get("cr_network"), sys.modules.get("cr_whisper")
    if network is not None:
        monkeypatch.setattr(network, "status", lambda *a, **k: {
            "ready": True, "message": "This computer reaches every service Studio uses.", "hosts": [], "blocked": [],
            "warnings": [], "fallbacks": [], "unreachable": []})
        monkeypatch.setattr(network, "start", lambda *a, **k: None)
    if whisper is not None:
        monkeypatch.setattr(whisper, "status", lambda: {"ready": True, "state": "ready", "reason": ""})
        monkeypatch.setattr(whisper, "start", lambda: None)
        monkeypatch.setattr(whisper, "local_path", lambda *a, **k: "")


def pytest_warning_recorded(warning_message, when, nodeid, location):
    """With PYTHONWARNDEFAULTENCODING=1 (the Windows CI sets it), Python warns wherever a text read, write or
    subprocess leaves the encoding to the computer, which is cp1252 on a creator's Windows PC. Studio's own code
    (local_runtime) must always say utf-8, so any such warning from it fails the run."""
    if not issubclass(warning_message.category, EncodingWarning):
        return
    path = Path(warning_message.filename)
    if path.is_absolute() and path.resolve().is_relative_to(RUNTIME):
        _DEFAULT_ENCODING.add(f"{path.name}:{warning_message.lineno} (seen in {nodeid or 'collection'})")


def pytest_terminal_summary(terminalreporter):
    if _DEFAULT_ENCODING:
        terminalreporter.section("Studio code that leaves the text encoding to Windows (cp1252)", red=True)
        for line in sorted(_DEFAULT_ENCODING):
            terminalreporter.line(line)


def pytest_sessionfinish(session, exitstatus):
    if _DEFAULT_ENCODING and exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
