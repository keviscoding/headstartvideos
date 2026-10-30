import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cr_shorts", ROOT / "local_runtime" / "cr_shorts.py")
shorts = importlib.util.module_from_spec(spec)
sys.modules["cr_shorts"] = shorts
spec.loader.exec_module(shorts)


def test_a_shorts_recipe_is_marked_by_its_format():
    assert shorts.is_short({"format": "short", "shorts": {"pipeline": "zack3d"}})
    assert not shorts.is_short({"format": "long"})
    assert not shorts.is_short(None)


def test_the_length_travels_at_the_front_of_the_extra_instructions():
    assert shorts.split_extra("[[short_seconds=45]] Keep it eerie.") == (45, "Keep it eerie.")
    assert shorts.split_extra("No marker here") == (None, "No marker here")
    assert shorts.split_extra("") == (None, "")


def test_the_length_comes_from_the_page_then_the_recipe_and_stays_in_range():
    dark = {"format": "short", "shorts": {"pipeline": "zack3d", "seconds": 45}}
    assert shorts.seconds_of(dark, {}, "") == 45                          # the recipe's own length
    assert shorts.seconds_of(dark, {}, "[[short_seconds=25]] x") == 25    # the Create page's choice wins
    assert shorts.seconds_of(dark, {}, "[[short_seconds=900]]") == 60     # never past a Short
    assert shorts.seconds_of(dark, {}, "[[short_seconds=3]]") == 15
    assert shorts.seconds_of({"format": "short"}, {"short_seconds": "bad"}, "") == 25


def test_roblox_story_shorts_run_up_to_three_minutes_and_the_others_a_minute():
    """JP (Discord, 2026-10-30) wanted 2-minute Roblox Shorts; the channels' 90-150 s Shorts get the most views."""
    roblox = {"format": "short", "shorts": {"pipeline": "roblox", "seconds": 55}}
    assert shorts.max_seconds(roblox) == 180 and shorts.max_seconds({"format": "short", "shorts": {}}) == 60
    assert shorts.seconds_of(roblox, {}, "[[short_seconds=120]]") == 120
    assert shorts.seconds_of(roblox, {}, "[[short_seconds=900]]") == 180      # a YouTube Short is at most 3 minutes
    photo = {"format": "short", "shorts": {"pipeline": "photo2d", "seconds": 25}}
    assert shorts.seconds_of(photo, {}, "[[short_seconds=120]]") == 60


def test_a_studio_without_the_pipelines_or_the_pack_says_what_is_missing(tmp_path):
    missing = shorts.ready(tmp_path)
    assert any("pipelines" in m for m in missing) and any("asset pack" in m for m in missing)
    for folder, script in shorts.PIPELINES.values():
        (tmp_path / "shorts" / folder).mkdir(parents=True, exist_ok=True)
        (tmp_path / "shorts" / folder / script).write_text("")
    index = tmp_path / "shorts" / "rbx" / "r3d" / "engine" / "skins" / "rbx" / "index.json"
    index.parent.mkdir(parents=True)
    index.write_text("{}")
    assert shorts.ready(tmp_path) == []


def _pack(path, names):
    import zipfile
    with zipfile.ZipFile(path, "w") as z:
        for n in names:
            z.writestr(n, "x")


def test_the_pack_downloads_once_and_unpacks_into_shorts_with_its_index_last(tmp_path, monkeypatch):
    import types
    index = "/".join(shorts.PACK_INDEX)
    calls = []
    fake = types.ModuleType("cr_update")
    class UpdateStopped(Exception):
        pass
    fake.UpdateStopped = UpdateStopped
    fake._settings = lambda home: ("https://channelrecipe.com", "cr_mcp_k")
    def download(base, key, target, path="/api/studio/download", **options):
        calls.append(path)
        assert options.get("resume") is True and callable(options.get("progress"))
        _pack(target, [index, "rbx/r3d/engine/skins/animals/dog.glb", "hoodie/host1/pose.png"])
    fake.download = download
    monkeypatch.setitem(sys.modules, "cr_update", fake)
    said = []
    shorts.ensure_pack(said.append, None, home=tmp_path)
    root = tmp_path / "shorts"
    assert calls == ["/api/studio/shorts-pack"]
    assert (root / index).is_file() and (root / "hoodie" / "host1" / "pose.png").is_file()
    assert not (tmp_path / ".cr-shorts-pack.zip").exists()
    shorts.ensure_pack(said.append, None, home=tmp_path)          # in place: no second download
    assert calls == ["/api/studio/shorts-pack"]


