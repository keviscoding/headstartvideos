"""A style study uses one of the plan's style clones only when its style is kept (Studio 124, owner 2026-11-02)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
sys.path.insert(0, str(Path(__file__).parent))
import cr_bridge  # noqa: E402
import cr_style_keep as keep  # noqa: E402

OP = "op_" + "a" * 24
STYLE = "cr_clone_war_maps"
PROJECT = "crv_" + "b" * 24


def study(home, *, version="5.3-admin.124", state=None, **extra):
    """A finished study of STYLE, as cr_style_clone saves it, with its style in styles/."""
    folder = Path(home) / "output" / "cr-style-clone" / OP
    folder.mkdir(parents=True, exist_ok=True)
    saved = {"operation_id": OP, "project_id": PROJECT, "state": "complete_local",
             "proof": {"local_version": version}, "spec": {"style_id": STYLE}, **extra}
    keep.start_trial(saved)
    if state:
        saved["keep"] = {"state": state, "since": 1}
    (folder / "operation.json").write_text(json.dumps(saved), encoding="utf-8")
    (Path(home) / "styles").mkdir(exist_ok=True)
    (Path(home) / "styles" / (STYLE + ".json")).write_text(
        json.dumps({"name": STYLE, "cr_style_clone": {"operation_id": OP}}), encoding="utf-8")
    return folder


class Bridge:
    def __init__(self, refuse=""):
        self.calls, self.refuse = [], refuse

    def call(self, path, payload=None):
        self.calls.append((path, payload))
        if self.refuse:
            raise RuntimeError(self.refuse)
        return {"kept": True, "style_clones": {"used": 1, "limit": 3, "remaining": 2}}


def test_a_new_study_of_a_channel_starts_on_trial_and_older_or_other_work_never_does(tmp_path):
    study(tmp_path)
    assert keep.style_on_trial(tmp_path, STYLE)
    for version, extra in [("5.3-admin.123", {}), ("5.3-admin.124", {"renderer_improve": True}),
                           ("5.3-admin.124", {"graphics_of": "cr_clone_x"})]:
        saved = {"proof": {"local_version": version}, **extra}
        keep.start_trial(saved)
        assert "keep" not in saved, (version, extra)
    kept = {"proof": {"local_version": "5.3-admin.124"}, "keep": {"state": "kept"}}
    keep.start_trial(kept)
    assert kept["keep"] == {"state": "kept"}, "a later save never puts a kept style back on trial"
    assert not keep.style_on_trial(tmp_path, "history"), "a recipe has no study"
    assert not keep.style_on_trial(tmp_path, "../styles"), "only style ids are looked up"


def test_keeping_counts_once_on_the_server_and_says_so(tmp_path):
    folder = study(tmp_path)
    bridge = Bridge()
    out = keep.keep(tmp_path, STYLE, bridge)
    assert bridge.calls == [("style-clones/keep", {"project_id": PROJECT})]
    assert out["style_clones"]["remaining"] == 2
    saved = json.loads((folder / "operation.json").read_text())
    assert saved["keep"]["state"] == "kept" and saved["keep"]["why"] == "kept"
    assert keep.keep(tmp_path, STYLE, bridge) == {"kept": True, "already": True}
    assert len(bridge.calls) == 1, "a kept style never asks again"
    assert keep.state(saved)["state"] == "kept"


def test_a_refused_keep_leaves_the_style_on_trial(tmp_path):
    study(tmp_path)
    with pytest.raises(RuntimeError, match="3 style clones"):
        keep.keep(tmp_path, STYLE, Bridge("Your Creator plan includes 3 style clones, and you've used 3."))
    assert keep.style_on_trial(tmp_path, STYLE)


def test_the_first_video_keeps_the_style_or_is_refused_with_the_way_out(tmp_path):
    study(tmp_path)
    with pytest.raises(ValueError) as refused:
        keep.before_video(tmp_path, STYLE, Bridge("Your Creator plan includes 3 style clones, and you've used 3."))
    assert "3 style clones" in str(refused.value) and "Discard a style" in str(refused.value)
    bridge = Bridge()
    keep.before_video(tmp_path, STYLE, bridge)
    keep.before_video(tmp_path, STYLE, bridge)
    keep.before_video(tmp_path, "history", bridge)
    assert len(bridge.calls) == 1, "kept by its first video, once; a recipe never asks"
    assert not keep.style_on_trial(tmp_path, STYLE)


def test_the_trial_says_what_keeping_and_discarding_cost(tmp_path):
    folder = study(tmp_path)
    said = keep.state(json.loads((folder / "operation.json").read_text()), {"remaining": 2})
    assert said["state"] == "trial" and "2 left" in said["say"] and "costs nothing" in said["say"]
    assert keep.state({}) == {}, "an older Studio's style says nothing"


def test_discarding_a_trial_style_moves_it_to_the_trash_and_says_no_clone_was_used(tmp_path, monkeypatch):
    import cr_trash
    study(tmp_path)
    plan = {"blocked": "", "title": "War maps", "goes": [], "moves": [], "kind": "style", "id": STYLE, "kept": []}
    monkeypatch.setattr(cr_trash, "style_plan", lambda studio, home, style: plan)
    monkeypatch.setattr(cr_trash, "_put_away", lambda home, kind, entry, moves: Path(home))
    monkeypatch.setattr(cr_trash, "_reload_styles", lambda studio: None)
    out = keep.discard(None, tmp_path, STYLE)
    assert out["message"].startswith("It was on trial, so it used none of your style clones.")
    study(tmp_path, state="kept")
    with pytest.raises(ValueError, match="Only a style on trial"):
        keep.discard(None, tmp_path, STYLE)


def test_the_library_card_and_the_tools_say_on_trial(tmp_path, monkeypatch):
    import cr_presentation
    import cr_studio_tools
    study(tmp_path)
    monkeypatch.setattr(cr_studio_tools, "HOME", tmp_path)
    real = keep.style_on_trial
    monkeypatch.setattr(keep, "style_on_trial", lambda home, style: real(tmp_path, style))
    row = cr_presentation.present({"name": STYLE, "custom": True}, {})
    assert row["on_trial"] and row["capabilities"][0] == cr_presentation.TRIAL_CHIP
    assert "on_trial" not in cr_presentation.present({"name": "history"}, {})
    card = cr_studio_tools.style_card({"name": STYLE, "custom": True})
    assert card["on_trial"] is True
    sent = []
    monkeypatch.setattr(cr_studio_tools, "same_studio", lambda: None)
    monkeypatch.setattr(cr_studio_tools, "call", lambda method, path, body=None, timeout=0: sent.append(path) or
                        {"kept": True, "style_clones": {"remaining": 1}})
    assert cr_studio_tools.tool_keep_style({"id": STYLE})["message"].endswith("(1 left).")
    assert sent == ["/cr-style-keep/" + STYLE]
    assert "keep_style" in cr_studio_tools.TOOLS


def test_a_video_with_a_trial_style_keeps_it_before_anything_is_queued(tmp_path, monkeypatch):
    import cr_submission
    from test_frontier_submission import studio_with_original
    study(tmp_path)
    monkeypatch.setattr(cr_submission, "HOME", tmp_path)
    refusing = Bridge("Your Creator plan includes 3 style clones, and you've used 3.")
    monkeypatch.setattr(cr_bridge, "call", refusing.call)
    studio, _, _ = studio_with_original()
    cr_submission.install(studio)
    client = studio.app.test_client()
    refused = client.post("/api/run", json={"title": "Battle of Hastings", "steps": ["video"], "style": STYLE})
    assert refused.status_code == 400 and "Discard a style" in refused.get_json()["error"]
    assert studio.QUEUE == [], "nothing queued when the keep is refused"
    kept = Bridge()
    monkeypatch.setattr(cr_bridge, "call", kept.call)
    assert client.post("/api/run", json={"title": "Battle of Hastings", "steps": ["video"],
                                         "style": STYLE}).status_code == 200
    assert kept.calls == [("style-clones/keep", {"project_id": PROJECT})] and len(studio.QUEUE) == 1


def test_the_trial_follows_the_release_a_study_was_begun_on(tmp_path):
    """Review 2026-11-02: a study prepared on 123 and begun on 124 is counted by ChannelRecipe as 124's."""
    prepared_on_123 = {"proof": {"local_version": "5.3-admin.123"}, "begun_version": "5.3-admin.124"}
    keep.start_trial(prepared_on_123)
    assert keep.on_trial(prepared_on_123)


