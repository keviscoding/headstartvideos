"""A score written for each video: one music cue per section, generated on the creator's Kie account (Suno).

Studio already splits a finished timeline into music sections (its chapters, by mood, long ones cut at a
sentence) and crossfades one track per section into a bed that ducks under the narration. This module gives
those sections music made for them instead of tracks found on a shelf:

- each cue is asked for at its section's exact length, in the channel's learned genre, moods and instruments
  (look.sound.score, from style learning), with the section's job in the story: a sparse cold open that builds
  into the hook, a steady underscore that leaves room for the voice, a finale that resolves;
- Suno answers with two takes; the one whose measured shape fits the job better is kept (a cold open should
  start quiet and rise, an underscore should stay even), never an AI listener's opinion;
- every submission is recorded before it is polled, and one whose result is uncertain is never sent again;
- cues are cached with the timeline: a re-render of the same cut generates nothing.

Levels stay with Studio's mixer (look.sound.music_rel_db / duck_db, set from the reference's measured bed).
Nothing here can fail a video: without a Kie key, or when a cue fails, the channel's usual music is used.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

BASE = "https://api.kie.ai/api/v1/jobs"
MODEL = "ai-music-api/generate"
SUNO = os.environ.get("CR_SCORE_MODEL", "V6")
PARALLEL = 3
CREDITS_PER_CUE = 12            # measured 2026-10-15: one call (two takes) of 47 s took 12 Kie credits
# Every cue: no singing, nothing that breaks a documentary bed (the music brief's failure log).
AVOID = ["vocals", "lyrics", "choir", "singing", "cheesy", "corporate stock music", "EDM drop", "dubstep",
         "brass fanfare", "comedy", "kazoo"]
ROLES = {
    "open": "cold open: starts sparse and quiet, no drums for the first third, tension builds steadily and rises "
            "into a lift at the end",
    "body": "steady underscore for narration: even energy, evolving texture every 10 to 15 seconds, leaves the "
            "middle frequencies clear for a voice, no big drops",
    "turn": "a turn in the story: begins with a short drop to near silence, then a new texture builds underneath",
    "end": "finale: builds to a resolution and ends on a clean final note with a warm tail",
}
MOOD_WORDS = {"dramatic": "tense, dramatic", "quiet": "quiet, reflective", "inspiring": "hopeful, uplifting",
              "dark": "dark, ominous", "tense": "tense, suspenseful", "curious": "curious, light",
              "sad": "melancholic", "triumphant": "triumphant, warm", "": ""}


def wanted(look: dict) -> dict:
    """The channel's score settings, or {} when it doesn't ask for one (or there's no Kie key)."""
    snd = (look or {}).get("sound") if isinstance(look, dict) else None
    cfg = snd.get("score") if isinstance(snd, dict) else None
    if not isinstance(cfg, dict) or cfg.get("on") is False or snd.get("music") is False:
        return {}
    if not (os.environ.get("KIE_API_KEY") or "").strip():
        return {}
    return cfg


def style_text(cfg: dict, mood: str, role: str) -> str:
    """Suno's style line for one cue: the channel's sound, the section's mood, the cue's job."""
    parts = [", ".join(cfg.get("genres") or ["cinematic documentary underscore"])]
    if cfg.get("instruments"):
        parts.append(", ".join(cfg["instruments"][:5]))
    mood_words = MOOD_WORDS.get(str(mood or "").lower(), str(mood or ""))
    moods = [m for m in [mood_words] + list(cfg.get("moods") or [])[:4] if m]
    if moods:
        parts.append(", ".join(dict.fromkeys(moods)))
    beat = {"none": "no drums, beatless", "soft": "soft laid-back beat", "driving": "steady driving beat"}.get(
        str(cfg.get("beat") or ""))
    if beat:
        parts.append(beat)
    parts.append("instrumental")
    parts.append(ROLES.get(role, ROLES["body"]))
    return "; ".join(parts)[:950]


def avoid_text(cfg: dict) -> str:
    # the brief bans these for documentary beds; a channel whose own sound is lo-fi or acoustic keeps them
    keep = re.sub(r"[^a-z]", "", " ".join((cfg.get("genres") or []) + (cfg.get("instruments") or [])).lower())
    extra = [a for a in ("acoustic guitar", "lo-fi", "folk") if re.sub(r"[^a-z]", "", a) not in keep]
    return ", ".join(AVOID + extra + list(cfg.get("avoid") or []))[:900]


def roles(n: int) -> list:
    if n <= 1:
        return ["open"]
    return ["open"] + ["body"] * (n - 2) + ["end"]


# ── Kie: submit once, record before polling, never resubmit an uncertain cue ───────────────────────────────

def _write(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value), encoding="utf-8")
    tmp.replace(path)


