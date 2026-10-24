"""The likeness check before a learned style is saved (local_runtime/cr_style_likeness.py) and its judge on the server
(webapp/style_watch.compare). The ifology clone saved mannequins without a word; now test pictures are drawn the way
the style's videos will be drawn, scored against the references, repaired once with the judge's fixes, and the
verdict is saved with the style. Every paid or networked part is a stand-in."""
import base64
import io
import json
import sys
import types
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_style_likeness as lk  # noqa: E402


def jpeg(path, colour=(200, 40, 40)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (320, 180), colour).save(path, "JPEG")
    return path


@pytest.fixture
def setup(tmp_path, monkeypatch):
    home, folder = tmp_path / "studio", tmp_path / "studio" / "output" / "cr-style-clone" / "op_1"
    frames = home / "output" / "looks" / "lk_1" / "frames"
    for i in range(6):
        jpeg(frames / f"f{i}.jpg", (10 * i, 80, 160))
    monkeypatch.setitem(sys.modules, "cr_style_look", types.SimpleNamespace(folder=lambda h, lid: frames.parent))
    calls = {"draw": [], "judge": []}

    def generate(prompt, dest, label, ref_urls=None, model=None, timeout_s=None):
        calls["draw"].append((prompt, dest.name, model))
        if calls.get("fail_at") == len(calls["draw"]):
            raise RuntimeError("The media task is still pending.")
        jpeg(Path(dest))

    def judge(path, payload):
        calls["judge"].append(payload)
        return calls["answers"].pop(0)
    style = {"name": "cr_clone_ifology", "look": {"still_suffix": " Low-poly 3D render, round black heads.",
                                                   "image_model": "nano-banana-2-1",
                                                   "character": "THE CAST — Runner: round black head, yellow jacket."}}
    run = lambda answers, st=style, spec=None: (calls.__setitem__("answers", list(answers)) or
                                                lk.run(folder, spec or {"look_id": "lk_1"}, st, lambda m: None,
                                                       home=home, generate=generate,
                                                       upload=lambda p: "https://x/" + p.name, call=judge))
    return home, folder, style, calls, run


def verdict(overall, characters=8, control=9, fixes=(), together=None):
    """The judge's answer: `together` is its score for all candidates at once (the hidden control among them)."""
    drawn = [{"overall": overall + d, "characters": characters} for d in (-0.5, 0.5, 0)]
    return {"overall": together or overall, "characters": characters, "medium": overall, "sets": overall,
            "palette": overall, "light": overall, "camera": overall,
            "dealbreakers": [] if overall >= 7 else ["the heads are skin-coloured"], "fixes": list(fixes),
            "cost_usd": 0.004, "candidates": drawn + [{"overall": control, "characters": 10}]}


def test_a_matching_style_is_checked_once_and_keeps_its_best_picture(setup):
    home, folder, style, calls, run = setup
    result = run([verdict(8)])
    assert result["verdict"] == "match" and result["reliable"] is True and result["round"] == 1
    assert len(calls["judge"]) == 1 and len(calls["draw"]) == 4                 # 3 pictures + the character sheet
    sent = calls["judge"][0]
    assert len(sent["candidates"]) == 4 and len(sent["references"]) == 4       # f0 drawn from, f5 the hidden control
    assert all(d[2] == "nano-banana-2-1" for d in calls["draw"])
    assert "Low-poly 3D render" in calls["draw"][0][0] and "characters designed the same way" in calls["draw"][0][0]
    assert result["best"] == "r0_1.jpg"                                         # the picture the judge scored highest
    styled = lk.apply_saved(folder, json.loads(json.dumps(style)), home)
    assert styled["look"]["style_image"] == "styles/learned/cr_clone_ifology/style.jpg"
    assert styled["look"]["character_image"] == "styles/learned/cr_clone_ifology/character.png"
    assert (home / "assets/styles/learned/cr_clone_ifology/style.jpg").is_file()
    assert styled["cr_style_clone"]["likeness"]["verdict"] == "match"
    assert run([]) == result and len(calls["judge"]) == 1                      # saved: never paid twice


def test_below_the_bar_the_judges_fixes_get_one_more_round(setup):
    home, folder, style, calls, run = setup
    result = run([verdict(4, characters=3, fixes=["make every head solid black", "add thin black stick legs"]),
                  verdict(7.5, characters=7)])
    assert len(calls["judge"]) == 2 and result["round"] == 2 and result["verdict"] == "match"
    assert "Must: make every head solid black; add thin black stick legs." in calls["draw"][3][0]
    styled = lk.apply_saved(folder, json.loads(json.dumps(style)), home)
    assert styled["look"]["still_suffix"].endswith("Must: make every head solid black; add thin black stick legs.")
    assert styled["cr_style_clone"]["likeness"]["pictures"] == ["r1_0.jpg", "r1_1.jpg", "r1_2.jpg"]


