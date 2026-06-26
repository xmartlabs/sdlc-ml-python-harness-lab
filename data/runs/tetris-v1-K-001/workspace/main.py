import pygame
import sys
import random

pygame.init()

WIDTH = 300
HEIGHT = 700
BLOCK_SIZE = 30
COLS = 10
ROWS = 20
GRID_OFFSET_Y = 0
SCREEN = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Tetris")

# Colors
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
GRAY = (40, 40, 40)
COLORS = [
    (0, 240, 240),    # I - cyan
    (0, 0, 240),      # J - blue
    (240, 160, 0),    # L - orange
    (240, 240, 0),    # O - yellow
    (0, 240, 0),      # S - green
    (160, 0, 240),    # T - purple
    (240, 0, 0),      # Z - red
]

# Tetromino shapes (each rotation state)
SHAPES = [
    [[1, 1, 1, 1]],                          # I
    [[1, 0, 0], [1, 1, 1]],                  # J
    [[0, 0, 1], [1, 1, 1]],                  # L
    [[1, 1], [1, 1]],                         # O
    [[0, 1, 1], [1, 1, 0]],                  # S
    [[0, 1, 0], [1, 1, 1]],                  # T
    [[1, 1, 0], [0, 1, 1]],                  # Z
]


def draw_grid():
    for x in range(0, WIDTH, BLOCK_SIZE):
        for y in range(GRID_OFFSET_Y, HEIGHT - BLOCK_SIZE * 2, BLOCK_SIZE):
            rect = pygame.Rect(x, y, BLOCK_SIZE, BLOCK_SIZE)
            pygame.draw.rect(SCREEN, GRAY, rect, 1)


def draw_piece(piece):
    shape = piece["shape"]
    color = piece["color"]
    for row_idx, row in enumerate(shape):
        for col_idx, cell in enumerate(row):
            if cell:
                x = (piece["x"] + col_idx) * BLOCK_SIZE
                y = (piece["y"] + row_idx) * BLOCK_SIZE
                rect = pygame.Rect(x, y, BLOCK_SIZE, BLOCK_SIZE)
                pygame.draw.rect(SCREEN, color, rect)
                pygame.draw.rect(SCREEN, WHITE, rect, 1)


def new_piece():
    idx = random.randint(0, len(SHAPES) - 1)
    return {
        "shape": SHAPES[idx],
        "color": COLORS[idx],
        "x": COLS // 2 - len(SHAPES[idx][0]) // 2,
        "y": 0,
    }


def valid_position(piece, board):
    shape = piece["shape"]
    for row_idx, row in enumerate(shape):
        for col_idx, cell in enumerate(row):
            if cell:
                x = piece["x"] + col_idx
                y = piece["y"] + row_idx
                if x < 0 or x >= COLS or y >= ROWS:
                    return False
                if y >= 0 and board[y][x]:
                    return False
    return True


def lock_piece(piece, board):
    shape = piece["shape"]
    for row_idx, row in enumerate(shape):
        for col_idx, cell in enumerate(row):
            if cell:
                x = piece["x"] + col_idx
                y = piece["y"] + row_idx
                if 0 <= y < ROWS:
                    board[y][x] = piece["color"]


def clear_lines(board, screen):
    lines_cleared = 0
    new_board = [row for row in board if any(cell == 0 for cell in row)]
    lines_cleared = ROWS - len(new_board)
    for _ in range(lines_cleared):
        new_board.insert(0, [0] * COLS)
    return new_board


def game_over(board):
    return any(cell != 0 for cell in board[0])


running = True
clock = pygame.time.Clock()
board = [[0] * COLS for _ in range(ROWS)]
current_piece = new_piece()
drop_timer = 0
DROP_INTERVAL = 500  # ms between auto-drop

while running:
    dt = clock.tick(60)
    drop_timer += dt

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_LEFT and valid_position(
                {**current_piece, "x": current_piece["x"] - 1}, board
            ):
                current_piece["x"] -= 1
            if event.key == pygame.K_RIGHT and valid_position(
                {**current_piece, "x": current_piece["x"] + 1}, board
            ):
                current_piece["x"] += 1
            if event.key == pygame.K_DOWN and valid_position(
                {**current_piece, "y": current_piece["y"] + 1}, board
            ):
                current_piece["y"] += 1
            if event.key == pygame.K_UP:
                rotated = list(zip(*current_piece["shape"][::-1]))
                rotated_shape = [list(row) for row in rotated]
                if valid_position(
                    {**current_piece, "shape": rotated_shape}, board
                ):
                    current_piece["shape"] = rotated_shape

    if drop_timer >= DROP_INTERVAL:
        drop_timer = 0
        if valid_position({**current_piece, "y": current_piece["y"] + 1}, board):
            current_piece["y"] += 1
        else:
            lock_piece(current_piece, board)
            board = clear_lines(board, SCREEN)
            current_piece = new_piece()
            if game_over(board):
                board = [[0] * COLS for _ in range(ROWS)]
                current_piece = new_piece()

    SCREEN.fill(BLACK)

    # Draw play area background
    play_area_rect = pygame.Rect(0, GRID_OFFSET_Y, WIDTH, ROWS * BLOCK_SIZE)
    pygame.draw.rect(SCREEN, (20, 20, 20), play_area_rect)

    # Draw locked cells
    for row in range(ROWS):
        for col in range(COLS):
            if board[row][col]:
                rect = pygame.Rect(col * BLOCK_SIZE, row * BLOCK_SIZE + GRID_OFFSET_Y, BLOCK_SIZE, BLOCK_SIZE)
                pygame.draw.rect(SCREEN, board[row][col], rect)
                pygame.draw.rect(SCREEN, WHITE, rect, 1)

    draw_grid()
    draw_piece(current_piece)

    # Score / instructions area below play field
    instructions_y = GRID_OFFSET_Y + ROWS * BLOCK_SIZE + 20
    SCREEN.fill(BLACK, (0, instructions_y, WIDTH, HEIGHT - instructions_y))

    font = pygame.font.Font(None, 24)
    label = font.render("← → move  ↑ rotate  ↓ soft drop", True, WHITE)
    SCREEN.blit(label, (10, instructions_y + 10))

    pygame.display.flip()

pygame.quit()
sys.exit()
