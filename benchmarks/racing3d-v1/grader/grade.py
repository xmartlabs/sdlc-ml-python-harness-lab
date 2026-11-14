"""The grade for racing3d-v1 (ADR-0078): probe, harvest, then one verdict per check.

Two commands share this file, and the case's ``task.yaml`` runs them in order:

``python grade.py harvest <workspace> <run_id>``
    Generates this run's tracks and probes, copies the agent's project to a
    scratch directory, runs the GDScript harvester in it three times (the race
    API headless, the scene on a software renderer, a plain launch headless)
    and stores the raw answers under ``<workspace>/.lab-grade/``. It judges
    nothing: it exits 1 only when there is no Godot project at all (the case's
    entry-point check) and 0 otherwise, so a broken game shows up as failed
    *checks*, never as a failed harvest.

``python grade.py check <name> <workspace> <run_id>``
    Reads those answers and decides one check against the lab's own geometry:
    the track polygon, the signed distance to the centreline and the checkpoint
    gates as segments across the track. Exit 0 passes, 1 fails, 124 means the
    lab could not measure (no harvest), which the validation lane records as
    inconclusive.

Why tracks are generated per run, not stored: the agent's container sees the
lab's git history, so a committed track is one ``git show`` away. A track drawn
from a seed derived from the run id cannot be read anywhere before the run.
The second, held-out track has a different family of shapes (tighter corners,
another width) and is used only for the rivals: an AI tuned to one track fails
there.

Why the harvester drives: the lab's own path follower (pure pursuit on the
lab's centreline) drives the agent's car through ``set_input``/``step``, so
lap, checkpoint, cut and wrong-way rules are judged on laps the lab drove and
measured itself, not on laps the agent says it drove.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import random
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

GRADER_DIR = Path(__file__).resolve().parent
GRADE_DIR = ".lab-grade"
CASE = "racing3d-v1"
TICK = 1.0 / 60.0

# The Race autoload's contract.
CONTRACT_METHODS = (
    "load_track",
    "reset",
    "set_input",
    "step",
    "car_state",
    "respawn",
    "add_rivals",
    "rival_states",
    "race_time",
)
ACTIONS = {
    # action: keys any of which satisfies "arrow keys / WASD and R"
    "accelerate": {"Up", "W"},
    "brake": {"Down", "S"},
    "steer_left": {"Left", "A"},
    "steer_right": {"Right", "D"},
    "reset_car": {"R"},
}

# The lab driver's speed profile: cautious on purpose, so any car that meets
# the contract's performance envelope can follow it.
A_LAT = 4.0
A_BRAKE = 3.0
V_MAX = 17.0
PATH_STEP = 2.0
START_BACK = 6.0

# The determinism program: (ticks, throttle, brake, steer).
PROGRAM = [
    (60, 1.0, 0.0, 0.0),
    (30, 1.0, 0.0, -0.5),
    (40, 0.8, 0.0, 0.4),
    (50, 0.6, 0.0, 0.0),
    (30, 0.0, 0.8, 0.0),
    (60, 1.0, 0.0, -0.3),
    (40, 0.7, 0.0, 0.35),
    (60, 0.5, 0.0, 0.0),
    (30, 0.0, 1.0, 0.0),
]

# Fields of one harvested trace sample, in order.
SAMPLE = (
    "tick",
    "x",
    "y",
    "z",
    "speed",
    "heading",
    "lap",
    "checkpoint",
    "wrong_way",
    "on_track",
    "upside_down",
    "race_time",
    "lap_times",
)


# --- Geometry ----------------------------------------------------------------------

Point = tuple[float, float]


def _sub(a: Point, b: Point) -> Point:
    return (a[0] - b[0], a[1] - b[1])


def _dot(a: Point, b: Point) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _cross(a: Point, b: Point) -> float:
    return a[0] * b[1] - a[1] * b[0]


def _norm(a: Point) -> float:
    return math.hypot(a[0], a[1])


def _unit(a: Point) -> Point:
    n = _norm(a) or 1.0
    return (a[0] / n, a[1] / n)


def _left(t: Point) -> Point:
    """The left-hand normal of a direction in the x/z plane, seen from above.

    Godot is right-handed with +Y up: seen from above (+Y), with -Z pointing
    up the screen, a left turn is counter-clockwise. For a direction (x, z)
    the left normal is (z, -x).
    """
    return (t[1], -t[0])


def heading_of(t: Point) -> float:
    """The contract's heading for a direction: 0 faces -Z, 90 faces -X."""
    return math.degrees(math.atan2(-t[0], -t[1]))


def forward_of(heading_deg: float) -> Point:
    h = math.radians(heading_deg)
    return (-math.sin(h), -math.cos(h))


def angle_diff(a: float, b: float) -> float:
    """Signed smallest difference a - b in degrees, in (-180, 180]."""
    d = (a - b) % 360.0
    return d - 360.0 if d > 180.0 else d


def _segments_cross(p1: Point, p2: Point, a: Point, b: Point) -> float | None:
    """Where on p1->p2 (0..1) it crosses segment a-b, or None."""
    r = _sub(p2, p1)
    s = _sub(b, a)
    denom = _cross(r, s)
    if abs(denom) < 1e-12:
        return None
    qp = _sub(a, p1)
    t = _cross(qp, s) / denom
    u = _cross(qp, r) / denom
    if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
        return t
    return None


def _point_segment(p: Point, a: Point, b: Point) -> tuple[float, float]:
    """(distance, t along a->b) from p to segment a-b."""
    ab = _sub(b, a)
    length2 = _dot(ab, ab)
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, _dot(_sub(p, a), ab) / length2))
    closest = (a[0] + ab[0] * t, a[1] + ab[1] * t)
    return _norm(_sub(p, closest)), t


def _circumradius(a: Point, b: Point, c: Point) -> float:
    ab, bc, ca = _norm(_sub(a, b)), _norm(_sub(b, c)), _norm(_sub(c, a))
    cross = abs(_cross(_sub(b, a), _sub(c, a)))
    if cross < 1e-9:
        return float("inf")
    return ab * bc * ca / (2.0 * cross)


def _shoelace(points: list[Point]) -> float:
    n = len(points)
    return sum(
        points[i][0] * points[(i + 1) % n][1] - points[(i + 1) % n][0] * points[i][1]
        for i in range(n)
    )


def _resample_closed(points: list[Point], n: int) -> list[Point]:
    lengths = [
        _norm(_sub(points[(i + 1) % len(points)], points[i]))
        for i in range(len(points))
    ]
    total = sum(lengths)
    out: list[Point] = []
    i = 0
    acc = 0.0
    for k in range(n):
        target = k * total / n
        while acc + lengths[i] < target:
            acc += lengths[i]
            i += 1
        t = (target - acc) / lengths[i] if lengths[i] else 0.0
        a, b = points[i], points[(i + 1) % len(points)]
        out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


