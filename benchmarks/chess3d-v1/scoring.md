# chess3d-v1 scoring

The grade is the shipped grader: `test_pass_rate` over 39 independent checks, every
one decided against python-chess on positions generated for the run, all objective.
The rubric exists so the LLM judge and a human reviewer land on the same ladder; every
level is tagged with what can establish it — `[check]` an automated check, `[diff]`
the code itself (quoted), `[manual]` only a human playing the game.

| Level | Meaning | Evidence |
|-------|---------|----------|
| 0 | No Godot project, or nothing that loads | [check] |
| 1 | A project that loads, but the `Chess` autoload or its contract is missing (`contract` fails) | [check] |
| 2 | The contract exists, with only own code on the Compatibility renderer, and round-trips the start FEN (`contract`, `own_code`, `renderer`, `fen_roundtrip`) | [check] |
| 4 | Legal moves are right in ordinary positions and shallow perft agrees (`legal_moves`, `perft_shallow`, `fen_tracking`, `illegal_refused`) | [check] |
| 6 | Every special rule is exact: en passant, castling, pins, double check, promotion, deep and random perft, undo (`en_passant`, `castling`, `pins_and_checks`, `promotion`, `perft_deep`, `perft_random`, `undo`) | [check] |
| 7 | Notation and game end are exact: SAN out and in with disambiguation, clocks, history, checkmate, stalemate and every draw rule (`san_*`, `clocks`, `history`, `mate_and_stalemate`, `draw_rules`, `status_everywhere`) | [check] |
| 8 | A 3D board a person can use: it renders, every square is on screen and on a grid, light and dark differ, pieces stand where the engine says, and the mouse plays a line with en passant and both castles (`scene_loads` … `click_moves`, `click_illegal`, `render`) | [check] |
| 9 | The board follows the engine, promotes by click, names check/checkmate/stalemate, and runs within the frame budget (`follow_engine`, `click_promotion`, `status_label`, `frame_time`) | [check] |
| 10 | The computer plays: legal suggestions within budget, forced mates in one and two found, and it answers a mouse move on the board (`ai_legal`, `ai_mate_in_one`, `ai_mate_in_two`, `ai_in_scene`), with no third-party code anywhere | [check] + [diff] |

Level 10 needs the diff as well as the checks: `own_code` refuses addons, native
libraries and binaries by file type, and the judge must confirm from the code that the
engine and the search were written, not pasted from a known engine.

Calibrated in the hardened validation lane (no network, all capabilities dropped,
4 GB): an empty workspace passes 0/39, a plausible build without en passant,
castling, SAN disambiguation, the halfmove clock and a search passes 24/39, and the
reference passes 39/39. The reference is kept outside the repository.

Not graded here, and left to a person in the **Play** sandbox: whether the game is
pleasant to play — piece design, animation, camera, feedback.
