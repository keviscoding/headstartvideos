"""From a Style Profile to Studio settings: the right base, the learned channel's own look, and no inherited cast."""
import json
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


RECONSTRUCTION = axis("Realistic 3D reconstructions of real events with faceless mannequins and film light.")


def test_3d_and_measured_music_levels():
    p = profile({"3d": 0.6, "graphic": 0.4}, **{"sound__music": axis("story score", level="score",
                                                                         bed_in_pauses_db=-16, moods=["tense"],
                                                                         genres=[]),
                                                 "pictures__art_style": RECONSTRUCTION})
    plan = cm.plan_style(p, STYLES)
    assert plan["base"] == "3d" and plan["route"]["engine"] == "threed"
    assert plan["overrides"]["look"]["sound"]["music_rel_db"] == -12.0
    assert cm.plan_style(profile({"3d": 0.5, "footage": 0.3}, **{"pictures__art_style": RECONSTRUCTION}),
                         STYLES)["base"] == "nocturne"


IFOLOGY = {"pictures__art_style": axis("Bright low-poly 3D render: characters with round solid black heads and simple "
                                       "white cute faces, bright jackets, thin black stick legs, bright simple sets."),
           "pictures__characters": axis([{"name": "Runner", "look": "round black head, simple white face, yellow "
                                          "jacket, black stick legs", "role": "mascot", "screen_share": 0.4}]),
           "motion__classes": axis({"still": 0.2, "camera": 0.2, "moving": 0.6})}
# a designed cast the designed-cast kit doesn't build (blocky game avatars): still drawn in its own look
BLOCKY = {"pictures__art_style": axis("Roblox-style blocky avatars with yellow block heads and decal faces, bright "
                                      "simple sets."),
          "pictures__characters": axis([{"name": "Noob", "look": "yellow block head with a smiley decal, blue torso, "
                                         "green legs", "role": "mascot", "screen_share": 0.4}]),
          "motion__classes": axis({"still": 0.2, "camera": 0.2, "moving": 0.6})}


def test_a_designed_cast_the_3d_engine_builds_is_rebuilt_in_3d():
    """@ifologyfilms (round black heads, cute faces, bright jackets, stick legs, low-poly sets) was cloned onto the 3D
    engine's faceless mannequins, then drawn as pictures (Studio 72-76: 6/10, and its characters never moved). Studio
    79's 3D engine builds ball-headed casts itself: the 3D recipe in the designed look, the cast spec, no picture model."""
    plan = cm.plan_style(profile({"3d": 0.85, "graphic": 0.15}, {"3d_render": 0.85}, **IFOLOGY), STYLES)
    assert plan["base"] == "3d" and plan["route"]["engine"] == "designed" and "medium_gap" not in plan["route"]
    lt = plan["overrides"]["look"]["threed"]
    assert lt["preset"] == "designed" and lt["designed"]["plan"] == "orb"
    assert lt["designed"]["cast"][0] == {"name": "Runner", "role": "lead", "top": "jacket", "color": "#E8B320"}
    assert lt["share"] == 0.85 and lt["fill"] is False, "the channel's own graphics keep their share"
    on = plan["overrides"]["features"]["on"]
    assert "threed" in on and "ai_images" not in on and "motion" in on
    assert "image_model" not in cm.apply(STYLES["3d"], plan)["look"]
    assert all(row["why"] for row in plan["settings"])


def test_a_designed_3d_world_the_engine_cant_build_is_drawn_in_its_own_look_not_put_on_mannequins():
    """A designed cast the 3D engine can't build goes the drawn route: the picture recipe, the style drawing and the
    2.1 model, its cast's character sheet, moving pictures, and the 3D engine off."""
    plan = cm.plan_style(profile({"3d": 0.85, "graphic": 0.15}, {"3d_render": 0.85}, **BLOCKY), STYLES)
    assert plan["base"] == "stories" and "drawn in their exact 3D-render look" in plan["base_why"]
    assert plan["route"]["engine"] == "drawn" and "mannequins" in plan["route"]["why"]
    on = plan["overrides"]["features"]["on"]
    assert "threed" not in on and "ai_images" in on
    assert {"style_image", "character_image"} <= set(plan["needs"])
    assert cm.apply(STYLES["stories"], plan)["look"]["image_model"] == "nano-banana-2-1"
    assert plan["overrides"]["pacing"]["clips_per_min"] > 0, "its characters move: animated pictures"