class Track:
    """The lab's view of a track: centreline, gates, signed distances."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.points: list[Point] = [
            (float(p[0]), float(p[1])) for p in data["centerline"]
        ]
        self.width = float(data["width"])
        self.checkpoints = [int(c) for c in data["checkpoints"]]
        self.n = len(self.points)
        self.cum = [0.0]
        for i in range(self.n):
            self.cum.append(
                self.cum[-1]
                + _norm(_sub(self.points[(i + 1) % self.n], self.points[i]))
            )
        self.length = self.cum[-1]
        self.gates = [self.gate(k) for k in range(len(self.checkpoints))]

    def tangent(self, i: int) -> Point:
        prev = self.points[(i - 1) % self.n]
        nxt = self.points[(i + 1) % self.n]
        return _unit(_sub(nxt, prev))

    def gate(self, k: int) -> tuple[Point, Point, Point, Point]:
        """(a, b, centre, tangent) of checkpoint gate k across the full width."""
        i = self.checkpoints[k]
        c = self.points[i]
        t = self.tangent(i)
        n = _left(t)
        half = self.width / 2.0
        return (
            (c[0] + n[0] * half, c[1] + n[1] * half),
            (c[0] - n[0] * half, c[1] - n[1] * half),
            c,
            t,
        )

    def project(self, p: Point) -> tuple[float, float, Point]:
        """(arc length s, signed lateral offset, tangent) of the closest point."""
        best = (float("inf"), 0, 0.0)
        for i in range(self.n):
            d, t = _point_segment(p, self.points[i], self.points[(i + 1) % self.n])
            if d < best[0]:
                best = (d, i, t)
        d, i, t = best
        a, b = self.points[i], self.points[(i + 1) % self.n]
        tangent = _unit(_sub(b, a))
        side = 1.0 if _cross(tangent, _sub(p, a)) < 0 else -1.0
        # _cross(t, p-a) < 0 means p is on the left of t in this x/z convention.
        s = self.cum[i] + t * _norm(_sub(b, a))
        return s, side * d, tangent

    def lateral(self, p: Point) -> float:
        return self.project(p)[1]

    def crossings(
        self, trace: list[tuple[float, Point]]
    ) -> list[tuple[int, float, bool]]:
        """Every gate crossing of a trace of (time, position): (gate, time, forward)."""
        out = []
        for (t1, p1), (t2, p2) in itertools.pairwise(trace):
            if p1 == p2:
                continue
            for k, (a, b, _c, tan) in enumerate(self.gates):
                f = _segments_cross(p1, p2, a, b)
                if f is not None:
                    forward = _dot(_sub(p2, p1), tan) > 0
                    out.append((k, t1 + (t2 - t1) * f, forward))
        out.sort(key=lambda row: row[1])
        return out

    def progress(self, trace: list[tuple[float, Point]]) -> dict[str, Any]:
        """The rules the contract states, applied by the lab to a trace."""
        passes: list[tuple[int, float]] = []
        laps: list[float] = []
        nxt = 0
        last = -1
        for k, t, forward in self.crossings(trace):
            if not forward or k != nxt:
                continue
            if k == 0 and last == len(self.checkpoints) - 1:
                laps.append(t)
            passes.append((k, t))
            last = k
            nxt = (k + 1) % len(self.checkpoints)
        return {"passes": passes, "laps": laps, "last": last}


def validate_track(data: Any, strict: bool) -> list[str]:
    """Problems with a track file; strict adds the lab's own shape rules."""
    problems: list[str] = []
    if not isinstance(data, dict):
        return ["not a JSON object"]
    line = data.get("centerline")
    if not isinstance(line, list) or not all(
        isinstance(p, list)
        and len(p) == 2
        and all(isinstance(v, (int, float)) for v in p)
        for p in line
    ):
        return ["centerline must be a list of [x, z] numbers"]
    width = data.get("width")
    if not isinstance(width, (int, float)) or isinstance(width, bool):
        problems.append("width must be a number")
    checkpoints = data.get("checkpoints")
    if (
        not isinstance(checkpoints, list)
        or not checkpoints
        or not all(isinstance(c, int) and not isinstance(c, bool) for c in checkpoints)
    ):
        problems.append("checkpoints must be a list of integers")
        checkpoints = []
    start = data.get("start")
    if not (
        isinstance(start, dict)
        and isinstance(start.get("position"), list)
        and len(start["position"]) == 3
        and isinstance(start.get("heading_deg"), (int, float))
    ):
        problems.append("start must have position [x, y, z] and heading_deg")
    if checkpoints and (
        checkpoints[0] != 0
        or any(b <= a for a, b in itertools.pairwise(checkpoints))
        or checkpoints[-1] >= len(line)
    ):
        problems.append(
            "checkpoints must start at 0, increase and index the centerline"
        )
    if not strict or problems:
        return problems
    if not isinstance(data.get("name"), str):
        problems.append("name must be a string")
    if data.get("closed") is not True:
        problems.append("closed must be true")
    if not isinstance(data.get("walls"), bool):
        problems.append("walls must be true or false")
    if not 10 <= width <= 14:
        problems.append(f"width {width} outside 10-14")
    if len(line) < 24:
        problems.append(f"{len(line)} centerline points, at least 24 required")
    if len(checkpoints) < 6:
        problems.append(f"{len(checkpoints)} checkpoints, at least 6 required")
    points = [(float(p[0]), float(p[1])) for p in line]
    if len(points) >= 3 and _shoelace(points) > 0:
        problems.append("the centerline is not counter-clockwise seen from above")
    n = len(points)
    for i in range(n):
        for j in range(i + 1, n):
            if min((i - j) % n, (j - i) % n) < 3:
                continue
            d = _seg_seg(points[i], points[(i + 1) % n], points[j], points[(j + 1) % n])
            if d < width:
                problems.append(
                    f"segments {i} and {j} come within {d:.1f} m (track overlaps)"
                )
                return problems
    if not problems:
        track = Track(data)
        sp = start["position"]
        _s, lat, tan = track.project((float(sp[0]), float(sp[2])))
        if abs(lat) > width / 2:
            problems.append("start is not on the track")
        elif abs(angle_diff(float(start["heading_deg"]), heading_of(tan))) > 30:
            problems.append("start heading does not face the racing direction")
    return problems


def _seg_seg(p: Point, q: Point, r: Point, s: Point) -> float:
    if _segments_cross(p, q, r, s) is not None:
        return 0.0
    return min(
        _point_segment(p, r, s)[0],
        _point_segment(q, r, s)[0],
        _point_segment(r, p, q)[0],
        _point_segment(s, p, q)[0],
    )


# --- Probes ------------------------------------------------------------------------


def seed_for(run_id: str) -> int:
    return int.from_bytes(
        hashlib.sha256(f"{CASE}:{run_id}".encode()).digest()[:8], "big"
    )


def generate_track(rng: random.Random, kind: str) -> dict[str, Any]:
    """A smooth, closed, counter-clockwise loop that never comes near itself.

    ``main``: a gently perturbed ellipse, 48 points, corners of radius >= 30 m.
    ``heldout``: rounder but with more lobes, 56 points, corners of 20-30 m and
    a narrower road. Different families on purpose: an AI that only learned the
    first kind of track shows it on the second.
    """
    for _attempt in range(2000):
        if kind == "main":
            a, b = rng.uniform(105, 140), rng.uniform(60, 90)
            amps = {
                2: rng.uniform(0, 0.08),
                3: rng.uniform(0.02, 0.10),
                4: rng.uniform(0, 0.05),
            }
            count, rmin, rmax, width = 48, 30.0, float("inf"), 12.0
        else:
            a, b = rng.uniform(85, 115), rng.uniform(75, 105)
            amps = {
                2: rng.uniform(0.0, 0.10),
                3: rng.uniform(0.05, 0.14),
                4: rng.uniform(0.03, 0.09),
                5: rng.uniform(0.0, 0.05),
            }
            count, rmin, rmax = 56, 20.0, 30.0
            width = rng.choice([11.0, 11.5, 12.0])
        phases = {k: rng.uniform(0, 2 * math.pi) for k in amps}
        rot = rng.uniform(0, 2 * math.pi)
        cx, cz = rng.uniform(-40, 40), rng.uniform(-40, 40)
        dense = []
        for j in range(720):
            th = 2 * math.pi * j / 720
            r = 1 + sum(amp * math.cos(k * th + phases[k]) for k, amp in amps.items())
            x, z = a * r * math.cos(th), b * r * math.sin(th)
            dense.append(
                (
                    cx + x * math.cos(rot) - z * math.sin(rot),
                    cz + x * math.sin(rot) + z * math.cos(rot),
                )
            )
        points = _resample_closed(dense, count)
        if _shoelace(points) > 0:
            points.reverse()
        # Start the loop at a random point so gate 0 is never in the same place.
        shift = rng.randrange(count)
        points = points[shift:] + points[:shift]
        radii = [
            _circumradius(points[i - 1], points[i], points[(i + 1) % count])
            for i in range(count)
        ]
        if not rmin <= min(radii) <= rmax:
            continue
        if any(
            _seg_seg(
                points[i], points[(i + 1) % count], points[j], points[(j + 1) % count]
            )
            < width + 16
            for i in range(count)
            for j in range(i + 1, count)
            if min((i - j) % count, (j - i) % count) >= 5
        ):
            continue
        points = [(round(x, 3), round(z, 3)) for x, z in points]
        # Start a few metres behind gate 0, facing along the road.
        last, first = points[-1], points[0]
        t = _unit(_sub(first, last))
        start = (first[0] - t[0] * START_BACK, first[1] - t[1] * START_BACK)
        return {
            "name": f"{kind}-{rng.randrange(10**6):06d}",
            "width": width,
            "closed": True,
            "centerline": [list(p) for p in points],
            "checkpoints": list(range(0, count, 8)),
            "start": {
                "position": [round(start[0], 3), 0.0, round(start[1], 3)],
                "heading_deg": round(heading_of(t), 3),
            },
            "walls": True,
        }
    raise RuntimeError(f"could not generate a {kind} track")


def stadium_track(rng: random.Random) -> dict[str, Any]:
    """A stadium-shaped loop whose start sits on a 150 m straight.

    The scene checks drive with the keyboard actions, which cannot steer along
    a curve: a long straight ahead of the start keeps them about the car and
    the camera, never about which way the run's random track happens to bend.
    """
    straight, radius = 150.0, rng.uniform(42.0, 50.0)
    dense: list[Point] = []
    for j in range(60):
        dense.append((straight / 2 - straight * j / 60, radius))
    for j in range(60):
        th = math.pi / 2 + math.pi * j / 60
        dense.append((-straight / 2 + radius * math.cos(th), radius * math.sin(th)))
    for j in range(60):
        dense.append((-straight / 2 + straight * j / 60, -radius))
    for j in range(60):
        th = -math.pi / 2 + math.pi * j / 60
        dense.append((straight / 2 + radius * math.cos(th), radius * math.sin(th)))
    rot = rng.uniform(0, 2 * math.pi)
    points = [
        (x * math.cos(rot) - z * math.sin(rot), x * math.sin(rot) + z * math.cos(rot))
        for x, z in _resample_closed(dense, 48)
    ]
    if _shoelace(points) > 0:
        points.reverse()
    # Gate 0 a quarter of the way down a straight, the start 6 m behind it.
    track = Track({"centerline": points, "width": 12.0, "checkpoints": [0]})
    best = max(
        range(48),
        key=lambda i: min(
            _circumradius(
                points[(i + k - 1) % 48], points[(i + k) % 48], points[(i + k + 1) % 48]
            )
            for k in range(-2, 8)
        ),
    )
    points = points[best:] + points[:best]
    points = [(round(x, 3), round(z, 3)) for x, z in points]
    t = _unit(_sub(points[1], points[0]))
    start = (points[0][0] - t[0] * START_BACK, points[0][1] - t[1] * START_BACK)
    del track
    return {
        "name": f"stadium-{rng.randrange(10**6):06d}",
        "width": 12.0,
        "closed": True,
        "centerline": [list(p) for p in points],
        "checkpoints": list(range(0, 48, 8)),
        "start": {
            "position": [round(start[0], 3), 0.0, round(start[1], 3)],
            "heading_deg": round(heading_of(t), 3),
        },
        "walls": True,
    }


