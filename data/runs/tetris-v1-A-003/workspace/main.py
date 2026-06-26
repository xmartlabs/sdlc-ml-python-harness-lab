import pygame
import sys

pygame.init()

WIDTH = 300
HEIGHT = 600
BLOCK_SIZE = 30
CELLS = WIDTH // BLOCK_SIZE, HEIGHT // BLOCK_SIZE

screen = pygame.display.set_mode(CELLS)
pygame.display.set_caption("Tetris")

clock = pygame.time.Clock()
running = True

while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False

    screen.fill((0, 0, 0))
    pygame.display.flip()
    clock.tick(60)

pygame.quit()
sys.exit()