def test_a_3d_recipe_picked_for_a_designed_world_is_warned_about():
    plan = cm.plan_style(profile({"3d": 0.85}, {"3d_render": 0.85}, **BLOCKY), STYLES, base="3d")
    assert plan["base"] == "3d" and "faceless mannequins" in plan["route"]["warning"]
    built = cm.plan_style(profile({"3d": 0.85}, {"3d_render": 0.85}, **IFOLOGY), STYLES, base="3d")
    assert built["route"]["engine"] == "designed" and "warning" not in built["route"]


def test_a_3d_recipe_picked_for_a_designed_world_keeps_the_3d_engine():
    plan = cm.plan_style(profile({"3d": 0.85}, {"3d_render": 0.85}, **IFOLOGY), STYLES, base="3d")
    assert "threed" in plan["overrides"]["features"]["on"], "the creator's own choice is never switched off"


def test_what_the_3d_engine_can_build():
    assert cm.threed_rebuildable(profile({"3d": 0.6}, **{"pictures__art_style": RECONSTRUCTION}))[0]
    assert not cm.threed_rebuildable(profile({"3d": 0.6}, **IFOLOGY))[0]
    cast_only = profile({"3d": 0.6}, **{"pictures__art_style": axis("3D scenes."),
                                         "pictures__characters": IFOLOGY["pictures__characters"]})
    assert not cm.threed_rebuildable(cast_only)[0], "a recurring designed character is enough"


# Two real looks saved by Studio: reconstruction channels Gemini calls "low-poly" and "stylized", and whose story
# characters are faceless. Both are exactly what the 3D engine builds.
LOW_POLY = {"pictures__art_style": axis("Minimalist low-poly 3D reconstruction with featureless white and blue "
                                        "mannequin figures", alternatives=["stylized toy-like scale model"])}
MANNEQUIN_LEAD = {"pictures__art_style": axis("Stylized smooth 3D renders with faceless mannequin-like human figures",
                                             alternatives=["Photorealistic 3D character and environment CGI with moody "
                                                           "lighting, contrasted with flat 2000s desktop OS windows"]),
                  "pictures__characters": axis([{"name": "Jonathan", "role": "character", "screen_share": 0.288,
                                                 "look": "Featureless chrome/blank mannequin face, grey suit"}])}


def test_reconstruction_channels_stay_on_the_3d_engine():
    for over, families, base in ((LOW_POLY, {"3d": 0.6, "graphic": 0.4}, "3d"),
                                 (MANNEQUIN_LEAD, {"3d": 0.36, "footage": 0.23, "graphic": 0.2}, "nocturne")):
        plan = cm.plan_style(profile(families, **over), STYLES)
        assert plan["route"]["engine"] == "threed" and plan["base"] == base, over
        assert "threed" in plan["overrides"]["features"]["on"]
    host = profile({"3d": 0.5, "footage": 0.2}, **{"pictures__art_style": RECONSTRUCTION, "pictures__characters": axis(
        [{"name": "Host", "role": "presenter", "screen_share": 0.2, "look": "real man filmed talking to camera"}])})
    assert cm.plan_style(host, STYLES)["route"]["engine"] == "threed", "a filmed host is not a designed cast"


