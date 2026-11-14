## Phase 3 of 5 — the 3D board and the mouse

The rules engine from phases 1–2 is in the workspace. Read it before you change it,
and do not change its behaviour.

Build the main scene, exactly as the scene contract in the task states:

- a 3D board seen through a `Camera3D`, every square visible in 1280×720, light and
  dark squares clearly different;
- the board node in the group `chess_board`, with `square_to_world(square)`;
- every piece a `Node3D` in the group `chess_pieces`, with metadata `square` and
  `piece`, standing on its square, distinguishable by shape and colour;
- the pieces always mirror the `Chess` position, including after `load_fen` is called
  from outside the scene (listen to `position_changed`);
- click to move: select a piece of the side to move, highlight its legal targets,
  play on a legal target, ignore illegal ones, promote to a queen by click;
- a visible `Label` in the group `status_label` naming whose turn it is, and `check`,
  `checkmate` or `stalemate` when they apply.

Verify with `godot-render`: take a screenshot of the start position, play a few moves
by injecting mouse clicks at the projected square centres (including castling and en
passant), and look at the result.

**Stop when phase 3 is done and verified.** Do not build the AI yet.