def driver_path(
    track: Track,
    shift_at: float | None = None,
    shift_by: float = 0.0,
    plateau: float = 12.0,
    reverse: bool = False,
    v_max: float = V_MAX,
) -> list[list[float]]:
    """The lab driver's line: [x, z, target speed] every ~2 m, closed.

    ``shift_at`` (an arc length on the centreline) moves the line ``shift_by``
    metres to the left (positive, the inside of the loop) for ``plateau``
    metres either side of that point, with a smooth 33 m ramp in and out:
    around a gate that makes the car pass the gate's line off the road (a
    cut), elsewhere it parks the car off the racing line.
    """
    points = track.points[::-1] if reverse else track.points
    dense: list[Point] = []
    for i in range(track.n):
        a, b = points[i], points[(i + 1) % track.n]
        steps = max(1, round(_norm(_sub(b, a)) / PATH_STEP))
        for k in range(steps):
            dense.append(
                (a[0] + (b[0] - a[0]) * k / steps, a[1] + (b[1] - a[1]) * k / steps)
            )
    m = len(dense)
    caps = [v_max] * m
    if shift_at is not None:
        shifted = []
        for i, p in enumerate(dense):
            s = track.project(p)[0]
            d = (s - shift_at + track.length / 2) % track.length - track.length / 2
            if abs(d) < plateau + 33:
                weight = (
                    1.0
                    if abs(d) <= plateau
                    else 0.5 * (1 + math.cos(math.pi * (abs(d) - plateau) / 33))
                )
                tangent = _unit(_sub(dense[(i + 1) % m], dense[i - 1]))
                n = _left(tangent)
                shifted.append(
                    (p[0] + n[0] * shift_by * weight, p[1] + n[1] * shift_by * weight)
                )
                caps[i] = min(caps[i], 9.0)
            else:
                shifted.append(p)
        dense = shifted
    speeds = []
    for i in range(m):
        r = _circumradius(dense[(i - 4) % m], dense[i], dense[(i + 4) % m])
        speeds.append(min(caps[i], math.sqrt(A_LAT * r)))
    for _ in range(2):
        for i in range(m - 1, -1, -1):
            ds = _norm(_sub(dense[(i + 1) % m], dense[i]))
            speeds[i] = min(
                speeds[i], math.sqrt(speeds[(i + 1) % m] ** 2 + 2 * A_BRAKE * ds)
            )
    return [
        [round(p[0], 3), round(p[1], 3), round(v, 3)]
        for p, v in zip(dense, speeds, strict=True)
    ]


def _nearest(path: list[list[float]], p: Point) -> int:
    return min(range(len(path)), key=lambda i: _norm(_sub((path[i][0], path[i][1]), p)))


def _gate_rows(track: Track) -> list[list[float]]:
    return [[a[0], a[1], b[0], b[1], t[0], t[1]] for a, b, _c, t in track.gates]


def _at_arc(track: Track, s: float) -> tuple[Point, Point]:
    """The centreline point and direction at arc length ``s``."""
    s %= track.length
    for i in range(track.n):
        if track.cum[i + 1] >= s:
            a, b = track.points[i], track.points[(i + 1) % track.n]
            f = (s - track.cum[i]) / max(1e-9, track.cum[i + 1] - track.cum[i])
            return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f), _unit(
                _sub(b, a)
            )
    return track.points[0], track.tangent(0)


def build_probe(run_id: str, track_dir: Path) -> dict[str, Any]:
    rng = random.Random(seed_for(run_id))
    main = generate_track(rng, "main")
    heldout = generate_track(rng, "heldout")
    scene = stadium_track(rng)
    cut = dict(main, name=main["name"] + "-open", walls=False)
    track_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    for name, data in (
        ("main", main),
        ("heldout", heldout),
        ("cut", cut),
        ("scene", scene),
    ):
        path = track_dir / f"{name}.json"
        path.write_text(json.dumps(data, indent=1))
        files[name] = str(path)
    bad = {
        "not_json": "{ this is not json",
        "array": "[1, 2, 3]",
        "two_points": json.dumps(
            dict(main, centerline=main["centerline"][:2], checkpoints=[0])
        ),
        "no_centerline": json.dumps(
            {k: v for k, v in main.items() if k != "centerline"}
        ),
        "checkpoint_out_of_range": json.dumps(dict(main, checkpoints=[0, 8, 999])),
        "no_start": json.dumps({k: v for k, v in main.items() if k != "start"}),
    }
    bad_paths = [str(track_dir / "missing.json")]
    for name, text in bad.items():
        path = track_dir / f"bad_{name}.json"
        path.write_text(text)
        bad_paths.append(str(path))

    main_t = Track(main)
    held_t = Track(heldout)
    detour = 2
    main_path = driver_path(main_t)
    gate_index = [_nearest(main_path, c) for _a, _b, c, _t in main_t.gates]

    # Driven into each barrier from the start: aimed 20 m beyond the road's
    # edge, 25 m down the road, so the line to it always meets the barrier.
    start, _heading = _start(main_t)
    p25, t25 = _at_arc(main_t, main_t.project(start)[0] + 25.0)
    n25 = _left(t25)
    wall_targets = [
        [round(p25[0] + n25[0] * side * 20, 3), round(p25[1] + n25[1] * side * 20, 3)]
        for side in (1.0, -1.0)
    ]
    # Off the road to the outside, 20 m past gate 1, on the open track.
    s1 = main_t.project(main_t.gates[1][2])[0]
    p, t = _at_arc(main_t, s1 + 20.0)
    right = (-_left(t)[0], -_left(t)[1])
    respawn_exit = [
        round(p[0] + right[0] * 15 + t[0] * 6, 3),
        round(p[1] + right[1] * 15 + t[1] * 6, 3),
    ]

    # Where the player's car is parked while the rivals race. On the run's
    # track at the inside edge, out of the way; on the held-out track in the
    # middle of the road, half a lap from the start: an obstacle to go around.
    park_main_s = (
        main_t.project(main_t.gates[0][2])[0] + 0.5 * main_t.length
    ) % main_t.length
    park_main = driver_path(main_t, shift_at=park_main_s, shift_by=4.0, plateau=25.0)
    park_main_stop = _nearest(park_main, _at_arc(main_t, park_main_s - 12.0)[0])
    park_held_s = (
        held_t.project(held_t.gates[0][2])[0] + 0.45 * held_t.length
    ) % held_t.length
    park_held = driver_path(held_t, shift_at=park_held_s, shift_by=0.0, plateau=25.0)
    park_held_stop = _nearest(park_held, _at_arc(held_t, park_held_s - 12.0)[0])
    return {
        "case": CASE,
        "run_id": run_id,
        "tracks": files,
        "bad_tracks": bad_paths,
        "track_data": {"main": main, "heldout": heldout, "scene": scene},
        "gates": {"main": _gate_rows(main_t), "heldout": _gate_rows(held_t)},
        "paths": {
            "main": main_path,
            "cut": driver_path(
                main_t,
                shift_at=main_t.project(main_t.gates[detour][2])[0],
                shift_by=15.0,
            ),
            "reverse": driver_path(main_t, reverse=True),
            "park_main": park_main,
            "park_heldout": park_held,
        },
        "gate_index": gate_index,
        "park_main_stop": park_main_stop,
        "park_heldout_stop": park_held_stop,
        "wall_targets": wall_targets,
        "respawn_exit": respawn_exit,
        "detour_gate": detour,
        "program": [list(row) for row in PROGRAM],
        "driver": {"lookahead": 7.0, "lookahead_gain": 0.45},
        "lap_limit_ticks": 7200,
        "rivals": 3,
        "rival_drive_ticks": 3600,
        "heldout_limit_ticks": 9000,
    }


# --- Harvest -----------------------------------------------------------------------

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


def _godot(argv: list[str], timeout: int) -> tuple[int, str]:
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False
        )
        return done.returncode, (done.stdout + done.stderr)[-20000:]
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"") + (exc.stderr or b"")
        text = out.decode(errors="replace") if isinstance(out, bytes) else str(out)
        return 124, text[-20000:]


