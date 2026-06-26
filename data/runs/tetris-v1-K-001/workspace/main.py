"""Tetris — tetris-v1-K-001"""

import pygame
import sys
import random


# Tetromino shapes defined as lists of (row, col) offsets from top-left corner
SHAPES = {
    "I": [(0, 0), (0, 1), (0, 2), (0, 3)],
    "O": [(0, 0), (0, 1), (1, 0), (1, 1)],
    "T": [(0, 1), (1, 0), (1, 1), (1, 2)],
    "S": [(0, 1), (0, 2), (1, 0), (1, 1)],
    "Z": [(0, 0), (0, 1), (1, 1), (1, 2)],
    "J": [(0, 0), (1, 0), (1, 1), (1, 2)],
    "L": [(0, 2), (1, 0), (1, 1), (1, 2)],
}

SHAPE_COLORS = {
    "I": (0, 255, 255),
    "O": (255, 255, 0),
    "T": (128, 0, 128),
    "S": (0, 255, 0),
    "Z": (255, 0, 0),
    "J": (0, 0, 255),
    "L": (255, 165, 0),
}


class Piece:
    """A tetromino piece with position and shape."""

    def __init__(self):
        self.types = list(SHAPES.keys())
        self.type = random.choice(self.types)
        self.shape = SHAPES[self.type]
        self.color = SHAPE_COLORS[self.type]
        self.x = 3  # column offset (board is 10 wide, center ~3)
        self.y = 0  # row offset

    def rotate(self):
        """Rotate shape 90 degrees clockwise: (r, c) -> (-c, r)."""
        self.shape = [(-c, r) for r, c in self.shape]


class Board:
    """10x20 grid for tetris board."""

    COLS = 10
    ROWS = 20
    CELL_SIZE = 30

    def __init__(self):
        self.grid = [[(0, 0, 0)] * self.COLS for _ in range(self.ROWS)]

    def get_board_pos(self, piece):
        """Convert piece world position to board grid cells."""
        cells = []
        for dr, dc in piece.shape:
            r = piece.y + dr
            c = piece.x + dc
            if 0 <= r < self.ROWS and 0 <= c < self.COLS:
                cells.append((r, c))
        return cells

    def can_move(self, piece, dx=0, dy=0):
        """Check if piece can move by (dx, dy) without going out of bounds or hitting locked cells."""
        for dr, dc in piece.shape:
            r = piece.y + dr + dy
            c = piece.x + dc + dx
            if not (0 <= r < self.ROWS and 0 <= c < self.COLS):
                return False
            if self.grid[r][c] != (0, 0, 0):
                return False
        return True

    def lock_piece(self, piece):
        """Copy the active piece onto the grid as locked cells."""
        for dr, dc in piece.shape:
            r = piece.y + dr
            c = piece.x + dc
            if 0 <= r < self.ROWS and 0 <= c < self.COLS:
                self.grid[r][c] = piece.color

    def can_rotate(self, piece):
        """Check if piece can rotate without collision; try basic wall-kicks."""
        rotated = [(-c, r) for r, c in piece.shape]
        # Check original position first
        for dr, dc in rotated:
            r = piece.y + dr
            c = piece.x + dc
            if not (0 <= r < self.ROWS and 0 <= c < self.COLS):
                pass  # out of bounds, try wall kicks below
            elif self.grid[r][c] != (0, 0, 0):
                pass  # collision with locked piece
            else:
                continue
            break
        else:
            return True

        # Try wall kicks: shift left by 1, right by 1, left by 2, right by 2
        for kick in [-1, 1, -2, 2]:
            all_clear = True
            for dr, dc in rotated:
                r = piece.y + dr
                c = piece.x + dc + kick
                if not (0 <= r < self.ROWS and 0 <= c < self.COLS):
                    all_clear = False
                    break
                elif self.grid[r][c] != (0, 0, 0):
                    all_clear = False
                    break
            if all_clear:
                piece.x += kick
                return True

        return False

    def draw(self, screen):
        """Draw the board grid background and locked pieces."""
        for row in range(self.ROWS):
            for col in range(self.COLS):
                rect = pygame.Rect(col * self.CELL_SIZE, row * self.CELL_SIZE,
                                    self.CELL_SIZE, self.CELL_SIZE)
                # Draw cell background (dark grey grid)
                pygame.draw.rect(screen, (30, 30, 30), rect)
                # Draw cell border (subtle grid lines)
                pygame.draw.rect(screen, (50, 50, 50), rect, 1)

        # Draw locked pieces from grid
        for row in range(self.ROWS):
            for col in range(self.COLS):
                color = self.grid[row][col]
                if color != (0, 0, 0):
                    rect = pygame.Rect(col * self.CELL_SIZE, row * self.CELL_SIZE,
                                       self.CELL_SIZE, self.CELL_SIZE)
                    pygame.draw.rect(screen, color, rect)
                    pygame.draw.rect(screen, (80, 80, 80), rect, 1)

    def clear_lines(self):
        """Remove full rows, add empty rows at top. Returns number of lines cleared."""
        lines_cleared = 0
        new_grid = []
        for row in range(self.ROWS):
            if all(cell != (0, 0, 0) for cell in self.grid[row]):
                lines_cleared += 1
            else:
                new_grid.append(self.grid[row])
        while len(new_grid) < self.ROWS:
            new_grid.insert(0, [(0, 0, 0)] * self.COLS)
        self.grid = new_grid
        return lines_cleared

    def draw_piece(self, screen, piece):
        """Draw the active piece on the board."""
        for dr, dc in piece.shape:
            r = piece.y + dr
            c = piece.x + dc
            if 0 <= r < self.ROWS and 0 <= c < self.COLS:
                rect = pygame.Rect(c * self.CELL_SIZE, r * self.CELL_SIZE,
                                   self.CELL_SIZE, self.CELL_SIZE)
                pygame.draw.rect(screen, piece.color, rect)
                pygame.draw.rect(screen, (80, 80, 80), rect, 1)


