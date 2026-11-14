# Engineering harness (read before you touch code)

You are operating inside a governed repository. Before writing code, ground
yourself in its harness — this is mandatory, not optional:

1. Read the governance docs at the repo root:
   - `.github/architecture.md`
   - `.github/standards.md`
   - `.github/domain-boundaries.md`
2. Read project memory for prior context:
   - `memory/context.md`
   - `memory/learnings.md`
3. Follow the working loop: Ground → Plan → Delegate → Verify → Compound.

Drive the work with the repo's skills, not ad-hoc improvisation:

- Use the **plan_and_execute_feature** skill to turn the task into explicit,
  phased work before you implement it.
- Use the **systematic_debugging** skill when something does not behave as
  expected — reproduce → isolate → hypothesize → fix → verify. No blind edits.
- Use the **verify_changes** skill to confirm the work before you consider it
  done.

Apply the harness to *how* you solve the task. The task itself, its scope and
its acceptance criteria are stated below and are not changed by this preamble.

---

# Task: build a playable 3D chess game in Godot

Build a complete chess game in **Godot 4.7** (GDScript): a 3D board a person can play
with the mouse, a rules engine that is exactly right, and a computer opponent.

All files go inside `{workspace}/`. The Godot project lives at the workspace root:
`{workspace}/project.godot`. The `godot` command (4.7.2) is installed; use it to run
and test your work. There is no GPU: `godot --headless` runs logic only, and
`godot-render` runs the game on a software renderer (it accepts the same arguments as
`godot`), which is what you need to take a screenshot or check what is on screen.

## What is being judged

An automated grader loads your project, calls the API below with positions and games
it generates **fresh for each run** (you cannot know them in advance), clicks on your
3D board through the camera, reads back the pieces in the scene, and compares every
answer against an independent chess engine. Exact answers only: a rules engine that is
right in 99% of positions fails the checks that probe the other 1%. The rules that
most engines get wrong are the ones probed hardest — castling through or out of check,
en passant (including one that would expose the king), pins, double check,
under-promotion, standard algebraic notation with disambiguation, threefold
repetition, the fifty-move rule and insufficient material.

## Hard constraints

- **Your own code only.** GDScript, scenes and resources you write. No `addons/`, no
  editor plugins, no GDExtension, no native libraries, no binaries, nothing downloaded
  (no chess engines, no move generators, no asset packs). Allowed file types: `.gd`,
  `.tscn`, `.tres`, `.gdshader`, `project.godot`, `.cfg`, `.png`, `.svg`, `.json`,
  `.md`, `.txt`, and the `.import`/`.uid` files Godot creates.
- **Renderer:** `project.godot` must set
  `rendering/renderer/rendering_method="gl_compatibility"` (the software renderer only
  supports Compatibility). A `run/main_scene` must be set.
- **Sky on the software renderer:** a `Sky` used for ambient light or reflections
  (the defaults when the background is a sky) crashes Godot 4.7 under `godot-render`
  part of the time. Show a sky as the background if you like, but set the
  environment's `ambient_light_source` to `Color` and its `reflected_light_source` to
  `Disabled`. A crash in the rendered game fails every check of the scene.
- **Pieces may be built from primitive meshes** (cylinders, spheres, boxes); they must
  be distinguishable by shape and colour.

## The API contract (exact)

An **autoload singleton named `Chess`** (Project Settings → Autoload, node name
`Chess`) holds the game state — the single source of truth the scene renders. It must
provide these methods and signal. Squares are `a1`…`h8`; moves in UCI are `e2e4`,
`e7e8q` (promotion piece letter `q r b n`); castling in UCI is the king's move
(`e1g1`).

