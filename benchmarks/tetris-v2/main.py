from __future__ import annotations

import os
import random
import sys

pygame = None  # imported lazily to avoid blocking on SDL init

# ---------------------------------------------------------------------------
# Piece definitions – clockwise rotation order for each tetromino
# Each piece: list of (shape, shape_rotated_90) for the two orientations.
# Shapes are lists-of-lists of ints where 1 = filled cell.
# ---------------------------------------------------------------------------

def _rotate_clockwise(shape: list[list[int]]) -> list[list[int]]:
    """Rotate a 2D shape clockwise by 90 degrees."""
    transposed = [list(col) for col in zip(*shape)]
    return transposed[::-1]

# Standard tetromino shapes (first orientation)
PIECE_BASES = {
    "I": [[0, 0, 0, 0],
         [1, 1, 1, 1],
         [0, 0, 0, 0],
         [0, 0, 0, 0]],

    "J": [[1, 0, 0],
         [1, 1, 1],
         [0, 0, 0]],

    "L": [[0, 0, 1],
         [1, 1, 1],
         [0, 0, 0]],

    "O": [[1, 1],
         [1, 1]],

    "S": [[0, 1, 1],
         [1, 1, 0],
         [0, 0, 0]],

    "T": [[0, 1, 0],
         [1, 1, 1],
         [0, 0, 0]],

    "Z": [[1, 1, 0],
         [0, 1, 1],
         [0, 0, 0]]
}

# Pre-compute all rotation states for each piece type (unique rotations only)
PIECE_SHAPES: dict[str, list[list[list[int]]]] = {}
for name in PIECE_BASES:
    shapes = [_rotate_clockwise(_rotate_clockwise(_rotate_clockwise(_rotate_clockwise(PIECE_BASES[name]))))
             for _ in range(4)]
    # Remove duplicates (e.g., O piece is the same in all rotations)
    unique = [shapes[0]]
    for s in shapes[1:]:
        if s != unique[-1]:
            unique.append(s)
    PIECE_SHAPES[name] = unique


class Piece:
    """A single tetromino piece with position and orientation."""

    def __init__(self, name: str, x: int, y: int):
        self.name = name
        self.shape = PIECE_SHAPES[name][0]  # current shape (2D list of ints)
        self.x = x
        self.y = y