def generate(inputs: dict, dest: Path, *, log=print, timeout_s=900, post=None, get=None, sleep=time.sleep) -> list:
    """Both takes of one cue as [path, path]; cached by the request, receipts in dest/.cr-tasks."""
    post, get = post or requests.post, get or requests.get
    key = (os.environ.get("KIE_API_KEY") or "").strip()
    headers = {"Authorization": "Bearer " + key}
    fp = hashlib.sha256(json.dumps({"wallet": hashlib.sha256(key.encode()).hexdigest(), "input": inputs},
                                   sort_keys=True).encode()).hexdigest()[:24]
    marker = dest.parent / ".cr-tasks" / f"{dest.stem}-{fp}.json"
    takes = [dest.with_name(f"{dest.stem}_take{i + 1}.mp3") for i in range(2)]
    receipt = json.loads(marker.read_text()) if marker.is_file() else {}
    if receipt.get("status") == "complete" and all(t.is_file() for t in takes[:receipt.get("takes", 2)]):
        return takes[:receipt.get("takes", 2)]
    if receipt.get("status") == "failed":
        raise RuntimeError("this cue failed before: " + str(receipt.get("reason") or "")[:120])
    if receipt and not receipt.get("id"):
        raise RuntimeError("an earlier submission of this cue has an uncertain result; check the Kie log before retrying")
    if not receipt:
        receipt = {"status": "submitting", "model": inputs.get("model"), "created_at": time.time()}
        _write(marker, receipt)
        res = post(BASE + "/createTask", headers=headers, json={"model": MODEL, "input": inputs}, timeout=(15, 90))
        data = res.json() if res.content else {}
        task = ((data or {}).get("data") or {}).get("taskId")
        if not task:
            code = str((data or {}).get("code") or res.status_code)
            if code in ("400", "401", "402", "403", "422", "429"):
                receipt.update(status="failed", reason=f"refused ({code}) {str(data.get('msg') or '')[:120]}")
                _write(marker, receipt)
                raise RuntimeError(receipt["reason"])
            raise RuntimeError("Kie returned no task ID; check the Kie log before retrying")
        receipt.update(id=task, status="pending")
        _write(marker, receipt)
    log(f"  score: Suno {inputs.get('model')} cue {dest.stem} · task {receipt['id']}")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        sleep(6)
        try:
            res = get(BASE + "/recordInfo", headers=headers, params={"taskId": receipt["id"]}, timeout=(10, 45))
            detail = (res.json() or {}).get("data") or {}
        except (requests.RequestException, ValueError):
            continue
        state = detail.get("state")
        if state == "success":
            items = (json.loads(detail.get("resultJson") or "{}").get("data") or [])
            urls = [i.get("audio_url") for i in items if isinstance(i, dict) and i.get("audio_url")][:2]
            if not urls:
                receipt.update(status="failed", reason="no audio in the result")
                _write(marker, receipt)
                raise RuntimeError(receipt["reason"])
            for url, path in zip(urls, takes):
                path.write_bytes(requests.get(url, timeout=120).content)
            receipt.update(status="complete", takes=len(urls), credits=detail.get("creditsConsumed"))
            _write(marker, receipt)
            return takes[:len(urls)]
        if state in ("fail", "failed"):
            receipt.update(status="failed", reason=str(detail.get("failMsg") or "")[:160])
            _write(marker, receipt)
            raise RuntimeError(receipt["reason"])
    raise RuntimeError("the cue is still generating on Kie; it will be collected next time")


# ── choosing a take by its measured shape ──────────────────────────────────────────────────────────────────

