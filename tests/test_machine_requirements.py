"""What a creator's computer needs, said in setup and on the site, and a video that can't fit on the disk stopped before
it starts (2026-10-25): long videos failed most often, and most failures were local ones with no reason recorded. A
disk that fills up halfway through a video is one of them.

cr_machine reads memory, processor cores and free disk; setup shows them as "This computer" (a note, never a stop);
each video's own check (cr_readiness.preset) stops one the disk can't hold even at what was measured, saying how much
to free, and warns under a comfortable margin; Claude Code's setup_check and quote say the same; the site's "What your
computer needs" says the numbers cr_machine uses. Review (2026-10-26): the disk measured is the one the videos go on
(CHANNELRECIPE_OUTPUT), a Mac counts purgeable space as Finder does, a batch is checked one video at a time, and a
saved project carrying on needs only what its folder doesn't hold yet."""
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "local_runtime"))
import cr_machine  # noqa: E402
import cr_readiness  # noqa: E402

LANDING = ROOT / "webapp" / "static" / "landing.html"


# ── what this computer has ────────────────────────────────────────────────────────────────────────────────────

def test_this_computers_memory_cores_and_free_disk_are_read(tmp_path):
    """Read for real wherever the suite runs (the Windows CI reads memory through GlobalMemoryStatusEx)."""
    memory, cores, free = cr_machine.memory_gb(), cr_machine.cores(), cr_machine.free_disk_gb(tmp_path)
    assert memory is not None and 0.5 < memory < 4096
    assert cores and cores >= 1
    assert free is not None and free >= 0
    assert cr_machine.where(tmp_path) == tmp_path
    (tmp_path / "output").mkdir()
    assert cr_machine.where(tmp_path) == tmp_path / "output", "videos fill the output folder's disk"


def test_the_disk_measured_is_the_one_the_videos_go_on(tmp_path, monkeypatch):
    """CHANNELRECIPE_OUTPUT (README, UPDATING) moves the videos to another drive (D:\\ChannelRecipe, an external SSD):
    that drive is measured, through the engine's own OUTPUT_ROOT, or the setting itself when no engine answers."""
    drive = tmp_path / "external" / "ChannelRecipe"
    drive.mkdir(parents=True)
    (tmp_path / "studio" / "output").mkdir(parents=True)
    studio = tmp_path / "studio"
    monkeypatch.delenv("CHANNELRECIPE_OUTPUT", raising=False)
    assert cr_machine.where(studio) == studio / "output"
    assert cr_machine.where(studio, root=drive) == drive, "the engine's OUTPUT_ROOT"
    assert cr_machine.where(studio, root=drive / "not-made-yet") == drive, "a folder not made yet: the disk under it"
    monkeypatch.setenv("CHANNELRECIPE_OUTPUT", str(drive))
    assert cr_machine.where(studio) == drive
    monkeypatch.setenv("CHANNELRECIPE_OUTPUT", str(tmp_path / "unplugged"))
    assert cr_machine.where(studio) == studio / "output", "an unplugged drive: the engine falls back to output/ too"
    seen = []
    monkeypatch.setattr(cr_machine.shutil, "disk_usage", lambda folder: seen.append(folder) or SimpleNamespace(free=9e9))
    monkeypatch.setattr(cr_machine, "_mac_available", lambda folder: None)
    assert cr_machine.free_disk_gb(studio, root=drive) == 9.0 and seen == [drive]
    assert cr_machine.status(studio, root=drive, memory=16.0, cpu=8)["free_disk_gb"] == 9.0 and seen[-1] == drive


def test_a_mac_counts_purgeable_space_as_finder_does(tmp_path, monkeypatch):
    monkeypatch.setattr(cr_machine.shutil, "disk_usage", lambda folder: SimpleNamespace(free=26.7e9))
    monkeypatch.setattr(cr_machine.sys, "platform", "darwin")
    monkeypatch.setattr(cr_machine, "_mac_available", lambda folder: 29.4e9)
    assert cr_machine.free_disk_gb(tmp_path) == 29.4
    monkeypatch.setattr(cr_machine, "_mac_available", lambda folder: None)
    assert cr_machine.free_disk_gb(tmp_path) == 26.7, "no answer from macOS: what the disk itself says"
    monkeypatch.setattr(cr_machine.sys, "platform", "win32")
    monkeypatch.setattr(cr_machine, "_mac_available", lambda folder: (_ for _ in ()).throw(AssertionError("asked")))
    assert cr_machine.free_disk_gb(tmp_path) == 26.7


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS reads its own available capacity")
def test_macos_answers_with_its_available_capacity(tmp_path):
    import shutil
    got = cr_machine._mac_available(tmp_path)
    assert got is not None and got >= shutil.disk_usage(tmp_path).free * 0.9
    assert cr_machine._mac_available(tmp_path / "missing" / "folder") is None


