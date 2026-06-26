import pygame
import sys
import random

def main():
    pygame.init()
    screen = pygame.display.set_mode((400, 500))
    pygame.display.set_caption('Tetris')
    running = True

    # Grid dimensions
    grid_width, grid_height = 10, 20
    cell_size = 20

    # Grid to store locked blocks (True means occupied)
    grid = [[False for _ in range(grid_width)] for _ in range(grid_height)]

    # Tetromino shapes (each shape is a list of (dx, dy) relative to a reference point)
    # Standard Tetris pieces
    shapes = [
        # I piece (4 blocks in a row)
        [(0, 0), (1, 0), (2, 0), (3, 0)],
        # O piece (2x2 square)
        [(0, 0), (1, 0), (0, 1), (1, 1)],
        # T piece
        [(0, 0), (1, 0), (2, 0), (1, 1)],
        # S piece
        [(1, 0), (2, 0), (0, 1), (1, 1)],
        # Z piece
        [(0, 0), (1, 0), (1, 1), (2, 1)],
        # J piece
        [(0, 0), (0, 1), (1, 1), (2, 1)],
        # L piece
        [(2, 0), (0, 1), (1, 1), (2, 1)],
    ]

    # Function to spawn a new block at the top center
    def spawn_shape():
        shape = random.choice(shapes)
        # For now, we only have one shape, but we keep it general
        # We want to place the shape so that it is centered at the top.
        # We'll find the bounding box of the shape to center it.
        if not shape:
            return [(grid_width // 2, 0)]
        min_dx = min(dx for dx, dy in shape)
        max_dx = max(dx for dx, dy in shape)
        min_dy = min(dy for dx, dy in shape)
        max_dy = max(dy for dx, dy in shape)
        shape_width = max_dx - min_dx + 1
        # We want the shape to appear such that its top row is at y = 0 (just visible)
        # and it is horizontally centered.
        # We'll set the reference point such that the minimum dx and min dy are at (ref_x, ref_y)
        # and then the actual blocks are (ref_x + dx, ref_y + dy).
        # We want the topmost block (min_dy) to be at y = 0, so ref_y = -min_dy.
        # We want the shape to be centered horizontally: the leftmost block at x = 0? 
        # Actually, we want the shape to be centered in the grid.
        # Let's compute the x offset so that the shape is centered.
        # The leftmost block will be at ref_x + min_dx.
        # We want the center of the shape to be at grid_width // 2.
        # The center of the shape in x is (min_dx + max_dx) / 2.
        # So we set: ref_x + (min_dx + max_dx)/2 = grid_width // 2
        # => ref_x = grid_width // 2 - (min_dx + max_dx)/2
        # But we want integer coordinates, so we'll use integer division and adjust.
        # Alternatively, we can set the reference point to be the bottom-left? 
        # To keep it simple, we'll just set the reference point to (0,0) for the shape and then
        # adjust the spawn position so that the shape is centered and its top is at y=0.
        # We'll set the reference point to be the minimum x and minimum y of the shape.
        ref_x = -min_dx
        ref_y = -min_dy
        # Now shift the shape so that it is centered horizontally and its top is at y=0.
        # We want the shape's top row (min_dy) to be at y=0 -> we already set ref_y = -min_dy, so when we add ref_y, we get 0.
        # For x, we want the shape to be centered. The current ref_x makes the leftmost block at x=0.
        # We want to shift the entire shape so that its center is at x = grid_width // 2.
        # The center of the shape in its current coordinates (with ref_x,ref_y applied) is:
        #   center_x = (min_dx + max_dx)/2 + ref_x = (min_dx + max_dx)/2 - min_dx = (max_dx - min_dx)/2
        # We want this to be grid_width // 2.
        # So we need to add an offset: offset_x = grid_width // 2 - (max_dx - min_dx)//2
        # But note: we already set ref_x = -min_dx, so the actual x of a block is: ref_x + dx + offset_x? 
        # Actually, we are going to set the reference point to (spawn_x, spawn_y) and then the block is at (spawn_x + dx, spaw_y + dy).
        # We want to choose spawn_x and spawn_y such that:
        #   For all (dx,dy) in shape: 
        #       x = spawn_x + dx
        #       y = spawn_y + dy
        #   and we want the minimum y to be 0 and the x to be centered.
        # We set spawn_y = -min_dy   (so that the minimum y becomes 0)
        # We set spawn_x such that the average x of the shape is grid_width//2.
        #   average x = (sum of (spawn_x + dx)) / len(shape) = spawn_x + (sum of dx)/len(shape)
        #   We want this to be grid_width//2.
        #   => spawn_x = grid_width//2 - (sum of dx)/len(shape)
        # But we want integer coordinates. We'll use integer division and hope it's okay.
        # Alternatively, we can set the spawn_x to be the leftmost x such that the shape fits and is centered.
        # Given time, we'll use a simple approach: set spawn_x = grid_width // 2 and then adjust the shape by subtracting the min_dx to shift leftmost to 0, then center.
        # Let's do:
        #   We'll shift the shape so that its minimum x is 0: subtract min_dx from each dx.
        #   Then the width is max_dx - min_dx.
        #   Then we set spawn_x = (grid_width - width) // 2
        #   And we set spawn_y = -min_dy   (so that the minimum y is 0)
        #   Then the actual block positions are: (spawn_x + (dx - min_dx), spawn_y + (dy - min_dy))
        # But note: we already have the shape defined as (dx,dy). We can precompute a normalized shape for each shape that has min_dx=0 and min_dy=0.
        # For simplicity, we will define our shapes such that min_dx=0 and min_dy=0. Then we can set:
        #   spawn_x = (grid_width - width) // 2
        #   spawn_y = 0
        #   and the block positions are (spawn_x + dx, spawn_y + dy)
        #
        # Since we are only using the single block shape for now, we can skip this complexity.
        # We'll just use the shape as is and set spawn_x = grid_width // 2, spawn_y = 0.
        # This will work for the single block shape.
        spawn_x = grid_width // 2
        spawn_y = 0
        return [(spawn_x + dx, spawn_y + dy) for (dx, dy) in shape]

    # Initialize the first block
    current_blocks = spawn_shape()

    fall_time = 0
    fall_speed = 500  # milliseconds

    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            # Handle keyboard input for lateral movement and soft drop
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_LEFT:
                    # Try moving left
                    new_blocks = [(x-1, y) for (x, y) in current_blocks]
                    if all(0 <= x < grid_width and y < grid_height and (y < 0 or not grid[y][x]) for (x, y) in new_blocks):
                        current_blocks = new_blocks
                if event.key == pygame.K_RIGHT:
                    # Try moving right
                    new_blocks = [(x+1, y) for (x, y) in current_blocks]
                    if all(0 <= x < grid_width and y < grid_height and (y < 0 or not grid[y][x]) for (x, y) in new_blocks):
                        current_blocks = new_blocks
                if event.key == pygame.K_DOWN:
                    # Soft drop: move down one step if possible
                    new_blocks = [(x, y+1) for (x, y) in current_blocks]
                    if all(0 <= x < grid_width and y < grid_height and (y < 0 or not grid[y][x]) for (x, y) in new_blocks):
                        current_blocks = new_blocks
                if event.key == pygame.K_UP:
                    # Try to rotate clockwise
                    if len(current_blocks) > 0:
                        pivot = current_blocks[0]
                        new_blocks = []
                        for (dx, dy) in current_blocks:
                            rel_x = dx - pivot[0]
                            rel_y = dy - pivot[1]
                            # Rotate 90 degrees clockwise: (x, y) -> (y, -x)
                            new_rel_x = rel_y
                            new_rel_y = -rel_x
                            new_x = pivot[0] + new_rel_x
                            new_y = pivot[1] + new_rel_y
                            new_blocks.append((new_x, new_y))
                        if all(0 <= x < grid_width and y < grid_height and (y < 0 or not grid[y][x]) for (x, y) in new_blocks):
                            current_blocks = new_blocks

        # Automatic falling
        current_time = pygame.time.get_ticks()
        if current_time - fall_time > fall_speed:
            # Try moving down
            new_blocks = [(x, y+1) for (x, y) in current_blocks]
            if all(0 <= x < grid_width and y < grid_height and (y < 0 or not grid[y][x]) for (x, y) in new_blocks):
                current_blocks = new_blocks
                fall_time = current_time
            else:
                # Lock the block in place
                for (x, y) in current_blocks:
                    if 0 <= y < grid_height:  # Only lock if within the grid
                        grid[y][x] = True

                # Clear completed lines
                # Create a new grid without the completed lines
                new_grid = [row for row in grid if not all(row)]
                # Add empty rows at the top to maintain the grid height
                while len(new_grid) < grid_height:
                    new_grid.insert(0, [False]*grid_width)
                grid = new_grid

                # Spawn a new block
                current_blocks = spawn_shape()
                # Optional: check if the new block collides immediately (game over)
                # We'll skip for now

        # Drawing
        screen.fill((0, 0, 0))

        # Draw grid lines
        for x in range(grid_width):
            for y in range(grid_height):
                rect = pygame.Rect(x * cell_size, y * cell_size, cell_size, cell_size)
                pygame.draw.rect(screen, (40, 40, 40), rect, 1)

        # Draw locked blocks
        for y in range(grid_height):
            for x in range(grid_width):
                if grid[y][x]:
                    rect = pygame.Rect(x * cell_size, y * cell_size, cell_size, cell_size)
                    pygame.draw.rect(screen, (255, 255, 255), rect)

        # Draw falling block
        for (x, y) in current_blocks:
            if 0 <= y < grid_height:  # Only draw if within the grid (visible)
                rect = pygame.Rect(x * cell_size, y * cell_size, cell_size, cell_size)
                pygame.draw.rect(screen, (255, 0, 0), rect)

        pygame.display.flip()

    pygame.quit()
    sys.exit()

if __name__ == '__main__':
    main()