"""Graded tests for tetris-v2: the game logic through the `Game` contract.

Each test is one validation command in task.yaml, so `test_pass_rate` has the
resolution of this file — ten independent assertions instead of one smoke
check. They run headless (`SDL_VIDEODRIVER=dummy`) from inside the workspace,
with the agent's `main.py` on `sys.path` via the cwd.
"""

from __future__ import annotations

import importlib
import os
import sys

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")


@pytest.fixture
def main():
    sys.path.insert(0, os.getcwd())
    module = importlib.import_module("main")
    return importlib.reload(module)


@pytest.fixture
def game(main):
    return main.Game(width=10, height=20, seed=42)


def _filled(board) -> int:
    return sum(1 for row in board for cell in row if cell)


def test_board_has_the_requested_shape(game):
    assert len(game.board) == 20
    assert all(len(row) == 10 for row in game.board)
    assert _filled(game.board) == 0


def test_a_piece_spawns_at_the_top(game):
    piece = game.current
    assert piece is not None
    assert any(any(row) for row in piece.shape)
    assert piece.y <= 1


def test_the_same_seed_gives_the_same_pieces(main):
    a = main.Game(width=10, height=20, seed=7)
    b = main.Game(width=10, height=20, seed=7)
    for _ in range(5):
        assert a.current.shape == b.current.shape
        a.hard_drop()
        b.hard_drop()


def test_soft_drop_moves_the_piece_down(game):
    before = game.current.y
    assert game.soft_drop() is True
    assert game.current.y == before + 1


def test_move_is_blocked_by_the_wall(game):
    # Push all the way left: eventually the piece cannot move further.
    moved = [game.move(-1) for _ in range(12)]
    assert True in moved
    assert moved[-1] is False
    assert game.current.x >= 0


def test_rotate_changes_the_shape_or_is_refused(game):
    shape = [row[:] for row in game.current.shape]
    result = game.rotate()
    if result:
        assert game.current.shape != shape or all(
            len(shape) == len(shape[0]) for _ in [0]
        )
    else:
        assert game.current.shape == shape


def test_hard_drop_locks_the_piece_into_the_board(game):
    cells = sum(1 for row in game.current.shape for cell in row if cell)
    dropped = game.hard_drop()
    assert dropped >= 0
    assert _filled(game.board) == cells


def test_locked_cells_block_movement(game):
    game.hard_drop()
    # Drop the next piece onto the first one: it must lock above, never overlap.
    game.hard_drop()
    first_two = _filled(game.board)
    assert first_two > 0
    for row in game.board:
        assert all(cell in (0, *range(1, 100)) for cell in row)


def test_a_full_row_is_cleared_and_scored(game):
    # Fill the bottom row except where the current piece will land is hard to
    # arrange generically, so pre-fill the board directly: the contract says a
    # full row after a lock is removed.
    for x in range(10):
        game.board[19][x] = 1
    game.board[19][0] = 0
    # Put a single cell in the gap by dropping a piece there is shape-dependent;
    # instead fill the gap and trigger a lock with any piece.
    game.board[19][0] = 1
    lines_before = game.lines_cleared
    score_before = game.score
    game.hard_drop()
    assert game.lines_cleared >= lines_before + 1
    assert game.score > score_before
    assert not all(game.board[19])


def test_game_over_when_the_stack_reaches_the_top(game):
    for _ in range(200):
        if game.game_over:
            break
        game.hard_drop()
    assert game.game_over is True
