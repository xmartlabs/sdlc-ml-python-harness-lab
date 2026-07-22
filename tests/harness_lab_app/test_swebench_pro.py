"""SWE-bench Pro lane unit tests — no network, no docker.

Covers the dataset client normalization (Python-repr list parsing, image tag
derivation), the Case import (prompt variants, metadata payload, gold-patch
exclusion), the case_spec staging for the runner, and the containerized-eval
verdict logic against a faked docker run. See
docs/design-swe-bench-pro-lane.md.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

from harness_lab_app import swebench_pro_client, tasks
from harness_lab_app.case_service import CaseService
from harness_lab_app.models import CaseType
from harness_lab_app.validation import build_validation_spec

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import swebench_pro_common  # noqa: E402


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

_IID = "instance_ansible__ansible-0000000000000000000000000000000000000000-vnan"


def _raw_row() -> dict:
    return {
        "instance_id": _IID,
        "repo": "ansible/ansible",
        "repo_language": "python",
        "base_commit": "0" * 40,
        "problem_statement": "Fix the frobnicator.",
        "requirements": "- frobnicate must be idempotent",
        "interface": "Type: Function\nName: frobnicate",
        "test_patch": "diff --git a/test_frob.py b/test_frob.py\n",
        # Pro columns are Python-repr strings, single quotes and all.
        "fail_to_pass": "['test/units/test_frob.py::test_frob']",
        "pass_to_pass": '["test/units/test_frob.py::test_ok"]',
        "selected_test_files_to_run": "['test/units/test_frob.py']",
        "before_repo_set_cmd": "git reset --hard 0000000",
        "dockerhub_tag": "ignored-derived-instead",
        "patch": "diff --git a/frob.py b/frob.py\n",
    }


def test_literal_list_parses_python_repr_and_json():
    assert swebench_pro_client._literal_list("['a', 'b']") == ["a", "b"]
    assert swebench_pro_client._literal_list('["a"]') == ["a"]
    assert swebench_pro_client._literal_list([1, "b"]) == ["1", "b"]
    assert swebench_pro_client._literal_list("  ") == []


def test_image_uri_strips_vnan_and_truncates():
    uri = swebench_pro_client.image_uri(_IID, "ansible/ansible")
    assert uri.startswith("jefzda/sweap-images:ansible.ansible-ansible__ansible-")
    assert not uri.endswith("-vnan")
    long_iid = "instance_qutebrowser__qutebrowser-" + "a" * 40 + "-v" + "b" * 40
    tag = swebench_pro_client.image_uri(long_iid, "qutebrowser/qutebrowser").split(
        ":", 1
    )[1]
    assert len(tag) <= 128


def test_normalize_instance_shapes_spec(monkeypatch):
    monkeypatch.setattr(
        swebench_pro_client, "fetch_instance", lambda *_a, **_k: _raw_row()
    )
    monkeypatch.setattr(
        swebench_pro_client,
        "fetch_run_assets",
        lambda *_a, **_k: {"run_script.sh": "#!/bin/bash\n", "parser.py": "# parse\n"},
    )
    spec = swebench_pro_client.normalize_instance(_IID)
    assert spec["fail_to_pass"] == ["test/units/test_frob.py::test_frob"]
    assert spec["selected_test_files_to_run"] == ["test/units/test_frob.py"]
    assert spec["dockerhub_image"].startswith("jefzda/sweap-images:")
    assert spec["run_script"].startswith("#!/bin/bash")
    assert spec["gold_patch"].startswith("diff --git")


def test_normalize_instance_rejects_non_python(monkeypatch):
    row = _raw_row() | {"repo_language": "go"}
    monkeypatch.setattr(swebench_pro_client, "fetch_instance", lambda *_a, **_k: row)
    try:
        swebench_pro_client.normalize_instance(_IID)
        raise AssertionError("expected ValueError for non-python instance")
    except ValueError as exc:
        assert "go" in str(exc)


def test_summarize_row_compacts_catalog_entry():
    entry = swebench_pro_client._summarize_row(
        _raw_row()
        | {
            "problem_statement": "**Title: Fix the frobnicator**\n\nLong body…",
            "issue_categories": '["major_bug","data_bug"]',
        }
    )
    assert entry["instance_id"] == _IID
    assert entry["repo"] == "ansible/ansible"
    assert entry["title"] == "Fix the frobnicator"
    assert entry["summary"] == "Long body…"
    assert entry["issue_categories"] == ["major_bug", "data_bug"]
    assert entry["f2p_count"] == 1
    assert entry["p2p_count"] == 1


def test_summarize_row_unescapes_json_style_statement():
    # Some Pro rows arrive as a quoted string with literal \n sequences and a
    # bare "Title" header line.
    entry = swebench_pro_client._summarize_row(
        _raw_row()
        | {"problem_statement": '"Title\\n\\nTypeError combining dicts\\n\\nBody…"'}
    )
    assert entry["title"] == "TypeError combining dicts"
    assert entry["summary"] == "Body…"


def test_title_and_summary_single_line_statement():
    # openlibrary-style: the whole issue on one line with inline ## sections.
    title, summary = swebench_pro_client.title_and_summary(
        "## Title: Allow Import API to Bypass Validation ## Description "
        "**Label:** Feature Request The current import fails when validation "
        "rules trigger."
    )
    assert title == "Allow Import API to Bypass Validation"
    assert summary.startswith("Description **Label:** Feature Request") or (
        "current import fails" in summary
    )


def test_title_and_summary_skips_header_noise():
    title, summary = swebench_pro_client.title_and_summary(
        "# Bug Report\n\n## Summary\n\nINI values are not unquoted correctly\n\n"
        "## Description\n\nSince 2.15 string values keep their quotes."
    )
    assert title == "INI values are not unquoted correctly"
    assert summary == "Since 2.15 string values keep their quotes."


def test_clean_text_unescapes_only_when_needed():
    assert swebench_pro_client._clean_text('"a\\nb"') == "a\nb"
    # Real newlines present → backslash sequences are content, keep them.
    assert swebench_pro_client._clean_text("a\nliteral \\n stays") == (
        "a\nliteral \\n stays"
    )


def test_list_instances_uses_disk_cache(monkeypatch, tmp_path):
    cache = tmp_path / "catalog.json"
    cache.write_text(json.dumps([{"instance_id": "x", "repo": "r"}]))
    monkeypatch.setattr(swebench_pro_client, "_CATALOG_CACHE", cache)
    # Fresh cache → no network call (urlopen would explode).
    monkeypatch.setattr(
        swebench_pro_client.urllib.request,
        "urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("network hit")),
    )
    assert swebench_pro_client.list_instances() == [{"instance_id": "x", "repo": "r"}]


# ---------------------------------------------------------------------------
# Case import
# ---------------------------------------------------------------------------


def _pro_spec() -> dict:
    return {
        "instance_id": _IID,
        "repo": "ansible/ansible",
        "repo_language": "python",
        "base_commit": "0" * 40,
        "problem_statement": "Fix the frobnicator.",
        "requirements": "- frobnicate must be idempotent",
        "interface": "Type: Function\nName: frobnicate",
        "test_patch": "diff --git a/test_frob.py b/test_frob.py\n",
        "fail_to_pass": ["test/units/test_frob.py::test_frob"],
        "pass_to_pass": ["test/units/test_frob.py::test_ok"],
        "selected_test_files_to_run": ["test/units/test_frob.py"],
        "before_repo_set_cmd": "git reset --hard 0000000",
        "dockerhub_image": "jefzda/sweap-images:ansible.ansible-x",
        "run_script": "#!/bin/bash\npytest\n",
        "parser": "# parser\n",
        "gold_patch": "diff --git a/frob.py b/frob.py\n",
    }


def test_import_swebench_pro_instance_builds_case():
    with _session() as session:
        case = CaseService(session).import_swebench_pro_instance(_pro_spec())
        assert case.type == CaseType.swe_pro_instance
        assert case.subject_repo == "ansible/ansible"
        # Display fields come from the heuristic extraction, not the raw id/mush.
        assert case.name == "Fix the frobnicator."
        assert case.description == "Fix the frobnicator."
        assert any("swebench_pro_validate" in c for c in case.validation_commands)
        # Both A/B arms exist; the treatment arm wraps the same task.
        default = case.prompt_variants["swe_default"]
        harness = case.prompt_variants["swe_harness"]
        assert "test_frob" in default
        assert "## Requirements" in default
        assert "## Interface" in default
        assert "docker run" in default
        assert harness.endswith(default)
        assert ".github/architecture.md" in harness
        # The gold patch must never be persisted (agent-readable channel).
        meta = case.metadata_json
        assert "gold_patch" not in meta
        assert "diff --git a/frob.py" not in json.dumps(meta)
        assert meta["dockerhub_image"].startswith("jefzda/")


def test_import_swebench_pro_instance_is_idempotent():
    with _session() as session:
        service = CaseService(session)
        first = service.import_swebench_pro_instance(_pro_spec())
        again = service.import_swebench_pro_instance(_pro_spec())
        assert first.id == again.id


def test_reimport_refreshes_prompt_variants():
    """Re-import rebuilds the prompts from the current templates, so prompt
    improvements (e.g. the sandbox env contract) reach existing cases."""
    with _session() as session:
        service = CaseService(session)
        case = service.import_swebench_pro_instance(_pro_spec())
        case.prompt_variants = {"swe_default": "OLD", "swe_harness": "OLD"}
        session.add(case)
        session.commit()
        refreshed = service.import_swebench_pro_instance(_pro_spec())
        assert "sandbox does **not** have" in refreshed.prompt_variants["swe_default"]
        assert refreshed.prompt_variants["swe_harness"].startswith("# Engineering")
        # Conductor arm: planner/executor roles wrapping the same task.
        conductor = refreshed.prompt_variants["swe_conductor"]
        assert conductor.startswith("# Conductor mode")
        assert "You are the **conductor**" in conductor
        assert conductor.endswith(refreshed.prompt_variants["swe_default"])


def test_import_swebench_pro_endpoint_creates_case(monkeypatch):
    from harness_lab_app import api as api_module
    from harness_lab_app.api import create_app
    from harness_lab_app.schemas import SwebenchImportRequest

    app = create_app()
    endpoint = None
    for route in app.router.routes:
        if getattr(route, "path", None) == "/api/cases/import-swebench-pro":
            endpoint = route.endpoint
    assert endpoint is not None

    monkeypatch.setattr(api_module, "pro_normalize_instance", lambda _id: _pro_spec())
    with _session() as session:
        case = endpoint(SwebenchImportRequest(instance_id=_IID), session)
        assert case.case_id == _IID
        assert case.type == CaseType.swe_pro_instance


# ---------------------------------------------------------------------------
# case_spec staging (worker → runner channel)
# ---------------------------------------------------------------------------


def test_write_case_spec_stages_pro_payload(tmp_path, monkeypatch):
    with _session() as session:
        CaseService(session).import_swebench_pro_instance(_pro_spec())

        class _Settings:
            data_runs_dir = tmp_path

        monkeypatch.setattr(tasks, "get_settings", lambda: _Settings())
        tasks._write_case_spec(session, "run-pro-1", _IID, "swe_harness")
        spec = json.loads((tmp_path / "run-pro-1" / "case_spec.json").read_text())
        assert spec["case_type"] == "swe_pro"
        assert spec["dockerhub_image"].startswith("jefzda/")
        assert spec["fail_to_pass"] == ["test/units/test_frob.py::test_frob"]
        assert spec["run_script"].startswith("#!/bin/bash")
        assert spec["prompt_variant"] == "swe_harness"
        assert ".github/architecture.md" in spec["prompt"]
        assert "gold_patch" not in spec


def test_validation_spec_marks_pro_local():
    with _session() as session:
        case = CaseService(session).import_swebench_pro_instance(_pro_spec())
        spec = build_validation_spec(case, "run-pro-1")
        assert spec["local"] is True
        assert any("swebench_pro_validate" in " ".join(c) for c in spec["commands"])


# ---------------------------------------------------------------------------
# Containerized eval verdict (faked docker run)
# ---------------------------------------------------------------------------


def _fake_docker_eval(monkeypatch, tmp_path, tests_json):
    """Fake `_run` so the docker invocation writes parser output into /eval."""

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, cwd=None, timeout=None):
        if cmd[0] == "docker" and cmd[1] == "run":
            # The stage dir is the source of the '/eval' bind mount.
            stage = Path(next(m for m in cmd if m.endswith(":/eval")).split(":")[0])
            (stage / "stdout.log").write_text("pytest output")
            (stage / "stderr.log").write_text("")
            (stage / "output.json").write_text(json.dumps(tests_json))
        return _Result()

    monkeypatch.setattr(swebench_pro_common, "_run", fake_run)
    monkeypatch.setattr(swebench_pro_common, "_REPO_ROOT", tmp_path)


def _eval_spec() -> dict:
    return {
        "dockerhub_image": "jefzda/sweap-images:x",
        "run_script": "#!/bin/bash\n",
        "parser": "# parser\n",
        "fail_to_pass": ["t/f.py::test_f"],
        "pass_to_pass": ["t/p.py::test_p"],
        "selected_test_files_to_run": ["t/f.py", "t/p.py"],
    }


def test_evaluate_containerized_resolved(monkeypatch, tmp_path):
    _fake_docker_eval(
        monkeypatch,
        tmp_path,
        {
            "tests": [
                {"name": "t/f.py::test_f", "status": "PASSED"},
                {"name": "t/p.py::test_p", "status": "PASSED"},
            ]
        },
    )
    verdict = swebench_pro_common.evaluate_containerized(tmp_path / "ws", _eval_spec())
    assert verdict["resolved"] is True
    assert verdict["f2p_passed"] == 1
    assert verdict["p2p_passed"] == 1


def test_evaluate_containerized_regression_not_resolved(monkeypatch, tmp_path):
    _fake_docker_eval(
        monkeypatch,
        tmp_path,
        {
            "tests": [
                {"name": "t/f.py::test_f", "status": "PASSED"},
                {"name": "t/p.py::test_p", "status": "FAILED"},
            ]
        },
    )
    verdict = swebench_pro_common.evaluate_containerized(tmp_path / "ws", _eval_spec())
    assert verdict["resolved"] is False
    assert verdict["p2p_passed"] == 0


def test_evaluate_containerized_missing_output_is_failure(monkeypatch, tmp_path):
    class _Result:
        returncode = 1
        stdout = ""
        stderr = "docker exploded"

    monkeypatch.setattr(swebench_pro_common, "_run", lambda *_a, **_k: _Result())
    monkeypatch.setattr(swebench_pro_common, "_REPO_ROOT", tmp_path)
    verdict = swebench_pro_common.evaluate_containerized(tmp_path / "ws", _eval_spec())
    assert verdict["resolved"] is False
    assert verdict["error"] == "parser produced no output.json"


def test_provision_run_resets_stale_workspace(monkeypatch, tmp_path):
    """The worker owns managed-run freshness: leftovers are wiped, not skipped.

    Regression: a pre-existing (even fully provisioned) workspace used to be
    skipped, and the runner's --fresh reset then deleted it — leaving the
    docker-less runner with nothing it could rebuild.
    """
    run_id = "run-pro-fresh"
    run_dir = tmp_path / "data" / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "case_spec.json").write_text(
        json.dumps({"case_type": "swe_pro", "dockerhub_image": "jefzda/x:y"})
    )
    workspace = tmp_path / "workspace" / run_id
    (workspace / ".git").mkdir(parents=True)
    (workspace / "stale-agent-edit.py").write_text("leftover")

    provisioned: list[Path] = []

    def fake_provision(ws: Path, _spec: dict) -> None:
        provisioned.append(ws)
        ws.mkdir(parents=True, exist_ok=True)
        (ws / ".git").mkdir(exist_ok=True)

    monkeypatch.setattr(swebench_pro_common, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(swebench_pro_common, "provision", fake_provision)
    assert swebench_pro_common._provision_run(run_id) == 0
    assert provisioned == [workspace]
    assert not (workspace / "stale-agent-edit.py").exists()


def test_host_path_translation(monkeypatch):
    monkeypatch.setenv("HARNESS_LAB_HOST_REPO_ROOT", "/Users/me/lab")
    assert (
        swebench_pro_common._host_path(Path("/app/workspace/r1"))
        == "/Users/me/lab/workspace/r1"
    )
    monkeypatch.delenv("HARNESS_LAB_HOST_REPO_ROOT")
    # Outside /app (or with no host root set) the resolved path passes through.
    out = swebench_pro_common._host_path(Path("/opt/elsewhere/x"))
    assert out.endswith("/x")
