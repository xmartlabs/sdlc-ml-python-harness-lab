import pygame
import sys
import random

pygame.init()

BLOCK_SIZE = 30
COLS = 10
ROWS = 20
BOARD_LEFT = 0
BOARD_TOP = 0

WIDTH = COLS * BLOCK_SIZE
HEIGHT = ROWS * BLOCK_SIZE

screen = pygame.display.set_mode((WIDTH + 200, HEIGHT))
pygame.display.set_caption("Tetris")
clock = pygame.time.Clock()

SIDE_X = WIDTH + 20
SIDE_WIDTH = 180

# Colors
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
GRID_COLOR = (40, 40, 40)

TETROMINOES = {
    'I': [(0,1),(1,1),(2,1),(3,1)],
    'O': [(0,0),(1,0),(0,1),(1,1)],
    'T': [(0,1),(1,0),(1,1),(2,1)],
    'S': [(1,0),(2,0),(0,1),(1,1)],
    'Z': [(0,0),(1,0),(1,1),(2,1)],
    'J': [(0,0),(0,1),(1,1),(2,1)],
    'L': [(2,0),(0,1),(1,1),(2,1)],
}

TETROMINO_COLORS = {
    'I': (0, 240, 240),
    'O': (240, 240, 0),
    'T': (160, 0, 240),
    'S': (0, 240, 0),
    'Z': (240, 20, 20),
    'J': (20, 20, 240),
    'L': (240, 160, 0),
}

PIECE_TYPES = list(TETROMINOES.keys())


class Piece:
    def __init__(self, piece_type=None):
        self.piece_type = piece_type or random.choice(PIECE_TYPES)
        self.blocks = [list(b) for b in TETROMINOES[self.piece_type]]
        self.x = COLS // 2 - 2
        self.y = 0

    def rotate(self):
        rotated = [(1 - b[1], b[0]) for b in self.blocks]
        min_x = min(b[0] for b in rotated)
        min_y = min(b[1] for b in rotated)
        self.blocks = [[b[0] - min_x, b[1] - min_y] for b in rotated]


def collides(piece, board, dx=0, dy=0):
    for bx, by in piece.blocks:
        nx = piece.x + bx + dx
        ny = piece.y + by + dy
        if nx < 0 or nx >= COLS or ny >= ROWS:
            return True
        if ny >= 0 and board[ny][nx]:
            return True
    return False


def lock_piece(piece, board):
    for bx, by in piece.blocks:
        nx = piece.x + bx
        ny = piece.y + by
        if 0 <= ny < ROWS and 0 <= nx < COLS:
            board[ny][nx] = TETROMINO_COLORS[piece.piece_type]


def draw_block(x, y, color):
    pygame.draw.rect(screen, color, (BOARD_LEFT + x * BLOCK_SIZE, BOARD_TOP + y * BLOCK_SIZE, BLOCK_SIZE - 1, BLOCK_SIZE - 1))
    pygame.draw.rect(screen, WHITE, (BOARD_LEFT + x * BLOCK_SIZE, BOARD_TOP + y * BLOCK_SIZE, BLOCK_SIZE - 1, BLOCK_SIZE - 1), 1)


def draw_grid():
    for y in range(ROWS):
        for x in range(COLS):
            pygame.draw.rect(screen, GRID_COLOR, (BOARD_LEFT + x * BLOCK_SIZE, BOARD_TOP + y * BLOCK_SIZE, BLOCK_SIZE - 1, BLOCK_SIZE - 1), 1)


def draw_piece(piece):
    for bx, by in piece.blocks:
        draw_block(piece.x + bx, piece.y + by, TETROMINO_COLORS[piece.piece_type])


def get_current_piece():
    return Piece()


def get_ghost_y(piece, board):
    dy = 0
    while not collides(piece, board, dx=0, dy=dy + 1):
        dy += 1
    return dy


def draw_ghost_piece(piece, board):
    ghost_dy = get_ghost_y(piece, board)
    color = TETROMINO_COLORS[piece.piece_type]
    for bx, by in piece.blocks:
        gx = piece.x + bx
        gy = piece.y + by + ghost_dy
        if 0 <= gy < ROWS and 0 <= gx < COLS:
            pygame.draw.rect(screen, color, (BOARD_LEFT + gx * BLOCK_SIZE, BOARD_TOP + gy * BLOCK_SIZE, BLOCK_SIZE - 1, BLOCK_SIZE - 1), 1)


