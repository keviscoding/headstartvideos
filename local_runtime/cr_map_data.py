"""The real map of a video's place, for a renderer that builds it (a 3D city growing over centuries, cr_timeline).

A renderer's page has no network (cr_animated2d), so Studio fetches the place once per video and puts it in the film as
Kit.scene.map, in metres around the place's centre (x east, y north), kept in output/cr-map-cache (one city, many
videos). The source is OpenFreeMap's vector tiles (OpenStreetMap data in the OpenMapTiles schema; free, no key, any
use): unlike OpenStreetMap's query servers (all four public ones failed or timed out on 2026-11-02), a static tile
service, and its sea comes as ready polygons, so nothing has to close a coastline.

  {"place": {...}, "span_m": 12000, "water": [{"kind": "ocean|lake|river|...", "ring": [[x, y], ...], "holes": []}],
   "streets": [{"kind", "line": [[x, y], ...]}], "buildings": [{"ring", "height", "base"}],
   "parks": [{"name", "ring"}], "landuse": [{"kind", "ring"}], "landmarks": [{"name", "kind", "at"}],
   "places": [{"name", "kind", "at"}], "source": "...", "note": ""}

Land is everything that isn't water: the renderer lays the ground, then the water over it. Nothing here stops a video:
no map, no `map` in the film, and the renderer draws its own place (said in the video's log)."""
from __future__ import annotations

import concurrent.futures
import gzip
import hashlib
import json
import math
import struct
import time
from pathlib import Path

TILEJSON = "https://tiles.openfreemap.org/planet"
SOURCE = "© OpenMapTiles © OpenStreetMap contributors (via OpenFreeMap)"
MAX_SPAN_KM = 24.0
MAX_TILES = 144
MAX_BUILDINGS = 15000
VERSION = 2


# ── Mapbox Vector Tiles, decoded with the standard library ─────────────────────────────────────────────────────

def _varint(buf: bytes, i: int):
    shift = result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def _fields(buf: bytes):
    """(field number, wire type, value) for every field of a protobuf message (varint, 64-bit, bytes, 32-bit)."""
    i, n = 0, len(buf)
    while i < n:
        key, i = _varint(buf, i)
        field, wire = key >> 3, key & 7
        if wire == 0:
            value, i = _varint(buf, i)
        elif wire == 1:
            value, i = buf[i:i + 8], i + 8
        elif wire == 2:
            size, i = _varint(buf, i)
            value, i = buf[i:i + size], i + size
        elif wire == 5:
            value, i = buf[i:i + 4], i + 4
        else:
            raise ValueError("unsupported protobuf wire type")
        yield field, wire, value


def _packed(buf: bytes) -> list:
    out, i = [], 0
    while i < len(buf):
        v, i = _varint(buf, i)
        out.append(v)
    return out


def _value(buf: bytes):
    for field, _, v in _fields(buf):
        if field == 1:
            return v.decode("utf-8", "replace")
        if field == 2:
            return struct.unpack("<f", v)[0]
        if field == 3:
            return struct.unpack("<d", v)[0]
        if field in (4, 5):
            return v
        if field == 6:
            return (v >> 1) ^ -(v & 1)
        if field == 7:
            return bool(v)
    return None


def _geometry(cmds: list) -> list:
    """The feature's parts in tile units: [[(x, y), ...], ...] (a line's lines, a polygon's rings, a point's points)."""
    parts, cur, x, y, i = [], [], 0, 0, 0
    while i < len(cmds):
        cmd, count = cmds[i] & 7, cmds[i] >> 3
        i += 1
        if cmd == 7:                                     # ClosePath
            if cur:
                cur.append(cur[0])
            continue
        for _ in range(count):
            dx, dy = cmds[i], cmds[i + 1]
            i += 2
            x += (dx >> 1) ^ -(dx & 1)
            y += (dy >> 1) ^ -(dy & 1)
            if cmd == 1:                                 # MoveTo: a new part
                if cur:
                    parts.append(cur)
                cur = [(x, y)]
            else:                                        # LineTo
                cur.append((x, y))
    if cur:
        parts.append(cur)
    return parts