def test_the_route_shown_on_the_look_screen_never_flips_after_an_answer():
    built = profile({"3d": 0.6}, **{"pictures__art_style": axis("3D scenes with soft light."),
                                     "pictures__characters": IFOLOGY["pictures__characters"]})
    assert cm.plan_style(built, STYLES)["route"]["engine"] == "designed"
    plan = cm.plan_style(built, STYLES, answers={"host": "No host"})
    assert plan["route"]["engine"] == "designed" and plan["base"] == "3d", "removing the host never changes the route"
    cast_only = profile({"3d": 0.6}, **{"pictures__art_style": axis("3D scenes with soft light."),
                                         "pictures__characters": BLOCKY["pictures__characters"]})
    assert cm.plan_style(cast_only, STYLES)["route"]["engine"] == "drawn"
    plan = cm.plan_style(cast_only, STYLES, answers={"host": "No host"})
    assert plan["route"]["engine"] == "drawn" and plan["base"] == "stories" and "style_image" in plan["needs"]
    assert "threed" not in plan["overrides"]["features"]["on"]
    assert cm.apply(STYLES[plan["base"]], plan)["look"]["image_model"] == "nano-banana-2-1"


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


ROUTE_CASES = json.loads((Path(__file__).parent / "fixtures" / "route_3d_cases.json").read_text())["cases"]


def _case_profile(case):
    looks = [{"look": x, "share": 0.3} for x in case.get("shot_looks") or []]
    p = profile({"3d": 0.8, "graphic": 0.2}, {"3d_render": 0.8}, **{
        "pictures__art_style": axis(case["art_style"], alternatives=case.get("alternatives") or [], looks=looks),
        "pictures__characters": axis(case.get("characters") or [])})
    p["recognisable"] = case.get("recognisable") or []
    return p


@pytest.mark.parametrize("case", ROUTE_CASES, ids=lambda c: c["family"] + ":" + c["art_style"][:40])
def test_looks_saved_before_gemini_judged_the_3d_people_are_routed_by_their_words(case):
    """Studio 72's keywords sent 24 of the first 56 to the wrong engine (7 of 12 ifology wordings onto mannequins).
    The rest were written later to break the words; the few still missed go the drawn route, never onto mannequins."""
    if case.get("known_miss"):
        pytest.xfail(case["known_miss"])
    engine = cm.plan_style(_case_profile(case), STYLES)["route"]["engine"]
    assert engine == case["expected"], case["why"]


def test_geminis_own_verdict_on_the_3d_people_decides_over_the_words():
    faceless_words = {"pictures__art_style": axis("Low-poly cel-shaded 3D with black round featureless heads."),
                      "pictures__people_3d": axis({"designed": 0.8, "no_people": 0.2})}
    assert cm.plan_style(profile({"3d": 0.85}, **faceless_words), STYLES)["route"]["engine"] == "designed", \
        "Gemini saw faces on ball heads: the designed-cast kit builds them"
    seen_faceless = {**IFOLOGY, "pictures__people_3d": axis({"faceless": 0.9, "designed": 0.1})}
    assert cm.plan_style(profile({"3d": 0.85}, **seen_faceless), STYLES)["route"]["engine"] == "threed"


def test_small_white_eyes_are_not_an_all_white_world():
    tiny = profile({"3d": 0.8}, **{"pictures__art_style": axis("Low-poly 3D: figures with black ball heads and small "
                                                               "white eyes, bright jackets.")})
    assert not cm._white_3d(tiny) and cm.plan_style(tiny, STYLES)["route"]["engine"] == "designed"
    assert cm._white_3d(profile({"3d": 0.8}, **{"pictures__art_style": axis("All-white matte 3D world.")}))


def test_the_real_ifology_look_is_built_in_3d_whichever_way_gemini_words_its_heads():
    """Gemini's own words for @ifologyfilms on 2026-10-21, including one character it called "featureless"."""
    real = {"pictures__art_style": axis("Minimalist low-poly 3D cel-shaded animation featuring black-headed stick "
                                        "figures with simple dot eyes and flat-colored jackets, interspersed with sleek "
                                        "2D neon vector science diagrams."),
            "pictures__characters": axis([
                {"name": "Protagonist", "look": "Black sphere head, minimalist dot eyes/mouth, yellow zip jacket, black "
                 "stick limbs", "role": "character", "screen_share": 0.436},
                {"name": "Protagonist", "look": "black round featureless head with expressive white eye slits, gray "
                 "hoodie, black pants", "role": "character", "screen_share": 0.665}])}
    plan = cm.plan_style(profile({"3d": 0.868, "graphic": 0.111}, **real), STYLES)
    assert plan["base"] == "3d" and plan["route"]["engine"] == "designed"
    assert plan["overrides"]["look"]["threed"]["designed"]["cast"][0]["top"] == "hoodie", "the lead: most on screen"
    only_featureless = {**real, "pictures__art_style": axis("Low-poly cel-shaded 3D animation."),
                        "pictures__characters": axis([real["pictures__characters"]["value"][1]])}
    assert cm.plan_style(profile({"3d": 0.868}, **only_featureless), STYLES)["route"]["engine"] == "designed", \
        "eye slits are a face, even on a head Gemini calls featureless"


