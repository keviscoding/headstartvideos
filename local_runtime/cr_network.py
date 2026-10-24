"""Setup's Network check: can this computer open a secure connection to every service Studio uses?

Creators' networks have cut hosts Studio depends on, and each one was found only when a video stopped halfway: one
internet provider reset every connection to DigitalOcean Spaces (Studio's downloads), another cut aiquickdraw.com
(Kie's file host) during the secure-connection handshake, so no voice or picture could be downloaded. This check
opens a short TLS handshake to each host (no request is made, so nothing is paid) and says which ones this computer
can't reach, in plain words.

Where Studio already has a way around a blocked host it says so and doesn't block: Spaces downloads come through
channelrecipe.com (cr_update, Studio 92), and Kie's file hosts go through ChannelRecipe's relay (cr_relay, Studio 97
for downloads, 98 for uploads). A host with no way around it blocks setup, with what to do.

An antivirus or firewall on the computer can block Studio itself while the browser works (a Windows creator's Norton,
2026-10-24). That looks different: Studio can't connect to itself on 127.0.0.1, or every host fails, or every secure
connection is inspected. Setup then says so, names the antivirus it finds running, and says how to allow Studio in it
(never to switch it off).

The check runs in the background: setup asks for the last result, which a passing check keeps for ten minutes and a
failing one for a minute, so a fix shows quickly.
"""
from __future__ import annotations

import os
import socket
import ssl
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

PORT = 443
TIMEOUT = 6.0
PASS_SECONDS, FAIL_SECONDS = 600, 60
VIA_CR = "channelrecipe.com"
STUDIO_PORT = 7860

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
    "intercepted": "something on this computer or network inspects its secure connections",
    "unreachable": "it can't be reached",
}
WHAT_TO_DO = {
    "dns": "Check this computer is online. If it is, a DNS filter on this network blocks it: allow {host}, or try "
           "another network (a phone hotspot works), then check again.",
    "intercepted": "An antivirus, VPN or company firewall that inspects secure connections does this: allow Studio "
                   "in it, or try another network (a phone hotspot works), then check again.",
}
WHAT_TO_DO_ELSE = ("Allow {host} in your firewall, antivirus or VPN, or try another network (a phone hotspot "
                   "works), then check again.")

# Antivirus and firewall programs, by their running process: (product, exact process names, distinctive name parts).
ANTIVIRUS = (
    ("Norton", ("nortonsecurity.exe", "nortonui.exe", "nortonsvc.exe"), ("norton", "symantec")),
    ("Kaspersky", ("avp.exe", "avpui.exe", "kav"), ("kaspersky",)),
    ("Avast", ("avastsvc.exe", "avastui.exe"), ("com.avast",)),
    ("AVG", ("avgsvc.exe", "avgui.exe"), ()),
    ("Bitdefender", ("bdagent.exe", "bdservicehost.exe", "vsserv.exe", "bdldaemon"), ("bitdefender",)),
    ("McAfee", ("mcshield.exe", "mfemms.exe", "mcuicnt.exe"), ("mcafee",)),
    ("ESET", ("ekrn.exe", "egui.exe"), ("esets_",)),
    ("Malwarebytes", ("mbamservice.exe", "mbam.exe"), ("malwarebytes",)),
    ("Sophos", ("savservice.exe", "sophoshealth.exe"), ("sophos",)),
    ("Webroot", ("wrsa.exe",), ()),
    ("ZoneAlarm", ("zatray.exe", "vsmon.exe"), ("zonealarm",)),
    ("Little Snitch", (), ("little snitch",)),
    ("LuLu", ("lulu",), ()),
)
AV_SECONDS = 600
LOCAL_KINDS = ("local", "inspects", "everything")       # a block on this computer, not the network

_lock = threading.Lock()
_last: dict = {}
_running: dict = {}
_antivirus: dict = {}


def running_antivirus(*, run=None) -> list:
    """The antivirus and firewall products running on this computer, from the process list (a fraction of a second),
    kept for ten minutes."""
    if _antivirus.get("until", 0) > time.monotonic():
        return list(_antivirus["found"])
    run = run or subprocess.run
    try:
        if os.name == "nt":
            # tasklist writes in the console's OEM code page; read as cp1252, a byte like 0x90 (É) failed the list
            out = run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, encoding="oem",
                      errors="replace", timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            names = {line.split(",")[0].strip().strip('"').lower() for line in out.splitlines() if line.strip()}
        else:
            out = run(["ps", "-axo", "comm="], capture_output=True, text=True, encoding="utf-8", errors="replace",
                      timeout=15).stdout
            names = {os.path.basename(line.strip()).lower() for line in out.splitlines() if line.strip()}
    except Exception:                                          # noqa: BLE001 - no process list: say "your antivirus"
        names = set()
    found = [product for product, exact, parts in ANTIVIRUS
             if any(n in names for n in exact) or any(part in n for n in names for part in parts)]
    _antivirus.update(found=found, until=time.monotonic() + AV_SECONDS)
    return found


