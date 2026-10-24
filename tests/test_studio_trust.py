"""Studio's Python trusts this computer's own certificates, the way the browser does (cr_trust).

An antivirus or company firewall that inspects secure connections (Avast, AVG, Kaspersky, Bitdefender and ESET web
shields, corporate proxies) re-signs every site with its own root certificate and adds that root to the computer's
store. The browser trusts it; Studio's Python trusted only certifi's list, so every download and upload failed while
websites worked (2026-10-24). Studio now checks certificates with the computer's store in every process it starts,
and setup's Network check uses the same certificates."""
import importlib.machinery
import os
import ssl
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "local_runtime"
sys.path.insert(0, str(RUNTIME))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cr_network  # noqa: E402
import cr_relay  # noqa: E402
import cr_trust  # noqa: E402
import cr_whisper  # noqa: E402
import studio_trust_lab  # noqa: E402

pytestmark = pytest.mark.real_setup_checks
EVERYTHING = {"kie": True, "speech_model": True, "youtube": True, "elevenlabs": False}


def fake_truststore(calls):
    module = types.ModuleType("truststore")
    module.__spec__ = importlib.machinery.ModuleSpec("truststore", None)
    module.inject_into_ssl = lambda: calls.append("inject")
    module.SSLContext = ssl.SSLContext
    return module


@pytest.fixture
def fresh(monkeypatch):
    """A process that hasn't tried yet."""
    monkeypatch.setattr(cr_trust, "_state", {})
    return monkeypatch


@pytest.fixture
def lab(tmp_path):
    folder = studio_trust_lab.make(tmp_path / "web-shield")
    server, port = studio_trust_lab.serve(folder)
    yield types.SimpleNamespace(folder=folder, port=port, url=f"https://localhost:{port}/", ca=str(folder / "ca.pem"))
    server.shutdown()


# ── once per process, and nothing changes without it ───────────────────────────────────────────────────────────────

def test_the_computers_store_is_installed_once_per_process(fresh):
    calls = []
    fresh.setitem(sys.modules, "truststore", fake_truststore(calls))
    assert cr_trust.install() is True and cr_trust.install() is True and cr_trust.install() is True
    assert calls == ["inject"], "ssl is patched once, however many parts of Studio ask"
    assert cr_trust.active() and cr_trust.describe() == "this computer's and certifi's"


def test_without_truststore_everything_stays_on_certifi_and_nothing_breaks(fresh):
    try:                                                       # this test process trusts the computer already
        from truststore._ssl_constants import _original_SSLContext
        fresh.setattr(ssl, "SSLContext", _original_SSLContext)
    except ImportError:
        pass
    fresh.setitem(sys.modules, "truststore", None)             # an install whose libraries aren't updated yet
    assert cr_trust.available() is False and cr_trust.install() is False and not cr_trust.active()
    context = cr_trust.context()
    assert type(context) is ssl.SSLContext and context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert context.cert_store_stats()["x509_ca"] > 100, "certifi's list, as before"
    assert cr_trust.describe() == "certifi's only"
    own = str(Path(sys.executable).with_name("yt-dlp"))
    assert cr_trust.ytdlp_command([own, "-q"]) == [own, "-q"], "yt-dlp runs as before"
    cr_relay.install()                                         # Studio, workers and the Shorts still start


def test_every_process_studio_starts_trusts_the_computer_first(monkeypatch):
    """cr_relay.install runs in Studio and its workers (through cr_media) and in every Shorts pipeline (shortsenv,
    the ranking pipeline's common): it trusts the computer's certificates before anything goes online."""
    asked = []
    monkeypatch.setattr(cr_trust, "install", lambda: asked.append("trust") or True)
    cr_relay.install()
    assert asked == ["trust"]
    for name, start in (("cr_studio.py", "def main():"), ("cr_worker.py", "def main():"),
                        ("cr_style_clone.py", "def main(argv=None):"), ("cr_style_look.py", "def main(argv=None):")):
        source = (RUNTIME / name).read_text(encoding="utf-8")
        body = source[source.index(start):]
        body = body[:min(i for i in (body.find("\ndef ", 1), body.find("\nif __name__", 1), len(body)) if i > 0)]
        assert "cr_trust.install()" in body, f"{name} trusts the computer's certificates when it starts"
    download = cr_whisper.DOWNLOAD
    assert download.index("truststore.inject_into_ssl()") < download.index("from huggingface_hub"), \
        "the speech model downloads in a child of its own (-I): it trusts the computer there too"
    shorts = ROOT / "data/frontier/shorts"
    for hook in (shorts / "hoodie/lib/shortsenv.py", shorts / "ranking/common.py"):
        if hook.exists():
            assert "cr_relay.install()" in hook.read_text(encoding="utf-8"), hook


