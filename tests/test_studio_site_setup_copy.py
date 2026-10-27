"""The site's Studio install guide, after the owner's Windows test (2026-10-26): Extract All nests the Studio folder in
another one, Bypass permissions is greyed out on a fresh Claude app, the download was "About 250 MB" (really ~1.1 GB
with setup), the walkthrough's Mac chapter showed on the Windows tab, and the cookie banner sat over Download."""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "webapp" / "static"
JS = (STATIC / "local-studio.js").read_text(encoding="utf-8")


def test_the_install_prompt_works_inside_the_inner_studio_folder():
    prompt = JS[JS.index("const CLAUDE_PROMPT"):JS.index(";", JS.index("const CLAUDE_PROMPT"))]
    assert '"ChannelRecipe studio" folder, work inside that one' in prompt
    assert "reopen Claude Code on it so Studio" in prompt
    setup = JS[JS.index("function setupSteps()"):JS.index("function planPanel(")]
    assert "open the inner one (it holds Start on Windows.cmd)" in setup, "Windows: which folder to open"


def test_bypass_permissions_is_not_needed_and_says_where_it_is():
    setup = JS[JS.index("function setupSteps()"):JS.index("function planPanel(")]
    assert "You don’t need <b>Bypass permissions</b>" in setup and "you press Allow" in setup
    assert "Settings › Claude Code › Allow bypass permissions mode" in setup


def test_the_download_size_counts_what_setup_downloads_too():
    assert "250 MB" not in JS
    sizes = JS[JS.index("const DOWNLOAD"):JS.index("\n", JS.index("const DOWNLOAD"))]
    assert "windows: {more: '900 MB', total: '1.1 GB'}" in sizes and "mac: {more: '800 MB', total: '1 GB'}" in sizes
    assert "The ZIP is about 240 MB." in JS and "' in all.</p>" in JS


def test_the_windows_tab_drops_the_mac_only_chapter():
    video = JS[JS.index("const WALKS"):JS.index("function setupSteps()")]
    assert "chapters.filter(([,label])=>platform!=='windows'||!/\\bMac\\b/.test(label))" in video
    assert "the Mac security prompt" in video and "platform==='windows'?" in video


def test_the_cookie_banner_is_a_small_corner_card_that_leaves_room():
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    banner = re.search(r'<div id="cookie-banner"[^>]*>', index).group(0)
    assert 'class="cookie-banner"' in banner and "max-width: 560px" not in banner and "margin: 0 auto" not in banner
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    rule = css[css.index(".cookie-banner {"):css.index("}", css.index(".cookie-banner {"))]
    assert "right: 16px" in rule and "max-width: 360px" in rule and "left:" not in rule
    assert "body.cookie-open { padding-bottom:" in css
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    for name in ("acceptCookies", "declineCookies"):
        body = app[app.index("function " + name):app.index("}", app.index("function " + name))]
        assert "classList.remove('cookie-open')" in body, name
    assert "classList.toggle('cookie-open'" in app
