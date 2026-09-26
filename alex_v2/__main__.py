"""Command line toggle.

    python -m alex_v2 status          # mode, split, model, counts
    python -m alex_v2 on | off        # switch v2 on/off for every Alex chat
    python -m alex_v2 ab 50           # A/B test, 50% of chats on v2
    python -m alex_v2 shadow          # v1 sends, v2 logs what it would send
    python -m alex_v2 serve [--port 8799] [--host 127.0.0.1]   # the Alex v2 page on its own
    python -m alex_v2 lab chat.txt [--platform hinge] [--no-model]
    python -m alex_v2 due             # chats with a scheduled reply / follow-up due now
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from . import llm as LLM
from . import settings as S
from . import trace as TR


def _status() -> None:
    s = S.load()
    primary, fallback = LLM.resolve_model(s)
    split = f" ({round(s['ab_split'] * 100)}% of chats on v2)" if s["mode"] == "ab" else ""
    print(f"Alex v2 mode: {s['mode'].upper()}{split}")
    if s.get("_locked_by_env"):
        print(f"  locked by env: {', '.join(s['_locked_by_env'])}")
    print(f"  model: {primary}" + (f"  (fallback {fallback})" if fallback else ""))
    print(f"  data:  {S.data_dir()}")
    ab = TR.ab_summary()
    for arm in ("v1", "v2"):
        a = ab.get(arm)
        if a:
            rr = f"{a['reply_rate']:.0%}" if a.get("reply_rate") is not None else "-"
            print(f"  {arm}: {a['threads']} chats · replies<72h {rr} · agreed {a['pct_agreed']:.0%} · met {a['pct_met']:.0%}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m alex_v2", description="Alex v2 toggle")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("status")
    sub.add_parser("on")
    sub.add_parser("off")
    sub.add_parser("shadow")
    ab = sub.add_parser("ab")
    ab.add_argument("percent", type=float, nargs="?", default=50)
    sv = sub.add_parser("serve")
    sv.add_argument("--port", type=int, default=8799)
    sv.add_argument("--host", default="127.0.0.1")
    lb = sub.add_parser("lab")
    lb.add_argument("file")
    lb.add_argument("--platform", default="")
    lb.add_argument("--tz", default="Europe/London")
    lb.add_argument("--no-model", action="store_true")
    sub.add_parser("due")
    a = p.parse_args(argv)

    if a.cmd in (None, "status"):
        _status()
    elif a.cmd in ("on", "off", "shadow"):
        S.save({"mode": a.cmd})
        _status()
    elif a.cmd == "ab":
        S.save({"mode": "ab", "ab_split": max(0.0, min(100.0, a.percent)) / 100})
        _status()
    elif a.cmd == "serve":
        try:
            import uvicorn
        except ImportError:
            print("pip install uvicorn  (or mount alex_v2.dashboard.router in your existing app)")
            return 1
        from .dashboard import create_app
        print(f"Alex v2 page: http://{a.host}:{a.port}/alex-v2")
        uvicorn.run(create_app(), host=a.host, port=a.port)
    elif a.cmd == "lab":
        from .lab import run_lab
        conv = open(a.file, encoding="utf-8").read()
        res = asyncio.run(run_lab(conv, platform=a.platform, her_tz=a.tz, call_model=not a.no_model))
        d = res["decision"]
        print(json.dumps({k: d[k] for k in ("action", "text", "suggested_text", "move_id", "reason", "handoff_reason", "due_at", "follow_up_at")},
                         indent=2, ensure_ascii=False))
        print(json.dumps(d["state"], indent=2, ensure_ascii=False))
        if a.no_model:
            print(d["debug"].get("system", ""))
            print("-----")
            print(d["debug"].get("user", ""))
        else:
            for c in d["debug"].get("candidates") or []:
                print(("OK  " if c["ok"] else "BAD ") + f"[{c['move']}] {c['text']}" + ("" if c["ok"] else f"  <- {'; '.join(c['fails'])}"))
    elif a.cmd == "due":
        from .engine import due_threads
        for t in due_threads():
            print(json.dumps(t))
    return 0


if __name__ == "__main__":
    sys.exit(main())
