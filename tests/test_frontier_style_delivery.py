"""Reference-only films cover the real voice with their approved kit, no paid calls."""
import copy
import ast
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_style_delivery as delivery


@pytest.fixture
def studio(tmp_path, monkeypatch):
    prompt = tmp_path / "synthetic-private-prompt.txt"
    prompt.write_text("fixture [KIT_CONTRACT] [CREATOR_INSTRUCTIONS] [PREVIOUS_SCENES]\nTIMED NARRATION BEATS:\n[TIMED_BEATS]")
    monkeypatch.setattr(delivery, "PROMPT_FILE", prompt)
    source = tmp_path / "kit.js"
    source.write_text("function draw(){return 'fixture animation';}")
    style = {"name": "learned", "auto_style": False, "voice": {"id": "my-voice"},
             "prompts": {"script": "my-original-script-instructions"},
             "cr_style_clone": {"visual_mode": "reference_only"},
             "pacing": {"cut_s": 4, "mix": [14, 56, 30]},
             "features": {"on": ["youtube", "ai_images", "motion", "threed", "sound"]},
             "look": {"graphics": "kit", "kit": "learned_kit", "palette": {"ink": "#123"},
                      "avatar": {"id": "unrelated"}, "threed": {"fill": "auto"}}}
    calls, timeline, mux, render_calls = [], [], [], []
    def claude(prompt, model, **options):
        calls.append((prompt, model, options))
        rows = json.loads(prompt.split("TIMED NARRATION BEATS:\n", 1)[1])
        return json.dumps([{"beat": row["beat"], "type": "animated_scene",
                            "subject": row["narration"]} for row in rows])
    def normalise(kit, raw):
        return {**raw, "kit": kit} if raw.get("type") in {"animated_scene", "title"} else None
    def render(kit, jobs, **options):
        render_calls.append((kit, jobs, options))
        for _, path in jobs:
            path.write_bytes(b"rendered-fixture" * 1000)
    def final_mux(job, audio, srt, captions, **options):
        mux.append((captions, options))
        (job / "video.mp4").write_bytes(b"assembled-fixture")
    kits = SimpleNamespace(KITS={"learned_kit": {"js_path": str(source), "types": {"animated_scene": 5},
        "prompt": "dynamic learned kit contract", "palette": {"ink": "#123"}}},
        kit_for_style=lambda engine, style: "learned_kit", prompt_block=lambda name: "dynamic learned kit contract",
        _normalise=normalise, render=render, sound_marks=lambda name, scene: [])
    engine = SimpleNamespace(STYLE_INFO={"learned": style}, _extra_block=lambda: "my-brief", claude=claude,
        step=lambda value: None, MOTION_WORKERS=2,
        _parse_srt_full=lambda text: [(0, 6, "first subject"), (6, 12.5, "second subject")],
        _word_clock=lambda job, srt: None, _audio_dur=lambda path: 12.5,
        _save_motion_plan=lambda job, plan, segs: timeline.extend(plan),
        _render_astro_segments=lambda plan, *a, **kw: [row[1] for row in plan],
        _concat_segments=lambda files, output: output.write_bytes(b"visual"),
        _with_sound_design=lambda job, audio, plan, style: audio,
        _final_mux=final_mux, _keep_clean_visual=lambda job, style: None,
        _visuals_plugin=lambda name: "original-plugin", _maybe_avatar=lambda *a, **kw: "original-avatar")
    job = tmp_path / "job"
    job.mkdir()
    srt = job / "subs.srt"
    srt.write_text("real voice timing fixture")
    return SimpleNamespace(engine=engine, kits=kits, style=style, calls=calls, timeline=timeline,
                           mux=mux, renders=render_calls, job=job, srt=srt, source=source)


