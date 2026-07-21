#!/usr/bin/env python3
"""CLI: critique a local ChannelRecipe cook with Gemini video understanding."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Allow running from repo root / scripts
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

# Video QC needs real Gemini multimodal (not Atlas text).
os.environ.setdefault("ALLOW_GOOGLE_GEMINI", "1")


def main() -> int:
    ap = argparse.ArgumentParser(description="Gemini visual QC for a local MP4")
    ap.add_argument("video", type=Path)
    ap.add_argument("--title", default="")
    ap.add_argument("--description", default="")
    ap.add_argument("--tags", default="")
    ap.add_argument("--out-dir", type=Path, default=None)
    ap.add_argument("--model", default=None)
    args = ap.parse_args()

    from core.video_qc import critique_local_video, critique_to_markdown

    out_dir = args.out_dir or (ROOT / "output" / "qc_runs" / args.video.stem)
    out_dir.mkdir(parents=True, exist_ok=True)

    def progress(msg: str) -> None:
        print(f"[qc] {msg}", flush=True)

    data = critique_local_video(
        args.video,
        title=args.title,
        description=args.description,
        tags=args.tags,
        model=args.model,
        progress=progress,
        work_dir=out_dir,
    )
    json_path = out_dir / "critique.json"
    md_path = out_dir / "critique.md"
    json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    md_path.write_text(critique_to_markdown(data), encoding="utf-8")
    print(f"[qc] wrote {json_path}")
    print(f"[qc] wrote {md_path}")
    print(f"[qc] score={data.get('overall_score')} verdict={data.get('verdict')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