class Game:
    """Top-level game loop and state."""

    WIDTH = 300
    HEIGHT = 600
    FPS = 60
    GRAVITY_INTERVAL = 1000  # milliseconds between drops

    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((self.WIDTH, self.HEIGHT))
        pygame.display.set_caption("Tetris")
        self.clock = pygame.time.Clock()
        self.running = True
        self.game_over = False
        self.board = Board()
        self.piece = Piece()
        self.gravity_timer = 0
        self.score = 0
        self.font = pygame.font.Font(None, 36)

    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            if event.type == pygame.KEYDOWN:
                if self.game_over and event.key == pygame.K_r:
                    self.restart()
                if event.key == pygame.K_LEFT:
                    if self.board.can_move(self.piece, dx=-1):
                        self.piece.x -= 1
                if event.key == pygame.K_RIGHT:
                    if self.board.can_move(self.piece, dx=1):
                        self.piece.x += 1
                if event.key == pygame.K_UP:
                    original_shape = self.piece.shape[:]
                    self.piece.rotate()
                    if not self.board.can_rotate(self.piece):
                        self.piece.shape = original_shape

    def restart(self):
        """Reset game state for a new game."""
        self.game_over = False
        self.board = Board()
        self.piece = Piece()
        self.gravity_timer = 0
        self.score = 0

    def update(self):
        delta = self.clock.get_time()
        self.gravity_timer += delta
        if self.gravity_timer >= self.GRAVITY_INTERVAL:
            if self.board.can_move(self.piece, dy=1):
                self.piece.y += 1
            else:
                self.board.lock_piece(self.piece)
                lines = self.board.clear_lines()
                if lines > 0:
                    self.score += lines * 100
                self.piece = Piece()
                if not self.board.can_move(self.piece, dy=0):
                    self.game_over = True
            self.gravity_timer = 0

    def render(self):
        self.screen.fill((0, 0, 0))
        self.board.draw(self.screen)
        if not self.game_over:
            self.board.draw_piece(self.screen, self.piece)
        score_text = self.font.render(f"Score: {self.score}", True, (255, 255, 255))
        self.screen.blit(score_text, (10, self.HEIGHT - 40))
        if self.game_over:
            game_over_text = self.font.render("GAME OVER", True, (255, 0, 0))
            restart_text = pygame.font.Font(None, 28).render("Press R to restart", True, (255, 255, 255))
            game_over_rect = game_over_text.get_rect(center=(self.WIDTH // 2, self.HEIGHT // 2 - 20))
            restart_rect = restart_text.get_rect(center=(self.WIDTH // 2, self.HEIGHT // 2 + 20))
            self.screen.blit(game_over_text, game_over_rect)
            self.screen.blit(restart_text, restart_rect)
        pygame.display.flip()

    def run(self):
        while self.running:
            self.handle_events()
            self.update()
            self.render()
            self.clock.tick(self.FPS)

        pygame.quit()
        sys.exit()


def main():
    game = Game()
    game.run()


if __name__ == "__main__":
    main()