def test_the_room_a_video_needs_grows_with_its_length_its_3d_and_the_batch():
    """Measured on the owner's Mac and in the review: a working folder keeps 0.2-0.35 GB a minute, finished files add
    25-45 MB a minute, 3D scenes about 1 GB a minute; each video in a batch keeps its own. need_gb is what was
    measured (below it a video stops before it starts), comfortable_gb adds a margin (below it, a warning)."""
    assert cr_machine.need_gb(10) == 4.0 and cr_machine.comfortable_gb(10) == 6.0
    assert cr_machine.need_gb(10, threed=True) == 14.0 and cr_machine.comfortable_gb(10, threed=True) == 16.0
    assert cr_machine.need_gb(10, videos=3) == 11.0 and cr_machine.comfortable_gb(10, videos=3) == 16.0
    assert cr_machine.need_gb(None) == cr_machine.need_gb("not a number") == cr_machine.need_gb(0) == cr_machine.need_gb(1)
    assert cr_machine.comfortable_gb(1) == 1.5


def test_a_computer_with_room_to_spare_has_nothing_to_say(tmp_path):
    pc = cr_machine.status(tmp_path, memory=16.0, cpu=8, free=120.0)
    assert pc["ready"] is True and pc["notes"] == [] and pc["message"] == "" and pc["short"] == []
    assert pc["ten_minute_video_gb"] == 4.0 and pc["ten_minute_3d_video_gb"] == 14.0
    assert pc["ten_minute_comfortable_gb"] == 6.0 and pc["ten_minute_3d_comfortable_gb"] == 16.0
    unknown = cr_machine.status(tmp_path, memory=None, cpu=None, free=None)
    assert unknown["notes"] == [], "a value that couldn't be read is never a worry"


def test_an_8_gb_computer_with_shared_graphics_memory_reads_as_8_gb(tmp_path):
    assert cr_machine.status(tmp_path, memory=7.8, cpu=8, free=120.0)["notes"] == []


def test_a_small_computer_gets_plain_notes_in_setup_never_a_stop(tmp_path):
    pc = cr_machine.status(tmp_path, memory=4.0, cpu=2, free=4.2)
    assert pc["ready"] is True and pc["short"] == ["memory", "cores", "disk"]
    memory, cores, disk = pc["notes"]
    assert "4 GB of memory" in memory and "up to about 4 GB" in memory and "Close other apps" in memory
    assert "2 processor cores" in cores and "more slowly" in cores
    assert "4.2 GB is free" in disk and "about 4 GB" in disk and "14 GB with 3D scenes" in disk
    assert "6 GB (16 GB) to be comfortable" in disk
    assert "before each video" in disk
    row, = [r for r in cr_readiness.setup_rows({"computer": pc}) if r["key"] == "computer"]
    assert row == {"key": "computer", "label": "This computer", "ready": True, "message": pc["message"]}
    assert cr_readiness.setup_rows({}) == [], "older callers without the check see no new row"
    line = cr_machine.diagnostics_line(pc)
    assert line == ("This computer: memory 4 GB · 2 cores · 4.2 GB free on the videos' disk "
                    "(short of: memory, cores, disk)")
    assert cr_machine.diagnostics_line(None) == ""


@pytest.mark.parametrize("platform,bin_name", [("darwin", "empty the Bin"), ("win32", "empty the Recycle Bin")])
def test_a_video_the_disk_cant_hold_says_how_much_to_free(platform, bin_name):
    assert cr_machine.disk_check(6.0, 10, platform=platform) == ("", "")
    one = cr_machine.disk_problem(2.1, 10, platform=platform)
    assert one.startswith("This 10-minute video needs about 4 GB free on the disk Studio's videos go on")
    assert "2.1 GB is free" in one and "Free up at least 1.9 GB" in one and bin_name in one
    assert "make it shorter" in one and "Nothing has started or been paid for." in one
    three = cr_machine.disk_problem(10, 10, threed=True, platform=platform)
    assert three.startswith("This 10-minute video with 3D scenes needs about 14 GB") and "Free up at least 4 GB" in three
    assert cr_machine.disk_check(None, 60, videos=15, threed=True) == ("", ""), "unknown free space never stops a video"