def _instanced(path: Path) -> bool:
    """Whether a rendered pass got as far as instancing the agent's scene."""
    try:
        return bool(json.loads(path.read_text()).get("instanced"))
    except (OSError, ValueError, AttributeError):
        return False


def harvest(workspace: Path, run_id: str) -> int:
    grade = workspace / GRADE_DIR
    if grade.exists():
        shutil.rmtree(grade)
    grade.mkdir(parents=True)
    probe = build_probe(run_id, grade / "tracks")
    (grade / "probe.json").write_text(json.dumps(probe))
    meta: dict[str, Any] = {"run_id": run_id, "seed": seed_for(run_id)}
    project = project_dir(workspace)
    meta["project"] = str(project.relative_to(workspace)) if project else None
    if project is None:
        (grade / "meta.json").write_text(json.dumps(meta, indent=1))
        # The entry-point check: no project is the agent's failure, and every
        # check after this says so.
        print("harvest: no project.godot in the workspace")
        return 1

    with tempfile.TemporaryDirectory(prefix="racing3d-") as scratch:
        copy = Path(scratch) / "project"
        shutil.copytree(
            project, copy, ignore=shutil.ignore_patterns(".godot", GRADE_DIR, ".git")
        )
        (copy / "__lab_grader__").mkdir(exist_ok=True)
        shutil.copy2(GRADER_DIR / "harvest.gd", copy / "__lab_grader__" / "harvest.gd")
        shots = grade / "shots"
        shots.mkdir()
        script = ["--script", "res://__lab_grader__/harvest.gd"]
        args = ["--", "--probe", str(grade / "probe.json")]

        code, log = _godot(
            ["godot", "--headless", "--path", str(copy), "--import"], 180
        )
        meta["import"] = {"exit": code}
        (grade / "import.log").write_text(log)

        runs = [
            # (phase, binary, extra Godot args, extra user args, timeout)
            ("logic", ["godot", "--headless"], [], [], 360),
            (
                "scene",
                ["godot-render"],
                ["--resolution", "1280x720"],
                [
                    "--shots",
                    str(shots),
                    f"--track={probe['tracks']['scene']}",
                    "--rivals=0",
                ],
                240,
            ),
            ("launch", ["godot", "--headless"], [], [], 90),
        ]
        for phase, binary, extra, user, timeout in runs:
            started = _now()
            # The rendered pass is retried only when Godot died before the
            # agent's scene was instanced: software OpenGL on a virtual display
            # occasionally segfaults at start-up, which is the lab's display
            # stack, not the game. A crash once the scene runs is the game's.
            for _attempt in range(3 if phase == "scene" else 1):
                code, log = _godot(
                    [
                        *binary,
                        "--fixed-fps",
                        "60",
                        "--path",
                        str(copy),
                        *extra,
                        *script,
                        *args,
                        "--out",
                        str(grade / f"{phase}.json"),
                        "--phase",
                        phase,
                        *user,
                    ],
                    timeout,
                )
                if code in (0, 124, 127) or _instanced(grade / f"{phase}.json"):
                    break
            meta[phase] = {"exit": code, "seconds": round(_now() - started, 1)}
            (grade / f"{phase}.log").write_text(log)
    (grade / "meta.json").write_text(json.dumps(meta, indent=1))
    print(
        "harvest: "
        + ", ".join(
            f"{p} exit {meta[p]['exit']} ({meta[p]['seconds']} s)"
            for p in ("logic", "scene", "launch")
        )
    )
    return 0


def _now() -> float:
    import time

    return time.monotonic()


# --- Checks ------------------------------------------------------------------------


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
        self.launch = self._load("launch.json") or {}
        self.main = Track(self.probe["track_data"]["main"])
        self.heldout = Track(self.probe["track_data"]["heldout"])
        self.stadium = Track(self.probe["track_data"]["scene"])

    def _load(self, name: str) -> dict[str, Any] | None:
        path = self.dir / name
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text())
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    def section(self, name: str) -> Any:
        value = self.logic.get(name)
        if value is None:
            raise AssertionError(
                f"the harvest never reached `{name}` (a crash, a hang or a missing method before it)"
            )
        return value


Result = tuple[bool, str]


def _num(v: Any) -> float | None:
    return (
        float(v)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
        else None
    )


def _int(v: Any) -> int | None:
    n = _num(v)
    return int(n) if n is not None and n == int(n) else None


def _state_pos(state: Any) -> Point | None:
    if not isinstance(state, dict):
        return None
    p = state.get("position")
    if isinstance(p, list) and len(p) == 3 and all(_num(v) is not None for v in p):
        return (float(p[0]), float(p[2]))
    return None


def _state_y(state: Any) -> float | None:
    if not isinstance(state, dict):
        return None
    p = state.get("position")
    return _num(p[1]) if isinstance(p, list) and len(p) == 3 else None


def _samples(rows: Any) -> list[dict[str, Any]]:
    out = []
    for row in rows or []:
        if isinstance(row, list) and len(row) == len(SAMPLE):
            out.append(dict(zip(SAMPLE, row, strict=True)))
    return out


def _trace(samples: list[dict[str, Any]]) -> list[tuple[float, Point]]:
    return [
        (s["tick"] * TICK, (float(s["x"]), float(s["z"])))
        for s in samples
        if _num(s.get("x")) is not None and _num(s.get("z")) is not None
    ]


def _start(track: Track) -> tuple[Point, float]:
    sp = track.data["start"]["position"]
    return (float(sp[0]), float(sp[2])), float(track.data["start"]["heading_deg"])


# Static checks ----------------------------------------------------------------------


def check_contract(g: Grade) -> Result:
    if not g.meta.get("project"):
        return False, "no project.godot in the workspace"
    if not g.logic.get("has_race_autoload"):
        return False, "no autoload named Race"
    present = g.logic.get("methods") or {}
    missing = [m for m in CONTRACT_METHODS if not present.get(m)]
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
    text = (
        (project / "project.godot").read_text(errors="replace")
        if (project / "project.godot").is_file()
        else ""
    )
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
    ok = (
        re.search(r'(?m)^renderer/rendering_method\s*=\s*"gl_compatibility"', text)
        is not None
    )
    return (
        ok,
        "Compatibility renderer"
        if ok
        else 'renderer/rendering_method must be "gl_compatibility"',
    )


def check_input_actions(g: Grade) -> Result:
    actions = g.logic.get("actions")
    if not isinstance(actions, dict):
        raise AssertionError("the input map was not harvested")
    for name, keys in ACTIONS.items():
        bound = set(actions.get(name) or [])
        if name not in actions:
            return False, f"no input action named {name!r}"
        if not bound & keys:
            return (
                False,
                f"{name!r} is bound to {sorted(bound)}, expected one of {sorted(keys)}",
            )
    return (
        True,
        "accelerate, brake, steer_left, steer_right and reset_car are bound to keys",
    )


def check_default_track(g: Grade) -> Result:
    project = project_dir(g.workspace)
    path = project / "tracks" / "default.json" if project else None
    if path is None or not path.is_file():
        return False, "no res://tracks/default.json"
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return False, f"res://tracks/default.json is not JSON: {exc}"
    problems = validate_track(data, strict=True)
    return (
        not problems,
        f"default track: {problems[0]}"
        if problems
        else "a valid closed track of the lab's format",
    )


# Driving checks -------------------------------------------------------------------


def check_load_track(g: Grade) -> Result:
    row = g.section("load")
    if row.get("result") is not True:
        return (
            False,
            f"load_track(<this run's track>) returned {row.get('result')!r}, expected true",
        )
    start, _heading = _start(g.main)
    for key, label in (
        ("state", "right after load_track"),
        ("settled", "after 30 idle ticks"),
    ):
        state = row.get(key)
        pos = _state_pos(state)
        if pos is None:
            return False, f"car_state() {label} has no position"
        d = _norm(_sub(pos, start))
        if d > 1.0:
            return False, f"{label} the car is {d:.2f} m from the track's start"
        speed = _num(state.get("speed"))
        if speed is None or speed > 0.5:
            return (
                False,
                f"{label} the car's speed is {state.get('speed')!r}, expected about 0",
            )
        y = _state_y(state)
        if y is None or not -0.5 <= y <= 3.0:
            return False, f"{label} the car's height is {y!r}"
    return True, "the car stands still on the start of a track it has never seen"


def check_bad_track(g: Grade) -> Result:
    rows = g.section("bad_tracks")
    expected = len(g.probe["bad_tracks"])
    if len(rows) < expected:
        return False, f"only {len(rows)}/{expected} malformed files answered (a crash?)"
    for path, row in zip(g.probe["bad_tracks"], rows, strict=False):
        if row.get("result") is not False:
            return (
                False,
                f"load_track({Path(path).name}) returned {row.get('result')!r}, expected false",
            )
    after = g.logic.get("reload_after_bad") or {}
    if after.get("result") is not True:
        return False, "a good track no longer loads after the malformed ones"
    return (
        True,
        f"{expected} malformed or missing files refused, and the game kept working",
    )


