"""Just shipped (webapp/static/local-studio.js shipped()): the newest Studio release at the top of a returning
customer's home for three weeks, its changes as tiles (New first, at most four), hidden per release by Got it. The
owner asked for every release to be visible and exciting, not a list at the bottom of the page."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "webapp" / "static" / "local-studio.js").read_text()
CODE = JS[JS.index("    const SHIPPED_DAYS"):JS.index("    function whatsNew() {")]
DAY = 86400000


def shipped(changelog, *, returning=True, seen=(), now_ms=None):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    script = """
const [code, data, returning, seen, now] = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
global.localStorage = {getItem: k => seen.includes(k) ? '1' : null};
if (now) Date.now = () => now;
const f = new Function('data', 'esc', 'icon', 'returning', 'STUDIO_URL', code + '; return shipped();');
process.stdout.write(f(data, esc, () => '<svg></svg>', () => returning, 'http://127.0.0.1:7860/'));
"""
    payload = json.dumps([CODE, {"changelog": changelog}, returning, list(seen), now_ms])
    out = subprocess.run(["node", "-e", script], input=payload, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return out.stdout


RELEASE = {"build": 67, "date": "2026-10-21", "title": "Queue 15 Ranking Shorts",
           "items": ["Improved: clips start and end between words. More.", "New: queue up to 15 Shorts. Then more.",
                     "Changed: text removal is off by default.", "New: clips from TikTok. Details.",
                     "Fixed: a crash. Gone.", "New: a fifth thing. Text."]}
SHIPPED_MS = 1792540800000                                     # 2026-10-21T00:00Z


def test_a_returning_customer_sees_the_newest_release_on_top():
    html = shipped([RELEASE], now_ms=SHIPPED_MS + 2 * DAY)
    assert 'id="ls-shipped"' in html and "Just shipped · Studio 67" in html
    heads = [h.split("</h3>")[0] for h in html.split("<h3>")[1:]]
    assert heads == ["Queue up to 15 Shorts", "Clips from TikTok", "A fifth thing", "Clips start and end between words"]
    assert "Changed" not in html                               # untagged lines stay in the full list
    assert 'data-ls-shipped-seen="67"' in html and 'href="#ls-whats-new"' in html


def test_it_goes_after_three_weeks_or_once_dismissed_and_never_for_a_new_customer():
    assert shipped([RELEASE], now_ms=SHIPPED_MS + 22 * DAY) == ""
    assert shipped([RELEASE], now_ms=SHIPPED_MS + DAY, seen=["cr-shipped-seen-67"]) == ""
    assert shipped([RELEASE], now_ms=SHIPPED_MS + DAY, returning=False) == ""
    assert shipped([], now_ms=SHIPPED_MS) == ""
    assert shipped([{**RELEASE, "date": "soon"}], now_ms=SHIPPED_MS) == ""


def test_text_is_escaped():
    evil = {**RELEASE, "title": "<img src=x onerror=alert(1)>", "items": ["New: <b>bold</b> claim. Rest <i>x</i>."]}
    html = shipped([evil], now_ms=SHIPPED_MS)
    assert "<img" not in html and "<b>bold" not in html and "&lt;b&gt;" in html


def test_the_real_changelog_reads_well_as_tiles():
    newest = json.loads((ROOT / "webapp" / "frontier" / "changelog.json").read_text(encoding="utf-8"))[0]
    html = shipped([newest], now_ms=SHIPPED_MS)
    heads = [h.split("</h3>")[0] for h in html.split("<h3>")[1:]]
    assert 1 <= len(heads) <= 4 and all(len(h) <= 70 for h in heads), heads
    assert "—" not in json.dumps(newest, ensure_ascii=False)
