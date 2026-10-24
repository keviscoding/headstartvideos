"""Setup's Network check: can this computer open a secure connection to every service Studio uses?

Creators' networks have cut hosts Studio depends on, and each one was found only when a video stopped halfway: one
internet provider reset every connection to DigitalOcean Spaces (Studio's downloads), another cut aiquickdraw.com
(Kie's file host) during the secure-connection handshake, so no voice or picture could be downloaded. This check
opens a short TLS handshake to each host (no request is made, so nothing is paid) and says which ones this computer
can't reach, in plain words.

Where Studio already has a way around a blocked host it says so and doesn't block: Spaces downloads come through
channelrecipe.com (cr_update, Studio 92), and Kie's file hosts go through ChannelRecipe's relay (cr_relay, Studio 97
for downloads, 98 for uploads). A host with no way around it blocks setup, with what to do.

The check runs in the background: setup asks for the last result, which a passing check keeps for ten minutes and a
failing one for a minute, so a fix shows quickly.
"""
from __future__ import annotations

import os
import socket
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor

PORT = 443
TIMEOUT = 6.0
PASS_SECONDS, FAIL_SECONDS = 600, 60
VIA_CR = "channelrecipe.com"

# host, what Studio uses it for, the way around it ("" = none), and what needs it (see needed())
HOSTS = (
    ("channelrecipe.com", "your ChannelRecipe account, Studio updates and its relays", "", "always"),
    ("channelrecipe-media.sfo3.digitaloceanspaces.com", "Studio updates and the Shorts pack",
     "Studio downloads them through channelrecipe.com instead.", "always"),
    ("api.kie.ai", "Kie.ai voice, pictures and animation", "", "kie"),
    ("tempfile.aiquickdraw.com", "downloading Kie.ai's finished voice, pictures and 3D models",
     "Studio fetches them through channelrecipe.com instead.", "kie"),
    ("kieai.redpandaai.co", "uploading reference pictures and clips to Kie.ai",
     "Studio uploads them through channelrecipe.com instead.", "kie"),
    ("huggingface.co", "downloading the speech-timing model (once)", "", "speech_model"),
    ("www.youtube.com", "YouTube footage, Ranking Shorts' clips and cloning styles from YouTube", "", "youtube"),
    ("api.elevenlabs.io", "your ElevenLabs voice", "", "elevenlabs"),
)
LABELS = {"channelrecipe.com": "ChannelRecipe", "channelrecipe-media.sfo3.digitaloceanspaces.com": "Studio downloads",
          "api.kie.ai": "Kie.ai", "tempfile.aiquickdraw.com": "Kie.ai's file host",
          "kieai.redpandaai.co": "Kie.ai's upload host", "huggingface.co": "Hugging Face",
          "www.youtube.com": "YouTube", "api.elevenlabs.io": "ElevenLabs"}
RELAYED = ("tempfile.aiquickdraw.com", "kieai.redpandaai.co")    # cr_relay goes around these once marked blocked

REASONS = {
    "dns": "this computer can't look up its address",
    "refused": "the connection is refused",
    "timeout": "the connection times out",
    "cut": "the network cuts the secure connection as it starts",
    "intercepted": "something on this network intercepts secure connections",
    "unreachable": "it can't be reached",
}
WHAT_TO_DO = {
    "dns": "Check this computer is online. If it is, a DNS filter on this network blocks it: allow {host}, or try "
           "another network (a phone hotspot works), then check again.",
    "intercepted": "Antivirus, a VPN or a company firewall that inspects secure connections does this: turn that off "
                   "for {host}, or try another network (a phone hotspot works), then check again.",
}
WHAT_TO_DO_ELSE = ("Allow {host} in your firewall, antivirus or VPN, or try another network (a phone hotspot "
                   "works), then check again.")

_lock = threading.Lock()
_last: dict = {}
_running: dict = {}


def _proxy_for(host: str) -> str:
    """The proxy requests would use for this host (system or environment settings), or ""."""
    try:
        import urllib.request
        if urllib.request.proxy_bypass(host):
            return ""
        return urllib.request.getproxies().get("https", "")
    except Exception:                                          # noqa: BLE001 - no proxy settings readable
        return ""


