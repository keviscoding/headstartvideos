"""Model adapter: one fast, cheap call per decision.

Any callable works as the model:  ``llm(system, user, **opts) -> str``
(sync or async). Pass your own to ``engine.decide(..., llm=...)`` if you
already have a Live/Gemini wrapper you trust.

The default adapter uses Gemini through ``google-genai``:
- a model id containing "live" goes through the Live API (text out),
- anything else through ``generate_content``,
- thinking is off by default (speed), safety filters are permissive (adult
  dating banter otherwise comes back empty),
- a 429 rotates the key (wingman's ``rotate_api_key``) and retries once,
- if the Live model refuses text output, v2 falls back to the fallback
  model (and says so on the Alex v2 page) instead of failing.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import time
from typing import Any, Callable, Optional

_LIVE_TEXT_BROKEN: dict[str, str] = {}   # model -> error (so we don't retry a broken path every call)
LAST_ERRORS: list[dict[str, Any]] = []   # small ring buffer shown on the Alex v2 page


def _note_error(model: str, err: str) -> None:
    LAST_ERRORS.append({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "model": model, "error": err[:300]})
    del LAST_ERRORS[:-20]


def _wingman_config():
    try:
        from wingman import config as wc  # type: ignore
        return wc
    except Exception:
        return None


def resolve_model(settings: dict[str, Any]) -> tuple[str, str]:
    """(primary, fallback) model ids.

    primary: Alex v2 page setting > ALEX_V2_MODEL (already merged into settings)
             > wingman LIVE_MODEL > wingman QUICK_MODEL > FLASH_MODEL.
    fallback: settings["fallback_model"] > wingman QUICK_MODEL > FLASH_MODEL.
    """
    wc = _wingman_config()
    primary = (settings.get("model") or "").strip()
    if not primary and wc is not None:
        primary = getattr(wc, "LIVE_MODEL", "") or getattr(wc, "QUICK_MODEL", "") or getattr(wc, "FLASH_MODEL", "")
    if not primary:
        primary = os.getenv("WINGMAN_LIVE_MODEL") or os.getenv("WINGMAN_QUICK_MODEL") or "gemini-3.7-flash"
    fallback = (settings.get("fallback_model") or "").strip()
    if not fallback and wc is not None:
        fallback = getattr(wc, "QUICK_MODEL", "") or getattr(wc, "FLASH_MODEL", "")
    if not fallback:
        fallback = os.getenv("WINGMAN_QUICK_MODEL") or "gemini-3.7-flash"
    if fallback == primary:
        fallback = ""
    return primary, fallback


def _client():
    wc = _wingman_config()
    if wc is not None and hasattr(wc, "make_genai_client"):
        return wc.make_genai_client()
    from google import genai
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or (os.getenv("GEMINI_API_KEYS") or "").split(",")[0].strip()
    return genai.Client(api_key=key) if key else genai.Client()


def _rotate() -> None:
    wc = _wingman_config()
    if wc is not None and hasattr(wc, "rotate_api_key"):
        try:
            wc.rotate_api_key()
        except Exception:
            pass


def _safety():
    wc = _wingman_config()
    if wc is not None and hasattr(wc, "permissive_safety_settings"):
        try:
            return wc.permissive_safety_settings()
        except Exception:
            pass
    try:
        from google.genai import types
        cats = ["HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH", "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT"]
        return [types.SafetySetting(category=c, threshold="BLOCK_NONE") for c in cats]
    except Exception:
        return None


def _is_rate_limit(exc: Exception) -> bool:
    t = str(exc).lower()
    return "429" in t or "resource_exhausted" in t or "rate limit" in t or "quota" in t


def _thinking_rejected(exc: Exception) -> bool:
    t = str(exc).lower()
    return "thinking" in t and ("budget" in t or "not supported" in t or "invalid" in t)


class GeminiLLM:
    """Default adapter. ``last_model`` / ``last_path`` say what actually answered."""

    def __init__(self, settings: dict[str, Any]):
        self.settings = settings
        self.model, self.fallback = resolve_model(settings)
        self.last_model = ""
        self.last_path = ""

    async def __call__(self, system: str, user: str, *, temperature: float = 0.7, max_output_tokens: int = 1200,
                       thinking_budget: Optional[int] = 0, timeout_s: float = 25) -> str:
        models = [self.model] + ([self.fallback] if self.fallback else [])
        last_exc: Optional[Exception] = None
        for i, model in enumerate(models):
            try:
                txt = await asyncio.wait_for(
                    self._one(model, system, user, temperature, max_output_tokens, thinking_budget),
                    timeout=timeout_s,
                )
                if txt.strip():
                    self.last_model = model
                    return txt
                last_exc = RuntimeError("empty response")
                _note_error(model, "empty response")
            except Exception as exc:  # try the fallback model
                last_exc = exc
                _note_error(model, f"{type(exc).__name__}: {exc}")
        raise RuntimeError(f"all models failed: {last_exc}")

    async def _one(self, model: str, system: str, user: str, temperature: float, max_tokens: int,
                   thinking_budget: Optional[int]) -> str:
        use_live = "live" in model.lower()
        if use_live and model in _LIVE_TEXT_BROKEN:
            raise RuntimeError(f"live text path disabled for {model}: {_LIVE_TEXT_BROKEN[model]}")
        for attempt in range(2):
            try:
                if use_live:
                    self.last_path = "live"
                    return await self._live(model, system, user, temperature, max_tokens, thinking_budget)
                self.last_path = "generate"
                return await self._generate(model, system, user, temperature, max_tokens, thinking_budget)
            except Exception as exc:
                if thinking_budget is not None and _thinking_rejected(exc):
                    thinking_budget = None
                    continue
                if _is_rate_limit(exc) and attempt == 0:
                    _rotate()
                    continue
                if use_live and ("modality" in str(exc).lower() or "response_modalities" in str(exc).lower()):
                    _LIVE_TEXT_BROKEN[model] = str(exc)[:200]
                raise
        raise RuntimeError("model call failed")

    async def _generate(self, model, system, user, temperature, max_tokens, thinking_budget) -> str:
        from google.genai import types
        cfg: dict[str, Any] = dict(system_instruction=system, temperature=temperature, max_output_tokens=max_tokens)
        if thinking_budget is not None:
            cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=int(thinking_budget))
        safety = _safety()
        if safety:
            cfg["safety_settings"] = safety
        client = _client()
        resp = await client.aio.models.generate_content(model=model, contents=user, config=types.GenerateContentConfig(**cfg))
        return _text_of(resp)

    async def _live(self, model, system, user, temperature, max_tokens, thinking_budget) -> str:
        from google.genai import types
        cfg: dict[str, Any] = dict(
            response_modalities=[types.Modality.TEXT],
            system_instruction=types.Content(parts=[types.Part(text=system)]),
            temperature=temperature,
            max_output_tokens=max_tokens,
        )
        if thinking_budget is not None:
            cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=int(thinking_budget))
        safety = _safety()
        if safety:
            cfg["safety_settings"] = safety
        client = _client()
        chunks: list[str] = []
        async with client.aio.live.connect(model=model, config=types.LiveConnectConfig(**cfg)) as session:
            await session.send_client_content(
                turns=types.Content(role="user", parts=[types.Part(text=user)]), turn_complete=True
            )
            async for msg in session.receive():
                sc = getattr(msg, "server_content", None)
                if sc is not None:
                    mt = getattr(sc, "model_turn", None)
                    for part in (getattr(mt, "parts", None) or []):
                        if getattr(part, "text", None) and not getattr(part, "thought", False):
                            chunks.append(part.text)
                    if getattr(sc, "turn_complete", False) or getattr(sc, "generation_complete", False):
                        break
        return "".join(chunks)


def _text_of(resp: Any) -> str:
    try:
        t = resp.text
        if t:
            return t
    except Exception:
        pass
    out = []
    for cand in getattr(resp, "candidates", None) or []:
        content = getattr(cand, "content", None)
        for part in (getattr(content, "parts", None) or []):
            if getattr(part, "text", None) and not getattr(part, "thought", False):
                out.append(part.text)
    return "".join(out)


def make_llm(settings: dict[str, Any]) -> GeminiLLM:
    return GeminiLLM(settings)


async def call(llm: Callable[..., Any], system: str, user: str, settings: dict[str, Any]) -> str:
    """Call any llm callable (sync or async) with v2's settings and a hard timeout."""
    opts = dict(
        temperature=float(settings.get("temperature", 0.7)),
        max_output_tokens=int(settings.get("max_output_tokens", 1200)),
        thinking_budget=settings.get("thinking_budget", 0),
        timeout_s=float(settings.get("timeout_s", 25)),
    )
    try:
        sig = inspect.signature(llm)
        if not any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values()):
            opts = {k: v for k, v in opts.items() if k in sig.parameters}
    except (TypeError, ValueError):
        pass
    timeout = float(settings.get("timeout_s", 25)) + 5
    if inspect.iscoroutinefunction(llm) or inspect.iscoroutinefunction(getattr(llm, "__call__", None)):
        res = await asyncio.wait_for(llm(system, user, **opts), timeout=timeout)
    else:
        res = await asyncio.wait_for(asyncio.to_thread(llm, system, user, **opts), timeout=timeout)
        if inspect.isawaitable(res):
            res = await asyncio.wait_for(res, timeout=timeout)
    return str(res or "")
