"""A creator's own comic panels as a video's pictures (cr_panels)."""
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_panels as panels  # noqa: E402


def png(w=400, h=300, colour=(200, 60, 60)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), colour).save(buf, "PNG")
    return buf.getvalue()


def strip(panels_n=3, w=600):
    """A webtoon strip: coloured panels separated by tall white gutters."""
    h = panels_n * 500 + (panels_n + 1) * 80
    img = Image.new("RGB", (w, h), "white")
    for i in range(panels_n):
        top = 80 + i * 580
        tile = Image.effect_noise((w - 40, 500), 60).convert("RGB")
        img.paste(tile, (20, top))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def test_an_upload_keeps_reading_order_and_the_same_panels_make_the_same_set(tmp_path):
    files = [("page 10.png", png(colour=(1, 2, 3))), ("page 2.png", png(colour=(9, 9, 9))), ("page 1.png", png())]
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        for name, data in files:
            z.writestr("chapter/" + name, data)
        z.writestr("__MACOSX/._page 1.png", b"junk")
        z.writestr("notes.txt", b"not a panel")
    info = panels.save([("chapter.cbz", archive.getvalue())], tmp_path)
    assert info["count"] == 3 and info["uploaded"] == 3
    got = panels.panel_set(info["id"], tmp_path)["files"]
    colours = [Image.open(f).getpixel((5, 5)) for f in got]
    assert colours[0][0] > 150 and colours[1][0] < 20 and colours[2][0] < 10, "page 1, page 2, page 10"
    assert panels.save([("chapter.cbz", archive.getvalue())], tmp_path)["id"] == info["id"]
    assert panels.recent(tmp_path)[0]["id"] == info["id"]


def test_a_webtoon_strip_is_cut_at_its_gutters(tmp_path):
    info = panels.save([("episode.jpg", strip(3))], tmp_path)
    assert info["count"] == 3
    sizes = [Image.open(f).size for f in panels.panel_set(info["id"], tmp_path)["files"]]
    assert all(450 <= h <= 560 for _, h in sizes), sizes


def test_files_that_are_not_panels_are_refused_plainly(tmp_path):
    with pytest.raises(ValueError, match="not a picture"):
        panels.save([("script.docx", b"x")], tmp_path)
    with pytest.raises(ValueError, match="Upload your panels again"):
        panels.panel_set("../etc", tmp_path)


def test_each_panel_is_framed_whole_on_16_9(tmp_path):
    src = tmp_path / "tall.jpg"
    Image.new("RGB", (300, 900), (240, 30, 30)).save(src)
    out = panels.frame(src, tmp_path / "out.jpg")
    img = Image.open(out)
    assert img.size == (1920, 1080)
    assert img.getpixel((960, 540))[0] > 200, "the panel itself in the middle"
    assert img.getpixel((40, 540))[0] < 160, "a darkened blur beside it"


def test_panels_are_read_a_few_at_a_time_once(tmp_path):
    files = [str(tmp_path / f"{i:04d}.jpg") for i in range(1, 12)]
    calls = []

    def see(prompt, images):
        calls.append(len(images))
        first = int(prompt.split("panels ")[1].split(" ")[0])
        return json.dumps([{"panel": first + k, "what": f"event {first + k}"} for k in range(len(images))])
    rows = panels.read_panels(files, see, tmp_path / "log.json", log=lambda m: None)
    assert calls == [8, 3] and rows[10] == {"panel": 11, "what": "event 11"}
    panels.read_panels(files, lambda *a: pytest.fail("read twice"), tmp_path / "log.json", log=lambda m: None)
    assert "Panel 3: event 3" in panels.sources_block(rows)


def test_the_narration_is_tied_to_panels_word_for_word_or_spread_evenly():
    rows = [{"panel": n, "what": f"p{n}"} for n in (1, 2, 3)]
    script = "The hero wakes up. He finds the sword. The goblin attacks him at dawn."
    good = json.dumps([{"panel": 1, "text": "The hero wakes up."}, {"panel": 2, "text": "He finds the sword."},
                       {"panel": 3, "text": "The goblin attacks him at dawn."}])
    assert [r["panel"] for r in panels.tie(script, rows, lambda p: good)] == [1, 2, 3]
    made_up = json.dumps([{"panel": 1, "text": "Something else entirely."}])
    spread = panels.tie(script, rows, lambda p: made_up)
    assert " ".join(r["text"] for r in spread) == script and [r["panel"] for r in spread] == [1, 2, 3]


def test_a_panel_job_shows_its_panels_and_draws_nothing(tmp_path, monkeypatch):
    info = panels.save([("a.png", png()), ("b.png", png(colour=(10, 200, 10)))], tmp_path / "sets")
    monkeypatch.setattr(panels, "ROOT", tmp_path / "sets")
    job = tmp_path / "job"
    job.mkdir()
    (job / "run_spec.json").write_text(json.dumps({"cr_panels": info["id"]}))
    engine = type("E", (), {})()
    engine.SCRIPT_MODEL = "m"
    engine.log = lambda m: None
    engine.claude_vision = lambda p, imgs, max_tokens=0: json.dumps([{"panel": 1, "what": "a"}, {"panel": 2, "what": "b"}])
    engine.claude = lambda p, m, max_tokens=0, provider=None: json.dumps(
        [{"panel": 1, "text": "First line here."}, {"panel": 2, "text": "Second line here."}])
    engine._sources_block = lambda job, title: ""
    engine.generate_astrology_visuals = lambda *a, **k: pytest.fail("a panel job never draws")
    panels.install(engine)
    photos, clips = engine.generate_astrology_visuals("First line here. Second line here.", job, False, "manga")
    assert clips == [] and [p.name for p in photos] == ["scene_00.jpg", "scene_01.jpg"]
    items = json.loads((job / "astro_scenes.json").read_text())
    assert [i["text"] for i in items] == ["First line here.", "Second line here."]
    assert "Panel 2: b" in engine._sources_block(job, "t")
    other = tmp_path / "other"
    other.mkdir()
    assert engine._sources_block(other, "t") == "", "other jobs are untouched"


def test_a_panel_on_a_white_page_margin_fills_the_frame_without_the_margin(tmp_path):
    from PIL import Image, ImageDraw
    page = Image.new("RGB", (900, 600), "white")
    ImageDraw.Draw(page).rectangle((150, 60, 750, 540), fill=(40, 90, 160))
    page.save(tmp_path / "p.png")
    out = panels.frame(tmp_path / "p.png", tmp_path / "f.jpg")
    framed = Image.open(out).convert("RGB")
    left_edge = framed.getpixel((int(1920 * 0.5 - 1080 * 0.94 * 600 / 480 / 2) + 6, 540))
    assert left_edge[2] > 120 and left_edge[0] < 90, "the art starts where the white margin was"
    assert max(framed.getpixel((960, 540))) < 200
    art = Image.new("RGB", (900, 600), (240, 240, 240))
    ImageDraw.Draw(art).rectangle((0, 0, 899, 299), fill=(250, 250, 250))
    assert panels._trim(art).size == (900, 600), "a pale picture with no margin is left whole"
