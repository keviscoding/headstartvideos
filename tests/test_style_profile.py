"""Style Profile: Gemini's logs are counted in code, checked against measurement, and doubts become questions."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_style_profile as sp  # noqa: E402
import cr_style_watch as sw  # noqa: E402


def shot(t, length, source, motion, art="painted stick figure", who="", audio="narration", text="none"):
    return {"t": t, "len_s": length, "source": source, "motion": motion, "art": art, "who": who, "audio": audio,
            "text": text}


STICK_LOG = {
    "narration": "voiceover",
    "shots": [shot("0:00", 2, "stick_figure", "zoom_in", who="Steve"), shot("0:02", 2, "stick_figure", "still"),
              shot("0:04", 2, "stick_figure", "cutout_move", who="Steve"), shot("0:06", 2, "map", "pan",
                                                                               art="hand-drawn map")],
    "music": {"share": 0.1, "role": "none", "moods": [], "under_voice": "none"},
    "sfx": {"density": "sparse", "kinds": ["whoosh", "pop"], "on": "characters moving"},
    "voice": {"gender": "male", "age": "adult", "accent": "american", "tone": "dry", "pace": "fast"},
    "look": {"grade": "bright", "grain": "none", "filter": "", "palette": ["#ffffff"]},
    "art_style": "Stick figures with round white heads on detailed watercolour backgrounds.",
    "recurring": [{"name": "Steve", "look": "stick figure in a red cap", "role": "mascot"}],
    "cta": [], "hook": "Opens on the mascot asking a question.", "breathing": "",
    "recognisable": ["red-cap stick mascot", "watercolour backgrounds"],
    "audio_events": [{"t": "0:03", "kind": "whoosh"}, {"t": "0:05", "kind": "foley"}],
}


def stick_reference(**extra):
    measures = {"edit": {"median_shot_s": 2.0, "cuts_per_min": 28, "motion_share": {"still": 0.3, "camera": 0.4,
                                                                                     "moving": 0.3},
                         "transitions": {"cut": 1.0}, "pops_per_min": 12},
                "opening": {"median_shot_s": 1.5}, "body": {"median_shot_s": 2.4},
                "look": {"luma": 190, "dark_share": 0.0, "saturation": 0.3, "flat": 0.6, "grain": 0.2,
                         "palette": [{"hex": "#FFFFFF", "share": 0.5}]},
                "sound": {"filled_pauses_share": 0.1, "bed_in_pauses_db": -48, "pause_fill": {"tonal": 0.1},
                          "pauses_per_min": {"0.3": 12, "0.6": 5, "1.5": 0.4}}}
    return {"id": "abcdefghijk", "title": "t", "channel": "c", "duration": 600, "measures": measures,
            "watch": {"windows": [{"span": [0, 180], "log": STICK_LOG}], "missing": []}, **extra}


def test_shares_are_counted_from_the_shot_log_not_taken_from_the_model():
    seen = sp.watched_summary({"windows": [{"span": [0, 180], "log": STICK_LOG}]})
    assert seen["sources"] == {"stick_figure": 0.75, "map": 0.25}
    assert seen["motion_class"] == {"camera": 0.5, "still": 0.25, "moving": 0.25}
    assert seen["recurring"][0]["name"] == "Steve" and seen["recurring"][0]["screen_share"] == 0.5
    assert seen["audio_events"]["whoosh"] > 0


def test_a_stick_figure_reference_becomes_a_profile_with_its_own_character_and_pace():
    profile = sp.build([stick_reference()])
    assert profile["pictures"]["art_style"]["value"].startswith("Stick figures with round white heads")
    assert profile["pictures"]["characters"]["value"][0]["look"] == "stick figure in a red cap"
    assert profile["edit"]["median_shot_s"]["value"] == 2.0
    assert profile["edit"]["median_shot_s"]["opening"] == 1.5
    assert profile["look"]["grade"]["value"] == "bright"
    assert profile["sound"]["music"]["value"] == "none" and profile["sound"]["music"]["confidence"] >= 0.9, \
        "measured silence in the pauses and Gemini's 'no music' agree"
    assert profile["sound"]["sfx"]["value"] == "sparse"
    assert profile["motion"]["classes"]["agreement"] > 0.7
    assert {q["id"] for q in profile["questions"]} == {"host"}, "a recurring mascot is offered to the creator"


def test_disagreeing_references_lower_confidence_and_ask():
    dark = stick_reference(id="bbbbbbbbbbb")
    dark["measures"]["look"]["luma"] = 40
    log = dict(STICK_LOG, look={"grade": "dark"}, art_style="Glossy 3D renders with neon light.")
    log["shots"] = [dict(s, art="glossy 3d render") for s in STICK_LOG["shots"]]
    dark["watch"] = {"windows": [{"span": [0, 180], "log": log}], "missing": []}
    profile = sp.build([stick_reference(), dark])
    assert profile["look"]["grade"]["confidence"] < 0.6
    asked = {q["id"] for q in profile["questions"]}
    assert "art_style" in asked or profile["pictures"]["art_style"]["alternatives"]


def test_measurement_alone_still_gives_a_profile_and_says_what_was_not_watched():
    ref = stick_reference()
    ref["watch"] = {"windows": [], "missing": ["0:00-3:00: style watching isn't available"]}
    profile = sp.build([ref])
    assert profile["edit"]["median_shot_s"]["value"] == 2.0
    assert profile["pictures"]["art_style"]["value"] == "" and profile["pictures"]["art_style"]["confidence"] == 0
    assert profile["references"][0]["not_watched"] == ["0:00-3:00: style watching isn't available"]


def test_measured_loudness_decides_how_much_music_there_is():
    ref = stick_reference()
    ref["measures"]["sound"] = {"filled_pauses_share": 0.95, "bed_in_pauses_db": -14, "pause_fill": {"tonal": 0.9}}
    music = sp.build([ref])["sound"]["music"]               # Gemini heard none; the pauses are full of music
    assert music["level"] == "score" and music["value"] != "none" and music["confidence"] < 0.9
    loud_log = dict(STICK_LOG, music={"share": 1.0, "role": "ambient bed", "moods": ["calm"]})
    quiet = stick_reference()                                # Gemini hears music everywhere; the pauses are silent
    quiet["watch"] = {"windows": [{"span": [0, 180], "log": loud_log}], "missing": []}
    music = sp.build([quiet])["sound"]["music"]
    assert music["level"] == "none" and music["value"] == "none" and music["moods"] == ["calm"]


def test_music_levels():
    assert sp.music_level(-14, 1.0, 0.9) == "score"
    assert sp.music_level(-30, 0.9, 0.8) == "bed"
    assert sp.music_level(-40, 0.6, 0.8) == "quiet"
    assert sp.music_level(-50, 0.9, 0.8) == "none" and sp.music_level(-14, 0.1, 0.9) == "none"
    assert sp.music_level(None, None, None) is None


def test_music_is_asked_about_only_when_nothing_could_measure_it():
    ref = stick_reference()
    ref["measures"]["sound"] = {}
    ref["watch"] = {"windows": [], "missing": ["not watched"]}
    assert "music" in {q["id"] for q in sp.build([ref])["questions"]}


def test_windows_cover_the_opening_and_the_middle():
    assert sw.windows(100) == [(0, 100)]
    assert sw.windows(1200) == [(0, 180), (540, 630)]
    assert sw.windows(0) == [(0, 180)]


def test_watching_waits_for_jobs_and_carries_on_without_them():
    calls, polls = [], {"n": 0}

    def call(path, payload=None):
        calls.append((path, payload))
        if payload is not None:
            return {"job": f"sw_{payload['start']}", "status": "running"}
        polls["n"] += 1
        if path.endswith("sw_0"):
            return {"job": "sw_0", "status": "done", "log": STICK_LOG}
        return {"job": "sw_540", "status": "failed", "error": "video unavailable"}
    out = sw.watch("abcdefghijk", 1200, call=call, log=lambda m: None, sleep=lambda s: None)
    assert [w["span"] for w in out["windows"]] == [[0, 180]]
    assert out["missing"] == ["9:00-10:30: video unavailable"]


def test_a_daily_limit_stops_asking_for_more_windows():
    def call(path, payload=None):
        raise RuntimeError("You've used today's style watching.")
    out = sw.watch("abcdefghijk", 1200, call=call, log=lambda m: None, sleep=lambda s: None)
    assert out["windows"] == [] and len(out["missing"]) == 1


V2_SHOT = {"t": "0:00", "len_s": 4, "source": "ai_image", "style": "candid phone photo, natural kitchen light",
           "what": "butcher holds a roast", "polish": "candid", "motion": "zoom_in", "text": "none", "who": "Frank",
           "audio": "narration"}


def v2_log(**extra):
    log = {"narration": "voiceover", "shots": [V2_SHOT, dict(V2_SHOT, t="0:04", source="presenter", motion="live_action")],
           "music": {"share": 0.6, "role": "lofi", "genre": "lofi hip-hop", "moods": ["calm"]},
           "sfx": {"density": "none"}, "ambience": {"present": True, "kinds": ["rain", "birds"], "share": 0.5,
                                                     "where": "in pauses"},
           "polish": {"overall": "candid", "notes": "phone-camera snapshots"}, "lettering": "hand-drawn",
           "presenter": {"on_camera_share": 0.09, "pictures_share": 0.4},
           "recurring": [{"name": "Frank", "look": "older man, white beard, green sweater, apron", "role": "presenter"}],
           "art_style": "Candid smartphone photos of an older butcher at home.", "cta": [], "recognisable": []}
    log.update(extra)
    return log


def test_prompt_v2_fields_reach_the_profile():
    sponsor = v2_log(shots=[dict(V2_SHOT, source="motion_graphic", polish="clean")],
                     cta=[{"t": "8:10", "kind": "ebook or pdf", "what": "ebook cover and a QR code"}])
    ref = stick_reference()
    ref["watch"] = {"windows": [{"span": [0, 180], "purpose": "opening", "log": v2_log()},
                                {"span": [480, 555], "purpose": "cta", "log": sponsor}], "missing": []}
    profile = sp.build([ref])
    assert profile["pictures"]["polish"]["value"] == "candid"
    assert profile["pictures"]["sources"]["value"] == {"ai_image": 0.5, "presenter": 0.5}, \
        "the call-to-action window adds its promotion, not its pictures"
    assert profile["format"]["cta"]["kinds"] == ["ebook or pdf"]
    assert profile["format"]["presenter"]["value"] == 0.09
    assert profile["text"]["lettering"]["value"] == "hand-drawn"
    assert profile["sound"]["ambience"]["present"] is True and profile["sound"]["ambience"]["value"] == ["rain", "birds"]
    assert profile["sound"]["music"]["genres"] == ["lofi hip-hop"]


def test_a_call_to_action_in_the_captions_gets_its_own_window():
    transcript = [(12, "welcome back"), (95, "subscribe for more"), (742, "I put together a free guide, link below"),
                  (760, "scan the QR code on screen")]
    assert sw.cta_times(transcript) == [742.0]
    spans = sw.windows(1500, [742.0])
    assert spans[0] == (0, 180) and spans[1] == (675, 765) and len(spans) == 2, "742 s is already in the middle window"
    spans = sw.windows(1500, [1100.0])
    assert spans[2] == (1085, 1160)


def test_watch_labels_each_window_by_why_it_was_watched():
    def call(path, payload=None):
        if payload is not None:
            return {"job": f"sw_{payload['start']}", "status": "done", "log": STICK_LOG}
    out = sw.watch("abcdefghijk", 1500, call=call, log=lambda m: None, sleep=lambda s: None,
                   transcript=[(1100, "today's sponsor is a free guide, link below")])
    assert [w["purpose"] for w in out["windows"]] == ["opening", "middle", "cta"]