@pytest.mark.parametrize("words", [
    "Low-poly 3D reconstructions with grey mannequins with featureless white faces.",
    "Faceless 3D mannequins in real places, with simple features such as a nose ridge.",
    "Grey mannequin figures with blank expressions in a realistic 3D town.",
    "Faceless grey 3D figures in real places; iris wipe transitions between scenes.",
])
def test_words_about_a_blank_face_are_not_a_designed_cast(words):
    assert cm.plan_style(profile({"3d": 0.6}, **{"pictures__art_style": axis(words)}), STYLES)["route"]["engine"] \
        == "threed"


def test_a_designed_mascot_that_talks_to_camera_is_still_a_designed_cast():
    fox = {"pictures__art_style": axis("Bright stylized 3D animation in a cosy room."),
           "pictures__characters": axis([{"name": "Fox", "look": "3D cartoon fox mascot with big eyes who talks to "
                                          "camera", "role": "mascot", "screen_share": 0.5}])}
    assert cm.plan_style(profile({"3d": 0.8}, **fox), STYLES)["route"]["engine"] == "drawn"


def test_the_creators_answer_on_how_the_3d_is_made_decides_the_route():
    designed = profile({"3d": 0.85}, {"3d_render": 0.85}, **IFOLOGY)
    plan = cm.plan_style(designed, STYLES)
    q = next(q for q in plan["questions"] if q["id"] == "medium")
    assert q["options"][0] == cm.MEDIUM_DESIGNED and plan["route"]["engine"] == "designed" and "medium_gap" not in plan["route"]
    blocky = cm.plan_style(profile({"3d": 0.85}, {"3d_render": 0.85}, **BLOCKY), STYLES)
    assert blocky["route"]["medium_gap"] == cm.MEDIUM_GAP, "a cast the engine can't build is said before anything is paid"
    told = cm.plan_style(designed, STYLES, answers={"medium": cm.MEDIUM_FACELESS})
    assert told["route"]["engine"] == "threed" and "medium_gap" not in told["route"] and told["base"] == "3d"
    recon = profile({"3d": 0.6}, **{"pictures__art_style": RECONSTRUCTION})
    assert next(q for q in cm.plan_style(recon, STYLES)["questions"] if q["id"] == "medium")["options"][0] \
        == cm.MEDIUM_FACELESS
    said = cm.plan_style(recon, STYLES, answers={"medium": cm.MEDIUM_DESIGNED})
    assert said["route"]["engine"] == "drawn" and "you said" in said["route"]["why"] and said["base"] == "stories"
    flat = profile({"generated": 0.9})
    assert not any(q["id"] == "medium" for q in cm.plan_style(flat, STYLES)["questions"]), "only 3D channels are asked"


def test_a_3d_recipe_picked_by_hand_for_characters_with_faces_is_said_too():
    plan = cm.plan_style(profile({"3d": 0.85}, {"3d_render": 0.85}, **BLOCKY), STYLES, base="3d")
    assert plan["route"]["engine"] == "threed" and plan["route"]["medium_gap"] == cm.MEDIUM_GAP_MANNEQUINS
    told = cm.plan_style(profile({"3d": 0.6}, **{"pictures__art_style": RECONSTRUCTION}), STYLES, base="3d",
                         answers={"medium": cm.MEDIUM_DESIGNED})
    assert told["route"]["medium_gap"] == cm.MEDIUM_GAP_MANNEQUINS, "the creator said faces, the recipe makes mannequins"