def test_between_what_was_measured_and_a_comfortable_margin_a_video_goes_ahead_with_a_warning():
    problem, warning = cr_machine.disk_check(5.0, 10)
    assert problem == "" and "fits" in warning and "5 GB is free" in warning and "about 6 GB is comfortable" in warning
    assert cr_machine.disk_problem(4.0, 10) == "", "exactly what was measured fits"


def test_a_batch_is_checked_one_video_at_a_time():
    """Videos in a batch are made one after another: only the first one's need stops it, the rest are a warning."""
    problem, warning = cr_machine.disk_check(10.0, 10, videos=3)
    assert problem == "" and warning.startswith("These 3 videos of 10 minutes are made one at a time")
    assert "together they need about 16 GB" in warning and "Free up about 6 GB" in warning and "or make fewer" in warning
    problem, _ = cr_machine.disk_check(3.0, 10, videos=3)
    assert problem.startswith("The first of these 3 videos of 10 minutes (they're made one at a time) needs about 4 GB")
    assert "make them shorter" in problem


def test_a_saved_video_carrying_on_needs_only_what_its_folder_doesnt_hold_yet():
    """A 10-minute video stopped on its presenter has written about 3 GB: Resume needs the rest, and a stop never says
    nothing has started (it has: its work is saved)."""
    assert cr_machine.disk_problem(1.5, 10, saved=[3.0]) == "", "4 GB needed, 3 GB of it already on the disk"
    assert cr_machine.disk_check(3.5, 10, saved=[3.0]) == ("", ""), "and 6 GB is comfortable"
    problem, _ = cr_machine.disk_check(0.3, 10, saved=[3.0])
    assert "needs about 1 GB more" in problem and "its saved work already holds 3 GB" in problem
    assert "Nothing has started" not in problem and "Its saved work is kept" in problem
    problem, _ = cr_machine.disk_check(0.3, 10, saved=[30.0])
    assert "about 0.5 GB more" in problem, "never less than Studio's own room"


def test_a_saved_videos_folder_is_measured(tmp_path):
    job = tmp_path / "doc" / "_work" / "why-rome-fell"
    (job / "segments").mkdir(parents=True)
    (job / "segments" / "seg_000.mp4").write_bytes(b"x" * 3_000_000)
    (job / "audio.mp3").write_bytes(b"x" * 1_000_000)
    assert cr_machine.folder_gb(job) == pytest.approx(0.004)
    assert cr_machine.folder_gb(tmp_path / "missing") == 0.0
    studio = SimpleNamespace(mv=SimpleNamespace(find_job=lambda slug: job if slug == "why-rome-fell" else None,
                                                slugify=lambda title: title.lower().replace(" ", "-")))
    assert cr_readiness.saved_gb(studio, {}, ["Why Rome fell", "Something new"]) == [pytest.approx(0.004), 0.0]
    assert cr_readiness.saved_gb(studio, {"cr_attempt_slug": "why-rome-fell"}, ["Another title"]) == \
        [pytest.approx(0.004)]
    assert cr_readiness.saved_gb(SimpleNamespace(mv=SimpleNamespace()), {}, ["Why Rome fell"]) == []


# ── each video checks the disk before it starts ──────────────────────────────────────────────────────────────

def preset_studio(features):
    info = {"features": {"offered": list(features), "on": list(features)}}
    return SimpleNamespace(
        mv=SimpleNamespace(prompts=SimpleNamespace(PROMPT_SETS={"doc": {}}), STYLE_INFO={"doc": info}),
        styles=SimpleNamespace(load_all=lambda: None, STYLES={"doc": info}),
        features=SimpleNamespace(capabilities=lambda: {"base": {"voice": {"ok": True}}, "features": {}},
                                 KEYS=list(features), EXPLICIT=[], BY_KEY={},
                                 resolve=lambda info, asked: {k: True for k in features}))


