"""Improve this style on a style's study page: one change to a coded style's renderer in the creator's words, as its
own owned operation that publishes nothing; the style changes only when the creator saves the new version."""
import hashlib

import pytest

from tests.test_frontier_style_clone import approved  # noqa: F401 - the fixture
from tests.test_frontier_style_refine import published  # noqa: F401 - the fixture
import cr_animated2d as a2d
import cr_style_clone as clone
import cr_style_lab as lab

CODE = "Kit.renderer({name: 'mine', manifest: {actions: {place: '{}'}}, frame() {}});"


def _coded(published):
    """The published study's style, written in code (its renderer saved, the receipt kept in step)."""
    a = published.approved
    folder, saved = clone._saved(a.home, published.operation, a.bridge)
    style = clone._read(a.home / "styles" / (saved["spec"]["style_id"] + ".json"))
    target = a.home / "assets/styles/learned" / style["name"]
    target.mkdir(parents=True)
    (target / "renderer-v1.js").write_text(CODE)
    style["look"] = {"visuals": a2d.VISUALS, "animated2d": {"renderer": {
        "file": f"styles/learned/{style['name']}/renderer-v1.js", "version": 1, "state": "passed",
        "sha256": hashlib.sha256(CODE.encode()).hexdigest()}}}
    clone.republish(folder, saved, style)
    return style["name"]


def test_improve_this_style_is_its_own_operation_and_publishes_nothing(published, tmp_path):
    a = published.approved
    with pytest.raises(RuntimeError, match="written in code"):
        clone.prepare_improvement(a.home, published.operation, "rounder heads", a.proof, a.bridge)
    name = _coded(published)
    with pytest.raises(ValueError, match="few words"):
        clone.prepare_improvement(a.home, published.operation, "x", a.proof, a.bridge)
    first = clone.prepare_improvement(a.home, published.operation, "  rounder\n heads ", a.proof, a.bridge)
    assert first["state"] == "prepared" and first["renderer_improve"]
    assert first["improvement"]["request"] == "rounder heads" and first["improvement"]["state"] == "drawing"
    op = first["operation_id"]
    with clone._lock(clone._operation(a.home, op)):          # its worker is working (it holds the change's lock)
        with pytest.raises(RuntimeError, match="still making your last change"):
            clone.prepare_improvement(a.home, published.operation, "something else", a.proof, a.bridge)
        again = clone.prepare_improvement(a.home, published.operation, "rounder heads", a.proof, a.bridge)
        assert again["operation_id"] == op, "the same click again is that change"
    saved = clone._read(clone._operation(a.home, op) / "operation.json")
    assert "renderer" not in saved["spec"] and saved["spec"]["max_calls"] == clone.IMPROVE_MAX_CALLS
    assert saved["source_style"] == name and saved["source_renderer"]["version"] == 1
    before = (a.home / "styles" / f"{name}.json").read_bytes()

    def change(folder, spec, progress):
        progress("improve")
        out = lab.improvement_folder(a.home, name, folder.name)
        out.mkdir(parents=True)
        (tmp_path / "elsewhere.jpg").write_bytes(b"x")
        lab._write(out / "candidate.json", {"state": "ready", "request": "rounder heads",
                                            "stills": [str(tmp_path / "elsewhere.jpg")], "current": {"stills": []},
                                            "check": {"overall": 6.5, "bar": 5.5}, "warnings": ["small text"]})
    result = clone.run(a.home, op, a.bridge, readiness=lambda: a.proof, execute=change)
    assert result["state"] == "done" and "Compare it" in result["message"]
    assert result["improvement"]["state"] == "ready" and result["improvement"]["warnings"] == ["small text"]
    assert (a.home / "styles" / f"{name}.json").read_bytes() == before, "nothing is published"
    assert [p for n, p in a.events if n == "finish"][-1]["artifacts"][0]["name"] == "style-improvement.json"
    begin = [p for n, p in a.events if n == "begin"][-1]
    assert begin["title"].startswith("Improve style: ") and begin["max_calls"] == clone.IMPROVE_MAX_CALLS
    assert clone.improvement_pictures(a.home, op, a.bridge)["new"] == [], "never a file outside the style's lab"
    assert clone.status(a.home, published.operation, a.bridge)["renderer"]["improvement_id"] == op
    assert clone.keep_improvement(a.home, op, a.bridge, use_new=False)["improvement"]["state"] == "kept_old"
    assert (a.home / "styles" / f"{name}.json").read_bytes() == before
    following = clone.prepare_improvement(a.home, published.operation, "calmer camera", a.proof, a.bridge)
    assert following["operation_id"] != op, "a finished change makes room for the next"

    def stop(folder, spec, progress):
        raise clone.StudyStopped("Claude's change couldn't be drawn yet (no sky). Your style is unchanged.")
    with pytest.raises(clone.StudyStopped):
        clone.run(a.home, following["operation_id"], a.bridge, readiness=lambda: a.proof, execute=stop)
    failed = clone.status(a.home, following["operation_id"], a.bridge)
    assert failed["state"] == "failed" and "no sky" in failed["stop_reason"] and "retry_spec" not in failed
    assert failed["setup_review_required"] is False
    with pytest.raises(RuntimeError, match="Wait for the change"):
        clone.keep_improvement(a.home, following["operation_id"], a.bridge, use_new=True)


