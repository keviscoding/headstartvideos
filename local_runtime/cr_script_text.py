"""Hindi (Devanagari) drawn right wherever Studio and the engine draw text with Pillow.

Customer Swapnil, 2026-10-29: "Add support for Hindi (India) language. (I needed it badly)". Captions, labels and
graphics are drawn by libass (ffmpeg) and Chromium, which shape Devanagari themselves. Pillow is the exception: the
thumbnail headline, the call-to-action card, chapter cards and a few labels go through ImageDraw.text, and Pillow
shapes complex scripts only with libraqm plus FriBiDi, which the Pillow wheels on the Mac and Windows don't carry
(features.check("raqm") is False on the owner's Studio). Without it Devanagari comes out wrong: the engine's fonts have
no Devanagari letters at all (boxes), and even a Devanagari font is drawn letter by letter, the ि vowel sign after its
consonant instead of before it and every conjunct broken into a visible virama (क्या as क्‌या).

So, for text with Devanagari in it only:
- the text is shaped by HarfBuzz (uharfbuzz, a pip wheel for every platform, in the requirements since this change),
  in Studio's own Devanagari font (CR Devanagari: Noto Sans Devanagari with Noto Sans's Latin letters, bold or
  regular like the font the caller asked for, the same size);
- each shaped glyph is drawn by Pillow itself through the font's private-use copy of it (U+F0000 + glyph id, made by
  scripts/build_devanagari_font.py), at HarfBuzz's position, so fills, strokes, anchors and every image mode behave as
  Pillow's own text does; strokes are drawn under all the letters first, then the letters, so a stroke never cuts
  into the headline bar of the letter before it;
- textlength, textbbox and FreeTypeFont.getlength/getbbox measure the same shaped text, so a headline fitted to a bar
  is fitted to what is drawn.
Text without Devanagari goes to Pillow exactly as before. Without uharfbuzz (an older Studio's libraries), the text is
still drawn in the Devanagari font, so its letters show (unshaped) instead of boxes, and the log says so once.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

HOME = Path(__file__).resolve().parent
PUA = 0xF0000                       # scripts/build_devanagari_font.py: glyph id g is also mapped at U+F0000 + g
FAMILY = "CR Devanagari"            # the fonts' family name, as libass (captions) looks it up
FILES = {False: "CRDevanagari-Regular.ttf", True: "CRDevanagari-Bold.ttf"}
SCRIPT = re.compile("[ऀ-ॿ꣠-ꣿ᳐-᳿]")     # Devanagari, its extension, Vedic marks
HEAVY = re.compile(r"bold|black|heavy|extrabold|semibold|anton|bebas|oswald|archivo|alfa|bangers|lilita|impact",
                   re.I)

_lock = threading.Lock()
_hb: dict = {}                      # font file -> (hb.Font, units per em)
_pil: dict = {}                     # (font file, size) -> Pillow FreeTypeFont
_said = {"no_shaper": False}
_original: dict = {}


def has_script(text) -> bool:
    """Whether a string has Devanagari in it (the only text this module ever handles)."""
    return isinstance(text, str) and bool(SCRIPT.search(text))


def folders() -> list:
    """Where Studio's Devanagari fonts are: the engine's font folder in an installed Studio (assets/fonts, where the
    release puts them and the captions' fontsdir points), else this repository's local_runtime/fonts."""
    return [HOME / "assets" / "fonts", HOME / "fonts"]


def font_file(bold: bool = True) -> Path | None:
    for folder in folders():
        path = folder / FILES[bool(bold)]
        if path.is_file():
            return path
    return None


def font_dir() -> Path | None:
    path = font_file(True)
    return path.parent if path else None


def _heavy(font) -> bool:
    """Whether the caller's font is a heavy one (bold, black, a display face): the Devanagari text is bold too."""
    name = " ".join(str(x) for x in ((getattr(font, "path", "") or ""),) + tuple(_safe_name(font)))
    return bool(HEAVY.search(name))


def _safe_name(font) -> tuple:
    try:
        return tuple(font.getname())
    except Exception:                                   # noqa: BLE001 - a font without names: its file name only
        return ()


def _size(font) -> float:
    return float(getattr(font, "size", 0) or 0)


def _usable(font) -> bool:
    """Only a TrueType font of a known size is replaced (Pillow's bitmap default font draws no Devanagari anyway)."""
    return _size(font) > 0 and hasattr(font, "getmask2")


def _shaper():
    try:
        import uharfbuzz
        return uharfbuzz
    except ImportError:
        return None


def pil_font(path: Path, size: float):
    from PIL import ImageFont
    key = (str(path), round(size, 3))
    with _lock:
        if key not in _pil:
            _pil[key] = ImageFont.truetype(str(path), size)
        return _pil[key]


def shape(text: str, path: Path, size: float) -> tuple:
    """([(glyph id, x, y)], advance) in pixels from the pen's start on the baseline, y down. Without uharfbuzz: the
    text's own letters, unshaped (glyph id None: drawn by their own code points)."""
    hb = _shaper()
    if hb is None:
        if not _said["no_shaper"]:
            _said["no_shaper"] = True
            _note("Hindi text: the text-shaping library (uharfbuzz) isn't installed, so letters are drawn one by one. "
                  "Update Studio to draw them joined.")
        font = pil_font(path, size)
        x, out = 0.0, []
        for ch in text:
            out.append((None, x, 0.0, ch))
            x += _raw("getlength", font)(font, ch)
        return out, x
    with _lock:
        if str(path) not in _hb:
            face = hb.Face(hb.Blob.from_file_path(str(path)))
            _hb[str(path)] = (hb.Font(face), face.upem)
        font, upem = _hb[str(path)]
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(font, buf, {})
        k = size / float(upem)
        x, out = 0.0, []
        for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
            if info.codepoint:                                  # 0: .notdef, nothing to draw
                out.append((info.codepoint, x + pos.x_offset * k, -pos.y_offset * k, None))
            x += pos.x_advance * k
        return out, x


def _raw(name: str, owner):
    """Pillow's own method (before install() routed it here): the shaper draws and measures single glyphs with it."""
    return _original.get(name) or getattr(type(owner), name)


def _note(message: str) -> None:
    try:
        import make_video                                     # the engine's progress log, where it is running
        make_video.log(message)
    except Exception:                                         # noqa: BLE001 - no engine here: nothing to tell
        pass


class _Line:
    """One shaped line: its glyphs, advance and box around the baseline origin (x0, y0, x1, y1), y down."""

    def __init__(self, text: str, font, stroke: float = 0):
        self.path = font_file(_heavy(font)) or font_file(True)
        self.size = _size(font)
        self.font = pil_font(self.path, self.size)
        self.glyphs, self.advance = shape(text, self.path, self.size)
        asc, desc = self.font.getmetrics()
        self.ascent, self.descent = float(asc), float(desc)
        box = None
        for gid, x, y, ch in self.glyphs:
            b = _raw("getbbox", self.font)(self.font, self.char(gid, ch), anchor="ls")
            b = (b[0] + x, b[1] + y, b[2] + x, b[3] + y)
            box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3]))
        box = box or (0.0, 0.0, 0.0, 0.0)
        s = float(stroke or 0)
        self.box = (box[0] - s, box[1] - s, box[2] + s, box[3] + s)

    @staticmethod
    def char(gid, ch) -> str:
        return ch if gid is None else chr(PUA + gid)

    def origin(self, xy, anchor: str) -> tuple:
        """The pen's start on the baseline for Pillow's anchor (two letters: l/m/r, then a/t/m/s/b/d)."""
        anchor = (anchor or "la") + "a"
        h, v = anchor[0], anchor[1]
        x, y = float(xy[0]), float(xy[1])
        x -= {"l": 0.0, "m": self.advance / 2.0, "r": self.advance}.get(h, 0.0)
        y += {"a": self.ascent, "t": -self.box[1], "m": (self.ascent - self.descent) / 2.0, "s": 0.0,
              "b": -self.box[3], "d": -self.descent}.get(v, self.ascent)
        return x, y