def test_a_pack_that_would_write_outside_shorts_is_refused(tmp_path, monkeypatch):
    import types
    fake = types.ModuleType("cr_update")
    fake.UpdateStopped = type("UpdateStopped", (Exception,), {})
    fake._settings = lambda home: ("https://channelrecipe.com", "cr_mcp_k")
    fake.download = lambda base, key, target, path=None, **options: _pack(target, ["../evil.py"])
    monkeypatch.setitem(sys.modules, "cr_update", fake)
    import pytest
    with pytest.raises(RuntimeError, match="damaged"):
        shorts.ensure_pack(None, None, home=tmp_path)
    assert not (tmp_path / "evil.py").exists()


def test_a_failed_pack_download_says_why_in_plain_words(tmp_path, monkeypatch):
    import types
    fake = types.ModuleType("cr_update")
    class UpdateStopped(Exception):
        pass
    fake.UpdateStopped = UpdateStopped
    fake._settings = lambda home: ("https://channelrecipe.com", "cr_mcp_k")
    def download(*a, **k):
        raise UpdateStopped("The download was interrupted. Nothing was changed; check your connection and try again.")
    fake.download = download
    monkeypatch.setitem(sys.modules, "cr_update", fake)
    import pytest
    with pytest.raises(RuntimeError, match="Shorts pack couldn't be downloaded. The download was interrupted. Check your connection"):
        shorts.ensure_pack(None, None, home=tmp_path)


def test_a_stopped_pack_download_keeps_what_arrived_and_resume_carries_on(tmp_path, monkeypatch):
    import types
    import pytest
    index = "/".join(shorts.PACK_INDEX)
    fake = types.ModuleType("cr_update")
    class UpdateStopped(Exception):
        pass
    fake.UpdateStopped = UpdateStopped
    fake._settings = lambda home: ("https://channelrecipe.com", "cr_mcp_k")
    runs = []
    def download(base, key, target, path=None, resume=False, progress=None):
        runs.append(target.stat().st_size if target.exists() else 0)
        if len(runs) == 1:
            target.write_bytes(b"PK partial")                       # some of it arrived, then the connection dropped
            raise UpdateStopped("The download was interrupted. Nothing was changed; check your connection and try again.")
        assert resume, "the second run carries on from the partial file"
        progress(200 << 20, 400 << 20)
        _pack(target, [index])
    fake.download = download
    monkeypatch.setitem(sys.modules, "cr_update", fake)
    said = []
    with pytest.raises(RuntimeError, match="Resume carries on from where it stopped"):
        shorts.ensure_pack(said.append, None, home=tmp_path)
    assert (tmp_path / ".cr-shorts-pack.zip").exists(), "what arrived is kept"
    shorts.ensure_pack(said.append, None, home=tmp_path)
    assert runs == [0, len(b"PK partial")]
    assert (tmp_path / "shorts" / index).is_file() and not (tmp_path / ".cr-shorts-pack.zip").exists()
    assert any("200 of 400 MB" in line for line in said) and any("Carrying on" in line for line in said)


def test_a_whole_pack_left_by_a_stopped_unpack_is_unpacked_without_downloading_again(tmp_path, monkeypatch):
    import types
    index = "/".join(shorts.PACK_INDEX)
    fake = types.ModuleType("cr_update")
    fake.UpdateStopped = type("UpdateStopped", (Exception,), {})
    fake._settings = lambda home: ("https://channelrecipe.com", "cr_mcp_k")
    def download(*a, **k):
        raise AssertionError("no second download")
    fake.download = download
    monkeypatch.setitem(sys.modules, "cr_update", fake)
    _pack(tmp_path / ".cr-shorts-pack.zip", [index, "hoodie/host1/pose.png"])
    shorts.ensure_pack(None, None, home=tmp_path)
    assert (tmp_path / "shorts" / index).is_file()


def test_a_ranking_shorts_options_travel_in_their_own_marker_and_only_known_kinds_pass():
    extra = '[[short_seconds=25]] [[ranking={"ranks": 7, "booms": false, "sfx": "light", "board": "dogs-1a2b3c", ' \
            '"ranks_typo": 3, "commentary": "yes", "punch_ins": 1}]] Keep it savage.'
    assert shorts.split_extra(extra) == (25, "Keep it savage.")
    assert shorts.ranking_options(extra) == {"ranks": 7, "booms": False, "sfx": "light", "board": "dogs-1a2b3c"}
    assert shorts.ranking_options('[[ranking={"ranks": 40}]]') == {"ranks": 10}
    assert shorts.ranking_options('[[ranking={"clean": false}]]') == {"clean": False}   # burned-in text left on
    assert shorts.ranking_options('[[ranking={not json}]]') == {}
    assert shorts.ranking_options("no marker") == {}


