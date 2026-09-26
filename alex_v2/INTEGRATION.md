# Wiring Alex v2 into WINGMAN OG

About 20 lines in the files that already exist. Nothing new to deploy separately: v2 is a folder your autopilot imports, and its page is mounted inside the dashboard you already have. With the toggle OFF (the default), everything behaves exactly as today.

> **Never touch `runtime_flags.json`** (locally or `/opt/wingman-edge/current/autopilot/runtime_flags.json`). v2 keeps its own settings in `<ALEX_V2_DATA_DIR>/settings.json`.

Where things live in WINGMAN OG:

| What | File |
|---|---|
| The only path that writes the text when Alex is on | `autopilot/main.py` → `generate_reply` |
| The Live 3.8 call | `wingman/live_reply.py` |
| The existing dashboard | `autopilot/control_app.py` + `autopilot/control_static/` |
| Lab transcripts | `autopilot/runtime_flags.py` → `apply_testing_transcript`, `sync_testing_run_from_glass` |

## 1. Copy the folder

Put `alex_v2/` in the root of WINGMAN OG, next to `autopilot/` and `wingman/`. It needs `fastapi` and `google-genai`, which you already use.

On the server, set `ALEX_V2_DATA_DIR` to a folder **outside** `/opt/wingman-edge/current` so deploys don't wipe the toggle, facts or logs. Use the same value for `wingman-edge` and `wingman-control`: the page writes the toggle and the autopilot reads it.

## 2. Use your Live 3.8 call

Read `wingman/live_reply.py` and wrap its Live call as a function v2 can call:

```python
def alex_v2_llm(system: str, user: str, **opts) -> str:
    # send `system` as the system instruction and `user` as the user turn to the SAME
    # Live model/session setup live_reply.py uses; return the model's text reply.
    ...
```

It can be sync or async. Pass it as `llm=alex_v2_llm` in step 3. Then v2 runs on exactly the Live path you already trust. (Without it, v2 opens its own Gemini client using `wingman.config`.)

## 3. Hook `generate_reply` in `autopilot/main.py`

At the top of the branch where Alex writes the reply:

```python
from alex_v2.integration import maybe_handle, thread_id_for   # or maybe_handle_async if generate_reply is async

tid = thread_id_for(platform, contact_name, messages, match_id=match_id_or_empty)
#   ^ must be unique per girl AND per lab run. Never the display name alone.

h = maybe_handle(tid, messages, contact_name=contact_name, platform=platform,
                 her_profile=profile_text_or_empty, llm=alex_v2_llm)
if h.handled:                           # v2 owns this chat: the existing Alex path must not run
    d = h.decision
    return d.text if d.should_send else None   # adapt to however generate_reply says "send this" / "send nothing"
# ... existing Alex path, unchanged ...
```

- `messages` can be the transcript's `Message` objects as they are (speaker `"me"`/`"them"`, `text`, `time_label`).
- v2 recognises its own message when it shows up in the chat. Calling `alex_v2.mark_sent(tid, text)` after sending is optional; it just records the exact send time.
- Handoffs (serious news, address/call/photo requests, a cancel it wasn't sure about) return nothing to send and appear under **Needs you** on the page.

## 4. Add the toggle page to the existing dashboard

In `autopilot/control_app.py`:

```python
from alex_v2.dashboard import router as alex_v2_router
app.include_router(alex_v2_router)      # -> /alex-v2
```

Add **one** link to the nav in `autopilot/control_static/`: `<a href="/alex-v2">Alex v2</a>`. Change nothing else on the dashboard.

## 5. The lab: one fresh chat per drill

v2 only knows what's in the messages it's given. If `apply_testing_transcript` / `sync_testing_run_from_glass` still carry the old WhatsApp thread (with Wednesday's plan) into a new drill, v2 sees it too. It treats a plan that has already passed as expired, but a new lab girl should still start clean. Make a new lab girl or run use:
- only the current drill's messages;
- a new id (so `thread_id_for` gives her a fresh v2 memory).

## 6. Timing (later)

- **Replies.** By default v2 replies straight away, like today, so step 3 is all it needs. v2's own human-like delays are off by default; turn them on in Advanced settings only after step 6's polling is in.
- **Follow-ups.** For v2 to send the double and triple texts, the night-before reminder and the day-of confirm, have the autopilot check once a minute:

  ```python
  from alex_v2 import due_threads
  for t in due_threads():                 # [{"thread_id", "contact", "platform", "due", "pending"}]
      ...open that chat and run step 3 for it...
  ```

## 7. Turn it on

1. Open `/alex-v2` and fill in **Your facts** (v2 never invents facts about you).
2. **Shadow** for a day: the current Alex sends, v2 logs what it would have sent.
3. **A/B 50%**, then **ON**. OFF is one click and restores the current Alex exactly.

CLI: `python -m alex_v2 status | on | off | ab 50 | shadow`. Tests: `python -m pytest alex_v2/tests`.

## Ready-to-paste prompt (for Claude on your Mac, in the WINGMAN OG folder)

```
Work only in this folder. alex_v2/ is a finished, tested package. Don't rewrite it and don't create any other package, branch or toggle.
Read alex_v2/INTEGRATION.md first, then wire it in:
1. Read wingman/live_reply.py and write alex_v2_llm(system, user, **opts) -> str that makes the same Live call
   with `system` as the system instruction and `user` as the user turn.
2. In autopilot/main.py generate_reply, at the top of the Alex branch, call alex_v2.integration.maybe_handle
   (or maybe_handle_async) exactly as INTEGRATION.md step 3 shows, with llm=alex_v2_llm and a thread id unique per
   girl and per lab run. If .handled, return v2's text only when decision.should_send, otherwise send nothing,
   and skip the existing Alex path. If not handled, the existing code runs unchanged.
3. In autopilot/control_app.py include alex_v2.dashboard.router, and add one "Alex v2" link to the nav in
   autopilot/control_static/. Change nothing else on the dashboard.
4. In autopilot/runtime_flags.py (apply_testing_transcript / sync_testing_run_from_glass): a new lab girl or run
   must start with only the current drill's messages and a new id, never the old WhatsApp thread.
5. Never read or write any runtime_flags.json.
6. Run python -m pytest alex_v2/tests. Then, with `python -m alex_v2 shadow`, replay a fresh lab girl who sends
   only "Annoying opener": v2 must not mention Wednesday, a time or Banyan.
Show me the diff before deploying.
```