def _lines(text: str, font, stroke: float, spacing: float):
    lines = [_Line(t, font, stroke) for t in text.split("\n")]
    step = _raw("getbbox", lines[0].font)(lines[0].font, "A")[3] + float(stroke or 0) + float(spacing)  # as Pillow
    return lines, step


def _placed(xy, text: str, font, anchor, spacing, align, stroke) -> list:
    """[(line, baseline origin)] for each line of the text, laid out as Pillow lays out its own (multi-line) text."""
    if "\n" not in text:
        line = _Line(text, font, stroke)
        return [(line, line.origin(xy, anchor or "la"))]
    lines, step = _lines(text, font, stroke, spacing)
    anchor = anchor or "la"
    width = max(l.advance for l in lines)
    top = float(xy[1]) - {"a": 0.0, "m": step * (len(lines) - 1) / 2.0, "d": step * (len(lines) - 1)}.get(
        anchor[1:2] or "a", 0.0)
    left = float(xy[0]) - {"l": 0.0, "m": width / 2.0, "r": width}.get(anchor[:1], 0.0)
    out = []
    for i, line in enumerate(lines):
        shift = {"center": (width - line.advance) / 2.0, "right": width - line.advance}.get(align, 0.0)
        out.append((line, (left + shift, top + i * step + line.ascent)))
    return out


def draw(drawer, xy, text: str, fill=None, font=None, anchor=None, spacing=4, align="left", stroke_width=0,
         stroke_fill=None) -> None:
    placed = _placed(xy, text, font, anchor, spacing, align, stroke_width)
    text_of = _raw("text", drawer)
    passes = ([(stroke_fill if stroke_fill is not None else fill, stroke_width)] if stroke_width else []) + [(fill, 0)]
    for ink, width in passes:                       # every stroke first, then every letter on top
        for line, (x0, y0) in placed:
            for gid, x, y, ch in line.glyphs:
                text_of(drawer, (x0 + x, y0 + y), line.char(gid, ch), fill=ink, font=line.font, anchor="ls",
                        stroke_width=width, stroke_fill=ink if width else None)