@pytest.fixture
def preset(monkeypatch):
    monkeypatch.setattr(cr_readiness, "basic", lambda checks: {"checks": [], "claude_code": {}, "brain_mode": "claudecode"})
    monkeypatch.setattr(cr_readiness, "proof", lambda *a, **k: {})
    monkeypatch.setattr(cr_readiness.cr_media, "active", lambda task: False)

    def run(free, spec, features=()):
        checks = {} if free is ... else {"computer": cr_machine.status(".", memory=16.0, cpu=8, free=free)}
        result = cr_readiness.preset(preset_studio(features), checks, {"style": "doc", **spec})
        return result, [c for c in result["checks"] if c["key"] == "disk_space"]
    return run


def test_a_long_video_on_a_nearly_full_disk_stops_before_it_starts(preset):
    result, (disk,) = preset(2.1, {"title": "Why Rome fell", "minutes": 10})
    assert result["ready"] is False and disk["ready"] is False
    assert "This 10-minute video needs about 4 GB" in disk["message"] and disk["message"] in result["blockers"]
    result, rows = preset(5.0, {"title": "Why Rome fell", "minutes": 10})
    assert result["ready"] is True and rows == [] and "with little to spare" in result["warnings"][0]
    result, rows = preset(7.0, {"title": "Why Rome fell", "minutes": 10})
    assert result["ready"] is True and rows == [] and "warnings" not in result


def test_3d_scenes_and_a_batch_need_more_room(preset):
    result, (disk,) = preset(10.0, {"title": "Pyramids", "minutes": 10}, features=["threed"])
    assert "with 3D scenes needs about 14 GB" in disk["message"]
    result, rows = preset(10.0, {"title": "One\nTwo\nThree", "minutes": 10})
    assert result["ready"] is True and rows == [], "the first of three fits: a batch goes ahead"
    assert result["warnings"][0].startswith("These 3 videos of 10 minutes are made one at a time")
    result, (disk,) = preset(3.0, {"title": "One\nTwo\nThree", "minutes": 10})
    assert "The first of these 3 videos" in disk["message"]
    result, (disk,) = preset(10.0, {"title": "Fast", "minutes": 10, "render_backend": "manimgx"})
    assert "with 3D scenes" in disk["message"], "Fast mode renders 3D scenes"


def test_a_saved_project_carrying_on_is_checked_for_what_it_still_needs(preset, monkeypatch):
    """Resume, a presenter choice and the 3D reassembly go through the same check: a 10-minute video stopped on its
    presenter after writing 3 GB carries on with 1.5 GB free, and is never told nothing has started."""
    monkeypatch.setattr(cr_readiness, "saved_gb", lambda studio, spec, titles: [3.0])
    result, rows = preset(1.5, {"title": "Why Rome fell", "minutes": 10})
    assert result["ready"] is True and rows == []
    result, (disk,) = preset(0.2, {"title": "Why Rome fell", "minutes": 10})
    assert "Nothing has started" not in disk["message"] and "Its saved work is kept" in disk["message"]


def test_work_that_makes_no_video_and_older_callers_are_never_stopped_by_the_disk(preset):
    result, rows = preset(1.0, {"title": "Script only", "minutes": 10, "steps": ["script"]})
    assert rows == [], "a script alone needs no room for a video"
    result, rows = preset(..., {"title": "Why Rome fell", "minutes": 10})
    assert rows == [] and result["ready"] is True


# ── Claude Code says it too ───────────────────────────────────────────────────────────────────────────────────

def test_setup_check_says_what_this_computer_has_and_where_its_short(monkeypatch, tmp_path):
    import cr_studio_tools as tools
    pc = cr_machine.status(tmp_path, memory=8.0, cpu=8, free=3.0)
    monkeypatch.setattr(tools, "other_folder", lambda: "")
    monkeypatch.setattr(tools, "call", lambda method, path, body=None, **kw: {
        "local_version": "5.3-admin.104", "readiness": {"ready": True, "blockers": []}, "computer": pc}
        if path == "/cr-status" else {"available": False})
    out = tools.run_tool("setup_check", {})["structuredContent"]["computer"]
    assert out["checked"] is True and out["memory_gb"] == 8.0 and out["cores"] == 8 and out["free_disk_gb"] == 3.0
    assert out["disk_needed_gb"] == {"10_minute_video": 4.0, "10_minute_3d_video": 14.0}
    assert out["disk_comfortable_gb"] == {"10_minute_video": 6.0, "10_minute_3d_video": 16.0}
    assert out["short_of"] == ["disk"] and "3 GB is free" in out["notes"][0]
    assert "Never delete their files" in out["what_to_do"]
    assert tools.computer_summary(None) == {"checked": False}
    assert tools.computer_summary(cr_machine.status(tmp_path, memory=16.0, cpu=8, free=200.0))["what_to_do"] == ""