def check_track_built(g: Grade) -> Result:
    rays = g.section("rays")
    w = g.main.width / 2
    surface = [r.get("down") for r in rays.get("walls", [])]
    hits = [v for v in surface if _num(v) is not None and -0.3 <= v <= 0.3]
    if len(hits) < 0.95 * len(g.main.points):
        return (
            False,
            f"the road surface is at y=0 under only {len(hits)}/{len(g.main.points)} centreline points",
        )
    good = 0
    for r in rays.get("walls", []):
        left, right = _num(r.get("left")), _num(r.get("right"))
        if (
            left is not None
            and right is not None
            and w - 1.0 <= left <= w + 2.0
            and w - 1.0 <= right <= w + 2.0
        ):
            good += 1
    if good < 0.9 * len(g.main.points):
        return (
            False,
            f"barriers found at both edges of only {good}/{len(g.main.points)} sections",
        )
    open_rows = rays.get("open", [])
    walled = sum(
        1
        for r in open_rows
        if _num(r.get("left")) is not None or _num(r.get("right")) is not None
    )
    ground = sum(
        1
        for r in open_rows
        if _num(r.get("outside")) is not None and -0.5 <= r["outside"] <= 0.5
    )
    if walled > 0.1 * len(open_rows):
        return (
            False,
            f'a track with "walls": false still has barriers at {walled} sections',
        )
    if ground < 0.9 * len(open_rows):
        return (
            False,
            f"the ground does not continue outside the road ({ground}/{len(open_rows)} sections)",
        )
    return (
        True,
        "road at y=0 along the centreline, barriers at both edges, open ground beyond",
    )


def _drive_rows(g: Grade, key: str) -> list[dict[str, Any]]:
    rows = _samples(g.section("dynamics").get(key))
    if len(rows) < 3:
        raise AssertionError(f"the `{key}` drive was not harvested")
    return rows


def check_accelerates(g: Grade) -> Result:
    rows = _drive_rows(g, "accelerate")
    start, heading = _start(g.main)
    last = rows[-1]
    speed = _num(last["speed"])
    if speed is None or speed <= 8.0:
        return (
            False,
            f"after 2 s of full throttle the speed is {last['speed']!r}, expected > 8 m/s",
        )
    d = _sub((last["x"], last["z"]), start)
    if _norm(d) < 5 or _dot(_unit(d), forward_of(heading)) < 0.9:
        return False, "full throttle did not move the car forward along its heading"
    prev = rows[-2]
    moved = _norm(_sub((last["x"], last["z"]), (prev["x"], prev["z"]))) / (
        (last["tick"] - prev["tick"]) * TICK
    )
    if abs(moved - speed) > max(1.0, 0.25 * speed):
        return (
            False,
            f"reported speed {speed:.1f} m/s, but the car moved at {moved:.1f} m/s",
        )
    return True, f"{speed:.1f} m/s after 2 s, straight ahead, speed matches motion"


def check_brakes(g: Grade) -> Result:
    acc = _drive_rows(g, "accelerate")
    rows = _drive_rows(g, "brake")
    peak = max(_num(r["speed"]) or 0.0 for r in acc)
    final = _num(rows[-1]["speed"])
    if peak <= 4.0:
        return False, f"the car never got going (peak {peak:.1f} m/s), nothing to brake"
    if final is None or final >= 0.3 * peak:
        return (
            False,
            f"2 s of full brake left {rows[-1]['speed']!r} m/s from a peak of {peak:.1f}",
        )
    return True, f"{peak:.1f} -> {final:.1f} m/s in 2 s of braking"


def _turn(rows: list[dict[str, Any]], start_heading: float) -> float:
    """How far the direction of motion turned, degrees, left positive."""
    a, b = rows[-3], rows[-1]
    d = _sub((b["x"], b["z"]), (a["x"], a["z"]))
    if _norm(d) < 0.2:
        return 0.0
    return angle_diff(heading_of(_unit(d)), start_heading)


def _steer_check(g: Grade, key: str, sign: float, word: str) -> Result:
    rows = _drive_rows(g, key)
    _start_pos, heading = _start(g.main)
    turned = _turn(rows, heading)
    if sign * turned < 15:
        return (
            False,
            f"steering {word} turned the car's motion {turned:+.0f} degrees (left positive)",
        )
    return True, f"steering {word} turned the car {abs(turned):.0f} degrees {word}"


def check_steers_left(g: Grade) -> Result:
    return _steer_check(g, "left", 1.0, "left")


def check_steers_right(g: Grade) -> Result:
    return _steer_check(g, "right", -1.0, "right")


def check_reverse_or_stop(g: Grade) -> Result:
    rows = _drive_rows(g, "brake_from_rest")
    start, heading = _start(g.main)
    d = _sub((rows[-1]["x"], rows[-1]["z"]), start)
    ahead = _dot(d, forward_of(heading))
    if ahead > 0.5:
        return False, f"braking from rest moved the car {ahead:.1f} m forward"
    return True, "braking from rest stays put or reverses"


def check_heading(g: Grade) -> Result:
    load = g.section("load")
    _start_pos, heading = _start(g.main)
    reported = _num((load.get("settled") or {}).get("heading_deg"))
    if reported is None:
        return False, "car_state() has no heading_deg"
    if abs(angle_diff(reported, heading)) > 5:
        return (
            False,
            f"heading_deg {reported:.1f} at the start, the track says {heading:.1f}",
        )
    for key in ("accelerate", "left", "right"):
        rows = _drive_rows(g, key)
        a, b = rows[-2], rows[-1]
        d = _sub((b["x"], b["z"]), (a["x"], a["z"]))
        h = _num(b["heading"])
        if _norm(d) < 0.3:
            continue
        if h is None or abs(angle_diff(h, heading_of(_unit(d)))) > 20:
            return (
                False,
                f"heading_deg {b['heading']!r} while the car moves toward {heading_of(_unit(d)):.0f}",
            )
    return True, "heading_deg follows the contract's convention while driving"


def check_determinism(g: Grade) -> Result:
    runs = g.section("determinism")
    if len(runs) < 3:
        return False, f"only {len(runs)}/3 replays of the input program finished"
    total = sum(row[0] for row in g.probe["program"])
    positions = []
    for run in runs:
        t0 = _num(run.get("time_after_reset"))
        if t0 is None or abs(t0) > 1.5 * TICK:
            return (
                False,
                f"race_time() right after reset() is {run.get('time_after_reset')!r}, expected 0",
            )
        t = _num(run.get("time"))
        if t is None or abs(t - total * TICK) > 1.5 * TICK:
            return (
                False,
                f"race_time() after {total} ticks is {run.get('time')!r}, expected {total * TICK:.3f}",
            )
        pos = _state_pos(run.get("state"))
        if pos is None:
            return False, "car_state() has no position after the program"
        positions.append((pos, _state_y(run.get("state")) or 0.0, run.get("state")))
    (pa, ya, sa) = positions[0]
    for label, (pb, yb, sb) in zip(
        ("a second run", "a run stepped one tick at a time"), positions[1:], strict=True
    ):
        gap = math.sqrt(_norm(_sub(pa, pb)) ** 2 + (ya - yb) ** 2)
        if gap > 0.01:
            return (
                False,
                f"the same inputs after reset() ended {gap:.3f} m apart in {label}",
            )
        if sa.get("lap") != sb.get("lap") or sa.get("checkpoint") != sb.get(
            "checkpoint"
        ):
            return False, f"the same inputs gave a different lap/checkpoint in {label}"
    return (
        True,
        f"three replays of {total} ticks agree within 1 cm; race_time() counts exact ticks",
    )


def check_wall_blocks(g: Grade) -> Result:
    walls = g.section("walls")
    if len(walls) < 2:
        return False, "the wall drives were not harvested"
    limit = g.main.width / 2 + 2.0
    worst = 0.0
    for drive in walls:
        rows = _samples(drive)
        if len(rows) < 10:
            return False, "a wall drive stopped early"
        for s in rows:
            lat = abs(g.main.lateral((s["x"], s["z"])))
            worst = max(worst, lat)
    if worst > limit:
        return (
            False,
            f"driven into a barrier, the car got {worst:.1f} m from the centreline (road edge {g.main.width / 2:.1f} m)",
        )
    hit = max(
        abs(g.main.lateral((s["x"], s["z"])))
        for drive in walls
        for s in _samples(drive)
    )
    if hit < g.main.width / 2 - 2.0:
        return False, "the lab's drive never reached a barrier"
    return True, f"barriers held: at most {worst:.1f} m from the centreline"


def _lap_rows(g: Grade) -> list[dict[str, Any]]:
    rows = _samples(g.section("lap"))
    if len(rows) < 50:
        raise AssertionError("the lab's lap was not harvested")
    return rows


def _lap_oracle(g: Grade) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = _lap_rows(g)
    oracle = g.main.progress(_trace(rows))
    if not oracle["laps"]:
        raise AssertionError(
            "the lab's driver could not complete a lap with this car "
            f"(it passed {len(oracle['passes'])} gates in order)"
        )
    return rows, oracle


