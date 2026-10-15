"""Measure a reference video's edit, picture and sound on this computer, for free.

A style study needs facts it can trust before any model describes the video:
where the cuts are and how shots change (hard cut, dissolve, fade, flash, whip),
what moves inside each shot (nothing, the camera over a still, or the picture
itself), how dark, saturated and grainy the picture is, and the shape of the
soundtrack: when the narrator talks, whether music fills the pauses, how loud
it sits, how often something hits.

Only ffmpeg and numpy are required. OpenCV (optical flow) separates camera moves
from moving pictures, and the voice detector bundled with faster-whisper finds
the narration; without them the matching numbers are left out, never guessed.
No model request is made here.
"""
from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path

import numpy as np

SCHEMA = 1
FPS = 10                     # analysis frame rate: 0.1 s precision is plenty for shots of 0.5 s and longer
W, H = 128, 72               # analysis frame size (16:9); flow and statistics run on this
CW, CH = 64, 36              # the small grey picture the cut detector compares (half the analysis size)
AUDIO_SR = 16000
HOP_S = 0.05                 # loudness frames
MAX_SECONDS = 15 * 60        # longest stretch measured from one file

# motion classes, in pixels per frame at W x H and FPS (calibrated on the benchmark set)
STILL_MOVE = 0.04            # below this a shot is a still picture
LOCAL_MOVE = 0.10            # picture-content motion left after removing the camera's own move
ZOOM_MOVE = 0.0008           # scale change per frame that counts as a zoom


def _run(cmd: list, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=timeout)


def probe(path) -> dict:
    """Duration and whether the file has video and sound."""
    p = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
              "-of", "default=nw=1", str(path)], timeout=60)
    text = p.stdout.decode("utf-8", "replace")
    m = re.search(r"duration=([\d.]+)", text)
    return {"duration": float(m.group(1)) if m else 0.0, "video": "codec_type=video" in text,
            "audio": "codec_type=audio" in text}


# ── pictures ────────────────────────────────────────────────────────────────────────────────────────────────

def _frames(path, seconds: float):
    """Yield W x H RGB frames at FPS."""
    cmd = ["ffmpeg", "-v", "error", "-t", f"{seconds:.2f}", "-i", str(path), "-an", "-vf",
           f"fps={FPS},scale={W}:{H}:flags=area,format=rgb24", "-f", "rawvideo", "-"]
    size = W * H * 3
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint8).reshape(H, W, 3)
    finally:
        proc.stdout.close()
        proc.wait(timeout=60)


def _grid():
    ys, xs = np.mgrid[4:H - 4:4, 4:W - 4:4]
    return xs.ravel().astype(np.int32), ys.ravel().astype(np.int32)


def _camera_fit(flow, xs, ys):
    """Fit the camera's own move (zoom, pan, a little rotation) to the optical flow.
    Returns (pan px/frame, zoom per frame, flow the camera move leaves unexplained)."""
    u = flow[ys, xs, 0].astype(np.float64)
    v = flow[ys, xs, 1].astype(np.float64)
    x = xs - W / 2.0
    y = ys - H / 2.0
    # u = s*x - r*y + tx ; v = r*x + s*y + ty
    a = np.zeros((2 * len(x), 4))
    a[0::2, 0], a[0::2, 1], a[0::2, 2] = x, -y, 1
    a[1::2, 0], a[1::2, 1], a[1::2, 3] = y, x, 1
    b = np.empty(2 * len(x))
    b[0::2], b[1::2] = u, v
    sol, *_ = np.linalg.lstsq(a, b, rcond=None)
    rest = b - a @ sol
    resid = np.hypot(rest[0::2], rest[1::2])
    # the median ignores a subtitle or logo; the 80th percentile still sees a figure walking across a still
    return float(math.hypot(sol[2], sol[3])), float(sol[0]), float(np.percentile(resid, 80))


