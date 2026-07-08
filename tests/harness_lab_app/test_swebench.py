from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

from harness_lab_app.case_service import CaseService
from harness_lab_app.models import CaseType

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import swebench_common  # noqa: E402


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_repo_url_from():
    assert swebench_common.repo_url_from("pallets/flask") == (
        "https://github.com/pallets/flask.git"
    )


def test_env_spec_known_and_default():
    flask = swebench_common.env_spec("pallets/flask")
    assert flask["python"] == "3.11"
    assert "Werkzeug<3" in flask["pins"]
    other = swebench_common.env_spec("some/unknown")
    assert other["python"] == "3.11"
    assert other["pins"] == []


def test_run_pytest_parses_status_first_lines(monkeypatch):
    # pytest -rA prints "PASSED <nodeid>" / "FAILED <nodeid>" (status first).
    fake_output = (
        "PASSED tests/test_config.py::test_a\n"
        "FAILED tests/test_config.py::test_b\n"
        "PASSED tests/test_config.py::test_c\n"
        "1 failed, 2 passed in 0.01s\n"
    )

    class _Result:
        stdout = fake_output
        stderr = ""
        returncode = 1

    monkeypatch.setattr(swebench_common, "_run", lambda *_a, **_k: _Result())
    parsed = swebench_common.run_pytest(
        Path("."), "python", ["tests/test_config.py::test_a"]
    )
    assert parsed["passed"] == 2
    assert parsed["failed"] == 1
    assert parsed["per_test"]["tests/test_config.py::test_b"] is False


def test_evaluate_resolved_logic(monkeypatch):
    per = {"f1": True, "p1": True, "p2": True}

    monkeypatch.setattr(
        swebench_common,
        "run_pytest",
        lambda *_a, **_k: {"per_test": per, "raw": "", "passed": 3, "failed": 0},
    )
    result = swebench_common.evaluate(Path("."), "python", ["f1"], ["p1", "p2"])
    assert result["resolved"] is True
    assert result["f2p_passed"] == 1
    assert result["p2p_passed"] == 2

    per2 = {"f1": False, "p1": True, "p2": True}
    monkeypatch.setattr(
        swebench_common,
        "run_pytest",
        lambda *_a, **_k: {"per_test": per2, "raw": "", "passed": 2, "failed": 1},
    )
    result2 = swebench_common.evaluate(Path("."), "python", ["f1"], ["p1", "p2"])
    assert result2["resolved"] is False


def _spec() -> dict:
    return {
        "instance_id": "pallets__flask-4992",
        "repo": "pallets/flask",
        "base_commit": "4c288bc97ea371817199908d0d9b12de9dae327e",
        "problem_statement": "Add a file mode parameter to flask.Config.from_file().",
        "test_patch": "diff --git a/tests/test_config.py b/tests/test_config.py\n",
        "fail_to_pass": ["tests/test_config.py::test_config_from_file_toml"],
        "pass_to_pass": ["tests/test_config.py::test_config_from_pyfile"],
        "version": "2.3",
        "repo_url": "https://github.com/pallets/flask.git",
        "python": "3.11",
        "install_pins": ["Werkzeug<3"],
    }


def test_import_swebench_instance_builds_case():
    with _session() as session:
        case = CaseService(session).import_swebench_instance(_spec())
        assert case.case_id == "pallets__flask-4992"
        assert case.type == CaseType.swe_bench_instance
        assert case.subject_repo == "pallets/flask"
        assert case.base_commit.startswith("4c288bc9")
        # Validation delegates to the swebench_validate helper.
        assert any("swebench_validate" in c for c in case.validation_commands)
        # SWE-bench payload round-trips through metadata_json (JSON-encoded lists).
        meta = case.metadata_json
        assert json.loads(meta["fail_to_pass"]) == _spec()["fail_to_pass"]
        assert meta["repo_url"] == "https://github.com/pallets/flask.git"
        assert json.loads(meta["install_pins"]) == ["Werkzeug<3"]
        # The prompt frames the task and names the failing test.
        prompt = case.prompt_variants["swe_default"]
        assert "test_config_from_file_toml" in prompt
        assert "pallets/flask" in prompt


def test_import_swebench_instance_is_idempotent():
    with _session() as session:
        service = CaseService(session)
        first = service.import_swebench_instance(_spec())
        first_id = first.id
        again = service.import_swebench_instance(_spec())
        assert again.id == first_id
