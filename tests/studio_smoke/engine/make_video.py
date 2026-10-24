"""A stand-in for the private engine's make_video module (see app.py): the functions Studio's adapters wrap when they
install, each refusing to run, and narration timing real enough for the smoke test's worker (Whisper's base model on
the CPU, int8, as the engine loads it)."""
from pathlib import Path

from stand_in import refuses

STYLE_INFO = {}
_WHISPER = None


def log(*parts, **_):
    print(*parts, flush=True)


def _get_whisper():
    global _WHISPER
    if _WHISPER is None:
        from faster_whisper import WhisperModel
        log("whisper: loading model 'base' (CPU, int8)")
        _WHISPER = WhisperModel("base", device="cpu", compute_type="int8")
    return _WHISPER


def _srt_ts(seconds):
    millis = int(round(max(0.0, float(seconds)) * 1000))
    return "%02d:%02d:%02d,%03d" % (millis // 3600000, millis // 60000 % 60, millis // 1000 % 60, millis % 1000)


def _image_provider():
    return ""                                       # no picture account: no keys in a smoke test


def _script_words_into_srt(srt: Path, script: str) -> int:
    srt.read_text(encoding="utf-8")                 # the engine reads the captions back as UTF-8
    return 0




# what Studio's adapters (cr_media, cr_render and the rest) wrap without checking first
for _name in ("image_gen", "_kie_still_with_url", "_tts_one", "_final_mux", "_sources_block", "generate_astrology_visuals", "publish_named_copy", "generate_script", "_extra_block", "generate_voiceover", "host_image", "_kie_upload_image"):
    globals()[_name] = refuses(_name)