# ── drawn characters the channel animates (@DartAnimates: stick-figure fights) ────────────────────────────────

def animated(moving=0.93, sources=None, narration="none", measured=True, **over):
    """A drawn 2D channel shaped like the measured @DartAnimates look: 88% stick figures, 93% of the runtime moving."""
    classes = {"value": {"moving": moving, "still": round(1 - moving, 3)}, "confidence": 0.9,
               "from": ["measured", "watched"] if measured else ["watched"]}
    return profile({"generated": 0.88, "graphic": 0.07}, sources or {"stick_figure": 0.88, "text_card": 0.04},
                   **{"motion__classes": classes, "format__narration": axis(narration), **over})


def test_a_channel_that_animates_its_drawn_characters_is_asked_and_a_medium_studio_cant_make_is_gated():
    """A @DartAnimates clone came out as AI stills with camera moves: the right look, and nothing moved."""
    dart = animated()
    assert cm.animated_2d(dart) == 1.0
    plan = cm.plan_style(dart, STYLES)
    question = next(q for q in plan["questions"] if q["id"] == "animation")
    assert question["options"] == [cm.ANIMATED_CODE, cm.ANIMATED_PICTURES], "Studio's guess first: animate them"
    assert plan["route"]["medium_gap"] == cm.ANIMATION_GAP, "never a silent swap to pictures"
    told = cm.plan_style(dart, STYLES, answers={"animation": cm.ANIMATED_PICTURES})["route"]
    assert "medium_gap" not in told and told["why"].startswith("you chose pictures")
    gated = cm.plan_style(dart, STYLES, answers={"animation": cm.ANIMATED_CODE})["route"]
    assert gated["medium_gap"] == cm.ANIMATION_GAP


def test_an_explainer_whose_figures_move_under_a_narrator_guesses_pictures_and_still_drawings_ask_nothing():
    # measured moving 0.64 on a stick-figure explainer with meme faces (narrated): asked, pictures first, no gate
    explainer = animated(0.64, {"stick_figure": 0.92}, narration="voiceover")
    question = next(q for q in cm.plan_style(explainer, STYLES)["questions"] if q["id"] == "animation")
    assert question["options"][0] == cm.ANIMATED_PICTURES
    assert "medium_gap" not in cm.plan_style(explainer, STYLES)["route"]
    # a short film with a few lines of voice: the animation carries it
    film = animated(0.69, {"drawing": 0.9}, narration="voiceover", voice=axis({}, wpm=19))
    assert cm.animation_guess(film) == cm.ANIMATED_CODE
    # stick-figure stories (measured moving 0.39): their figures slide as cut-outs, as before
    stories = animated(0.39, {"stick_figure": 0.93}, narration="voiceover")
    assert not cm.animated_2d(stories)
    assert all(q["id"] != "animation" for q in cm.plan_style(stories, STYLES)["questions"])


def test_footage_moving_beside_paintings_is_not_animation_and_a_model_alone_is_not_believed():
    essay = animated(0.37, {"painting": 0.23, "drawing": 0.17, "live_footage": 0.26, "stock_footage": 0.19},
                     narration="voiceover")
    assert not cm.animated_2d(essay), "the footage moves, not the paintings"
    # unmeasured (downloads blocked): a watching model calls nearly every drawn shot animated
    assert not cm.animated_2d(animated(0.9, narration="voiceover", measured=False))
    assert cm.animated_2d(animated(0.9, narration="none", measured=False)), "no narrator: the pictures carry it"


def test_a_3d_channel_is_asked_about_its_3d_not_about_2d_animation():
    p = animated(0.9, {"3d_render": 0.3, "stick_figure": 0.6})
    p["pictures"]["sources"]["families"] = {"3d": 0.3, "generated": 0.6}
    questions = [q["id"] for q in cm.plan_style(p, STYLES)["questions"]]
    assert "medium" in questions and "animation" not in questions
