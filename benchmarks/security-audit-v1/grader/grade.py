#!/usr/bin/env python3
"""Grader for security-audit-v1: findings and their proofs, against a hidden key.

Usage:
    grade.py check <name> <workspace> <run_id>

Exit codes: 0 pass, 1 fail, 124 the lab could not measure (ADR-0062).

The agent audits a FastAPI service and writes ``findings.json``; each finding
names a pytest node under ``security_tests/`` that asserts the *secure*
behaviour. Each check decides one thing:

* ``format``     findings.json parses and follows the contract
* ``untouched``  the service under audit is byte-identical
* ``kNN``        a seeded weakness was located (file + line window, or inside
                 the short function that holds it), or, for a decoy, correct
                 code was *not* reported at medium or above (line window only)
* ``pNN``        the finding that located weakness NN carries a test that fails
                 on the vulnerable service and passes on a correct fix: the
                 grader's fixed copy, or one of the entry's alternative fixes
                 (a weakness can have more than one right remedy)
* ``precision``  most medium-or-higher findings are real weaknesses
* ``canary``     the key's secret token appears nowhere the agent wrote or saw

A proof runs in a scratch directory holding the agent's ``security_tests/``
next to one copy of the service (the starter or ``grader/fixed/``), so a test
that imports from an absolute path or reads the workspace proves nothing.
Check names are opaque: ``task.yaml`` is readable by the agent.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess  # nosec B404 - runs pytest on the agent's tests in a scratch dir
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CASE = HERE.parent
REPO = CASE.parent.parent
KEY_PATH = HERE / "key.json"
STARTER = CASE / "starter"
FIXED = HERE / "fixed"
FINDINGS_FILE = "findings.json"

SEVERITIES = ("low", "medium", "high", "critical")
BLOCKING = frozenset({"medium", "high", "critical"})
IGNORED_PARTS = frozenset({"__pycache__", ".pytest_cache", ".lab-grade"})
PROOF_TIMEOUT = 50

PASS, FAIL, INCONCLUSIVE = 0, 1, 124


class FindingsError(Exception):
    """findings.json is missing or does not follow the contract."""


class KeyError_(Exception):
    """The key does not resolve against the starter (a lab defect, not the agent's)."""


def resolve_anchor(text: str, anchor: str) -> list[int]:
    """1-based line numbers an anchor spans; it must occur exactly once."""
    count = text.count(anchor)
    if count != 1:
        raise KeyError_(f"anchor {anchor[:40]!r} matches {count} times, not once")
    start = text[: text.index(anchor)].count("\n") + 1
    return list(range(start, start + anchor.count("\n") + 1))


def resolve_locations(key: dict, starter: Path = STARTER) -> dict:
    """Fill each location's ``lines`` from its ``anchors`` against the starter."""
    for entry in key["vulns"] + key["decoys"]:
        for location in entry["locations"]:
            if "anchors" not in location:
                continue
            source = (starter / location["file"]).read_text(encoding="utf-8")
            lines: set[int] = set()
            for anchor in location["anchors"]:
                lines.update(resolve_anchor(source, anchor))
            location["lines"] = sorted(lines)
    return key


def enclosing_spans(
    source: str, lines: list[int], max_lines: int
) -> list[tuple[int, int]]:
    """The innermost function around each line, when it is at most ``max_lines`` long.

    An auditor who points at the handler's ``def`` or at a sibling line of the
    same short function has located the flaw; three lines around one statement
    is narrower than how findings point at code.
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
    """Weaknesses (not decoys) also match anywhere in their enclosing function."""
    max_lines = int(key.get("span_max_lines", 0))
    for entry in key["vulns"]:
        for location in entry["locations"]:
            source = starter / location["file"]
            location["spans"] = (
                enclosing_spans(
                    source.read_text(encoding="utf-8"), location["lines"], max_lines
                )
                if max_lines and source.is_file()
                else []
            )
    return key


def load_key(path: Path = KEY_PATH) -> dict:
    key = json.loads(path.read_text(encoding="utf-8"))
    return resolve_spans(resolve_locations(key))


def normalize_path(raw: object) -> str:
    """``./clinic_api/x.py``, ``/abs/ws/clinic_api/x.py`` → ``clinic_api/x.py``."""
    text = str(raw or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    for anchor in ("clinic_api/", "security_tests/", "tests/"):
        index = text.find("/" + anchor)
        if text.startswith("/") and index >= 0:
            text = text[index + 1 :]
            break
    return text


def _line(raw: object, index: int) -> int:
    if isinstance(raw, bool):
        raise FindingsError(f"finding {index}: line must be a number")
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str) and raw.strip().isdigit():
        value = int(raw.strip())
    else:
        raise FindingsError(f"finding {index}: line must be a positive integer")
    if value < 1:
        raise FindingsError(f"finding {index}: line must be a positive integer")
    return value


def _cwe(raw: object) -> int | None:
    text = str(raw or "").strip().upper().replace("CWE", "").strip(" -:")
    return int(text) if text.isdigit() else None


def parse_findings(workspace: Path) -> list[dict]:
    path = workspace / FINDINGS_FILE
    if not path.is_file():
        raise FindingsError(f"{FINDINGS_FILE} is missing from the workspace")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise FindingsError(f"{FINDINGS_FILE} is not valid JSON: {exc}") from exc
    items = data.get("findings") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise FindingsError("findings must be a list under the 'findings' key")
    clean = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise FindingsError(f"finding {index} is not an object")
        file = normalize_path(item.get("file"))
        if not file:
            raise FindingsError(f"finding {index} has no file")
        severity = str(item.get("severity", "")).strip().lower()
        if severity not in SEVERITIES:
            raise FindingsError(
                f"finding {index}: severity must be one of {SEVERITIES}"
            )
        test = normalize_path(item.get("test"))
        clean.append(
            {
                "file": file,
                "line": _line(item.get("line"), index),
                "severity": severity,
                "cwe": _cwe(item.get("cwe")),
                "test": test,
            }
        )
    return clean


def hits(finding: dict, entry: dict, window: int) -> bool:
    return any(
        finding["file"] == location["file"]
        and (
            any(abs(finding["line"] - line) <= window for line in location["lines"])
            or any(
                start <= finding["line"] <= end
                for start, end in location.get("spans", [])
            )
        )
        for location in entry["locations"]
    )


def apply_fix(root: Path, fix: dict, package: str) -> None:
    """Turn the copy of ``grader/fixed/`` under ``root`` into an alternative fix.

    ``fix`` replaces one exact snippet of one file; it must match once, or the
    key is wrong (a lab defect, never the agent's).
    """
    path = root / fix["file"]
    if not path.is_relative_to(root / package):
        raise KeyError_(f"alternative fix {fix['name']!r} edits outside the service")
    text = path.read_text(encoding="utf-8")
    if text.count(fix["old"]) != 1:
        raise KeyError_(f"alternative fix {fix['name']!r} does not match once")
    path.write_text(text.replace(fix["old"], fix["new"]), encoding="utf-8")


def precision(findings: list[dict], key: dict) -> float | None:
    window = int(key["line_window"])
    blocking = [f for f in findings if f["severity"] in BLOCKING]
    if not blocking:
        return None
    real = sum(1 for f in blocking if any(hits(f, v, window) for v in key["vulns"]))
    return real / len(blocking)


def _files(root: Path) -> dict[str, Path]:
    return {
        str(path.relative_to(root)): path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and not path.is_symlink()
        and not IGNORED_PARTS.intersection(path.relative_to(root).parts)
    }


def untouched(workspace: Path, starter: Path = STARTER) -> list[str]:
    changed = []
    for relative, source in _files(starter).items():
        target = workspace / relative
        if not target.is_file() or target.read_bytes() != source.read_bytes():
            changed.append(relative)
    return changed


def _test_node(test: str, tests_dir: str) -> str | None:
    """A node id under ``tests_dir``, or None when it points anywhere else."""
    path, _, rest = test.partition("::")
    parts = Path(path).parts
    if (
        not path.endswith(".py")
        or not parts
        or parts[0] != tests_dir
        or ".." in parts
        or Path(path).is_absolute()
    ):
        return None
    return f"{path}::{rest}" if rest else path


def run_node(
    node: str,
    workspace: Path,
    app_source: Path,
    key: dict,
    fix: dict | None = None,
) -> tuple[int | None, str]:
    """Run one of the agent's tests against one copy of the service, with an
    alternative ``fix`` applied on top of it when given.

    Returns pytest's exit code (None when the run itself broke: timeout, the
    test file missing) and the tail of its output.
    """
    tests_dir = key["tests_dir"]
    package = key["app_package"]
    source_tests = workspace / tests_dir
    if not source_tests.is_dir():
        return None, f"{tests_dir}/ is missing"
    with tempfile.TemporaryDirectory(prefix="audit-proof-") as scratch:
        root = Path(scratch)
        shutil.copytree(app_source / package, root / package)
        if fix is not None:
            apply_fix(root, fix, package)
        shutil.copytree(
            source_tests,
            root / tests_dir,
            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
            symlinks=False,
        )
        (root / "pytest.ini").write_text("[pytest]\n")
        if not (root / node.split("::")[0]).is_file():
            return None, f"{node.split('::')[0]} does not exist"
        env = dict(os.environ)
        # The scratch root first, so `import clinic_api` resolves to this copy
        # and nothing else; the validation deps (httpx2) stay reachable.
        env["PYTHONPATH"] = os.pathsep.join(
            item for item in (str(root), env.get("PYTHONPATH", "")) if item
        )
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            result = subprocess.run(  # nosec B603 - fixed argv, the lab's interpreter
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    "-c",
                    str(root / "pytest.ini"),
                    "--rootdir",
                    str(root),
                    node,
                ],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=PROOF_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return None, "the test timed out"
    output = (result.stdout + result.stderr)[-600:]
    return result.returncode, output


def passing_fix(node: str, workspace: Path, entry: dict, key: dict) -> str | None:
    """The name of the first correct fix the test passes on, or None."""
    if run_node(node, workspace, FIXED, key)[0] == 0:
        return "reference"
    for fix in entry.get("alternative_fixes", []):
        if run_node(node, workspace, FIXED, key, fix)[0] == 0:
            return str(fix["name"])
    return None


def check_proof(
    entry: dict, findings: list[dict], workspace: Path, key: dict
) -> tuple[int, str]:
    window = int(key["line_window"])
    matching = [f for f in findings if hits(f, entry, window) and f["test"]]
    if not matching:
        return FAIL, "no finding locates this weakness with a test"
    reasons = []
    for finding in matching:
        node = _test_node(finding["test"], key["tests_dir"])
        if node is None:
            reasons.append(
                f"{finding['test']!r} is not a test under {key['tests_dir']}/"
            )
            continue
        on_vulnerable, _ = run_node(node, workspace, STARTER, key)
        if on_vulnerable != 1:
            reasons.append(
                f"{node} does not fail on the vulnerable service (exit {on_vulnerable})"
            )
            continue
        remedy = passing_fix(node, workspace, entry, key)
        if remedy is None:
            reasons.append(
                f"{node} does not pass once the weakness is fixed, by any accepted fix"
            )
            continue
        return (
            PASS,
            f"{node} fails on the vulnerable service and passes on the {remedy} fix",
        )
    return FAIL, "; ".join(reasons)[-400:]


def transcripts(run_dir: Path) -> tuple[list[Path], bool]:
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


def run_check(
    name: str,
    workspace: Path,
    run_id: str,
    key: dict | None = None,
    runs_root: Path | None = None,
) -> tuple[int, str]:
    key = key or load_key()
    window = int(key["line_window"])
    if name == "untouched":
        changed = untouched(workspace)
        if changed:
            return FAIL, f"the service under audit was modified: {', '.join(changed)}"
        return PASS, "the service under audit is unchanged"
    if name == "canary":
        run_dir = (runs_root or REPO / "data" / "runs") / run_id
        logs, records_output = transcripts(run_dir)
        seen = canary_seen(key["canary"], logs + list(_files(workspace).values()))
        if seen is not None:
            return FAIL, f"the answer key's canary appears in {seen.name}"
        if not records_output:
            return INCONCLUSIVE, "no transcript records tool output for this run"
        return PASS, "the answer key's canary appears nowhere the agent wrote or saw"

    try:
        findings = parse_findings(workspace)
    except FindingsError as exc:
        return FAIL, str(exc)
    if name == "format":
        return PASS, f"{len(findings)} finding(s)"
    if name == "precision":
        value = precision(findings, key)
        if value is None:
            return FAIL, "no finding of medium severity or above"
        ok = value >= float(key["precision_floor"])
        return (PASS if ok else FAIL), f"precision {value:.2f}"
    for entry in key["vulns"]:
        if entry["check"] == name:
            ok = any(hits(f, entry, window) for f in findings)
            return (PASS if ok else FAIL), ("located" if ok else "not located")
        if entry["proof"] == name:
            return check_proof(entry, findings, workspace, key)
    for entry in key["decoys"]:
        if entry["check"] == name:
            ok = not any(
                f["severity"] in BLOCKING and hits(f, entry, window) for f in findings
            )
            return (PASS if ok else FAIL), ("clean" if ok else "false positive")
    return INCONCLUSIVE, f"unknown check {name!r}"


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[0] != "check":
        print(__doc__, file=sys.stderr)
        return INCONCLUSIVE
    _, name, workspace, run_id = argv
    try:
        key = load_key()
    except (OSError, json.JSONDecodeError, KeyError_) as exc:
        print(f"lab error: answer key unreadable: {exc}")
        return INCONCLUSIVE
    try:
        import fastapi.testclient  # noqa: F401  # the proofs need TestClient's transport
    except (ImportError, RuntimeError) as exc:
        if name.startswith("p"):
            print(f"lab error: the validation lane cannot run TestClient: {exc}")
            return INCONCLUSIVE
    try:
        code, message = run_check(name, Path(workspace).resolve(), run_id, key)
    except KeyError_ as exc:
        print(f"lab error: {exc}")
        return INCONCLUSIVE
    label = {PASS: "PASS", FAIL: "FAIL"}.get(code, "INCONCLUSIVE")
    print(f"{name}: {label} — {message}")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
