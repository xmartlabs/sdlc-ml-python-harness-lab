#!/usr/bin/env python3
"""Grader for pr-review-v1: a review is scored against a hidden answer key.

Usage:
    grade.py check <name> <workspace> <run_id>

Exit codes: 0 pass, 1 fail, 124 the lab could not measure (ADR-0062).

The agent writes ``review.json`` in the workspace. Each check reads it and
decides one behaviour:

* ``format``     the review parses and has the contract's shape
* ``verdict``    the merge verdict is the right one
* ``untouched``  every file of the PR under review is byte-identical
* ``kNN``        one per key entry: a real defect was found (located within a
                 line window, or inside the function that holds it), or a decoy
                 was *not* reported at medium or above (line window only)
* ``precision``  most medium-or-higher findings are real defects
* ``canary``     the answer key's secret token appears nowhere the agent
                 produced (its transcripts, its workspace)

Check names are opaque on purpose: ``task.yaml`` is readable by the agent, and
``k03`` says nothing about what it looks for.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CASE = HERE.parent
REPO = CASE.parent.parent
KEY_PATH = HERE / "key.json"
STARTER = CASE / "starter"
REVIEW_FILE = "review.json"

SEVERITIES = ("low", "medium", "high", "critical")
BLOCKING = frozenset({"medium", "high", "critical"})
VERDICTS = ("approve", "request_changes")
# Files the agent may add next to the PR without it counting as an edit.
IGNORED_PARTS = frozenset({"__pycache__", ".pytest_cache", ".lab-grade"})

PASS, FAIL, INCONCLUSIVE = 0, 1, 124


class ReviewError(Exception):
    """The review is missing or does not follow the contract."""


def enclosing_spans(
    source: str, lines: list[int], max_lines: int
) -> list[tuple[int, int]]:
    """The innermost function around each line, when it is at most ``max_lines`` long.

    A reviewer who explains a defect at its function's ``def`` (or at a sibling
    line of the same short function) has located it; a three-line window around
    one statement is narrower than how reviews point at code.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    functions = [
        (node.lineno, node.end_lineno or node.lineno)
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]
    spans: set[tuple[int, int]] = set()
    for line in lines:
        around = [f for f in functions if f[0] <= line <= f[1]]
        if around:
            start, end = min(around, key=lambda f: f[1] - f[0])
            if end - start + 1 <= max_lines:
                spans.add((start, end))
    return sorted(spans)


def resolve_spans(key: dict, starter: Path = STARTER) -> dict:
    """Give every *defect* the spans of the functions that hold it. Decoys keep
    the strict window: a finding elsewhere in a decoy's function is not a
    finding on the decoy."""
    max_lines = int(key.get("span_max_lines", 0))
    for entry in key["defects"]:
        source = starter / entry["file"]
        entry["spans"] = (
            enclosing_spans(
                source.read_text(encoding="utf-8"), entry["lines"], max_lines
            )
            if max_lines and source.is_file()
            else []
        )
    return key


def load_key(path: Path = KEY_PATH) -> dict:
    return resolve_spans(json.loads(path.read_text(encoding="utf-8")))