def test_a_study_tells_its_begin_which_style_it_makes(tmp_path):
    assert keep.begin_options({"spec": {"style_id": STYLE}}) == {"learns_style": STYLE}
    graphics = {"spec": {"style_id": "cr_clone_war_maps_g"}, "graphics_of": OP, "source_style": STYLE}
    assert keep.begin_options(graphics) == {"learns_style": "cr_clone_war_maps_g", "counts_with": STYLE}
    assert keep.begin_options({"spec": {"style_id": STYLE}, "renderer_improve": True}) == {}
    assert keep.begin_options({"spec": {"style_id": "Not A Style"}}) == {}


def test_the_numbers_match_the_servers():
    sys.path.insert(0, str(Path(__file__).parents[1]))
    from webapp import frontier_studio
    assert keep.KEEPS_FROM == frontier_studio.KEEPS_ITS_STYLES
    assert keep.LEARNED.pattern == frontier_studio.LEARNED_STYLE + r"\Z"


def test_a_keep_made_meanwhile_is_never_written_back_to_trial(tmp_path):
    """Review: the likeness check runs for minutes after the style is saved and then writes its own copy of the
    study's state; a keep made in that window must survive it."""
    import cr_style_clone
    folder = study(tmp_path, other="before")
    stale = json.loads((folder / "operation.json").read_text())
    keep.keep(tmp_path, STYLE, Bridge())
    cr_style_clone._write(folder / "operation.json", {**stale, "likeness": "done"})
    now = json.loads((folder / "operation.json").read_text())
    assert now["keep"]["state"] == "kept" and now["likeness"] == "done"