def _context() -> ssl.SSLContext:
    """The certificates requests checks against (certifi), so a pass here means Studio's own downloads work too."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:                                          # noqa: BLE001
        return ssl.create_default_context()


def classify(error: BaseException) -> str:
    if isinstance(error, socket.gaierror):
        return "dns"
    if isinstance(error, ssl.SSLCertVerificationError):
        return "intercepted"
    if isinstance(error, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(error, ConnectionRefusedError):
        return "refused"
    if isinstance(error, (ssl.SSLError, ConnectionResetError, ConnectionAbortedError, EOFError)):
        return "cut"
    return "unreachable"


def handshake(host: str, timeout: float = TIMEOUT) -> str:
    """"" when this computer completes a secure connection to host, else why not (a REASONS key). Only the
    handshake: no request is sent."""
    proxy = _proxy_for(host)
    if proxy:
        # Through the proxy the computer is set to use, as Studio's downloads go: any answer means it's reachable.
        try:
            import requests
            requests.head(f"https://{host}/", timeout=timeout, allow_redirects=False)
            return ""
        except Exception as error:                             # noqa: BLE001
            text = repr(error)
            return ("dns" if "NameResolution" in text or "getaddrinfo" in text
                    else "intercepted" if "CERTIFICATE_VERIFY_FAILED" in text
                    else "timeout" if "Timeout" in text else "cut" if "SSL" in text else "unreachable")
    try:
        with socket.create_connection((host, PORT), timeout=timeout) as raw:
            with _context().wrap_socket(raw, server_hostname=host) as tls:
                tls.settimeout(timeout)
        return ""
    except Exception as error:                                 # noqa: BLE001
        return classify(error)


def needed(need: str, wants: dict) -> bool:
    return need == "always" or bool(wants.get(need))


def _row(host, purpose, fallback, need, problem, wants, reachable_cr):
    row = {"host": host, "label": LABELS.get(host, host), "for": purpose, "reachable": not problem,
           "reason": REASONS.get(problem, "") if problem else "", "needed": needed(need, wants),
           "fallback": "", "blocking": False, "warning": False, "message": ""}
    if not problem:
        return row
    what = (WHAT_TO_DO.get(problem) or WHAT_TO_DO_ELSE).format(host=host)
    lead = f"{row['label']} ({host}, for {purpose}): {row['reason']}."
    if not row["needed"]:
        row["message"] = lead + (" Not needed right now: the speech-timing model is already on this computer."
                                 if need == "speech_model" else " You don't use it at the moment.")
    elif fallback and reachable_cr:
        row["fallback"] = fallback
        row["message"] = f"{lead} {fallback}"
    elif need == "youtube":
        # only some videos use YouTube: those are stopped before they start (cr_readiness.preset), the rest go ahead
        row["warning"] = True
        row["message"] = (f"{lead} Recipes with YouTube footage, Ranking Shorts and cloning a style from a YouTube "
                          f"link won't work on this network. {what}")
    else:
        row["blocking"] = True
        around = " Its way around goes through channelrecipe.com, which can't be reached either." if fallback else ""
        row["message"] = f"{lead}{around} {what}"
    return row


def summarise(rows: list) -> dict:
    blocking = [r for r in rows if r["blocking"]]
    warnings = [r for r in rows if r["warning"]]
    around = [r for r in rows if r["fallback"]]
    if blocking:
        hosts = [r["host"] for r in blocking]
        names = hosts[0] if len(hosts) == 1 else ", ".join(hosts[:-1]) + " and " + hosts[-1]
        message = (f"This computer can't reach {names}, which Studio needs and can't go around. "
                   + " ".join(r["message"] for r in blocking))
    elif warnings or around:
        message = " ".join(r["message"] for r in warnings + around)
    else:
        message = "This computer reaches every service Studio uses."
    return {"ready": not blocking, "message": message, "hosts": rows,
            "blocked": [r["host"] for r in blocking], "warnings": [r["host"] for r in warnings],
            "fallbacks": [r["host"] for r in around],
            "unreachable": [r["host"] for r in rows if not r["reachable"]]}


def _hosts(wants: dict) -> list:
    return [h for h in HOSTS if h[3] != "elevenlabs" or wants.get("elevenlabs")]


def probe_all(hosts, *, probe=handshake, retry: bool = True) -> dict:
    """{host: problem ("" = reachable)} for every host at once (a few seconds at most). A failure is tried once more
    before it counts, so one dropped packet doesn't block setup."""
    names = [h if isinstance(h, str) else h[0] for h in hosts]
    with ThreadPoolExecutor(max_workers=max(1, len(names))) as pool:
        problems = dict(zip(names, pool.map(probe, names)))
        again = [n for n in names if problems[n]] if retry else []
        problems.update(zip(again, pool.map(probe, again)))
    return problems


def classify_all(problems: dict, wants: dict) -> dict:
    hosts = [h for h in _hosts(wants) if h[0] in problems]
    reachable_cr = not problems.get(VIA_CR)
    return summarise([_row(host, purpose, fallback, need, problems[host], wants, reachable_cr)
                      for host, purpose, fallback, need in hosts])