def check_stays_on_ground(g: Grade) -> Result:
    rows = _lap_rows(g)
    bad = [s for s in rows if _num(s["y"]) is None or not -0.5 <= s["y"] <= 3.0]
    if bad:
        return (
            False,
            f"car height left [-0.5, 3] m during a normal lap (y={bad[0]['y']!r})",
        )
    flipped = [s for s in rows if s.get("upside_down") is True]
    if flipped:
        return False, "upside_down reported during a normal lap"
    return True, "the car stays on the road surface for a full lap"


def _changes(rows: list[dict[str, Any]], key: str) -> list[tuple[Any, float]]:
    out: list[tuple[Any, float]] = []
    for s in rows:
        v = s.get(key)
        if not out or out[-1][0] != v:
            out.append((v, s["tick"] * TICK))
    return out


def check_checkpoint_order(g: Grade) -> Result:
    rows, oracle = _lap_oracle(g)
    k = len(g.main.checkpoints)
    seen = _changes(rows, "checkpoint")
    values = [v for v, _t in seen]
    expected = [-1, *range(k), 0]
    if values[: len(expected)] != expected:
        return False, f"checkpoint went {values[:12]}, expected {expected}"
    for (value, t), (gate, t_lab) in zip(
        seen[1 : len(expected)], oracle["passes"], strict=False
    ):
        if value != gate or abs(t - t_lab) > 0.3:
            return (
                False,
                f"checkpoint {value} reported at {t:.2f} s, the car crossed gate {gate} at {t_lab:.2f} s",
            )
    return True, f"{k} checkpoints counted in order, each as the car crossed its gate"


def check_lap_counts(g: Grade) -> Result:
    rows, oracle = _lap_oracle(g)
    t_lap = oracle["laps"][0]
    seen = _changes(rows, "lap")
    values = [v for v, _t in seen]
    if values != [0, 1]:
        return False, f"lap went {values} over one lap and a bit, expected [0, 1]"
    if abs(seen[1][1] - t_lap) > 0.3:
        return (
            False,
            f"lap counted at {seen[1][1]:.2f} s, the car crossed the line at {t_lap:.2f} s",
        )
    times = rows[-1].get("lap_times")
    if not isinstance(times, list) or len(times) != 1:
        return False, f"lap_times after one lap is {times!r}"
    return True, f"one lap counted at {seen[1][1]:.2f} s, exactly once"


def check_lap_time_consistent(g: Grade) -> Result:
    rows, oracle = _lap_oracle(g)
    t_lap = oracle["laps"][0]
    first = next((s for s in rows if s.get("lap") == 1), None)
    times = rows[-1].get("lap_times")
    if (
        first is None
        or not isinstance(times, list)
        or not times
        or _num(times[0]) is None
    ):
        return False, "no lap time recorded"
    lap_time = float(times[0])
    race_time = _num(first.get("race_time"))
    if race_time is None or not -TICK <= race_time - lap_time <= 3.5 * TICK:
        return (
            False,
            f"lap time {lap_time:.3f} s, but race_time() was {first.get('race_time')!r} when the lap counted",
        )
    if abs(lap_time - t_lap) > 0.3:
        return (
            False,
            f"lap time {lap_time:.3f} s, the lab timed the crossing at {t_lap:.3f} s of simulated time",
        )
    return True, f"lap time {lap_time:.2f} s of simulated time, matching the lab's"


def check_cut_not_counted(g: Grade) -> Result:
    rows = _samples(g.section("cut"))
    if len(rows) < 50:
        return False, "the cut lap was not harvested"
    k = g.probe["detour_gate"]
    oracle = g.main.progress(_trace(rows))
    crossed = {gate for gate, _t, fwd in g.main.crossings(_trace(rows)) if fwd}
    if k in crossed:
        return (
            False,
            f"the lab's driver could not steer this car around gate {k} (the car does not follow a path)",
        )
    passed = [gate for gate, _t in oracle["passes"]]
    if passed[:k] != list(range(k)):
        return (
            False,
            f"the lab's driver did not reach gate {k} on the open track (passed {passed})",
        )
    # Did the car come back across the finish line after the cut?
    finish = [t for gate, t, fwd in g.main.crossings(_trace(rows)) if gate == 0 and fwd]
    if len(finish) < 2:
        return (
            False,
            "the lab's driver did not get back to the finish line after the cut",
        )
    values = [v for v, _t in _changes(rows, "checkpoint")]
    if k - 1 not in values:
        return False, f"checkpoints before the cut were not counted ({values})"
    if any(_int(v) is not None and _int(v) >= k for v in values):
        return (
            False,
            f"a checkpoint at or after the cut gate {k} was counted ({values})",
        )
    if rows[-1].get("lap") != 0 or rows[-1].get("lap_times"):
        return (
            False,
            f"a lap with gate {k} skipped was counted (lap={rows[-1].get('lap')!r})",
        )
    return (
        True,
        f"skipping gate {k} off the road stopped the checkpoint count, and no lap was counted",
    )


def check_wrong_way(g: Grade) -> Result:
    rows = _samples(g.section("reverse"))
    if len(rows) < 20:
        return False, "the wrong-way drive was not harvested"
    start, _heading = _start(g.main)
    end = rows[-1]
    s0 = g.main.project(start)[0]
    s1 = g.main.project((end["x"], end["z"]))[0]
    back = (s0 - s1) % g.main.length
    if not 20 <= back <= g.main.length / 2:
        return (
            False,
            "the lab's driver could not drive this car backwards along the track",
        )
    tail = [s for s in rows if s["tick"] >= rows[-1]["tick"] - 60]
    if not all(s.get("wrong_way") is True for s in tail):
        return False, "driving against the racing direction, wrong_way is not true"
    lap = _lap_rows(g)
    false = sum(1 for s in lap if s.get("wrong_way") is False)
    if false < 0.95 * len(lap):
        return (
            False,
            f"wrong_way is false in only {false}/{len(lap)} samples of a normal lap",
        )
    return True, "wrong_way is true driving backwards and false on a normal lap"


def check_on_track(g: Grade) -> Result:
    lap = _lap_rows(g)
    w = g.main.width / 2
    inside = [s for s in lap if abs(g.main.lateral((s["x"], s["z"]))) < w - 2]
    good = sum(1 for s in inside if s.get("on_track") is True)
    if not inside or good < 0.95 * len(inside):
        return (
            False,
            f"on_track is true in only {good}/{len(inside)} samples on the road",
        )
    off = [
        s
        for key in ("cut", "respawn_drive")
        for s in _samples(g.logic.get(key))
        if abs(g.main.lateral((s["x"], s["z"]))) > w + 3
    ]
    if len(off) < 5:
        return False, "the lab could not drive this car off the road"
    false = sum(1 for s in off if s.get("on_track") is False)
    if false < 0.9 * len(off):
        return (
            False,
            f"on_track is false in only {false}/{len(off)} samples on the grass",
        )
    return True, "on_track is true on the road and false on the grass"


def _gate_or_start(
    g: Grade, checkpoint: Any, track: Track | None = None
) -> tuple[Point, Point]:
    track = track or g.main
    k = _int(checkpoint)
    if k is not None and 0 <= k < len(track.gates):
        _a, _b, centre, tangent = track.gates[k]
        return centre, tangent
    start, heading = _start(track)
    return start, forward_of(heading)


def check_respawn(g: Grade) -> Result:
    row = g.section("respawn")
    w = g.main.width / 2
    before = row.get("before") or {}
    pos = _state_pos(before)
    if pos is None or abs(g.main.lateral(pos)) < w + 2:
        return False, "the lab could not drive this car off the road before respawn()"
    checkpoint = _int(before.get("checkpoint"))
    if checkpoint != 1:
        return (
            False,
            f"the car had passed checkpoint 1 when it left the road, the game says {before.get('checkpoint')!r}",
        )
    target, tangent = _gate_or_start(g, checkpoint)
    for key, label in (
        ("after", "right after respawn()"),
        ("settled", "1 s after respawn()"),
    ):
        state = row.get(key) or {}
        p = _state_pos(state)
        if p is None:
            return False, f"{label}: car_state() has no position"
        if _norm(_sub(p, target)) > 6.0:
            return (
                False,
                f"{label} the car is {_norm(_sub(p, target)):.1f} m from checkpoint {checkpoint}",
            )
        speed = _num(state.get("speed"))
        if speed is None or speed > 1.0:
            return False, f"{label} the car moves at {state.get('speed')!r} m/s"
        if state.get("on_track") is not True or state.get("upside_down") is not False:
            return (
                False,
                f"{label}: on_track={state.get('on_track')!r} upside_down={state.get('upside_down')!r}",
            )
        if _int(state.get("checkpoint")) != checkpoint or _int(
            state.get("lap")
        ) != _int(before.get("lap")):
            return False, f"{label} the race progress changed"
    go = _state_pos(row.get("driven"))
    settled = _state_pos(row.get("settled"))
    if go is None or settled is None:
        return False, "the car did not move after respawn()"
    d = _sub(go, settled)
    if _norm(d) < 2 or _dot(_unit(d), tangent) < 0.8:
        return False, "after respawn() the car does not face the racing direction"
    return (
        True,
        f"respawn() put the car upright at checkpoint {checkpoint}, still, facing the race",
    )


