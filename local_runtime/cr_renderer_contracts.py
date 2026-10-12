"""Read-only recipe diagnostics for the packaged renderer's actual contract.

This module never fixes geometry, chooses shots, or starts model/render work.
The private director and renderer stay unchanged; their ordinary checks receive
additional facts about input the renderer would ignore, clamp, or skip.
"""
from __future__ import annotations

import functools
import importlib
import math
import re
from pathlib import Path

_NUMBER = r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
HARD_CODES = ("renderer_contract_keys", "renderer_contract_parts", "renderer_contract_clamp")
_MISSING = object()


def _js_string(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ",".join(_js_string(item) for item in value)
    if isinstance(value, dict):
        return "[object Object]"
    return str(value)


def _key_number(value, default=None):
    # Animation key times use raw JavaScript Number coercion.
    if value is _MISSING:
        return default
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, list):
        value = _js_string(value)
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return 0.0
        if value.lower().startswith(("0x", "0b", "0o")):
            try:
                return float(int(value, 0))
            except ValueError:
                return default
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (ValueError, TypeError):
        return default


def _number(value, default=None):
    # The renderer's num() additionally treats null/undefined as missing.
    return default if value is None or value is _MISSING else _key_number(value, default)


def _match_number(pattern, text):
    match = re.search(pattern, text)
    return float(match.group(1)) if match else None


def read_contract(path):
    """Extract narrow supported facts. Unknown source syntax fails open."""
    path = Path(path)
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return {}
    contract = {"path": str(path.resolve()), "minimums": {}, "copies": {}}
    maximum = _match_number(r"const\s+MAX_PARTS\s*=\s*(" + _NUMBER + r")", source)
    if maximum is not None:
        contract["maximum_parts"] = int(maximum)
    depth = _match_number(r"depth\s*>\s*(" + _NUMBER + r")", source)
    if depth is not None:
        contract["maximum_depth"] = int(depth)
    contract["pair_keys"] = bool(re.search(r"\.filter\(\(k\)\s*=>\s*Array\.isArray\(k\)\s*&&\s*k\.length\s*>=\s*2", source))
    contract["translate_delta"] = "n.position.add(" in source
    contract["centered_grid"] = bool(re.search(r"i\s*-\s*\(nx\s*-\s*1\)\s*/\s*2", source))
    segment = re.search(r"seg\s*=\s*Math\.round\(clamp\(num\(p\.seg,\s*(" + _NUMBER + r")\),\s*(" + _NUMBER + r"),\s*(" + _NUMBER + r")\)\)", source)
    if segment:
        contract["segments"] = tuple(float(x) for x in segment.groups())
    lathe = _match_number(r"LatheGeometry\(pts,\s*seg\s*\+\s*(" + _NUMBER + r")", source)
    if lathe is not None:
        contract["lathe_offset"] = lathe
    # Read the actual repeat/grid bounds rather than applying a guessed budget.
    for name, default in (("repeat", 2), ("ring", 8), ("sphere", 20)):
        match = re.search(r"num\(p\." + name + r"\.n,\s*(" + _NUMBER + r")\),\s*(" + _NUMBER + r"),\s*(" + _NUMBER + r")\)", source)
        if match:
            contract["copies"][name] = tuple(float(x) for x in match.groups())
    grid = re.search(r"num\(n\[0\],\s*(" + _NUMBER + r")\),\s*(" + _NUMBER + r"),\s*(" + _NUMBER + r")\)", source)
    if grid:
        contract["copies"]["grid"] = tuple(float(x) for x in grid.groups())
    # Each case block describes one shape and its aliases. Only explicit input
    # fields with a directly readable lower clamp are diagnosed.
    heads = list(re.finditer(r"^\s*case\s+'[^']+':.*", source, re.M))
    for index, head in enumerate(heads):
        block = source[head.start():heads[index + 1].start() if index + 1 < len(heads) else source.find("\n    default:", head.end())]
        names = re.findall(r"case\s+'([^']+)'", head.group())
        minimums = {}
        for field in ("r", "tube", "depth", "h"):
            minimum = _match_number(r"Math\.max\((" + _NUMBER + r"),\s*num\(p\." + field + r"\b", block)
            if minimum is not None:
                minimums[field] = minimum
        if any(name in {"cylinder", "cyl", "tube_straight", "rod"} for name in names):
            radius = _match_number(r"Math\.max\((" + _NUMBER + r"),\s*num\(r\[0\]", block)
            if radius is not None:
                minimums["r"] = radius
        height = _match_number(r"Math\.max\((" + _NUMBER + r"),\s*h\)", block)
        if height is not None:
            minimums["h"] = height
        size = _match_number(r"\.map\(\(v\)\s*=>\s*Math\.max\((" + _NUMBER + r"),\s*Math\.abs\(v\)", block)
        if size is not None:
            minimums["size"] = size
        plane = _match_number(r"PlaneGeometry\(Math\.max\((" + _NUMBER + r"),", block)
        if plane is not None:
            minimums["size"] = plane
        if any(name in {"torus", "ring"} for name in names):
            factor = _match_number(r",\s*seg\s*\*\s*(" + _NUMBER + r"),", block)
            if factor is not None:
                contract["torus_factor"] = factor
        for name in names:
            contract["minimums"][name] = minimums
    return contract