def test_still_far_after_the_repair_is_said_plainly_and_the_better_round_kept(setup):
    home, folder, style, calls, run = setup
    result = run([verdict(5, characters=4, fixes=["solid black heads"]), verdict(3, characters=2, fixes=["x"])])
    assert result["verdict"] == "close" and result["round"] == 1 and result["dealbreakers"]
    styled = lk.apply_saved(folder, json.loads(json.dumps(style)), home)
    assert "Must:" not in styled["look"]["still_suffix"]                       # the fixes made it worse: not kept


def test_a_judge_that_misreads_the_references_is_not_trusted(setup):
    home, folder, style, calls, run = setup
    assert run([verdict(8, control=3)])["reliable"] is False


def test_a_style_that_does_not_draw_its_pictures_is_skipped(setup):
    home, folder, style, calls, run = setup
    filmed = {"name": "cr_clone_doc", "look": {"still_suffix": " Documentary footage."}}
    assert run([], filmed)["state"] == "skipped" and calls["draw"] == []
    assert lk.apply_saved(folder, filmed, home)["cr_style_clone"]["likeness"]["state"] == "skipped"
    graphics = {**json.loads(json.dumps(style)), "cr_style_clone": {"visual_mode": "reference_only"}}
    assert "motion graphics" in run([], graphics)["why"] and calls["draw"] == []


def test_the_verdict_is_the_drawn_pictures_own_never_the_hidden_controls(setup):
    """The judge's score for all candidates together includes the channel's own frame (a 9): three 6s next to it read
    as a 7.5 'match'. The drawn pictures' own scores decide, so the fixes get their round."""
    home, folder, style, calls, run = setup
    result = run([verdict(6, together=7.5, fixes=["solid black heads"]), verdict(7.5)])
    assert len(calls["judge"]) == 2 and result["round"] == 2
    assert result["rounds"][0]["overall"] == 6 and result["rounds"][0]["control"] == 9


def test_a_creator_uploading_their_own_host_gets_no_drawn_sheet(setup):
    home, folder, style, calls, run = setup
    result = run([verdict(8)], spec={"look_id": "lk_1", "answers": {"host": "Yes, I'll upload mine"}})
    assert len(calls["draw"]) == 3 and "character" not in result


def test_a_stop_in_the_second_round_keeps_the_first_and_says_so(setup):
    home, folder, style, calls, run = setup
    calls["fail_at"] = 5                                                       # round 2's second picture
    result = run([verdict(5, fixes=["solid black heads"])])
    assert result["state"] == "done" and result["round"] == 1 and "pending" in result["stopped"]
    assert lk.apply_saved(folder, json.loads(json.dumps(style)), home)["cr_style_clone"]["likeness"]["verdict"] == "close"


def test_a_check_that_never_got_a_verdict_is_saved_as_stopped_with_its_cost(setup):
    home, folder, style, calls, run = setup
    calls["fail_at"] = 2
    result = run([])
    assert result["state"] == "failed" and result["cost_usd"] == 0.04 and len(calls["judge"]) == 0
    shown = lk.apply_saved(folder, json.loads(json.dumps(style)), home)["cr_style_clone"]["likeness"]
    assert shown["state"] == "failed" and "could not finish" in shown["why"]
    assert run([]) == result, "a stopped check is never paid for again on its own"


def test_a_check_made_for_other_picture_words_is_never_grafted_on(setup):
    home, folder, style, calls, run = setup
    run([verdict(8)])
    other = json.loads(json.dumps(style))
    other["look"]["still_suffix"] = " Watercolour stick figures."
    applied = lk.apply_saved(folder, other, home)
    assert "likeness" not in applied.get("cr_style_clone", {}) and "style_image" not in applied["look"]


# ── the judge on the server ────────────────────────────────────────────────────────────────────────────────────────
def b64jpeg():
    out = io.BytesIO()
    Image.new("RGB", (64, 36), (0, 0, 0)).save(out, "JPEG")
    return base64.b64encode(out.getvalue()).decode()


