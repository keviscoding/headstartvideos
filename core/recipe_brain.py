"""
Recipe Brain — knowledge pack + gated chat over starter docs.

Full RAG over channel corpus comes later. This module loads
webapp/knowledge/, exposes the starter pack, and (when enabled)
answers chat from retrieved markdown chunks via Atlas cheap text.
"""

from __future__ import annotations

import re
from pathlib import Path

import config

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "webapp" / "knowledge"
STARTER_DOC = "20_mistakes.md"


def knowledge_enabled() -> bool:
    return bool(getattr(config, "RECIPE_BRAIN_ENABLED", False))


def _read_doc(name: str) -> str:
    path = KNOWLEDGE_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"Knowledge doc missing: {name}")
    return path.read_text(encoding="utf-8")


def starter_pack() -> dict:
    """Return the curated 20-mistakes starter list (always available)."""
    raw = _read_doc(STARTER_DOC)
    mistakes: list[str] = []
    for line in raw.splitlines():
        m = re.match(r"^\d+\.\s+\*\*(.+?)\*\*\s*[—–-]\s*(.+)$", line.strip())
        if m:
            mistakes.append(f"{m.group(1)} — {m.group(2).strip()}")
            continue
        m2 = re.match(r"^\d+\.\s+(.+)$", line.strip())
        if m2:
            mistakes.append(m2.group(1).strip())
    if len(mistakes) < 10:
        # Fallback: non-empty bullet-ish lines
        for line in raw.splitlines():
            s = line.strip()
            if re.match(r"^\d+\.", s):
                mistakes.append(re.sub(r"^\d+\.\s*", "", s))
    return {
        "id": "20_mistakes",
        "title": "20 mistakes not to make when starting YouTube automation",
        "mistakes": mistakes[:20],
        "count": len(mistakes[:20]),
        "chat_enabled": knowledge_enabled(),
    }


def _chunk_docs(max_chars: int = 900) -> list[dict]:
    chunks: list[dict] = []
    if not KNOWLEDGE_DIR.is_dir():
        return chunks
    for path in sorted(KNOWLEDGE_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        buf = ""
        for p in paras:
            if len(buf) + len(p) + 2 > max_chars and buf:
                chunks.append({"source": path.name, "text": buf.strip()})
                buf = p
            else:
                buf = f"{buf}\n\n{p}" if buf else p
        if buf.strip():
            chunks.append({"source": path.name, "text": buf.strip()})
    return chunks


def _retrieve(query: str, k: int = 4) -> list[dict]:
    q_terms = {t.lower() for t in re.findall(r"[a-zA-Z0-9']{3,}", query or "")}
    scored: list[tuple[int, dict]] = []
    for ch in _chunk_docs():
        body = ch["text"].lower()
        score = sum(1 for t in q_terms if t in body) if q_terms else 0
        # Always keep starter pack chunks lightly boosted
        if ch["source"] == STARTER_DOC:
            score += 1
        scored.append((score, ch))
    scored.sort(key=lambda x: (-x[0], x[1]["source"]))
    top = [c for s, c in scored if s > 0][:k]
    if not top:
        top = [c for _, c in scored[:k]]
    return top


def chat(messages: list[dict]) -> dict:
    """
    Answer from retrieved knowledge chunks via Atlas.

    messages: [{role: user|assistant, content: str}, ...]
    Requires RECIPE_BRAIN_ENABLED.
    """
    if not knowledge_enabled():
        raise RuntimeError("Recipe Brain chat is not enabled yet")

    from core.atlas_llm import generate_text, has_atlas

    if not has_atlas():
        raise RuntimeError("ATLASCLOUD_KEY required for Recipe Brain chat")

    user_turns = [m for m in messages if (m.get("role") or "") == "user" and m.get("content")]
    if not user_turns:
        raise ValueError("Send at least one user message")
    latest = str(user_turns[-1]["content"]).strip()
    retrieved = _retrieve(latest)
    context = "\n\n---\n\n".join(
        f"[{c['source']}]\n{c['text']}" for c in retrieved
    ) or "(no knowledge loaded)"

    history_lines = []
    for m in messages[-8:]:
        role = (m.get("role") or "user").strip()
        content = str(m.get("content") or "").strip()
        if content:
            history_lines.append(f"{role.upper()}: {content}")
    history = "\n".join(history_lines)

    system = (
        "You are Recipe Brain, ChannelRecipe's YouTube-automation advisor. "
        "Answer ONLY using the provided knowledge excerpts when they apply. "
        "Be direct, practical, and specific. If the knowledge does not cover "
        "the question, say what is missing and give cautious general guidance. "
        "Do not invent proprietary channel metrics you were not given."
    )
    prompt = (
        f"KNOWLEDGE EXCERPTS:\n{context}\n\n"
        f"CONVERSATION:\n{history}\n\n"
        "Reply as Recipe Brain to the latest user message."
    )
    answer = generate_text(
        prompt,
        max_tokens=1024,
        temperature=0.4,
        system=system,
    ).strip()
    return {
        "reply": answer,
        "sources": [{"id": c["source"]} for c in retrieved],
    }