| Member | Contract |
|---|---|
| `signal position_changed` | Emitted after every change of position (`load_fen`, `play`, `undo`). |
| `load_fen(fen: String) -> bool` | Set the position from a FEN. Clears the move history. |
| `fen() -> String` | The current position as a full six-field FEN. Write the en passant square only when an en passant capture is actually legal (otherwise `-`). |
| `legal_moves() -> PackedStringArray` | Every legal move, in UCI, in any order. |
| `play(move: String) -> String` | Play a move given in UCI **or** SAN (`Nf3`, `exd5`, `O-O`, `e8=Q+`). Return the move's SAN (with `+`/`#`), or `""` and change nothing if it is illegal or unreadable. |
| `undo() -> bool` | Take back the last move played; exact restoration (castling rights, en passant, clocks). |
| `status() -> String` | One of `ongoing`, `check`, `checkmate`, `stalemate`, `insufficient_material`, `fifty_moves`, `threefold_repetition` — in that order of precedence after `checkmate`/`stalemate`. |
| `result() -> String` | `1-0`, `0-1`, `1/2-1/2`, or `*` while the game goes on. |
| `history_san() -> PackedStringArray` | The SAN of every move played since the last `load_fen`. |
| `perft(depth: int) -> int` | The number of leaf nodes of the legal move tree to `depth` from the current position (standard perft). Must not change the position. |
| `best_move(budget_ms: int) -> String` | The computer's chosen move in UCI, **without playing it**, within `budget_ms` milliseconds. It must find a forced mate in one and in two. |
| `turn() -> String` | `white` or `black`. |
| `piece_at(square: String) -> String` | `P N B R Q K` for white, `p n b r q k` for black, `""` for empty. |
| `ai_side` (property) | `"white"`, `"black"` or `""`: the side the computer plays in the game scene. When it is that side's turn the scene asks `best_move` and plays it. |

`status()` follows FIDE: threefold repetition counts positions (placement, side to
move, castling rights, en passant) and is reported when the current position has
occurred three times; `fifty_moves` when the halfmove clock reaches 100;
`insufficient_material` for K vs K, K+minor vs K, and kings with bishops all on one
colour.

## The scene contract (exact)

- The main scene shows the board in 3D through a `Camera3D`, with every square visible
  in a 1280×720 window. Light and dark squares are clearly different in brightness.
- The board node is in the group **`chess_board`** and has
  `square_to_world(square: String) -> Vector3`: the centre of that square on the board
  surface, in global coordinates. Squares lie on a regular 8×8 grid.
- Every piece on the board is a `Node3D` in the group **`chess_pieces`**, with metadata
  `square` (e.g. `"e1"`) and `piece` (the `piece_at` letter, e.g. `"K"`), standing on
  that square. The pieces always mirror `Chess`'s position — including after
  `load_fen` is called from outside the scene.
- **Click to move:** a left click on one of the side-to-move's pieces selects it
  (highlight its legal destinations); a left click on a legal destination plays the
  move. Clicking an illegal destination plays nothing. A pawn clicked to the last rank
  promotes to a queen. Clicks are on the board surface; the grader clicks the screen
  point of `square_to_world(...)`.
- A visible `Label` in the group **`status_label`** says whose turn it is and, when it
  applies, contains the word `check`, `checkmate` or `stalemate`.
- Running the game with `godot --path {workspace} -- --ai=black` plays against the
  computer as white.

## How the work is paced: five phases

The whole contract above is the destination. You build it in **five phases, one per
step**; each step ends when its phase is done and verified, and the next phase is
given to you in the next step. Everything is graded at every step, so a phase done
well is visible on its own.

1. **Rules core** — this step.
2. Notation and the end of the game.
3. The 3D board and the mouse.
4. The computer opponent.
5. Finish: play it end to end and polish.

Test your own work as you go: write your own GDScript test scripts and run them with
`godot --headless --script`, and check what is on screen with `godot-render`.

## Phase 1 of 5 — the rules core (do only this now)

Create the Godot project (`project.godot` with the Compatibility renderer and the
`Chess` autoload) and implement, in the `Chess` autoload:

- `load_fen`, `fen`, `legal_moves`, `play` (UCI input is enough for now), `undo`,
  `perft`, `turn`, `piece_at`, and the `position_changed` signal;
- every rule of movement exactly: castling (never through, into or out of check),
  en passant (including the one that would expose the king), pins, double check,
  promotion to all four pieces;
- the FEN fields exactly, including the halfmove and fullmove clocks.

Verify with perft from the starting position (depth 1–4: 20, 400, 8902, 197281) and
from at least one position that exercises castling, en passant and promotion.

**Stop when phase 1 is done and verified.** Do not build the scene or the AI yet;
state what you built and how you verified it.