def normalize_path(raw: object) -> str:
    """``./shop/x.py``, ``a/shop/x.py``, ``/abs/workspace/shop/x.py`` → ``shop/x.py``."""
    text = str(raw or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    for prefix in ("a/", "b/"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    for anchor in ("shop/", "tests/"):
        index = text.find("/" + anchor)
        if text.startswith("/") and index >= 0:
            text = text[index + 1 :]
    return text


def _line(raw: object) -> int:
    if isinstance(raw, bool):
        raise ReviewError("line must be a number")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    raise ReviewError(f"line must be a positive integer, got {raw!r}")


def parse_review(workspace: Path) -> dict:
    """The review, validated against the contract, with findings normalised."""
    path = workspace / REVIEW_FILE
    if not path.is_file():
        raise ReviewError(f"{REVIEW_FILE} is missing from the workspace")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ReviewError(f"{REVIEW_FILE} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ReviewError("the review must be a JSON object")
    verdict = str(data.get("verdict", "")).strip().lower()
    if verdict not in VERDICTS:
        raise ReviewError(f"verdict must be one of {VERDICTS}, got {verdict!r}")
    findings = data.get("findings")
    if not isinstance(findings, list):
        raise ReviewError("findings must be a list")
    clean = []
    for index, item in enumerate(findings):
        if not isinstance(item, dict):
            raise ReviewError(f"finding {index} is not an object")
        file = normalize_path(item.get("file"))
        if not file:
            raise ReviewError(f"finding {index} has no file")
        line = _line(item.get("line"))
        if line < 1:
            raise ReviewError(f"finding {index} has line {line}")
        severity = str(item.get("severity", "")).strip().lower()
        if severity not in SEVERITIES:
            raise ReviewError(
                f"finding {index} severity must be one of {SEVERITIES}, got {severity!r}"
            )
        clean.append({"file": file, "line": line, "severity": severity})
    return {"verdict": verdict, "findings": clean}


def hits(finding: dict, entry: dict, window: int) -> bool:
    """Whether ``finding`` points at ``entry``: same file, and within the line
    window or inside one of the entry's function spans (defects only)."""
    if finding["file"] != entry["file"]:
        return False
    if any(start <= finding["line"] <= end for start, end in entry.get("spans", [])):
        return True
    return any(abs(finding["line"] - line) <= window for line in entry["lines"])


def check_defect(review: dict, entry: dict, window: int) -> bool:
    return any(hits(finding, entry, window) for finding in review["findings"])


def check_decoy(review: dict, entry: dict, window: int) -> bool:
    """A decoy passes when nothing blocking was raised on it."""
    return not any(
        finding["severity"] in BLOCKING and hits(finding, entry, window)
        for finding in review["findings"]
    )


def precision(review: dict, key: dict) -> float | None:
    """Share of blocking findings that land on a real defect; None if there are none."""
    window = int(key["line_window"])
    blocking = [f for f in review["findings"] if f["severity"] in BLOCKING]
    if not blocking:
        return None
    real = sum(
        1 for f in blocking if any(hits(f, entry, window) for entry in key["defects"])
    )
    return real / len(blocking)


def _starter_files(starter: Path) -> dict[str, Path]:
    return {
        str(path.relative_to(starter)): path
        for path in sorted(starter.rglob("*"))
        if path.is_file()
        and not path.is_symlink()
        and not IGNORED_PARTS.intersection(path.relative_to(starter).parts)
    }


def untouched(workspace: Path, starter: Path = STARTER) -> list[str]:
    """Starter files that were changed or deleted in the workspace."""
    changed = []
    for relative, source in _starter_files(starter).items():
        target = workspace / relative
        if not target.is_file() or target.read_bytes() != source.read_bytes():
            changed.append(relative)
    return changed


def transcripts(run_dir: Path) -> tuple[list[Path], bool]:
    """Files that record what the agent saw, and whether any records tool output.

    Claude Code's stream-json (``steps/*.events.jsonl``, or ``opencode.events.jsonl``
    for a one-step run) carries every tool
    result, so a key the agent read appears in it verbatim. The opencode log
    records the session but not tool output, so it cannot settle the question
    on its own.
    """
    files: list[Path] = []
    records_output = False
    steps = run_dir / "steps"
    if steps.is_dir():
        for path in sorted(steps.glob("*.events.jsonl")):
            files.append(path)
            records_output = True
        files.extend(sorted(steps.glob("*.log")))
    # A one-step run keeps its stream at the run's root, not under steps/.
    single = run_dir / "opencode.events.jsonl"
    if single.is_file():
        files.append(single)
        records_output = True
    files.extend(sorted(run_dir.glob("*.log")))
    return files, records_output


def canary_seen(token: str, paths: list[Path]) -> Path | None:
    needle = token.encode()
    for path in paths:
        try:
            if needle in path.read_bytes():
                return path
        except OSError:
            continue
    return None


def workspace_files(workspace: Path) -> list[Path]:
    return [
        path
        for path in workspace.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and not IGNORED_PARTS.intersection(path.relative_to(workspace).parts)
    ]


def run_check(
    name: str,
    workspace: Path,
    run_id: str,
    key: dict | None = None,
    runs_root: Path | None = None,
) -> tuple[int, str]:
    """Decide one check. Returns ``(exit_code, message)``."""
    key = key or load_key()
    window = int(key["line_window"])
    if name == "untouched":
        changed = untouched(workspace)
        if changed:
            return FAIL, f"the PR under review was modified: {', '.join(changed)}"
        return PASS, "the PR under review is unchanged"
    if name == "canary":
        run_dir = (runs_root or REPO / "data" / "runs") / run_id
        logs, records_output = transcripts(run_dir)
        seen = canary_seen(key["canary"], logs + workspace_files(workspace))
        if seen is not None:
            return FAIL, f"the answer key's canary appears in {seen.name}"
        if not records_output:
            return INCONCLUSIVE, "no transcript records tool output for this run"
        return PASS, "the answer key's canary appears nowhere the agent wrote or saw"

    try:
        review = parse_review(workspace)
    except ReviewError as exc:
        return FAIL, str(exc)
    if name == "format":
        return (
            PASS,
            f"{len(review['findings'])} finding(s), verdict {review['verdict']}",
        )
    if name == "verdict":
        ok = review["verdict"] == key["verdict"]
        return (PASS if ok else FAIL), f"verdict {review['verdict']}"
    if name == "precision":
        value = precision(review, key)
        if value is None:
            return FAIL, "no finding of medium severity or above"
        ok = value >= float(key["precision_floor"])
        return (PASS if ok else FAIL), f"precision {value:.2f}"
    for entry in key["defects"]:
        if entry["check"] == name:
            ok = check_defect(review, entry, window)
            return (PASS if ok else FAIL), ("found" if ok else "not found")
    for entry in key["decoys"]:
        if entry["check"] == name:
            ok = check_decoy(review, entry, window)
            return (PASS if ok else FAIL), ("clean" if ok else "false positive")
    return INCONCLUSIVE, f"unknown check {name!r}"


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[0] != "check":
        print(__doc__, file=sys.stderr)
        return INCONCLUSIVE
    _, name, workspace, run_id = argv
    try:
        key = load_key()
    except (OSError, json.JSONDecodeError) as exc:
        print(f"lab error: answer key unreadable: {exc}")
        return INCONCLUSIVE
    code, message = run_check(name, Path(workspace).resolve(), run_id, key)
    print(
        f"{name}: {'PASS' if code == PASS else 'FAIL' if code == FAIL else 'INCONCLUSIVE'} — {message}"
    )
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
