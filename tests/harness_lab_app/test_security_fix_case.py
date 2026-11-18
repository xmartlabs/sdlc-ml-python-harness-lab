"""security-fix-v1: the hidden grader discriminates, and the case is well formed.

The grader is exercised for real against the shipped starter (always present) and,
when it exists on this machine, the git-ignored reference fix. Nothing here names
the weaknesses: this file is not masked from the agent.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess  # nosec B404 - runs the lab's own grader
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CASE = REPO_ROOT / "benchmarks" / "security-fix-v1"
GRADER = CASE / "grader" / "grade.py"
STARTER = CASE / "starter"
REFERENCE = REPO_ROOT / "data" / "reference" / "security-fix-v1" / "gold"
RUN_ID = "security-fix-v1-K-001"

REGRESSIONS = [
    "regression_accounts",
    "regression_notes",
    "regression_search",
    "regression_attachments",
    "regression_export",
    "regression_preferences",
]
SECURITY = [f"security_{n}" for n in range(1, 7)]


def _grader() -> ModuleType:
    spec = importlib.util.spec_from_file_location("secfix_grade", GRADER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _grade(check: str, workspace: Path, run_id: str = RUN_ID) -> int:
    return subprocess.run(  # nosec B603 - our interpreter, our grader
        [sys.executable, str(GRADER), "check", check, str(workspace), run_id],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    ).returncode


@pytest.fixture
def starter(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    shutil.copytree(STARTER, workspace)
    return workspace


# --- payloads ------------------------------------------------------------------


def test_payloads_are_deterministic_per_run_id() -> None:
    grade = _grader()
    for check in SECURITY:
        assert grade.payloads_for(check, RUN_ID) == grade.payloads_for(check, RUN_ID)


def test_payloads_differ_across_run_ids() -> None:
    grade = _grader()
    draws = {
        repr(grade.payloads_for("security_1", f"security-fix-v1-K-{n:03d}"))
        for n in range(1, 9)
    }
    assert len(draws) > 1


def test_the_canonical_payload_is_always_sent() -> None:
    """So the untouched starter fails every security check on every run."""
    grade = _grader()
    for n in range(1, 6):
        run_id = f"security-fix-v1-K-{n:03d}"
        drawn = grade.payloads_for("security_1", run_id)
        listed = [value for value in drawn.values() if isinstance(value, list)]
        assert len(listed) == 1
        first = listed[0][0]
        # The first draw is the same on every run: the canonical payload.
        assert (
            first
            == grade.payloads_for("security_1", "security-fix-v1-K-999")[
                next(k for k, v in drawn.items() if isinstance(v, list))
            ][0]
        )
        payload = grade.payloads_for("security_4", run_id)
        assert payload["reads"][:2] == [grade.READ_CANONICAL, grade.READ_ABSOLUTE]
        assert payload["writes"][:2] == [grade.WRITE_CANONICAL, grade.WRITE_ABSOLUTE]


# --- the grader against real workspaces ------------------------------------------


def test_the_starter_keeps_every_feature_and_fails_every_attack(starter: Path) -> None:
    assert [c for c in REGRESSIONS if _grade(c, starter) != 0] == []
    assert [c for c in SECURITY if _grade(c, starter) == 0] == []
    assert _grade("report", starter) == 1


def test_an_empty_workspace_passes_nothing(tmp_path: Path) -> None:
    assert [c for c in [*REGRESSIONS, *SECURITY] if _grade(c, tmp_path) == 0] == []


def test_deleting_a_feature_fails_its_regression(starter: Path) -> None:
    service = starter / "notes_service" / "service.py"
    source = service.read_text()
    source, count = re.subn(
        r"(    def search_notes\(self[^)]*\)[^:]*:\n)",
        r'\1        raise NotImplementedError("disabled")\n',
        source,
    )
    assert count == 1
    service.write_text(source)
    assert _grade("regression_search", starter) == 1
    assert _grade("security_1", starter) == 1


def test_printing_a_verdict_does_not_pass_a_check(starter: Path) -> None:
    """The verdict travels behind a nonce the agent's code never sees."""
    init = starter / "notes_service" / "__init__.py"
    init.write_text(
        'import os, sys\nprint("PASS ok")\nprint(\'{"passed": true}\')\n'
        "sys.stdout.flush()\nos._exit(0)\n"
    )
    assert _grade("security_1", starter) == 1


def test_an_unknown_check_is_inconclusive(starter: Path) -> None:
    assert _grade("no_such_check", starter) == 124


@pytest.mark.skipif(not REFERENCE.is_dir(), reason="reference fix not on this host")
def test_the_reference_fix_passes_every_check(tmp_path: Path) -> None:
    workspace = tmp_path / "gold"
    shutil.copytree(REFERENCE, workspace)
    failing = [c for c in [*REGRESSIONS, *SECURITY, "report"] if _grade(c, workspace)]
    assert failing == []


# --- the case ------------------------------------------------------------------


def _task() -> dict:
    return yaml.safe_load((CASE / "task.yaml").read_text())


def test_the_case_hides_its_grader_rubric_notes_and_candidate_skill() -> None:
    from harness_lab_app.case_profile import agent_hidden_for

    hidden = agent_hidden_for(_task())
    for path in ("grader/", "scoring.md", "README.md", "candidate-skill/"):
        assert f"benchmarks/security-fix-v1/{path}" in hidden


def test_the_case_grades_one_behaviour_per_command() -> None:
    from harness_lab_app.case_validation import check_commands

    commands = _task()["validation_commands"]
    assert check_commands(commands) == []
    assert commands[0].startswith("git -C /app diff --quiet HEAD -- ")
    assert "security-fix-v1/grader" in commands[0]
    graded = commands[2:]
    named = [re.search(r" check (\w+) ", c).group(1) for c in graded]  # type: ignore[union-attr]
    assert named == [*REGRESSIONS, *SECURITY, "report"]
    for command in graded:
        assert command.startswith("cd {workspace} && timeout ")
        assert '"$PWD" {run_id}' in command


def test_the_prompt_does_not_name_the_weaknesses() -> None:
    """Read from the hidden grader, never spelled out here: this file is visible."""
    grade = _grader()
    prompt = (CASE / "prompt_default.md").read_text().lower()
    for name, (_checks, cwes, _features) in grade.CLASSES.items():
        for word in (name.replace("_", " "), *name.split("_")):
            if len(word) > 6:  # skip short generic words ("access", "path")
                assert word not in prompt, f"the prompt names {word!r}"
        for cwe in cwes:
            assert f"cwe-{cwe}" not in prompt, f"the prompt names CWE-{cwe}"


def test_the_candidate_skill_is_portable() -> None:
    skill = (
        CASE / "candidate-skill" / "remediate_vulnerabilities" / "SKILL.md"
    ).read_text()
    front = yaml.safe_load(skill.split("---")[1])
    assert front["name"] == "remediate_vulnerabilities"
    assert "pentest" in front["description"]
    assert "/app" not in skill
    assert "benchmarks/" not in skill
