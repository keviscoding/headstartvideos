"""Presentation never rewrites a creative recipe or advertises old card art."""
import copy
import hashlib
from pathlib import Path
import sys
from xml.etree import ElementTree

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_presentation as presentation


def test_builtin_display_names_preserve_identity_and_creative_fields():
    original = {"name": "3d", "engine": "private-original-engine", "label": "old name",
                "features": {"on": ["threed", "maps"]}, "look": {"kit": "private-kit"},
                "mix": [10, 20, 70], "custom": False, "sample": True}
    saved = copy.deepcopy(original)
    row = presentation.present(original)
    assert row["label"] == "3D Reconstruction"
    assert row["name"] == "3d" and row["engine"] == "private-original-engine"
    assert row["features"] == original["features"] and row["look"] == original["look"]
    assert row["mix"] == original["mix"] and row["sample"] is True
    assert row["art_is_conceptual"] is True and row["art_label"] == "Recipe artwork"
    row["features"]["on"].append("fixture")
    assert original == saved


def test_complete_builtin_mapping_is_unique_and_owned():
    expected = {"3d", "almanac", "atlas", "avatar-premium", "avatar", "courtside",
                "deep", "documentary", "dossier", "folio", "hacker", "history",
                "horizon", "hustle", "jung", "kinetic", "legend", "mentor",
                "modern-homes", "news", "nocturne", "pitmaster", "pulse", "quest",
                "stories", "workshop"}
    assert presentation.BUILTIN_IDS == expected
    rows = presentation.presentationCatalog([{"name": name} for name in sorted(expected)])
    assert len({row["label"] for row in rows}) == len(expected)
    assert all(row["capabilities"] and row["family"] and row["description"] for row in rows)
    assert all("Frontier" not in str(row) for row in rows)
    assert all(row["art_url"] == presentation.ART_BASE + "/" + row["name"] + ".svg" for row in rows)


def test_catalogue_does_not_inject_intentionally_hidden_variants():
    assert [row["name"] for row in presentation.presentation_catalog([{"name": "3d"}])] == ["3d"]
    for name in ("gaming", "grooming"):
        row = presentation.present({"name": name, "variant_of": "documentary"})
        assert row["variant_of"] == "documentary" and row["label"] != name
        assert presentation.asset(name).is_file()


@pytest.mark.parametrize("name", ["automatic", "auto", "ai-style", "ai_style"])
def test_automatic_alias_has_own_art_and_keeps_actual_engine_identity(name):
    row = presentation.present({"name": name, "engine": "documentary"})
    assert row["name"] == name and row["engine"] == "documentary"
    assert row["label"] == "Adaptive Documentary"
    assert row["art_url"].endswith("/automatic.svg")


def test_automatic_display_can_keep_documentary_id_without_replacing_plain_recipe():
    source = {"name": "documentary", "auto_style": True}
    assert presentation.present(source)["label"] == "Editorial Documentary"
    automatic = presentation.present(source, automatic=True)
    assert automatic["name"] == "documentary" and automatic["auto_style"] is True
    assert automatic["label"] == "Adaptive Documentary"
    assert automatic["art_url"].endswith("/automatic.svg")


def test_custom_name_preserved_and_actual_saved_metadata_defines_visual_policy():
    channel = {"name": "cr_clone_2a9fec0189ec", "label": "Ifverse · my 2D style",
               "description": "My creative direction", "custom": True,
               "features": {"on": ["motion", "sound"]}}
    info = {"cr_style_clone": {"visual_mode": "reference_only"}, "look": {"kit": "private"}}
    row = presentation.present(channel, info)
    assert row["label"] == channel["label"] and "learned animation" in row["description"]
    assert channel["description"] == "My creative direction"
    assert row["family"] == "Animation" and row["visual_policy"] == "reference_only"
    assert "no footage" in row["visual_policy_label"] and "Animation only" in row["capabilities"]
    assert row["art_url"].endswith("/custom.svg")
    assert row == presentation.present(channel, {channel["name"]: info})


def test_older_mixed_style_is_not_described_as_animation_only():
    channel = {"name": "cr_clone_old", "label": "My old style", "custom": True,
               "features": {"on": ["youtube", "ai_images", "motion"]}}
    row = presentation.present(channel, {"cr_style_clone": {"operation_id": "fixture"}})
    assert row["label"] == channel["label"]
    assert row["visual_policy"] == "recipe_defaults" and "Animation only" not in row["capabilities"]


def test_explicit_custom_name_does_not_get_replaced_by_builtin_label():
    row = presentation.present({"name": "3d", "custom": True, "label": "My channel's identity"})
    assert row["label"] == "My channel's identity" and row["art_url"].endswith("/custom.svg")