# ── Studio's yt-dlp is a process of its own ─────────────────────────────────────────────────────────────────────────

def test_studios_own_ytdlp_runs_through_the_wrapper_and_the_creators_choice_is_left_alone(monkeypatch, tmp_path):
    venv = tmp_path / ".venv" / "bin"
    venv.mkdir(parents=True)
    python = str(venv / "python")
    monkeypatch.setattr(cr_trust.sys, "executable", python)
    monkeypatch.setattr(cr_trust, "available", lambda: True)
    wrapper = str(cr_trust.YTDLP)
    for name in ("yt-dlp", "yt-dlp.exe"):
        assert cr_trust.ytdlp_command([str(venv / name), "--flat-playlist"]) == [python, wrapper, "--flat-playlist"]
    assert cr_trust.ytdlp_command([python, "-m", "yt_dlp", "-J", "x"]) == [python, wrapper, "-J", "x"]
    assert cr_trust.ytdlp_command(["/opt/homebrew/bin/yt-dlp", "-q"]) == ["/opt/homebrew/bin/yt-dlp", "-q"]
    assert cr_trust.ytdlp_command([]) == []
    monkeypatch.setattr(cr_trust, "available", lambda: False)
    assert cr_trust.ytdlp_command([str(venv / "yt-dlp")]) == [str(venv / "yt-dlp")]


def test_the_wrapper_trusts_the_computer_before_ytdlp_loads_and_hides_the_engines_files(monkeypatch):
    import cr_ytdlp
    order = []
    monkeypatch.setattr(cr_trust, "install", lambda: order.append("trust") or True)
    fake = types.ModuleType("yt_dlp")
    fake.main = lambda: order.append(("yt-dlp", list(sys.path))) or 0
    monkeypatch.setitem(sys.modules, "yt_dlp", fake)
    monkeypatch.setattr(sys, "path", [cr_ytdlp.HERE, "/elsewhere"])
    assert cr_ytdlp.main() == 0
    assert order == ["trust", ("yt-dlp", ["/elsewhere"])]


def test_the_wrapper_runs_the_real_ytdlp():
    pytest.importorskip("yt_dlp")
    done = subprocess.run([sys.executable, str(RUNTIME / "cr_ytdlp.py"), "--version"], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=120)
    assert done.returncode == 0 and done.stdout.strip()[:4].isdigit(), done.stderr[-400:]


def test_style_studies_use_the_wrapper_too(monkeypatch):
    pytest.importorskip("yt_dlp")
    import cr_style_clone
    monkeypatch.setattr(cr_trust, "available", lambda: True)
    assert cr_style_clone._ytdlp_command()[:2] == [sys.executable, str(cr_trust.YTDLP)]


# ── setup's Network check uses the same certificates ───────────────────────────────────────────────────────────────

def test_the_network_check_handshakes_with_the_computers_certificates(fresh):
    truststore = pytest.importorskip("truststore")
    assert isinstance(cr_network._context(), truststore.SSLContext)


def test_an_inspection_the_computer_trusts_passes_and_one_it_doesnt_is_still_intercepted(monkeypatch, lab):
    truststore = pytest.importorskip("truststore")
    monkeypatch.setattr(cr_network, "PORT", lab.port)
    monkeypatch.setattr(cr_network, "_proxy_for", lambda host: "")
    monkeypatch.setattr(cr_trust, "_state", {})
    # the stand-in root is in no store: certifi doesn't trust it, nor does this computer
    assert cr_network.handshake("localhost", timeout=5) == "intercepted"

    def computer_trusting_the_root():                         # what the antivirus does: its root joins the store
        context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cafile=lab.ca)
        return context
    monkeypatch.setattr(cr_trust, "context", computer_trusting_the_root)
    assert cr_network.handshake("localhost", timeout=5) == ""