def test_the_ranking_pipeline_needs_no_asset_pack_and_says_so_when_it_is_missing(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(shorts, "ensure_pack", lambda *a, **k: calls.append("pack"))
    monkeypatch.setenv("CR_SHORTS_ROOT", str(tmp_path / "shorts"))

    class Engine:
        def job_dir(self, title, style):
            return str(tmp_path / "job")
    info = {"format": "short", "shorts": {"pipeline": "ranking"}}
    try:
        shorts.run_short(Engine(), "Funniest Dog Moments", "cr-shorts-ranking", info, extra='[[ranking={"ranks": 5}]]')
    except RuntimeError as error:
        assert "Ranking Shorts pipeline is not installed" in str(error)
    else:
        raise AssertionError("a Studio without the pipeline must say so")
    assert calls == [], "no 400 MB pack download for a ranking Short"


def test_the_ranking_pipeline_gets_its_options_and_progress_reads_in_plain_words(tmp_path, monkeypatch):
    root = tmp_path / "shorts"
    (root / "ranking").mkdir(parents=True)
    (root / "ranking" / "make_ranking.py").write_text(
        "import json, os, sys, pathlib\n"
        "work = pathlib.Path(sys.argv[2])\n"
        "print('12:00:00 watching 5 of 26 watched', flush=True)\n"
        "(work / 'opts.json').write_text(os.environ['CR_RANKING'])\n"
        "(work / 'short.mp4').write_bytes(b'mp4')\n"
        "(work / 'plan.json').write_text(json.dumps({'title': 'Ranking Funniest Dog Moments', 'narration': 'Bro.'}))\n"
        "(work / 'voice.json').write_text(json.dumps({'words': [[0.1, 0.4, 'Bro.']]}))\n")
    monkeypatch.setenv("CR_SHORTS_ROOT", str(root))
    said = []

    class Engine:
        def job_dir(self, title, style):
            return str(tmp_path / "job")
    info = {"format": "short", "label": "Ranking Shorts", "shorts": {"pipeline": "ranking"}}
    job = shorts.run_short(Engine(), "Funniest Dog Moments", "cr-shorts-ranking", info, sink=said.append,
                           extra='[[short_seconds=25]] [[ranking={"ranks": 6, "commentary": false}]]')
    assert json.loads((job / "short" / "opts.json").read_text()) == {"ranks": 6, "commentary": False}
    assert (job / "video.mp4").read_bytes() == b"mp4" and "Bro." in (job / "subs.srt").read_text()
    assert any(line.startswith("Watching candidate clips") for line in said)
    assert any("6 ranks" in line for line in said)


def test_the_roblox_pipeline_uses_the_pack_the_recipes_voice_and_plain_progress_words(tmp_path, monkeypatch):
    import types
    root = tmp_path / "shorts"
    (root / "roblox").mkdir(parents=True)
    (root / "roblox" / "make_roblox.py").write_text(
        "import json, os, sys, pathlib\n"
        "work = pathlib.Path(sys.argv[2])\n"
        "for step in ('director...', 'staging...', 'sets...', 'storyboard check...', 'render (final)...'):\n"
        "    print('12:00:00 ' + step, flush=True)\n"
        "(work / 'seen.json').write_text(json.dumps({'args': sys.argv[3:], 'voice': os.environ.get('CR_RBX_VOICE')}))\n"
        "(work / 'short.mp4').write_bytes(b'mp4')\n"
        "(work / 'plan.json').write_text(json.dumps({'title': 'Promise Scars', 'narration': 'In this world.'}))\n"
        "(work / 'voice.json').write_text(json.dumps({'words': [[0.1, 0.4, 'In'], [0.4, 0.8, 'this']]}))\n")
    monkeypatch.setenv("CR_SHORTS_ROOT", str(root))
    packs = []
    monkeypatch.setattr(shorts, "ensure_pack", lambda *a, **k: packs.append("pack"))
    monkeypatch.setattr(shorts, "ready", lambda home=None: [])
    voice = "bIHbv24MWmeRgasZH58o"
    monkeypatch.setitem(sys.modules, "voices", types.SimpleNamespace(
        picks=lambda: {"cr-shorts-roblox": {"provider": "elevenlabs", "voice_id": voice}}))
    said = []

    class Engine:
        def job_dir(self, title, style):
            return str(tmp_path / "job")
    info = {"format": "short", "label": "Roblox Story Shorts", "shorts": {"pipeline": "roblox", "seconds": 55}}
    job = shorts.run_short(Engine(), "Every broken promise leaves a scar", "cr-shorts-roblox", info, sink=said.append)
    assert packs == ["pack"], "the blocky avatars render in the Shorts 3D engine: the pack comes first"
    seen = json.loads((job / "short" / "seen.json").read_text())
    assert seen == {"args": ["Every broken promise leaves a scar", "55"], "voice": voice}
    assert (job / "video.mp4").read_bytes() == b"mp4" and "In this" in (job / "subs.srt").read_text()
    for words in ("Staging every shot", "Building the sets", "Checking a still of every shot", "Building visual scenes"):
        assert any(line.startswith(words) for line in said), words


def test_a_roblox_short_gets_the_creators_avatar_picture_and_never_drops_it(tmp_path, monkeypatch):
    """A customer's first Roblox Story Short (2026-10-28) drew generic kids although they had uploaded their avatar:
    Studio's Shorts path dropped the job's character picture. It now reaches the pipeline, which reads it once into the
    main character's avatar (shorts/roblox hero())."""
    import types
    monkeypatch.syspath_prepend(str(ROOT / "local_runtime"))
    root = tmp_path / "shorts"
    (root / "roblox").mkdir(parents=True)
    (root / "roblox" / "make_roblox.py").write_text(
        "import json, os, sys, pathlib\n"
        "work = pathlib.Path(sys.argv[2])\n"
        "(work / 'seen.json').write_text(json.dumps({k: os.environ.get(k) for k in ('CR_RBX_HERO_IMAGE', 'CR_RBX_HERO_WORDS')}))\n"
        "(work / 'short.mp4').write_bytes(b'mp4')\n")
    monkeypatch.setenv("CR_SHORTS_ROOT", str(root))
    monkeypatch.setattr(shorts, "ensure_pack", lambda *a, **k: None)
    monkeypatch.setattr(shorts, "ready", lambda home=None: [])
    monkeypatch.setitem(sys.modules, "voices", types.SimpleNamespace(picks=lambda: {}))
    home = tmp_path / "studio"
    (home / "assets" / "characters").mkdir(parents=True)
    picture = home / "assets" / "characters" / "my-avatar-1a2b3c.png"
    picture.write_bytes(b"png")
    monkeypatch.setattr(shorts, "HOME", home)
    import cr_character
    said = []

    class Engine:
        def __init__(self):
            self.calls = []

        def job_dir(self, title, style):
            return str(tmp_path / ("job-" + str(len(said))))

        def run_custom(self, title, minutes=20, **kwargs):
            self.calls.append(kwargs)
    info = {"format": "short", "label": "Roblox Story Shorts", "shorts": {"pipeline": "roblox", "seconds": 40}}
    job = shorts.run_short(Engine(), "Everyone has a hat", "cr-shorts-roblox", info, sink=said.append,
                           character=cr_character.UPLOADED, character_image="characters/my-avatar-1a2b3c.png")
    seen = json.loads((job / "short" / "seen.json").read_text())
    assert seen == {"CR_RBX_HERO_IMAGE": str(picture), "CR_RBX_HERO_WORDS": None}, "Studio's own words aren't theirs"
    said.append("x")
    job = shorts.run_short(Engine(), "Everyone has a hat 2", "cr-shorts-roblox", info, sink=said.append,
                           character="She always wears her lucky hat", character_image="characters/my-avatar-1a2b3c.png")
    assert json.loads((job / "short" / "seen.json").read_text())["CR_RBX_HERO_WORDS"] == "She always wears her lucky hat"
    said.clear()
    job = shorts.run_short(Engine(), "Everyone has a hat 3", "cr-shorts-roblox", info, sink=said.append,
                           character_image="characters/gone.png")
    assert json.loads((job / "short" / "seen.json").read_text())["CR_RBX_HERO_IMAGE"] is None
    assert any("isn't in this Studio any more" in line for line in said), "a picture that's gone is said"
    assert shorts.hero_picture("../secrets.png") is None and shorts.hero_picture("characters/../x.png") is None
    # the engine's run_custom hands the job's character on (it was dropped here before)
    engine = Engine()
    engine.STYLE_INFO = {"cr-shorts-roblox": info}
    got = {}
    monkeypatch.setattr(shorts, "run_short", lambda *a, **k: got.update(k))
    shorts.install(engine)
    engine.run_custom("t", 1, style="cr-shorts-roblox", character="w", character_image="characters/a.png")
    assert got["character"] == "w" and got["character_image"] == "characters/a.png"


def test_what_a_character_picture_does_is_said_per_recipe(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "local_runtime"))
    import cr_character as cc
    roblox = cc.picture_use({"format": "short", "shorts": {"pipeline": "roblox"}})
    assert roblox["use"] == "avatar" and "blocky 3D avatar" in roblox["say"] and "no picture is paid for" in roblox["say"]
    for pipe in ("zack3d", "photo2d", "ranking"):
        assert cc.picture_use({"format": "short", "shorts": {"pipeline": pipe}})["use"] == "none"
    assert "in 3D from the story's words" in cc.picture_use({"format": "short", "shorts": {"pipeline": "zack3d"}})["say"]
    coded = cc.picture_use({"look": {"visuals": "cr_animated3d"}})
    assert coded["use"] == "none" and "animation code draws its characters" in coded["say"]
    assert cc.picture_use({"look": {"threed": {"designed": {"plan": "orb"}}}})["use"] == "partial"
    assert cc.picture_use({"look": {"character_image": "characters/x.png"}}) == {"use": "stills", "say": ""}


