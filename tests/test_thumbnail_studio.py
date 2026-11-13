"""Thumbnail Studio: a request always answers before Cloudflare's 100 s, and the page never shows a raw HTML error.

A Creator customer pressed Generate Thumbnails and got "Thumbnail generation failed: Unexpected token '<', <!DOCTYPE
... is not valid JSON" (2026-11-06, again 2026-11-13): the options were made one after another, each polling Atlas up
to 120 s through 3-4 fallback models, so Cloudflare cut the request and sent its HTML page, which the page parsed as
JSON. Atlas out of credit (2026-11-13) has to say so, at once."""
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import thumbnail_gen as tg  # noqa: E402


class _Resp:
    def __init__(self, status=200, body=None, content=b""):
        self.status_code, self._body, self.content = status, body or {}, content
        self.text = json.dumps(self._body)

    def json(self):
        return self._body

    def raise_for_status(self):
        pass


class _Atlas:
    """A stand-in Atlas: each prediction takes `seconds` (forever when None); `balance` False answers 402."""

    def __init__(self, seconds=0.5, balance=True):
        self.seconds, self.balance = seconds, balance
        self.started, self.lock, self.models = {}, threading.Lock(), []

    def post(self, url, **kw):
        if not self.balance:
            return _Resp(402, {"code": 402, "msg": "insufficient balance"})
        if url.endswith("/uploadMedia"):
            return _Resp(200, {"data": {"download_url": "https://atlas.example/ref.png"}})
        with self.lock:
            pid = f"p{len(self.started)}"
            self.started[pid] = time.monotonic()
            self.models.append(kw["json"]["model"])
        return _Resp(200, {"data": {"id": pid}})

    def get(self, url, **kw):
        if "/prediction/" not in url:
            return _Resp(200, content=b"png")
        pid = url.rsplit("/", 1)[-1]
        done = self.seconds is not None and time.monotonic() - self.started[pid] >= self.seconds
        return _Resp(200, {"data": {"status": "succeeded" if done else "processing",
                                    "outputs": [f"https://atlas.example/{pid}.png"]}})


@pytest.fixture
def atlas(monkeypatch):
    import httpx

    def make(**kw):
        fake = _Atlas(**kw)
        monkeypatch.setattr(httpx, "post", fake.post)
        monkeypatch.setattr(httpx, "get", fake.get)
        return fake
    monkeypatch.setattr(tg, "ATLASCLOUD_KEY", "test-key")
    monkeypatch.setattr(tg, "POLL_S", 0.05)
    return make


def _ref(tmp_path):
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"x" * 600)
    return [str(ref)]


def test_four_options_are_made_at_the_same_time_each_in_its_own_file(atlas, tmp_path):
    atlas(seconds=0.6)
    t0 = time.monotonic()
    paths = tg.generate_thumbnails("GTA 6 map leak", _ref(tmp_path), "neon", num_images=4, output_dir=str(tmp_path / "out"))
    assert time.monotonic() - t0 < 2.0, "one after another took 4 x the time"
    assert [Path(p).name for p in paths] == ["thumb_01.png", "thumb_02.png", "thumb_03.png", "thumb_04.png"]
    t0 = time.monotonic()
    paths = tg.generate_thumbnail_no_refs("GTA 6 map leak", "neon", output_dir=str(tmp_path / "t2i"), count=3)
    assert time.monotonic() - t0 < 2.0 and len(set(paths)) == 3


def test_a_provider_that_never_finishes_answers_within_the_budget_with_plain_words(atlas, tmp_path, monkeypatch):
    fake = atlas(seconds=None)
    monkeypatch.setattr(tg, "MIN_TRY_S", 0.5)
    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match="took too long"):
        tg.generate_thumbnails("t", _ref(tmp_path), num_images=2, output_dir=str(tmp_path / "out"), budget_s=2.0)
    took = time.monotonic() - t0
    assert took < 3.0, f"{took:.1f} s: the budget must hold whatever Atlas does"
    assert fake.models, "it did try"
    with pytest.raises(RuntimeError, match="took too long"):
        tg.generate_thumbnail_no_refs("t", output_dir=str(tmp_path / "t2i"), count=1, budget_s=1.5)


def test_atlas_out_of_credit_says_so_at_once(atlas, tmp_path):
    atlas(balance=False)
    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match="provider balance"):
        tg.generate_thumbnails("t", _ref(tmp_path), num_images=2, output_dir=str(tmp_path / "out"))
    with pytest.raises(RuntimeError, match="provider balance"):
        tg.generate_thumbnail_no_refs("t", output_dir=str(tmp_path / "t2i"), count=2)
    assert time.monotonic() - t0 < 1.0


def test_the_site_answers_503_for_both_reasons():
    from webapp.server import _provider_http_status
    assert _provider_http_status(RuntimeError(tg.TOO_LONG)) == 503
    assert _provider_http_status(RuntimeError("Thumbnail service unavailable (provider balance). Try again later.")) == 503


def test_the_page_shows_plain_words_when_an_html_page_comes_back():
    """Runs app.js's own readJson, friendlyApiError and noReplyMessage on Cloudflare's 524 page and on a JSON 503."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    js = (ROOT / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
    names = ("async function readJson(", "function noReplyMessage(", "function friendlyApiError(")
    parts = []
    for name in names:
        start = js.index(name)
        parts.append(js[start:js.index("\n}\n", start) + 3])
    for fn in ("generateStudioThumbnails", "regenerateThumbnails", "handleThumbFinalUpload"):
        body = js[js.index(f"async function {fn}("):]
        body = body[:body.index("\n}\n")]
        assert ".json()" not in body and "readJson(res" in body, f"{fn} must not parse an HTML page as JSON"
    harness = "\n".join(parts) + """
const html = { status: 524, text: async () => '<!DOCTYPE html><html><title>A timeout occurred</title></html>' };
const busy = { status: 503, text: async () => JSON.stringify({ detail: 'Thumbnail generation failed: Thumbnail service unavailable (provider balance). Try again later.' }) };
(async () => {
  const out = [];
  for (const res of [html, busy]) out.push(friendlyApiError(await readJson(res, {}), noReplyMessage(res)));
  console.log(JSON.stringify(out));
})();
"""
    out = subprocess.run([node, "-e", harness], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    said = json.loads(out.stdout)
    assert said[0] == "the server took too long to answer. Try again in a moment."
    assert "provider balance" in said[1]
