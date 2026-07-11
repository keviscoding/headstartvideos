"""
ChannelRecipe cooks on Modal — scale-to-zero ffmpeg workers.

Why Modal (not another always-on DO box):
  - Pay only while a cook is running
  - Burst to N parallel cooks when the queue spikes
  - $0 when idle overnight

Setup (one-time):
  1. pip install modal && modal setup
  2. modal secret create channelrecipe-env \\
       DATABASE_URL=... SPACES_KEY=... SPACES_SECRET=... SPACES_BUCKET=... \\
       SPACES_REGION=... SPACES_ENDPOINT=... SPACES_CDN_ENDPOINT=... \\
       GEMINI_KEY=... ATLASCLOUD_KEY=... GROQ_API_KEY=... \\
       POSTHOG_KEY=... SENTRY_DSN=... SECRETS_KEY=... \\
       COOK_ON_WEB=0 ALLOW_LOCAL_WHISPER=0
  3. modal deploy modal_cook.py
  4. On DigitalOcean **web**: COOK_ON_WEB=0 COOK_ON_MODAL=1
     + MODAL_TOKEN_ID / MODAL_TOKEN_SECRET
     (You can scale cook-worker instances to 0.)

Local test one job:
  modal run modal_cook.py --job-id <uuid>
"""
from __future__ import annotations

import modal

APP_NAME = "channelrecipe-cook"
SECRET_NAME = "channelrecipe-env"

app = modal.App(APP_NAME)

# CPU cook image: ffmpeg + app deps. No GPU needed for slideshow assemble.
cook_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg", "git")
    .pip_install(
        "fastapi==0.135.2",
        "uvicorn==0.42.0",
        "pydantic==2.12.5",
        "python-dotenv==1.2.2",
        "google-genai==2.3.0",
        "google-api-python-client>=2.100.0",
        "groq>=0.9.0",
        "anthropic==0.86.0",
        "httpx==0.28.1",
        "pillow==12.1.1",
        "python-multipart>=0.0.9",
        "resend>=2.0.0",
        "stripe>=10.0.0",
        "posthog>=3.5.0",
        "sentry-sdk[fastapi]>=2.0.0",
        "psycopg[binary]>=3.1.0",
        "boto3>=1.34.0",
        "cryptography>=42.0.0",
        # Whisper via Groq in prod — skip heavy faster-whisper on Modal
    )
    .env({"ALLOW_LOCAL_WHISPER": "0", "COOK_ON_WEB": "0"})
    .add_local_dir(
        ".",
        remote_path="/app",
        copy=True,
        ignore=[
            ".git",
            ".env",
            "output",
            "data",
            "**/__pycache__",
            ".venv",
            "node_modules",
            "*.mp4",
            "*.wav",
        ],
    )
)


def _run_one(job_id: str) -> str:
    import os
    import sys
    import time

    os.chdir("/app")
    if "/app" not in sys.path:
        sys.path.insert(0, "/app")

    from webapp.cook_runner import hydrate_job_from_row, run_cook_job
    from webapp.database import claim_cook_job, get_cook_job, update_cook_job

    worker_id = f"modal-{os.getenv('MODAL_TASK_ID', 'local')}-{job_id[:8]}"
    row = claim_cook_job(job_id, worker_id)
    if not row:
        existing = get_cook_job(job_id)
        status = (existing or {}).get("status")
        print(f"[modal] skip {job_id} — not claimable (status={status})")
        return f"skipped:{status}"

    job = hydrate_job_from_row(row)
    job["status"] = "running"
    job["progress"].append({
        "time": time.time(),
        "message": "Starting your cook on Modal...",
        "phase": "running",
    })
    try:
        import json
        update_cook_job(
            job_id,
            status="running",
            started=True,
            heartbeat=True,
            worker_id=worker_id,
            progress_json=json.dumps(job["progress"][-40:]),
        )
    except Exception:
        pass

    def _track(uid, event, props=None):
        try:
            import config
            if not getattr(config, "POSTHOG_KEY", ""):
                return
            from posthog import Posthog
            client = Posthog(project_api_key=config.POSTHOG_KEY, host=config.POSTHOG_HOST)
            client.capture(distinct_id=str(uid), event=event, properties=props or {})
            client.shutdown()
        except Exception:
            pass

    def _capture(exc, context=None):
        print(f"[modal] error: {exc} context={context}")

    def _cancel():
        r = get_cook_job(job_id)
        return bool(r and r.get("status") == "cancelled")

    print(f"[modal] cooking {job_id} recipe={row.get('recipe')}")
    run_cook_job(
        job_id,
        job,
        track=_track,
        capture_error=_capture,
        cancel_check=_cancel,
    )
    print(f"[modal] finished {job_id} status={job.get('status')}")
    return job.get("status") or "unknown"


@app.function(
    image=cook_image,
    secrets=[modal.Secret.from_name(SECRET_NAME)],
    timeout=60 * 60,
    cpu=2.0,
    memory=4096,
    # Burst ceiling — raise via redeploy if needed. Pay only for running containers.
    max_containers=8,
)
def run_cook(job_id: str) -> str:
    """Entrypoint spawned by the ChannelRecipe web dyno."""
    return _run_one(job_id)


@app.local_entrypoint()
def main(job_id: str = ""):
    if not job_id:
        print("Usage: modal run modal_cook.py --job-id <uuid>")
        return
    print(run_cook.remote(job_id))
