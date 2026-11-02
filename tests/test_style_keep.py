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
