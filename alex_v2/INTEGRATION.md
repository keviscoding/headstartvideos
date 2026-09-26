# Wiring Alex v2 into WINGMAN OG

About 20 lines in two files. With the toggle OFF (the default), your system behaves exactly as today.

> **Never touch `autopilot/runtime_flags.json`.** v2 has its own settings file (`<ALEX_V2_DATA_DIR>/settings.json`) and its own page.

## 1. Copy the package

Copy the whole `alex_v2/` folder into the root of WINGMAN OG, next to `wingman/` and `autopilot/`. It needs `fastapi` (already used by your apps) and `google-genai` (already used). Nothing else.

On the server, point the data folder at a persistent location outside the release folder (e.g. a `shared/alex_v2` folder next to your releases), so deploys don't wipe the toggle, facts or logs. In the systemd units for both the autopilot service and the control/dashboard service:

```
Environment=ALEX_V2_DATA_DIR=<persistent path>/alex_v2
```

Both services must use the **same** folder: the page writes the toggle, and the autopilot reads it.

## 2. Add the page to the control app (1 import + 1 line)

In `control_app.py` (the FastAPI app behind the dashboard):

```python
from alex_v2.dashboard import router as alex_v2_router
app.include_router(alex_v2_router)          # -> /alex-v2
```

Add **one** small link on the main dashboard: `<a href="/alex-v2">Alex v2</a>`. That's the only change to the dashboard.

If the control app is reachable from the internet and has no auth of its own, set `ALEX_V2_TOKEN=<something long>` and open `/alex-v2?token=<something long>` once. A cookie remembers it.

## 3. Hook the autopilot (where Alex texting generates a reply)

In `autopilot/alex_texting.py`, find where the Alex reply for a chat is generated. Put this **before** the existing generation:

```python
from alex_v2.integration import maybe_handle_async, thread_id_for
from alex_v2 import mark_sent

# a stable, unique id per girl. Use the platform's match/chat id if you have one.
# NEVER the display name alone (two "Sofia"s would share memory).
tid = thread_id_for(platform, contact_name, messages, match_id=match_id_or_empty)

h = await maybe_handle_async(
    tid, messages,                 # wingman Message objects (speaker "me"/"them", text, time_label) are fine
    contact_name=contact_name,
    platform=platform,             # "hinge", "tinder", "bumble", "instagram", "whatsapp", ...
    her_profile=profile_text_or_empty,
    her_tz=None,                   # e.g. "America/New_York" if you know it; default = your timezone
)
if h.handled:                      # v2 owns this chat. v1 must not send.
    d = h.decision
    if d.should_send:
        await send_text(d.text)    # your existing send function
        mark_sent(tid, d.text)     # exact send time keeps every timer right
    # d.action == "wait": nothing to send yet. Call again at d.due_at / d.follow_up_at.
    # d.action == "handoff": shown under "Needs you" on /alex-v2. Don't send.
    # d.action == "none": nothing to do (closed / paused / she said stop).
    return
# ... existing Alex texting (v1) continues unchanged ...
```

If that code is synchronous, use `maybe_handle(...)` (same arguments).

If you have your own Live-model wrapper you'd rather use, pass it as `llm=`. Any `fn(system, user, **opts) -> str` works, sync or async. Otherwise v2 uses:
- `wingman.config.make_genai_client()` with the model from the page;
- if that is blank, `ALEX_V2_MODEL`, then `WINGMAN_LIVE_MODEL`.

## 4. Let v2 act on time (scheduled replies and follow-ups)

v2 doesn't reply instantly, and it sends double texts, reminders and day-of confirms when she hasn't written. To make that happen, the autopilot must look at chats v2 says are due. Once a minute:

```python
from alex_v2 import due_threads

for t in due_threads():            # [{"thread_id", "contact", "platform", "due", "pending"}]
    ...open that chat, read its messages, then run the same hook as in step 3...
```

If your autopilot already re-reads every chat every few minutes, that is enough. The hook returns `wait` until the reply's time has come, then `send`. It never double-sends, and it rewrites a scheduled reply if she writes again before it goes out.

If you want replies immediately instead, turn off **Human-like reply delays** in Advanced settings.

## 5. Roll out

1. **Fill in "Your facts"** on `/alex-v2`. This is the most important step.
2. **SHADOW for a day.** v1 keeps sending. Read "What v2 did" and "Try it", and paste the chats that went wrong before.
3. **A/B at 50%.** Watch "Results: v1 vs v2" (reply rate, % agreed, % met, flakes).
4. **ON** when v2 is winning. **OFF** is always one click away and restores v1 exactly.

## 6. Ready-to-paste Cursor prompt

```
In this repo there is a new self-contained package `alex_v2/` (read alex_v2/README.md and alex_v2/INTEGRATION.md first).
Wire it in without changing any existing behaviour when its mode is "off":
1. In control_app.py: `from alex_v2.dashboard import router as alex_v2_router` and `app.include_router(alex_v2_router)`.
   Add one small "Alex v2" link to the main dashboard nav pointing to /alex-v2. Change nothing else on the dashboard.
2. In autopilot/alex_texting.py, at the point where the Alex reply for a chat is generated, call
   `alex_v2.integration.maybe_handle_async(thread_id_for(platform, contact_name, messages, match_id=...), messages, contact_name=..., platform=..., her_profile=...)`
   before the existing generation. If `.handled` is True: send `decision.text` only when `decision.should_send`, then call
   `alex_v2.mark_sent(thread_id, text)`, and skip the existing v1 generation for that chat entirely (also for wait/handoff/none).
   If `.handled` is False, run the existing code unchanged.
3. In the autopilot loop, once a minute, iterate `alex_v2.due_threads()` and run the same per-chat flow for those chats.
4. Do NOT read, write or modify autopilot/runtime_flags.json. v2 has its own settings file.
5. Set ALEX_V2_DATA_DIR to a persistent folder shared by both services (outside the release directory).
6. Run `python -m pytest alex_v2/tests` and make sure everything passes.
Show me the diff before applying.
```

## What each decision means

| `decision.action` | Meaning | What the autopilot does |
|---|---|---|
| `send` | Send `decision.text` now | Send, then `mark_sent(tid, text)` |
| `wait` | Not yet (a reply is scheduled for `due_at`, or the next timer is `follow_up_at`) | Nothing. Look again later (`due_threads()`) |
| `handoff` | A human should reply (`handoff_reason`, `suggested_text`) | Nothing. It's listed under "Needs you" |
| `none` | Nothing to do (closed, paused, walked away) | Nothing |

Every decision is logged with its reasons in `<data dir>/logs/decisions.jsonl` and shown on the page.
