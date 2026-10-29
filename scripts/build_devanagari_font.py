"""Build Studio's Devanagari caption font (local_runtime/fonts/CRDevanagari-*.ttf) from Google's Noto fonts.

Hindi videos (customer Swapnil, 2026-10-29) need a font that draws Devanagari on every computer. None of the engine's
fonts has a single Devanagari letter, and on Windows the ffmpeg that burns the captions may find no system font to fall
back to. So Studio ships one: Noto Sans Devanagari (the script) merged with Noto Sans's Latin letters (an English name
or a brand in a Hindi line), as static Regular and Bold. Both are SIL Open Font License 1.1 (no reserved font names),
by The Noto Project Authors; the licence ships beside them (OFL-NotoSans.txt).

Every glyph is also mapped at U+F0000 + its glyph id (Supplementary Private Use Area A), so cr_script_text can draw
HarfBuzz's shaped glyphs one by one with Pillow, which has no shaping of its own without libraqm and FriBiDi.

Run once by a developer (needs fontTools):
    python3 scripts/build_devanagari_font.py <folder with NotoSansDevanagari[wght].ttf and NotoSans[wght].ttf>
The sources are the Google Fonts variable builds (github.com/google/fonts, ofl/notosansdevanagari and ofl/notosans).
"""
from __future__ import annotations

import sys
from pathlib import Path

from fontTools import subset
from fontTools.merge import Merger
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._c_m_a_p import cmap_format_12
from fontTools.varLib import instancer

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "local_runtime" / "fonts"
FAMILY = "CR Devanagari"
PUA = 0xF0000                       # cr_script_text.PUA: glyph id g is drawn as chr(PUA + g)
LATIN = "U+0020-007E,U+00A0-017F,U+0218-021B,U+2010-2027,U+2030-203A,U+20AC,U+2122"


def static(path: Path, weight: int) -> TTFont:
    return instancer.instantiateVariableFont(TTFont(path), {"wght": weight}, updateFontNames=False)


def build(src: Path, weight: int, style: str) -> Path:
    tmp = OUT / f".tmp-{style}"
    tmp.mkdir(parents=True, exist_ok=True)
    deva, latin = static(src / "NotoSansDevanagari[wght].ttf", weight), static(src / "NotoSans[wght].ttf", weight)
    opts = subset.Options()
    opts.layout_features = ["*"]
    opts.name_IDs = ["*"]
    opts.notdef_outline = True
    sub = subset.Subsetter(opts)
    sub.populate(unicodes=subset.parse_unicodes(LATIN))
    sub.subset(latin)
    for f in (deva, latin):
        for t in ("STAT", "MVAR", "HVAR", "avar", "fvar", "gvar"):
            if t in f:
                del f[t]
    paths = [tmp / "deva.ttf", tmp / "latin.ttf"]          # Devanagari first: its digits and punctuation win
    deva.save(paths[0])
    latin.save(paths[1])
    font = Merger().merge([str(p) for p in paths])
    for p in paths:
        p.unlink()
    tmp.rmdir()

    order = font.getGlyphOrder()
    best = font["cmap"].getBestCmap()
    table = cmap_format_12(12)
    table.platformID, table.platEncID, table.language = 3, 10, 0
    table.cmap = dict(best)
    table.cmap.update({PUA + gid: name for gid, name in enumerate(order)})
    font["cmap"].tables = [t for t in font["cmap"].tables if not (t.platformID == 3 and t.platEncID == 10)] + [table]

    name = font["name"]
    full, ps = f"{FAMILY} {style}", f"CRDevanagari-{style}"
    for nid, value in ((1, FAMILY), (2, style), (3, f"ChannelRecipe:{ps}"), (4, full), (6, ps), (16, FAMILY),
                       (17, style)):
        name.setName(value, nid, 3, 1, 0x409)
        name.setName(value, nid, 1, 0, 0)
    for nid in (25,):
        name.removeNames(nameID=nid)
    name.setName("Copyright 2022 The Noto Project Authors (https://github.com/notofonts/devanagari, "
                 "https://github.com/notofonts/latin-greek-cyrillic). Merged for ChannelRecipe Studio.", 0, 3, 1, 0x409)
    font["OS/2"].usWeightClass = weight
    font["OS/2"].fsSelection = (font["OS/2"].fsSelection & ~0b1100001) | (0b100000 if weight >= 700 else 0b1000000)
    font["head"].macStyle = 1 if weight >= 700 else 0
    out = OUT / f"{ps}.ttf"
    font.save(out)
    return out


if __name__ == "__main__":
    folder = Path(sys.argv[1])
    for w, s in ((400, "Regular"), (700, "Bold")):
        print(build(folder, w, s))