def _copies(part, contract):
    bounds = contract.get("copies", {})
    for name in ("repeat", "ring", "sphere", "grid"):
        config = part.get(name)
        if not isinstance(config, dict):
            continue
        if name not in bounds:
            return None
        default, lower, upper = bounds[name]
        def count(value):
            return math.floor(min(upper, max(lower, _number(value, default))) + 0.5)
        if name == "grid":
            value = config.get("n", default)
            pair = value if isinstance(value, list) else [value, value]
            return count(pair[0] if pair else _MISSING) * count(pair[1] if len(pair) > 1 else _MISSING)
        return count(config.get("n", _MISSING))
    return 1


def _is_group(part):
    return (str(part.get("shape", "")).lower() == "group" or
            (part.get("group") is not None and isinstance(part.get("parts"), list)) or
            (isinstance(part.get("parts"), list) and not part.get("shape")))


def _parts(parts, maximum_depth):
    # Use an iterative walk and the source's exact depth limit. Children inside
    # ordinary primitives, and groups deeper than the renderer supports, are
    # never built and must not produce repair instructions.
    stack = [(parts, 0)]
    maximum_depth = maximum_depth if maximum_depth is not None else 0
    while stack:
        items, depth = stack.pop()
        if not isinstance(items, list) or depth > maximum_depth:
            continue
        for part in items:
            if not isinstance(part, dict):
                continue
            yield part, depth
            if _is_group(part):
                stack.append((part.get("parts"), depth + 1))


def _budget(parts, contract):
    """Follow build order and group repetition; a single instance batch can overshoot."""
    maximum = contract.get("maximum_parts")
    max_depth = contract.get("maximum_depth")
    if maximum is None or max_depth is None:
        return None
    count, skipped, uncertain, visits = 0, False, False, 0
    def visit(items, depth=0):
        nonlocal count, skipped, uncertain, visits
        if not isinstance(items, list) or depth > max_depth:
            return
        for part in items:
            visits += 1
            # Empty repeated groups can expand exponentially without ever
            # spending the mesh budget. A diagnostic must never stall a job.
            if visits > 10000 or uncertain:
                uncertain = True
                return
            if not isinstance(part, dict):
                continue
            if count >= maximum:
                skipped = True
                return
            copies = _copies(part, contract)
            if copies is None:
                uncertain = True
                return
            if _is_group(part):
                for _ in range(copies):
                    visit(part.get("parts"), depth + 1)
                    if uncertain:
                        return
            else:
                count += copies
    visit(parts)
    return None if uncertain else {"count": count, "skipped": skipped, "maximum": maximum}