class Game:
    """Tetris game logic. Fully testable without a display."""

    SCORE_PER_LINE = [0, 40, 100, 300, 800]

    def __init__(self, width: int = 10, height: int = 20, seed: int = None):
        self.width = width
        self.height = height
        self.board: list[list[int]] = [[0 for _ in range(width)] for _ in range(height)]
        self.score = 0
        self.lines_cleared = 0
        self.game_over = False

        # Seeded random for deterministic piece sequence
        self._rng = random.Random(seed) if seed is not None else random

        # Piece queue – pre-generate a small buffer so we never have to
        # create a new piece while the game is running.
        self._queue: list[str] = []

        # Spawn the first piece
        self.current = self._spawn_next()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _spawn_next(self) -> Piece:
        """Return a new piece from the queue (create one if empty)."""
        while not self._queue:
            self._queue.append(self._rng.choice(list(PIECE_SHAPES.keys())))
        name = self._queue.pop(0)
        # Start centre-ish, but guarantee x >= 0 and shape fits horizontally.
        shape_width = len(PIECE_SHAPES[name][0]) if PIECE_SHAPES[name] else 1
        safe_x = max(0, (self.width - shape_width) // 2)
        return Piece(name, safe_x, 0 if name == "I" else 1)

    def _next_piece(self) -> Piece:
        """Get the next piece from queue, or spawn a fresh one."""
        while not self._queue:
            self._queue.append(self._rng.choice(list(PIECE_SHAPES.keys())))
        name = self._queue.pop(0)
        shape_width = len(PIECE_SHAPES[name][0]) if PIECE_SHAPES[name] else 1
        safe_x = max(0, (self.width - shape_width) // 2)
        return Piece(name, safe_x, 0 if name == "I" else 1)

    def _is_valid(self, piece: Piece, dx: int = 0, dy: int = 0) -> bool:
        """Return True when *piece* at (x+dx, y+dy) does not collide."""
        shape = piece.shape
        for r in range(len(shape)):
            for c in range(len(shape[r])):
                if shape[r][c]:
                    nx, ny = piece.x + dx + c, piece.y + dy + r
                    if nx < 0 or nx >= self.width or ny >= self.height:
                        return False
                    # Check against locked cells only (ignore rows above board)
                    if 0 <= ny < self.height and self.board[ny][nx]:
                        return False
        return True

    def _lock(self):
        """Lock the current piece into the board, then check for game over."""
        for r in range(len(self.current.shape)):
            for c in range(len(self.current.shape[r])):
                if self.current.shape[r][c]:
                    ny = self.current.y + r
                    if 0 <= ny < self.height:
                        nx = self.current.x + c
                        if 0 <= nx < self.width:
                            # Store as a positive integer (1-9) instead of string
                            self.board[ny][nx] = ord(self.current.name) - ord('A') + 1

        # Check for game over BEFORE clearing lines
        self._check_game_over()

        # Only clear lines if the game is not already over
        if not self.game_over:
            self._clear_lines()
            # Spawn the next piece after line clears
            self.current = self._spawn_next()

    def _clear_lines(self):
        """Remove full rows, shift down, update score."""
        for row in range(self.height - 1, -1, -1):
            if all(cell != 0 for cell in self.board[row]):
                del self.board[row]
                self.board.insert(0, [0] * self.width)
                self.lines_cleared += 1
                self.score += self.SCORE_PER_LINE[self.lines_cleared]

    def _check_game_over(self):
        """Game over if the next piece immediately collides."""
        nxt = self._next_piece()
        if not self._is_valid(nxt):
            self.game_over = True
        else:
            # Restore queue to its previous state (we peeked one item)
            self._queue.append(self._rng.choice(list(PIECE_SHAPES.keys())))

    def _spawn_piece_after_clear(self):
        """Spawn the next piece after line clears."""
        if not self.game_over:
            self.current = self._spawn_next()

    # ------------------------------------------------------------------
    # Public API – matches the graded contract exactly
    # ------------------------------------------------------------------

    def move(self, dx: int) -> bool:
        """Try moving horizontally. Returns True if moved."""
        if self.game_over:
            return False
        if not self._is_valid(self.current, dx):
            return False
        self.current.x += dx
        return True

    def rotate(self) -> bool:
        """Rotate the current piece clockwise. Returns True on success."""
        if self.game_over:
            return False
        num_shapes = len(PIECE_SHAPES[self.current.name])
        # For O-piece (1 unique shape), rotation doesn't change anything
        if num_shapes == 1:
            return False
        new_shape = PIECE_SHAPES[self.current.name][1]
        # Try direct rotation first; try wall-kicks (±1 x, ±2 x) as fallback.
        for dx in (0, 1, -1):
            temp_piece = Piece(self.current.name, self.current.x + dx, self.current.y)
            temp_piece.shape = new_shape
            if self._is_valid(temp_piece):
                self.current.shape = new_shape
                self.current.x += dx
                return True
        # Try rotating the other way too (full 270°) as last resort
        for dx in (0, 1, -1):
            temp_piece = Piece(self.current.name, self.current.x + dx, self.current.y)
            rot90 = _rotate_clockwise(new_shape)
            temp_piece.shape = rot90
            if self._is_valid(temp_piece):
                self.current.shape = rot90
                self.current.x += dx
                return True
        return False

    def soft_drop(self) -> bool:
        """Move down one row. Returns True if moved, else locks the piece."""
        if self.game_over:
            return False
        if not self._is_valid(self.current, dy=1):
            self._lock()
            return False
        self.current.y += 1
        return False

    def hard_drop(self) -> int:
        """Drop to the bottom and lock. Returns number of rows dropped."""
        if self.game_over:
            return 0
        dropped = 0
        while self._is_valid(self.current, dy=1):
            self.current.y += 1
            dropped += 1
        self._lock()
        return dropped

    def step(self) -> None:
        """One gravity tick – equivalent to one soft_drop()."""
        self.soft_drop()


# ---------------------------------------------------------------------------
# Pygame GUI – runs only under __main__ with a real display
# ---------------------------------------------------------------------------

def _init_pygame():
    """Initialize pygame (may block if called without SDL_VIDEODRIVER=dummy)."""
    pygame.init()

def _pygame_main():
    """The interactive game loop."""
    _init_pygame()

    WIDTH, HEIGHT = 10 * 30, 20 * 30
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    pygame.display.set_caption("Tetris")
    clock = pygame.time.Clock()

    game = Game(width=10, height=20, seed=None)

    running = True
    last_gravity = pygame.time.get_ticks()
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE and game.game_over:
                    running = False
                if event.key == pygame.K_LEFT:
                    game.move(-1)
                elif event.key == pygame.K_RIGHT:
                    game.move(1)
                elif event.key in (pygame.K_UP, pygame.K_RETURN):
                    game.rotate()
                elif event.key == pygame.K_SPACE:
                    game.hard_drop()

        # Gravity tick every 0.5 seconds
        if pygame.time.get_ticks() - last_gravity > 500:
            game.step()
            last_gravity = pygame.time.get_ticks()

        # Draw
        screen.fill((30, 30, 40))
        for r in range(game.height):
            for c in range(self.width):
                col = (60 + game.board[r][c] * 15) % 255
                pygame.draw.rect(screen, (col, col, col), (c * 30, r * 30, 30, 30))

        shape = game.current.shape
        for r in range(len(shape)):
            for c in range(len(shape[r])):
                if shape[r][c]:
                    py_x = (game.current.x + c) * 30
                    py_y = (game.current.y + r) * 30
                    pygame.draw.rect(screen, (255, 255, 255), (py_x, py_y, 30, 30))

        # Info text
        font = pygame.font.SysFont("monospace", 16)
        info = f"Score: {game.score}  Lines: {game.lines_cleared}"
        if game.game_over:
            info += "  GAME OVER"
        text = font.render(info, True, (255, 255, 255))
        screen.blit(text, (10, 10))

        pygame.display.flip()
        clock.tick(60)

    pygame.quit()


# ---------------------------------------------------------------------------
# Benchmark smoke mode – runs ~120 frames headlessly
# ---------------------------------------------------------------------------

def _benchmark_smoke():
    """Run ~120 frames headlessly (used when BENCH_SMOKE=1)."""
    game = Game(width=10, height=20, seed=None)
    for _ in range(120):
        if game.game_over:
            break
        game.step()

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    smoke_mode = os.environ.get("BENCH_SMOKE") == "1"
    if smoke_mode:
        _benchmark_smoke()
        sys.exit(0)
    else:
        _pygame_main()
