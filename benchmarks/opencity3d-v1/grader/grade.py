"""The grade for opencity3d-v1 (ADR-0078): probe, harvest, then one verdict per check.

Two commands share this file, and the case's ``task.yaml`` runs them in order:

``python grade.py harvest <workspace> <run_id>``
    Builds this run's held-out map and probes, copies the agent's project to a
    scratch directory, runs the GDScript harvester in it twice (the API under
    ``godot --headless``, the scene under ``godot-render``) and stores the raw
    answers under ``<workspace>/.lab-grade/``. It judges nothing: it exits 1
    only when there is no Godot project at all (the case's entry-point check)
    and 0 otherwise, so a broken game shows up as failed *checks*.

``python grade.py check <name> <workspace> <run_id>``
    Reads those answers and decides one check against the map file itself:
    footprints, the height rule, road centrelines, point-in-polygon and
    distances computed here, by code the agent did not write. Exit 0 passes,
    1 fails, 124 means the lab could not measure (no harvest), which the
    validation lane records as inconclusive.

Why the map is generated per run: the agent's container sees the lab's git
history, so a committed test map is one ``git show`` away. The held-out map is
a random window of the shipped Ciudad Vieja extract, rotated, moved and
re-tagged with a seed derived from the run id, so a game that only knows the
shipped map — or hard-codes anything about it — fails.

Why the answers live in the workspace: per-step reward points every command at
each step's snapshot by rewriting the workspace path, so each snapshot gets its
own harvest and its own verdicts.

Standard library only: the oracle is plain geometry.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import math
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

CASE = "opencity3d-v1"
GRADER_DIR = Path(__file__).resolve().parent
SOURCE_MAP = GRADER_DIR.parent / "data" / "ciudad-vieja.json"
GRADE_DIR = ".lab-grade"
DEFAULT_MAP = "maps/ciudad-vieja.json"

# The height rule the prompt states: a positive `height` wins, else positive
# `levels` x 3.0, else 9.0 m. Footprints under 4 m2 may be skipped.
LEVEL_METRES = 3.0
DEFAULT_HEIGHT = 9.0
DEGENERATE_AREA = 4.0

# Street widths by `kind`, as the prompt states them. Traffic is on a road
# when it is within half the width plus ROAD_SLACK of its centreline.
ROAD_WIDTHS = {
    "motorway": 14.0,
    "trunk": 12.0,
    "primary": 12.0,
    "secondary": 10.0,
    "tertiary": 9.0,
    "residential": 7.0,
    "unclassified": 7.0,
    "living_street": 6.0,
    "pedestrian": 6.0,
    "service": 5.0,
}
DEFAULT_ROAD_WIDTH = 6.0
ROAD_SLACK = 2.0

ACTIONS = (
    "move_forward",
    "move_back",
    "move_left",
    "move_right",
    "sprint",
    "interact",
    "accelerate",
    "brake",
    "steer_left",
    "steer_right",
)

# The held-out map: a WINDOW x WINDOW square of the source, roads kept
# ROAD_MARGIN beyond it, and an open band of BOUNDS_MARGIN around everything.
WINDOW = 400.0
ROAD_MARGIN = 40.0
BOUNDS_MARGIN = 100.0
BAND_INSET = 50.0

Point = tuple[float, float]


# --- Geometry (map coordinates: x east, y north) ----------------------------------


def signed_area(poly: list[Point]) -> float:
    total = 0.0
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def area_centroid(poly: list[Point]) -> Point:
    a = signed_area(poly)
    if abs(a) < 1e-9:
        return (
            sum(p[0] for p in poly) / len(poly),
            sum(p[1] for p in poly) / len(poly),
        )
    cx = cy = 0.0
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        cross = x1 * y2 - x2 * y1
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    return cx / (6.0 * a), cy / (6.0 * a)


def inside(p: Point, poly: list[Point]) -> bool:
    x, y = p
    hit = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def seg_dist(p: Point, a: Point, b: Point) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dy * dy
    t = 0.0
    if length > 0:
        t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def edge_dist(p: Point, poly: list[Point]) -> float:
    return min(
        seg_dist(p, poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly))
    )


def polyline_dist(p: Point, line: list[Point]) -> float:
    if len(line) == 1:
        return math.dist(p, line[0])
    return min(seg_dist(p, line[i], line[i + 1]) for i in range(len(line) - 1))


def bbox(points: list[Point]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def polylabel(poly: list[Point], precision: float = 0.2) -> tuple[Point, float]:
    """The interior point farthest from the boundary (pole of inaccessibility).

    Concave footprints have no safe "centre"; this is the point a roof probe can
    hit with the widest margin. Returns the point and its distance to an edge.
    """

    def signed(x: float, y: float) -> float:
        d = edge_dist((x, y), poly)
        return d if inside((x, y), poly) else -d

    minx, miny, maxx, maxy = bbox(poly)
    size = min(maxx - minx, maxy - miny)
    if size <= 0:
        return area_centroid(poly), 0.0
    h = size / 2.0
    heap: list[tuple[float, float, float, float, float]] = []

    def push(x: float, y: float, half: float) -> None:
        d = signed(x, y)
        heapq.heappush(heap, (-(d + half * math.sqrt(2)), x, y, half, d))

    x = minx
    while x < maxx:
        y = miny
        while y < maxy:
            push(x + h, y + h, h)
            y += size
        x += size
    cx, cy = area_centroid(poly)
    best = ((cx, cy), signed(cx, cy))
    bx, by = (minx + maxx) / 2, (miny + maxy) / 2
    if signed(bx, by) > best[1]:
        best = ((bx, by), signed(bx, by))
    while heap:
        neg_max, x, y, half, d = heapq.heappop(heap)
        if d > best[1]:
            best = ((x, y), d)
        if -neg_max - best[1] <= precision:
            continue
        half /= 2
        for dx in (-half, half):
            for dy in (-half, half):
                push(x + dx, y + dy, half)
    return best


def expected_height(building: dict[str, Any]) -> float:
    height = building.get("height")
    if isinstance(height, (int, float)) and not isinstance(height, bool) and height > 0:
        return float(height)
    levels = building.get("levels")
    if isinstance(levels, (int, float)) and not isinstance(levels, bool) and levels > 0:
        return float(levels) * LEVEL_METRES
    return DEFAULT_HEIGHT


def road_width(kind: Any) -> float:
    return ROAD_WIDTHS.get(str(kind), DEFAULT_ROAD_WIDTH)


def to_world(p: Point) -> list[float]:
    """Map (x east, y north) to Godot (X, Z): north is -Z."""
    return [round(p[0], 3), round(-p[1], 3)]


def to_map(xz: list[float]) -> Point:
    return float(xz[0]), -float(xz[1])


class CityMap:
    """The oracle: the map file, with the queries every check needs."""

    def __init__(self, data: dict[str, Any]):
        self.data = data
        self.bounds = [float(v) for v in data["bounds"]]
        self.buildings: dict[int, dict[str, Any]] = {}
        self.footprints: dict[int, list[Point]] = {}
        self.boxes: dict[int, tuple[float, float, float, float]] = {}
        for b in data["buildings"]:
            fp = [(float(p[0]), float(p[1])) for p in b["footprint"]]
            self.buildings[int(b["id"])] = b
            self.footprints[int(b["id"])] = fp
            self.boxes[int(b["id"])] = bbox(fp)
        self.solid = {
            i
            for i, fp in self.footprints.items()
            if abs(signed_area(fp)) >= DEGENERATE_AREA
        }
        self.roads = [
            {
                "kind": r.get("kind"),
                "line": [(float(p[0]), float(p[1])) for p in r["line"]],
            }
            for r in data["roads"]
            if len(r.get("line") or []) >= 2
        ]

    def near(self, p: Point, reach: float) -> list[int]:
        out = []
        for i, (x0, y0, x1, y1) in self.boxes.items():
            if x0 - reach <= p[0] <= x1 + reach and y0 - reach <= p[1] <= y1 + reach:
                out.append(i)
        return out

    def clearance(
        self, p: Point, reach: float = 40.0, skip: int | None = None
    ) -> float:
        """Distance to the nearest footprint edge; negative depth when inside one."""
        best = reach
        for i in self.near(p, reach):
            if i == skip:
                continue
            fp = self.footprints[i]
            d = edge_dist(p, fp)
            if inside(p, fp):
                return -d
            best = min(best, d)
        return best

    def depth(self, p: Point) -> float:
        """How far inside a solid footprint ``p`` is (0 when outside all)."""
        worst = 0.0
        for i in self.near(p, 0.0):
            if i in self.solid and inside(p, self.footprints[i]):
                worst = max(worst, edge_dist(p, self.footprints[i]))
        return worst

    def road_excess(self, p: Point) -> float:
        """Distance beyond the nearest road's half width (<= 0 on the road)."""
        return min(
            polyline_dist(p, r["line"]) - road_width(r["kind"]) / 2 for r in self.roads
        )

    def in_bounds(self, p: Point, inset: float = 0.0) -> bool:
        x0, y0, x1, y1 = self.bounds
        return x0 + inset <= p[0] <= x1 - inset and y0 + inset <= p[1] <= y1 - inset

    def centre(self) -> Point:
        x0, y0, x1, y1 = self.bounds
        return (x0 + x1) / 2, (y0 + y1) / 2

    def nearest_road_distance(self, p: Point) -> float:
        return min(polyline_dist(p, r["line"]) for r in self.roads)