def test_the_roblox_recipe_runs_the_roblox_pipeline():
    recipe = ROOT / "data" / "frontier" / "recipes" / "cr-shorts-roblox" / "style.json"
    if not recipe.is_file():
        import pytest
        pytest.skip("the private recipes are not in this checkout")
    row = json.loads(recipe.read_text(encoding="utf-8"))
    assert row["format"] == "short" and row["builtin"] is True and row["label"] == "Roblox Story Shorts"
    assert row["shorts"]["pipeline"] in shorts.PIPELINES and row["shorts"]["pipeline"] not in shorts.PACKLESS
    assert shorts.SECONDS[0] <= row["shorts"]["seconds"] <= shorts.SECONDS[1]
    assert "minutes" in row["cost"] and "Kie" in row["cost"], "the cost and the time are quoted"


def _roblox_stub(tmp_path, monkeypatch, seconds=31.6):
    """a Studio whose make_roblox only says what it was handed, and whose engine voices module knows one upload"""
    import types
    monkeypatch.syspath_prepend(str(ROOT / "local_runtime"))          # cr_language, as in a Studio
    root = tmp_path / "shorts"
    (root / "roblox").mkdir(parents=True)
    (root / "roblox" / "make_roblox.py").write_text(
        "import json, os, sys, pathlib\n"
        "work = pathlib.Path(sys.argv[2])\n"
        "(work / 'seen.json').write_text(json.dumps({'args': sys.argv[3:], 'own': os.environ.get('CR_RBX_OWN_VOICE'),\n"
        "                                          'voice': os.environ.get('CR_RBX_VOICE')}))\n"
        "(work / 'short.mp4').write_bytes(b'mp4')\n"
        "(work / 'plan.json').write_text(json.dumps({'title': 'Pitched', 'narration': 'In this world everyone'}))\n"
        "(work / 'voice.json').write_text(json.dumps({'words': [[0.1, 0.4, 'In'], [0.4, 0.8, 'this']]}))\n")
    monkeypatch.setenv("CR_SHORTS_ROOT", str(root))
    monkeypatch.setattr(shorts, "ensure_pack", lambda *a, **k: None)
    monkeypatch.setattr(shorts, "ready", lambda home=None: [])
    uploads = tmp_path / "uploads" / "voiceovers"
    uploads.mkdir(parents=True)
    take = uploads / "adam-pitched-1a2b.mp3"
    take.write_bytes(b"mp3")
    lengths = {take.name: seconds}

    def resolve(ref):
        ref = str(ref or "").strip()
        p = uploads / ref if "/" not in ref else Path(ref)
        return p if p.is_file() else None
    monkeypatch.setitem(sys.modules, "voices", types.SimpleNamespace(
        picks=lambda: {"cr-shorts-roblox": {"provider": "elevenlabs", "voice_id": "bIHbv24MWmeRgasZH58o"}},
        resolve=resolve, _dur=lambda p: lengths.get(Path(p).name, 0.0)))
    return take, lengths