def diagnose(board, contract):
    """Return definite renderer input facts by scene. Input board is untouched."""
    result = {}
    if not contract:
        return result
    scenes = list(board.get("scenes") or [])
    for moment in board.get("moments") or []:
        if isinstance(moment, dict):
            scenes.extend(moment.get("scenes") or [])
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        findings = []
        for section in ("objects", "structures", "groups"):
            for index, item in enumerate(scene.get(section) or []):
                if not isinstance(item, dict) or item.get("kind") != "prop.build":
                    continue
                # The builder merges params, then direct fields override them.
                recipe = {**(item.get("params") if isinstance(item.get("params"), dict) else {}), **item}
                ref = f"{section}:{index}"
                animations = recipe.get("anim") or []
                if isinstance(animations, dict):
                    animations = [animations]
                invalid_keys, total_keys, affected_parts = 0, 0, []
                for animation in animations if isinstance(animations, list) else []:
                    if not isinstance(animation, dict):
                        continue
                    kind = str(animation.get("type", animation.get("kind", "rotate"))).lower()
                    keys = animation.get("keys")
                    if contract.get("pair_keys") and kind in {"translate", "move", "rotate", "turn", "scale"} and isinstance(keys, list):
                        invalid = sum(not (isinstance(key, list) and len(key) >= 2 and _key_number(key[0]) is not None) for key in keys)
                        if invalid:
                            invalid_keys += invalid
                            total_keys += len(keys)
                            affected_parts.append(str(animation.get("part", "body")))
                budget = _budget(recipe.get("parts"), contract)
                if budget and budget["skipped"]:
                    findings.append(f"renderer_contract_parts: {ref} reaches {budget['count']} instances with a {budget['maximum']} per-prop limit; later parts are skipped. Count nested group copies and every grid instance.")
                if invalid_keys:
                    findings.append(f"renderer_contract_keys: {ref} animation input ignores {invalid_keys}/{total_keys} keys for {', '.join(dict.fromkeys(affected_parts))[:200]}; use [time,value] pairs. Translation values are deltas from the part's base position.")
                clamps = []
                unsupported = []
                for part, _ in _parts(recipe.get("parts"), contract.get("maximum_depth")):
                    shape = str(part.get("shape", part.get("type", "box"))).lower()
                    if "segments" in part and "seg" not in part and contract.get("segments"):
                        unsupported.append(str(part.get("id", shape)))
                    for field, minimum in contract.get("minimums", {}).get(shape, {}).items():
                        if field not in part:
                            continue
                        if field == "h" and isinstance(part.get("from"), list) and isinstance(part.get("to"), list):
                            continue  # The renderer uses endpoint distance, not h.
                        raw = part[field]
                        if field == "size":
                            if shape in {"box", "cube"}:
                                # arr3 accepts 2/3+ components or a number. Its
                                # one-element vectors and strings use defaults.
                                if isinstance(raw, list):
                                    values = raw[:3] if len(raw) >= 3 else raw if len(raw) == 2 else []
                                else:
                                    values = [raw] if isinstance(raw, (int, float)) and not isinstance(raw, bool) else []
                            else:
                                values = raw[:2] if isinstance(raw, list) else []
                        elif field == "r" and shape in {"cylinder", "cyl", "tube_straight", "rod"} and isinstance(raw, list):
                            values = raw[:2]
                        else:
                            values = [raw]  # Scalar num() coerces the whole list.
                        for value in values:
                            numeric = _key_number(value) if field == "size" and shape in {"plane", "sheet"} else _number(value)
                            if numeric is None:
                                continue
                            if field == "size" and shape in {"plane", "sheet"} and not numeric:
                                continue  # +size || default bypasses the clamp.
                            numeric = abs(numeric) if field == "size" and shape in {"box", "cube"} else numeric
                            if numeric < minimum:
                                clamps.append(f"{part.get('id', shape)}.{field} {numeric:g}→{minimum:g} m")
                if clamps:
                    findings.append(f"renderer_contract_clamp: {ref} clamps explicit dimensions before part/prop scaling: {'; '.join(dict.fromkeys(clamps))[:440]}. Choose representational units with these minima in mind; no automatic correction is applied.")
                if unsupported:
                    findings.append(f"renderer_contract_segments: {ref} parts {', '.join(dict.fromkeys(unsupported))[:160]} use segments, which this renderer does not read; its tessellation field is seg.")
        if findings:
            result[str(scene.get("id"))] = list(dict.fromkeys(findings))
    return result


