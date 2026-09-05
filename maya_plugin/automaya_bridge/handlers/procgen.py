"""procgen.* commands: parametric generators at real world scale (cm).

Every generator builds from maya.cmds primitives plus poly ops, names parts
``<name>_<part>_geo`` under ``<name>_grp``, freezes transforms, puts pivots at
the base centre and returns ``{group, parts, bbox, stats}`` so the agent can
check the scale it got. Noise and sampling helpers are pure python so they are
deterministic per seed and testable outside Maya.
"""
from __future__ import annotations

import math
import random
import struct
import zlib
from typing import Any, Callable, Dict, List, Sequence, Tuple

from ..registry import command
from . import _util
from ._util import BridgeError, require_maya, require_nodes

try:
    from maya import cmds  # type: ignore
except ImportError:  # pragma: no cover
    cmds = None  # type: ignore

STYLES = ("flat", "brick", "glass", "classical")
ROOFS = ("flat", "parapet", "pitched", "hip")
REVEAL = {"flat": 15.0, "brick": 20.0, "glass": 5.0, "classical": 30.0}
CANOPIES = ("round", "conical", "columnar", "umbrella")
ORDERS = ("plain", "tuscan", "doric", "ionic", "corinthian")
# Real world furniture dimensions in cm: width (x), height (y), depth (z).
FURNITURE = {
    "table": (120.0, 75.0, 80.0),
    "chair": (45.0, 90.0, 50.0),
    "sofa": (200.0, 85.0, 90.0),
    "bed": (160.0, 110.0, 200.0),
    "desk": (140.0, 75.0, 70.0),
    "shelf": (80.0, 200.0, 30.0),
    "lamp": (40.0, 150.0, 40.0),
}
# Vehicles: length (x), width (z), height (y), wheel diameter, wheelbase.
VEHICLES = {
    "car": (450.0, 180.0, 150.0, 65.0, 270.0),
    "van": (500.0, 200.0, 220.0, 70.0, 320.0),
    "bus": (1200.0, 255.0, 320.0, 100.0, 600.0),
}
MAX_PARTS = 4000


# pure python helpers (deterministic, no Maya) ----------------------------------
def _hash01(ix: int, iz: int, seed: int) -> float:
    """Integer lattice hash to 0..1, stable across platforms."""
    n = (ix * 374761393 + iz * 668265263 + seed * 1442695041) & 0xFFFFFFFF
    n = ((n ^ (n >> 13)) * 1274126177) & 0xFFFFFFFF
    n ^= n >> 16
    return (n & 0xFFFFFF) / float(0xFFFFFF)


def _smooth_noise(x: float, z: float, seed: int) -> float:
    ix, iz = math.floor(x), math.floor(z)
    fx, fz = x - ix, z - iz
    sx, sz = fx * fx * (3.0 - 2.0 * fx), fz * fz * (3.0 - 2.0 * fz)
    a, b = _hash01(ix, iz, seed), _hash01(ix + 1, iz, seed)
    c, d = _hash01(ix, iz + 1, seed), _hash01(ix + 1, iz + 1, seed)
    top = a + (b - a) * sx
    bottom = c + (d - c) * sx
    return top + (bottom - top) * sz


def value_noise(x: float, z: float, seed: int = 0, octaves: int = 4, frequency: float = 1.0, persistence: float = 0.5) -> float:
    """Layered (fractal) value noise in 0..1. Same inputs always give the same value."""
    total, amp, freq, norm = 0.0, 1.0, float(frequency), 0.0
    for o in range(max(1, int(octaves))):
        total += amp * _smooth_noise(x * freq, z * freq, int(seed) + o * 101)
        norm += amp
        amp *= persistence
        freq *= 2.0
    return total / norm


def terrain_height(x: float, z: float, seed: int, octaves: int, amplitude: float, feature_size: float) -> float:
    """Height in cm at world (x, z): noise centred on zero so the plane stays at its origin."""
    n = value_noise(x / feature_size, z / feature_size, seed, octaves)
    return (n - 0.5) * 2.0 * amplitude