ROBLOX = {"format": "short", "label": "Roblox Story Shorts", "shorts": {"pipeline": "roblox", "seconds": 55}}


def test_a_roblox_short_is_narrated_by_the_creators_own_recording_at_its_length(tmp_path, monkeypatch):
    """Primo (Discord, 2026-10-29): he pitches the Adam voice 1.3x in CapCut and uploads that take as the narration, but
    Roblox Story Shorts refused it and the engine's run_custom wrapper dropped byo_audio. The recording now reaches
    make_roblox (CR_RBX_OWN_VOICE), no AI voice is chosen for it, and its length is the Short's."""
    take, _ = _roblox_stub(tmp_path, monkeypatch, seconds=31.6)
    said = []

    class Engine:
        def job_dir(self, title, style):
            return str(tmp_path / "job")
    job = shorts.run_short(Engine(), "Everyone has a pitch", "cr-shorts-roblox", ROBLOX, sink=said.append,
                           extra="[[short_seconds=55]]", recording=str(take))
    seen = json.loads((job / "short" / "seen.json").read_text())
    assert seen == {"args": ["Everyone has a pitch", "32"], "own": str(take), "voice": None}
    assert any("narrated by your recording" in line for line in said)
    # an upload id (what the Create page sends) resolves to the same recording
    job = shorts.run_short(Engine(), "Everyone has a pitch", "cr-shorts-roblox", ROBLOX, recording=take.name)
    assert json.loads((job / "short" / "seen.json").read_text())["own"] == str(take)
    # and without a recording the recipe's own voice reads it, exactly as before
    job = shorts.run_short(Engine(), "Everyone has a pitch", "cr-shorts-roblox", ROBLOX, extra="[[short_seconds=55]]")
    assert json.loads((job / "short" / "seen.json").read_text()) == {
        "args": ["Everyone has a pitch", "55"], "own": None, "voice": "bIHbv24MWmeRgasZH58o"}