def test_unknown_future_recipe_gets_neutral_owned_art():
    row = presentation.present({"name": "new_recipe", "label": "Future visual recipe", "custom": False})
    assert row["label"] == "Future visual recipe" and row["art_url"].endswith("/custom.svg")
    assert presentation.asset("new_recipe.svg") == presentation.asset("custom")


@pytest.mark.parametrize("name", ["../cr-connection.json", "..", "3d.svg/../../private", "3d/", "/3d", "3d%2F..", "3d.svg?x=1", "", None])
def test_art_route_rejects_non_allowlist_paths(name):
    with pytest.raises(ValueError):
        presentation.asset(name)


def test_all_thirty_artworks_are_distinct_wellformed_local_illustrations():
    files = {path.stem: path for path in presentation.ART_ROOT.glob("*.svg")}
    assert set(files) == presentation.ART_IDS and len(files) == 30
    assert len({hashlib.sha256(path.read_bytes()).hexdigest() for path in files.values()}) == 30
    for name, artwork in files.items():
        root = ElementTree.fromstring(artwork.read_bytes())
        assert root.attrib["viewBox"] == "0 0 1280 720"
        assert root.attrib["role"] == "img"
        assert presentation.asset(name + ".svg") == artwork
        tags = [element.tag.split("}")[-1] for element in root.iter()]
        assert "rect" in tags and ({"path", "circle", "ellipse"} & set(tags))
        assert not ({"script", "image", "foreignObject", "text", "animate"} & set(tags))
        assert not any("href" in key or key.lower().startswith("on")
                       for element in root.iter() for key in element.attrib)


def test_symlink_cannot_turn_art_lookup_into_private_file_read(tmp_path, monkeypatch):
    art_root = tmp_path / "art"
    art_root.mkdir()
    secret = tmp_path / "private.svg"
    secret.write_text("private")
    (art_root / "3d.svg").symlink_to(secret)
    monkeypatch.setattr(presentation, "ART_ROOT", art_root)
    with pytest.raises(ValueError):
        presentation.asset("3d")


def test_reference_animation_public_copy_cannot_inherit_a_three_d_base_description():
    row={"name":"cr_clone_reference","label":"My illustrated story", "custom":True,
         "description":"Premium 3D documentary with real footage", "tagline":"Animated 3D reconstructions", "features":{"on":["motion","sound"]}}
    result=presentation.present(row,{"cr_style_clone":{"visual_mode":"reference_only"}})
    assert result["label"]==row["label"] and result["name"]==row["name"]
    assert result["description"]==result["tagline"] and "learned animation" in result["description"]
    assert "3D" not in result["description"] and "footage" not in result["description"]
    assert row["description"]=="Premium 3D documentary with real footage"


def test_display_copy_has_no_em_dashes_but_prompts_and_ids_are_untouched():
    catalog = {"channels": [{"name": "nocturne", "character": "He is in EVERY frame \u2014 always",
                             "points": ["FPV drone one-takes \u2014 through the clouds"],
                             "options_note": "Real footage \u2014 found on YouTube \u2014 trimmed"}],
               "prices": {"features": {"maps": {"note": "drawn on your computer \u2014 free"}}}}
    shown = presentation.plain_copy(catalog)
    row = shown["channels"][0]
    assert row["points"] == ["FPV drone one-takes, through the clouds"]
    assert row["options_note"] == "Real footage, found on YouTube, trimmed"
    assert shown["prices"]["features"]["maps"]["note"] == "drawn on your computer, free"
    assert row["character"] == catalog["channels"][0]["character"]
    assert row["name"] == "nocturne"
    assert presentation.plain_copy("\u2014 leading and trailing \u2014") == "leading and trailing"


def test_ready_made_recipes_have_their_own_names_and_the_nearest_artwork():
    rows = presentation.presentationCatalog([{"name": name} for name in sorted(presentation.READY_MADE_IDS)])
    assert len({row["label"] for row in rows}) == len(presentation.READY_MADE_IDS) == 14
    assert all(row["capabilities"] and row["family"] and row["description"] for row in rows)
    # the Shorts recipes (owner-only builds ship them) sit together in their own family
    shorts = {row["name"] for row in rows if row["family"] == "Shorts"}
    assert shorts == {"cr-shorts-3d", "cr-shorts-3d-dark", "cr-shorts-photo", "cr-shorts-ranking"}
    assert presentation.asset("cr-manga-recaps.svg") == presentation.ART_ROOT / "folio.svg"
    assert not presentation.READY_MADE_IDS & presentation.BUILTIN_IDS