def test_reference_defaults_keep_learned_identity_and_script_voice(studio):
    studio.style["look"]["cinema"] = {"reference_finish": "learned"}
    studio.style["look"]["footage_grade"] = "learned-colour-treatment"
    before = copy.deepcopy(studio.style)
    assert delivery.configure_style(studio.style) is studio.style
    assert studio.style["features"] == {"offered": ["motion", "sound"], "on": ["motion", "sound"]}
    assert studio.style["pacing"]["mix"] == [0, 0, 100]
    assert studio.style["voice"] == before["voice"] and studio.style["prompts"] == before["prompts"]
    assert studio.style["look"]["kit"] == before["look"]["kit"]
    assert "avatar" not in studio.style["look"] and "threed" not in studio.style["look"]
    assert studio.style["look"]["cinema"] == before["look"]["cinema"]
    assert studio.style["look"]["footage_grade"] == before["look"]["footage_grade"]


def test_old_mixed_presets_are_unchanged(studio):
    studio.style["cr_style_clone"].pop("visual_mode")
    before = copy.deepcopy(studio.style)
    delivery.configure_style(studio.style)
    assert studio.style == before


def test_whole_voice_has_contiguous_learned_scenes_captions_and_timeline(studio):
    delivery.assemble(studio.engine, studio.job, studio.job / "audio.mp3", studio.srt,
                      "learned", False, True, kits=studio.kits)
    assert len(studio.calls) == 1 and studio.calls[0][1] == delivery.cr_claude.VISUAL_MODEL
    assert studio.calls[0][2]["provider"] == "claudecode"
    assert len(studio.timeline) == 4 and {row[0] for row in studio.timeline} == {"motion"}
    assert sum(row[3] for row in studio.timeline) == 12.5
    assert all("kit_reference" in row[1] and not row[2] for row in studio.timeline)
    assert studio.mux == [(True, {"add_qr": False, "vignette": False,
                                 "no_dust_ranges": [(0.0, 12.5)], "sub_center": False, "no_sub_ranges": []})]
    report = json.loads((studio.job / ".cr-reference2d/delivery.json").read_text())
    assert report["coverage_seconds"] == report["narration_seconds"] == 12.5


def test_saved_scenes_and_response_reused_without_new_claude_call(studio):
    for _ in range(2):
        delivery.assemble(studio.engine, studio.job, studio.job / "audio.mp3", studio.srt,
                          "learned", False, False, kits=studio.kits)
    assert len(studio.calls) == len(studio.renders) == 1


@pytest.mark.parametrize("reply,match", [("not-json", "incomplete"), ("[]", "uncovered"),
    ('[{"beat":1,"type":"none"}]', "uncovered")])
def test_incomplete_response_is_saved_and_never_retried(studio, reply, match):
    calls = []
    studio.engine.claude = lambda *a, **kw: calls.append(1) or reply
    for _ in range(2):
        with pytest.raises(RuntimeError, match=match):
            delivery.plan(studio.engine, studio.job, studio.srt, "learned", 12.5, kits=studio.kits)
    assert calls == [1]
    assert (studio.job / ".cr-reference2d/response-000.txt").read_text() == reply
    assert not studio.renders


@pytest.mark.parametrize("field", ["ground", "photo", "image_url", "video", "alpha"])
def test_external_media_and_alpha_overlays_are_rejected(studio, field):
    windows = delivery.beats([(0, 5, "narration")], 5, 5)
    with pytest.raises(RuntimeError, match="external media"):
        delivery._validate([{"beat": 1, "type": "animated_scene", field: "unrelated"}],
                           windows, "learned_kit", studio.kits)


def test_renderer_failure_stops_without_assembly_or_fallback(studio):
    def broken(*args, **kwargs):
        raise RuntimeError("fixture renderer failure")
    studio.kits.render = broken
    with pytest.raises(RuntimeError, match="fixture renderer failure"):
        delivery.assemble(studio.engine, studio.job, studio.job / "audio.mp3", studio.srt,
                          "learned", False, True, kits=studio.kits)
    assert not studio.timeline and not studio.mux
    assert (studio.job / ".cr-reference2d/plan.json").is_file()