def studio_programs() -> list:
    """The Python files an antivirus or firewall has to allow for Studio: the venv's, and the interpreter it runs."""
    paths = [sys.executable]
    other = getattr(sys, "_base_executable", "") if os.name == "nt" else os.path.realpath(sys.executable)
    if other and other not in paths:
        paths.append(other)
    return paths


def local_probe(port: int = STUDIO_PORT, timeout: float = 3.0) -> str:
    """"" when Studio can connect to itself on this computer, else why not. Only an antivirus or firewall on the
    computer stops that."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return ""
    except Exception as error:                                 # noqa: BLE001
        return classify(error)


def local_block(problems: dict, local: str = "", antivirus=()) -> dict:
    """Whether a block is on this computer (an antivirus or firewall stopping Studio itself) rather than the network,
    with what to do about it."""
    failed = {h: p for h, p in problems.items() if p}
    kind = ("local" if local else "inspects" if "intercepted" in failed.values()
            else "everything" if problems and len(failed) == len(problems) and set(failed.values()) != {"dns"}
            else "offline" if problems and len(failed) == len(problems) else "")
    if not kind:
        return {"suspected": False, "kind": "", "antivirus": "", "message": "", "programs": []}
    if kind == "offline":
        return {"suspected": False, "kind": kind, "antivirus": "", "programs": [],
                "message": "This computer can't look up any of Studio's services. Check it's online (open a website "
                           "in your browser), then check again."}
    name = " and ".join(antivirus) if antivirus else ""
    who = name or "An antivirus or firewall on this computer"
    lead = {"local": f"{who} is blocking Studio's own connections: Studio can't even connect to itself on this "
                     f"computer (127.0.0.1:{STUDIO_PORT}).",
            "inspects": f"{who} inspects this computer's secure connections, and Studio's fail because of it (your "
                        "browser trusts the inspection, Studio doesn't).",
            "everything": f"Studio can't reach any of its services. If websites open in your browser, {name or 'an antivirus or firewall'} "
                          "is blocking Studio itself, not the network."}[kind]
    programs = studio_programs()
    where = " (in Norton: Settings, Firewall, Program Control)" if "Norton" in antivirus else ""
    fix = (f"Allow Studio in {name or 'your antivirus or firewall'}: add Studio's Python ({' and '.join(programs)}) to "
           f"its allowed programs{where}, and allow connections to 127.0.0.1 port {STUDIO_PORT} (Studio on this "
           "computer). Then check again.")
    return {"suspected": True, "kind": kind, "antivirus": name, "programs": programs, "message": f"{lead} {fix}"}


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


def _row(host, purpose, fallback, need, problem, wants, reachable_cr, on_computer=False):
    row = {"host": host, "label": LABELS.get(host, host), "for": purpose, "reachable": not problem,
           "reason": REASONS.get(problem, "") if problem else "", "needed": needed(need, wants),
           "fallback": "", "blocking": False, "warning": False, "message": ""}
    if not problem:
        return row
    # when the block is on this computer, the network row says once how to allow Studio (local_block)
    what = "" if on_computer else (WHAT_TO_DO.get(problem) or WHAT_TO_DO_ELSE).format(host=host)
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
                          f"link won't work on this network. {what}").rstrip()
    else:
        row["blocking"] = True
        around = " Its way around goes through channelrecipe.com, which can't be reached either." if fallback else ""
        row["message"] = f"{lead}{around} {what}".rstrip()
    return row


def summarise(rows: list, local: dict | None = None) -> dict:
    blocking = [r for r in rows if r["blocking"]]
    warnings = [r for r in rows if r["warning"]]
    around = [r for r in rows if r["fallback"]]
    local = local or {"suspected": False, "kind": "", "antivirus": "", "message": "", "programs": []}
    if local["suspected"] or local["kind"] == "offline":
        message = local["message"] + "".join(" " + r["message"] for r in blocking)
    elif blocking:
        hosts = [r["host"] for r in blocking]
        names = hosts[0] if len(hosts) == 1 else ", ".join(hosts[:-1]) + " and " + hosts[-1]
        message = (f"This computer can't reach {names}, which Studio needs and can't go around. "
                   + " ".join(r["message"] for r in blocking))
    elif warnings or around:
        message = " ".join(r["message"] for r in warnings + around)
    else:
        message = "This computer reaches every service Studio uses."
    return {"ready": not blocking and not (local["suspected"] and local["kind"] == "local"), "message": message,
            "hosts": rows, "local_block": local,
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


def classify_all(problems: dict, wants: dict, *, local: str = "", antivirus=()) -> dict:
    hosts = [h for h in _hosts(wants) if h[0] in problems]
    reachable_cr = not problems.get(VIA_CR)
    block = local_block({h[0]: problems[h[0]] for h in hosts}, local, antivirus)
    return summarise([_row(host, purpose, fallback, need, problems[host], wants, reachable_cr,
                           on_computer=block["kind"] in LOCAL_KINDS)
                      for host, purpose, fallback, need in hosts], block)


def scan(hosts, *, port=None, probe=handshake, probe_local=local_probe, find_antivirus=running_antivirus) -> dict:
    """Every host, Studio's own address when it's serving (port), and the antivirus running when the answers look like
    a block on this computer."""
    problems = probe_all(hosts, probe=probe)
    local = probe_local(port) if port else ""
    suspected = local_block(problems, local)["kind"] in LOCAL_KINDS
    return {"problems": problems, "local": local, "port": port,
            "antivirus": find_antivirus() if suspected else [], "checked_at": time.time()}


def check(wants: dict | None = None, *, probe=handshake, retry: bool = True, local: str = "", antivirus=()) -> dict:
    wants = dict(wants or {})
    problems = probe_all(_hosts(wants), probe=probe, retry=retry)
    result = classify_all(problems, wants, local=local, antivirus=antivirus)
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


def _fresh(last: dict, wants: dict, port=None) -> bool:
    problems = last.get("problems") or {}
    if not problems or any(h[0] not in problems for h in _hosts(wants)) or (port and last.get("port") != port):
        return False
    age = time.time() - last.get("checked_at", 0)
    return age < (FAIL_SECONDS if any(problems.values()) or last.get("local") else PASS_SECONDS)


def _answer(last: dict, wants: dict, **extra) -> dict:
    return {**classify_all(last["problems"], wants, local=last.get("local") or "", antivirus=last.get("antivirus") or ()),
            "checked_at": last.get("checked_at"), **extra}


def status(wants: dict | None = None, *, wait: float = TIMEOUT * 2 + 2, probe=handshake, port=None,
           probe_local=local_probe, find_antivirus=running_antivirus) -> dict:
    """The latest check, read for these needs. A stale one is started again in the background; this waits up to
    `wait` seconds for it and otherwise answers with the older result (or "checking")."""
    wants = dict(wants or {})
    with _lock:
        last = {k: v for k, v in _last.items()}
        if _fresh(last, wants, port):
            return _answer(last, wants)
        worker = _running.get("thread")
        if worker is None or not worker.is_alive():
            hosts = [h[0] for h in HOSTS if h[3] != "elevenlabs" or wants.get("elevenlabs")]

            def run():
                try:
                    found = scan(hosts, port=port, probe=probe, probe_local=probe_local, find_antivirus=find_antivirus)
                except Exception:                              # noqa: BLE001 - never break setup over the check
                    found = {"problems": {}, "checked_at": time.time()}
                _note_blocked(found["problems"])
                with _lock:
                    _last.clear()
                    _last.update(found)
            worker = threading.Thread(target=run, daemon=True, name="cr-network-check")
            _running["thread"] = worker
            worker.start()
    if wait > 0:
        worker.join(wait)
    with _lock:
        now = {k: v for k, v in _last.items()}
    if now.get("problems") and (_fresh(now, wants, port) or not worker.is_alive()):
        return _answer(now, wants)
    if last.get("problems"):
        return _answer(last, wants, checking=True)
    return {"ready": True, "checking": True, "message": "Checking which services this computer can reach…",
            "hosts": [], "blocked": [], "warnings": [], "fallbacks": [], "unreachable": [],
            "local_block": {"suspected": False, "kind": "", "antivirus": "", "message": "", "programs": []}}


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
    local = result.get("local_block") or {}
    on_computer = (f" · blocked on this computer ({local['kind']}); antivirus: {local.get('antivirus') or 'none found'}"
                   if local.get("suspected") else "")
    if not bad:
        return "Network: every service reachable" + on_computer
    def tag(r):
        return ("blocks setup" if r["blocking"] else "goes around through channelrecipe.com" if r["fallback"]
                else "some recipes" if r["warning"] else "not needed now")
    return "Network: can't reach " + "; ".join(f"{r['host']} ({r['reason']}, {tag(r)})" for r in bad) + on_computer


def wants_from_env(*, speech_model_missing: bool, hub_provider: str = "") -> dict:
    """What this Studio needs, from its saved keys (never read into anything but booleans)."""
    kie = bool(os.getenv("KIE_API_KEY", "").strip()) or hub_provider in ("", "kie")
    return {"kie": kie, "speech_model": speech_model_missing, "youtube": True,
            "elevenlabs": bool(os.getenv("ELEVENLABS_API_KEY", "").strip())}
