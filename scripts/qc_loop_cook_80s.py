#!/usr/bin/env python3
"""
Local QC-loop cook: full 80s fashion script → Atlas VO → cinematic B-roll → critique.

Runs as admin email nwalikelv@gmail.com against local sqlite (prod session not available).
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

os.environ.setdefault("ADMIN_EMAILS", "nwalikelv@gmail.com")
os.environ.setdefault("COOK_ON_WEB", "1")
os.environ.setdefault("ALLOW_LOCAL_WHISPER", "1")

OUT = ROOT / "output" / "qc_loop_80s"
OUT.mkdir(parents=True, exist_ok=True)
SCRIPT_PATH = ROOT / "output" / "qc_80s_fashion" / "full_script.txt"
TITLE = "HOW TO SHOP 80'S FASHION IN THE UK TODAY (FOR A 50+ Y/O)"


def main() -> int:
    script = SCRIPT_PATH.read_text(encoding="utf-8").strip()
    words = len(script.split())
    print(f"[qc-loop] script words={words} est_min={words/150:.1f}", flush=True)

    # Local DB user (admin)
    from webapp.database import (
        get_user_by_email, create_user, update_user, create_cook_job, get_cook_job,
    )
    email = "nwalikelv@gmail.com"
    user = get_user_by_email(email)
    if not user:
        user = create_user(email)
        print(f"[qc-loop] created local user id={user['id']}", flush=True)
    update_user(user["id"], plan="pro", credits=max(int(user.get("credits") or 0), 20))
    user = get_user_by_email(email)
    print(f"[qc-loop] user id={user['id']} plan={user.get('plan')} credits={user.get('credits')}", flush=True)

    # 1) Full voiceover via Atlas (chunked) — proves no truncation
    from core.voiceover_gen import generate_voiceover
    vo_dir = OUT / "voiceover"
    vo_dir.mkdir(parents=True, exist_ok=True)
    print("[qc-loop] generating FULL voiceover via Atlas…", flush=True)
    t0 = time.time()
    vo_path = generate_voiceover(
        script=script,
        voice="leo",
        output_dir=str(vo_dir),
    )
    print(f"[qc-loop] VO done in {time.time()-t0:.0f}s → {vo_path}", flush=True)

    import subprocess
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", vo_path],
        capture_output=True, text=True,
    )
    vo_dur = float((probe.stdout or "0").strip() or 0)
    print(f"[qc-loop] VO duration={vo_dur:.1f}s ({vo_dur/60:.2f} min)", flush=True)
    expected = words / 150 * 60
    if vo_dur < expected * 0.7:
        raise SystemExit(
            f"VO still truncated: {vo_dur:.0f}s vs expected ~{expected:.0f}s — aborting cook"
        )

    # 2) Cook cinematic B-roll
    job_id = str(uuid.uuid4())
    req = {
        "script": script,
        "voiceover_path": vo_path,
        "title": TITLE,
        "recipe": "broll_cinematic",
        "image_quality": "standard",
        "notify_email": email,
    }
    create_cook_job(
        job_id,
        int(user["id"]),
        recipe="broll_cinematic",
        title=TITLE,
        request_json=json.dumps(req),
        credit_deducted=True,
        status="web_queued",
    )
    print(f"[qc-loop] cook job {job_id} starting…", flush=True)

    from webapp.cook_runner import run_cook_job, hydrate_job_from_row
    row = get_cook_job(job_id)
    job = hydrate_job_from_row(row)
    run_cook_job(job_id, job)
    row = get_cook_job(job_id)
    print(f"[qc-loop] cook status={row.get('status')} error={row.get('error')}", flush=True)
    if (row.get("status") or "") not in ("completed", "complete"):
        raise SystemExit(f"Cook failed: status={row.get('status')} error={row.get('error')}")

    result = {}
    try:
        result = json.loads(row.get("result_json") or "{}")
    except Exception:
        result = job.get("result") or {}
    out_mp4 = result.get("output_path") or ""
    print(f"[qc-loop] output={out_mp4}", flush=True)

    # 3) Harsh QC
    from core.video_qc import critique_local_video, critique_to_markdown
    qc_dir = OUT / "critique"
    data = critique_local_video(
        out_mp4,
        title=TITLE,
        description="UK 80s fashion shopping guide for women 50+",
        tags="80s fashion, fashion over 50, vintage shopping UK",
        script=script,
        progress=lambda m: print(f"[qc] {m}", flush=True),
        work_dir=qc_dir,
    )
    (qc_dir / "critique.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    (qc_dir / "critique.md").write_text(critique_to_markdown(data), encoding="utf-8")
    print(
        f"[qc-loop] SCORE={data.get('overall_score')} "
        f"completeness={data.get('completeness')} "
        f"verdict={data.get('verdict')}",
        flush=True,
    )
    summary = {
        "job_id": job_id,
        "vo_duration_sec": vo_dur,
        "output_path": out_mp4,
        "overall_score": data.get("overall_score"),
        "completeness": data.get("completeness"),
        "verdict": data.get("verdict"),
        "primary_failures": data.get("primary_failures"),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"[qc-loop] wrote {OUT / 'summary.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