def poisson_pick(candidates: Callable[[], Sequence[float]], count: int, min_distance: float, attempts: int) -> Tuple[List[Sequence[float]], int]:
    """Draw ``count`` samples from ``candidates()`` keeping every pair at least ``min_distance`` apart.

    Returns (accepted, rejected). Pure python so scatter is testable outside Maya.
    """
    accepted: List[Sequence[float]] = []
    rejected = 0
    d2 = float(min_distance) ** 2
    for _ in range(attempts):
        if len(accepted) >= count:
            break
        p = candidates()
        if d2 > 0 and any((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2 < d2 for q in accepted):
            rejected += 1
            continue
        accepted.append(p)
    return accepted, rejected


def cube_face(side: str, row: int, col: int, sx: int, sy: int, sz: int) -> int:
    """Face index on a polyCube with subdivisions sx, sy, sz.

    Maya orders polyCube faces front (+Z), top (+Y), back (-Z), bottom (-Y), right (+X),
    left (-X); inside a side they run row by row from the bottom, columns from -X or -Z.
    """
    n_fb, n_tb, n_lr = sx * sy, sx * sz, sz * sy
    starts = {"front": 0, "top": n_fb, "back": n_fb + n_tb, "bottom": 2 * n_fb + n_tb, "right": 2 * n_fb + 2 * n_tb, "left": 2 * n_fb + 2 * n_tb + n_lr}
    cols = sx if side in ("front", "back", "top", "bottom") else sz
    return starts[side] + row * cols + col


def read_heightmap(path: str) -> Tuple[int, int, List[List[float]]]:
    """Read an 8/16 bit non interlaced PNG or a binary PGM into rows of 0..1 (first channel). Stdlib only."""
    with open(path, "rb") as fh:
        data = fh.read()
    if data.startswith(b"P5"):
        parts = data.split(maxsplit=4)
        w, h, maxval = int(parts[1]), int(parts[2]), int(parts[3])
        raw = parts[4] if len(parts) > 4 else b""
        step = 2 if maxval > 255 else 1
        fmt = ">%dH" % (w * h) if step == 2 else "%dB" % (w * h)
        vals = struct.unpack(fmt, raw[: w * h * step])
        return w, h, [[vals[r * w + c] / float(maxval) for c in range(w)] for r in range(h)]
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise BridgeError("heightmap must be an 8 or 16 bit PNG or a binary PGM (P5): %s" % path)
    pos, idat, w, h, depth, ctype = 8, b"", 0, 0, 8, 0
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos : pos + 8])
        body = data[pos + 8 : pos + 8 + length]
        if kind == b"IHDR":
            w, h, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
            if interlace:
                raise BridgeError("interlaced PNGs are not supported; re-save the heightmap without interlacing")
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
        pos += 12 + length
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ctype, 1)
    if depth not in (8, 16) or ctype == 3:
        raise BridgeError("heightmap PNG must be 8 or 16 bit greyscale/RGB (palette PNGs are not supported)")
    bpp = channels * depth // 8
    stride = w * bpp
    raw = zlib.decompress(idat)
    rows: List[List[float]] = []
    prev = bytearray(stride)
    maxval = float((1 << depth) - 1)
    for r in range(h):
        ftype = raw[r * (stride + 1)]
        line = bytearray(raw[r * (stride + 1) + 1 : (r + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if ftype == 1:
                line[i] = (line[i] + a) & 0xFF
            elif ftype == 2:
                line[i] = (line[i] + b) & 0xFF
            elif ftype == 3:
                line[i] = (line[i] + (a + b) // 2) & 0xFF
            elif ftype == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
        if depth == 16:
            rows.append([((line[x * bpp] << 8) | line[x * bpp + 1]) / maxval for x in range(w)])
        else:
            rows.append([line[x * bpp] / maxval for x in range(w)])
        prev = line
    return w, h, rows


def sample_heightmap(rows: List[List[float]], u: float, v: float) -> float:
    """Bilinear lookup of a 0..1 grid at u, v in 0..1 (v=0 is the first row)."""
    h, w = len(rows), len(rows[0])
    fx, fz = min(max(u, 0.0), 1.0) * (w - 1), min(max(v, 0.0), 1.0) * (h - 1)
    x0, z0 = int(fx), int(fz)
    x1, z1 = min(x0 + 1, w - 1), min(z0 + 1, h - 1)
    tx, tz = fx - x0, fz - z0
    top = rows[z0][x0] + (rows[z0][x1] - rows[z0][x0]) * tx
    bottom = rows[z1][x0] + (rows[z1][x1] - rows[z1][x0]) * tx
    return top + (bottom - top) * tz


# maya helpers ----------------------------------------------------------------
def _first(result: Any, fallback: str) -> str:
    nodes = list(result) if isinstance(result, (list, tuple)) else [result]
    return str(nodes[0]) if nodes and nodes[0] else fallback


def _rng(seed: int | None) -> Tuple[random.Random, int]:
    seed = int(seed) if seed is not None else random.randrange(1 << 30)
    return random.Random(seed), seed


def _pos(node: str, x: float, y: float, z: float) -> str:
    cmds.xform(node, worldSpace=True, translation=[float(x), float(y), float(z)])
    return node


def _rot(node: str, rx: float, ry: float, rz: float) -> str:
    cmds.xform(node, worldSpace=True, rotation=[float(rx), float(ry), float(rz)])
    return node


def _box(name: str, w: float, h: float, d: float, x: float = 0.0, y: float = 0.0, z: float = 0.0, sx: int = 1, sy: int = 1, sz: int = 1) -> str:
    """polyCube with its base at ``y`` (Maya centres cubes, so we lift by h/2)."""
    node = _first(cmds.polyCube(width=w, height=h, depth=d, subdivisionsX=sx, subdivisionsY=sy, subdivisionsZ=sz, constructionHistory=False, name=name), name)
    return _pos(node, x, y + h / 2.0, z)


def _cyl(name: str, radius: float, h: float, x: float = 0.0, y: float = 0.0, z: float = 0.0, sides: int = 16, sub_h: int = 1) -> str:
    node = _first(cmds.polyCylinder(radius=radius, height=h, subdivisionsAxis=sides, subdivisionsHeight=sub_h, subdivisionsCaps=1, constructionHistory=False, name=name), name)
    return _pos(node, x, y + h / 2.0, z)


def _cone(name: str, radius: float, h: float, x: float, y: float, z: float, sides: int = 16) -> str:
    node = _first(cmds.polyCone(radius=radius, height=h, subdivisionsAxis=sides, subdivisionsHeight=1, subdivisionsCaps=0, constructionHistory=False, name=name), name)
    return _pos(node, x, y + h / 2.0, z)


def _sphere(name: str, radius: float, x: float, y: float, z: float, sub: int = 12) -> str:
    node = _first(cmds.polySphere(radius=radius, subdivisionsX=sub, subdivisionsY=sub, constructionHistory=False, name=name), name)
    return _pos(node, x, y, z)


def _bar_between(name: str, a: Sequence[float], b: Sequence[float], radius: float, sides: int = 8) -> str:
    """Cylinder whose axis runs from point a to point b."""
    d = [b[i] - a[i] for i in range(3)]
    length = math.sqrt(sum(v * v for v in d)) or 1.0
    node = _first(cmds.polyCylinder(radius=radius, height=length, subdivisionsAxis=sides, subdivisionsHeight=1, subdivisionsCaps=1, constructionHistory=False, name=name), name)
    rot = cmds.angleBetween(euler=True, v1=[0.0, 1.0, 0.0], v2=[d[0] / length, d[1] / length, d[2] / length]) or [0.0, 0.0, 0.0]
    _rot(node, rot[0], rot[1], rot[2])
    return _pos(node, (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0, (a[2] + b[2]) / 2.0)


def _extrude(node: str, faces: Sequence[int], **flags: Any) -> None:
    if faces:
        cmds.polyExtrudeFacet(*["%s.f[%d]" % (node, f) for f in faces], keepFacesTogether=False, constructionHistory=False, **flags)


def _unite(name: str, parts: Sequence[str]) -> str:
    """Combine parts into one mesh named ``name`` (single part is just renamed)."""
    if len(parts) == 1:
        return cmds.rename(parts[0], name) or name
    result = _first(cmds.polyUnite(*parts, constructionHistory=False, mergeUVSets=1, name=name), name)
    leftovers = [p for p in parts if p != result and cmds.objExists(p)]
    if leftovers:
        cmds.delete(leftovers)
    return result


def _faces(node: str) -> int:
    value = cmds.polyEvaluate(node, face=True)
    return int(value) if isinstance(value, (int, float)) else 0


def _bbox(node: str) -> Dict[str, Any]:
    return _util.world_bbox([node]) or {"min": [0.0, 0.0, 0.0], "max": [0.0, 0.0, 0.0], "size": [0.0, 0.0, 0.0], "center": [0.0, 0.0, 0.0]}


def _pivot_to_base(node: str) -> None:
    bb = _bbox(node)
    cmds.xform(node, worldSpace=True, pivots=[bb["center"][0], bb["min"][1], bb["center"][2]])


def _finish(name: str, parts: Sequence[str], extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Freeze parts, group them, set pivots at the base centre and build the standard reply."""
    parts = [p for p in parts if p]
    if not parts:
        raise BridgeError("nothing was built; check the parameters")
    for p in parts:
        cmds.makeIdentity(p, apply=True, translate=True, rotate=True, scale=True, normal=0, preserveNormals=True)
        _pivot_to_base(p)
    grp = _util.long_name(cmds.group(parts, name=name + "_grp") or name + "_grp")
    _pivot_to_base(grp)
    bb = _bbox(grp)
    out: Dict[str, Any] = {
        "group": grp,
        "parts": [_util.long_name(p) for p in parts],
        "bbox": bb,
        "stats": {"faces": sum(_faces(p) for p in parts), "width": bb["size"][0], "depth": bb["size"][2], "height": bb["size"][1], "parts": len(parts)},
    }
    if extra:
        out.update(extra)
    return out


def _check(value: Any, label: str, low: float, high: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise BridgeError("%s must be a number" % label) from None
    if not low <= v <= high:
        raise BridgeError("%s must be between %g and %g (cm), got %g" % (label, low, high, v))
    return v


def _choice(value: Any, label: str, options: Sequence[str]) -> str:
    v = str(value or options[0]).lower()
    if v not in options:
        raise BridgeError("%s must be one of %s" % (label, ", ".join(options)))
    return v


# building --------------------------------------------------------------------
def _facade_windows(body: str, name: str, cols_x: int, cols_z: int, floors: int, fh: float, bay_x: float, bay_z: float, ww: float, wh: float, sill: float, reveal: float, shopfront: bool, entrance: bool) -> Dict[str, Any]:
    """Cut windows, a shopfront and a door into a subdivided box by scaling then pushing in faces."""
    sx, sy, sz = cols_x, floors, cols_z
    groups: Dict[Tuple[float, float, float], List[int]] = {}

    def add(side: str, row: int, col: int, w: float, h: float, base: float) -> None:
        bay = bay_x if side in ("front", "back") else bay_z
        key = (round(min(w / bay, 0.92), 4), round(min(h / fh, 0.92), 4), round(base + h / 2.0 - fh / 2.0, 3))
        groups.setdefault(key, []).append(cube_face(side, row, col, sx, sy, sz))

    door_col = cols_x // 2 if entrance else -1
    for row in range(floors):
        for side, cols in (("front", cols_x), ("back", cols_x), ("right", cols_z), ("left", cols_z)):
            for col in range(cols):
                if row == 0 and side == "front" and col == door_col:
                    add(side, row, col, 90.0, 210.0, 0.0)
                elif row == 0 and side == "front" and shopfront:
                    add(side, row, col, bay_x - 30.0, fh - 50.0, 0.0)
                else:
                    add(side, row, col, ww, wh, sill)
    count = 0
    for (scale_x, scale_y, offset_y), faces in groups.items():
        _extrude(body, faces, localScaleX=scale_x, localScaleY=scale_y, localTranslateY=offset_y)
        _extrude(body, faces, localTranslateZ=-reveal)
        count += len(faces)
    return {"openings": count, "door": entrance, "shopfront": shopfront}


def _roof(name: str, kind: str, width: float, depth: float, top: float, pitch: float = 30.0, overhang: float = 30.0) -> List[str]:
    if kind == "flat":
        return []
    if kind == "parapet":
        p = _box(name + "_parapet_geo", width, 90.0, depth, y=top)
        _extrude(p, [1], offset=20.0)
        _extrude(p, [1], localTranslateZ=-80.0)
        return [p]
    rise = (depth / 2.0 + overhang) * math.tan(math.radians(pitch))
    if kind == "pitched":
        x0 = -width / 2.0 - overhang
        profile = [(x0, top, -depth / 2.0 - overhang), (x0, top + rise, 0.0), (x0, top, depth / 2.0 + overhang)]
        roof = _first(cmds.polyCreateFacet(point=profile, constructionHistory=False, name=name + "_roof_geo"), name + "_roof_geo")
        cmds.polyExtrudeFacet(roof + ".f[0]", translate=[width + 2.0 * overhang, 0.0, 0.0], constructionHistory=False)
        return [roof]
    # hip: a fascia slab whose top face is shrunk to a ridge and lifted (top face local X runs along world X).
    roof = _box(name + "_roof_geo", width + 2.0 * overhang, 8.0, depth + 2.0 * overhang, y=top)
    ridge = max(0.02, (width - depth) / (width + 2.0 * overhang)) if width > depth else 0.02
    _extrude(roof, [1], localScaleX=ridge, localScaleY=0.02, localTranslateZ=rise)
    return [roof]


@command("procgen.building", mutates=True)
def building(
    name: str = "building",
    width: float = 1200.0,
    depth: float = 1000.0,
    floors: int = 3,
    floor_height: float = 320.0,
    style: str = "flat",
    window_width: float = 120.0,
    window_height: float = 150.0,
    window_spacing: float = 300.0,
    sill: float = 90.0,
    mullions: bool = False,
    shopfront: bool = False,
    cornice: bool = True,
    roof: str = "flat",
    entrance: bool = True,
    footprint: List[List[float]] | None = None,
    reveal: float | None = None,
) -> Dict[str, Any]:
    """Parametric building: facade window grid cut into a subdivided box, optional shopfront, cornice and roof.

    ``footprint`` ([[x, z], ...], 3+ points) extrudes a custom plan instead of the box; windows are skipped then.
    """
    require_maya()
    width, depth = _check(width, "width", 200, 50000), _check(depth, "depth", 200, 50000)
    floors, fh = int(_check(floors, "floors", 1, 120)), _check(floor_height, "floor_height", 200, 1000)
    style, roof = _choice(style, "style", STYLES), _choice(roof, "roof", ROOFS)
    ww, wh, sill = _check(window_width, "window_width", 20, 1000), _check(window_height, "window_height", 20, 1000), _check(sill, "sill", 0, 500)
    spacing = _check(window_spacing, "window_spacing", ww + 10, 5000)
    if wh + sill > fh:
        raise BridgeError("sill + window_height (%g) must fit inside floor_height (%g)" % (wh + sill, fh))
    reveal = float(reveal) if reveal is not None else REVEAL[style]
    height = floors * fh
    parts: List[str] = []
    facade: Dict[str, Any] = {"openings": 0}
    if footprint:
        if len(footprint) < 3:
            raise BridgeError("footprint needs at least 3 [x, z] points")
        pts = [(float(p[0]), 0.0, float(p[1])) for p in footprint]
        body = _first(cmds.polyCreateFacet(point=pts, constructionHistory=False, name=name + "_body_geo"), name + "_body_geo")
        cmds.polyExtrudeFacet(body + ".f[0]", translate=[0.0, height, 0.0], constructionHistory=False)
        cols_x = cols_z = 1
        xs, zs = [p[0] for p in pts], [p[2] for p in pts]
        width, depth = max(xs) - min(xs), max(zs) - min(zs)
    else:
        cols_x, cols_z = max(1, int(round(width / spacing))), max(1, int(round(depth / spacing)))
        body = _box(name + "_body_geo", width, height, depth, sx=cols_x, sy=floors, sz=cols_z)
        facade = _facade_windows(body, name, cols_x, cols_z, floors, fh, width / cols_x, depth / cols_z, ww, wh, sill, reveal, bool(shopfront), bool(entrance))
    parts.append(body)
    if mullions and not footprint:
        parts.append(_mullions(name, cols_x, cols_z, floors, fh, width, depth, wh, sill))
    if cornice:
        parts.append(_box(name + "_cornice_geo", width + 40.0, 25.0, depth + 40.0, y=height - 25.0))
    parts.extend(_roof(name, roof, width, depth, height))
    extra = {"style": style, "roof": roof, "floors": floors, "floor_height": fh, "reveal": reveal, "window_columns": [cols_x, cols_z], "facade": facade}
    return _finish(name, parts, extra)


def _mullions(name: str, cols_x: int, cols_z: int, floors: int, fh: float, width: float, depth: float, wh: float, sill: float) -> str:
    """One vertical bar per window bay on every side, combined into a single mesh."""
    bars: List[str] = []
    bay_x, bay_z = width / cols_x, depth / cols_z
    for row in range(floors):
        y = row * fh + sill
        for col in range(cols_x):
            x = -width / 2.0 + bay_x * (col + 0.5)
            bars.append(_box("%s_mullion%d" % (name, len(bars)), 4.0, wh, 6.0, x, y, depth / 2.0))
            bars.append(_box("%s_mullion%d" % (name, len(bars)), 4.0, wh, 6.0, x, y, -depth / 2.0))
        for col in range(cols_z):
            z = -depth / 2.0 + bay_z * (col + 0.5)
            bars.append(_box("%s_mullion%d" % (name, len(bars)), 6.0, wh, 4.0, width / 2.0, y, z))
            bars.append(_box("%s_mullion%d" % (name, len(bars)), 6.0, wh, 4.0, -width / 2.0, y, z))
    return _unite(name + "_mullions_geo", bars)


# street block ----------------------------------------------------------------
def _lamp(name: str, x: float, z: float, height: float = 600.0) -> List[str]:
    return [_cyl(name + "_pole", 6.0, height, x, 0.0, z, sides=8), _box(name + "_head", 60.0, 20.0, 25.0, x, height, z)]


def _tree_parts(name: str, height: float, canopy: str, trunk_ratio: float, x: float = 0.0, z: float = 0.0) -> List[str]:
    trunk_h = height * trunk_ratio
    crown_h = height - trunk_h
    parts = [_cyl(name + "_trunk_geo", max(4.0, height * 0.02), trunk_h, x, 0.0, z, sides=8)]
    if canopy == "conical":
        parts.append(_cone(name + "_canopy_geo", crown_h * 0.35, crown_h, x, trunk_h, z))
    else:
        r = crown_h / 2.0
        node = _sphere(name + "_canopy_geo", r, x, trunk_h + r, z)
        if canopy == "columnar":
            cmds.xform(node, scale=[0.5, 1.0, 0.5])
        elif canopy == "umbrella":
            cmds.xform(node, scale=[1.3, 0.45, 1.3])
            _pos(node, x, trunk_h + r * 0.45, z)
        parts.append(node)
    return parts


@command("procgen.street_block", mutates=True)
def street_block(
    name: str = "block",
    lots: int = 4,
    lot_width: float = 1200.0,
    lot_depth: float = 1000.0,
    road_width: float = 700.0,
    sidewalk: float = 250.0,
    curb: float = 15.0,
    floors_min: int = 2,
    floors_max: int = 5,
    style: str = "flat",
    lamp_spacing: float = 1500.0,
    tree_spacing: float = 1000.0,
    lamps: bool = True,
    trees: bool = True,
    seed: int | None = None,
) -> Dict[str, Any]:
    """A street: road (two 350 cm lanes by default), raised sidewalks with a curb, a row of buildings on each side, lamp posts and tree proxies.

    The road runs along X and is centred on the origin; buildings face the road.
    """
    require_maya()
    lots = int(_check(lots, "lots", 1, 40))
    lot_w, lot_d = _check(lot_width, "lot_width", 300, 10000), _check(lot_depth, "lot_depth", 300, 10000)
    road, walk, curb = _check(road_width, "road_width", 300, 5000), _check(sidewalk, "sidewalk", 0, 1000), _check(curb, "curb", 0, 50)
    fmin, fmax = int(_check(floors_min, "floors_min", 1, 100)), int(_check(floors_max, "floors_max", 1, 100))
    if fmin > fmax:
        raise BridgeError("floors_min must not exceed floors_max")
    rng, seed = _rng(seed)
    length = lots * lot_w
    parts = [_box(name + "_road_geo", length, 2.0, road, 0.0, -2.0, 0.0)]
    buildings: List[Dict[str, Any]] = []
    for side, sign in (("north", 1.0), ("south", -1.0)):
        z_walk = sign * (road / 2.0 + walk / 2.0)
        if walk > 0:
            parts.append(_box("%s_sidewalk_%s_geo" % (name, side), length, curb, walk, 0.0, 0.0, z_walk))
        for i in range(lots):
            floors = rng.randint(fmin, fmax)
            x = -length / 2.0 + lot_w * (i + 0.5)
            z = sign * (road / 2.0 + walk + lot_d / 2.0)
            b = building("%s_%s_%d" % (name, side, i), lot_w - 20.0, lot_d, floors, style=style, roof=rng.choice(("flat", "parapet")), shopfront=rng.random() < 0.5)
            grp = b["group"]
            _pos(grp, x, curb, z)
            if sign < 0:
                _rot(grp, 0.0, 180.0, 0.0)
            buildings.append({"group": grp, "floors": floors, "x": x, "z": z})
            parts.append(grp)
        if lamps and walk > 0:
            for k in range(int(length // lamp_spacing) + 1):
                parts.extend(_lamp("%s_lamp_%s_%d" % (name, side, k), -length / 2.0 + lamp_spacing * k + lamp_spacing / 2.0, sign * (road / 2.0 + 40.0)))
        if trees and walk > 0:
            for k in range(int(length // tree_spacing)):
                h = rng.uniform(500.0, 900.0)
                parts.extend(_tree_parts("%s_tree_%s_%d" % (name, side, k), h, "round", 0.35, -length / 2.0 + tree_spacing * (k + 0.5), sign * (road / 2.0 + walk - 60.0)))
    return _finish(name, parts, {"seed": seed, "buildings": buildings, "lanes": int(road // 350) or 1, "length": length})


# room shell ------------------------------------------------------------------
@command("procgen.room_shell", mutates=True)
def room_shell(
    name: str = "room",
    width: float = 500.0,
    depth: float = 400.0,
    height: float = 280.0,
    wall_thickness: float = 20.0,
    ceiling: bool = True,
    openings: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    """Interior box built from closed slabs (floor, ceiling, 4 walls) so normals stay correct on both faces.

    ``openings`` = [{wall: front|back|left|right, kind: door|window, width, height, sill, offset}] cut with booleans.
    Defaults: door 90 x 210 on the floor, window 120 x 150 with a 90 sill. ``offset`` slides along the wall from its centre.
    """
    require_maya()
    w, d, h = _check(width, "width", 100, 20000), _check(depth, "depth", 100, 20000), _check(height, "height", 100, 2000)
    t = _check(wall_thickness, "wall_thickness", 1, 200)
    parts = [_box(name + "_floor_geo", w + 2 * t, t, d + 2 * t, 0.0, -t, 0.0)]
    if ceiling:
        parts.append(_box(name + "_ceiling_geo", w + 2 * t, t, d + 2 * t, 0.0, h, 0.0))
    # wall name -> (size x, size z, centre x, centre z, axis the opening slides along)
    walls = {
        "front": (w + 2 * t, t, 0.0, d / 2.0 + t / 2.0, "x"),
        "back": (w + 2 * t, t, 0.0, -d / 2.0 - t / 2.0, "x"),
        "right": (t, d, w / 2.0 + t / 2.0, 0.0, "z"),
        "left": (t, d, -w / 2.0 - t / 2.0, 0.0, "z"),
    }
    nodes = {k: _box("%s_wall_%s_geo" % (name, k), sx, h, sz, cx, 0.0, cz) for k, (sx, sz, cx, cz, _) in walls.items()}
    cut = 0
    for i, op in enumerate(openings or []):
        wall = _choice(op.get("wall"), "opening wall", tuple(walls))
        kind = _choice(op.get("kind", "door"), "opening kind", ("door", "window"))
        ow = float(op.get("width", 90.0 if kind == "door" else 120.0))
        oh = float(op.get("height", 210.0 if kind == "door" else 150.0))
        base = float(op.get("sill", 0.0 if kind == "door" else 90.0))
        off = float(op.get("offset", 0.0))
        sx, sz, cx, cz, axis = walls[wall]
        if axis == "x":
            cutter = _box("%s_cut%d" % (name, i), ow, oh, t * 3.0, cx + off, base, cz)
        else:
            cutter = _box("%s_cut%d" % (name, i), t * 3.0, oh, ow, cx, base, cz + off)
        nodes[wall] = _first(cmds.polyBoolOp(nodes[wall], cutter, operation=2, constructionHistory=False, name="%s_wall_%s_geo" % (name, wall)), nodes[wall])
        cut += 1
    parts.extend(nodes.values())
    return _finish(name, parts, {"openings": cut, "interior": [w, h, d], "wall_thickness": t})


# stairs, railing, pipes, fence, column ---------------------------------------
@command("procgen.stairs", mutates=True)
def stairs(name: str = "stairs", rise: float = 17.0, run: float = 28.0, steps: int | None = None, total_rise: float | None = None, width: float = 100.0, landing: float = 0.0) -> Dict[str, Any]:
    """Solid straight stair climbing along +Z. Give ``steps`` or ``total_rise`` (steps = ceil(total_rise / rise))."""
    require_maya()
    rise, run, width = _check(rise, "rise", 5, 40), _check(run, "run", 15, 60), _check(width, "width", 30, 2000)
    if total_rise is not None:
        steps = int(math.ceil(_check(total_rise, "total_rise", rise, 5000) / rise))
    steps = int(_check(steps if steps is not None else 12, "steps", 1, 400))
    landing = _check(landing, "landing", 0, 1000)
    blocks = [_box("%s_step%d" % (name, i), width, rise * (i + 1), run, 0.0, 0.0, run * (i + 0.5)) for i in range(steps)]
    if landing > 0:
        blocks.append(_box(name + "_landing", width, rise * steps, landing, 0.0, 0.0, run * steps + landing / 2.0))
    mesh = _unite(name + "_geo", blocks)
    return _finish(name, [mesh], {"steps": steps, "rise": rise, "run": run, "total_rise": rise * steps, "total_run": run * steps + landing, "angle_deg": round(math.degrees(math.atan2(rise, run)), 2)})


def _curve_points(curve: str, count: int) -> Tuple[List[List[float]], List[List[float]]]:
    """Evenly spaced (by parameter percentage) positions and unit tangents along a curve."""
    pts, tans = [], []
    for i in range(count):
        t = i / float(max(1, count - 1))
        p = cmds.pointOnCurve(curve, turnOnPercentage=True, parameter=t, position=True) or [0.0, 0.0, 0.0]
        tan = cmds.pointOnCurve(curve, turnOnPercentage=True, parameter=t, normalizedTangent=True) or [1.0, 0.0, 0.0]
        pts.append([float(v) for v in p])
        tans.append([float(v) for v in tan])
    return pts, tans


@command("procgen.railing", mutates=True)
def railing(name: str = "railing", length: float = 300.0, height: float = 100.0, post_spacing: float = 120.0, post_diameter: float = 4.0, rail_diameter: float = 4.0, mid_rails: int = 1, curve: str | None = None) -> Dict[str, Any]:
    """Posts plus top and mid rails, straight along +X or following a curve (posts at curve samples)."""
    require_maya()
    height, spacing = _check(height, "height", 20, 300), _check(post_spacing, "post_spacing", 20, 1000)
    pr, rr = _check(post_diameter, "post_diameter", 1, 30) / 2.0, _check(rail_diameter, "rail_diameter", 1, 30) / 2.0
    mid_rails = int(_check(mid_rails, "mid_rails", 0, 10))
    if curve:
        require_nodes([curve])
        arc = cmds.arclen(curve) or length
        count = max(2, int(math.ceil(float(arc) / spacing)) + 1)
        pts, _ = _curve_points(curve, count)
    else:
        length = _check(length, "length", spacing, 100000)
        count = int(math.ceil(length / spacing)) + 1
        pts = [[length * i / float(count - 1), 0.0, 0.0] for i in range(count)]
    if count > MAX_PARTS:
        raise BridgeError("too many posts (%d); raise post_spacing" % count)
    parts = [_cyl("%s_post%d" % (name, i), pr, height, p[0], p[1], p[2], sides=8) for i, p in enumerate(pts)]
    levels = [height] + [height * (k + 1) / float(mid_rails + 1) for k in range(mid_rails)]
    for li, level in enumerate(levels):
        for i in range(count - 1):
            a = [pts[i][0], pts[i][1] + level, pts[i][2]]
            b = [pts[i + 1][0], pts[i + 1][1] + level, pts[i + 1][2]]
            parts.append(_bar_between("%s_rail%d_%d" % (name, li, i), a, b, rr))
    mesh = _unite(name + "_geo", parts)
    return _finish(name, [mesh], {"posts": count, "rails": len(levels), "curve": _util.long_name(curve) if curve else None})


@command("procgen.pipes_along_curve", mutates=True)
def pipes_along_curve(name: str = "pipe", curve: str = "", radius: float = 5.0, segments: int = 12, count: int = 1, spacing: float | None = None, divisions: int | None = None) -> Dict[str, Any]:
    """Round pipes swept along a curve (polyExtrudeFacet with inputCurve). Several pipes run side by side offset along X."""
    require_maya()
    if not curve:
        raise BridgeError("curve is required (see modeling.create_curve)")
    require_nodes([curve])
    radius, segments = _check(radius, "radius", 0.2, 500), int(_check(segments, "segments", 3, 64))
    count = int(_check(count, "count", 1, 50))
    gap = float(spacing) if spacing is not None else radius * 3.0
    arc = float(cmds.arclen(curve) or 1000.0)
    divs = int(divisions) if divisions is not None else max(8, min(200, int(arc / (radius * 4.0))))
    pts, tans = _curve_points(curve, 1)
    start, tangent = pts[0], tans[0]
    rot = cmds.angleBetween(euler=True, v1=[0.0, 1.0, 0.0], v2=tangent) or [0.0, 0.0, 0.0]
    parts: List[str] = []
    for i in range(count):
        off = (i - (count - 1) / 2.0) * gap
        node = _first(cmds.polyCylinder(radius=radius, height=0.1, subdivisionsAxis=segments, subdivisionsHeight=1, subdivisionsCaps=1, constructionHistory=False, name="%s_%d_geo" % (name, i)), "%s_%d_geo" % (name, i))
        _rot(node, rot[0], rot[1], rot[2])
        _pos(node, start[0] + off, start[1], start[2])
        # cap faces come after the side faces: bottom = segments, top = segments + 1
        cmds.polyExtrudeFacet("%s.f[%d]" % (node, segments + 1), inputCurve=curve, divisions=divs, constructionHistory=False, keepFacesTogether=True)
        parts.append(node)
    return _finish(name, parts, {"curve": _util.long_name(curve), "arc_length": arc, "divisions": divs, "count": count})


@command("procgen.fence", mutates=True)
def fence(name: str = "fence", length: float = 1000.0, height: float = 120.0, post_spacing: float = 200.0, rails: int = 2, pickets: bool = True, picket_width: float = 8.0, picket_gap: float = 6.0, post_size: float = 10.0) -> Dict[str, Any]:
    """Picket or rail fence along +X starting at the origin: square posts, horizontal rails, optional pickets."""
    require_maya()
    length, height = _check(length, "length", 50, 100000), _check(height, "height", 30, 400)
    spacing, rails = _check(post_spacing, "post_spacing", 30, 1000), int(_check(rails, "rails", 1, 6))
    pw, gap, ps = _check(picket_width, "picket_width", 1, 50), _check(picket_gap, "picket_gap", 0, 100), _check(post_size, "post_size", 2, 50)
    posts = int(math.ceil(length / spacing)) + 1
    n_pickets = int(length // (pw + gap)) if pickets else 0
    if posts + n_pickets > MAX_PARTS:
        raise BridgeError("fence would need %d parts; shorten it or widen the pickets" % (posts + n_pickets))
    parts = [_box("%s_post%d" % (name, i), ps, height + 5.0, ps, min(length, spacing * i), 0.0, 0.0) for i in range(posts)]
    for r in range(rails):
        y = height * (r + 1) / float(rails + 1)
        parts.append(_box("%s_rail%d" % (name, r), length, 4.0, 3.0, length / 2.0, y, ps / 2.0 + 1.5))
    for k in range(n_pickets):
        parts.append(_box("%s_picket%d" % (name, k), pw, height, 2.0, (pw + gap) * k + pw / 2.0, 0.0, ps / 2.0 + 4.0))
    mesh = _unite(name + "_geo", parts)
    return _finish(name, [mesh], {"posts": posts, "rails": rails, "pickets": n_pickets})


@command("procgen.column", mutates=True)
def column(name: str = "column", order: str = "doric", height: float = 400.0, diameter: float | None = None, taper: float = 0.85) -> Dict[str, Any]:
    """Classical column proxy: tapered shaft, base and an order specific capital (plain skips base and capital).

    Diameter defaults follow the order (height / 7 tuscan, / 8 doric, / 9 ionic, / 10 corinthian).
    """
    require_maya()
    order, height = _choice(order, "order", ORDERS), _check(height, "height", 50, 5000)
    ratio = {"plain": 8.0, "tuscan": 7.0, "doric": 8.0, "ionic": 9.0, "corinthian": 10.0}[order]
    r = (_check(diameter, "diameter", 5, 1000) if diameter is not None else height / ratio) / 2.0
    taper = _check(taper, "taper", 0.5, 1.0)
    sides = 24
    base_h = 0.0 if order == "plain" else r * 0.5
    cap_h = {"plain": 0.0, "tuscan": r * 0.6, "doric": r * 0.6, "ionic": r * 0.8, "corinthian": r * 2.2}[order]
    shaft_h = height - base_h - cap_h
    shaft = _cyl(name + "_shaft_geo", r, shaft_h, 0.0, base_h, 0.0, sides=sides)
    # top ring vertices of a polyCylinder follow the bottom ring: vtx[sides : 2 * sides - 1]
    cmds.scale(taper, 1.0, taper, "%s.vtx[%d:%d]" % (shaft, sides, 2 * sides - 1), pivot=[0.0, base_h + shaft_h, 0.0], relative=True)
    parts = [shaft]
    if order != "plain":
        parts.append(_box(name + "_plinth_geo", r * 3.2, base_h * 0.5, r * 3.2))
        parts.append(_cyl(name + "_torus_geo", r * 1.3, base_h * 0.5, 0.0, base_h * 0.5, 0.0, sides=sides))
    top = base_h + shaft_h
    if order in ("tuscan", "doric"):
        parts.append(_cyl(name + "_echinus_geo", r * 1.3, cap_h * 0.5, 0.0, top, 0.0, sides=sides))
        parts.append(_box(name + "_abacus_geo", r * 2.8, cap_h * 0.5, r * 2.8, 0.0, top + cap_h * 0.5, 0.0))
    elif order == "ionic":
        for i, sx in enumerate((-1.0, 1.0)):
            v = _cyl("%s_volute%d_geo" % (name, i), r * 0.45, r * 2.2, sx * r * 1.05, top, 0.0, sides=16)
            _rot(v, 90.0, 0.0, 0.0)
            _pos(v, sx * r * 1.05, top + r * 0.45, 0.0)
            parts.append(v)
        parts.append(_box(name + "_abacus_geo", r * 2.6, cap_h - r * 0.9, r * 2.6, 0.0, top + r * 0.9, 0.0))
    elif order == "corinthian":
        bell = _cone(name + "_bell_geo", r * 1.6, cap_h * 0.8, 0.0, top, 0.0, sides=sides)
        _rot(bell, 180.0, 0.0, 0.0)
        parts.extend([bell, _box(name + "_abacus_geo", r * 3.0, cap_h * 0.2, r * 3.0, 0.0, top + cap_h * 0.8, 0.0)])
    return _finish(name, parts, {"order": order, "diameter": r * 2.0, "shaft_height": shaft_h})


# proxies ---------------------------------------------------------------------
def _furniture_parts(name: str, kind: str, w: float, h: float, d: float) -> List[str]:
    p: List[str] = []

    def n(part: str) -> str:
        return "%s_%s_geo" % (name, part)

    if kind in ("table", "desk"):
        p.append(_box(n("top"), w, 4.0, d, 0.0, h - 4.0, 0.0))
        if kind == "table":
            for i, (sx, sz) in enumerate(((-1, -1), (1, -1), (-1, 1), (1, 1))):
                p.append(_box(n("leg%d" % i), 5.0, h - 4.0, 5.0, sx * (w / 2.0 - 8.0), 0.0, sz * (d / 2.0 - 8.0)))
        else:
            p.append(_box(n("side_l"), 3.0, h - 4.0, d - 4.0, -w / 2.0 + 1.5, 0.0, 0.0))
            p.append(_box(n("side_r"), 3.0, h - 4.0, d - 4.0, w / 2.0 - 1.5, 0.0, 0.0))
            p.append(_box(n("modesty"), w - 6.0, 30.0, 2.0, 0.0, h - 34.0, -d / 2.0 + 4.0))
    elif kind == "chair":
        seat = 45.0
        p.append(_box(n("seat"), w, 4.0, w, 0.0, seat - 4.0, 0.0))
        for i, (sx, sz) in enumerate(((-1, -1), (1, -1), (-1, 1), (1, 1))):
            p.append(_box(n("leg%d" % i), 4.0, seat - 4.0, 4.0, sx * (w / 2.0 - 4.0), 0.0, sz * (w / 2.0 - 4.0)))
        p.append(_box(n("back"), w, h - seat, 3.0, 0.0, seat, -w / 2.0 + 1.5))
    elif kind == "sofa":
        p.append(_box(n("seat"), w, 45.0, d, 0.0, 0.0, 0.0))
        p.append(_box(n("back"), w, h - 45.0, 22.0, 0.0, 45.0, -d / 2.0 + 11.0))
        p.append(_box(n("arm_l"), 20.0, 20.0, d - 22.0, -w / 2.0 + 10.0, 45.0, 11.0))
        p.append(_box(n("arm_r"), 20.0, 20.0, d - 22.0, w / 2.0 - 10.0, 45.0, 11.0))
    elif kind == "bed":
        p.append(_box(n("frame"), w, 30.0, d, 0.0, 0.0, 0.0))
        p.append(_box(n("mattress"), w - 4.0, 20.0, d - 4.0, 0.0, 30.0, 0.0))
        p.append(_box(n("headboard"), w, h - 30.0, 5.0, 0.0, 30.0, -d / 2.0 + 2.5))
    elif kind == "shelf":
        p.append(_box(n("side_l"), 3.0, h, d, -w / 2.0 + 1.5, 0.0, 0.0))
        p.append(_box(n("side_r"), 3.0, h, d, w / 2.0 - 1.5, 0.0, 0.0))
        p.append(_box(n("back"), w, h, 2.0, 0.0, 0.0, -d / 2.0 + 1.0))
        for i in range(6):
            p.append(_box(n("shelf%d" % i), w - 6.0, 3.0, d - 2.0, 0.0, min(h - 3.0, i * (h - 3.0) / 5.0), 1.0))
    else:  # lamp
        p.append(_cyl(n("base"), w / 2.0 * 0.75, 3.0, 0.0, 0.0, 0.0))
        p.append(_cyl(n("pole"), 1.5, h - 33.0, 0.0, 3.0, 0.0, sides=8))
        p.append(_cone(n("shade"), w / 2.0, 30.0, 0.0, h - 30.0, 0.0))
    return p


@command("procgen.furniture_proxy", mutates=True)
def furniture_proxy(name: str | None = None, kind: str = "table", width: float | None = None, height: float | None = None, depth: float | None = None) -> Dict[str, Any]:
    """Blockout furniture at real dimensions (table 120x75x80, chair seat 45, sofa 200x85x90, bed 160x110x200, desk 140x75x70, shelf 80x200x30, floor lamp 150 high)."""
    require_maya()
    kind = _choice(kind, "kind", tuple(FURNITURE))
    dw, dh, dd = FURNITURE[kind]
    w = _check(width, "width", 10, 1000) if width is not None else dw
    h = _check(height, "height", 10, 400) if height is not None else dh
    d = _check(depth, "depth", 10, 1000) if depth is not None else dd
    name = name or kind
    return _finish(name, _furniture_parts(name, kind, w, h, d), {"kind": kind, "dimensions": [w, h, d]})


@command("procgen.vehicle_proxy", mutates=True)
def vehicle_proxy(name: str | None = None, kind: str = "car", length: float | None = None, width: float | None = None, height: float | None = None) -> Dict[str, Any]:
    """Blockout vehicle facing +X at real size (car 450x180x150, van 500x200x220, bus 1200x255x320 as length x width x height)."""
    require_maya()
    kind = _choice(kind, "kind", tuple(VEHICLES))
    dl, dw, dh, wheel, base = VEHICLES[kind]
    ln = _check(length, "length", 100, 3000) if length is not None else dl
    w = _check(width, "width", 50, 400) if width is not None else dw
    h = _check(height, "height", 50, 500) if height is not None else dh
    name = name or kind
    clearance = wheel * 0.3
    parts: List[str] = []
    if kind == "car":
        body_h = (h - clearance) * 0.45
        parts.append(_box(name + "_body_geo", ln, body_h, w, 0.0, clearance, 0.0))
        parts.append(_box(name + "_cabin_geo", ln * 0.5, h - clearance - body_h, w * 0.92, -ln * 0.05, clearance + body_h, 0.0))
    else:
        parts.append(_box(name + "_body_geo", ln, h - clearance, w, 0.0, clearance, 0.0))
        parts.append(_box(name + "_bumper_geo", 12.0, 25.0, w, ln / 2.0 + 6.0, clearance, 0.0))
    axles = [-base / 2.0, base / 2.0] if kind != "bus" else [-base / 2.0, base / 2.0 - 100.0, base / 2.0]
    for i, ax in enumerate(axles):
        for j, side in enumerate((-1.0, 1.0)):
            wh = _cyl("%s_wheel%d%d_geo" % (name, i, j), wheel / 2.0, wheel * 0.3, ax, 0.0, side * (w / 2.0 - wheel * 0.15), sides=16)
            _rot(wh, 90.0, 0.0, 0.0)
            _pos(wh, ax, wheel / 2.0, side * (w / 2.0 - wheel * 0.15))
            parts.append(wh)
    return _finish(name, parts, {"kind": kind, "forward": "+x", "dimensions": [ln, w, h], "wheel_diameter": wheel})


@command("procgen.tree_proxy", mutates=True)
def tree_proxy(name: str = "tree", height: float = 800.0, canopy: str = "round", trunk_ratio: float = 0.3) -> Dict[str, Any]:
    """Trunk cylinder plus a canopy proxy: round, conical, columnar or umbrella."""
    require_maya()
    height, canopy = _check(height, "height", 50, 10000), _choice(canopy, "canopy", CANOPIES)
    ratio = _check(trunk_ratio, "trunk_ratio", 0.05, 0.9)
    return _finish(name, _tree_parts(name, height, canopy, ratio), {"canopy": canopy, "trunk_height": height * ratio})


def _vertex_positions(node: str, count: int) -> List[List[float]]:
    flat = cmds.xform("%s.vtx[*]" % node, query=True, worldSpace=True, translation=True) or []
    if len(flat) >= count * 3:
        return [[float(flat[i * 3]), float(flat[i * 3 + 1]), float(flat[i * 3 + 2])] for i in range(count)]
    return [[0.0, 0.0, 0.0] for _ in range(count)]


def _vertex_count(node: str) -> int:
    value = cmds.polyEvaluate(node, vertex=True)
    return int(value) if isinstance(value, (int, float)) else 0


@command("procgen.rock", mutates=True)
def rock(name: str = "rock", size: float = 100.0, subdivisions: int = 12, noise: float = 0.25, seed: int | None = None, flatten: float = 0.7) -> Dict[str, Any]:
    """Noise displaced sphere squashed to ``flatten`` on Y. ``noise`` is the displacement as a fraction of size."""
    require_maya()
    size, sub = _check(size, "size", 1, 10000), int(_check(subdivisions, "subdivisions", 4, 60))
    amount, flatten = _check(noise, "noise", 0, 1), _check(flatten, "flatten", 0.2, 1.5)
    rng, seed = _rng(seed)
    r = size / 2.0
    node = _sphere(name + "_geo", r, 0.0, r * flatten, 0.0, sub)
    cmds.xform(node, scale=[1.0, flatten, 1.0])
    cmds.makeIdentity(node, apply=True, translate=True, rotate=True, scale=True, normal=0, preserveNormals=True)
    n = _vertex_count(node)
    feature = size * 0.5
    phase = rng.uniform(0.0, 1000.0)
    for i, p in enumerate(_vertex_positions(node, n)):
        dx, dy, dz = p[0], p[1] - r * flatten, p[2]
        ln = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
        k = (value_noise((dx + 0.5 * dy) / feature + phase, (dz + 0.5 * dy) / feature, seed, 3) - 0.5) * 2.0 * amount * r
        cmds.xform("%s.vtx[%d]" % (node, i), worldSpace=True, translation=[p[0] + dx / ln * k, p[1] + dy / ln * k, p[2] + dz / ln * k])
    return _finish(name, [node], {"seed": seed, "vertices": n})


@command("procgen.terrain", mutates=True)
def terrain(
    name: str = "terrain",
    width: float = 10000.0,
    depth: float = 10000.0,
    subdivisions: int = 50,
    height: float = 300.0,
    octaves: int = 4,
    feature_size: float | None = None,
    seed: int | None = None,
    heightmap: str | None = None,
) -> Dict[str, Any]:
    """Plane displaced by layered value noise (deterministic per seed) or by a PNG/PGM heightmap (0..1 times height)."""
    require_maya()
    width, depth = _check(width, "width", 10, 1000000), _check(depth, "depth", 10, 1000000)
    sub, amp = int(_check(subdivisions, "subdivisions", 1, 250)), _check(height, "height", 0, 100000)
    octaves = int(_check(octaves, "octaves", 1, 8))
    feature = float(feature_size) if feature_size is not None else max(width, depth) / 4.0
    rng, seed = _rng(seed)
    rows = read_heightmap(heightmap)[2] if heightmap else None
    node = _first(cmds.polyPlane(width=width, height=depth, subdivisionsX=sub, subdivisionsY=sub, constructionHistory=False, name=name + "_geo"), name + "_geo")
    n = _vertex_count(node) or (sub + 1) * (sub + 1)
    positions = _vertex_positions(node, n)
    lo, hi = float("inf"), float("-inf")
    for i, p in enumerate(positions):
        if rows:
            y = sample_heightmap(rows, p[0] / width + 0.5, p[2] / depth + 0.5) * amp
        else:
            y = terrain_height(p[0], p[2], seed, octaves, amp, feature)
        lo, hi = min(lo, y), max(hi, y)
        cmds.xform("%s.vtx[%d]" % (node, i), worldSpace=True, translation=[p[0], y, p[2]])
    return _finish(name, [node], {"seed": seed, "vertices": n, "height_range": [lo, hi], "feature_size": feature, "source": "heightmap" if rows else "noise"})


# scatter and arrays ----------------------------------------------------------
def _parse_faces(node: str) -> List[List[int]]:
    """Face -> vertex index lists from polyInfo (no OpenMaya)."""
    lines = cmds.polyInfo("%s.f[*]" % node, faceToVertex=True) or []
    faces = []
    for line in lines:
        ids = [int(t) for t in str(line).replace(":", " ").split()[2:] if t.lstrip("-").isdigit()]
        if len(ids) >= 3:
            faces.append(ids)
    return faces


def _parse_normals(node: str) -> List[List[float]]:
    out = []
    for line in cmds.polyInfo("%s.f[*]" % node, faceNormals=True) or []:
        vals = [float(t) for t in str(line).replace(":", " ").split()[2:]]
        out.append(vals[:3] if len(vals) >= 3 else [0.0, 1.0, 0.0])
    return out


def _surface_sampler(surface: str | None, bounds: Any, rng: random.Random) -> Tuple[Callable[[], List[float]], str]:
    """Random point + normal generator: triangles of the surface, else its bbox top, else a ground rectangle."""
    if surface:
        n = _vertex_count(surface)
        verts = _vertex_positions(surface, n) if n and n <= 200000 else []
        faces = _parse_faces(surface) if verts else []
        if faces:
            normals = _parse_normals(surface)

            def on_surface() -> List[float]:
                fi = rng.randrange(len(faces))
                f = faces[fi]
                k = rng.randrange(1, len(f) - 1)  # fan triangle (f[0], f[k], f[k + 1]) of the polygon
                a, b, c = verts[f[0]], verts[f[k]], verts[f[k + 1]]
                u, v = rng.random(), rng.random()
                if u + v > 1.0:
                    u, v = 1.0 - u, 1.0 - v
                nrm = normals[fi] if fi < len(normals) else [0.0, 1.0, 0.0]
                return [a[k] + (b[k] - a[k]) * u + (c[k] - a[k]) * v for k in range(3)] + nrm

            return on_surface, "faces"
        bb = _bbox(surface)
        lo, hi = bb["min"], bb["max"]
        return (lambda: [rng.uniform(lo[0], hi[0]), hi[1], rng.uniform(lo[2], hi[2]), 0.0, 1.0, 0.0]), "bbox"
    b = bounds or [[-500.0, -500.0], [500.0, 500.0]]
    return (lambda: [rng.uniform(b[0][0], b[1][0]), 0.0, rng.uniform(b[0][1], b[1][1]), 0.0, 1.0, 0.0]), "ground"


@command("procgen.scatter", mutates=True)
def scatter(
    name: str = "scatter",
    sources: List[str] | None = None,
    surface: str | None = None,
    count: int | None = None,
    density: float | None = None,
    min_distance: float = 0.0,
    align_to_normal: bool = False,
    rotation_random: float = 360.0,
    scale_range: List[float] | None = None,
    bounds: List[List[float]] | None = None,
    seed: int | None = None,
) -> Dict[str, Any]:
    """Instance ``sources`` over ``surface`` (random points on its faces) or a ground rectangle ``bounds`` [[x0, z0], [x1, z1]].

    ``density`` is items per square metre of surface area and overrides ``count``. Instances share shapes, so edits to a source update all.
    """
    require_maya()
    if not sources:
        raise BridgeError("sources is required: one or more nodes to instance")
    require_nodes(sources)
    if surface:
        require_nodes([surface])
    rng, seed = _rng(seed)
    if density is not None and surface:
        area = cmds.polyEvaluate(surface, worldArea=True)
        area = float(area) if isinstance(area, (int, float)) else 0.0
        count = int(round(_check(density, "density", 0, 1000) * area / 10000.0))
    count = int(_check(count if count is not None else 50, "count", 1, 5000))
    min_distance = _check(min_distance, "min_distance", 0, 100000)
    lo_s, hi_s = (float(scale_range[0]), float(scale_range[1])) if scale_range else (1.0, 1.0)
    sampler, mode = _surface_sampler(surface, bounds, rng)
    points, rejected = poisson_pick(sampler, count, min_distance, count * 30)
    instances: List[str] = []
    for i, p in enumerate(points):
        src = sources[rng.randrange(len(sources))]
        inst = _first(cmds.instance(src, name="%s_%d" % (name, i)), "%s_%d" % (name, i))
        yaw = rng.uniform(0.0, float(rotation_random))
        rot = [0.0, yaw, 0.0]
        if align_to_normal:
            rot = list(cmds.angleBetween(euler=True, v1=[0.0, 1.0, 0.0], v2=p[3:6]) or [0.0, 0.0, 0.0])
            rot[1] += yaw
        _rot(inst, rot[0], rot[1], rot[2])
        s = rng.uniform(lo_s, hi_s)
        cmds.xform(inst, scale=[s, s, s])
        _pos(inst, p[0], p[1], p[2])
        instances.append(inst)
    if not instances:
        raise BridgeError("no points passed the min_distance test; lower min_distance or count")
    grp = _util.long_name(cmds.group(instances, name=name + "_grp") or name + "_grp")
    bb = _bbox(grp)
    return {
        "group": grp,
        "parts": [_util.long_name(n) for n in instances],
        "bbox": bb,
        "stats": {"count": len(instances), "requested": count, "rejected": rejected, "mode": mode, "width": bb["size"][0], "depth": bb["size"][2], "height": bb["size"][1]},
        "seed": seed,
        "positions": [[round(v, 3) for v in p[:3]] for p in points[:200]],
    }


def _copy(src: str, name: str, instance: bool) -> str:
    made = cmds.instance(src, name=name) if instance else cmds.duplicate(src, returnRootsOnly=True, name=name)
    return _first(made, name)


@command("procgen.array_along_curve", mutates=True)
def array_along_curve(name: str = "array", node: str = "", curve: str = "", count: int = 10, align: bool = True, instance: bool = True, forward_axis: str = "x") -> Dict[str, Any]:
    """Copies or instances of ``node`` spaced evenly along ``curve``, optionally aiming their forward axis down the tangent."""
    require_maya()
    if not node or not curve:
        raise BridgeError("node and curve are required")
    require_nodes([node, curve])
    count = int(_check(count, "count", 1, 2000))
    axis = _choice(forward_axis, "forward_axis", ("x", "y", "z"))
    fwd = {"x": [1.0, 0.0, 0.0], "y": [0.0, 1.0, 0.0], "z": [0.0, 0.0, 1.0]}[axis]
    pts, tans = _curve_points(curve, count)
    items: List[str] = []
    for i, (p, t) in enumerate(zip(pts, tans)):
        c = _copy(node, "%s_%d" % (name, i), bool(instance))
        if align:
            rot = cmds.angleBetween(euler=True, v1=fwd, v2=t) or [0.0, 0.0, 0.0]
            _rot(c, rot[0], rot[1], rot[2])
        _pos(c, p[0], p[1], p[2])
        items.append(c)
    grp = _util.long_name(cmds.group(items, name=name + "_grp") or name + "_grp")
    bb = _bbox(grp)
    return {"group": grp, "parts": [_util.long_name(n) for n in items], "bbox": bb, "stats": {"count": len(items), "width": bb["size"][0], "depth": bb["size"][2], "height": bb["size"][1]}, "curve": _util.long_name(curve), "positions": pts[:200]}


@command("procgen.grid_array", mutates=True)
def grid_array(name: str = "grid", node: str = "", rows: int = 3, columns: int = 3, spacing_x: float | None = None, spacing_z: float | None = None, jitter: float = 0.0, instance: bool = True, seed: int | None = None) -> Dict[str, Any]:
    """Rows x columns copies of ``node`` on the ground plane; spacing defaults to 1.5 x the node's footprint. ``jitter`` (cm) randomises positions."""
    require_maya()
    if not node:
        raise BridgeError("node is required")
    require_nodes([node])
    rows, columns = int(_check(rows, "rows", 1, 200)), int(_check(columns, "columns", 1, 200))
    if rows * columns > MAX_PARTS:
        raise BridgeError("grid would make %d items; keep it under %d" % (rows * columns, MAX_PARTS))
    bb = _bbox(node)
    sx = float(spacing_x) if spacing_x is not None else (bb["size"][0] or 100.0) * 1.5
    sz = float(spacing_z) if spacing_z is not None else (bb["size"][2] or 100.0) * 1.5
    rng, seed = _rng(seed)
    jitter = _check(jitter, "jitter", 0, 100000)
    origin = cmds.xform(node, query=True, worldSpace=True, translation=True) or [0.0, 0.0, 0.0]
    items, positions = [], []
    for r in range(rows):
        for c in range(columns):
            x = origin[0] + c * sx + rng.uniform(-jitter, jitter)
            z = origin[2] + r * sz + rng.uniform(-jitter, jitter)
            item = _copy(node, "%s_%d_%d" % (name, r, c), bool(instance))
            _pos(item, x, origin[1], z)
            items.append(item)
            positions.append([x, origin[1], z])
    grp = _util.long_name(cmds.group(items, name=name + "_grp") or name + "_grp")
    gb = _bbox(grp)
    return {"group": grp, "parts": [_util.long_name(n) for n in items], "bbox": gb, "stats": {"count": len(items), "rows": rows, "columns": columns, "width": gb["size"][0], "depth": gb["size"][2], "height": gb["size"][1]}, "spacing": [sx, sz], "seed": seed, "positions": positions[:200]}