def test_through_a_proxy_truststores_refusal_still_reads_as_intercepted(monkeypatch):
    import requests
    monkeypatch.setattr(cr_network, "_proxy_for", lambda host: "http://proxy.example:8080")

    def head(*args, **kwargs):                                 # truststore words it the operating system's way
        raise requests.exceptions.SSLError("Max retries exceeded (Caused by SSLError(SSLCertVerificationError("
                                           "'“Web Shield Root” certificate is not trusted')))")
    monkeypatch.setattr(requests, "head", head)
    assert cr_network.handshake("api.kie.ai") == "intercepted"


def test_what_setup_says_about_an_inspection_matches_which_certificates_studio_trusts(monkeypatch):
    problems = {"channelrecipe.com": "intercepted", "api.kie.ai": "intercepted"}
    monkeypatch.setattr(cr_trust, "_state", {"on": True})
    trusting = cr_network.check(EVERYTHING, probe=lambda h: problems.get(h, ""), antivirus=["Avast"])
    assert trusting["message"].startswith("Avast inspects this computer's secure connections with a certificate this "
                                          "computer doesn't trust")
    assert "Allow Studio in Avast" in trusting["message"] and "switch" not in trusting["message"].lower()
    assert "certificates: this computer's and certifi's" in cr_network.diagnostics_line(trusting)
    monkeypatch.setattr(cr_trust, "_state", {"on": False})
    older = cr_network.check(EVERYTHING, probe=lambda h: problems.get(h, ""), antivirus=["Avast"])
    assert "(your browser trusts the inspection, Studio doesn't)" in older["message"]
    assert "certificates: certifi's only" in cr_network.diagnostics_line(older)


def test_the_updater_downloads_with_the_computers_certificates(fresh):
    truststore = pytest.importorskip("truststore")
    import cr_update
    context = cr_update.tls_context()
    assert isinstance(context, truststore.SSLContext) and context.verify_mode == ssl.CERT_REQUIRED


# ── on a computer whose store holds the inspecting root (the Studio trust job adds it, as an antivirus does) ───────

OS_TRUSTED = os.environ.get("CR_TEST_OS_TRUSTED_CA", "")


@pytest.mark.skipif(not OS_TRUSTED, reason="needs a stand-in antivirus root in this computer's own store (CI job)")
def test_behind_an_inspecting_antivirus_studio_works_where_certifi_alone_failed(monkeypatch):
    folder = Path(OS_TRUSTED)
    server, port = studio_trust_lab.serve(folder)
    url = f"https://localhost:{port}/"
    child = {"cwd": str(RUNTIME), "capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace",
             "timeout": 120}
    try:
        get = ("import sys; sys.path.insert(0, '.'); {trust}import requests; "
               f"print(requests.get({url!r}, timeout=20).text)")
        before = subprocess.run([sys.executable, "-I", "-c", get.format(trust="")], **child)
        assert before.returncode != 0 and "SSL" in before.stderr, "certifi alone refuses the inspected connection"
        after = subprocess.run([sys.executable, "-I", "-c", get.format(trust="import cr_trust; cr_trust.install(); ")],
                               **child)
        assert after.returncode == 0 and "inspected hello" in after.stdout, after.stderr[-600:]
        # setup's Network check passes, through the same certificates
        monkeypatch.setattr(cr_network, "PORT", port)
        monkeypatch.setattr(cr_network, "_proxy_for", lambda host: "")
        monkeypatch.setattr(cr_trust, "_state", {})
        assert cr_network.handshake("localhost", timeout=10) == ""
        # and so does Studio's yt-dlp (the page isn't a video: yt-dlp says so only after the secure connection)
        if subprocess.run([sys.executable, "-c", "import yt_dlp"], capture_output=True).returncode == 0:
            ran = subprocess.run([sys.executable, str(RUNTIME / "cr_ytdlp.py"), "--no-warnings", "--skip-download",
                                  "--print", "title", url], **child)
            assert "certificate" not in ran.stderr.lower() and "Unsupported URL" in ran.stderr, ran.stderr[-600:]
    finally:
        server.shutdown()