def test_a_change_studio_stopped_working_on_never_blocks_its_style(published):
    """The 108 review: a change left running (Studio quit mid-change) counted as active from its file alone, so every
    Improve returned it and Delete style was refused for good. With no worker holding its lock it stops, said plainly;
    one left validated is finished, its version waiting for the creator."""
    import cr_trash
    from types import SimpleNamespace
    a = published.approved
    name = _coded(published)
    first = clone.prepare_improvement(a.home, published.operation, "rounder heads", a.proof, a.bridge)
    folder, saved = clone._saved(a.home, first["operation_id"], a.bridge)
    saved.update(state="running", project_id="crv_" + "3" * 24, started_at=0)
    clone._write(folder / "operation.json", saved)
    studio = SimpleNamespace(JOBS={}, QUEUE=[])
    with clone._lock(folder):
        assert "making a change" in cr_trash._style_busy(studio, a.home, name), "a working change is in the way"
        assert clone.settle_improvement(a.home, first["operation_id"], a.bridge)["state"] == "running"
    assert cr_trash._style_busy(studio, a.home, name) == "", "a change nobody is working on is not"
    settled = clone.settle_improvement(a.home, first["operation_id"], a.bridge)
    assert settled["state"] == "failed"
    shown = clone.status(a.home, first["operation_id"], a.bridge)
    assert "Studio closed while Claude was making this change" in shown["stop_reason"]
    assert shown["setup_review_required"] is False
    assert [p for n, p in a.events if n == "finish"][-1]["status"] == "failed", "its project is closed, not left open"
    following = clone.prepare_improvement(a.home, published.operation, "calmer camera", a.proof, a.bridge)
    assert following["operation_id"] != first["operation_id"] and following["improvement"]["request"] == "calmer camera"
    # left validated (Studio quit just after drawing it): finished, never redone
    folder, saved = clone._saved(a.home, following["operation_id"], a.bridge)
    out = lab.improvement_folder(a.home, name, following["operation_id"])
    out.mkdir(parents=True)
    lab._write(out / "candidate.json", {"state": "ready", "request": "calmer camera", "current": {}})
    saved.update(state="validated", project_id="crv_" + "4" * 24)
    clone._write(folder / "operation.json", saved)
    assert clone.settle_improvement(a.home, following["operation_id"], a.bridge)["state"] == "complete"
    assert clone.status(a.home, following["operation_id"], a.bridge)["improvement"]["state"] == "ready"


def test_saving_a_change_never_undoes_a_newer_version(published):
    """The 108 review: a change made to version 1, saved after version 2 was saved in Style Lab, would have published a
    version built on 1 and quietly dropped 2."""
    a = published.approved
    name = _coded(published)
    op = clone.prepare_improvement(a.home, published.operation, "rounder heads", a.proof, a.bridge)["operation_id"]
    out = lab.improvement_folder(a.home, name, op)
    out.mkdir(parents=True)
    new = CODE.replace("mine", "rounder")
    (out / "renderer.js").write_text(new)
    lab._write(out / "candidate.json", {"state": "ready", "sha256": hashlib.sha256(new.encode()).hexdigest(),
                                        "current": {"sha256": hashlib.sha256(CODE.encode()).hexdigest(), "version": 1}})
    lab._write(lab.folder(a.home, name) / "lab.json", {"dims": 2, "history": []})
    folder, saved = clone._saved(a.home, published.operation, a.bridge)
    style = clone._read(a.home / "styles" / f"{name}.json")
    newer = CODE.replace("mine", "newer")
    (a.home / "assets/styles/learned" / name / "renderer-v2.js").write_text(newer)
    style["look"]["animated2d"]["renderer"].update(file=f"styles/learned/{name}/renderer-v2.js", version=2,
                                                   sha256=hashlib.sha256(newer.encode()).hexdigest())
    clone.republish(folder, saved, style)
    with pytest.raises(lab.LabError, match="made to version 1.*version 2 saved since"):
        lab.keep_candidate(a.home, name, op, use_new=True)
    assert clone._read(a.home / "styles" / f"{name}.json")["look"]["animated2d"]["renderer"]["version"] == 2