def test_the_quote_says_when_the_disk_cant_hold_the_videos(monkeypatch, tmp_path):
    import cr_studio_tools as tools
    monkeypatch.setattr(tools, "STATE", tmp_path / "state")
    row = {"name": "doc", "label": "Documentary", "features": {"offered": ["threed"], "on": []}}
    cat = {"prices": {"voice": {"per_min": 0.02}, "features": {}}}
    measured = []
    monkeypatch.setattr(tools, "output_root", lambda: "D:/ChannelRecipe")
    monkeypatch.setattr(cr_machine, "free_disk_gb", lambda home, root=None: measured.append(root) or 3.0)
    q = tools.quote_for(cat, row, titles=2, minutes=10, where="this_computer", features={"threed": False})
    assert measured == ["D:/ChannelRecipe"], "the disk the running Studio's videos go on"
    assert q["disk"]["needs_gb"] == 4.0 and q["disk"]["free_gb"] == 3.0 and q["disk"]["comfortable_gb"] == 11.0
    assert "The first of these 2 videos of 10 minutes" in q["summary"] and "needs about 4 GB" in q["summary"]
    monkeypatch.setattr(cr_machine, "free_disk_gb", lambda home, root=None: 8.0)
    q = tools.quote_for(cat, row, titles=2, minutes=10, where="this_computer", features={"threed": False})
    assert "problem" not in q["disk"] and "a later one may stop for room" in q["summary"]
    monkeypatch.setattr(cr_machine, "free_disk_gb", lambda home, root=None: 500.0)
    q = tools.quote_for(cat, row, titles=1, minutes=10, where="this_computer", features={"threed": True})
    assert q["disk"] == {"needs_gb": 14.0, "comfortable_gb": 16.0, "free_gb": 500.0} and "disk" not in q["summary"]


def test_studio_tells_claude_codes_tools_where_its_videos_go(monkeypatch, tmp_path):
    import cr_studio_tools as tools
    monkeypatch.setattr(tools, "HOME", tmp_path)
    assert tools.output_root() is None
    (tmp_path / ".cr-runtime.json").write_text('{"token": "t", "output": "/Volumes/T7/ChannelRecipe"}', encoding="utf-8")
    assert tools.output_root() == "/Volumes/T7/ChannelRecipe" and tools._token() == "t"
    source = (ROOT / "local_runtime" / "cr_studio.py").read_text(encoding="utf-8")
    assert '"output":str(getattr(studio.mv, "OUTPUT_ROOT", "") or "")' in source
    assert 'cr_machine.status(HOME, root=getattr(studio.mv, "OUTPUT_ROOT", None))' in source


# ── the site says the same numbers ────────────────────────────────────────────────────────────────────────────

def test_the_site_says_what_a_computer_needs_in_its_pricing_with_studios_own_numbers():
    html = LANDING.read_text(encoding="utf-8")
    pricing = html[html.index('<section class="pricing-section" id="pricing">'):]
    pricing = pricing[:pricing.index("</section>")]
    assert 'id="computer-needs"' in pricing and "What your computer needs" in pricing
    needs = re.sub(r"<[^>]+>", " ", pricing[pricing.index('id="computer-needs"'):])
    needs = re.sub(r"\s+", " ", needs)
    assert "A Mac or a Windows PC" in needs
    assert f"{cr_machine.MIN_MEMORY_GB} GB or more" in needs and f"{cr_machine.GOOD_MEMORY_GB} GB for long or 3D" in needs
    assert f"up to about {cr_machine.STUDIO_MEMORY_GB} GB while it renders" in needs
    assert f"{cr_machine.MIN_CORES} cores or more" in needs
    minutes = cr_machine.YARDSTICK_MINUTES
    assert f"About {cr_machine.need_gb(minutes):g} GB for a {minutes}-minute video" in needs
    assert f"{cr_machine.need_gb(minutes, threed=True):g} GB with 3D scenes" in needs
    assert (f"to be comfortable ({cr_machine.comfortable_gb(minutes):g} GB, or "
            f"{cr_machine.comfortable_gb(minutes, threed=True):g} GB with 3D)") in needs
    assert '<a href="#computer-needs"' in html, "the FAQ's 'What do I need' points at it"
    assert "—" not in needs, "no em dashes in customer copy"