def envelope(path: Path, step_s: float = 1.0) -> list:
    """Loudness in dB per second of a file."""
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                       capture_output=True, timeout=120)
    import numpy as np
    x = np.frombuffer(p.stdout[: len(p.stdout) // 2 * 2], np.int16).astype(np.float32) / 32768.0
    hop = int(8000 * step_s)
    n = len(x) // hop
    if n < 2:
        return []
    rms = np.sqrt((x[: n * hop].reshape(n, hop) ** 2).mean(1)) + 1e-9
    return [float(v) for v in 20 * np.log10(rms)]


def fit(env: list, role: str, want_s: float) -> float:
    """How well a take's shape fits its job (higher is better)."""
    if not env:
        return -99.0
    import numpy as np
    e = np.array(env)
    third = max(1, len(e) // 3)
    first, last = e[:third].mean(), e[-third:].mean()
    length = -abs(len(e) - want_s) / max(want_s, 1.0) * 10       # a take far from the asked length cuts badly
    if role == "open":
        shape = (last - first)                                     # quiet first, rising
    elif role == "end":
        shape = (e[-third - third:-third].mean() - e[:third].mean()) if len(e) > 2 * third else 0.0
    else:
        shape = -float(e[third:].std())                            # even under the voice
    silence = -10.0 * float((e < -45).mean())                      # dead air inside a cue
    return float(shape + length + silence)


# ── the bed for one video ──────────────────────────────────────────────────────────────────────────────────

def ensure_music(job: Path, look: dict, total: float, log=print, *, generate_fn=None, sections=None):
    """A bed scored for this timeline (job/sound/_bed.flac), or None to keep the channel's usual music."""
    cfg = wanted(look)
    if not cfg:
        return None
    import epidemic_sound
    generate_fn = generate_fn or generate
    try:
        sections = sections if sections is not None else epidemic_sound._sections(job, total)
        if not sections:
            return None
        sig = hashlib.sha1(json.dumps([[round(a, 2), round(b, 2), m] for a, b, m, _ in sections]
                                      + [cfg]).encode()).hexdigest()[:12]
        plan_f = job / "sound" / "score_plan.json"
        if plan_f.is_file():
            got = json.loads(plan_f.read_text(encoding="utf-8"))
            if got.get("sig") == sig and all((job / "sound" / "music" / p["file"]).is_file() for p in got["tracks"]):
                return epidemic_sound._bed(job, got["tracks"], total)
        jobs = roles(len(sections))
        folder = job / "sound" / "music"
        folder.mkdir(parents=True, exist_ok=True)
        log(f"  score: {len(sections)} cue(s) on Kie (Suno {SUNO}), about {len(sections) * CREDITS_PER_CUE} credits")

        def one(i):
            a, b, mood, title = sections[i]
            # every section but the last runs a crossfade longer (the crossfade eats it at the join)
            want = (b - a) + (epidemic_sound.XFADE_S if i < len(sections) - 1 else 2.0)
            inputs = {"custom_mode": True, "instrumental": True, "model": SUNO,
                      "title": re.sub(r"[^\w ]+", "", str(title or f"Section {i + 1}"))[:60] or f"Section {i + 1}",
                      "style": style_text(cfg, mood, jobs[i]), "negative_tags": avoid_text(cfg),
                      "duration": int(min(360, max(10, round(want))))}
            takes = generate_fn(inputs, folder / f"{i + 1:02d}_score", log=log)
            scored = sorted(((fit(envelope(t), jobs[i], want), t) for t in takes), key=lambda x: -x[0])
            best = scored[0][1]
            return {"t0": round(a, 2), "t1": round(b, 2), "mood": mood, "chapter": title, "file": best.name,
                    "role": jobs[i], "style": inputs["style"], "fit": round(scored[0][0], 2)}

        with ThreadPoolExecutor(max_workers=PARALLEL) as ex:
            plan = list(ex.map(one, range(len(sections))))
        plan_f.write_text(json.dumps({"sig": sig, "tracks": plan}, indent=1), encoding="utf-8")
        for p in plan:
            log(f"  score {p['t0'] / 60:5.1f}-{p['t1'] / 60:5.1f} min [{p['role']}, {p['mood'] or '-'}] {p['file']}")
        return epidemic_sound._bed(job, plan, total)
    except Exception as exc:                                       # noqa: BLE001 - the usual music stays
        log(f"  score: not made ({type(exc).__name__}: {str(exc)[:140]}); the channel's usual music plays")
        return None


def install(engine=None):
    """Score videos whose style asks for it; every other video keeps Studio's own music path."""
    try:
        import epidemic_sound
    except ImportError:
        return
    original = epidemic_sound.ensure_music
    if getattr(original, "_cr_score", False):
        return

    def ensure(job, look, total, log=print):
        bed = ensure_music(Path(job), look, total, log) if wanted(look) else None
        return bed if bed is not None else original(job, look, total, log)

    ensure._cr_score = True
    epidemic_sound.ensure_music = ensure
