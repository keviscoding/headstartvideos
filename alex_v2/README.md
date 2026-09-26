# Alex v2

A rebuilt engine for the hands-off Alex texting autopilot. It sits behind its **own toggle** and its **own page** (`/alex-v2`), so nothing on the main dashboard changes. **OFF means your current Alex texting runs exactly as before.**

It's built for the fast, cheap Live model:
- **One model call per reply.** It returns both its reading of her message and 4 candidate texts.
- **One extra "repair" call**, only when every candidate fails the checks.
- **Timed follow-ups use no model call.** Double texts, day-of confirms and the takeaway ladder use Alex's exact lines, so they're free and instant.

## Why v2 fixes the whack-a-mole

The old setup sent one long playbook and a prose summary to the model, then hoped it chose the right move. Tightening the prompt made it robotic; loosening it made it watered down and let lines fire in the wrong situation ("Still good for Wednesday?" to a girl with no date; "If you're too nervous" to a receptive girl).

v2 splits the job so the model only does the part it's good at:

| Step | Who does it | What it guarantees |
|---|---|---|
| **State** | Code, from *this chat only* | The date state (NONE → INTEREST → SCHEDULING → PROPOSED → AGREED → CONFIRMED → MET, plus RESCHEDULING/FLAKED) is computed from messages with evidence and absolute times. "Our date" teases never create a date. Old plans expire. No summaries, no cross-chat memory. |
| **Policy** | Code | Only the moves whose conditions hold right now are offered. For example: takeaways only after real silence or flakes, "breaking my heart" only if a plan existed, and sexual moves only on her signals. |
| **Writing** | The model (one call) | Writes in modern-Alex voice from the offered moves, with Alex's example lines for each. |
| **Checks** | Code | Every candidate is checked before it can go out. It must not claim a date that doesn't exist, name a day that is wrong or stale, or use Alex's biography or any fact not in your fact sheet. It must not be needy, apologetic, assistant-like, an emoji pile-up, too long, over the sexual ceiling, or a repeat. The model's reading of her message can only **restrict** what's allowed. |
| **Timing** | Code | Human reply delays (fast only for same-day logistics); double text at 48–72h, triple at ~a week, sweeps, then walk away; night-before reminder; day-of warm-up and confirm; quiet hours in her timezone. |

Reply delays are off by default, so v2 replies straight away like today. Turn them on once the autopilot polls `due_threads()` (see INTEGRATION.md, step 6).

Nothing the model says can make a line legal that the state doesn't allow. That's what stops the pendulum: no line can fire in the wrong situation, so the prompt no longer needs to be over-restrictive.

## The toggle

Open **`/alex-v2`** (or `python -m alex_v2 status`).

| Mode | What happens |
|---|---|
| **OFF** (default) | v1 handles everything. v2 does nothing. |
| **ON** | v2 handles every Alex chat. |
| **A/B** | Each chat is **permanently** v1 or v2 (a stable hash split, e.g. 50%). Both arms are measured the same way, and results appear on the page. |
| **SHADOW** | v1 sends. v2 logs what it *would* have sent, so you can compare side by side at no risk. |

CLI: `python -m alex_v2 on | off | ab 50 | shadow | status`.

Environment overrides: `ALEX_V2_MODE`, `ALEX_V2_AB_SPLIT`, `ALEX_V2_MODEL`, `ALEX_V2_DATA_DIR`. They take precedence, and the page shows them as "locked".

v2 keeps its settings in `<data dir>/settings.json`. **It never reads or writes `autopilot/runtime_flags.json`.**

## The page (`/alex-v2`)

- **Who texts?** One click to switch OFF / ON / A/B / SHADOW, plus the A/B split slider. It also shows which model is in use and any recent model errors.
- **Needs you:** chats v2 deliberately stepped back from. These include serious news, address/call/photo requests, a cancel it wasn't sure about, or a failure. Each shows a suggested reply. Nobody texts those chats until the chat moves or you click "Handled it".
- **Results: v1 vs v2:**
  - Replies within 72h.
  - Share of chats that reached "yes to meeting", "date agreed" and "date happened".
  - Flakes.
- **What v2 did:** every decision. Click one to see her message and the date state with the exact messages it's based on, the moves offered, and each candidate with the check that rejected it.
- **Try it:** paste any chat and see what v2 would do (with the model, or prompt-only). Nothing is sent.
- **Your facts:** the only facts v2 may state about you. Empty means unknown, which means it is never mentioned.
- **Advanced settings:** model, candidates, timing, quiet hours, sexual ceiling, handoffs.

**Fill in "Your facts" first.** v2 won't invent a pet, a balcony, a job or a height. Any line that needs a fact you haven't given is rejected.

## Files

```
alex_v2/
  engine.py        decide(): one decision per chat (send / wait / handoff / none)
  integration.py   maybe_handle(): the autopilot hook, respects the toggle
  state.py         the date state machine + thread state (evidence-based)
  moves.py         Alex's moves, each with the conditions that license it
  policy.py        guardrails -> timers -> legal moves
  prompt.py        the single, compact model prompt + tolerant parser
  llm.py           Gemini adapter (Live API for "live" models, fallback model)
  critic.py        deterministic checks on every candidate
  templates.py     canon follow-up lines (no model call)
  timing.py        reply delays, follow-up timers, quiet hours
  normalize.py     scraper messages -> clean messages with absolute times
  timeparse.py     "Thursday", "tmrw", "Yesterday 6:12 PM" -> real datetimes
  facts.py         your fact sheet
  settings.py      the toggle (own file, env overrides, A/B hashing)
  store.py         small per-chat memory (times, what v2 sent, timers). No prose summaries
  trace.py         decision log + A/B outcomes
  dashboard.py     the /alex-v2 page (FastAPI router) + page.html
  lab.py           "Try it"
  tests/           regression suite (python -m pytest alex_v2/tests)
```

Data is stored under `ALEX_V2_DATA_DIR` (default `alex_v2/data/`):
- `settings.json` and `facts.json`
- `threads/<namespace>/*.json`
- `logs/decisions.jsonl` and `logs/outcomes.json`

Put it outside the release folder on the server so deploys don't wipe it.

## Tests

```
python -m pytest alex_v2/tests
```

The suite covers:
- **The date bugs:**
  - A stale "Wednesday" plan is never confirmed.
  - An annoying opener is not a date.
  - "Our date" is a tease, not a plan.
  - Two girls with the same name don't share state.
- **Wrong-situation lines:**
  - "Too nervous" on a receptive girl.
  - "Breaking my heart" or "flaky" with no plan.
  - A takeaway after a sincere cancel.
- **Fabrication:** Alex's husky, a balcony, "I live alone" when you have roommates.
- **Calibration:**
  - Yellow light.
  - ESL / Latin (no trolls).
  - Instagram cold DM.
  - Vanilla profile.
  - Pick one thread.
  - "tomorrow" after midnight.
- **Guardrails:** minor, stop, scam, serious news.
- **Timing:**
  - No 10-minute double text.
  - Canon double and triple texts.
  - The day-of warm-up → confirm → "too nervous" protocol.
  - Scheduled replies never double-send and are rewritten if stale.
- **Robustness:** model errors, garbage output, the Live → fallback path.
- **The toggle, the page and A/B assignment.**

All of these run offline, with a scripted model.