def check_upside_down(g: Grade) -> Result:
    row = g.section("flip")
    if row.get("flipped_up") is None:
        return False, "no player_car node to put on its roof"
    if row["flipped_up"] > 0:
        return (
            False,
            "setting the player_car node's transform did not flip the car (its pose is not the node's)",
        )
    state = row.get("flipped") or {}
    if state.get("upside_down") is not True:
        return False, f"on its roof, upside_down is {state.get('upside_down')!r}"
    for key in ("after", "settled"):
        s = row.get(key) or {}
        if s.get("upside_down") is not False:
            return False, f"after respawn() upside_down is {s.get('upside_down')!r}"
    if (row.get("settled_up") or 0) < 0.9:
        return False, "after respawn() the car is not upright"
    return True, "upside_down detected on the roof, and respawn() rights the car"


# Rivals ---------------------------------------------------------------------------


def _rival_run(g: Grade, key: str) -> dict[str, Any]:
    run = g.section(key)
    if not isinstance(run, dict):
        raise AssertionError(f"the `{key}` race was not harvested")
    return run


def _rival_traces(run: dict[str, Any]) -> list[list[tuple[float, Point]]]:
    traces: list[list[tuple[float, Point]]] = []
    for row in run.get("samples") or []:
        tick, cars = row[0], row[2]
        for i, car in enumerate(cars):
            while len(traces) <= i:
                traces.append([])
            if (
                isinstance(car, list)
                and len(car) >= 3
                and all(_num(v) is not None for v in car[:3])
            ):
                traces[i].append((tick * TICK, (car[0], car[2])))
    return traces


def check_rivals_spawn(g: Grade) -> Result:
    run = _rival_run(g, "rivals_main")
    spawn = run.get("spawn")
    if not isinstance(spawn, list) or len(spawn) != g.probe["rivals"]:
        return (
            False,
            f"rival_states() after add_rivals(3) returned {len(spawn) if isinstance(spawn, list) else spawn!r} cars",
        )
    start, _h = _start(g.main)
    s0 = g.main.project(start)[0]
    player = _state_pos(run.get("player")) or start
    positions = [player]
    for i, state in enumerate(spawn):
        p = _state_pos(state)
        if p is None:
            return False, f"rival {i} has no position"
        s_rel, lat, _t = g.main.project(p)
        behind = (s0 - s_rel) % g.main.length
        if abs(lat) > g.main.width / 2 - 0.5:
            return (
                False,
                f"rival {i} spawned off the road ({lat:+.1f} m from the centreline)",
            )
        if min(behind, g.main.length - behind) > 60:
            return (
                False,
                f"rival {i} spawned {behind:.0f} m of road away from the start",
            )
        positions.append(p)
    for i in range(len(positions)):
        for j in range(i + 1, len(positions)):
            if _norm(_sub(positions[i], positions[j])) < 2.5:
                return False, "two cars spawned on top of each other"
    return (
        True,
        "3 rivals on the grid, on the road, apart from each other and the player",
    )


def check_rivals_drive(g: Grade) -> Result:
    run = _rival_run(g, "rivals_main")
    traces = _rival_traces(run)
    if len(traces) < g.probe["rivals"]:
        return False, f"rival_states() tracked {len(traces)} rivals"
    w = g.main.width / 2
    for i, trace in enumerate(traces[: g.probe["rivals"]]):
        passes = g.main.progress(trace)["passes"]
        if len(passes) < 3:
            return (
                False,
                f"rival {i} passed {len(passes)} checkpoints in 60 s, expected at least 3",
            )
        on = sum(1 for _t, p in trace if abs(g.main.lateral(p)) <= w + 0.5)
        if on < 0.9 * len(trace):
            return False, f"rival {i} was on the road in only {on}/{len(trace)} samples"
    return True, "every rival drove the track by itself, on the road"


def check_rivals_finish_heldout(g: Grade) -> Result:
    run = _rival_run(g, "rivals_heldout")
    if run.get("loaded") is not True:
        return False, "load_track() refused the held-out track"
    traces = _rival_traces(run)
    if len(traces) < g.probe["rivals"]:
        return (
            False,
            f"rival_states() tracked {len(traces)} rivals on the held-out track",
        )
    limit = g.probe["heldout_limit_ticks"] * TICK
    for i, trace in enumerate(traces[: g.probe["rivals"]]):
        laps = g.heldout.progress(trace)["laps"]
        if not laps:
            passes = len(g.heldout.progress(trace)["passes"])
            return (
                False,
                f"rival {i} did not finish a lap of an unseen track in {limit:.0f} s (passed {passes} gates)",
            )
    return (
        True,
        f"all rivals finished a lap of an unseen track past a stopped car within {limit:.0f} s",
    )


def check_rivals_avoid_stopped(g: Grade) -> Result:
    """The player's car, stopped in the middle of the held-out road, is driven
    around, never shoved out of the way: ramming through it is passing
    through it by other means."""
    run = _rival_run(g, "rivals_heldout")
    start = _state_pos(run.get("player"))
    if start is None:
        return False, "no position for the stopped car"
    if abs(g.heldout.lateral(start)) > 2.0:
        return False, "the lab could not stop the car in the middle of the road"
    samples = run.get("samples") or []
    if len(samples) < 100:
        return False, "the held-out race was not harvested"
    pushed = 0.0
    for row in samples:
        p = row[1]
        if (
            isinstance(p, list)
            and len(p) >= 3
            and all(_num(v) is not None for v in p[:3])
        ):
            pushed = max(pushed, _norm(_sub((p[0], p[2]), start)))
    passes = [
        len(g.heldout.progress(t)["passes"])
        for t in _rival_traces(run)[: g.probe["rivals"]]
    ]
    if not passes or min(passes) < 3:
        return (
            False,
            f"the rivals did not reach the stopped car (gates passed: {passes})",
        )
    if pushed > 1.5:
        return (
            False,
            f"the rivals shoved the stopped car {pushed:.1f} m instead of driving around it",
        )
    return (
        True,
        f"the rivals drove around a car stopped in the road (it moved {pushed:.2f} m)",
    )


def check_rivals_no_overlap(g: Grade) -> Result:
    worst = float("inf")
    count = 0
    for key in ("rivals_main", "rivals_heldout"):
        run = _rival_run(g, key)
        for row in run.get("samples") or []:
            cars = [row[1], *row[2]]
            pts = [
                (c[0], c[2])
                for c in cars
                if isinstance(c, list)
                and len(c) >= 3
                and all(_num(v) is not None for v in c[:3])
            ]
            for i in range(len(pts)):
                for j in range(i + 1, len(pts)):
                    worst = min(worst, _norm(_sub(pts[i], pts[j])))
            count += 1
    if count < 100:
        return False, "the races with rivals were not harvested"
    if worst <= 1.0:
        return (
            False,
            f"two cars came within {worst:.2f} m of each other's centre (they pass through each other)",
        )
    return True, f"cars never closer than {worst:.1f} m centre to centre"


# Scene checks ---------------------------------------------------------------------


def _scene_ready(g: Grade) -> None:
    if not g.scene:
        raise Unmeasured("the scene was not harvested")


def check_scene_loads(g: Grade) -> Result:
    s = g.scene
    if not s.get("main_scene"):
        return False, "no main scene set in project.godot"
    if not s.get("instanced"):
        return False, f"the main scene {s.get('main_scene')!r} did not load"
    if not s.get("has_camera"):
        return False, "no current Camera3D"
    if s.get("player_cars") != 1:
        return False, f"{s.get('player_cars')!r} nodes in group player_car, expected 1"
    if not s.get("track_nodes"):
        return False, "no node in group track"
    start, _h = _start(g.stadium)
    pos = _state_pos(s.get("start_state"))
    if pos is None or _norm(_sub(pos, start)) > 1.5:
        return False, "the scene did not start the race on the track given with --track"
    node = s.get("player_node")
    if not isinstance(node, list) or _norm(_sub((node[0], node[2]), pos)) > 1.0:
        return False, "the player_car node is not where car_state() says the car is"
    return (
        True,
        "main scene with a camera, the player's car and the track given with --track",
    )