# --- The held-out map ---------------------------------------------------------------


def seed_for(run_id: str) -> int:
    return int.from_bytes(
        hashlib.sha256(f"{CASE}:{run_id}".encode()).digest()[:8], "big"
    )


def _clip_segment(
    a: Point, b: Point, box: tuple[float, float, float, float]
) -> tuple[Point, Point] | None:
    """Liang-Barsky: the part of segment a-b inside the box, or None."""
    x0, y0, x1, y1 = box
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if p == 0:
            if q < 0:
                return None
            continue
        r = q / p
        if p < 0:
            t0 = max(t0, r)
        else:
            t1 = min(t1, r)
        if t0 > t1:
            return None
    return (a[0] + t0 * dx, a[1] + t0 * dy), (a[0] + t1 * dx, a[1] + t1 * dy)


def _clip_line(
    line: list[Point], box: tuple[float, float, float, float]
) -> list[list[Point]]:
    pieces: list[list[Point]] = []
    current: list[Point] = []
    for i in range(len(line) - 1):
        clipped = _clip_segment(line[i], line[i + 1], box)
        if clipped is None:
            if current:
                pieces.append(current)
                current = []
            continue
        start, end = clipped
        if current and math.dist(current[-1], start) < 1e-6:
            current.append(end)
        else:
            if current:
                pieces.append(current)
            current = [start, end]
        if math.dist(end, line[i + 1]) > 1e-6:
            pieces.append(current)
            current = []
    if current:
        pieces.append(current)
    return [
        p
        for p in pieces
        if sum(math.dist(p[i], p[i + 1]) for i in range(len(p) - 1)) >= 8.0
    ]


def build_heldout(rng: random.Random) -> dict[str, Any]:
    """A neighbourhood of the shipped extract that no game has seen: a random
    window, rotated by a random angle, moved, with levels and heights re-tagged
    and every id replaced, plus two degenerate footprints a loader must survive."""
    src = json.loads(SOURCE_MAP.read_text())
    cents = [
        (b, area_centroid([tuple(p) for p in b["footprint"]])) for b in src["buildings"]
    ]
    xs = [c[0] for _b, c in cents]
    ys = [c[1] for _b, c in cents]
    half = WINDOW / 2
    chosen: list[dict[str, Any]] = []
    cx = cy = 0.0
    for _ in range(500):
        cx = rng.uniform(min(xs) + half * 0.5, max(xs) - half * 0.5)
        cy = rng.uniform(min(ys) + half * 0.5, max(ys) - half * 0.5)
        chosen = [
            b for b, c in cents if abs(c[0] - cx) <= half and abs(c[1] - cy) <= half
        ]
        if len(chosen) >= 80:
            break
    box = (
        cx - half - ROAD_MARGIN,
        cy - half - ROAD_MARGIN,
        cx + half + ROAD_MARGIN,
        cy + half + ROAD_MARGIN,
    )
    theta = rng.uniform(0, 2 * math.pi)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    tx, ty = rng.uniform(-350, 350), rng.uniform(-350, 350)

    def tf(p: Point) -> list[float]:
        dx, dy = p[0] - cx, p[1] - cy
        return [
            round(tx + dx * cos_t - dy * sin_t, 2),
            round(ty + dx * sin_t + dy * cos_t, 2),
        ]

    ids = rng.sample(
        range(1_000_000, 2_000_000_000), len(chosen) + len(src["roads"]) * 4 + 8
    )
    buildings = []
    rng.shuffle(chosen)
    for b in chosen:
        fp = [tf(tuple(p)) for p in b["footprint"]]
        if rng.random() < 0.5:
            fp.reverse()
        k = rng.randrange(len(fp))
        fp = fp[k:] + fp[:k]
        levels, height = b.get("levels"), b.get("height")
        if rng.random() < 0.3:
            levels = rng.choice([None, 1, 2, 3, 4, 5, 6, 7, 8, 10, 12])
        if rng.random() < 0.1:
            height = round(rng.uniform(4.5, 42.0), 1)
        buildings.append(
            {"id": ids.pop(), "levels": levels, "height": height, "footprint": fp}
        )
    roads = []
    for r in src["roads"]:
        for piece in _clip_line([tuple(p) for p in r["line"]], box):
            roads.append(
                {
                    "id": ids.pop(),
                    "kind": r.get("kind"),
                    "name": r.get("name", ""),
                    "oneway": bool(r.get("oneway")),
                    "line": [tf(p) for p in piece],
                }
            )
    rng.shuffle(roads)
    every = [tuple(p) for b in buildings for p in b["footprint"]] + [
        tuple(p) for r in roads for p in r["line"]
    ]
    x0, y0, x1, y1 = bbox(every)
    bounds = [
        round(x0 - BOUNDS_MARGIN, 2),
        round(y0 - BOUNDS_MARGIN, 2),
        round(x1 + BOUNDS_MARGIN, 2),
        round(y1 + BOUNDS_MARGIN, 2),
    ]
    # Degenerate footprints, out in the open band: a 1.2 m square and a sliver
    # with three collinear points. A loader may skip them; it must not crash.
    bx, by = bounds[0] + 8.0, bounds[1] + 8.0
    buildings.append(
        {
            "id": ids.pop(),
            "levels": 2,
            "height": None,
            "footprint": [
                [bx, by],
                [bx + 1.2, by],
                [bx + 1.2, by + 1.2],
                [bx, by + 1.2],
            ],
        }
    )
    buildings.append(
        {
            "id": ids.pop(),
            "levels": None,
            "height": None,
            "footprint": [[bx + 6, by], [bx + 9, by], [bx + 12, by]],
        }
    )
    return {
        "attribution": src["attribution"],
        "source": "held-out neighbourhood generated by the lab from " + src["source"],
        "units": "metres; x east, y north",
        "levels_to_metres": LEVEL_METRES,
        "bounds": bounds,
        "buildings": buildings,
        "roads": roads,
    }


# --- Probes -------------------------------------------------------------------------


def _unit(dx: float, dy: float) -> Point:
    n = math.hypot(dx, dy)
    return dx / n, dy / n


def heading_deg(u: Point) -> float:
    """Compass heading of a map direction: 0 north (-Z), 90 east (+X)."""
    return round(math.degrees(math.atan2(u[0], u[1])) % 360.0, 3)


def _path_clear(
    city: CityMap,
    start: Point,
    u: Point,
    t0: float,
    t1: float,
    clear: float,
    step: float = 1.0,
) -> bool:
    t = t0
    while t <= t1 + 1e-9:
        p = (start[0] + u[0] * t, start[1] + u[1] * t)
        if not city.in_bounds(p, 10.0) or city.clearance(p, reach=clear + 1) < clear:
            return False
        t += step
    return True


def _road_samples(city: CityMap, spacing: float) -> list[tuple[Point, Point]]:
    """Points along every centreline with the segment's direction."""
    out = []
    for road in city.roads:
        line = road["line"]
        for i in range(len(line) - 1):
            a, b = line[i], line[i + 1]
            length = math.dist(a, b)
            if length < 1e-6:
                continue
            u = _unit(b[0] - a[0], b[1] - a[1])
            t = spacing / 2
            while t < length:
                out.append(((a[0] + u[0] * t, a[1] + u[1] * t), u))
                t += spacing
    return out


def _spread(
    rng: random.Random, points: list[Any], count: int, apart: float, key=lambda p: p
) -> list[Any]:
    pool = points[:]
    rng.shuffle(pool)
    picked: list[Any] = []
    for item in pool:
        if all(math.dist(key(item), key(other)) >= apart for other in picked):
            picked.append(item)
        if len(picked) == count:
            break
    return picked


def _wall_approaches(
    city: CityMap, min_edge: float, length: float, clear: float
) -> list[dict[str, Any]]:
    """Points facing a long wall head-on with ``length`` m of open ground."""
    out = []
    for i in sorted(city.solid):
        fp = city.footprints[i]
        for k in range(len(fp)):
            a, b = fp[k], fp[(k + 1) % len(fp)]
            if math.dist(a, b) < min_edge:
                continue
            q = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            ex, ey = _unit(b[0] - a[0], b[1] - a[1])
            n = (ey, -ex)
            if inside((q[0] + n[0] * 0.3, q[1] + n[1] * 0.3), fp):
                n = (-n[0], -n[1])
            ok = True
            t = 0.6
            while t <= length + 1e-9:
                p = (q[0] + n[0] * t, q[1] + n[1] * t)
                if (
                    not city.in_bounds(p, 10.0)
                    or city.clearance(p, reach=clear + 1) < min(t, clear) - 0.1
                ):
                    ok = False
                    break
                t += 0.4
            if ok:
                out.append({"id": i, "q": q, "n": n})
    return out


