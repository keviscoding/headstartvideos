"""The real map of a video's place for its renderer (cr_map_data: OpenFreeMap vector tiles, decoded with the stdlib)."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_map_data as m  # noqa: E402

PLACE = {"name": "Manila", "lat": 14.5995, "lon": 120.9842, "span_km": 2}


# a vector tile written by hand: protobuf fields, zigzag geometry commands
def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _field(num: int, payload) -> bytes:
    if isinstance(payload, int):
        return _varint(num << 3) + _varint(payload)
    return _varint(num << 3 | 2) + _varint(len(payload)) + payload


def _zz(v: int) -> int:
    return (v << 1) ^ (v >> 31)


def _rings(*rings) -> bytes:
    """Geometry commands for polygon rings given in tile units (each without its closing point)."""
    cmds, x, y = [], 0, 0
    for ring in rings:
        (sx, sy), rest = ring[0], ring[1:]
        cmds += [1 | (1 << 3), _zz(sx - x), _zz(sy - y)]
        x, y = sx, sy
        cmds.append(2 | (len(rest) << 3))
        for px, py in rest:
            cmds += [_zz(px - x), _zz(py - y)]
            x, y = px, py
        cmds.append(7 | (1 << 3))
    return b"".join(_varint(c) for c in cmds)


def _layer(name: str, features: list, keys: list, values: list) -> bytes:
    body = _field(15, 2) + _field(1, name.encode()) + _field(5, 4096)
    for tags, kind, geom in features:
        body += _field(2, _field(2, b"".join(_varint(t) for t in tags)) + _field(3, kind) + _field(4, geom))
    for k in keys:
        body += _field(3, k.encode())
    for v in values:
        body += _field(4, _field(1, v.encode()) if isinstance(v, str) else _field(2, __import__("struct").pack("<f", v)))
    return _field(3, body)


# in tile units (y down) an outline goes clockwise on screen: positive area by the shoelace formula
OUTER = [(1000, 1000), (3000, 1000), (3000, 3000), (1000, 3000)]
HOLE = [(1500, 1500), (1500, 2500), (2500, 2500), (2500, 1500)]
TILE = (_layer("water", [([0, 0], 3, _rings(OUTER, HOLE))], ["class"], ["ocean"])
        + _layer("building", [([0, 0], 3, _rings([(100, 100), (300, 100), (300, 300), (100, 300)]))],
                 ["render_height"], [120.0]))


def test_a_vector_tile_decodes_to_layers_with_tags_and_parts():
    tile = m.decode(TILE)
    (kind, props, parts), = tile["water"]
    assert kind == 3 and props == {"class": "ocean"} and len(parts) == 2
    assert parts[0][0] == (1000, 1000) and parts[0][-1] == parts[0][0], "the ring closes"
    assert tile["_extent"]["water"] == 4096 and tile["building"][0][1]["render_height"] == 120.0
    assert m.decode(__import__("gzip").compress(TILE))["water"], "a gzipped tile too"


def test_outlines_and_their_holes_stay_together_whatever_way_the_map_turns():
    tile = m.decode(TILE)
    groups = m.polygons(tile["water"][0][2])
    assert len(groups) == 1 and len(groups[0][1]) == 1, "one outline, one hole"
    x, y = m._tile_of(PLACE["lat"], PLACE["lon"], 14)
    out = m.shape([((int(x), int(y)), tile)], {**PLACE, "span_km": 12}, 14, 6000.0)
    sea = out["water"][0]
    assert sea["kind"] == "ocean" and len(sea["holes"]) == 1
    assert m._area(sea["ring"]) > 0 > m._area(sea["holes"][0]), "north up: outline counter-clockwise, hole clockwise"
    assert out["buildings"][0]["height"] == 120.0 and m._area(out["buildings"][0]["ring"]) > 0


def test_the_place_is_in_metres_around_its_centre_north_up():
    z = 14
    x, y = m._tile_of(PLACE["lat"], PLACE["lon"], z)
    xy = m.projector(PLACE, z)
    centre = xy(int(x), int(y), 4096, (x % 1) * 4096, (y % 1) * 4096)
    north = xy(int(x), int(y), 4096, (x % 1) * 4096, (y % 1) * 4096 - 100)
    assert abs(centre[0]) < 1 and abs(centre[1]) < 1
    assert north[1] > centre[1], "up the tile is north"
    zoom, tiles, half = m.tiles_for({**PLACE, "span_km": 12})
    assert zoom == 14 and 0 < len(tiles) <= m.MAX_TILES and half == 6000
    zoom, tiles, _ = m.tiles_for({**PLACE, "span_km": 24})
    assert len(tiles) <= m.MAX_TILES, "a wide place at a coarser zoom"


class _Res:
    def __init__(self, status, content=b"", body=None):
        self.status_code, self.content, self._body = status, content, body

    def json(self):
        return self._body


def test_the_map_is_fetched_once_per_place_and_any_failure_leaves_the_renderer_its_own_place(tmp_path):
    asked = []

    def get(url, timeout=None, headers=None):
        asked.append(url)
        if url == m.TILEJSON:
            return _Res(200, body={"tiles": ["https://t.example/{z}/{x}/{y}.pbf"]})
        return _Res(200, TILE)
    said = []
    first = m.map_for(tmp_path, PLACE, log=said.append, get=get, sleep=lambda s: None)
    assert first["water"] and first["source"].startswith("© OpenMapTiles") and "map: Manila" in said[-1]
    count = len(asked)
    assert m.map_for(tmp_path, PLACE, get=lambda *a, **k: (_ for _ in ()).throw(AssertionError("cached"))) == first
    assert count > 1
    down = m.map_for(tmp_path / "other", PLACE, log=said.append, sleep=lambda s: None,
                     get=lambda *a, **k: (_ for _ in ()).throw(ConnectionError("offline")))
    assert down == {} and "draws the place its own way" in said[-1]
    assert m.map_for(tmp_path, {"name": "nowhere"}) == {}, "no coordinates, no map"


def test_a_timeline_video_credits_the_map_in_its_description(tmp_path, monkeypatch):
    import cr_timeline as tl
    (tmp_path / "timeline.json").write_text(json.dumps({"seconds": 60, "place": PLACE,
                                                        "beats": [{"at": 0, "until": 60, "year": "1571"}]}))
    monkeypatch.setattr(m, "map_for", lambda home, place, log=print: {"water": [], "source": m.SOURCE})
    eng = SimpleNamespace(log=lambda s: None)
    assert tl.map_of(eng, tmp_path)["source"] == m.SOURCE
    (tmp_path / "youtube.txt").write_text("Manila, 500 years.\n")
    tl.credit(tmp_path)
    tl.credit(tmp_path)
    text = (tmp_path / "youtube.txt").read_text()
    assert text.count("Map data: © OpenMapTiles © OpenStreetMap contributors") == 1
