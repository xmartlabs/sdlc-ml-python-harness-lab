import pygame, random, os
pygame.init()
from tetris_v1_K_057.main import GameManager, SHAPES, GRID_HEIGHT

game = GameManager()
for i in range(4):
    shape_key = game.active_shape.shape_key if game.active_shape else "EMPTY"
    grid_full_rows = sum(1 for y in range(GRID_HEIGHT) if all(game.grid[y][x] is not None for x in range(10)))
    print(f'Round {i}: Shape={shape_key}, Grid full rows={grid_full_rows}')
    game.drop_one()
    blocked = any(c.y+1 >= GRID_HEIGHT or game.grid[c.y+1][c.x] is not None for c in game.active_shape.cells) if game.active_shape else False
    print(f'  Blocked={blocked}', end='')
    if blocked:
        game.lock_into_grid()
        print(' -> locked')
    else:
        print(' -> not locked (need to force lock)')

print('Final grid state:')
for y, row in enumerate(game.grid):
    line = ''.join('.' if cell is None or str(cell[0]) in 'IJLOSZ' or (str(cell[0]) == "T" and cell[1] == 0x800080) else '#' for cell in row)
    print(f'{y}: {line}')
