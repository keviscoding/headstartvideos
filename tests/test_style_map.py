"""From a Style Profile to Studio settings: the right base, the learned channel's own look, and no inherited cast."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_style_map as cm  # noqa: E402

STYLES = {
    "stories": {"look": {"character": "THE MAIN CHARACTER is the man from the reference image",
                         "character_image": "characters/stories.png", "reference": "styles/stories.jpg",
                         "still_suffix": " Flat 2D cartoon"}, "features": {"offered": ["depth", "avatar"], "on": ["depth"]},
                "pacing": {"photo_every_s": 5.5}},
    "documentary": {"look": {}, "features": {"offered": ["youtube", "maps", "headlines", "motion", "sound"],
                                             "auto_own": ["ai_images"]}},
    "avatar-premium": {"look": {"avatar": {"provider": "pruna", "per_min": 24, "window_s": 10}},
                       "features": {"offered": ["ai_images", "avatar", "sound"]}},
    "3d": {"look": {}, "features": {"offered": ["threed", "maps", "sound"]}},
    "nocturne": {"look": {}, "features": {"offered": ["threed", "youtube", "maps", "sound"]}},
    "jung": {"look": {}, "features": {"offered": ["stock", "ai_images", "motion", "sound"]}},
}


def axis(value, confidence=0.8, **extra):
    return {"value": value, "confidence": confidence, "from": ["watched"], **extra}


def profile(families, sources=None, **over):
    p = {
        "pictures": {"sources": axis(sources or {}, families=families),
                     "art_style": axis("Stick figures with round white heads on watercolour backgrounds."),
                     "polish": axis("clean"), "characters": axis([])},
        "look": {"grade": axis("bright", luma=200, saturation=0.3, filter=[]), "grain": axis("none", measured=0.2),
                 "palette": axis([{"hex": "#D04A2C", "share": 0.1}, {"hex": "#1A1A1A", "share": 0.4}]),
                 "drawn": axis(0.6)},
        "motion": {"classes": axis({"still": 0.6, "camera": 0.3, "moving": 0.1}),
                   "kinds": axis({"still": 0.5, "zoom_in": 0.4, "animated": 0.1}),
                   "camera": axis({"zoom_in_share": 0.6, "zoom_out_share": 0.3})},
        "edit": {"median_shot_s": axis(2.4, opening=1.8, body=2.6), "transitions": axis({"cut": 0.95, "start": 0.05}),
                 "source_clips": axis(0.0), "hook": axis([])},
        "text": {"density": axis({"none": 0.8, "label": 0.2}), "lettering": axis("typeset"), "style": axis([]),
                 "graphics": axis([])},
        "sound": {"music": axis("none", level="none", bed_in_pauses_db=-60, moods=[], genres=[]),
                  "sfx": axis("sparse", kinds=["pop", "whoosh"]), "ambience": axis([], present=False)},
        "voice": axis({"gender": "male", "age": "adult", "tone": "dry, conversational", "pace": "fast"}),
        "format": {"presenter": axis(0.0, who=[]), "cta": axis([], kinds=[])},
        "questions": [],
    }
    for path, value in over.items():
        cur = p
        keys = path.split("__")
        for k in keys[:-1]:
            cur = cur[k]
        cur[keys[-1]] = value
    return p


def test_a_drawn_channel_builds_on_the_picture_recipe_and_drops_its_cast():
    plan = cm.plan_style(profile({"generated": 0.9, "graphic": 0.1}), STYLES)
    assert plan["base"] == "stories"
    removed = {r["path"] for r in plan["remove"]}
    assert {"look.character_image", "look.reference"} <= removed
    styled = cm.apply(STYLES["stories"], plan)
    assert styled["look"]["character"] == "" and "character_image" not in styled["look"]
    assert styled["pacing"]["photo_every_s"] == 2.6, "the measured pace, not the base's 5.5 s"
    assert "style_image" in plan["needs"]
    assert styled["look"]["image_model"] == "nano-banana-2-1", "drawn against the style drawing on Nano Banana 2.1"
    assert plan["overrides"]["look"]["fx"]["finish"] == {"weave": False, "vignette": "none"}, \
        "clean drawings never inherit the base recipe's vignette"
    assert any(r["path"] == "look.image_model" and r["value"] == "Nano Banana 2.1" for r in plan["settings"])


def test_drawn_pictures_that_move_slide_as_cut_outs_rather_than_ai_video():
    moving = profile({"generated": 0.9}, **{"motion__classes": axis({"still": 0.4, "moving": 0.6})})
    plan = cm.plan_style(moving, STYLES)
    on = plan["overrides"]["features"]["on"]
    assert "depth" in on
    assert plan["overrides"]["pacing"]["clips_per_min"] <= 1.0
    assert plan["overrides"]["look"]["photofx"] == {"depth_every": 2, "depth_subjects": "any"}


def test_stills_only_channels_get_no_moving_pictures():
    still = profile({"generated": 0.9}, **{"motion__classes": axis({"still": 0.96, "moving": 0.04}),
                                           "motion__kinds": axis({"animated": 0.9})})
    plan = cm.plan_style(still, STYLES)
    assert plan["overrides"]["pacing"]["clips_per_min"] == 0.0, "measured stillness beats a model calling it animated"
    assert "depth" not in plan["overrides"]["features"]["on"]


def test_photoreal_pictures_that_move_get_image_to_video():
    photo = profile({"generated": 0.8}, **{"motion__classes": axis({"still": 0.3, "moving": 0.7}),
                                           "look__drawn": axis(0.1),
                                           "pictures__art_style": axis("Cinematic photoreal AI footage of cities.")})
    plan = cm.plan_style(photo, STYLES)
    assert plan["overrides"]["pacing"]["clips_per_min"] >= 3


def test_a_talking_presenter_channel_gets_its_measured_screen_time_and_product():
    p = profile({"generated": 0.5, "footage": 0.35, "presenter": 0.15},
                {"ai_image": 0.5, "stock_footage": 0.1, "presenter": 0.15},
                **{"format__presenter": axis(0.09, who=[{"name": "Frank", "look": "older man, white beard", "role": "presenter"}]),
                   "format__cta": axis([{"kind": "ebook or pdf"}], kinds=["ebook or pdf", "qr code"]),
                   "pictures__art_style": axis("Realistic photos of an older butcher at home."),
                   "look__drawn": axis(0.1)})
    plan = cm.plan_style(p, STYLES)
    assert plan["base"] == "avatar-premium"
    assert plan["overrides"]["look"]["avatar"]["per_min"] == pytest.approx(5.4)
    assert plan["overrides"]["look"]["avatar"]["provider"] == "pruna", "the base's presenter machinery is kept"
    assert plan["overrides"]["look"]["offer"]["at"] == [0.4, 0.7]
    assert {"presenter", "product"} <= set(plan["needs"])
    assert any(n.get("polish_rule", "").startswith("candid") for n in plan["notes"]), \
        "photoreal AI pictures get the everyday phone look, not a glossy advert"


def test_real_footage_channels_build_on_the_documentary_with_their_own_clips():
    crime = profile({"footage": 0.8, "graphic": 0.2}, {"bodycam_cctv": 0.45, "archival_footage": 0.35, "map": 0.05},
                    **{"edit__source_clips": axis(0.5), "edit__hook": axis(["Opens on raw bodycam footage."]),
                       "look__grade": axis("dark", luma=70, saturation=0.14, filter=["desaturated, dark"]),
                       "sound__music": axis("ambient bed", level="score", bed_in_pauses_db=-20, moods=["tense", "eerie"],
                                            genres=[]),
                       "sound__sfx": axis("none", kinds=[]),
                       "pictures__art_style": axis("Raw bodycam footage.")})
    plan = cm.plan_style(crime, STYLES)
    ov = plan["overrides"]
    assert plan["base"] == "documentary"
    assert ov["look"]["soundbites"]["quotes"] >= 10 and ov["pacing"]["cold_open_s"] > 0
    assert ov["look"]["soundbites"]["montage"] is False, "raw footage opens it, never commentators saying the name"
    assert ov["look"]["fx"]["grade"] == "bleach_bypass"
    assert ov["look"]["sound"]["music"] is True and ov["look"]["sound"]["sfx_db"] == -60.0
    assert ov["look"]["sound"]["vibe"] == "tense_investigation"
    assert "maps" in ov["features"]["on"] and "youtube" in ov["features"]["on"]
    assert "spotlight" in ov["features"]["on"], "real photos of who is named fill what footage can't show"
    assert ov["pacing"]["mix"] == [0, 80, 20], "bodycam and archive footage fill the footage slots, not graphics"
    assert "character_image" not in plan["needs"], "real people in footage are filmed, not designed"
    assert "auto_own" in {r["path"].split(".")[-1] for r in plan["remove"]}


def test_3d_and_measured_music_levels():
    p = profile({"3d": 0.6, "graphic": 0.4}, **{"sound__music": axis("story score", level="score",
                                                                         bed_in_pauses_db=-16, moods=["tense"],
                                                                         genres=[])})
    plan = cm.plan_style(p, STYLES)
    assert plan["base"] == "3d"
    assert plan["overrides"]["look"]["sound"]["music_rel_db"] == -12.0
    assert cm.plan_style(profile({"3d": 0.5, "footage": 0.3}), STYLES)["base"] == "nocturne"


def test_hand_lettering_and_subtitles_follow_the_references():
    p = profile({"generated": 0.9}, **{"text__lettering": axis("comic"),
                                       "text__density": axis({"caption": 0.5, "none": 0.5})})
    ov = cm.plan_style(p, STYLES)["overrides"]
    assert ov["look"]["captions"] == {"off": False, "font": "PatrickHand"}
    assert ov["look"]["textfx"]["fonts"]["head"] == "Bangers", "comic lettering gets comic display letters"
    marker = profile({"generated": 0.9}, **{"text__lettering": axis("hand-drawn"),
                                            "text__density": axis({"caption": 0.5, "none": 0.5})})
    assert cm.plan_style(marker, STYLES)["overrides"]["look"]["textfx"]["fonts"]["head"] == "Caveat"


def test_every_setting_says_why():
    plan = cm.plan_style(profile({"generated": 0.9}), STYLES)
    assert plan["settings"] and all(row["why"] and row["label"] for row in plan["settings"])
    assert plan["cost"]["usd_per_min"] > 0


def test_a_base_the_creator_chose_is_kept_and_a_missing_one_refused():
    assert cm.plan_style(profile({"generated": 0.9}), STYLES, base="jung")["base"] == "jung"
    with pytest.raises(ValueError):
        cm.plan_style(profile({"generated": 0.9}), STYLES, base="folio")


def test_apply_keeps_the_base_unchanged():
    plan = cm.plan_style(profile({"generated": 0.9}), STYLES)
    before = str(STYLES["stories"])
    cm.apply(STYLES["stories"], plan)
    assert str(STYLES["stories"]) == before


def test_a_character_who_carries_the_video_is_offered_as_the_channels_mascot():
    import cr_style_profile as sp
    tom = {"name": "Tom", "look": "curly dark hair, light blue shirt, grey trousers", "role": "character",
           "screen_share": 0.51}
    p = profile({"generated": 0.9}, **{"pictures__characters": axis([tom])})
    asked = [q for q in sp.questions(p) if q["id"] == "host"]
    assert asked and asked[0]["lead"] == "Tom" and "51%" in asked[0]["ask"]
    assert "character_image" not in cm.plan_style(p, STYLES)["needs"], "not without the creator's yes"
    plan = cm.plan_style(p, STYLES, answers={"host": "Yes, design one for my channel"})
    assert "character_image" in plan["needs"]
    assert p["pictures"]["characters"]["value"][0]["role"] == "character", "the measured profile is kept as it was"
    assert "character_image" not in cm.plan_style(p, STYLES, answers={"host": "No, a new cast for each story"})["needs"]


def test_candid_photo_pictures_get_the_phone_camera_finish():
    candid = profile({"generated": 0.8}, **{"pictures__polish": axis("clean"), "look__drawn": axis(0.05),
                                           "pictures__art_style": axis("Realistic photos of an older butcher at home.")})
    plan = cm.plan_style(candid, STYLES)
    assert plan["overrides"]["look"]["stills"] == {"realism": "home_video"}
    warm = profile({"generated": 0.8}, **{"pictures__polish": axis("clean"), "look__drawn": axis(0.05),
                                         "pictures__art_style": axis("Realistic photos of an older butcher at home."),
                                         "look__grade": axis("neutral", luma=120, saturation=0.3, filter=["warm tones"])})
    assert cm.plan_style(warm, STYLES)["overrides"]["look"]["fx"]["grade"] == "natural", "never graded like film"
    drawn = cm.plan_style(profile({"generated": 0.9}), STYLES)
    assert "stills" not in drawn["overrides"].get("look", {}), "drawn pictures are never degraded"
    collage = profile({"generated": 0.6}, **{"pictures__polish": axis("clean"), "look__drawn": axis(0.04),
                      "pictures__art_style": axis("Paper cutout collage with photographic cutouts on cutting mats.")})
    assert "stills" not in cm.plan_style(collage, STYLES)["overrides"].get("look", {}), "designed pictures stay crisp"


def test_a_channel_that_holds_its_pictures_still_is_not_pushed_on_every_one():
    held = profile({"generated": 0.9}, **{"motion__classes": axis({"still": 0.55, "camera": 0.05, "moving": 0.4})})
    assert cm.plan_style(held, STYLES)["overrides"]["look"]["zoom"] == "mix:0.92:alternate"
    frozen = profile({"generated": 0.9}, **{"motion__classes": axis({"still": 0.9, "camera": 0.0, "moving": 0.1})})
    assert cm.plan_style(frozen, STYLES)["overrides"]["look"]["zoom"] == "still"
    pushed = profile({"generated": 0.9}, **{"motion__classes": axis({"still": 0.05, "camera": 0.8, "moving": 0.15}),
                                             "motion__camera": axis({"zoom_in_share": 0.9, "zoom_out_share": 0.0})})
    assert cm.plan_style(pushed, STYLES)["overrides"]["look"]["zoom"] == "in"


def test_a_very_quiet_music_bed_stays_very_quiet():
    quiet = profile({"generated": 0.9}, **{"sound__music": axis("quiet background", level="quiet", bed_in_pauses_db=-42.6,
                                                                filled_pauses=0.4, moods=["calm"], genres=[])})
    snd = cm.plan_style(quiet, STYLES)["overrides"]["look"]["sound"]
    assert snd["music_rel_db"] == -30.0, "music heard in 40% of pauses: set from where it plays, not the silent median"
    assert snd["target"] == {"bed_in_pauses_db": -42.6, "pauses_filled": 0.4}
    heard = profile({"generated": 0.9}, **{"sound__music": axis("quiet background", level="quiet", bed_in_pauses_db=-42.6,
                                                                bed_when_heard_db=-36.0, filled_pauses=0.4,
                                                                moods=["calm"], genres=[])})
    assert cm.plan_style(heard, STYLES)["overrides"]["look"]["sound"]["music_rel_db"] == -32.0
    steady = profile({"generated": 0.9}, **{"sound__music": axis("quiet background", level="quiet", bed_in_pauses_db=-41.0,
                                                                 filled_pauses=0.9, moods=["calm"], genres=[])})
    assert cm.plan_style(steady, STYLES)["overrides"]["look"]["sound"]["music_rel_db"] == -37.0, \
        "a quiet bed in every pause stays that quiet"


def test_a_documentary_that_opens_on_a_broadcast_clip_keeps_its_first_seconds_for_footage():
    doc = profile({"footage": 0.4, "graphic": 0.5}, {"motion_graphic": 0.5, "news_clip": 0.1},
                  **{"edit__source_clips": axis(0.035),
                     "edit__hook": axis(["A cold broadcast clip asking an audience a question, then a jump cut."])})
    ov = cm.plan_style(doc, STYLES)["overrides"]
    assert ov["pacing"]["cold_open_s"] == 6.0, "however little of the rest plays with its own sound"
    assert "soundbites" not in ov.get("look", {})


def test_a_whiteboard_channel_draws_each_picture_on_instead_of_sliding_cut_outs():
    board = profile({"generated": 0.9}, **{"motion__classes": axis({"still": 0.3, "moving": 0.7}),
                                           "pictures__art_style": axis("Digital whiteboard animation with a human hand "
                                                                       "sketching black marker lines.")})
    plan = cm.plan_style(board, STYLES)
    ov = plan["overrides"]
    assert ov["look"]["reveal"] == "draw"
    assert "depth" not in ov["features"]["on"] and "photofx" not in ov["look"], "the drawing is the motion"
    assert ov["pacing"]["clips_per_min"] == 0.0
    stick = profile({"generated": 0.9}, **{"motion__classes": axis({"still": 0.3, "moving": 0.7})})
    assert "reveal" not in cm.plan_style(stick, STYLES)["overrides"]["look"]


def test_an_essay_channel_gets_its_measured_mix_of_pictures_footage_and_cards():
    essay = profile({"generated": 0.4, "footage": 0.33, "graphic": 0.27}, {"painting": 0.23, "stock_footage": 0.19},
                    **{"look__drawn": axis(0.3), "motion__classes": axis({"still": 0.6, "moving": 0.4})})
    plan = cm.plan_style(essay, STYLES, base="jung")
    assert plan["overrides"]["pacing"]["mix"] == [40, 33, 27]


def test_a_white_3d_channel_renders_its_3d_as_an_all_white_matte_world():
    white = profile({"3d": 0.5, "graphic": 0.5}, {"3d_render": 0.49},
                    **{"look__grade": axis("bright", luma=185, saturation=0.05, filter=[]),
                       "pictures__art_style": axis("Minimalist all-white matte 3D reconstructions with mannequins.")})
    plan = cm.plan_style(white, STYLES)
    assert plan["base"] == "3d" and plan["overrides"]["look"]["threed"]["preset"] == "white_studio"
    dark = profile({"3d": 0.5, "graphic": 0.5}, {"3d_render": 0.49},
                   **{"look__grade": axis("dark", luma=60, saturation=0.4, filter=[]),
                      "pictures__art_style": axis("Moody realistic 3D night recreations.")})
    assert "threed" not in cm.plan_style(dark, STYLES)["overrides"].get("look", {})


def test_an_explainer_acts_its_ideas_out_and_a_story_shows_each_line():
    explainer = profile({"generated": 0.26, "footage": 0.49, "graphic": 0.19, "document": 0.05},
                        **{"text__density": axis({"data": 0.31, "headline": 0.29, "quote": 0.05, "label": 0.04,
                                                  "none": 0.22})})
    plan = cm.plan_style(explainer, STYLES)
    assert plan["overrides"]["look"]["storyboard"] == "explain"
    story = cm.plan_style(profile({"generated": 0.9, "graphic": 0.1}), STYLES)
    assert story["base"] == "stories" and story["overrides"]["look"]["storyboard"] == "literal"
    documentary = profile({"footage": 0.8, "graphic": 0.2}, **{"text__density": axis({"none": 0.9, "label": 0.1})})
    assert "storyboard" not in (cm.plan_style(documentary, STYLES)["overrides"].get("look") or {}), \
        "a channel whose pictures set a mood keeps its recipe's storyboard"
