"""The grade for chess3d-v1 (ADR-0078): probe, harvest, then one verdict per check.

Two commands share this file, and the case's ``task.yaml`` runs them in order:

``python grade.py harvest <workspace> <run_id>``
    Generates this run's probes, copies the agent's project to a scratch
    directory, runs the GDScript harvester in it twice (rules headless, scene
    on a software renderer) and stores the raw answers under
    ``<workspace>/.lab-grade/``. It judges nothing: it exits 1 only when there
    is no Godot project at all (the case's entry-point check) and 0 otherwise,
    so a broken game shows up as failed *checks*, never as a failed harvest.

``python grade.py check <name> <workspace> <run_id>``
    Reads those answers and decides one check against python-chess, an engine
    the agent did not write. Exit 0 passes, 1 fails, 2 means the lab could not
    measure (no harvest), which the validation lane records as inconclusive.

Why probes are generated per run, not stored: the agent's container sees the
lab's git history, so a committed list of positions is one ``git show`` away.
Positions reached by seeded random play, and puzzles found by searching them,
cannot be read from anywhere before the run starts.

Why the answers live in the workspace: per-step reward points every command at
each step's snapshot by rewriting the workspace path, so each snapshot gets its
own harvest and its own verdicts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import chess

GRADER_DIR = Path(__file__).resolve().parent
GRADE_DIR = ".lab-grade"
START_FEN = chess.STARTING_FEN

# Perft from fixed positions: the standard suite every chess programmer tests
# against. Public on purpose — they are hard because every rule must be right,
# not because they are secret. Per-run random positions are added on top.
PERFT_FIXED = [
    # (fen, depth, nodes)
    (START_FEN, 1, 20),
    (START_FEN, 2, 400),
    (START_FEN, 3, 8902),
    ("r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1", 1, 48),
    ("r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1", 2, 2039),
    ("8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1", 3, 2812),
    ("r3k2r/Pppp1ppp/1b3nbN/nP6/BBP1P3/q4N2/Pp1P2PP/R2Q1RK1 w kq - 0 1", 3, 9467),
    ("rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8", 3, 62379),
    ("r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1", 3, 97862),
    (START_FEN, 4, 197281),
]

# Positions that each isolate one rule a careless engine gets wrong.
TRAPS = [
    # En passant available, and en passant that would expose the king (illegal).
    "rnbqkbnr/ppp1p1pp/8/3pPp2/8/8/PPPP1PPP/RNBQKBNR w KQkq f6 0 3",
    "8/8/8/K2pP2r/8/8/8/7k w - d6 0 1",
    # Castling through an attacked square is illegal; out of check too.
    "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1",
    "r3k2r/8/8/8/8/5q2/8/R3K2R w KQkq - 0 1",
    "4k3/8/8/8/8/8/4r3/R3K2R w KQ - 0 1",
    # A pinned piece may only move along the pin.
    "4k3/4r3/8/8/8/8/4R3/4K3 w - - 0 1",
    # Double check: only the king moves.
    "4k3/8/8/8/1b6/8/4r3/3QK3 w - - 0 1",
    # Under-promotion, with and without capture.
    "1r5k/P7/8/8/8/8/8/7K w - - 0 1",
    # Checkmate and stalemate.
    "R5k1/5ppp/8/8/8/8/8/6K1 b - - 0 1",
    "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1",
]

# Ends of game the status contract must report.
STATUS_FENS = {
    "checkmate": "R5k1/5ppp/8/8/8/8/8/6K1 b - - 0 1",
    "stalemate": "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1",
    "insufficient_material": "8/8/4k3/8/8/3K4/8/8 w - - 0 1",
    "check": "4k3/8/8/8/8/8/4R3/4K3 b - - 0 1",
}

# The status words the contract allows, from python-chess's view of a board.
STATUS_WORDS = (
    "ongoing",
    "check",
    "checkmate",
    "stalemate",
    "insufficient_material",
    "fifty_moves",
    "threefold_repetition",
)


# --- Probes ----------------------------------------------------------------------


def seed_for(run_id: str) -> int:
    return int.from_bytes(
        hashlib.sha256(f"chess3d-v1:{run_id}".encode()).digest()[:8], "big"
    )


def random_position(rng: random.Random, plies: int) -> chess.Board:
    """A position reached by legal random play, re-drawn if the game ended."""
    while True:
        board = chess.Board()
        for _ in range(plies):
            moves = list(board.legal_moves)
            if not moves:
                break
            board.push(rng.choice(moves))
        if not board.is_game_over():
            return board


def random_game(rng: random.Random, max_plies: int) -> tuple[str, list[str]]:
    board = chess.Board()
    played: list[str] = []
    for _ in range(max_plies):
        moves = list(board.legal_moves)
        if not moves or board.is_game_over(claim_draw=False):
            break
        # Prefer captures, checks and promotions now and then, so SAN suffixes,
        # disambiguation and promotions all appear in a short game.
        spicy = [
            m
            for m in moves
            if board.is_capture(m) or board.gives_check(m) or m.promotion
        ]
        move = rng.choice(spicy) if spicy and rng.random() < 0.35 else rng.choice(moves)
        played.append(move.uci())
        board.push(move)
    return START_FEN, played


# SAN with a disambiguator: piece letter, then a file and/or rank before the
# destination (`Nbd2`, `R1e2`, `Qh4xe1`, `Raxd1+`).
_DISAMBIGUATED_SAN = re.compile(r"^[NBRQK][a-h1-8]{1,2}x?[a-h][1-8]")


def ambiguous_position(rng: random.Random) -> chess.Board:
    """A position where SAN needs a file or rank to tell two pieces apart."""
    for _ in range(4000):
        board = random_position(rng, rng.randint(16, 60))
        if any(_DISAMBIGUATED_SAN.match(board.san(m)) for m in board.legal_moves):
            return board
    # Fixed fallback: knights on b1 and f1 both reach d2.
    return chess.Board("4k3/8/8/8/8/8/8/1N2KN2 w - - 0 1")


def mate_in_one(rng: random.Random) -> chess.Board | None:
    for _ in range(20000):
        board = random_position(rng, rng.randint(20, 80))
        mates = [m for m in board.legal_moves if _is_mate_after(board, m)]
        if len(mates) == 1:
            return board
    return None


def _is_mate_after(board: chess.Board, move: chess.Move) -> bool:
    board.push(move)
    mate = board.is_checkmate()
    board.pop()
    return mate


def forces_mate_in_two(board: chess.Board, move: chess.Move) -> bool:
    """After ``move``, every defence allows a mate in one."""
    board.push(move)
    try:
        if board.is_game_over():
            return board.is_checkmate()
        for reply in list(board.legal_moves):
            board.push(reply)
            found = any(_is_mate_after(board, m) for m in board.legal_moves)
            board.pop()
            if not found:
                return False
        return True
    finally:
        board.pop()


def mate_in_two(rng: random.Random) -> chess.Board | None:
    """A position with a forced mate in two and no mate in one."""
    for _ in range(3000):
        board = random_position(rng, rng.randint(24, 90))
        if len(board.piece_map()) > 18 or any(
            _is_mate_after(board, m) for m in board.legal_moves
        ):
            continue
        if any(
            forces_mate_in_two(board, m)
            for m in board.legal_moves
            if board.gives_check(m)
        ):
            return board
    return None


def build_probe(run_id: str) -> dict[str, Any]:
    rng = random.Random(seed_for(run_id))
    random_positions = [random_position(rng, rng.randint(8, 70)) for _ in range(10)]
    perft = [{"fen": fen, "depth": depth} for fen, depth, _nodes in PERFT_FIXED]
    for board in random_positions[:3]:
        perft.append({"fen": board.fen(), "depth": 3})

    games = [
        dict(zip(("fen", "uci"), random_game(rng, 120), strict=True)) for _ in range(4)
    ]
    # A repetition and a fifty-move ending, reached by play rather than by FEN.
    games.append(
        {
            "fen": START_FEN,
            "uci": ["g1f3", "g8f6", "f3g1", "f6g8", "g1f3", "g8f6", "f3g1", "f6g8"],
        }
    )
    games.append({"fen": "8/8/4k3/8/8/3K4/8/R7 w - - 99 120", "uci": ["a1a2"]})

    san_positions = []
    for board in [ambiguous_position(rng), random_positions[3], chess.Board(TRAPS[7])]:
        san_positions.append(
            {"fen": board.fen(), "uci": [m.uci() for m in board.legal_moves]}
        )

    san_input = []
    for board in random_positions[4:8]:
        move = rng.choice(list(board.legal_moves))
        san_input.append({"fen": board.fen(), "san": board.san(move)})

    illegal = [
        {"fen": START_FEN, "move": "e2e5"},
        {"fen": START_FEN, "move": "e1g1"},
        {"fen": TRAPS[1], "move": "e5d6"},
        {"fen": TRAPS[3], "move": "e1g1"},
        {"fen": TRAPS[5], "move": "e2d2"},
        {"fen": START_FEN, "move": "not-a-move"},
    ]
    undo = [
        {"fen": TRAPS[0], "uci": ["e5f6", "e8f7", "f6g7"]},
        {"fen": TRAPS[2], "uci": ["e1g1", "e8c8"]},
        {"fen": TRAPS[7], "uci": ["a7b8n", "h8g7"]},
    ]

    ai: list[dict[str, Any]] = [
        {"kind": "legal", "fen": b.fen()} for b in random_positions[8:10]
    ]
    for kind, finder, count in (
        ("mate_in_1", mate_in_one, 3),
        ("mate_in_2", mate_in_two, 2),
    ):
        for _ in range(count):
            board = finder(rng)
            if board is not None:
                ai.append({"kind": kind, "fen": board.fen()})

    return {
        "case": "chess3d-v1",
        "run_id": run_id,
        "start_fen": START_FEN,
        "legal_positions": TRAPS + [b.fen() for b in random_positions],
        "perft": perft,
        "games": games,
        "san_positions": san_positions,
        "san_input": san_input,
        "illegal": illegal,
        "undo": undo,
        "ai": ai,
        "ai_budget_ms": 5000,
        # Scene probes.
        # Empty in the start position, three of each shade: d4 e5 c3 dark,
        # e4 d5 f3 light. Sampled before any move, so no piece covers them.
        "color_squares": ["d4", "e5", "c3", "e4", "d5", "f3"],
        "click_line": [
            ["e2", "e4"],
            ["d7", "d5"],
            ["e4", "e5"],
            ["f7", "f5"],
            ["e5", "f6"],
            ["g8", "f6"],
            ["g1", "f3"],
            ["e7", "e6"],
            ["f1", "e2"],
            ["f8", "e7"],
            ["e1", "g1"],
            ["e8", "g8"],
        ],
        "illegal_click": ["d1", "d5"],
        "follow": {"fen": TRAPS[7], "promotion_click": ["a7", "a8"]},
        "status_positions": [
            {"kind": k, "fen": v}
            for k, v in STATUS_FENS.items()
            if k != "insufficient_material"
        ],
        "ai_click": ["e2", "e4"],
        "ai_scene_wait_ms": 15000,
    }


# --- Harvest ---------------------------------------------------------------------

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


def harvest(workspace: Path, run_id: str) -> int:
    grade = workspace / GRADE_DIR
    if grade.exists():
        shutil.rmtree(grade)
    grade.mkdir(parents=True)
    probe = build_probe(run_id)
    (grade / "probe.json").write_text(json.dumps(probe, indent=1))
    meta: dict[str, Any] = {"run_id": run_id, "seed": seed_for(run_id)}
    project = project_dir(workspace)
    meta["project"] = str(project.relative_to(workspace)) if project else None
    if project is None:
        (grade / "meta.json").write_text(json.dumps(meta, indent=1))
        # The entry-point check, like `test -f index.html` in todo-web-v1: no
        # project is the agent's failure, and every check after this says so.
        print("harvest: no project.godot in the workspace")
        return 1

    with tempfile.TemporaryDirectory(prefix="chess3d-") as scratch:
        copy = Path(scratch) / "project"
        shutil.copytree(
            project, copy, ignore=shutil.ignore_patterns(".godot", GRADE_DIR, ".git")
        )
        (copy / "__lab_grader__").mkdir(exist_ok=True)
        shutil.copy2(GRADER_DIR / "harvest.gd", copy / "__lab_grader__" / "harvest.gd")
        shots = grade / "shots"
        shots.mkdir()
        script = ["--script", "res://__lab_grader__/harvest.gd"]

        code, log = _godot(
            ["godot", "--headless", "--path", str(copy), "--import"], 300
        )
        meta["import"] = {"exit": code}
        (grade / "import.log").write_text(log)

        code, log = _godot(
            [
                "godot",
                "--headless",
                "--path",
                str(copy),
                *script,
                "--",
                "--probe",
                str(grade / "probe.json"),
                "--out",
                str(grade / "logic.json"),
                "--phase",
                "logic",
            ],
            420,
        )
        meta["logic"] = {"exit": code}
        (grade / "logic.log").write_text(log)

        code, log = _godot(
            [
                "godot-render",
                "--path",
                str(copy),
                "--resolution",
                "1280x720",
                *script,
                "--",
                "--probe",
                str(grade / "probe.json"),
                "--out",
                str(grade / "scene.json"),
                "--phase",
                "scene",
                "--shots",
                str(shots),
            ],
            300,
        )
        meta["scene"] = {"exit": code}
        (grade / "scene.log").write_text(log)
    (grade / "meta.json").write_text(json.dumps(meta, indent=1))
    print(
        f"harvest: logic exit {meta['logic']['exit']}, scene exit {meta['scene']['exit']}"
    )
    return 0


# --- Checks ----------------------------------------------------------------------


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

    def _load(self, name: str) -> dict[str, Any] | None:
        path = self.dir / name
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text())
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None


def _fen_key(fen: Any) -> str:
    """The four fields that define a position; clocks are judged separately."""
    return " ".join(str(fen).split()[:4]) if isinstance(fen, str) else ""


def _expected_status(board: chess.Board) -> str:
    if board.is_checkmate():
        return "checkmate"
    if board.is_stalemate():
        return "stalemate"
    if board.is_insufficient_material():
        return "insufficient_material"
    if board.is_fifty_moves():
        return "fifty_moves"
    if board.is_repetition(3):
        return "threefold_repetition"
    if board.is_check():
        return "check"
    return "ongoing"


def _expected_result(board: chess.Board) -> str:
    status = _expected_status(board)
    if status == "checkmate":
        return "0-1" if board.turn == chess.WHITE else "1-0"
    if status in ("ongoing", "check"):
        return "*"
    return "1/2-1/2"


def _replay(fen: str, ucis: list[str]) -> list[chess.Board]:
    board = chess.Board(fen)
    boards = []
    for uci in ucis:
        board.push_uci(uci)
        boards.append(board.copy())
    return boards


Result = tuple[bool, str]


def check_contract(g: Grade) -> Result:
    if not g.meta.get("project"):
        return False, "no project.godot in the workspace"
    if not g.logic.get("has_chess_autoload"):
        return False, "no autoload named Chess"
    required = {
        "load_fen",
        "fen",
        "legal_moves",
        "play",
        "undo",
        "status",
        "result",
        "history_san",
        "perft",
        "best_move",
    }
    missing = sorted(required & set(g.logic.get("missing", [])))
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
    ok = 'rendering_method="gl_compatibility"' in text
    return (
        ok,
        "Compatibility renderer"
        if ok
        else 'renderer/rendering_method must be "gl_compatibility"',
    )


def check_fen_roundtrip(g: Grade) -> Result:
    got = g.logic.get("start_fen")
    return got == START_FEN, f"fen() after load_fen(start) = {got!r}"


def _legal_rows(g: Grade, fens: list[str]) -> Result:
    rows = {row.get("fen"): row for row in g.logic.get("legal", [])}
    for fen in fens:
        row = rows.get(fen)
        if row is None or row.get("moves") is None:
            return False, f"no legal_moves for {fen}"
        expected = {m.uci() for m in chess.Board(fen).legal_moves}
        got = set(row["moves"])
        if got != expected:
            return (
                False,
                f"{fen}: missing {sorted(expected - got)[:6]}, extra {sorted(got - expected)[:6]}",
            )
    return True, f"{len(fens)} positions exact"


def check_legal_basic(g: Grade) -> Result:
    # The per-run random positions: ordinary middlegames nobody could prepare for.
    return _legal_rows(g, g.probe["legal_positions"][len(TRAPS) :])


def check_en_passant(g: Grade) -> Result:
    return _legal_rows(g, [TRAPS[0], TRAPS[1]])


def check_castling(g: Grade) -> Result:
    return _legal_rows(g, [TRAPS[2], TRAPS[3], TRAPS[4]])


def check_pins_and_checks(g: Grade) -> Result:
    return _legal_rows(g, [TRAPS[5], TRAPS[6]])


def check_promotion(g: Grade) -> Result:
    return _legal_rows(g, [TRAPS[7]])


def _perft_rows(g: Grade, depth_limit: int, fixed_only: bool) -> Result:
    rows = g.logic.get("perft") or []
    wanted = [p for p in g.probe["perft"] if p["depth"] <= depth_limit]
    if fixed_only:
        fixed = {(f, d) for f, d, _n in PERFT_FIXED}
        wanted = [p for p in wanted if (p["fen"], p["depth"]) in fixed]
    else:
        fixed = {(f, d) for f, d, _n in PERFT_FIXED}
        wanted = [p for p in wanted if (p["fen"], p["depth"]) not in fixed]
    by_key = {(r.get("fen"), r.get("depth")): r for r in rows}
    for item in wanted:
        row = by_key.get((item["fen"], item["depth"]))
        if row is None:
            return (
                False,
                f"perft({item['depth']}) not answered for {item['fen']} (too slow or crashed)",
            )
        expected = _perft(chess.Board(item["fen"]), item["depth"])
        if row.get("nodes") != expected:
            return (
                False,
                f"perft({item['depth']}) of {item['fen']} = {row.get('nodes')}, expected {expected}",
            )
    return True, f"{len(wanted)} perft counts exact"


_PERFT_CACHE: dict[tuple[str, int], int] = {}


def _perft(board: chess.Board, depth: int) -> int:
    key = (board.fen(), depth)
    if key in _PERFT_CACHE:
        return _PERFT_CACHE[key]
    for fen, d, nodes in PERFT_FIXED:
        if (fen, d) == key:
            return nodes
    if depth == 0:
        return 1
    total = 0
    for move in board.legal_moves:
        board.push(move)
        total += _perft(board, depth - 1)
        board.pop()
    _PERFT_CACHE[key] = total
    return total


def check_perft_shallow(g: Grade) -> Result:
    return _perft_rows(g, 2, fixed_only=True)


def check_perft_deep(g: Grade) -> Result:
    return _perft_rows(g, 4, fixed_only=True)


def check_perft_random(g: Grade) -> Result:
    return _perft_rows(g, 3, fixed_only=False)


def _games(g: Grade) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    rows = g.logic.get("games") or []
    return list(
        zip(
            g.probe["games"],
            rows + [{}] * (len(g.probe["games"]) - len(rows)),
            strict=True,
        )
    )


def check_fen_tracking(g: Grade) -> Result:
    for spec, row in _games(g)[:4]:
        boards = _replay(spec["fen"], spec["uci"])
        fens = row.get("fens") or []
        for i, board in enumerate(boards):
            got = fens[i] if i < len(fens) else None
            if _fen_key(got) != _fen_key(board.fen()):
                return (
                    False,
                    f"after {spec['uci'][: i + 1][-3:]}: fen {got!r}, expected {board.fen()!r}",
                )
    return True, "position fields exact after every move of 4 random games"


def check_clocks(g: Grade) -> Result:
    for spec, row in _games(g)[:4]:
        boards = _replay(spec["fen"], spec["uci"])
        fens = row.get("fens") or []
        for i, board in enumerate(boards):
            got = fens[i] if i < len(fens) else None
            if got != board.fen():
                return (
                    False,
                    f"full FEN after move {i + 1}: {got!r}, expected {board.fen()!r}",
                )
    return True, "halfmove and fullmove clocks exact"


def check_san_output(g: Grade) -> Result:
    for spec, row in _games(g)[:4]:
        board = chess.Board(spec["fen"])
        sans = row.get("sans") or []
        for i, uci in enumerate(spec["uci"]):
            move = chess.Move.from_uci(uci)
            expected = board.san(move)
            got = sans[i] if i < len(sans) else None
            if got != expected:
                return False, f"play({uci}) returned {got!r}, expected {expected!r}"
            board.push(move)
    return True, "SAN exact for every move of 4 random games"


def check_san_disambiguation(g: Grade) -> Result:
    rows = g.logic.get("san_all") or []
    for spec, row in zip(
        g.probe["san_positions"],
        rows + [{}] * len(g.probe["san_positions"]),
        strict=False,
    ):
        board = chess.Board(spec["fen"])
        sans = row.get("sans") or []
        for i, uci in enumerate(spec["uci"]):
            expected = board.san(chess.Move.from_uci(uci))
            got = sans[i] if i < len(sans) else None
            if got != expected:
                return (
                    False,
                    f"{spec['fen']}: {uci} gave {got!r}, expected {expected!r}",
                )
    return True, "SAN exact for every legal move of crowded positions"


def check_san_input(g: Grade) -> Result:
    rows = g.logic.get("san_input") or []
    for spec, row in zip(
        g.probe["san_input"], rows + [{}] * len(g.probe["san_input"]), strict=False
    ):
        board = chess.Board(spec["fen"])
        board.push_san(spec["san"])
        if _fen_key(row.get("fen")) != _fen_key(board.fen()):
            return False, f"play({spec['san']!r}) left {row.get('fen')!r}"
    return True, "SAN accepted as input"


def check_illegal_refused(g: Grade) -> Result:
    rows = g.logic.get("illegal") or []
    for spec, row in zip(
        g.probe["illegal"], rows + [{}] * len(g.probe["illegal"]), strict=False
    ):
        # Against the normalised position, not the probe's text: a FEN may name
        # an en passant square no capture can use, which a FEN writer drops.
        unchanged = _fen_key(chess.Board(spec["fen"]).fen())
        if row.get("played") not in ("", None) or _fen_key(row.get("fen")) != unchanged:
            return (
                False,
                f"{spec['move']} in {spec['fen']} was accepted or changed the position",
            )
    return len(rows) == len(g.probe["illegal"]), "every illegal move refused"


def check_undo(g: Grade) -> Result:
    rows = g.logic.get("undo") or []
    for spec, row in zip(
        g.probe["undo"], rows + [{}] * len(g.probe["undo"]), strict=False
    ):
        if row.get("fen") != chess.Board(spec["fen"]).fen():
            return False, f"undo after {spec['uci']} left {row.get('fen')!r}"
    return len(rows) == len(g.probe["undo"]), "undo restores the exact position"


def _status_rows(g: Grade) -> dict[str, str | None]:
    return {row.get("fen"): row.get("status") for row in g.logic.get("legal", [])}


def check_mate_and_stalemate(g: Grade) -> Result:
    statuses = _status_rows(g)
    for fen in (TRAPS[8], TRAPS[9]):
        expected = _expected_status(chess.Board(fen))
        if statuses.get(fen) != expected:
            return (
                False,
                f"status() of {fen} = {statuses.get(fen)!r}, expected {expected!r}",
            )
    return True, "checkmate and stalemate detected"


def check_draw_rules(g: Grade) -> Result:
    games = _games(g)
    repetition_spec, repetition = games[4]
    fifty_spec, fifty = games[5]
    expected_rep = _expected_status(
        _replay(repetition_spec["fen"], repetition_spec["uci"])[-1]
    )
    expected_fifty = _expected_status(_replay(fifty_spec["fen"], fifty_spec["uci"])[-1])
    if repetition.get("status") != expected_rep:
        return (
            False,
            f"after a threefold repetition status() = {repetition.get('status')!r}",
        )
    if fifty.get("status") != expected_fifty:
        return (
            False,
            f"after 50 moves without capture or pawn move status() = {fifty.get('status')!r}",
        )
    if repetition.get("result") != "1/2-1/2" or fifty.get("result") != "1/2-1/2":
        return False, "a drawn game must report result 1/2-1/2"
    return True, "threefold repetition and fifty-move rule"


def check_status_every_position(g: Grade) -> Result:
    statuses = _status_rows(g)
    for fen in g.probe["legal_positions"]:
        expected = _expected_status(chess.Board(fen))
        if statuses.get(fen) != expected:
            return (
                False,
                f"status() of {fen} = {statuses.get(fen)!r}, expected {expected!r}",
            )
    for spec, row in _games(g)[:4]:
        board = (
            _replay(spec["fen"], spec["uci"])[-1]
            if spec["uci"]
            else chess.Board(spec["fen"])
        )
        if row.get("status") != _expected_status(board) or row.get(
            "result"
        ) != _expected_result(board):
            return (
                False,
                f"end of a random game: {row.get('status')!r}/{row.get('result')!r}",
            )
    return True, "status and result agree everywhere"


def check_history(g: Grade) -> Result:
    for spec, row in _games(g)[:4]:
        board = chess.Board(spec["fen"])
        expected = []
        for uci in spec["uci"]:
            move = chess.Move.from_uci(uci)
            expected.append(board.san(move))
            board.push(move)
        if row.get("history") != expected:
            return False, "history_san() does not match the moves played"
    return True, "move history in SAN"


def _ai_rows(g: Grade, kind: str) -> list[dict[str, Any]]:
    return [row for row in g.logic.get("ai") or [] if row.get("kind") == kind]


def check_ai_legal(g: Grade) -> Result:
    rows = _ai_rows(g, "legal")
    if len(rows) < 2:
        return False, "best_move() did not answer"
    for row in rows:
        board = chess.Board(row["fen"])
        move = row.get("move") or ""
        if move not in {m.uci() for m in board.legal_moves}:
            return False, f"best_move() returned {move!r}, not a legal move"
        if _fen_key(row.get("fen_after")) != _fen_key(row["fen"]):
            return False, "best_move() changed the position; it must only suggest"
        if int(row.get("ms") or 0) > 8000:
            return False, f"best_move() took {row.get('ms')} ms on a 5000 ms budget"
    return True, "legal suggestions within budget"


def _ai_puzzles(g: Grade, kind: str) -> list[dict[str, Any]]:
    """The answers to this run's puzzles of ``kind``.

    Unmeasured only when the *lab* could not find a puzzle in this run's
    positions. A puzzle the engine never answered is the engine's failure.
    """
    if not any(item["kind"] == kind for item in g.probe["ai"]):
        raise Unmeasured(
            f"no {kind.replace('_', ' ')} puzzle could be generated for this run"
        )
    rows = _ai_rows(g, kind)
    expected = sum(1 for item in g.probe["ai"] if item["kind"] == kind)
    if len(rows) < expected:
        raise AssertionError(
            f"best_move() answered {len(rows)}/{expected} {kind} puzzles"
        )
    return rows


def check_ai_mate_in_one(g: Grade) -> Result:
    rows = _ai_puzzles(g, "mate_in_1")
    for row in rows:
        board = chess.Board(row["fen"])
        move = row.get("move") or ""
        if move not in {m.uci() for m in board.legal_moves} or not _is_mate_after(
            board, chess.Move.from_uci(move)
        ):
            return False, f"missed a mate in one in {row['fen']} (played {move!r})"
    return True, f"found {len(rows)} mates in one"


def check_ai_mate_in_two(g: Grade) -> Result:
    rows = _ai_puzzles(g, "mate_in_2")
    for row in rows:
        board = chess.Board(row["fen"])
        move = row.get("move") or ""
        legal = {m.uci() for m in board.legal_moves}
        if move not in legal or not forces_mate_in_two(
            board, chess.Move.from_uci(move)
        ):
            return (
                False,
                f"missed a forced mate in two in {row['fen']} (played {move!r})",
            )
    return True, f"found {len(rows)} forced mates in two"


# Scene checks ----------------------------------------------------------------


def _scene_ready(g: Grade) -> None:
    if not g.scene:
        raise Unmeasured("the scene was not harvested")


def check_scene_loads(g: Grade) -> Result:
    if not g.scene.get("main_scene"):
        return False, "no main scene set in project.godot"
    ok = bool(
        g.scene.get("has_camera")
        and g.scene.get("has_board")
        and g.scene.get("has_square_to_world")
    )
    return (
        ok,
        "main scene, a Camera3D and a chess_board with square_to_world()"
        if ok
        else (
            f"camera={g.scene.get('has_camera')} board={g.scene.get('has_board')} "
            f"square_to_world={g.scene.get('has_square_to_world')}"
        ),
    )


def check_board_geometry(g: Grade) -> Result:
    squares = g.scene.get("squares") or {}
    worlds = {s: v.get("world") for s, v in squares.items() if v.get("world")}
    if len(worlds) != 64:
        return False, f"square_to_world() answered {len(worlds)}/64 squares"
    a1, h1, a8 = worlds["a1"], worlds["h1"], worlds["a8"]
    step_file = [(h1[i] - a1[i]) / 7 for i in range(3)]
    step_rank = [(a8[i] - a1[i]) / 7 for i in range(3)]
    for square, world in worlds.items():
        f, r = "abcdefgh".index(square[0]), int(square[1]) - 1
        predicted = [a1[i] + f * step_file[i] + r * step_rank[i] for i in range(3)]
        if max(abs(predicted[i] - world[i]) for i in range(3)) > 0.05 * max(
            1e-6, _norm(step_file)
        ):
            return False, f"{square} is off the 8x8 grid"
    if (
        _norm(step_file) < 1e-3
        or abs(_dot(step_file, step_rank)) > 0.05 * _norm(step_file) ** 2
    ):
        return False, "files and ranks are not perpendicular"
    return True, "64 squares on a regular grid"


def _norm(v: list[float]) -> float:
    return sum(x * x for x in v) ** 0.5


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def check_board_visible(g: Grade) -> Result:
    squares = g.scene.get("squares") or {}
    size = g.scene.get("viewport") or [0, 0]
    inside = 0
    for value in squares.values():
        screen = value.get("screen")
        if screen and 0 <= screen[0] <= size[0] and 0 <= screen[1] <= size[1]:
            inside += 1
    return (
        inside == 64,
        f"{inside}/64 squares inside the {size[0]:.0f}x{size[1]:.0f} view",
    )


def check_board_colors(g: Grade) -> Result:
    luma = g.scene.get("square_luma") or {}
    dark = [luma.get(s) for s in ("d4", "e5", "c3")]
    light = [luma.get(s) for s in ("e4", "d5", "f3")]
    if any(v is None for v in dark + light):
        return False, "squares could not be sampled on screen"
    gap = min(light) - max(dark)
    return gap > 0.04, f"light squares brighter than dark ones by {gap:.3f}"


def _piece_map(rows: list[dict[str, Any]]) -> dict[str, str]:
    return {row["square"]: row["piece"] for row in rows if row.get("square")}


def _expected_piece_map(fen: str) -> dict[str, str]:
    board = chess.Board(fen)
    return {
        chess.square_name(sq): piece.symbol() for sq, piece in board.piece_map().items()
    }


def check_pieces_start(g: Grade) -> Result:
    start = g.scene.get("start") or {}
    got = _piece_map(start.get("pieces") or [])
    expected = _expected_piece_map(START_FEN)
    if got != expected:
        return (
            False,
            f"pieces: {len(got)} placed, mismatches {sorted(set(got.items()) ^ set(expected.items()))[:6]}",
        )
    return _pieces_on_squares(g, start.get("pieces") or [])


def _pieces_on_squares(g: Grade, rows: list[dict[str, Any]]) -> Result:
    squares = g.scene.get("squares") or {}
    a1, b1 = squares.get("a1", {}).get("world"), squares.get("b1", {}).get("world")
    if not a1 or not b1:
        return False, "no board geometry"
    tolerance = 0.35 * _norm([b1[i] - a1[i] for i in range(3)])
    for row in rows:
        world = squares.get(row.get("square"), {}).get("world")
        if not world:
            return False, f"piece on unknown square {row.get('square')!r}"
        horizontal = [row["world"][0] - world[0], row["world"][2] - world[2]]
        if _norm(horizontal) > tolerance:
            return (
                False,
                f"the {row.get('piece')} meta says {row.get('square')} but it stands elsewhere",
            )
    return True, "every piece stands on its square"


def check_click_moves(g: Grade) -> Result:
    clicks = g.scene.get("clicks") or []
    line = g.probe["click_line"]
    board = chess.Board()
    for i, pair in enumerate(line):
        row = clicks[i] if i < len(clicks) else {}
        move = chess.Move.from_uci(pair[0] + pair[1])
        board.push(move)
        state = row.get("state") or {}
        if _fen_key(state.get("fen")) != _fen_key(board.fen()):
            return False, f"clicking {pair[0]}->{pair[1]} left {state.get('fen')!r}"
        if _piece_map(state.get("pieces") or []) != _expected_piece_map(board.fen()):
            return (
                False,
                f"after clicking {pair[0]}->{pair[1]} the 3D pieces do not match the position",
            )
    return (
        True,
        f"{len(line)} moves played with the mouse, en passant and both castles included",
    )


def check_click_illegal(g: Grade) -> Result:
    row = g.scene.get("illegal_click") or {}
    if not row.get("clicked"):
        return False, "the squares could not be clicked"
    return row.get("fen_after") == row.get(
        "fen_before"
    ), "an illegal click pair changed nothing"


def check_follow_engine(g: Grade) -> Result:
    follow = g.scene.get("follow") or {}
    fen = g.probe["follow"]["fen"]
    if _fen_key(follow.get("fen")) != _fen_key(fen):
        return False, "load_fen() through the API did not take"
    if _piece_map(follow.get("pieces") or []) != _expected_piece_map(fen):
        return False, "the 3D board did not follow a position loaded through the API"
    return _pieces_on_squares(g, follow.get("pieces") or [])


def check_click_promotion(g: Grade) -> Result:
    row = (g.scene.get("promotion") or {}).get("state") or {}
    board = chess.Board(g.probe["follow"]["fen"])
    board.push_uci("a7a8q")
    if _fen_key(row.get("fen")) != _fen_key(board.fen()):
        return (
            False,
            f"a pawn clicked to the last rank did not promote to a queen: {row.get('fen')!r}",
        )
    return _piece_map(row.get("pieces") or []) == _expected_piece_map(
        board.fen()
    ), "promotion shown on the board"


def check_status_label(g: Grade) -> Result:
    labels = g.scene.get("status_labels") or {}
    wanted = {"checkmate": "checkmate", "stalemate": "stalemate", "check": "check"}
    for kind, word in wanted.items():
        texts = " ".join(labels.get(kind) or []).lower()
        if word not in texts:
            return False, f"no visible status_label says {word!r} in a {kind} position"
    return True, "the HUD names check, checkmate and stalemate"


def check_ai_in_scene(g: Grade) -> Result:
    row = g.scene.get("ai_scene") or {}
    state = row.get("state") or {}
    fen = state.get("fen") or ""
    board = chess.Board()
    board.push_uci("e2e4")
    if not isinstance(fen, str) or fen.split(" ")[1:2] != ["w"]:
        return False, "the AI did not reply to a move made with the mouse"
    replies = {m.uci() for m in board.legal_moves}
    replied = None
    for uci in replies:
        candidate = board.copy()
        candidate.push_uci(uci)
        if _fen_key(candidate.fen()) == _fen_key(fen):
            replied = uci
    if replied is None:
        return (
            False,
            f"the position after the AI's reply is not one legal move away: {fen!r}",
        )
    after = board.copy()
    after.push_uci(replied)
    if _piece_map(state.get("pieces") or []) != _expected_piece_map(after.fen()):
        return False, "the AI moved in the engine but the 3D board does not show it"
    return True, f"the AI answered 1.e4 with {after.peek().uci()} on the board"


def check_render(g: Grade) -> Result:
    stats = g.scene.get("image_start") or {}
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
    # Software rendering on a CPU (ADR-0073): a loose ceiling that an
    # unoptimised but sane scene meets and a runaway one does not.
    return p95 < 250, f"p95 frame {p95:.0f} ms on a CPU renderer"


CHECKS = {
    "contract": check_contract,
    "own_code": check_own_code,
    "renderer": check_renderer,
    "fen_roundtrip": check_fen_roundtrip,
    "legal_moves": check_legal_basic,
    "en_passant": check_en_passant,
    "castling": check_castling,
    "pins_and_checks": check_pins_and_checks,
    "promotion": check_promotion,
    "perft_shallow": check_perft_shallow,
    "perft_deep": check_perft_deep,
    "perft_random": check_perft_random,
    "fen_tracking": check_fen_tracking,
    "clocks": check_clocks,
    "san_output": check_san_output,
    "san_disambiguation": check_san_disambiguation,
    "san_input": check_san_input,
    "illegal_refused": check_illegal_refused,
    "undo": check_undo,
    "mate_and_stalemate": check_mate_and_stalemate,
    "draw_rules": check_draw_rules,
    "status_everywhere": check_status_every_position,
    "history": check_history,
    "ai_legal": check_ai_legal,
    "ai_mate_in_one": check_ai_mate_in_one,
    "ai_mate_in_two": check_ai_mate_in_two,
    "scene_loads": check_scene_loads,
    "board_geometry": check_board_geometry,
    "board_visible": check_board_visible,
    "board_colors": check_board_colors,
    "pieces_start": check_pieces_start,
    "click_moves": check_click_moves,
    "click_illegal": check_click_illegal,
    "follow_engine": check_follow_engine,
    "click_promotion": check_click_promotion,
    "status_label": check_status_label,
    "ai_in_scene": check_ai_in_scene,
    "render": check_render,
    "frame_time": check_frame_time,
}
SCENE_CHECKS = {
    "scene_loads",
    "board_geometry",
    "board_visible",
    "board_colors",
    "pieces_start",
    "click_moves",
    "click_illegal",
    "follow_engine",
    "click_promotion",
    "status_label",
    "ai_in_scene",
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
    args = parser.parse_args(argv)
    if args.cmd == "harvest":
        return harvest(args.workspace.resolve(), args.run_id)
    if args.cmd == "probe":
        print(json.dumps(build_probe(args.run_id), indent=1))
        return 0
    return run_check(args.name, args.workspace.resolve())


if __name__ == "__main__":
    sys.exit(main())