def build_probe(run_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The held-out map and every probe the harvester drives, both seeded by run id."""
    rng = random.Random(seed_for(run_id))
    data = build_heldout(rng)
    city = CityMap(data)

    # Buildings with a safe roof point: deep inside, away from every neighbour.
    roofable = []
    for i in sorted(city.solid):
        point, depth = polylabel(city.footprints[i])
        if depth >= 2.0 and city.clearance(point, reach=3.0, skip=i) >= 2.0:
            roofable.append((i, point))
    rng.shuffle(roofable)
    tested = roofable[:20]
    # The height rule's three branches, forced so every run tests each one.
    by_id = {int(b["id"]): b for b in data["buildings"]}
    for n, (i, _point) in enumerate(tested):
        b = by_id[i]
        if n < 6:
            b["height"] = round(rng.uniform(4.5, 45.0), 1)
            if n % 2 == 0 and b["levels"] is None:
                b["levels"] = rng.randint(1, 12)
        elif n < 12:
            b["height"] = None
            if not b["levels"]:
                b["levels"] = rng.randint(1, 12)
        elif n < 16:
            b["height"] = None
            b["levels"] = None
    city = CityMap(data)

    street = [
        (p, u)
        for p, u in _road_samples(city, 4.0)
        if city.in_bounds(p, 20.0) and city.clearance(p, reach=5.0) >= 4.0
    ]
    street_points = _spread(rng, street, 12, 15.0, key=lambda s: s[0])
    teleports = _spread(rng, street, 3, 60.0, key=lambda s: s[0])

    # Inside a footprint's axis-aligned box, outside every footprint: a game that
    # builds boxes instead of footprints has a roof here.
    notch = []
    for i in sorted(city.solid):
        x0, y0, x1, y1 = city.boxes[i]
        found = []
        x = x0 + 1.0
        while x <= x1 - 1.0:
            y = y0 + 1.0
            while y <= y1 - 1.0:
                if city.clearance((x, y), reach=3.0) >= 2.5:
                    found.append((x, y))
                y += 1.5
            x += 1.5
        if found:
            notch.append((i, rng.choice(found)))
    rng.shuffle(notch)
    notch = notch[:10]

    # A straight stretch of street to walk and sprint along.
    walk = None
    corridors = []
    for p, u in _road_samples(city, 3.0):
        if _path_clear(city, p, u, 0.0, 36.0, 2.0):
            corridors.append((p, u))
    if corridors:
        p, u = rng.choice(corridors)
        walk = {"start": to_world(p), "dir": [u[0], -u[1]], "on_street": True}

    walls = _wall_approaches(city, 6.0, 8.5, 1.5)
    rng.shuffle(walls)
    wall = None
    if walls:
        w = walls[0]
        start = (w["q"][0] + w["n"][0] * 8.0, w["q"][1] + w["n"][1] * 8.0)
        wall = {"id": w["id"], "start": to_world(start), "dir": [-w["n"][0], w["n"][1]]}
    car_walls = _wall_approaches(city, 8.0, 24.0, 2.6)
    rng.shuffle(car_walls)
    car_wall = None
    if car_walls:
        w = car_walls[0]
        q, n = w["q"], w["n"]
        car_wall = {
            "id": w["id"],
            "player": to_world((q[0] + n[0] * 21.5, q[1] + n[1] * 21.5)),
            "car": to_world((q[0] + n[0] * 18.0, q[1] + n[1] * 18.0)),
            "heading": heading_deg((-n[0], -n[1])),
        }

    # The open band around the city: where a car's own dynamics are measured,
    # one bounds edge each, far from every building by construction.
    x0, y0, x1, y1 = city.bounds
    edges = ["south", "north", "west", "east"]
    rng.shuffle(edges)

    def band(edge: str, along: float) -> tuple[Point, Point]:
        if edge == "south":
            return (x0 + along, y0 + BAND_INSET), (1.0, 0.0)
        if edge == "north":
            return (x1 - along, y1 - BAND_INSET), (-1.0, 0.0)
        if edge == "west":
            return (x0 + BAND_INSET, y1 - along), (0.0, -1.0)
        return (x1 - BAND_INSET, y0 + along), (0.0, 1.0)

    run_start, run_u = band(edges[0], 60.0)
    run = {
        "player": to_world(run_start),
        "car": to_world((run_start[0] + run_u[0] * 3.5, run_start[1] + run_u[1] * 3.5)),
        "heading": heading_deg(run_u),
        "dir": [run_u[0], -run_u[1]],
    }

    def mid(edge: str) -> tuple[Point, Point]:
        return band(edge, (x1 - x0 if edge in ("south", "north") else y1 - y0) / 2)

    def parked(edge: str) -> dict[str, Any]:
        at, u = mid(edge)
        return {
            "player": to_world(at),
            "car": to_world((at[0] + u[0] * 3.5, at[1] + u[1] * 3.5)),
            "heading": heading_deg(u),
            "dir": [u[0], -u[1]],
        }

    steer = parked(edges[1])
    keys = parked(edges[2])
    box_c = mid(edges[3])[0]
    # A solid box only the lab knows about, out in the open band.
    box = {"at": to_world(box_c), "height": round(rng.uniform(6.0, 25.0), 1)}

    # The top-down view: centred on the neighbourhood, 200 m tall on screen.
    solid_cents = [area_centroid(city.footprints[i]) for i in city.solid]
    ox = sum(c[0] for c in solid_cents) / len(solid_cents)
    oy = sum(c[1] for c in solid_cents) / len(solid_cents)

    used = set(city.buildings)
    unknown = 7
    while unknown in used:
        unknown += 1

    probe = {
        "case": CASE,
        "run_id": run_id,
        "default_map": "res://" + DEFAULT_MAP,
        "info_ids": [i for i, _p in tested],
        "unknown_id": unknown,
        "roof_points": [{"id": i, "at": to_world(p)} for i, p in tested],
        "street_points": [to_world(p) for p, _u in street_points],
        "notch_points": [{"id": i, "at": to_world(p)} for i, p in notch],
        "teleports": [to_world(p) for p, _u in teleports],
        "walk": walk,
        "wall": wall,
        "car_wall": car_wall,
        "run": run,
        "steer": steer,
        "keys": keys,
        "band_points": [run["player"], steer["player"], keys["player"]],
        "box": box,
        "ortho": {"center": to_world((ox, oy)), "size": 200.0, "grid": 8},
        "traffic": {"first": 8, "second": 3, "samples": 41, "every": 30},
    }
    return data, probe


# --- Harvest ------------------------------------------------------------------------

# Files the agent's project may contain. Anything else — an addon, a native
# library, a prebuilt binary — is third-party or opaque and fails `own_code`.
_ALLOWED_SUFFIXES = {
    ".gd",
    ".tscn",
    ".tres",
    ".gdshader",
    ".godot",
    ".cfg",
    ".md",
    ".txt",
    ".json",
    ".png",
    ".svg",
    ".import",
    ".uid",
    ".gitignore",
    ".gitattributes",
    ".editorconfig",
}
_IGNORED_DIRS = {".godot", ".git", GRADE_DIR, "__lab_grader__"}


def project_dir(workspace: Path) -> Path | None:
    if (workspace / "project.godot").is_file():
        return workspace
    found = sorted(p.parent for p in workspace.glob("*/project.godot"))
    return found[0] if found else None


def _godot(argv: list[str], timeout: int) -> tuple[int, str, float]:
    started = time.monotonic()
    if shutil.which(argv[0]) is None:
        # The lab's image has no engine: nothing the agent did.
        return 127, f"{argv[0]}: not found", 0.0
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False
        )
        return (
            done.returncode,
            (done.stdout + done.stderr)[-20000:],
            time.monotonic() - started,
        )
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"") + (exc.stderr or b"")
        text = out.decode(errors="replace") if isinstance(out, bytes) else str(out)
        return 124, text[-20000:], time.monotonic() - started


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def harvest(workspace: Path, run_id: str) -> int:
    grade = workspace / GRADE_DIR
    if grade.exists():
        shutil.rmtree(grade)
    grade.mkdir(parents=True)
    started = time.monotonic()
    heldout, probe = build_probe(run_id)
    heldout_path = grade / "heldout.json"
    heldout_path.write_text(json.dumps(heldout))
    # Maps a loader must refuse: broken JSON, the wrong shape, and no file.
    (grade / "bad_syntax.json").write_text(
        '{"buildings": [{"id": 1, "footprint": [[0, 0], [10'
    )
    (grade / "bad_shape.json").write_text(json.dumps({"buildings": "none", "roads": 3}))
    probe["heldout_path"] = str(heldout_path)
    probe["bad_maps"] = [
        str(grade / "bad_syntax.json"),
        str(grade / "bad_shape.json"),
        str(grade / "no_such_map.json"),
    ]
    (grade / "probe.json").write_text(json.dumps(probe, indent=1))
    meta: dict[str, Any] = {
        "run_id": run_id,
        "seed": seed_for(run_id),
        "probe_s": round(time.monotonic() - started, 2),
    }
    project = project_dir(workspace)
    meta["project"] = str(project.relative_to(workspace)) if project else None
    if project is None:
        (grade / "meta.json").write_text(json.dumps(meta, indent=1))
        print("harvest: no project.godot in the workspace")
        return 1

    # Written now and after every pass: a harvest cut short by the outer timeout
    # still tells the checks there was a project.
    (grade / "meta.json").write_text(json.dumps(meta, indent=1))
    with tempfile.TemporaryDirectory(prefix="opencity3d-") as scratch:
        copy = Path(scratch) / "project"
        shutil.copytree(
            project, copy, ignore=shutil.ignore_patterns(".godot", GRADE_DIR, ".git")
        )
        (copy / "__lab_grader__").mkdir(exist_ok=True)
        shutil.copy2(GRADER_DIR / "harvest.gd", copy / "__lab_grader__" / "harvest.gd")
        shots = grade / "shots"
        shots.mkdir()
        script = ["--fixed-fps", "60", "--script", "res://__lab_grader__/harvest.gd"]
        args = ["--probe", str(grade / "probe.json")]

        code, log, secs = _godot(
            ["godot", "--headless", "--path", str(copy), "--import"], 120
        )
        meta["import"] = {"exit": code, "s": round(secs, 1)}
        (grade / "import.log").write_text(log)

        code, log, secs = _godot(
            [
                "godot",
                "--headless",
                "--path",
                str(copy),
                *script,
                "--",
                *args,
                "--out",
                str(grade / "logic.json"),
                "--phase",
                "logic",
            ],
            420,
        )
        meta["logic"] = {"exit": code, "s": round(secs, 1)}
        (grade / "logic.log").write_text(log)
        (grade / "meta.json").write_text(json.dumps(meta, indent=1))

        # The rendered pass, retried only when the engine died before the
        # agent's scene was even instanced: that start-up is the lab's display
        # stack, not the game. A crash once the scene runs is the game's.
        scene_argv = [
            "godot-render",
            "--path",
            str(copy),
            "--resolution",
            "1280x720",
            *script,
            "--",
            *args,
            "--out",
            str(grade / "scene.json"),
            "--phase",
            "scene",
            "--shots",
            str(shots),
            f"--map={heldout_path}",
        ]
        attempts = []
        for _attempt in range(3):
            code, log, secs = _godot(scene_argv, 240)
            attempts.append({"exit": code, "s": round(secs, 1)})
            answered = _read_json(grade / "scene.json")
            if code == 0 or answered.get("instanced") or code in (124, 127):
                break
        meta["scene"] = {"exit": code, "s": round(sum(a["s"] for a in attempts), 1)}
        meta["scene_attempts"] = attempts
        (grade / "scene.log").write_text(log)
    meta["total_s"] = round(time.monotonic() - started, 1)
    (grade / "meta.json").write_text(json.dumps(meta, indent=1))
    print(
        f"harvest: logic exit {meta['logic']['exit']} ({meta['logic']['s']} s), "
        f"scene exit {meta['scene']['exit']} ({meta['scene']['s']} s), total {meta['total_s']} s"
    )
    return 0


# --- Checks -------------------------------------------------------------------------


class Unmeasured(Exception):
    """The lab could not produce what this check reads."""


class Grade:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.dir = workspace / GRADE_DIR
        if not (self.dir / "probe.json").is_file():
            raise Unmeasured(
                "no harvest in this workspace; run `grade.py harvest` first"
            )
        self.probe = json.loads((self.dir / "probe.json").read_text())
        self.meta = self._load("meta.json") or {}
        self.logic = self._load("logic.json") or {}
        self.scene = self._load("scene.json") or {}
        held = self._load("heldout.json")
        if held is None:
            raise Unmeasured("the held-out map is missing from the harvest")
        self.city = CityMap(held)

    def _load(self, name: str) -> dict[str, Any] | None:
        path = self.dir / name
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text())
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None


Result = tuple[bool, str]


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _xyz(value: Any) -> list[float] | None:
    if (
        isinstance(value, list)
        and len(value) >= 3
        and all(_num(v) is not None for v in value[:3])
    ):
        return [float(v) for v in value[:3]]
    return None


def _state(row: Any) -> dict[str, Any]:
    """A player_state answer, normalised; {} when unreadable."""
    if not isinstance(row, dict):
        return {}
    pos = _xyz(row.get("position"))
    if pos is None:
        return {}
    return {
        "pos": pos,
        "flat": (pos[0], -pos[2]),
        "in_vehicle": row.get("in_vehicle") is True,
        "vehicle_id": row.get("vehicle_id"),
        "speed": _num(row.get("speed")),
    }


def _on_ground(state: dict[str, Any]) -> bool:
    return bool(state) and -0.5 <= state["pos"][1] <= 2.5


def check_contract(g: Grade) -> Result:
    if not g.logic.get("has_city_autoload"):
        return False, "no autoload named City"
    required = {
        "load_map",
        "building_count",
        "building_info",
        "ground_height_at",
        "player_state",
        "set_player_input",
        "set_vehicle_input",
        "step",
        "teleport_player",
        "enter_nearest_vehicle",
        "exit_vehicle",
        "spawn_parked_car",
        "traffic_states",
        "set_traffic",
        "minimap_player_marker",
    }
    have = set(g.logic.get("methods") or [])
    missing = sorted(required - have)
    return (
        not missing,
        f"missing methods: {missing}" if missing else "all contract methods present",
    )


def check_own_code(g: Grade) -> Result:
    project = project_dir(g.workspace)
    if project is None:
        return False, "no project"
    offenders = []
    for path in project.rglob("*"):
        if any(part in _IGNORED_DIRS for part in path.relative_to(project).parts):
            continue
        if path.is_dir():
            if path.name == "addons":
                offenders.append(str(path.relative_to(project)))
            continue
        if (
            path.suffix.lower() not in _ALLOWED_SUFFIXES
            and path.name not in _ALLOWED_SUFFIXES
        ):
            offenders.append(str(path.relative_to(project)))
        elif path.suffix == ".gd" and path.stat().st_size > 400_000:
            offenders.append(f"{path.relative_to(project)} (over 400 KB)")
    text = (project / "project.godot").read_text(errors="replace")
    if re.search(r"(?m)^\[editor_plugins\]", text) or "gdextension" in text.lower():
        offenders.append("project.godot enables a plugin or GDExtension")
    return (
        not offenders,
        f"not the agent's own code: {offenders[:8]}"
        if offenders
        else "only own sources and assets",
    )


def check_renderer(g: Grade) -> Result:
    project = project_dir(g.workspace)
    text = (project / "project.godot").read_text(errors="replace") if project else ""
    ok = 'rendering_method="gl_compatibility"' in text
    return (
        ok,
        "Compatibility renderer"
        if ok
        else 'renderer/rendering_method must be "gl_compatibility"',
    )


def check_input_actions(g: Grade) -> Result:
    actions = g.logic.get("actions") or {}
    missing = [
        a for a in ACTIONS if not isinstance(actions.get(a), int) or actions[a] < 1
    ]
    return (
        not missing,
        f"input actions missing or without an event: {missing}"
        if missing
        else "all 10 input actions bound",
    )


def check_default_map(g: Grade) -> Result:
    project = project_dir(g.workspace)
    path = project / DEFAULT_MAP if project else None
    if path is None or not path.is_file():
        return False, f"no res://{DEFAULT_MAP} in the project"
    try:
        shipped = json.loads(path.read_text())
        ids = {int(b["id"]) for b in shipped["buildings"]}
    except Exception:
        return False, f"res://{DEFAULT_MAP} is not the shipped map"
    source = json.loads(SOURCE_MAP.read_text())
    expected = {int(b["id"]) for b in source["buildings"]}
    if ids != expected:
        return False, f"res://{DEFAULT_MAP} is not the shipped Ciudad Vieja map"
    row = g.logic.get("default") or {}
    count = len(expected)
    if row.get("ok") is not True or row.get("count") != count:
        return (
            False,
            f"load_map(res://{DEFAULT_MAP}) = {row.get('ok')!r}, building_count() = {row.get('count')!r}, expected {count}",
        )
    scene_count = (g.logic.get("default_scene") or {}).get("count")
    if scene_count != count:
        return (
            False,
            f"the main scene with no --map argument built {scene_count!r} buildings, expected {count}",
        )
    return True, f"the shipped map loads by default: {count} buildings"


def _heldout_counts(g: Grade) -> tuple[int, int]:
    return len(g.city.solid), len(g.city.buildings)


def check_heldout_loads(g: Grade) -> Result:
    row = g.logic.get("heldout") or {}
    if row.get("ok") is not True:
        return False, f"load_map(held-out map) returned {row.get('ok')!r}"
    ms = _num(row.get("ms")) or 0
    if ms > 30000:
        return False, f"the held-out map took {ms / 1000:.1f} s to build (limit 30 s)"
    return True, f"a map the game never saw loads in {ms / 1000:.1f} s"


def check_bad_map(g: Grade) -> Result:
    if (g.logic.get("heldout") or {}).get("ok") is not True:
        return (
            False,
            "load_map never returns true, so refusing a bad map proves nothing",
        )
    rows = g.logic.get("bad") or []
    if len(rows) != len(g.probe["bad_maps"]):
        return False, "the harvest stopped while loading bad maps"
    for path, row in zip(g.probe["bad_maps"], rows, strict=True):
        if row.get("ok") is not False:
            return (
                False,
                f"load_map({Path(path).name}) returned {row.get('ok')!r}, expected false",
            )
    return True, "broken JSON, a wrong shape and a missing file are refused"


def check_building_count(g: Grade) -> Result:
    count = (g.logic.get("heldout") or {}).get("count")
    low, high = _heldout_counts(g)
    ok = isinstance(count, int) and low <= count <= high
    return (
        ok,
        f"building_count() = {count!r}, expected {low} (or {high} counting degenerate footprints)",
    )


def _info_rows(g: Grade) -> dict[int, Any]:
    return {int(r["id"]): r.get("info") for r in g.logic.get("info") or [] if "id" in r}


def check_building_heights(g: Grade) -> Result:
    rows = _info_rows(g)
    ids = g.probe["info_ids"]
    for i in ids:
        info = rows.get(i)
        expected = expected_height(g.city.buildings[i])
        got = _num(info.get("height")) if isinstance(info, dict) else None
        if got is None or abs(got - expected) > 0.5:
            b = g.city.buildings[i]
            return False, (
                f"building {i} (levels={b.get('levels')!r}, height={b.get('height')!r}): "
                f"height {got!r}, expected {expected}"
            )
    return (
        True,
        f"{len(ids)} heights follow the rule (height, else levels x 3, else 9 m)",
    )


def check_building_info(g: Grade) -> Result:
    rows = _info_rows(g)
    for i in g.probe["info_ids"]:
        info = rows.get(i)
        if not isinstance(info, dict) or not info:
            return False, f"building_info({i}) is empty"
        if info.get("id") != i:
            return False, f"building_info({i}).id = {info.get('id')!r}"
        cent = info.get("centroid")
        if not (
            isinstance(cent, list)
            and len(cent) >= 2
            and all(_num(v) is not None for v in cent[:2])
        ):
            return False, f"building_info({i}).centroid = {cent!r}, expected [x, z]"
        expected = to_world(area_centroid(g.city.footprints[i]))
        off = math.dist(expected, [float(cent[0]), float(cent[1])])
        if off > 1.0:
            return (
                False,
                f"building {i}: centroid {cent}, expected {expected} ({off:.1f} m off)",
            )
        node = (g.logic.get("info_nodes") or {}).get(str(i)) or {}
        if (
            not node.get("found")
            or node.get("building_id") != i
            or not node.get("in_group")
        ):
            return (
                False,
                f"building_info({i}).node_path does not resolve to that building's node: {node}",
            )
    unknown = g.logic.get("info_unknown")
    if unknown != {}:
        return False, f"building_info(unknown id) = {unknown!r}, expected {{}}"
    return True, "ids, centroids and node paths match the map"


def _building_nodes(g: Grade, key: str = "buildings") -> list[dict[str, Any]]:
    return [r for r in g.logic.get(key) or [] if isinstance(r, dict)]


def check_buildings_group(g: Grade) -> Result:
    nodes = _building_nodes(g)
    if not nodes:
        return False, "no node in the group `buildings`"
    ids = [n.get("id") for n in nodes]
    unknown = [i for i in ids if i not in g.city.buildings]
    if unknown:
        return (
            False,
            f"{len(unknown)} building nodes have no building_id from the map, e.g. {unknown[:3]}",
        )
    if len(set(ids)) != len(ids):
        return False, "several building nodes share one building_id"
    missing = sorted(g.city.solid - set(ids))
    if missing:
        return (
            False,
            f"{len(missing)} buildings of the map have no node, e.g. {missing[:3]}",
        )
    return (
        True,
        f"{len(ids)} building nodes, one per footprint, each tagged with its map id",
    )


def check_footprints_placed(g: Grade) -> Result:
    boxes = {n.get("id"): n.get("aabb") for n in _building_nodes(g)}
    for i in g.probe["info_ids"]:
        box = boxes.get(i)
        if not (isinstance(box, list) and len(box) == 6):
            return False, f"building {i} has no mesh under its node"
        x0, y0, x1, y1 = g.city.boxes[i]
        # World X = x, world Z = -y.
        expected = [x0, 0.0, -y1, x1, expected_height(g.city.buildings[i]), -y0]
        if any(abs(box[k] - expected[k]) > 1.0 for k in (0, 2, 3, 5)):
            return (
                False,
                f"building {i}: mesh spans x {box[0]:.1f}..{box[3]:.1f} z {box[2]:.1f}..{box[5]:.1f}, footprint x {expected[0]:.1f}..{expected[3]:.1f} z {expected[2]:.1f}..{expected[5]:.1f}",
            )
        if abs(box[4] - expected[4]) > 0.75 or box[1] > 0.5:
            return (
                False,
                f"building {i}: mesh from y {box[1]:.1f} to {box[4]:.1f}, expected 0 to {expected[4]:.1f}",
            )
    return (
        True,
        f"{len(g.probe['info_ids'])} buildings stand on their footprints at their heights",
    )


def _heights(g: Grade, key: str) -> list[Any]:
    return list(g.logic.get(key) or [])


def _roof_ok(g: Grade, answers: list[Any]) -> tuple[int, str]:
    good = 0
    first = ""
    for spec, got in zip(
        g.probe["roof_points"],
        answers + [None] * len(g.probe["roof_points"]),
        strict=False,
    ):
        expected = expected_height(g.city.buildings[spec["id"]])
        value = _num(got)
        if value is not None and abs(value - expected) <= 0.75:
            good += 1
        elif not first:
            first = f"inside building {spec['id']} at {spec['at']}: {got!r}, expected its roof at {expected}"
    return good, first


def check_roof_raycast(g: Grade) -> Result:
    good, first = _roof_ok(g, _heights(g, "roof_heights"))
    total = len(g.probe["roof_points"])
    return (
        good == total,
        first or f"ground_height_at() finds {total} roofs at their heights",
    )


def _spawn(g: Grade) -> dict[str, Any]:
    return _state(g.logic.get("spawn"))


def _ground_rows(g: Grade, key: str, points: list[Any]) -> tuple[bool, str]:
    answers = _heights(g, key)
    spawn = _spawn(g)
    for k, at in enumerate(points):
        got = _num(answers[k]) if k < len(answers) else None
        if spawn and math.dist(to_map(at), spawn["flat"]) < 4.0:
            continue
        if got is None or abs(got) > 0.3:
            return (
                False,
                f"at {at}: {answers[k] if k < len(answers) else None!r}, expected ground level 0",
            )
    return True, ""


def check_street_raycast(g: Grade) -> Result:
    good, _first = _roof_ok(g, _heights(g, "roof_heights"))
    if good < len(g.probe["roof_points"]) / 2:
        return (
            False,
            "ground_height_at() does not see roofs, so a flat answer on streets proves nothing",
        )
    ok, why = _ground_rows(g, "street_heights", g.probe["street_points"])
    return ok, why or f"{len(g.probe['street_points'])} street points at ground level"


def check_footprint_shape(g: Grade) -> Result:
    good, _first = _roof_ok(g, _heights(g, "roof_heights"))
    if good < len(g.probe["roof_points"]) / 2:
        return (
            False,
            "ground_height_at() does not see roofs, so a flat answer here proves nothing",
        )
    points = [n["at"] for n in g.probe["notch_points"]]
    if len(points) < 3:
        raise Unmeasured(
            "the lab found too few points outside the footprints for this run"
        )
    ok, why = _ground_rows(g, "notch_heights", points)
    return (
        ok,
        why
        or f"{len(points)} points inside footprint boxes but outside the footprints are open ground",
    )


def check_collision_geometry(g: Grade) -> Result:
    rays = g.logic.get("rays") or {}
    roofs = rays.get("roofs") or []
    for spec, got in zip(
        g.probe["roof_points"],
        roofs + [None] * len(g.probe["roof_points"]),
        strict=False,
    ):
        expected = expected_height(g.city.buildings[spec["id"]])
        value = _num(got)
        if value is None or abs(value - expected) > 0.75:
            return (
                False,
                f"a ray down onto building {spec['id']} hit {got!r}, expected its roof at {expected}",
            )
    for key, points in (
        ("streets", g.probe["street_points"]),
        ("band", g.probe["band_points"]),
    ):
        hits = rays.get(key) or []
        spawn = _spawn(g)
        for k, at in enumerate(points):
            if spawn and math.dist(to_map(at), spawn["flat"]) < 4.0:
                continue
            value = _num(hits[k]) if k < len(hits) else None
            if value is None or abs(value) > 0.3:
                return (
                    False,
                    f"a ray down at {at} ({key}) hit {value!r}, expected solid ground at 0",
                )
    return True, "buildings and the ground are solid where the map puts them"


def check_ground_uses_physics(g: Grade) -> Result:
    spec = g.probe["box"]
    got = _num(g.logic.get("box_height"))
    ok = got is not None and abs(got - spec["height"]) <= 0.3
    return ok, (
        f"ground_height_at() sees a {spec['height']} m solid box the lab placed at {spec['at']}"
        if ok
        else f"ground_height_at() on top of a {spec['height']} m solid box the lab placed = {g.logic.get('box_height')!r}; it must read the physics world"
    )


def check_step_ticks(g: Grade) -> Result:
    rows = g.logic.get("ticks") or []
    if not rows:
        return False, "step() was not measured"
    for row in rows:
        if (
            row.get("counted") is None
            or abs(int(row["counted"]) - int(row["asked"])) > 1
        ):
            return (
                False,
                f"step({row.get('asked')}) advanced {row.get('counted')!r} physics ticks",
            )
    return True, "step(n) advances exactly n physics ticks"


def check_spawn(g: Grade) -> Result:
    s = _spawn(g)
    if not s:
        return False, f"player_state() after load_map = {g.logic.get('spawn')!r}"
    if not _on_ground(s) or s["in_vehicle"]:
        return False, f"the player spawned at {s['pos']} (in_vehicle={s['in_vehicle']})"
    if g.city.depth(s["flat"]) > 0.3:
        return False, "the player spawned inside a building"
    if g.city.road_excess(s["flat"]) > ROAD_SLACK:
        return (
            False,
            f"the player spawned {g.city.road_excess(s['flat']):.1f} m off the nearest street",
        )
    centre = g.city.centre()
    allowed = g.city.nearest_road_distance(centre) + 5.0
    if math.dist(centre, s["flat"]) > allowed:
        return (
            False,
            f"the player spawned {math.dist(centre, s['flat']):.0f} m from the map centre; the nearest street is {allowed - 5:.0f} m away",
        )
    return True, "spawned on the street nearest the map centre"


def check_teleport(g: Grade) -> Result:
    rows = g.logic.get("teleports") or []
    if len(rows) != len(g.probe["teleports"]):
        return False, "teleport_player() was not measured"
    for target, row in zip(g.probe["teleports"], rows, strict=True):
        s = _state(row.get("state"))
        if not s or s["in_vehicle"] or not _on_ground(s):
            return False, f"after teleport_player{tuple(target)}: {row.get('state')!r}"
        off = math.dist(to_map(target), s["flat"])
        if off > 1.0:
            return (
                False,
                f"teleport_player{tuple(target)} left the player {off:.1f} m away",
            )
    return True, "teleport places the player on the ground where asked"


def _along(
    start: list[float], direction: list[float], s: dict[str, Any]
) -> tuple[float, float]:
    """Distance along and across a world direction from a world start."""
    dx, dz = s["pos"][0] - start[0], s["pos"][2] - start[1]
    along = dx * direction[0] + dz * direction[1]
    across = abs(-dx * direction[1] + dz * direction[0])
    return along, across


def _walk(g: Grade, key: str) -> tuple[float | None, str]:
    spec = g.probe.get("walk")
    if not spec:
        raise Unmeasured("the lab found no open stretch of street for this run")
    row = g.logic.get(key) or {}
    samples = [_state(s) for s in row.get("samples") or []]
    if len(samples) < 7 or not all(samples):
        return None, "player_state() did not answer while walking"
    if not all(_on_ground(s) for s in samples):
        return (
            None,
            f"the player left the ground while walking: {[round(s['pos'][1], 2) for s in samples]}",
        )
    along, across = _along(spec["start"], spec["dir"], samples[-1])
    if across > 1.5:
        return None, f"walking straight drifted {across:.1f} m sideways"
    return along, ""


def check_walk(g: Grade) -> Result:
    along, why = _walk(g, "walk")
    if along is None:
        return False, why
    if not 3.0 <= along <= 20.0:
        return (
            False,
            f"3 s of set_player_input forward moved {along:.1f} m (expected 3-20 m)",
        )
    stop = _state((g.logic.get("walk") or {}).get("stopped"))
    if not stop or (stop["speed"] or 0) > 1.0:
        return False, "the player keeps moving after the input returns to zero"
    return True, f"walked {along:.1f} m in 3 s and stopped"


def check_sprint(g: Grade) -> Result:
    walk, why = _walk(g, "walk")
    if walk is None or walk < 3.0:
        return False, why or "walking does not work, so sprinting cannot be compared"
    sprint, why = _walk(g, "sprint")
    if sprint is None:
        return False, why
    ok = sprint >= 1.25 * walk and sprint <= 36.0
    return ok, f"sprinting covered {sprint:.1f} m against {walk:.1f} m walking"


def _blocked(
    g: Grade, key: str, spec_key: str, inside_limit: float, reach: float
) -> Result:
    spec = g.probe.get(spec_key)
    if not spec:
        raise Unmeasured("the lab found no open wall to approach in this run")
    row = g.logic.get(key) or {}
    samples = [_state(s) for s in row.get("samples") or []]
    if len(samples) < 10 or not all(samples):
        return False, f"player_state() did not answer during the approach ({key})"
    fp = g.city.footprints[spec["id"]]
    closest = min(
        edge_dist(s["flat"], fp) if not inside(s["flat"], fp) else 0.0 for s in samples
    )
    for s in samples:
        if inside(s["flat"], fp) and edge_dist(s["flat"], fp) > inside_limit:
            return (
                False,
                f"went {edge_dist(s['flat'], fp):.1f} m into building {spec['id']}",
            )
        if g.city.depth(s["flat"]) > inside_limit:
            return False, "went through a building"
    if closest > reach:
        return (
            False,
            f"never reached the wall (closest {closest:.1f} m), so being stopped is not shown",
        )
    return (
        True,
        f"stopped by the wall of building {spec['id']} ({closest:.1f} m from it at the closest)",
    )


def check_walls_block(g: Grade) -> Result:
    return _blocked(g, "wall", "wall", 0.3, 1.5)


def _entered(g: Grade, key: str) -> tuple[bool, str]:
    row = g.logic.get(key) or {}
    s = _state(row.get("state"))
    vid = row.get("vehicle_id")
    if row.get("entered") is not True or not s or not s["in_vehicle"]:
        return (
            False,
            f"enter_nearest_vehicle() = {row.get('entered')!r}, player_state = {row.get('state')!r}",
        )
    if not isinstance(vid, int) or s["vehicle_id"] != vid:
        return (
            False,
            f"in vehicle {s['vehicle_id']!r}, but spawn_parked_car returned {vid!r}",
        )
    return True, ""


def check_enter_vehicle(g: Grade) -> Result:
    row = g.logic.get("enter") or {}
    if row.get("far") is not False:
        return (
            False,
            f"enter_nearest_vehicle() with no car within 5 m returned {row.get('far')!r}",
        )
    ok, why = _entered(g, "enter")
    if not ok:
        return False, why
    near = [v for v in row.get("vehicles") or [] if isinstance(v, list) and len(v) >= 3]
    car = to_map(g.probe["run"]["car"])
    if not any(math.dist((v[0], -v[2]), car) <= 1.5 for v in near):
        return False, "no node in the group `vehicles` stands where the car was spawned"
    return True, "a parked car is entered from 3.5 m and not from far away"


def _speed_of(s: dict[str, Any]) -> float:
    return s["speed"] if s and s["speed"] is not None else 0.0


def check_drive(g: Grade) -> Result:
    ok, why = _entered(g, "enter")
    if not ok:
        return False, "could not get into a car: " + why
    samples = [_state(s) for s in (g.logic.get("drive") or {}).get("samples") or []]
    if len(samples) < 6 or not all(samples):
        return False, "player_state() did not answer while driving"
    run = g.probe["run"]
    along, across = _along(run["car"], run["dir"], samples[-1])
    speed = _speed_of(samples[-1])
    if not all(s["in_vehicle"] for s in samples):
        return False, "the player left the car while driving"
    if speed <= 6.0 or along < 8.0:
        return (
            False,
            f"3 s of full throttle: speed {speed:.1f} m/s, {along:.1f} m forward",
        )
    if across > 3.0 or not all(-0.5 <= s["pos"][1] <= 3.0 for s in samples):
        return False, f"the car left its line ({across:.1f} m sideways) or the ground"
    return True, f"3 s of throttle: {speed:.1f} m/s, {along:.1f} m forward"


def check_brake(g: Grade) -> Result:
    drive = [_state(s) for s in (g.logic.get("drive") or {}).get("samples") or []]
    if not drive or not all(drive) or _speed_of(drive[-1]) < 4.0:
        return False, "the car never got going, so braking is not shown"
    samples = [_state(s) for s in (g.logic.get("brake") or {}).get("samples") or []]
    if len(samples) < 4 or not all(samples):
        return False, "player_state() did not answer while braking"
    end = _speed_of(samples[-1])
    return (
        end < 1.5,
        f"from {_speed_of(drive[-1]):.1f} m/s, 3 s of brake left {end:.1f} m/s",
    )


def check_steering(g: Grade) -> Result:
    ok, why = _entered(g, "steer_enter")
    if not ok:
        return False, "could not get into a car: " + why
    samples = [_state(s) for s in (g.logic.get("steer") or {}).get("samples") or []]
    if len(samples) < 10 or not all(samples):
        return False, "player_state() did not answer while steering"
    turned = 0.0
    previous = None
    last = samples[0]["flat"]
    for s in samples[1:]:
        dx, dy = s["flat"][0] - last[0], s["flat"][1] - last[1]
        if math.hypot(dx, dy) < 0.05:
            continue
        bearing = math.degrees(math.atan2(dx, dy))
        if previous is not None:
            turned += (bearing - previous + 540.0) % 360.0 - 180.0
        previous = bearing
        last = s["flat"]
    return (
        turned >= 45.0,
        f"steer +1 (right) for 3 s turned the car {turned:+.0f} degrees (clockwise is +)",
    )


def check_drive_blocked(g: Grade) -> Result:
    ok, why = _entered(g, "car_wall_enter")
    if not ok:
        return False, "could not get into a car: " + why
    return _blocked(g, "car_wall", "car_wall", 0.5, 4.0)


def check_exit_vehicle(g: Grade) -> Result:
    ok, why = _entered(g, "enter")
    if not ok:
        return False, "never got into a car: " + why
    row = g.logic.get("exit") or {}
    s = _state(row.get("state"))
    if (
        row.get("exited") is not True
        or not s
        or s["in_vehicle"]
        or s["vehicle_id"] != -1
    ):
        return (
            False,
            f"exit_vehicle() = {row.get('exited')!r}, player_state = {row.get('state')!r}",
        )
    car = _state(row.get("car_state"))
    if not _on_ground(s) or g.city.depth(s["flat"]) > 0.3:
        return False, f"the player got out at {s['pos']}, not on the ground"
    if car and math.dist(car["flat"], s["flat"]) > 6.0:
        return (
            False,
            f"the player got out {math.dist(car['flat'], s['flat']):.1f} m from the car",
        )
    return True, "the player steps out beside the car, on foot"


def _traffic(g: Grade) -> dict[int, list[Point]]:
    tracks: dict[int, list[Point]] = {}
    for sample in (g.logic.get("traffic") or {}).get("samples") or []:
        for row in sample if isinstance(sample, list) else []:
            pos = _xyz(row.get("position")) if isinstance(row, dict) else None
            if pos is None or not isinstance(row.get("id"), int):
                continue
            tracks.setdefault(row["id"], []).append((pos[0], -pos[2]))
    return tracks


def check_traffic_spawn(g: Grade) -> Result:
    row = g.logic.get("traffic") or {}
    first, second = g.probe["traffic"]["first"], g.probe["traffic"]["second"]
    states = row.get("first") or []
    ids = {s.get("id") for s in states if isinstance(s, dict)}
    if len(states) != first or len(ids) != first:
        return (
            False,
            f"set_traffic({first}) gave {len(states)} states with {len(ids)} distinct ids",
        )
    if row.get("group_first") != first:
        return (
            False,
            f"set_traffic({first}) left {row.get('group_first')!r} nodes in the group `traffic`",
        )
    if len(row.get("second") or []) != second:
        return (
            False,
            f"set_traffic({second}) after {first} gave {len(row.get('second') or [])} states",
        )
    for s in states:
        pos = _xyz(s.get("position"))
        if (
            pos is None
            or not -0.5 <= pos[1] <= 3.0
            or not g.city.in_bounds((pos[0], -pos[2]))
        ):
            return (
                False,
                f"a traffic car at {s.get('position')!r} is not on the ground of the map",
            )
    return True, f"set_traffic() places exactly {first}, then {second}, cars"


def _moving(g: Grade) -> tuple[list[int], str]:
    """Traffic cars that drove at least 20 m over the sampled 20 s.

    A car may be respawned elsewhere (a dead end, say); a jump counts as at most
    15 m of driving, so teleporting cannot stand in for moving.
    """
    tracks = _traffic(g)
    samples = g.probe["traffic"]["samples"]
    moved = []
    for car, points in tracks.items():
        if len(points) < samples:
            continue
        steps = [
            min(15.0, math.dist(points[k], points[k + 1]))
            for k in range(len(points) - 1)
        ]
        if sum(steps) >= 20.0:
            moved.append(car)
    return moved, ""


def check_traffic_moves(g: Grade) -> Result:
    moved, why = _moving(g)
    if why:
        return False, why
    need = int(0.75 * g.probe["traffic"]["first"])
    return (
        len(moved) >= need,
        f"{len(moved)}/{g.probe['traffic']['first']} traffic cars drove 20 m or more in 20 s",
    )


def check_traffic_on_roads(g: Grade) -> Result:
    moved, why = _moving(g)
    if len(moved) < int(0.75 * g.probe["traffic"]["first"]):
        return (
            False,
            why or "traffic does not move, so staying on the road is not shown",
        )
    points = [p for track in _traffic(g).values() for p in track]
    on = sum(1 for p in points if g.city.road_excess(p) <= ROAD_SLACK)
    share = on / len(points)
    return (
        share >= 0.95,
        f"{share:.0%} of {len(points)} traffic positions on a road (half its width + {ROAD_SLACK:.0f} m)",
    )


def check_traffic_not_in_buildings(g: Grade) -> Result:
    moved, why = _moving(g)
    if len(moved) < int(0.75 * g.probe["traffic"]["first"]):
        return False, why or "traffic does not move, so avoiding buildings is not shown"
    points = [p for track in _traffic(g).values() for p in track]
    bad = [p for p in points if g.city.depth(p) > 0.5]
    share = len(bad) / len(points)
    return (
        share <= 0.02,
        f"{len(bad)} of {len(points)} traffic positions ({share:.1%}) more than 0.5 m inside a building (at most 2%)",
    )


def check_minimap_marker(g: Grade) -> Result:
    rows = g.logic.get("markers") or []
    if len(rows) < 3:
        return False, "minimap_player_marker() was not measured"
    x0, y0, x1, y1 = g.city.bounds
    for row in rows:
        s = _state(row.get("state"))
        uv = row.get("uv")
        if not s or not (
            isinstance(uv, list)
            and len(uv) >= 2
            and all(_num(v) is not None for v in uv[:2])
        ):
            return False, f"minimap_player_marker() = {uv!r}"
        u = (s["flat"][0] - x0) / (x1 - x0)
        v = (y1 - s["flat"][1]) / (y1 - y0)
        if abs(uv[0] - u) > 0.05 or abs(uv[1] - v) > 0.05:
            return (
                False,
                f"marker {uv} for the player at {s['pos']}, expected [{u:.3f}, {v:.3f}]",
            )
    return True, f"the marker follows the player across {len(rows)} positions"


# Scene checks ----------------------------------------------------------------------


def check_scene_loads(g: Grade) -> Result:
    if not g.scene.get("main_scene"):
        return False, "no main scene set in project.godot"
    ok = bool(
        g.scene.get("instanced")
        and g.scene.get("has_camera")
        and g.scene.get("players") == 1
    )
    return ok, (
        "main scene, a current Camera3D and one node in the group `player`"
        if ok
        else f"instanced={g.scene.get('instanced')} camera={g.scene.get('has_camera')} players={g.scene.get('players')}"
    )


def check_map_argument(g: Grade) -> Result:
    count = g.scene.get("building_count")
    low, high = _heldout_counts(g)
    if not (isinstance(count, int) and low <= count <= high):
        return (
            False,
            f"with --map=<held-out map> the scene built {count!r} buildings, expected {low}",
        )
    return True, "`-- --map=<path>` loads that map in the game"


def _follow_ok(row: Any) -> tuple[bool, str]:
    if (
        not isinstance(row, dict)
        or row.get("camera") is None
        or row.get("player") is None
    ):
        return False, "no camera or player to compare"
    dist = math.dist(row["camera"], row["player"])
    screen = row.get("screen")
    size = row.get("viewport") or [0, 0]
    on = (
        isinstance(screen, list)
        and 0 <= screen[0] <= size[0]
        and 0 <= screen[1] <= size[1]
    )
    if not 2.0 <= dist <= 40.0 or not on:
        return False, f"camera {dist:.1f} m from the player, player on screen: {on}"
    return True, f"{dist:.1f} m behind"


def check_camera_follows(g: Grade) -> Result:
    for key in ("follow_start", "follow_moved"):
        ok, why = _follow_ok(g.scene.get(key))
        if not ok:
            return False, f"{key}: {why}"
    return True, "a third-person camera keeps the player in view, also after a teleport"


def _stats_ok(stats: Any) -> bool:
    return (
        isinstance(stats, dict)
        and stats.get("variance", 0) > 0.002
        and stats.get("distinct", 0) >= 12
    )


def check_city_visible(g: Grade) -> Result:
    stats = g.scene.get("image_start")
    if not _stats_ok(stats):
        return False, f"the frame is blank or flat: {stats}"
    changed = _num(g.scene.get("buildings_share"))
    if changed is None or changed < 0.03:
        return (
            False,
            f"hiding the buildings changes {changed!r} of the game camera's view; buildings are not on screen",
        )
    return True, f"buildings cover {changed:.0%} of the game camera's view"


def check_roofs_from_above(g: Grade) -> Result:
    shot = g.scene.get("ortho") or {}
    mask = shot.get("mask")
    if not isinstance(mask, str) or not mask:
        return False, "the top-down view could not be rendered"
    cols, rows = shot.get("cols"), shot.get("rows")
    size = g.probe["ortho"]["size"]
    center = g.probe["ortho"]["center"]
    width, height = shot.get("width"), shot.get("height")
    grid = g.probe["ortho"]["grid"]
    metres = size / height
    inter = union = expect_n = 0
    for r in range(rows):
        for c in range(cols):
            px, py = c * grid + grid / 2, r * grid + grid / 2
            wx = center[0] + (px - width / 2) * metres
            wz = center[1] + (py - height / 2) * metres
            p = (wx, -wz)
            expect = any(
                inside(p, g.city.footprints[i])
                for i in g.city.near(p, 0.0)
                if i in g.city.solid
            )
            got = mask[r * cols + c] == "1"
            expect_n += expect
            inter += expect and got
            union += expect or got
    if expect_n < 50:
        raise Unmeasured("the lab's top-down view holds too few buildings")
    iou = inter / union if union else 0.0
    return (
        iou >= 0.8,
        f"rendered roofs match the footprints from above: IoU {iou:.2f} (need 0.80)",
    )


def check_attribution(g: Grade) -> Result:
    labels = g.scene.get("attribution") or []
    size = g.scene.get("viewport") or [0, 0]
    for label in labels:
        rect = label.get("rect") or [0, 0, 0, 0]
        visible = label.get("visible") and rect[2] > 0 and rect[3] > 0
        on = (
            rect[0] >= -2
            and rect[1] >= -2
            and rect[0] + rect[2] <= size[0] + 2
            and rect[1] + rect[3] <= size[1] + 2
        )
        if visible and on and "OpenStreetMap" in str(label.get("text", "")):
            return True, "the ODbL attribution is on screen"
    return (
        False,
        f"no visible Label in the group `attribution` says OpenStreetMap: {labels[:3]}",
    )


def check_minimap(g: Grade) -> Result:
    maps = g.scene.get("minimap") or []
    size = g.scene.get("viewport") or [0, 0]
    for row in maps:
        rect = row.get("rect") or [0, 0, 0, 0]
        on = (
            rect[0] >= -2
            and rect[1] >= -2
            and rect[0] + rect[2] <= size[0] + 2
            and rect[1] + rect[3] <= size[1] + 2
        )
        if (
            row.get("visible")
            and rect[2] >= 96
            and rect[3] >= 96
            and on
            and (row.get("distinct") or 0) >= 4
        ):
            return True, f"a {rect[2]:.0f}x{rect[3]:.0f} minimap is drawn on screen"
    return (
        False,
        f"no visible minimap Control (group `minimap`, at least 96x96 px, drawn): {maps[:3]}",
    )


def check_keyboard_walk(g: Grade) -> Result:
    row = g.scene.get("keys_walk") or {}
    before, after = _state(row.get("before")), _state(row.get("after"))
    if not before or not after:
        return False, "player_state() did not answer in the scene"
    moved = math.dist(before["flat"], after["flat"])
    return moved >= 2.0 and _on_ground(
        after
    ), f"holding move_forward for 2 s moved the player {moved:.1f} m"


def check_keyboard_drive(g: Grade) -> Result:
    row = g.scene.get("keys_drive") or {}
    entered, driven, left = (_state(row.get(k)) for k in ("entered", "driven", "left"))
    if not entered or not entered["in_vehicle"]:
        return False, "pressing interact next to a parked car did not enter it"
    if not driven:
        return False, "player_state() did not answer while driving"
    moved = math.dist(entered["flat"], driven["flat"])
    if moved < 3.0:
        return False, f"holding accelerate for 2 s moved the car {moved:.1f} m"
    if not left or left["in_vehicle"]:
        return False, "pressing interact again did not leave the car"
    return True, f"interact enters, accelerate drives {moved:.1f} m, interact leaves"


def check_frame_time(g: Grade) -> Result:
    times = sorted(g.scene.get("frame_ms") or [])
    if len(times) < 10:
        return False, "no frame timings"
    p95 = times[int(0.95 * (len(times) - 1))]
    # Software rendering on a CPU (ADR-0073): a loose ceiling that an
    # unoptimised but sane city meets and a runaway one does not.
    return (
        p95 < 250,
        f"p95 frame {p95:.0f} ms on a CPU renderer, whole city and 8 traffic cars",
    )


CHECKS = {
    "contract": check_contract,
    "own_code": check_own_code,
    "renderer": check_renderer,
    "input_actions": check_input_actions,
    "default_map": check_default_map,
    "heldout_loads": check_heldout_loads,
    "bad_map": check_bad_map,
    "building_count": check_building_count,
    "building_heights": check_building_heights,
    "building_info": check_building_info,
    "buildings_group": check_buildings_group,
    "footprints_placed": check_footprints_placed,
    "roof_raycast": check_roof_raycast,
    "street_raycast": check_street_raycast,
    "footprint_shape": check_footprint_shape,
    "collision_geometry": check_collision_geometry,
    "ground_uses_physics": check_ground_uses_physics,
    "map_argument": check_map_argument,
    "roofs_from_above": check_roofs_from_above,
    "city_visible": check_city_visible,
    "step_ticks": check_step_ticks,
    "spawn": check_spawn,
    "teleport": check_teleport,
    "walk": check_walk,
    "sprint": check_sprint,
    "walls_block": check_walls_block,
    "scene_loads": check_scene_loads,
    "camera_follows": check_camera_follows,
    "keyboard_walk": check_keyboard_walk,
    "enter_vehicle": check_enter_vehicle,
    "drive": check_drive,
    "brake": check_brake,
    "steering": check_steering,
    "drive_blocked": check_drive_blocked,
    "exit_vehicle": check_exit_vehicle,
    "keyboard_drive": check_keyboard_drive,
    "traffic_spawn": check_traffic_spawn,
    "traffic_moves": check_traffic_moves,
    "traffic_on_roads": check_traffic_on_roads,
    "traffic_not_in_buildings": check_traffic_not_in_buildings,
    "minimap_marker": check_minimap_marker,
    "minimap": check_minimap,
    "attribution": check_attribution,
    "frame_time": check_frame_time,
}
SCENE_CHECKS = {
    "map_argument",
    "roofs_from_above",
    "city_visible",
    "scene_loads",
    "camera_follows",
    "keyboard_walk",
    "keyboard_drive",
    "minimap",
    "attribution",
    "frame_time",
}


def run_check(name: str, workspace: Path) -> int:
    try:
        g = Grade(workspace)
        if not g.meta.get("project"):
            # Nothing to grade is the agent's result, for every check alike.
            print(f"FAIL {name}: no project.godot in the workspace")
            return 1
        engine = [g.meta.get(k, {}).get("exit") for k in ("logic", "scene")]
        if not g.logic and not g.scene and 127 in engine:
            raise Unmeasured("the lab's image could not start Godot")
        if name not in ("own_code", "renderer") and not g.logic and not g.scene:
            print(f"FAIL {name}: the project did not run under Godot (no answers)")
            return 1
        scene_exit = (g.meta.get("scene") or {}).get("exit")
        if (
            name in SCENE_CHECKS
            and g.scene.get("phase")
            and not g.scene.get("completed")
            and scene_exit not in (0, None)
        ):
            # The game itself died in the rendered pass (a crash or a hang):
            # whatever it answered before that is still judged, the rest fails.
            print(
                f"note: the rendered pass ended with exit {scene_exit} before it completed"
            )
        if name in SCENE_CHECKS and not g.scene.get("phase"):
            # The API ran but the rendered pass never started: the lab's display.
            raise Unmeasured("the rendered scene pass did not run")
        if (
            name not in SCENE_CHECKS
            and not g.logic.get("phase")
            and name not in ("own_code", "renderer")
        ):
            raise Unmeasured("the headless pass did not run")
        passed, detail = CHECKS[name](g)
    except Unmeasured as exc:
        print(f"UNMEASURED {name}: {exc}")
        # 124 is the code the validation lane already reads as "no answer".
        return 124
    except Exception as exc:  # a malformed answer is the agent's, not the lab's
        print(f"FAIL {name}: unreadable answer ({type(exc).__name__}: {exc})")
        return 1
    print(f"{'PASS' if passed else 'FAIL'} {name}: {detail}")
    return 0 if passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("harvest")
    h.add_argument("workspace", type=Path)
    h.add_argument("run_id")
    c = sub.add_parser("check")
    c.add_argument("name", choices=sorted(CHECKS))
    c.add_argument("workspace", type=Path)
    c.add_argument("run_id", nargs="?", default="")
    p = sub.add_parser("probe")
    p.add_argument("run_id")
    p.add_argument("--map", type=Path, help="also write the held-out map here")
    args = parser.parse_args(argv)
    if args.cmd == "harvest":
        return harvest(args.workspace.resolve(), args.run_id)
    if args.cmd == "probe":
        data, probe = build_probe(args.run_id)
        if args.map:
            args.map.write_text(json.dumps(data))
        print(json.dumps(probe, indent=1))
        return 0
    return run_check(args.name, args.workspace.resolve())


if __name__ == "__main__":
    sys.exit(main())