def bbox(xy, text: str, font, anchor=None, spacing=4, align="left", stroke_width=0) -> tuple:
    box = None
    for line, (x0, y0) in _placed(xy, text, font, anchor, spacing, align, stroke_width):
        b = (line.box[0] + x0, line.box[1] + y0, line.box[2] + x0, line.box[3] + y0)
        box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3]))
    return box or (float(xy[0]), float(xy[1]), float(xy[0]), float(xy[1]))


def _ours(text, font) -> bool:
    return has_script(text) and _usable(font) and font_file(True) is not None


def install() -> bool:
    """Route Devanagari text through the shaper in this process (once). True when it is installed."""
    from PIL import ImageDraw, ImageFont
    if _original:
        return True
    if font_file(True) is None:
        return False
    D, F = ImageDraw.ImageDraw, ImageFont.FreeTypeFont
    _original.update(text=D.text, textbbox=D.textbbox, textlength=D.textlength, multiline_text=D.multiline_text,
                     multiline_textbbox=D.multiline_textbbox, getlength=F.getlength, getbbox=F.getbbox)

    def text(self, xy, text, fill=None, font=None, anchor=None, spacing=4, align="left", direction=None,
             features=None, language=None, stroke_width=0, stroke_fill=None, embedded_color=False, *args, **kwargs):
        if _ours(text, font):
            return draw(self, xy, text, fill, font, anchor, spacing, align, stroke_width, stroke_fill)
        return _original["text"](self, xy, text, fill, font, anchor, spacing, align, direction, features, language,
                                 stroke_width, stroke_fill, embedded_color, *args, **kwargs)

    def multiline_text(self, xy, text, fill=None, font=None, anchor=None, spacing=4, align="left", direction=None,
                       features=None, language=None, stroke_width=0, stroke_fill=None, embedded_color=False,
                       *args, **kwargs):
        if _ours(text, font):
            return draw(self, xy, text, fill, font, anchor, spacing, align, stroke_width, stroke_fill)
        return _original["multiline_text"](self, xy, text, fill, font, anchor, spacing, align, direction, features,
                                           language, stroke_width, stroke_fill, embedded_color, *args, **kwargs)

    def textbbox(self, xy, text, font=None, anchor=None, spacing=4, align="left", direction=None, features=None,
                 language=None, stroke_width=0, embedded_color=False, *args, **kwargs):
        if _ours(text, font):
            return bbox(xy, text, font, anchor, spacing, align, stroke_width)
        return _original["textbbox"](self, xy, text, font, anchor, spacing, align, direction, features, language,
                                     stroke_width, embedded_color, *args, **kwargs)

    def multiline_textbbox(self, xy, text, font=None, anchor=None, spacing=4, align="left", direction=None,
                           features=None, language=None, stroke_width=0, embedded_color=False, *args, **kwargs):
        if _ours(text, font):
            return bbox(xy, text, font, anchor, spacing, align, stroke_width)
        return _original["multiline_textbbox"](self, xy, text, font, anchor, spacing, align, direction, features,
                                               language, stroke_width, embedded_color, *args, **kwargs)

    def textlength(self, text, font=None, *args, **kwargs):
        if _ours(text, font):
            return _Line(text, font).advance
        return _original["textlength"](self, text, font, *args, **kwargs)

    def getlength(self, text, *args, **kwargs):
        if _ours(text, self):
            return _Line(text, self).advance
        return _original["getlength"](self, text, *args, **kwargs)

    def getbbox(self, text, mode="", direction=None, features=None, language=None, stroke_width=0, anchor=None,
                *args, **kwargs):
        if _ours(text, self) and "\n" not in text:
            return tuple(int(round(v)) for v in bbox((0, 0), text, self, anchor, 4, "left", stroke_width))
        return _original["getbbox"](self, text, mode, direction, features, language, stroke_width, anchor, *args,
                                    **kwargs)

    for name, fn in (("text", text), ("multiline_text", multiline_text), ("textbbox", textbbox),
                     ("multiline_textbbox", multiline_textbbox), ("textlength", textlength)):
        fn._cr_script_text = True
        setattr(D, name, fn)
    for name, fn in (("getlength", getlength), ("getbbox", getbbox)):
        fn._cr_script_text = True
        setattr(F, name, fn)
    return True


def uninstall() -> None:
    """Put Pillow back as it was (tests)."""
    from PIL import ImageDraw, ImageFont
    for name in ("text", "multiline_text", "textbbox", "multiline_textbbox", "textlength"):
        if name in _original:
            setattr(ImageDraw.ImageDraw, name, _original[name])
    for name in ("getlength", "getbbox"):
        if name in _original:
            setattr(ImageFont.FreeTypeFont, name, _original[name])
    _original.clear()