def test_the_server_judge_checks_its_input_and_answers_scores(monkeypatch, tmp_path):
    from webapp import style_watch as sw
    from webapp import database as db
    from fastapi import HTTPException
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db", raising=False)
    monkeypatch.setattr(db, "IS_PG", False, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    sw._ready.clear()
    seen = {}

    class Res:
        status_code = 200
        def json(self):
            return {"usageMetadata": {"promptTokenCount": 9000, "candidatesTokenCount": 300},
                    "candidates": [{"content": {"parts": [{"text": json.dumps(
                        {"medium": 9, "characters": 3, "sets": 8, "palette": 8, "light": 7, "camera": 7, "overall": 5,
                         "candidates": [{"overall": 5, "characters": 12, "why": "heads"}],
                         "dealbreakers": ["skin-coloured heads"],
                         "fixes": ["solid black heads"]})}]}}]}

    def post(url, headers=None, json=None, timeout=None):
        seen["parts"] = json["contents"][0]["parts"]
        return Res()
    user = {"id": 1, "email": "a@b.c", "plan": "studio_pro"}
    got = sw.compare(user, [b64jpeg(), b64jpeg()], [b64jpeg()], "drawn 3D", post=post)
    assert got["overall"] == 5 and got["characters"] == 3 and got["fixes"] == ["solid black heads"]
    assert got["candidates"] == [{"characters": 10.0, "overall": 5.0, "why": "heads"}]   # every area, clamped
    assert got["cost_usd"] > 0 and [p.get("text") for p in seen["parts"] if "text" in p][:3] == [
        "REFERENCE 1:", "REFERENCE 2:", "CANDIDATE 1:"]
    for bad in ([], ["not base64!"], [base64.b64encode(b"GIF89a....").decode()]):
        with pytest.raises(HTTPException):
            sw.compare(user, bad or [b64jpeg()], [b64jpeg()] if bad else [], post=post)


def test_a_designed_cast_style_is_checked_on_shots_the_3d_engine_renders(setup, tmp_path):
    """Studio 79: a style whose designed cast the engine builds is checked on three RENDERED shots (no picture paid,
    one round: a judge's prompt fix can't change code), judged like any other."""
    home, folder, style, calls, run = setup
    built = {"name": "cr_clone_ifology", "look": {"still_suffix": " Low-poly 3D.", "threed": {"preset": "designed",
             "designed": {"plan": "orb", "cast": [{"name": "Dot", "role": "lead", "top": "hoodie", "color": "#3E8C4A"}]}}}}
    assert lk.wanted(built) == ""
    rendered = []

    def render(h, st, out_dir, timeout_s=None):
        rendered.append((h, st["look"]["threed"]["designed"]["plan"], timeout_s > 0))
        return [jpeg(Path(out_dir) / f"r0_{k}.jpg") for k in range(3)]
    calls["answers"] = [verdict(5, fixes=["bigger heads"]), verdict(9)]
    result = lk.run(folder, {"look_id": "lk_1"}, built, lambda m: None, home=home, generate=None, upload=None,
                    call=lambda path, payload: (calls["judge"].append(payload) or calls["answers"].pop(0)), render=render)
    assert rendered == [(home, "orb", True)] and calls["draw"] == [], "rendered, never drawn"
    assert len(calls["judge"]) == 1 and result["state"] == "done" and len(result["rounds"]) == 1
    assert "rendered by a 3D engine" in calls["judge"][0]["context"] and result["people"] is True
    assert result["cost_usd"] == 0.004, "only the judge is paid"
    saved = lk.apply_saved(folder, built, home)
    assert "style_image" not in saved["look"] and saved["cr_style_clone"]["likeness"]["state"] == "done"
    changed = json.loads(json.dumps(built))
    changed["look"]["threed"]["designed"]["cast"][0]["color"] = "#D2382D"
    assert lk._hash(changed) != lk._hash(built), "a new cast spec is a new check"


def test_the_designed_shots_come_from_this_studios_own_renderer(tmp_path):
    home, out = tmp_path / "studio", tmp_path / "out"
    kit = home / "threed/engine/lib/designed/index.js"
    kit.parent.mkdir(parents=True)
    kit.write_text("")
    ran = []

    def fake(cmd, cwd=None, capture_output=None, text=None, timeout=None, encoding=None, errors=None):
        ran.append((cmd, cwd, timeout))
        work = Path(cmd[3])
        for scene in json.loads(Path(cmd[2]).read_text())["scenes"]:
            jpeg(work / f"{scene['id']}_01.60.jpg")
    style = {"look": {"threed": {"preset": "designed", "designed": {}}}}
    pics = lk.render_designed(home, style, out, timeout_s=90, run=fake)
    assert [p.name for p in pics] == ["r0_0.jpg", "r0_1.jpg", "r0_2.jpg"]
    cmd, cwd, timeout = ran[0]
    assert Path(cmd[1]).as_posix().endswith("threed/render.py") and "--preview" in cmd and cwd == str(home / "threed") and timeout == 90
    with pytest.raises(RuntimeError, match="designed-cast kit"):
        lk.render_designed(tmp_path / "old", style, out, run=fake)


def test_a_rendered_style_is_judged_against_the_channels_3d_scenes_not_its_flat_graphics(tmp_path):
    navy = jpeg(tmp_path / "navy.jpg", (14, 22, 52))
    scene = tmp_path / "scene.jpg"
    img = Image.new("RGB", (320, 180), (90, 150, 220))
    for x in range(320):
        for y in range(90, 180):
            img.putpixel((x, y), (60 + x % 120, 160, 40 + y % 60))
    img.save(scene)
    assert lk.flat_graphic(navy) and not lk.flat_graphic(scene)
