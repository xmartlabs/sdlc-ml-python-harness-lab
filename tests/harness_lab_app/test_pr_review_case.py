"""pr-review-v1: the grader's rules and the case's isolation invariants.

The answer key's contents are never asserted here by location: tests are in the
lab tree an agent can read, and a test that spells out where a defect is would
be the key under another name. The tests work from the key file itself.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CASE = REPO_ROOT / "benchmarks" / "pr-review-v1"
TASK = CASE / "task.yaml"


def _load_grader():
    spec = importlib.util.spec_from_file_location(
        "pr_review_grade", CASE / "grader" / "grade.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


grade = _load_grader()
KEY = grade.load_key()


def _workspace(tmp_path: Path, review: dict | str | None) -> Path:
    workspace = tmp_path / "ws"
    shutil.copytree(CASE / "starter", workspace)
    if review is not None:
        text = review if isinstance(review, str) else json.dumps(review)
        (workspace / "review.json").write_text(text)
    return workspace


def _finding(entry: dict, severity: str = "high", offset: int = 0) -> dict:
    return {
        "file": entry["file"],
        "line": entry["lines"][0] + offset,
        "severity": severity,
        "category": "correctness",
        "title": "t",
        "explanation": "e",
    }


def _gold() -> dict:
    return {
        "verdict": "request_changes",
        "findings": [_finding(entry) for entry in KEY["defects"]],
    }


def _check(name: str, workspace: Path, tmp_path: Path, run_id: str = "r-1"):
    return grade.run_check(name, workspace, run_id, KEY, runs_root=tmp_path / "runs")


# --- The key is well formed --------------------------------------------------------


def test_every_key_entry_has_an_opaque_unique_check_and_points_at_a_starter_file():
    entries = KEY["defects"] + KEY["decoys"]
    names = [entry["check"] for entry in entries]
    assert len(names) == len(set(names))
    for entry in entries:
        assert entry["check"].startswith("k") and entry["check"][1:].isdigit()
        source = CASE / "starter" / entry["file"]
        assert source.is_file(), entry["file"]
        length = len(source.read_text().splitlines())
        assert all(1 <= line <= length for line in entry["lines"]), entry["check"]


def test_the_task_runs_every_key_check_and_nothing_names_what_it_looks_for():
    commands = " ".join(yaml.safe_load(TASK.read_text())["validation_commands"])
    for entry in KEY["defects"] + KEY["decoys"]:
        assert f" check {entry['check']} " in commands
        assert entry["id"] not in commands


# --- Recall, decoys, precision -----------------------------------------------------


def test_the_gold_review_passes_every_review_check(tmp_path):
    workspace = _workspace(tmp_path, _gold())
    for name in ["format", "verdict", "untouched", "precision"] + [
        entry["check"] for entry in KEY["defects"] + KEY["decoys"]
    ]:
        code, message = _check(name, workspace, tmp_path)
        assert code == grade.PASS, (name, message)


def test_a_finding_within_the_window_hits_and_one_outside_misses(tmp_path):
    entry = KEY["defects"][0]
    window = KEY["line_window"]
    near = _workspace(
        tmp_path / "a",
        {"verdict": "request_changes", "findings": [_finding(entry, offset=window)]},
    )
    far = _workspace(
        tmp_path / "b",
        {
            "verdict": "request_changes",
            "findings": [_finding(entry, offset=window + 40)],
        },
    )
    assert _check(entry["check"], near, tmp_path)[0] == grade.PASS
    assert _check(entry["check"], far, tmp_path)[0] == grade.FAIL


def test_a_finding_in_another_file_does_not_hit(tmp_path):
    entry = KEY["defects"][0]
    wrong = dict(_finding(entry), file="shop/catalog.py")
    workspace = _workspace(
        tmp_path, {"verdict": "request_changes", "findings": [wrong]}
    )
    assert _check(entry["check"], workspace, tmp_path)[0] == grade.FAIL


def test_paths_are_normalised_before_matching(tmp_path):
    entry = KEY["defects"][0]
    for spelling in (
        f"./{entry['file']}",
        f"b/{entry['file']}",
        f"/app/workspace/x/{entry['file']}",
    ):
        finding = dict(_finding(entry), file=spelling)
        workspace = _workspace(
            tmp_path / spelling.replace("/", "_"),
            {"verdict": "request_changes", "findings": [finding]},
        )
        assert _check(entry["check"], workspace, tmp_path)[0] == grade.PASS, spelling


def test_a_decoy_fails_only_on_a_blocking_finding(tmp_path):
    decoy = KEY["decoys"][0]
    low = _workspace(
        tmp_path / "low",
        {"verdict": "request_changes", "findings": [_finding(decoy, "low")]},
    )
    high = _workspace(
        tmp_path / "high",
        {"verdict": "request_changes", "findings": [_finding(decoy, "medium")]},
    )
    assert _check(decoy["check"], low, tmp_path)[0] == grade.PASS
    assert _check(decoy["check"], high, tmp_path)[0] == grade.FAIL


def test_a_shotgun_review_fails_precision(tmp_path):
    findings = [_finding(KEY["defects"][0])] + [
        {"file": "shop/catalog.py", "line": n, "severity": "medium"}
        for n in range(1, 8)
    ]
    workspace = _workspace(
        tmp_path, {"verdict": "request_changes", "findings": findings}
    )
    code, message = _check("precision", workspace, tmp_path)
    assert code == grade.FAIL, message


def test_low_severity_notes_do_not_cost_precision(tmp_path):
    review = _gold()
    review["findings"] += [
        {"file": "shop/catalog.py", "line": 3, "severity": "low"}
    ] * 10
    workspace = _workspace(tmp_path, review)
    assert _check("precision", workspace, tmp_path)[0] == grade.PASS


def test_an_empty_approve_fails_the_verdict_and_every_defect(tmp_path):
    workspace = _workspace(tmp_path, {"verdict": "approve", "findings": []})
    assert _check("verdict", workspace, tmp_path)[0] == grade.FAIL
    assert _check("precision", workspace, tmp_path)[0] == grade.FAIL
    for entry in KEY["defects"]:
        assert _check(entry["check"], workspace, tmp_path)[0] == grade.FAIL


# --- Format and the code under review ----------------------------------------------


@pytest.mark.parametrize(
    "review",
    [
        None,
        "not json",
        "[]",
        {"verdict": "maybe", "findings": []},
        {"verdict": "approve", "findings": {}},
        {
            "verdict": "approve",
            "findings": [{"file": "shop/orders.py", "line": "x", "severity": "high"}],
        },
        {
            "verdict": "approve",
            "findings": [{"file": "shop/orders.py", "line": 3, "severity": "urgent"}],
        },
        {
            "verdict": "approve",
            "findings": [{"file": "", "line": 3, "severity": "high"}],
        },
    ],
)
def test_a_review_off_contract_fails_format(tmp_path, review):
    workspace = _workspace(tmp_path, review)
    assert _check("format", workspace, tmp_path)[0] == grade.FAIL


def test_editing_the_code_under_review_fails_untouched(tmp_path):
    workspace = _workspace(tmp_path, _gold())
    target = workspace / KEY["defects"][0]["file"]
    target.write_text(target.read_text() + "\n# fixed\n")
    code, message = _check("untouched", workspace, tmp_path)
    assert code == grade.FAIL and KEY["defects"][0]["file"] in message


def test_scratch_files_and_caches_do_not_count_as_edits(tmp_path):
    workspace = _workspace(tmp_path, _gold())
    (workspace / "notes.md").write_text("scratch")
    (workspace / "shop" / "__pycache__").mkdir()
    (workspace / "shop" / "__pycache__" / "x.pyc").write_bytes(b"\0")
    assert _check("untouched", workspace, tmp_path)[0] == grade.PASS


# --- The canary --------------------------------------------------------------------


def _run_dir(tmp_path: Path, run_id: str = "r-1") -> Path:
    steps = tmp_path / "runs" / run_id / "steps"
    steps.mkdir(parents=True)
    return steps


def test_the_canary_passes_a_clean_claude_transcript(tmp_path):
    steps = _run_dir(tmp_path)
    (steps / "step-1.events.jsonl").write_text(
        '{"type":"tool_result","content":"PR.md"}\n'
    )
    workspace = _workspace(tmp_path, _gold())
    assert _check("canary", workspace, tmp_path)[0] == grade.PASS


def test_the_canary_fails_a_run_that_read_the_key(tmp_path):
    steps = _run_dir(tmp_path)
    (steps / "step-1.events.jsonl").write_text(json.dumps({"content": json.dumps(KEY)}))
    workspace = _workspace(tmp_path, _gold())
    code, message = _check("canary", workspace, tmp_path)
    assert code == grade.FAIL and "step-1.events.jsonl" in message


def test_the_canary_fails_a_workspace_holding_the_key(tmp_path):
    _run_dir(tmp_path)
    workspace = _workspace(tmp_path, _gold())
    (workspace / "copied.json").write_text(KEY["canary"])
    assert _check("canary", workspace, tmp_path)[0] == grade.FAIL


def test_the_canary_is_inconclusive_without_a_transcript_of_tool_output(tmp_path):
    steps = _run_dir(tmp_path)
    (steps / "step-1.log").write_text("opencode session log, no tool output\n")
    workspace = _workspace(tmp_path, _gold())
    assert _check("canary", workspace, tmp_path)[0] == grade.INCONCLUSIVE


# --- The case ----------------------------------------------------------------------


def test_the_starter_is_a_measurable_baseline(tmp_path):
    """The untouched starter must fail every review check (the dry run's `ok`)."""
    workspace = _workspace(tmp_path, None)
    for entry in KEY["defects"]:
        assert _check(entry["check"], workspace, tmp_path)[0] == grade.FAIL
    assert _check("format", workspace, tmp_path)[0] == grade.FAIL


def test_the_prs_own_tests_pass_on_the_starter(tmp_path):
    """The defects are the ones the PR's tests miss; a red suite would give them away."""
    import subprocess
    import sys

    workspace = _workspace(tmp_path, None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-c",
            "pytest.ini",
        ],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_answer_key_and_the_candidate_skill_are_hidden_from_the_agent():
    from harness_lab_app.case_profile import agent_hidden_for

    raw = yaml.safe_load(TASK.read_text())
    hidden = agent_hidden_for(raw)
    assert "benchmarks/pr-review-v1/grader/" in hidden
    assert "benchmarks/pr-review-v1/candidate-skill/" in hidden
    assert "benchmarks/pr-review-v1/scoring.md" in hidden


def test_the_first_check_guards_the_grader_and_the_pr_against_edits():
    commands = yaml.safe_load(TASK.read_text())["validation_commands"]
    assert commands[0].startswith("git -C /app diff --quiet HEAD -- ")
    assert "benchmarks/pr-review-v1/grader" in commands[0]
    assert "benchmarks/pr-review-v1/starter" in commands[0]
    assert commands[1] == "test -s {workspace}/review.json"
    for command in commands[2:]:
        assert '"$PWD"' in command and "{run_id}" in command


def test_the_key_is_never_spelled_out_where_the_agent_can_read_it():
    """Visible files must not carry the key's canary or its entry ids."""
    hidden = {"grader", "scoring.md", "README.md", "candidate-skill"}
    visible = [
        path
        for path in CASE.rglob("*")
        if path.is_file() and path.relative_to(CASE).parts[0] not in hidden
    ]
    for path in visible:
        text = path.read_text(errors="ignore")
        assert KEY["canary"] not in text, path
        for entry in KEY["defects"] + KEY["decoys"]:
            assert entry["id"] not in text, (path, entry["id"])


# --- One step: the review is the deliverable (ADR-0087) ----------------------------


def test_the_review_case_is_a_one_step_report():
    from harness_lab_app.case_profile import profile_from_task
    from harness_lab_app.case_service import step_budget_from_task

    raw = yaml.safe_load(TASK.read_text())
    assert step_budget_from_task(raw, []) == 1
    assert profile_from_task(raw)["deliverable"] == "report"


def test_a_report_case_runs_one_step_whatever_the_form_asked():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "bench_run_for_report", REPO_ROOT / "scripts" / "bench_run.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert module.report_deliverable({"deliverable": "report"})
    assert module.report_deliverable({"deliverable": " Report "})
    assert not module.report_deliverable({"deliverable": ""})
    assert not module.report_deliverable(None)


def test_an_unknown_deliverable_is_refused():
    from harness_lab_app.case_profile import CaseProfileError, profile_from_task

    with pytest.raises(CaseProfileError, match="deliverable"):
        profile_from_task({"deliverable": "essay"})


def test_an_explicit_step_budget_wins_over_target_level():
    from harness_lab_app.case_service import step_budget_from_task

    assert step_budget_from_task({"target_level": 8, "max_steps": 1}, []) == 1
    assert step_budget_from_task({"target_level": 8}, []) == 8
    assert step_budget_from_task({"max_steps": 1}, ["a.md", "b.md"]) == 3
    with pytest.raises(ValueError):
        step_budget_from_task({"max_steps": 0}, [])


def test_the_canary_reads_a_one_step_runs_root_transcript(tmp_path):
    run = tmp_path / "runs" / "r-1"
    run.mkdir(parents=True)
    (run / "opencode.events.jsonl").write_text(json.dumps({"content": KEY["canary"]}))
    workspace = _workspace(tmp_path, _gold())
    code, message = _check("canary", workspace, tmp_path)
    assert code == grade.FAIL and "opencode.events.jsonl" in message
    (run / "opencode.events.jsonl").write_text('{"content": "PR.md"}\n')
    assert _check("canary", workspace, tmp_path)[0] == grade.PASS


def test_every_defect_has_a_function_span_and_no_decoy_does():
    """Spans widen location for defects only; a decoy keeps the strict window."""
    for entry in KEY["defects"]:
        assert entry["spans"], entry["check"]
        for start, end in entry["spans"]:
            assert end - start + 1 <= KEY["span_max_lines"]
            assert any(start <= line <= end for line in entry["lines"])
    assert all("spans" not in entry for entry in KEY["decoys"])


def test_a_finding_at_the_functions_def_hits_its_defect(tmp_path):
    """A reviewer pointing at the enclosing function has located the defect."""
    entry = next(e for e in KEY["defects"] if e["spans"][0][0] < min(e["lines"]) - 3)
    start, _ = entry["spans"][0]
    finding = dict(_finding(entry), line=start)
    workspace = _workspace(
        tmp_path, {"verdict": "request_changes", "findings": [finding]}
    )
    assert _check(entry["check"], workspace, tmp_path)[0] == grade.PASS


def test_a_function_longer_than_the_cap_gets_no_span():
    source = "def f():\n" + "    x = 1\n" * 40
    assert grade.enclosing_spans(source, [20], max_lines=30) == []
    assert grade.enclosing_spans(source, [20], max_lines=60) == [(1, 41)]


def test_the_innermost_function_is_the_span():
    source = "def outer():\n    def inner():\n        return 1\n    return inner\n"
    assert grade.enclosing_spans(source, [3], max_lines=30) == [(2, 3)]
