"""Whiteboard styles: each picture draws itself on, a hand tracing the lines, then the colour comes in (cr_drawon)."""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_drawon as drawon  # noqa: E402


def drawing(path):
    img = Image.new("RGB", (1920, 1080), "white")
    d = ImageDraw.Draw(img)
    d.rectangle((200, 300, 700, 800), fill=(70, 130, 220), outline="black", width=10)      # left: a blue box
    d.ellipse((1200, 300, 1700, 800), fill=(230, 90, 60), outline="black", width=10)        # right: a red circle
    img.save(path)
    return path


def frame(video, t):
    out = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(video), "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(out, np.uint8).reshape(1080, 1920, 3).astype(int)


def test_a_drawing_draws_its_lines_left_to_right_with_the_hand_then_its_colour(tmp_path):
    still = drawing(tmp_path / "scene_00.jpg")
    anim = drawon.render(still, tmp_path / "scene_00.anim.mp4", words=8)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                str(anim)], capture_output=True, text=True).stdout)
    draw = drawon.draw_seconds(8)
    assert abs(dur - (draw + drawon.FILL_S + drawon.HOLD_S)) < 0.15
    early = frame(anim, draw * 0.3)
    assert early[310:790, 201:208].mean() < 60, "the left box's line is drawn first"
    assert early[540, 1700].mean() > 200, "the right circle is not drawn yet"
    assert early[550, 450].mean() > 200, "no colour while the lines are drawn"
    mid = frame(anim, draw * 0.6)
    hand = np.abs(mid - early).sum(axis=2) > 120
    assert hand[:, 900:].sum() > 5000, "the hand follows the newest line"
    last = frame(anim, dur - 0.05)
    original = np.asarray(Image.open(still).convert("RGB")).astype(int)
    assert np.abs(last - original).mean() < 6, "it ends on the picture itself"


def test_how_long_the_lines_take_follows_the_words():
    assert drawon.draw_seconds(2) == 1.0 and drawon.draw_seconds(60) == 2.6
    assert 1.0 < drawon.draw_seconds(9) < 2.6


def test_only_a_whiteboard_style_draws_and_the_marker_is_heard(tmp_path, monkeypatch):
    job = tmp_path / "job"
    (job / "images").mkdir(parents=True)
    still = drawing(job / "images" / "scene_00.jpg")
    (job / "style.txt").write_text("board\n")
    (job / "astro_scenes.json").write_text(json.dumps([{"text": "one two three four five six"}]))
    drawn = []
    monkeypatch.setattr(drawon, "render", lambda s, d, n: drawn.append((s.name, n)) or d.write_bytes(b"x" * 30000))
    engine = type("E", (), {})()
    engine.log = lambda m: None
    engine.STYLE_INFO = {"board": {"look": {"reveal": "draw"}}, "plain": {"look": {}}}
    engine.generate_astrology_visuals = lambda script, job_, force, style="": ([str(still)], [])
    drawon.install(engine)
    engine.generate_astrology_visuals("s", job, False, "board")
    assert drawn == [("scene_00.jpg", 6)]
    engine.generate_astrology_visuals("s", job, False, "board")
    assert len(drawn) == 1, "a fresh drawing is kept"
    plan = [("photo", str(still), False, 3.0), ("motion", "kit.mp4", False, 2.0), ("photo", str(still), False, 3.0)]
    assert drawon.cues(plan, {"reveal": "draw"}) == [(0.05, "scribble", -21.0), (5.05, "scribble", -21.0)]
    assert drawon.cues(plan, {}) == []
    (job / "style.txt").write_text("plain\n")
    (job / "images" / "scene_00.anim.mp4").unlink()
    engine.generate_astrology_visuals("s", job, False, "plain")
    assert len(drawn) == 1, "other styles keep their stills"
