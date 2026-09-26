"""A scripted stand-in for the model, so the whole engine runs offline."""

from __future__ import annotations


def out(cands, **a) -> str:
    base = dict(type="statement", light="unknown", temp="positive", culture="default", esl="0", asked_me="no",
                flags="none", date="none", thread="her message", skip="no")
    base.update(a)
    lines = [f"{k}: {v}" for k, v in base.items()]
    lines += [f"C: {m} | {t}" for m, t in cands]
    return "\n".join(lines)


class FakeLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []
        self.last_model = "fake"

    def __call__(self, system, user, **kw):
        self.calls.append((system, user))
        if not self.responses:
            raise AssertionError("FakeLLM called more times than scripted")
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    @property
    def n(self) -> int:
        return len(self.calls)

    @property
    def last_user(self) -> str:
        return self.calls[-1][1] if self.calls else ""