def test_the_engine_hands_its_byo_audio_to_the_short(tmp_path, monkeypatch):
    got = {}
    monkeypatch.setattr(shorts, "run_short", lambda *a, **k: got.update(k))

    class Engine:
        STYLE_INFO = {"cr-shorts-roblox": ROBLOX}

        def run_custom(self, title, minutes=20, **kwargs):
            raise AssertionError("a Short never runs the long-video engine")
    engine = Engine()
    shorts.install(engine)
    engine.run_custom("t", 1, style="cr-shorts-roblox", byo_audio="/x/take.mp3")
    assert got["recording"] == "/x/take.mp3"


def test_only_a_recipe_a_recording_can_narrate_takes_one_and_a_bad_recording_stops_before_anything(tmp_path, monkeypatch):
    take, lengths = _roblox_stub(tmp_path, monkeypatch)
    assert shorts.takes_own_recording(ROBLOX)
    for pipe in ("zack3d", "photo2d", "ranking"):
        info = {"format": "short", "label": "3D Explainer Shorts", "shorts": {"pipeline": pipe}}
        assert not shorts.takes_own_recording(info)
        assert "writes and voices its own narration" in shorts.own_recording_problem(info, take.name, "3D Explainer Shorts")
    assert not shorts.takes_own_recording({"format": "video"})
    assert shorts.own_recording_problem({"look": {}}, take.name) == "", "a long video's recording is the engine's own"
    assert shorts.own_recording_problem(ROBLOX, "") == "" and shorts.own_recording_problem(ROBLOX, take.name) == ""
    assert "not on this computer any more" in shorts.own_recording_problem(ROBLOX, "gone.mp3")
    lengths[take.name] = 3.0
    assert "at least 5 seconds" in shorts.own_recording_problem(ROBLOX, take.name)
    lengths[take.name] = 200.0
    assert "at most 3 minutes" in shorts.own_recording_problem(ROBLOX, take.name)
    packs = []
    monkeypatch.setattr(shorts, "ensure_pack", lambda *a, **k: packs.append(1))

    class Engine:
        def job_dir(self, title, style):
            raise AssertionError("nothing is started for a recording that can't be used")
    import pytest
    with pytest.raises(RuntimeError, match="at most 3 minutes"):
        shorts.run_short(Engine(), "t", "cr-shorts-roblox", ROBLOX, recording=take.name)
    assert packs == []
