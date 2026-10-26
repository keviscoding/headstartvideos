"""A sound cue the script writer slipped into the narration is not read aloud: "Growth sound." was voiced in a
1-minute story in a cloned style (2026-10-26). Sentences about a sound are the story and stay."""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_spoken  # noqa: E402


def test_the_customers_line_loses_only_its_cue():
    para = "Growth sound. Ten feet eight. I tie the banner at zero as noon strikes and confetti explodes. Pip cheers."
    assert cr_spoken.strip_cues(para) == ("Ten feet eight. I tie the banner at zero as noon strikes and confetti "
                                          "explodes. Pip cheers.")


def test_cues_go_but_story_sentences_about_sounds_stay():
    script = ("A strange sound came from the hull.\n\nWhoosh SFX. The door opened.\n\n"
              "Thunder sound effect. It rained for days.\n\nSounds.\n\nShe heard the sound again, louder.")
    assert cr_spoken.strip_cues(script) == ("A strange sound came from the hull.\n\nThe door opened.\n\n"
                                            "It rained for days.\n\nShe heard the sound again, louder.")


def test_a_script_without_cues_is_left_exactly_as_it_was():
    script = "Ten feet. \"Wait,\" she said. Then nothing.\n\nThe end?"
    assert cr_spoken.strip_cues(script) == script


def test_it_wraps_the_engines_cleaner_once():
    engine = types.SimpleNamespace(_clean_script=lambda t: t.replace("Here is the script:\n\n", ""))
    cr_spoken.install(engine)
    cr_spoken.install(engine)
    assert engine._clean_script("Here is the script:\n\nGrowth sound. Pip cheers.") == "Pip cheers."
    assert engine._clean_script._cr_spoken is True