def _texture(rgb) -> dict:
    """Picture texture of one frame: flat colour (drawings), grain (film, phone video), colour count."""
    g = rgb.astype(np.float32).mean(2)
    blocks = g[: H // 8 * 8, : W // 8 * 8].reshape(H // 8, 8, W // 8, 8).std(axis=(1, 3))
    hp = g[1:-1, 1:-1] * 4 - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    calm_px = np.kron(blocks < 6, np.ones((8, 8), bool))[1:-1, 1:-1]     # same shape as hp
    grain = float(np.median(np.abs(hp[calm_px]))) if calm_px.sum() > 200 else float(np.median(np.abs(hp)))
    q = (rgb >> 4).astype(np.int32)
    colours = len(np.unique(q[..., 0] * 256 + q[..., 1] * 16 + q[..., 2]))
    return {"flat": float((blocks < 2.5).mean()), "grain": grain, "colours": colours}


def _scan(path, seconds: float) -> dict:
    """One pass over the frames: the cut detector's tiny pictures plus per-frame motion and light."""
    try:
        import cv2
    except ImportError:
        cv2 = None
    xs, ys = _grid()
    small, luma, sat, pan, zoom, local, diff, pop, area, keep = [], [], [], [], [], [], [], [], [], []
    prev_g = None
    for i, rgb in enumerate(_frames(path, seconds)):
        f = rgb.astype(np.float32)
        g = f @ np.array([0.299, 0.587, 0.114], np.float32)
        small.append(g.reshape(CH, H // CH, CW, W // CW).mean(axis=(1, 3)).ravel())
        luma.append(float(g.mean()))
        mx, mn = f.max(2), f.min(2)
        sat.append(float(np.mean((mx - mn) / (mx + 1e-3))))
        if prev_g is not None:
            d = np.abs(g - prev_g)
            diff.append(float(d.mean()))
            blocks = d.reshape(H // 8, 8, W // 8, 8).mean(axis=(1, 3))
            pop.append(float(blocks.max()))                  # something appeared, moved or changed somewhere
            area.append(float((blocks > 6).mean()))          # ...and how much of the picture it covered
            if cv2 is not None:
                flow = cv2.calcOpticalFlowFarneback(prev_g.astype(np.uint8), g.astype(np.uint8), None,
                                                    0.5, 2, 9, 3, 5, 1.1, 0)
                p, z, r = _camera_fit(flow, xs, ys)
                pan.append(p), zoom.append(z), local.append(r)
        else:
            diff.append(0.0)
            pop.append(0.0)
            area.append(0.0)
            if cv2 is not None:
                pan.append(0.0), zoom.append(0.0), local.append(0.0)
        if i % FPS == 0:                                   # one frame a second for texture and palette
            keep.append((i, rgb.copy()))
        prev_g = g
    return {"small": np.array(small, np.float32), "luma": np.array(luma), "sat": np.array(sat),
            "pan": np.array(pan) if pan else None, "zoom": np.array(zoom) if zoom else None,
            "local": np.array(local) if local else None, "diff": np.array(diff),
            "pop": np.array(pop), "area": np.array(area), "keep": keep}


def _corr(a, b):
    a = a - a.mean(-1, keepdims=True)
    b = b - b.mean(-1, keepdims=True)
    return (a * b).sum(-1) / (np.linalg.norm(a, axis=-1) * np.linalg.norm(b, axis=-1) + 1e-3)


def transitions(scan: dict) -> list:
    """[(first frame, last frame, kind)] for every change of shot: cut, dissolve, fade (through black),
    flash (through white) or whip (a cut hidden in a fast camera move)."""
    s, luma = scan["small"], scan["luma"]
    n = len(s)
    if n < 4:
        return []
    live = s.std(0) > 2.0                                  # pixels that never change (bars, a logo) hide cuts
    x = s[:, live] if live.sum() > s.shape[1] * 0.1 else s
    c1 = np.ones(n)
    c1[1:] = _corr(x[1:], x[:-1])
    med = float(np.median(c1[1:]))
    thr = min(0.6, med * 0.85)
    drop = max(0.2, (1.0 - med) * 0.55)
    pan = scan.get("pan")
    marks = []
    for i in range(1, n):
        lo, hi = max(1, i - 12), min(n, i + 12)
        if c1[i] < thr and c1[i] < float(np.median(c1[lo:hi])) - drop:
            kind = "cut"
            if pan is not None and i + 1 < n and max(pan[i - 1], pan[min(i + 1, n - 1)]) > 4.0:
                kind = "whip"
            marks.append((i, i, kind))
    # through black or white
    dark, bright = luma < 14, luma > 235
    for flag, kind in ((dark, "fade"), (bright, "flash")):
        i = 0
        while i < n:
            if flag[i]:
                j = i
                while j + 1 < n and flag[j + 1]:
                    j += 1
                if (kind == "fade" or j - i <= 4) and 0 < i and j < n - 1:
                    marks.append((i, j, kind))
                i = j + 1
            else:
                i += 1
    # slow dissolves: a frame that is a mix of the pictures 0.4 s before and after, which really differ
    k = 4
    soft = []
    for i in range(k, n - k):
        a0, b0, m0 = x[i - k], x[i + k], x[i]
        if float(_corr(a0, b0)) > 0.5:
            continue
        A = np.stack([a0, b0], 1)
        coef, *_ = np.linalg.lstsq(A, m0, rcond=None)
        fit = A @ coef
        if 0.15 < coef[0] < 0.9 and 0.15 < coef[1] < 0.9 and float(_corr(fit, m0)) > 0.97:
            soft.append(i)
    i = 0
    while i < len(soft):
        j = i
        while j + 1 < len(soft) and soft[j + 1] - soft[j] <= 1:
            j += 1
        if soft[j] - soft[i] >= 2 and not any(soft[i] - k <= m[0] <= soft[j] + k for m in marks):
            marks.append((soft[i], soft[j], "dissolve"))
        i = j + 1
    marks.sort()
    merged = []
    for m in marks:
        if merged and m[0] - merged[-1][1] <= 2:            # one change of shot, seen by two tests
            prev = merged[-1]
            keep = prev[2] if prev[2] in ("fade", "dissolve", "flash", "whip") else m[2]
            merged[-1] = (prev[0], max(prev[1], m[1]), keep)
        else:
            merged.append(m)
    return merged


def _shots(n: int, marks: list) -> list:
    edges, kinds = [0], ["start"]
    for a, b, kind in marks:
        mid = (a + b + 1) // 2
        if mid - edges[-1] >= 2:                            # 0.2 s: a glitch passage fakes many cuts
            edges.append(mid)
            kinds.append(kind)
    edges.append(n)
    return [(edges[i], edges[i + 1], kinds[i]) for i in range(len(edges) - 1) if edges[i + 1] > edges[i]]


def _pops(scan: dict, lo: int, hi: int) -> int:
    """Pop-ons inside a shot: a word, label, line or figure appearing while most of the picture stays put
    (the way motion graphics and drawn explainers move), counted once per burst."""
    pop, area = scan["pop"][lo:hi], scan["area"][lo:hi]
    pan = scan["pan"][lo:hi] if scan.get("pan") is not None else np.zeros(len(pop))
    hit = (pop > 15) & (area < 0.35) & (pan < 0.3)
    return int(((hit[1:] & ~hit[:-1]).sum() + (1 if len(hit) and hit[0] else 0)))


def _motion(scan: dict, a: int, b: int) -> dict:
    """What moves inside frames a..b of one shot: nothing (still), the camera over the picture (camera), or the
    picture itself (moving); plus pop-ons, the motion-graphics kind of change."""
    lo, hi = a + 1, b - 1                                   # ignore the frames that touch a cut
    if hi - lo < 2:
        lo, hi = a, b
    diff = scan["diff"][lo:hi]
    pops = _pops(scan, lo, hi)
    if scan.get("pan") is None:
        moving = float(np.median(diff)) if len(diff) else 0.0
        return {"kind": "still" if moving < 0.6 else "moving", "flow": False, "pops": pops}
    pan, zoom, local = scan["pan"][lo:hi], scan["zoom"][lo:hi], scan["local"][lo:hi]
    p, z, l = float(np.median(pan)), float(np.median(np.abs(zoom))), float(np.median(local))
    cam = p >= STILL_MOVE or z >= ZOOM_MOVE
    if l >= max(LOCAL_MOVE, 0.35 * p):
        kind = "moving"
    elif cam:
        kind = "camera"
    else:
        kind = "still"
    # snappy: the camera's move arrives in a burst rather than drifting evenly
    speed = np.hypot(pan, np.abs(zoom) * W)
    snap = float(speed.max() / (speed.mean() + 1e-6)) if cam and len(speed) > 3 else 0.0
    return {"kind": kind, "flow": True, "pan": round(p, 3), "zoom": round(float(np.median(zoom)), 5),
            "local": round(l, 3), "snap": round(snap, 2), "pops": pops}


def _palette(frames: list, k: int = 6) -> list:
    if not frames:
        return []
    px = np.concatenate([f[::4, ::4].reshape(-1, 3) for f in frames]).astype(np.float32)
    rng = np.random.default_rng(7)
    centres = px[rng.choice(len(px), k, replace=False)] if len(px) >= k else px
    for _ in range(12):
        d = ((px[:, None, :] - centres[None]) ** 2).sum(-1)
        lab = d.argmin(1)
        for j in range(len(centres)):
            if (lab == j).any():
                centres[j] = px[lab == j].mean(0)
    share = np.bincount(lab, minlength=len(centres)) / len(lab)
    order = np.argsort(-share)
    return [{"hex": "#%02X%02X%02X" % tuple(int(v) for v in centres[j]), "share": round(float(share[j]), 3)}
            for j in order if share[j] >= 0.02]


def _look(scan: dict) -> dict:
    frames = [f for _, f in scan["keep"]]
    tex = [_texture(f) for f in frames]
    luma = scan["luma"]
    out = {"luma": round(float(np.median(luma)), 1), "dark_share": round(float((luma < 60).mean()), 3),
           "bright_share": round(float((luma > 170).mean()), 3), "saturation": round(float(np.median(scan["sat"])), 3)}
    if tex:
        out.update(flat=round(float(np.median([t["flat"] for t in tex])), 3),
                   grain=round(float(np.median([t["grain"] for t in tex])), 2),
                   colours=int(np.median([t["colours"] for t in tex])))
    if frames:
        g = np.stack([f.astype(np.float32).mean(2) for f in frames])
        centre = g[:, H // 4: 3 * H // 4, W // 4: 3 * W // 4].mean()
        corners = np.concatenate([g[:, :H // 6, :W // 8].ravel(), g[:, :H // 6, -W // 8:].ravel(),
                                  g[:, -H // 6:, :W // 8].ravel(), g[:, -H // 6:, -W // 8:].ravel()]).mean()
        out["vignette"] = round(float(corners / (centre + 1e-3)), 3)
        rows = g.mean(axis=(0, 2))
        out["letterbox"] = bool(rows[:4].mean() < 12 and rows[-4:].mean() < 12 and rows[H // 2] > 30)
    out["palette"] = _palette(frames)
    return out


def measure_picture(path, seconds: float) -> dict:
    scan = _scan(path, seconds)
    n = len(scan["small"])
    if n < 4:
        return {}
    marks = transitions(scan)
    shots = []
    for a, b, kind in _shots(n, marks):
        row = {"t0": round(a / FPS, 2), "t1": round(b / FPS, 2), "in": kind}
        row.update(_motion(scan, a, b))
        shots.append(row)
    return {"frames": n, "shots": shots, "look": _look(scan), "flow": scan.get("pan") is not None,
            "keyframes": [(i / FPS, f) for i, f in scan["keep"]]}


# ── sound ───────────────────────────────────────────────────────────────────────────────────────────────────

def _audio(path, seconds: float):
    p = _run(["ffmpeg", "-v", "error", "-t", f"{seconds:.2f}", "-i", str(path), "-vn", "-ac", "1", "-ar",
              str(AUDIO_SR), "-f", "f32le", "-"], timeout=300)
    return np.frombuffer(p.stdout[: len(p.stdout) // 4 * 4], np.float32).copy()


def speech_spans(x) -> list:
    """[(start s, end s)] where somebody speaks, from the voice detector faster-whisper ships with; None without it."""
    try:
        from faster_whisper.vad import VadOptions, get_speech_timestamps
    except ImportError:
        return None
    # strict enough that a music bed under the voice does not count as speech; checked against word timings
    opts = VadOptions(threshold=0.7, min_speech_duration_ms=150, min_silence_duration_ms=80, speech_pad_ms=0)
    try:
        rows = get_speech_timestamps(x, opts, sampling_rate=AUDIO_SR)
    except Exception:                                       # noqa: BLE001 - an onnx problem must not stop a study
        return None
    return [(r["start"] / AUDIO_SR, r["end"] / AUDIO_SR) for r in rows]


def _bands(x):
    """Per HOP_S frame: loudness (dBFS RMS), sub-bass under 70 Hz (below a narrator's own voice), the speech band
    (300-3400 Hz) and spectral flatness (low for music and held notes, high for rain, wind and whooshes)."""
    hop = int(HOP_S * AUDIO_SR)
    n = len(x) // hop
    if n < 4:
        return None
    rms = np.sqrt((x[: n * hop].reshape(n, hop) ** 2).mean(1) + 1e-12)
    win = 2048                                              # 8 Hz bins: enough to see under 70 Hz
    pad = np.pad(x, (0, win))
    frames = np.stack([pad[i * hop:i * hop + win] for i in range(n)]) * np.hanning(win).astype(np.float32)
    spec = np.abs(np.fft.rfft(frames, axis=1)) ** 2 + 1e-12
    freqs = np.fft.rfftfreq(win, 1 / AUDIO_SR)
    total = spec.sum(1)

    def share(lo, hi):
        return 10 * np.log10(spec[:, (freqs >= lo) & (freqs < hi)].sum(1) / total)
    band = spec[:, (freqs >= 150) & (freqs < 6000)]
    flat = np.exp(np.log(band).mean(1)) / band.mean(1)
    db = 20 * np.log10(rms)
    return {"db": db, "low": db + share(25, 70), "mid": db + share(300, 3400), "flat": flat}


def measure_sound(path, seconds: float) -> dict:
    x = _audio(path, seconds)
    if len(x) < AUDIO_SR * 2:
        return {}
    b = _bands(x)
    if b is None:
        return {}
    db = b["db"]
    n = len(db)
    length = n * HOP_S
    out = {"seconds": round(length, 1)}
    loud = db[db > -70]
    if len(loud):
        out["level_p95_db"] = round(float(np.percentile(loud, 95)), 1)
    spans = speech_spans(x)
    if spans is None:
        return out
    speech = np.zeros(n, bool)
    for a, e in spans:
        speech[int(a / HOP_S):int(math.ceil(e / HOP_S))] = True
    out["speech_share"] = round(float(speech.mean()), 3)
    # sudden jumps: hits and stabs where nobody speaks; deep booms anywhere (a voice has almost nothing under 70 Hz)
    jump = db[2:] - db[:-2]
    hits = np.flatnonzero((jump > 10) & ~speech[2:] & (db[2:] > np.percentile(db, 50)))
    # a boom rises sharply, dominates the sound under 70 Hz and rings for at least 0.15 s (a plosive does not)
    low_jump = b["low"][2:] - b["low"][:-2]
    dominant = (b["low"] - db) > -6
    rings = dominant[2:] & np.roll(dominant, -1)[2:] & np.roll(dominant, -2)[2:]
    booms = np.flatnonzero((low_jump > 10) & rings & (b["low"][2:] > np.percentile(db, 95) - 20))

    def dedupe(ix):
        kept, last = [], -99
        for i in ix:
            if i - last > 6:
                kept.append(i)
                last = i
        return kept
    out["hits_in_pauses_per_min"] = round(len(dedupe(hits)) / (length / 60), 1)
    out["booms_per_min"] = round(len(dedupe(booms)) / (length / 60), 1)
    if speech.sum() < 20:
        out["narrated"] = False
        quiet = db < np.percentile(db, 95) - 45
        out["silence_share"] = round(float(quiet.mean()), 3)
        return out
    out["narrated"] = True
    vpk = float(np.percentile(db[speech], 95))
    # pauses between phrases: what fills them (music, ambience, nothing) and how loud, against the voice peak
    gaps, i = [], 0
    while i < n:
        if not speech[i]:
            j = i
            while j < n and not speech[j]:
                j += 1
            if i > 0 and j < n:                            # interior pauses only
                gaps.append((i, j))
            i = j
        else:
            i += 1
    mins = length / 60
    lens = np.array([(j - i) * HOP_S for i, j in gaps]) if gaps else np.zeros(0)
    out["pauses_per_min"] = {"0.3": round(float((lens >= 0.3).sum() / mins), 1),
                             "0.6": round(float((lens >= 0.6).sum() / mins), 1),
                             "1.5": round(float((lens >= 1.5).sum() / mins), 1)}
    out["longest_pause_s"] = round(float(lens.max()), 2) if len(lens) else 0.0
    inner = []
    for i, j in gaps:
        if (j - i) * HOP_S >= 0.3:
            inner.extend(range(i + 1, j - 1))                # skip the frames touching speech
    if inner:
        inner = np.array(inner)
        gap_db = db[inner] - vpk
        out["bed_in_pauses_db"] = round(float(np.median(gap_db)), 1)
        out["silent_pauses_share"] = round(float((gap_db < -40).mean()), 3)
        sounding = inner[gap_db >= -40]
        if len(sounding):
            flat = b["flat"][sounding]
            out["pause_fill"] = {"tonal": round(float((flat < 0.08).mean()), 3),     # music, a held note
                                 "noisy": round(float((flat > 0.3).mean()), 3)}      # rain, room, wind, whooshes
        filled = [float(np.median(db[i + 1:j - 1]) - vpk) >= -40 for i, j in gaps if (j - i) * HOP_S >= 0.3]
        out["filled_pauses_share"] = round(sum(filled) / len(filled), 3) if filled else None
    # a bed under the voice shows up as sub-bass the voice itself does not make; compare with the pauses
    out["sub_bass_under_speech_db"] = round(float(np.median(b["low"][speech] - b["mid"][speech])), 1)
    out["voice_peak_dbfs"] = round(vpk, 1)
    out["speech_s"] = round(float(speech.sum() * HOP_S), 1)
    return out


# ── putting it together ─────────────────────────────────────────────────────────────────────────────────────

def _q(values, p):
    return round(float(np.percentile(values, p)), 2) if len(values) else None


def edit_summary(shots: list, start: float = 0.0) -> dict:
    """Shot lengths, cuts per minute, transition mix and motion shares for a list of shots."""
    if not shots:
        return {}
    lens = np.array([s["t1"] - s["t0"] for s in shots])
    total = float(lens.sum())
    kinds = {}
    for s in shots[1:]:
        kinds[s["in"]] = kinds.get(s["in"], 0) + 1
    moves = {}
    for s, ln in zip(shots, lens):
        moves[s["kind"]] = moves.get(s["kind"], 0.0) + float(ln)
    out = {"shots": len(shots), "seconds": round(total, 1), "median_shot_s": _q(lens, 50),
           "p25_shot_s": _q(lens, 25), "p75_shot_s": _q(lens, 75),
           "cuts_per_min": round((len(shots) - 1) / max(total / 60, 1e-6), 1),
           "longest_shot_s": round(float(lens.max()), 1),
           "transitions": {k: round(v / max(1, len(shots) - 1), 3) for k, v in sorted(kinds.items())},
           "motion_share": {k: round(v / total, 3) for k, v in sorted(moves.items())},
           "pops_per_min": round(sum(s.get("pops", 0) for s in shots) / max(total / 60, 1e-6), 1),
           # stills that come alive with pop-ons (labels, lines, counters): animated graphics, not static pictures
           "still_with_pops_share": round(sum(ln for s, ln in zip(shots, lens) if s["kind"] == "still"
                                              and s.get("pops", 0) > 0) / total, 3)}
    cam = [s for s in shots if s["kind"] == "camera" and s.get("flow")]
    if cam:
        zooms = [s["zoom"] for s in cam]
        out["camera"] = {"zoom_in_share": round(float(np.mean([z > ZOOM_MOVE for z in zooms])), 3),
                         "zoom_out_share": round(float(np.mean([z < -ZOOM_MOVE for z in zooms])), 3),
                         "zoom_per_s": round(float(np.median(np.abs(zooms))) * FPS, 4),
                         "pan_px_per_s": round(float(np.median([s["pan"] for s in cam])) * FPS * (1920 / W), 1),
                         "snap": round(float(np.median([s["snap"] for s in cam])), 2)}
    return out


def measure(path, *, start: float = 0.0, seconds: float = None, keyframes: bool = False) -> dict:
    """Measure one file (one section of a reference). `start` is where the file begins in the full video."""
    info = probe(path)
    length = min(info["duration"] or 0.0, seconds or MAX_SECONDS, MAX_SECONDS)
    out = {"schema": SCHEMA, "start": round(start, 1), "seconds": round(length, 1)}
    if info["video"] and length > 0:
        pic = measure_picture(path, length)
        if pic:
            for s in pic["shots"]:
                s["t0"], s["t1"] = round(s["t0"] + start, 2), round(s["t1"] + start, 2)
            out["shots"] = pic["shots"]
            out["edit"] = edit_summary(pic["shots"])
            out["look"] = pic["look"]
            out["flow"] = pic["flow"]
            if keyframes:
                out["_keyframes"] = [(t + start, f) for t, f in pic["keyframes"]]
    if info["audio"] and length > 0:
        out["sound"] = measure_sound(path, length)
    return out


def _weighted(rows: list, key: str):
    vals = [(r.get(key), r.get("_w", 1.0)) for r in rows if isinstance(r.get(key), (int, float))]
    if not vals:
        return None
    return round(sum(v * w for v, w in vals) / sum(w for _, w in vals), 3)


def combine(sections: list) -> dict:
    """One video's sections (each from measure()) as one summary: overall, the opening and the rest."""
    sections = [s for s in sections if s]
    shots = [s for sec in sections for s in sec.get("shots") or []]
    out = {"schema": SCHEMA, "seconds": round(sum(s.get("seconds", 0) for s in sections), 1),
           "sections": [[s["start"], round(s["start"] + s["seconds"], 1)] for s in sections]}
    if shots:
        out["edit"] = edit_summary(shots)
        opening = [s for s in shots if s["t0"] < 60]
        later = [s for s in shots if s["t0"] >= 60]
        if opening and sections[0]["start"] == 0:
            out["opening"] = edit_summary(opening)
            first30 = [s for s in shots if s["t0"] < 30]
            out["opening"]["first_30s_median_shot_s"] = edit_summary(first30).get("median_shot_s")
        if later:
            out["body"] = edit_summary(later)
    looks = [dict(sec["look"], _w=sec.get("seconds", 1)) for sec in sections if sec.get("look")]
    if looks:
        look = {k: _weighted(looks, k) for k in ("luma", "dark_share", "bright_share", "saturation", "flat", "grain",
                                                 "colours", "vignette")}
        look["letterbox"] = any(lk.get("letterbox") for lk in looks)
        look["palette"] = looks[0].get("palette", [])
        out["look"] = {k: v for k, v in look.items() if v is not None}
    sounds = [dict(sec["sound"], _w=sec["sound"].get("seconds", 1)) for sec in sections if sec.get("sound")]
    if sounds:
        snd = {k: _weighted(sounds, k) for k in ("speech_share", "bed_in_pauses_db", "silent_pauses_share",
                                                 "filled_pauses_share", "hits_in_pauses_per_min", "booms_per_min",
                                                 "longest_pause_s", "sub_bass_under_speech_db", "voice_peak_dbfs")}
        per = [s["pauses_per_min"] for s in sounds if isinstance(s.get("pauses_per_min"), dict)]
        if per:
            snd["pauses_per_min"] = {k: round(float(np.mean([p[k] for p in per])), 1) for k in per[0]}
        fills = [s["pause_fill"] for s in sounds if isinstance(s.get("pause_fill"), dict)]
        if fills:
            snd["pause_fill"] = {k: round(float(np.mean([f[k] for f in fills])), 3) for k in fills[0]}
        narrated = [s.get("narrated") for s in sounds if "narrated" in s]
        if narrated:
            snd["narrated"] = sum(bool(v) for v in narrated) >= len(narrated) / 2
        out["sound"] = {k: v for k, v in snd.items() if v is not None}
    return out


def keyframe_sheet(frames: list, out: Path, cols: int = 6) -> Path:
    """Save small keyframes as one contact sheet (for a vision model or the review screen)."""
    from PIL import Image
    if not frames:
        return None
    rows = math.ceil(len(frames) / cols)
    sheet = Image.new("RGB", (cols * W * 2, rows * H * 2), (0, 0, 0))
    for i, (_, f) in enumerate(frames):
        im = Image.fromarray(f).resize((W * 2, H * 2))
        sheet.paste(im, ((i % cols) * W * 2, (i // cols) * H * 2))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=82)
    return out