def test_keeping_changes_nothing_else_in_the_study(tmp_path):
    import cr_style_clone
    folder = study(tmp_path)
    saved = json.loads((folder / "operation.json").read_text())
    stale_hash = {**saved, "style_sha256": "new"}
    cr_style_clone._write(folder / "operation.json", stale_hash)
    keep.keep(tmp_path, STYLE, Bridge())
    assert json.loads((folder / "operation.json").read_text())["style_sha256"] == "new"


def test_a_discarded_style_can_not_be_kept_until_it_is_restored(tmp_path):
    study(tmp_path)
    (tmp_path / "styles" / (STYLE + ".json")).unlink()
    with pytest.raises(ValueError, match="Restore it from Recently deleted"):
        keep.keep(tmp_path, STYLE, Bridge())


def test_the_graphics_copy_of_a_trial_style_keeps_its_source(tmp_path):
    """Review: Use pictures, then Study its graphics, made a copy that was never on trial: a free clone."""
    folder = study(tmp_path)
    copy_op = "op_" + "c" * 24
    copy_style = "cr_clone_" + "c" * 12
    copy = tmp_path / "output" / "cr-style-clone" / copy_op
    copy.mkdir(parents=True)
    copy_saved = {"operation_id": copy_op, "project_id": "crv_" + "d" * 24, "graphics_of": OP, "source_style": STYLE,
                  "proof": {"local_version": "5.3-admin.124"}, "spec": {"style_id": copy_style}}
    keep.start_trial(copy_saved)
    assert "keep" not in copy_saved, "the copy itself is never on trial"
    (copy / "operation.json").write_text(json.dumps(copy_saved), encoding="utf-8")
    (tmp_path / "styles" / (copy_style + ".json")).write_text(
        json.dumps({"name": copy_style, "cr_style_clone": {"operation_id": copy_op}}), encoding="utf-8")
    assert keep.style_on_trial(tmp_path, copy_style)
    bridge = Bridge()
    keep.before_video(tmp_path, copy_style, bridge)
    assert bridge.calls == [("style-clones/keep", {"project_id": PROJECT})], "the source study is kept"
    assert json.loads((folder / "operation.json").read_text())["keep"]["state"] == "kept"
    assert not keep.style_on_trial(tmp_path, STYLE) and not keep.style_on_trial(tmp_path, copy_style)


def test_a_refusal_says_what_actually_went_wrong(tmp_path):
    study(tmp_path)
    with pytest.raises(ValueError, match="another ChannelRecipe account"):
        keep.before_video(tmp_path, STYLE, Bridge("That style study wasn't found on your account."))
    with pytest.raises(ValueError) as offline:
        keep.before_video(tmp_path, STYLE, Bridge("Could not reach ChannelRecipe. Your work is saved; check your connection."))
    assert "couldn't record that" in str(offline.value) and "free style clone" not in str(offline.value)


def test_a_broken_style_file_never_breaks_the_library(tmp_path):
    (tmp_path / "styles").mkdir()
    (tmp_path / "styles" / (STYLE + ".json")).write_text(json.dumps({"cr_style_clone": "not a dict"}))
    assert keep.style_on_trial(tmp_path, STYLE) is False


def test_a_video_the_engine_refuses_never_keeps_the_style(tmp_path, monkeypatch):
    """Review: the keep ran before the engine's own checks, so a video refused there still used a clone."""
    import cr_submission
    from test_frontier_submission import studio_with_original
    study(tmp_path)
    monkeypatch.setattr(cr_submission, "HOME", tmp_path)
    bridge = Bridge()
    monkeypatch.setattr(cr_bridge, "call", bridge.call)
    studio, _, _ = studio_with_original()
    cr_submission.install(studio)
    refused = studio.app.test_client().post("/api/run", json={"title": "", "steps": ["video"], "style": STYLE})
    assert refused.status_code == 422 and bridge.calls == [] and keep.style_on_trial(tmp_path, STYLE)
