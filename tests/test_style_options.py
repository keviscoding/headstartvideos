"""Learned mixed-media styles offer every option; defaults and files stay as they were."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_style_options as options  # noqa: E402

CATALOG = [{"key": key} for key in ("youtube", "stock", "ai_images", "motion", "sound")] + [{"key": "threed", "explicit": True}]


def test_mixed_clones_offer_every_general_option_without_changing_defaults():
    styles = {
        "stickmen": {"cr_style_clone": {"visual_mode": "mixed"}, "features": {"offered": ["ai_images"], "on": ["ai_images"]}},
        "animated": {"cr_style_clone": {"visual_mode": "reference_only"}, "features": {"offered": ["motion", "sound"]}},
        "documentary": {"features": {"offered": ["youtube"]}},
        "no_on_list": {"cr_style_clone": {"visual_mode": "mixed"}, "features": {"offered": ["sound"]}},
    }
    assert sorted(options.widen(styles, CATALOG)) == ["no_on_list", "stickmen"]
    assert styles["stickmen"]["features"] == {"offered": ["ai_images", "youtube", "stock", "motion", "sound"], "on": ["ai_images"]}
    assert "threed" not in styles["stickmen"]["features"]["offered"], "3D needs a style built for it"
    assert styles["animated"]["features"] == {"offered": ["motion", "sound"]}, "reference animation keeps its list"
    assert styles["documentary"]["features"] == {"offered": ["youtube"]}, "built-in recipes are curated"
    # Without an "on" list everything offered would be on; today's defaults are pinned first.
    assert styles["no_on_list"]["features"]["on"] == ["sound"]
    assert options.widen(styles, CATALOG) == [], "widening twice changes nothing"


def test_install_widens_now_and_after_every_reload(monkeypatch):
    import types
    monkeypatch.setitem(sys.modules, "features", types.SimpleNamespace(FEATURES=CATALOG))
    table, pushed = {}, []

    def load_all():
        table.clear()
        table["stickmen"] = {"cr_style_clone": {"visual_mode": "mixed"}, "features": {"offered": ["ai_images"], "on": []}}
        return table

    load_all()
    studio = SimpleNamespace(styles=SimpleNamespace(STYLES=table, load_all=load_all,
                                                    apply_to_engine=lambda mv, motion: pushed.append(True)),
                             mv=None, motion=None)
    options.install(studio)
    assert "sound" in table["stickmen"]["features"]["offered"] and pushed == [True]
    studio.styles.load_all()
    assert "sound" in table["stickmen"]["features"]["offered"], "a reload from disk is widened again"
    options.install(studio)
    assert studio.styles.load_all._cr_widened