def draw_next_piece(piece):
    preview_x = SIDE_X + 30
    preview_y = 120
    piece_blocks = piece.blocks
    min_x = min(b[0] for b in piece_blocks)
    max_x = max(b[0] for b in piece_blocks)
    min_y = min(b[1] for b in piece_blocks)
    max_y = max(b[1] for b in piece_blocks)
    piece_w = max_x - min_x + 1
    piece_h = max_y - min_y + 1
    offset_x = (6 - piece_w) // 2
    offset_y = (6 - piece_h) // 2
    for bx, by in piece_blocks:
        px = preview_x + (bx - min_x + offset_x) * BLOCK_SIZE
        py = preview_y + (by - min_y + offset_y) * BLOCK_SIZE
        pygame.draw.rect(screen, TETROMINO_COLORS[piece.piece_type], (px, py, BLOCK_SIZE - 1, BLOCK_SIZE - 1))
        pygame.draw.rect(screen, WHITE, (px, py, BLOCK_SIZE - 1, BLOCK_SIZE - 1), 1)


running = True
board = [[BLACK for _ in range(COLS)] for _ in range(ROWS)]
current_piece = get_current_piece()
next_piece = get_current_piece()
drop_timer = 0
DROP_INTERVAL = 30  # frames
score = 0
lines_cleared_total = 0
level = 1

LINE_SCORES = {1: 100, 2: 300, 3: 500, 4: 800}

LEVEL_LINES = 10


def clear_lines(board):
    global score, lines_cleared_total, level
    full_rows = [y for y in range(ROWS) if all(board[y][x] != BLACK for x in range(COLS))]
    num_lines = len(full_rows)
    if num_lines > 0:
        score += LINE_SCORES.get(num_lines, 0) * (lines_cleared_total + 1)
        lines_cleared_total += num_lines
        level = lines_cleared_total // LEVEL_LINES + 1
        for row in full_rows:
            board.pop(row)
            board.insert(0, [BLACK for _ in range(COLS)])
    return num_lines


while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_LEFT and not collides(current_piece, board, dx=-1, dy=0):
                current_piece.x -= 1
            elif event.key == pygame.K_RIGHT and not collides(current_piece, board, dx=1, dy=0):
                current_piece.x += 1
            elif event.key == pygame.K_DOWN and not collides(current_piece, board, dx=0, dy=1):
                current_piece.y += 1
            elif event.key == pygame.K_UP:
                old_blocks = [list(b) for b in current_piece.blocks]
                current_piece.rotate()
                if collides(current_piece, board):
                    current_piece.blocks = old_blocks
            elif event.key == pygame.K_SPACE:
                drop_dist = 0
                while not collides(current_piece, board, dx=0, dy=drop_dist + 1):
                    drop_dist += 1
                current_piece.y += drop_dist
                lock_piece(current_piece, board)
                clear_lines(board)
                current_piece = next_piece
                next_piece = get_current_piece()
                if collides(current_piece, board):
                    running = False

    keys = pygame.key.get_pressed()
    if keys[pygame.K_LEFT] and not collides(current_piece, board, dx=-1, dy=0):
        current_piece.x -= 1
    elif keys[pygame.K_RIGHT] and not collides(current_piece, board, dx=1, dy=0):
        current_piece.x += 1

    drop_timer += 1
    current_interval = max(5, DROP_INTERVAL - (level - 1) * 3)
    if drop_timer >= current_interval:
        drop_timer = 0
        if not collides(current_piece, board, dx=0, dy=1):
            current_piece.y += 1
        else:
            lock_piece(current_piece, board)
            clear_lines(board)
            current_piece = next_piece
            next_piece = get_current_piece()
            if collides(current_piece, board):
                running = False

    screen.fill(BLACK)

    for y in range(ROWS):
        for x in range(COLS):
            if board[y][x] != BLACK:
                draw_block(x, y, board[y][x])

    draw_grid()
    draw_ghost_piece(current_piece, board)
    draw_piece(current_piece)

    pygame.draw.rect(screen, (20, 20, 20), (SIDE_X, 0, SIDE_WIDTH, HEIGHT))
    side_font = pygame.font.Font(None, 28)
    side_text = side_font.render("Next:", True, WHITE)
    screen.blit(side_text, (SIDE_X + 10, 80))
    draw_next_piece(next_piece)

    level_text = side_font.render(f"Level: {level}", True, WHITE)
    screen.blit(level_text, (SIDE_X + 10, 40))

    font = pygame.font.Font(None, 36)
    score_text = font.render(f"Score: {score}", True, WHITE)
    screen.blit(score_text, (10, HEIGHT - 40))

    pygame.display.flip()
    clock.tick(60)

pygame.quit()
sys.exit()