def check_default_launch(g: Grade) -> Result:
    s = g.launch
    if not s:
        raise Unmeasured("the plain launch was not harvested")
    if not s.get("instanced"):
        return False, "the main scene did not load without arguments"
    project = project_dir(g.workspace)
    try:
        data = json.loads((project / "tracks" / "default.json").read_text())
        sp = data["start"]["position"]
        start = (float(sp[0]), float(sp[2]))
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return False, "no readable res://tracks/default.json to start on"
    pos = _state_pos(s.get("state"))
    if pos is None or _norm(_sub(pos, start)) > 1.5:
        return (
            False,
            "launched without arguments, the car is not on the default track's start",
        )
    rivals = s.get("rivals")
    if not isinstance(rivals, list) or len(rivals) != 3:
        return (
            False,
            f"launched without arguments there are {len(rivals) if isinstance(rivals, list) else rivals!r} rivals, expected 3",
        )
    if s.get("rival_nodes") != 3:
        return (
            False,
            f"{s.get('rival_nodes')!r} visible nodes with a mesh in group rival_cars, expected 3",
        )
    later = s.get("rivals_later")
    if not isinstance(later, list) or len(later) != 3:
        return False, "rival_states() stopped answering while the game ran"
    for i, (a, b) in enumerate(zip(rivals, later, strict=True)):
        pa, pb = _state_pos(a), _state_pos(b)
        if pa is None or pb is None or _norm(_sub(pa, pb)) < 3.0:
            return False, f"rival {i} did not drive in the first 5 s of a plain launch"
    return (
        True,
        "a plain launch races on res://tracks/default.json against 3 visible rivals that drive",
    )


def _scene_drive(g: Grade) -> list[dict[str, Any]]:
    rows = g.scene.get("drive") or []
    if len(rows) < 5:
        raise AssertionError("the keyboard drive in the scene was not harvested")
    return rows


def check_camera_follows(g: Grade) -> Result:
    rows = _scene_drive(g)
    size = g.scene.get("viewport") or [0, 0]
    first, last = rows[0], rows[-1]
    for r in rows:
        cam, car = r.get("camera"), r.get("car")
        if not (isinstance(cam, list) and isinstance(car, list)):
            return False, "no camera or car position while driving"
        d = math.dist(cam, car)
        if not 2.0 <= d <= 30.0:
            return False, f"the camera is {d:.1f} m from the car"
    moved_car = math.dist(first["car"], last["car"])
    moved_cam = math.dist(first["camera"], last["camera"])
    if moved_car < 5:
        return (
            False,
            "the car did not move in the scene, so the camera cannot be judged",
        )
    if moved_cam < 0.5 * moved_car:
        return False, f"the car moved {moved_car:.0f} m, the camera {moved_cam:.0f} m"
    ahead = [r.get("ahead_screen") for r in rows[len(rows) // 2 :]]
    inside = sum(
        1
        for p in ahead
        if isinstance(p, list) and 0 <= p[0] <= size[0] and 0 <= p[1] <= size[1]
    )
    if inside < 0.8 * len(ahead):
        return False, "the camera does not look along the road ahead of the car"
    return (
        True,
        f"a chase camera {math.dist(last['camera'], last['car']):.1f} m from the car, looking down the road",
    )


def check_car_visible(g: Grade) -> Result:
    rows = _scene_drive(g)
    size = g.scene.get("viewport") or [0, 0]
    for r in rows:
        p = r.get("car_screen")
        if not (isinstance(p, list) and 0 <= p[0] <= size[0] and 0 <= p[1] <= size[1]):
            return False, f"the player car is off screen ({p!r}) while driving"
    return True, "the player car stays on screen while it drives"


def check_hud(g: Grade) -> Result:
    texts = [r.get("hud") for r in _scene_drive(g)]
    if not all(isinstance(t, str) for t in texts):
        return False, "no visible Label in group race_hud"
    lowered = [t.lower() for t in texts]
    if not all("lap" in t and re.search(r"\d", t) for t in lowered):
        return False, f"the HUD does not show the lap: {texts[0]!r}"
    if len(set(texts)) < 3:
        return False, "the HUD text does not change while the car drives (time, speed)"
    return True, f"the HUD reads {texts[-1]!r}"


def check_keyboard_drive(g: Grade) -> Result:
    kb = g.scene.get("keyboard") or {}
    speed = _num((kb.get("accelerated") or {}).get("speed"))
    if speed is None or speed <= 5.0:
        return False, f"2 s of the accelerate action left the car at {speed!r} m/s"
    for key, sign, word in (("left", 1, "steer_left"), ("right", -1, "steer_right")):
        turned = _num(kb.get(f"turn_{key}"))
        if turned is None or sign * turned < 10:
            return False, f"{word} turned the car {turned!r} degrees (left positive)"
    braked = _num((kb.get("braked") or {}).get("speed"))
    before = _num((kb.get("before_brake") or {}).get("speed"))
    if braked is None or before is None or braked > 0.5 * before:
        return (
            False,
            f"the brake action took the car from {before!r} to {braked!r} m/s in 1.5 s",
        )
    return True, "the keyboard actions accelerate, steer both ways and brake the car"


def check_keyboard_reset(g: Grade) -> Result:
    kb = g.scene.get("keyboard") or {}
    before = kb.get("before_reset") or {}
    after = kb.get("after_reset") or {}
    p0, p1 = _state_pos(before), _state_pos(after)
    if p0 is None or p1 is None:
        return False, "no car state around the reset_car action"
    target, _t = _gate_or_start(g, before.get("checkpoint"), g.stadium)
    if _norm(_sub(p0, target)) < 8:
        return (
            False,
            "the car was still at its respawn point before reset_car (nothing to measure)",
        )
    if _norm(_sub(p1, target)) > 6.0:
        return (
            False,
            f"after reset_car the car is {_norm(_sub(p1, target)):.1f} m from its last checkpoint",
        )
    speed = _num(after.get("speed"))
    if speed is None or speed > 1.0:
        return False, f"after reset_car the car moves at {after.get('speed')!r} m/s"
    return True, "reset_car puts the car back at its last checkpoint"


def check_render(g: Grade) -> Result:
    stats = g.scene.get("image") or {}
    if not stats:
        return False, "no frame rendered"
    ok = stats.get("variance", 0) > 0.002 and stats.get("distinct", 0) >= 12
    return (
        ok,
        f"rendered frame variance {stats.get('variance', 0):.4f}, {stats.get('distinct', 0)} colours",
    )


def check_frame_time(g: Grade) -> Result:
    times = sorted(g.scene.get("frame_ms") or [])
    if len(times) < 10:
        return False, "no frame timings"
    p95 = times[int(0.95 * (len(times) - 1))]
    # Software rendering on a CPU (ADR-0073): a loose ceiling that a sane
    # scene meets and a runaway one does not.
    return p95 < 250, f"p95 frame {p95:.0f} ms on a CPU renderer"


CHECKS = {
    "contract": check_contract,
    "own_code": check_own_code,
    "renderer": check_renderer,
    "load_track": check_load_track,
    "track_built": check_track_built,
    "accelerates": check_accelerates,
    "brakes": check_brakes,
    "steers_left": check_steers_left,
    "steers_right": check_steers_right,
    "reverse_or_stop": check_reverse_or_stop,
    "heading": check_heading,
    "stays_on_ground": check_stays_on_ground,
    "wall_blocks": check_wall_blocks,
    "determinism": check_determinism,
    "bad_track": check_bad_track,
    "checkpoint_order": check_checkpoint_order,
    "lap_counts": check_lap_counts,
    "lap_time_consistent": check_lap_time_consistent,
    "cut_not_counted": check_cut_not_counted,
    "wrong_way": check_wrong_way,
    "on_track": check_on_track,
    "respawn": check_respawn,
    "upside_down": check_upside_down,
    "input_actions": check_input_actions,
    "default_track": check_default_track,
    "scene_loads": check_scene_loads,
    "default_launch": check_default_launch,
    "camera_follows": check_camera_follows,
    "car_visible": check_car_visible,
    "hud": check_hud,
    "keyboard_drive": check_keyboard_drive,
    "keyboard_reset": check_keyboard_reset,
    "render": check_render,
    "frame_time": check_frame_time,
    "rivals_spawn": check_rivals_spawn,
    "rivals_drive": check_rivals_drive,
    "rivals_no_overlap": check_rivals_no_overlap,
    "rivals_avoid_stopped": check_rivals_avoid_stopped,
    "rivals_finish_heldout": check_rivals_finish_heldout,
}
SCENE_CHECKS = {
    "scene_loads",
    "camera_follows",
    "car_visible",
    "hud",
    "keyboard_drive",
    "keyboard_reset",
    "render",
    "frame_time",
}


def run_check(name: str, workspace: Path) -> int:
    try:
        g = Grade(workspace)
        if not g.meta.get("project"):
            # Nothing to grade is the agent's result, for every check alike.
            print(f"FAIL {name}: no project.godot in the workspace")
            return 1
        if name in SCENE_CHECKS:
            _scene_ready(g)
        passed, detail = CHECKS[name](g)
    except AssertionError as exc:
        print(f"FAIL {name}: {exc}")
        return 1
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
    p.add_argument("out_dir", type=Path)
    args = parser.parse_args(argv)
    if args.cmd == "harvest":
        return harvest(args.workspace.resolve(), args.run_id)
    if args.cmd == "probe":
        probe = build_probe(args.run_id, args.out_dir.resolve())
        print(
            json.dumps({k: v for k, v in probe.items() if k != "paths"}, indent=1)[
                :4000
            ]
        )
        return 0
    return run_check(args.name, args.workspace.resolve())


if __name__ == "__main__":
    sys.exit(main())