def compatibility_note(contract, camera_path):
    if not contract:
        return ""
    facts = []
    if contract.get("pair_keys"):
        facts.append("prop.build animation keys are [time,value] pairs")
    if contract.get("translate_delta"):
        facts.append("translate values add deltas to base pos")
    if contract.get("centered_grid"):
        facts.append("grid.pos is the grid CENTER; repeat.step starts from the first copy")
    if contract.get("maximum_parts") is not None:
        facts.append(f"later parts stop when a prop's running mesh-instance count reaches {contract['maximum_parts']}, including nested copies; a final instance batch can exceed it")
    if contract.get("lathe_offset") is not None:
        facts.append(f"lathe radial sides are clamped seg+{contract['lathe_offset']:g}")
    if contract.get("torus_factor") is not None:
        facts.append(f"torus ring sides are clamped seg×{contract['torus_factor']:g}")
    camera_path = Path(camera_path)
    read_paths = [contract["path"], str(camera_path)]
    engine_root = camera_path.parent.parent
    for relative in ("lib/v3/stage.js", "lib/v3/lights.js"):
        path = engine_root / relative
        if path.is_file():
            read_paths.append(str(path))
    return ("\n\nRenderer compatibility (schema facts, not creative direction): " + "; ".join(facts) +
            ". Primitive lower-size clamps occur before scaling. These facts are not instructions to alter valid intentional recipes. "
            "When Read is available, inspect the actual renderer contracts before repairs:\n" + "\n".join(read_paths) +
            "\nCamera keys use world coordinates after prop scaling/ground fitting; explicit look keys determine direction, while target determines focus unless track is enabled. Preserve the original creative choices and review rendered frames.")


def install(engine):
    """Add facts to the original shared checker and notes path, once per module."""
    try:
        threed = importlib.import_module("threed")
    except ImportError:
        return None
    if not getattr(threed, "ROOT", None) or not callable(getattr(getattr(threed, "SB", None), "board_checks", None)) or not callable(getattr(threed, "_notes_of", None)):
        return None
    checker = threed.SB.board_checks
    if getattr(checker, "__cr_renderer_contracts__", False):
        return checker
    root = Path(threed.ROOT)
    contract = read_contract(root / "engine/lib/human/propbuild.js")
    if not contract:
        return checker
    @functools.wraps(checker)
    def checks(board, *args, **kwargs):
        original = checker(board, *args, **kwargs)
        extra = diagnose(board, contract)
        if not extra or not isinstance(original, dict):
            return original
        result = dict(original)
        result["scenes"] = {sid: list(lines) for sid, lines in original.get("scenes", {}).items()}
        added = 0
        for sid, lines in extra.items():
            current = result["scenes"].setdefault(sid, [])
            for line in lines:
                if line not in current:
                    current.append(line)
                    added += 1
        result["stats"] = dict(original.get("stats", {}))
        if isinstance(result["stats"].get("violations"), (int, float)):
            result["stats"]["violations"] += added
        return result
    checks.__cr_renderer_contracts__ = True
    threed.SB.board_checks = checks
    original_hard = getattr(threed.SB, "SPEC_HARD", ())
    additions = [code for code in HARD_CODES if code not in original_hard]
    if isinstance(original_hard, (set, frozenset)):
        threed.SB.SPEC_HARD = type(original_hard)(set(original_hard).union(additions))
    elif isinstance(original_hard, list):
        threed.SB.SPEC_HARD = [*original_hard, *additions]
    else:
        threed.SB.SPEC_HARD = (*original_hard, *additions)
    notes = threed._notes_of
    note = compatibility_note(contract, root / "engine/core/camera.js")
    @functools.wraps(notes)
    def with_contracts(job):
        return notes(job) + note
    threed._notes_of = with_contracts
    return checks