def decode(tile: bytes) -> dict:
    """{layer name: [(type, {tags}, parts in tile units)], "_extent": {layer: extent}} for one tile."""
    if tile[:2] == b"\x1f\x8b":
        tile = gzip.decompress(tile)
    out, extents = {}, {}
    for field, _, layer in _fields(tile):
        if field != 3:
            continue
        name, keys, values, feats, extent = "", [], [], [], 4096
        for f, _, v in _fields(layer):
            if f == 1:
                name = v.decode("utf-8", "replace")
            elif f == 2:
                feats.append(v)
            elif f == 3:
                keys.append(v.decode("utf-8", "replace"))
            elif f == 4:
                values.append(_value(v))
            elif f == 5:
                extent = v
        rows = []
        for feat in feats:
            kind, tags, geom = 0, [], []
            for f, _, v in _fields(feat):
                if f == 2:
                    tags = _packed(v)
                elif f == 3:
                    kind = v
                elif f == 4:
                    geom = _packed(v)
            props = {keys[tags[k]]: values[tags[k + 1]] for k in range(0, len(tags) - 1, 2)
                     if tags[k] < len(keys) and tags[k + 1] < len(values)}
            rows.append((kind, props, _geometry(geom)))
        out[name], extents[name] = rows, extent
    out["_extent"] = extents
    return out


# ── tiles for a place ──────────────────────────────────────────────────────────────────────────────────────────

def _tile_of(lat: float, lon: float, z: int):
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


def tiles_for(place: dict):
    """(zoom, [(x, y)], half-span in metres): the tiles covering the place's area, at most MAX_TILES."""
    lat, lon = float(place["lat"]), float(place["lon"])
    half = min(MAX_SPAN_KM, max(1.0, float(place.get("span_km") or 12))) * 500.0
    dlat = half / 111_320.0
    dlon = half / (111_320.0 * max(0.2, math.cos(math.radians(lat))))
    tiles = []
    for z in (14, 13, 12):
        x0, y0 = _tile_of(lat + dlat, lon - dlon, z)
        x1, y1 = _tile_of(lat - dlat, lon + dlon, z)
        tiles = [(x, y) for x in range(int(x0), int(x1) + 1) for y in range(int(y0), int(y1) + 1)]
        if len(tiles) <= MAX_TILES:
            return z, tiles, half
    return 12, tiles[:MAX_TILES], half


def projector(place: dict, z: int):
    """Tile units of tile (tx, ty) → metres around the place's centre."""
    lat0, lon0 = float(place["lat"]), float(place["lon"])
    kx = 111_320.0 * math.cos(math.radians(lat0))
    n = 2 ** z

    def xy(tx, ty, extent, px, py):
        lon = (tx + px / extent) / n * 360.0 - 180.0
        lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (ty + py / extent) / n))))
        return [round((lon - lon0) * kx, 1), round((lat - lat0) * 111_320.0, 1)]
    return xy


def _area(ring: list) -> float:
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:])) / 2.0


def polygons(parts: list) -> list:
    """A tile's polygon rings grouped [(outline, [holes])]: in tile units an outline has a positive area and its holes
    follow it with a negative one (the vector tile spec), whatever direction the map is turned afterwards."""
    out = []
    for ring in parts:
        if len(ring) < 4:
            continue
        a = _area(ring)
        if a > 0:
            out.append((ring, []))
        elif a < 0 and out:
            out[-1][1].append(ring)
    return out


