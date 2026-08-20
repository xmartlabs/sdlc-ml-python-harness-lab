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
        # The harness variant wraps the same task with the governance preamble
        # (treatment arm of the harness A/B) — see attribution reading surface.
        harness_prompt = case.prompt_variants["swe_harness"]
        assert "test_config_from_file_toml" in harness_prompt
        assert ".github/architecture.md" in harness_prompt
        assert "systematic_debugging" in harness_prompt
        assert harness_prompt.endswith(prompt)


def test_import_swebench_instance_is_idempotent():
    with _session() as session:
        service = CaseService(session)
        first = service.import_swebench_instance(_spec())
        first_id = first.id
        again = service.import_swebench_instance(_spec())
        assert again.id == first_id


def test_write_case_spec_for_swe_case(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from harness_lab_app import tasks

    monkeypatch.setattr(
        tasks, "get_settings", lambda: SimpleNamespace(data_runs_dir=tmp_path)
    )
    with _session() as session:
        CaseService(session).import_swebench_instance(_spec())
        tasks._write_case_spec(session, "run-x", "pallets__flask-4992", "swe_default")

    spec_path = tmp_path / "run-x" / "case_spec.json"
    assert spec_path.exists()
    written = json.loads(spec_path.read_text())
    assert written["repo"] == "pallets/flask"
    assert written["repo_url"] == "https://github.com/pallets/flask.git"
    assert written["fail_to_pass"] == _spec()["fail_to_pass"]
    assert written["install_pins"] == ["Werkzeug<3"]
    assert "flask.Config.from_file" in written["prompt"]


def test_write_case_spec_noop_for_missing_case(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from harness_lab_app import tasks

    monkeypatch.setattr(
        tasks, "get_settings", lambda: SimpleNamespace(data_runs_dir=tmp_path)
    )
    with _session() as session:
        # Unknown case → no spec written, no crash.
        tasks._write_case_spec(session, "run-y", "does-not-exist", "swe_default")
    assert not (tmp_path / "run-y").exists()


def test_import_swebench_endpoint_creates_case(monkeypatch):
    import pytest
    from fastapi import HTTPException

    from harness_lab_app import api as api_module
    from harness_lab_app.api import create_app
    from harness_lab_app.schemas import SwebenchImportRequest
    from harness_lab_app.swebench_client import SwebenchFetchError

    app = create_app()
    endpoint = None
    for route in app.router.routes:
        if getattr(route, "path", None) == "/api/cases/import-swebench":
            endpoint = route.endpoint
    assert endpoint is not None

    monkeypatch.setattr(api_module, "normalize_instance", lambda _id: _spec())
    with _session() as session:
        case = endpoint(
            SwebenchImportRequest(instance_id="pallets__flask-4992"), session
        )
        assert case.case_id == "pallets__flask-4992"
        # Idempotent: importing again returns the same case.
        again = endpoint(
            SwebenchImportRequest(instance_id="pallets__flask-4992"), session
        )
        assert again.id == case.id

    # Network trouble surfaces as 502; unknown instance as 404.
    def _boom(_id):
        raise SwebenchFetchError("HF unreachable")

    monkeypatch.setattr(api_module, "normalize_instance", _boom)
    with _session() as session, pytest.raises(HTTPException) as exc:
        endpoint(SwebenchImportRequest(instance_id="pallets__flask-4992"), session)
    assert exc.value.status_code == 502

    def _missing(_id):
        raise ValueError("Instance not found in SWE-bench Lite: nope__nope-1")

    monkeypatch.setattr(api_module, "normalize_instance", _missing)
    with _session() as session, pytest.raises(HTTPException) as exc:
        endpoint(SwebenchImportRequest(instance_id="nope__nope-1"), session)
    assert exc.value.status_code == 404


def test_swebench_import_request_validates_instance_shape():
    import pytest
    from pydantic import ValidationError

    from harness_lab_app.schemas import SwebenchImportRequest

    assert (
        SwebenchImportRequest(instance_id=" pallets__flask-4992 ").instance_id
        == "pallets__flask-4992"
    )
    for bad in ("flask-4992", "pallets__flask", "pallets flask 4992", ""):
        with pytest.raises(ValidationError):
            SwebenchImportRequest(instance_id=bad)


def test_normalize_instance_shapes_spec(monkeypatch):
    from harness_lab_app import swebench_client

    record = {
        "instance_id": "pallets__flask-4992",
        "repo": "pallets/flask",
        "base_commit": "4c288bc97ea371817199908d0d9b12de9dae327e",
        "problem_statement": "Add text mode.",
        "test_patch": "diff --git a/x b/x\n",
        "FAIL_TO_PASS": json.dumps(["tests/test_config.py::test_a"]),
        "PASS_TO_PASS": json.dumps(["tests/test_config.py::test_b"]),
        "version": "2.3",
    }
    monkeypatch.setattr(swebench_client, "fetch_instance", lambda _id, **_kw: record)
    spec = swebench_client.normalize_instance("pallets__flask-4992")
    assert spec["fail_to_pass"] == ["tests/test_config.py::test_a"]
    assert spec["repo_url"] == "https://github.com/pallets/flask.git"
    assert spec["python"] == "3.11"
    assert spec["install_pins"] == ["Werkzeug<3"]


def test_write_case_spec_skips_non_swe_case_with_subject_repo(tmp_path, monkeypatch):
    # Regression: Tetris carries subject_repo for provenance but is NOT a repo
    # task — it must not get a case_spec.json (which would try to clone it).
    from types import SimpleNamespace

    from harness_lab_app import tasks
    from harness_lab_app.models import Case, CaseType

    monkeypatch.setattr(
        tasks, "get_settings", lambda: SimpleNamespace(data_runs_dir=tmp_path)
    )
    # A stale spec from a prior run reusing this id must be cleaned up.
    run_dir = tmp_path / "tetris-run-1"
    run_dir.mkdir()
    (run_dir / "case_spec.json").write_text("{}")

    with _session() as session:
        session.add(
            Case(
                case_id="tetris-v1",
                name="Tetris",
                type=CaseType.build_from_scratch,
                subject_repo="ml-python-base",
                base_commit="deadbeef",
            )
        )
        session.commit()
        tasks._write_case_spec(session, "tetris-run-1", "tetris-v1", "sdlc")

    assert not (run_dir / "case_spec.json").exists()
