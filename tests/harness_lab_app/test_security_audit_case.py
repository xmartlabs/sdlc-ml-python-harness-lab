"""security-audit-v1: the grader's rules and the case's isolation invariants.

Nothing here names the weaknesses or where they are: this file is readable by
the agent in a lab_root run. Anything sensitive is read from the hidden key at
runtime. The proof checks run real pytest subprocesses against the service and
need FastAPI's TestClient (httpx2); they are skipped where it is missing.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CASE = REPO_ROOT / "benchmarks" / "security-audit-v1"
TASK = CASE / "task.yaml"
REFERENCE = REPO_ROOT / "data" / "reference" / "security-audit-v1"


def _load_grader():
    spec = importlib.util.spec_from_file_location(
        "security_audit_grade", CASE / "grader" / "grade.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


grade = _load_grader()
KEY = grade.load_key()


def _testclient_available() -> bool:
    try:
        import fastapi.testclient  # noqa: F401
    except (ImportError, RuntimeError):
        return False
    return True


needs_testclient = pytest.mark.skipif(
    not _testclient_available(), reason="FastAPI TestClient needs httpx2"
)


def _workspace(tmp_path: Path, findings: dict | str | None) -> Path:
    workspace = tmp_path / "ws"
    shutil.copytree(CASE / "starter", workspace)
    if findings is not None:
        text = findings if isinstance(findings, str) else json.dumps(findings)
        (workspace / "findings.json").write_text(text)
    return workspace


def _finding(
    entry: dict, severity: str = "high", offset: int = 0, test: str = ""
) -> dict:
    location = entry["locations"][0]
    return {
        "id": entry["check"],
        "file": location["file"],
        "line": location["lines"][0] + offset,
        "cwe": "CWE-0",
        "severity": severity,
        "title": "t",
        "impact": "i",
        "test": test,
    }


def _check(name: str, workspace: Path, tmp_path: Path, run_id: str = "r-1"):
    return grade.run_check(name, workspace, run_id, KEY, runs_root=tmp_path / "runs")


def _all_located() -> dict:
    return {"findings": [_finding(entry) for entry in KEY["vulns"]]}


# --- The key and the case ---------------------------------------------------------


def test_every_key_entry_has_opaque_unique_checks_inside_the_starter():
    names = [e["check"] for e in KEY["vulns"] + KEY["decoys"]]
    names += [e["proof"] for e in KEY["vulns"]]
    assert len(names) == len(set(names))
    for entry in KEY["vulns"] + KEY["decoys"]:
        for location in entry["locations"]:
            source = CASE / "starter" / location["file"]
            assert source.is_file(), location["file"]
            length = len(source.read_text().splitlines())
            assert all(1 <= line <= length for line in location["lines"])


def test_the_task_runs_every_key_check_and_names_none_of_them():
    commands = " ".join(yaml.safe_load(TASK.read_text())["validation_commands"])
    for entry in KEY["vulns"]:
        assert f" check {entry['check']} " in commands
        assert f" check {entry['proof']} " in commands
    for entry in KEY["vulns"] + KEY["decoys"]:
        assert entry["id"] not in commands


def test_the_fixed_service_differs_from_the_starter_only_in_the_service():
    starter = {
        p.relative_to(CASE / "starter")
        for p in (CASE / "starter" / KEY["app_package"]).rglob("*.py")
    }
    fixed = {
        p.relative_to(CASE / "grader" / "fixed")
        for p in (CASE / "grader" / "fixed" / KEY["app_package"]).rglob("*.py")
    }
    assert starter == fixed


def test_the_key_and_the_skill_are_hidden_and_never_spelled_out_where_visible():
    from harness_lab_app.case_profile import agent_hidden_for

    raw = yaml.safe_load(TASK.read_text())
    hidden = agent_hidden_for(raw)
    for path in ("grader/", "candidate-skill/", "scoring.md", "README.md"):
        assert f"benchmarks/security-audit-v1/{path}" in hidden
    hidden_roots = {"grader", "scoring.md", "README.md", "candidate-skill"}
    visible = [
        path
        for path in CASE.rglob("*")
        if path.is_file() and path.relative_to(CASE).parts[0] not in hidden_roots
    ]
    for path in visible:
        text = path.read_text(errors="ignore")
        assert KEY["canary"] not in text, path
        for entry in KEY["vulns"] + KEY["decoys"]:
            assert entry["id"] not in text, (path, entry["id"])


def test_the_prompt_names_no_weakness_class():
    prompt = (CASE / "prompt_default.md").read_text().lower()
    for entry in KEY["vulns"]:
        for cwe in entry["cwes"]:
            assert f"cwe-{cwe}" not in prompt
        # The id's distinctive words, minus the service's own domain vocabulary.
        domain = {"appointment", "webhook", "login", "token"}
        for word in entry["id"].split("-"):
            if len(word) > 5 and word not in domain:
                assert word not in prompt, word


def test_the_first_checks_guard_the_grader_and_name_the_entry_point():
    commands = yaml.safe_load(TASK.read_text())["validation_commands"]
    assert commands[0].startswith("git -C /app diff --quiet HEAD -- ")
    assert "security-audit-v1/grader" in commands[0]
    assert "security-audit-v1/starter" in commands[0]
    assert commands[1] == "test -s {workspace}/findings.json"
    for command in commands[2:]:
        assert '"$PWD"' in command and "{run_id}" in command


# --- Location, decoys, precision ----------------------------------------------------


def test_locating_every_weakness_passes_detection_and_precision(tmp_path):
    workspace = _workspace(tmp_path, _all_located())
    for name in ["format", "untouched", "precision"] + [
        e["check"] for e in KEY["vulns"] + KEY["decoys"]
    ]:
        code, message = _check(name, workspace, tmp_path)
        assert code == grade.PASS, (name, message)


def test_a_finding_outside_the_window_or_in_another_file_misses(tmp_path):
    entry = KEY["vulns"][0]
    far = _workspace(tmp_path / "far", {"findings": [_finding(entry, offset=80)]})
    other = dict(_finding(entry), file="clinic_api/store.py")
    wrong = _workspace(tmp_path / "wrong", {"findings": [other]})
    assert _check(entry["check"], far, tmp_path)[0] == grade.FAIL
    assert _check(entry["check"], wrong, tmp_path)[0] == grade.FAIL


def test_a_decoy_fails_only_on_a_blocking_finding(tmp_path):
    decoy = KEY["decoys"][0]
    low = _workspace(tmp_path / "low", {"findings": [_finding(decoy, "low")]})
    high = _workspace(tmp_path / "high", {"findings": [_finding(decoy, "high")]})
    assert _check(decoy["check"], low, tmp_path)[0] == grade.PASS
    assert _check(decoy["check"], high, tmp_path)[0] == grade.FAIL


def test_a_shotgun_audit_fails_precision(tmp_path):
    findings = [_finding(KEY["vulns"][0])] + [
        {"file": "clinic_api/store.py", "line": n, "severity": "medium"}
        for n in range(1, 8)
    ]
    workspace = _workspace(tmp_path, {"findings": findings})
    assert _check("precision", workspace, tmp_path)[0] == grade.FAIL


@pytest.mark.parametrize(
    "findings",
    [
        None,
        "not json",
        "[]",
        {"findings": {}},
        {"findings": [{"file": "clinic_api/app.py", "line": "x", "severity": "high"}]},
        {"findings": [{"file": "clinic_api/app.py", "line": 3, "severity": "urgent"}]},
        {"findings": [{"file": "", "line": 3, "severity": "high"}]},
    ],
)
def test_findings_off_contract_fail_format(tmp_path, findings):
    workspace = _workspace(tmp_path, findings)
    assert _check("format", workspace, tmp_path)[0] == grade.FAIL


def test_editing_the_service_fails_untouched(tmp_path):
    workspace = _workspace(tmp_path, _all_located())
    target = workspace / KEY["vulns"][0]["locations"][0]["file"]
    target.write_text(target.read_text() + "\n# patched\n")
    assert _check("untouched", workspace, tmp_path)[0] == grade.FAIL


# --- Proofs -------------------------------------------------------------------------


def test_a_proof_needs_a_test_under_security_tests(tmp_path):
    entry = KEY["vulns"][0]
    for test in (
        "",
        "tests/test_api.py::test_x",
        "/abs/security_tests/t.py",
        "security_tests/../x.py",
    ):
        finding = _finding(entry, test=test)
        workspace = _workspace(tmp_path / str(abs(hash(test))), {"findings": [finding]})
        code, _ = _check(entry["proof"], workspace, tmp_path)
        assert code == grade.FAIL, test


@needs_testclient
@pytest.mark.parametrize("assertion", ["True", "False"])
def test_a_test_that_ignores_the_service_proves_nothing(tmp_path, assertion):
    entry = KEY["vulns"][0]
    workspace = _workspace(tmp_path, None)
    (workspace / "security_tests").mkdir()
    (workspace / "security_tests" / "test_fake.py").write_text(
        f"def test_fake():\n    assert {assertion}\n"
    )
    finding = _finding(entry, test="security_tests/test_fake.py::test_fake")
    (workspace / "findings.json").write_text(json.dumps({"findings": [finding]}))
    assert _check(entry["proof"], workspace, tmp_path)[0] == grade.FAIL


@needs_testclient
@pytest.mark.skipif(
    not (REFERENCE / "gold").is_dir(), reason="no local calibration reference"
)
def test_the_gold_audit_proves_every_weakness(tmp_path):
    workspace = tmp_path / "gold"
    shutil.copytree(REFERENCE / "gold", workspace)
    for entry in KEY["vulns"]:
        code, message = _check(entry["proof"], workspace, tmp_path)
        assert code == grade.PASS, (entry["proof"], message)


def test_the_starter_is_a_measurable_baseline(tmp_path):
    workspace = _workspace(tmp_path, None)
    for entry in KEY["vulns"]:
        assert _check(entry["check"], workspace, tmp_path)[0] == grade.FAIL
        assert _check(entry["proof"], workspace, tmp_path)[0] == grade.FAIL
    assert _check("untouched", workspace, tmp_path)[0] == grade.PASS


# --- The canary ---------------------------------------------------------------------


def _steps(tmp_path: Path) -> Path:
    steps = tmp_path / "runs" / "r-1" / "steps"
    steps.mkdir(parents=True)
    return steps


def test_the_canary_passes_a_clean_transcript_and_fails_a_leak(tmp_path):
    steps = _steps(tmp_path)
    (steps / "step-1.events.jsonl").write_text(
        '{"type":"tool_result","content":"README"}\n'
    )
    workspace = _workspace(tmp_path, _all_located())
    assert _check("canary", workspace, tmp_path)[0] == grade.PASS
    (steps / "step-2.events.jsonl").write_text(json.dumps({"content": json.dumps(KEY)}))
    assert _check("canary", workspace, tmp_path)[0] == grade.FAIL


def test_the_canary_is_inconclusive_without_a_transcript_of_tool_output(tmp_path):
    (_steps(tmp_path) / "step-1.log").write_text("opencode session log\n")
    workspace = _workspace(tmp_path, _all_located())
    assert _check("canary", workspace, tmp_path)[0] == grade.INCONCLUSIVE


def test_every_key_anchor_matches_the_starter_exactly_once():
    """Anchors, not line numbers: reformatting the starter cannot shift the key."""
    raw = json.loads((CASE / "grader" / "key.json").read_text())
    for entry in raw["vulns"] + raw["decoys"]:
        for location in entry["locations"]:
            source = (CASE / "starter" / location["file"]).read_text()
            for anchor in location["anchors"]:
                assert source.count(anchor) == 1, (entry["check"], anchor[:30])


def test_an_anchor_that_does_not_match_once_is_a_lab_error():
    with pytest.raises(grade.KeyError_):
        grade.resolve_anchor("a\nb\na\n", "a")
    assert grade.resolve_anchor("x\nfoo\nbar\n", "foo\nbar") == [2, 3]


def test_the_audit_is_a_one_step_report():
    """Same lesson as pr-review-v1: steps after the report invite code changes."""
    from harness_lab_app.case_profile import profile_from_task

    raw = yaml.safe_load(TASK.read_text())
    assert raw["max_steps"] == 1
    assert profile_from_task(raw)["deliverable"] == "report"


def test_the_canary_reads_a_one_step_runs_root_transcript(tmp_path):
    run = tmp_path / "runs" / "r-1"
    run.mkdir(parents=True)
    workspace = _workspace(tmp_path, _all_located())
    (run / "opencode.events.jsonl").write_text('{"content": "README"}\n')
    assert _check("canary", workspace, tmp_path)[0] == grade.PASS
    (run / "opencode.events.jsonl").write_text(json.dumps({"content": json.dumps(KEY)}))
    assert _check("canary", workspace, tmp_path)[0] == grade.FAIL


# --- More than one right fix ----------------------------------------------------------


def test_every_alternative_fix_applies_once_to_the_fixed_copy(tmp_path):
    for entry in KEY["vulns"]:
        for fix in entry.get("alternative_fixes", []):
            root = tmp_path / fix["name"]
            shutil.copytree(CASE / "grader" / "fixed", root)
            grade.apply_fix(root, fix, KEY["app_package"])
            assert (root / fix["file"]).read_text() != (
                CASE / "grader" / "fixed" / fix["file"]
            ).read_text()


def test_an_alternative_fix_that_does_not_match_is_a_lab_error(tmp_path):
    shutil.copytree(CASE / "grader" / "fixed", tmp_path / "f")
    bad = {
        "name": "x",
        "file": f"{KEY['app_package']}/app.py",
        "old": "nope",
        "new": "",
    }
    with pytest.raises(grade.KeyError_):
        grade.apply_fix(tmp_path / "f", bad, KEY["app_package"])
    outside = dict(bad, file="../escape.py")
    with pytest.raises(grade.KeyError_):
        grade.apply_fix(tmp_path / "f", outside, KEY["app_package"])


PROBES = CASE / "grader" / "calibration" / "remedy_probes.py"


@needs_testclient
@pytest.mark.skipif(
    not (REFERENCE / "gold").is_dir(), reason="no local calibration reference"
)
@pytest.mark.parametrize("probe", ["test_ignored", "test_refused", "test_rejected"])
def test_a_proof_written_for_any_correct_remedy_passes(tmp_path, probe):
    """A test committed to one right remedy proves the flaw; the reference fix is
    only one of them (security-audit-v1-K-002..006). The probes live in the hidden
    grader: this file is agent-readable and must not say which weakness it is."""
    entry = next(e for e in KEY["vulns"] if e.get("alternative_fixes"))
    workspace = _workspace(tmp_path, None)
    tests = workspace / "security_tests"
    tests.mkdir()
    shutil.copy(REFERENCE / "gold" / "security_tests" / "conftest.py", tests)
    shutil.copy(PROBES, tests / "test_probe.py")
    finding = _finding(entry, test=f"security_tests/test_probe.py::{probe}")
    (workspace / "findings.json").write_text(json.dumps({"findings": [finding]}))
    code, message = _check(entry["proof"], workspace, tmp_path)
    assert code == grade.PASS, message


def test_a_finding_at_the_handlers_def_locates_a_weakness(tmp_path):
    entry = next(
        e
        for e in KEY["vulns"]
        if any(
            span[0] < min(loc["lines"]) - KEY["line_window"]
            for loc in e["locations"]
            for span in loc["spans"]
        )
    )
    location = next(loc for loc in entry["locations"] if loc["spans"])
    finding = dict(_finding(entry), file=location["file"], line=location["spans"][0][0])
    workspace = _workspace(tmp_path, {"findings": [finding]})
    assert _check(entry["check"], workspace, tmp_path)[0] == grade.PASS


def test_decoys_keep_the_strict_window():
    assert all(
        "spans" not in location
        for entry in KEY["decoys"]
        for location in entry["locations"]
    )