def shape(decoded: list, place: dict, z: int, half: float) -> dict:
    """The film's map from decoded tiles [((tx, ty), tile)], kept to the place's square."""
    xy = projector(place, z)
    water, streets, buildings, parks, landuse, landmarks, places = [], [], [], [], [], [], []
    seen = set()

    def inside(p):
        return abs(p[0]) <= half * 1.02 and abs(p[1]) <= half * 1.02

    for (tx, ty), tile in decoded:
        ext = tile.get("_extent") or {}

        def conv(layer, part):
            e = ext.get(layer, 4096)
            return [xy(tx, ty, e, px, py) for px, py in part]
        def areas(layer, parts):
            # outline counter-clockwise, holes clockwise, in north-up metres (the y axis turns, so both reverse)
            return [(conv(layer, outline)[::-1], [conv(layer, h)[::-1] for h in holes])
                    for outline, holes in polygons(parts)]
        for kind, props, parts in tile.get("water", []):
            for ring, holes in areas("water", parts):
                if any(inside(p) for p in ring):
                    water.append({"kind": str(props.get("class") or "water"), "ring": ring, "holes": holes})
        for kind, props, parts in tile.get("transportation", []):
            cls = str(props.get("class") or "")
            if cls in ("path", "track", "ferry", "rail", "transit", "aerialway", "service", "minor_construction"):
                continue
            for part in parts:
                line = conv("transportation", part)
                if len(line) >= 2 and any(inside(p) for p in line):
                    streets.append({"kind": cls, "line": line})
        for kind, props, parts in tile.get("building", []):
            for outline, _ in polygons(parts):
                ring = conv("building", outline)[::-1]
                if not inside(ring[0]):
                    continue
                key = (round(ring[0][0]), round(ring[0][1]), len(ring))   # one building on two tiles: once
                if key in seen:
                    continue
                seen.add(key)
                buildings.append({"ring": ring, "height": round(float(props.get("render_height") or 0), 1),
                                  "base": round(float(props.get("render_min_height") or 0), 1)})
        for kind, props, parts in tile.get("park", []):
            for ring, holes in areas("park", parts):
                if any(inside(p) for p in ring):
                    parks.append({"name": str(props.get("name") or "")[:50], "ring": ring, "holes": holes})
        for kind, props, parts in tile.get("landuse", []):
            for ring, holes in areas("landuse", parts):
                if any(inside(p) for p in ring):
                    landuse.append({"kind": str(props.get("class") or ""), "ring": ring, "holes": holes})
        for layer, bucket in (("poi", landmarks), ("place", places)):
            for kind, props, parts in tile.get(layer, []):
                name = str(props.get("name:latin") or props.get("name") or "")
                if not name or not parts or not parts[0]:
                    continue
                at = conv(layer, parts[0][:1])[0]
                if inside(at) and (layer, name) not in seen:
                    seen.add((layer, name))
                    bucket.append({"name": name[:60], "kind": str(props.get("class") or ""), "at": at})
    buildings.sort(key=lambda b: -abs(_area(b["ring"])))   # the biggest first, when the cap drops some
    landmarks.sort(key=lambda l: l["kind"] not in ("attraction", "museum", "monument", "castle", "place_of_worship"))
    return {"version": VERSION, "place": place, "span_m": round(2 * half), "zoom": z, "water": water,
            "streets": streets, "buildings": buildings[:MAX_BUILDINGS], "parks": parks, "landuse": landuse,
            "landmarks": landmarks[:300], "places": places[:200], "source": SOURCE,
            "note": f"{len(buildings) - MAX_BUILDINGS} smaller buildings left out" if len(buildings) > MAX_BUILDINGS else ""}


def _key(place: dict) -> str:
    return hashlib.sha1(json.dumps([round(float(place["lat"]), 3), round(float(place["lon"]), 3),
                                    round(float(place.get("span_km") or 12), 1), VERSION]).encode()).hexdigest()[:16]


def map_for(home, place: dict, *, log=print, get=None, sleep=time.sleep) -> dict:
    """The place's map (cached for every video of it), or {} when it can't be had: never stops a video."""
    try:
        float(place["lat"]), float(place["lon"])
    except (KeyError, TypeError, ValueError):
        return {}
    cache = Path(home) / "output" / "cr-map-cache" / (_key(place) + ".json")
    try:
        saved = json.loads(cache.read_text(encoding="utf-8"))
        if saved.get("version") == VERSION:
            return saved
    except (OSError, ValueError):
        pass
    import requests
    get = get or requests.get
    headers = {"User-Agent": "ChannelRecipe Studio"}
    try:
        template = get(TILEJSON, timeout=(10, 30), headers=headers).json()["tiles"][0]
        z, tiles, half = tiles_for(place)

        def one(t):
            for attempt in range(3):
                try:
                    res = get(template.format(z=z, x=t[0], y=t[1]), timeout=(10, 60), headers=headers)
                    if res.status_code == 200:
                        return t, decode(res.content)
                    if res.status_code == 404:
                        return t, {}                     # open sea: no tile
                except Exception:                        # noqa: BLE001 - asked again, then left out
                    pass
                sleep(2 * (attempt + 1))
            return t, None
        with concurrent.futures.ThreadPoolExecutor(8) as pool:
            got = list(pool.map(one, tiles))
        missing = sum(1 for _, tile in got if tile is None)
        if missing > len(tiles) // 4:
            raise RuntimeError(f"{missing} of {len(tiles)} map tiles didn't come")
        out = shape([(t, tile) for t, tile in got if tile], place, z, half)
    except Exception as exc:                             # noqa: BLE001 - the renderer draws its own place
        log(f"  map: couldn't get the map of {place.get('name') or 'the place'} ({type(exc).__name__}: "
            f"{str(exc)[:120]}); the renderer draws the place its own way")
        return {}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    log(f"  map: {place.get('name') or 'the place'} ({len(tiles)} tiles, zoom {z}): {len(out['buildings'])} buildings, "
        f"{len(out['streets'])} streets, {len(out['water'])} water areas, {len(out['landmarks'])} landmarks")
    return out