def check(wants: dict | None = None, *, probe=handshake, retry: bool = True) -> dict:
    wants = dict(wants or {})
    problems = probe_all(_hosts(wants), probe=probe, retry=retry)
    result = classify_all(problems, wants)
    result["checked_at"] = time.time()
    _note_blocked(problems)
    return result


def _note_blocked(problems: dict) -> None:
    """Kie's file hosts found cut here go through the relay from the very first file of the next video."""
    try:
        import cr_relay
        for host in RELAYED:
            if problems.get(host):
                cr_relay.mark_blocked(host)
    except Exception:                                          # noqa: BLE001 - the relay still finds it by itself
        pass


def _fresh(last: dict, wants: dict) -> bool:
    problems = last.get("problems") or {}
    if not problems or any(h[0] not in problems for h in _hosts(wants)):
        return False
    age = time.time() - last.get("checked_at", 0)
    return age < (FAIL_SECONDS if any(problems.values()) else PASS_SECONDS)


def _answer(last: dict, wants: dict, **extra) -> dict:
    return {**classify_all(last["problems"], wants), "checked_at": last.get("checked_at"), **extra}


def status(wants: dict | None = None, *, wait: float = TIMEOUT * 2 + 2, probe=handshake) -> dict:
    """The latest check, read for these needs. A stale one is started again in the background; this waits up to
    `wait` seconds for it and otherwise answers with the older result (or "checking")."""
    wants = dict(wants or {})
    with _lock:
        last = {k: v for k, v in _last.items()}
        if _fresh(last, wants):
            return _answer(last, wants)
        worker = _running.get("thread")
        if worker is None or not worker.is_alive():
            hosts = [h[0] for h in HOSTS if h[3] != "elevenlabs" or wants.get("elevenlabs")]

            def run():
                try:
                    problems = probe_all(hosts, probe=probe)
                except Exception:                              # noqa: BLE001 - never break setup over the check
                    problems = {}
                _note_blocked(problems)
                with _lock:
                    _last.clear()
                    _last.update(problems=problems, checked_at=time.time())
            worker = threading.Thread(target=run, daemon=True, name="cr-network-check")
            _running["thread"] = worker
            worker.start()
    if wait > 0:
        worker.join(wait)
    with _lock:
        now = {k: v for k, v in _last.items()}
    if now.get("problems") and (_fresh(now, wants) or not worker.is_alive()):
        return _answer(now, wants)
    if last.get("problems"):
        return _answer(last, wants, checking=True)
    return {"ready": True, "checking": True, "message": "Checking which services this computer can reach…",
            "hosts": [], "blocked": [], "warnings": [], "fallbacks": [], "unreachable": []}


def forget() -> None:
    """The next status() checks again."""
    with _lock:
        _last.clear()


def start(wants: dict | None = None) -> None:
    """At Studio start: check in the background so setup has the answer when it opens."""
    status(wants, wait=0)


def youtube_problem(result: dict) -> str:
    """Why a recipe that uses YouTube can't start on this network ("" when YouTube is reachable)."""
    row = next((r for r in result.get("hosts") or [] if r["host"] == "www.youtube.com"), None)
    if not row or row["reachable"]:
        return ""
    what = (WHAT_TO_DO.get(_reason_key(row["reason"])) or WHAT_TO_DO_ELSE).format(host=row["host"])
    return (f"This recipe uses YouTube, and this computer can't reach www.youtube.com ({row['reason']}). {what} "
            "Or turn YouTube footage off for this video.")


def _reason_key(text: str) -> str:
    return next((k for k, v in REASONS.items() if v == text), "unreachable")


def diagnostics_line(result: dict) -> str:
    if result.get("checking") and not result.get("hosts"):
        return "Network: still checking"
    rows = result.get("hosts") or []
    bad = [r for r in rows if not r["reachable"]]
    if not bad:
        return "Network: every service reachable"
    def tag(r):
        return ("blocks setup" if r["blocking"] else "goes around through channelrecipe.com" if r["fallback"]
                else "some recipes" if r["warning"] else "not needed now")
    return "Network: can't reach " + "; ".join(f"{r['host']} ({r['reason']}, {tag(r)})" for r in bad)


def wants_from_env(*, speech_model_missing: bool, hub_provider: str = "") -> dict:
    """What this Studio needs, from its saved keys (never read into anything but booleans)."""
    kie = bool(os.getenv("KIE_API_KEY", "").strip()) or hub_provider in ("", "kie")
    return {"kie": kie, "speech_model": speech_model_missing, "youtube": True,
            "elevenlabs": bool(os.getenv("ELEVENLABS_API_KEY", "").strip())}