def test_changed_kit_cannot_reuse_saved_response_or_video(studio):
    delivery.plan(studio.engine, studio.job, studio.srt, "learned", 12.5, kits=studio.kits)
    studio.source.write_text("changed kit")
    with pytest.raises(RuntimeError, match="different inputs"):
        delivery.plan(studio.engine, studio.job, studio.srt, "learned", 12.5, kits=studio.kits)
    assert len(studio.calls) == 1


def test_only_reference_mode_uses_plugin_and_never_unrelated_presenter(studio, monkeypatch):
    monkeypatch.setitem(sys.modules, "kits", studio.kits)
    delivery.install(studio.engine)
    delivery.install(studio.engine)
    assert studio.engine._visuals_plugin("learned") is delivery
    assert studio.engine._visuals_plugin("other") == "original-plugin"
    (studio.job / "style.txt").write_text("learned\n")
    final = studio.job / "video.mp4"
    assert studio.engine._maybe_avatar(final, studio.job) == final
    (studio.job / "style.txt").write_text("other\n")
    assert studio.engine._maybe_avatar(final, studio.job) == "original-avatar"


def test_missing_kit_stops_before_any_generation(studio, monkeypatch):
    monkeypatch.setitem(sys.modules, "kits", studio.kits)
    studio.source.unlink()
    delivery.install(studio.engine)
    with pytest.raises(RuntimeError, match="missing or incomplete"):
        studio.engine._visuals_plugin("learned")
    assert not studio.calls


def test_invalid_kit_scene_is_not_silently_dropped(studio):
    windows = delivery.beats([(0, 5, "narration")], 5, 5)
    with pytest.raises(RuntimeError, match="cannot draw"):
        delivery._validate([{"beat": 1, "type": "other-kit-style"}], windows,
                           "learned_kit", studio.kits)


def test_pinned_original_assembly_helpers_accept_delivery_arguments():
    path = Path("/Users/workcomputer/Downloads/ChannelRecipe studio/make_video.py")
    if not path.is_file():
        pytest.skip("The private original runtime is not installed on this computer.")
    functions = {node.name: node for node in ast.parse(path.read_text()).body
                 if isinstance(node, ast.FunctionDef)}
    for name, positional, keywords in [
        ("_word_clock", 2, set()), ("_save_motion_plan", 3, set()),
        ("_render_astro_segments", 3, {"white_fade", "style"}),
        ("_concat_segments", 2, set()), ("_with_sound_design", 4, set()),
        ("_keep_clean_visual", 2, set()),
        ("_final_mux", 4, {"add_qr", "vignette", "no_dust_ranges", "sub_center", "no_sub_ranges"}),
    ]:
        node = functions[name]
        parameters = [argument.arg for argument in node.args.args]
        assert len(parameters) >= positional
        assert keywords.issubset(set(parameters[positional:]))


def test_original_native_helper_preserves_visual_role_marker():
    path = Path("/Users/workcomputer/Downloads/ChannelRecipe studio/make_video.py")
    if not path.is_file():
        pytest.skip("The private original runtime is not installed here.")
    node = next(n for n in ast.parse(path.read_text()).body
                if isinstance(n, ast.FunctionDef) and n.name == "_claude_code_try")
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="fixture", stderr="")
    namespace = {"subprocess": SimpleNamespace(run=run, TimeoutExpired=RuntimeError),
                 "_claude_exe": lambda: "/fixture/claude", "CC_MODEL": "sonnet",
                 "CC_SYSTEM": "fixture", "CC_TIMEOUT": 900, "CC_CWD": "/fixture",
                 "_cc_env": lambda: {}, "_ClaudeError": RuntimeError}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    assert namespace["_claude_code_try"](delivery.cr_claude.VISUAL_MODEL, "fixture") == "fixture"
    assert "opus" in commands[0][commands[0].index("--model") + 1]
