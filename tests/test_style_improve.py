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
    again = clone.prepare_improvement(a.home, published.operation, "something else", a.proof, a.bridge)
    assert again["operation_id"] == op, "one change at a time per style"
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
