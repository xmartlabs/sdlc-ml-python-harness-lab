"""A case names its own machine: image, sandbox and play command (ADR-0078)."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from harness_lab_app import (
    executors,
    tasks,
    validation_service as vs_module,
)
from harness_lab_app.case_profile import (
    PROFILE_KEYS,
    CaseProfileError,
    image_is_allowed,
    play_command_for,
    profile_from_task,
    runner_image_for,
    sandbox_image_for,
)
from harness_lab_app.case_service import CaseService
from harness_lab_app.executors import DockerRunExecutor
from harness_lab_app.models import Case, CaseStatus, CaseType
from harness_lab_app.run_service import RunService
from harness_lab_app.sandbox_service import VALID_KINDS
from harness_lab_app.schemas import RunConfigCreate, RunCreate
from harness_lab_app.validation import build_validation_spec
from harness_lab_app.validation_service import ValidationService

REPO_ROOT = Path(__file__).resolve().parents[2]
GODOT_RUNNER = "harness-lab-runner-godot:latest"
GODOT_SANDBOX = "harness-lab-sandbox-godot:latest"


def make_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def seed(session: Session, run_id: str, metadata: dict[str, Any]) -> None:
    session.add(
        Case(
            case_id="game-case",
            name="game-case",
            type=CaseType.build_from_scratch,
            status=CaseStatus.active,
            validation_commands=["cd {workspace} && test -f project.godot"],
            metadata_json=metadata,
        )
    )
    session.commit()
    RunService(session).create_run(
        RunCreate(
            run_id=run_id,
            config=RunConfigCreate(
                case_id="game-case", prompt_variant="default", executor_model="m"
            ),
        )
    )


# --- The profile itself ---------------------------------------------------------


@pytest.mark.parametrize(
    "image",
    [
        "harness-lab-runner-godot",
        "harness-lab-runner-godot:latest",
        "harness-lab-x:1.2",
    ],
)
def test_a_lab_image_is_allowed(image: str) -> None:
    assert image_is_allowed(image)


@pytest.mark.parametrize(
    "image",
    [
        "ubuntu:24.04",
        "docker.io/harness-lab-runner:latest",
        "evil/harness-lab-runner",
        "harness-lab-runner; rm -rf /",
        "harness-lab-",
        "",
    ],
)
def test_anything_else_is_refused(image: str) -> None:
    """A case file must not make the worker pull and run an arbitrary image."""
    assert not image_is_allowed(image)
    with pytest.raises(CaseProfileError):
        runner_image_for(SimpleNamespace(metadata_json={"runner_image": image or "x"}))


def test_a_case_without_a_profile_uses_the_shared_images() -> None:
    case = SimpleNamespace(metadata_json={"starter_dir": "benchmarks/x/starter"})
    assert runner_image_for(case) == ""
    assert sandbox_image_for(case) == ""
    assert play_command_for(case) == ""


def test_a_play_command_is_one_short_line() -> None:
    ok = SimpleNamespace(metadata_json={"play_command": "godot --path /work"})
    assert play_command_for(ok) == "godot --path /work"
    for bad in ("godot\nrm -rf /work", "x" * 401):
        with pytest.raises(CaseProfileError):
            play_command_for(SimpleNamespace(metadata_json={"play_command": bad}))


def test_profile_from_task_keeps_only_what_was_declared() -> None:
    assert profile_from_task({"task_id": "t"}) == {}
    profile = profile_from_task(
        {"runner_image": GODOT_RUNNER, "play_command": "godot --path /work"}
    )
    assert profile == {
        "runner_image": GODOT_RUNNER,
        "play_command": "godot --path /work",
    }
    assert set(profile) <= set(PROFILE_KEYS)


def test_a_bad_image_in_task_yaml_fails_at_seed_time() -> None:
    with pytest.raises(CaseProfileError):
        profile_from_task({"runner_image": "alpine"})


# --- Seeding --------------------------------------------------------------------


def test_task_yaml_carries_the_profile_into_the_case_and_reseed_refreshes_it(
    tmp_path: Path,
) -> None:
    task = tmp_path / "task.yaml"
    task.write_text(
        yaml.safe_dump(
            {
                "task_id": "game-case",
                "runner_image": GODOT_RUNNER,
                "sandbox_image": GODOT_SANDBOX,
                "play_command": "godot --path /work",
                "validation_commands": ["cd {workspace} && test -f project.godot"],
            }
        )
    )
    engine = make_engine()
    with Session(engine) as session:
        case = CaseService(session).import_task_yaml(task)
        assert case.metadata_json["runner_image"] == GODOT_RUNNER
        assert case.metadata_json["sandbox_image"] == GODOT_SANDBOX
        assert case.metadata_json["play_command"] == "godot --path /work"


def test_the_shipped_3d_case_declares_its_own_machine() -> None:
    raw = yaml.safe_load((REPO_ROOT / "benchmarks/chess3d-v1/task.yaml").read_text())
    profile = profile_from_task(raw)
    assert profile["runner_image"] == GODOT_RUNNER
    assert profile["sandbox_image"] == GODOT_SANDBOX
    assert profile["play_command"]
    # Every other case keeps the shared images: the engine is opt-in.
    for task in sorted((REPO_ROOT / "benchmarks").glob("*/task.yaml")):
        if task.parent.name != "chess3d-v1":
            other = profile_from_task(yaml.safe_load(task.read_text()))
            assert "runner_image" not in other, task
            assert "sandbox_image" not in other, task


# --- The executor ---------------------------------------------------------------


class _Container:
    def logs(self, **_kwargs: Any) -> Iterator[bytes]:
        yield b"ok\n"

    def wait(self) -> dict[str, int]:
        return {"StatusCode": 0}

    def remove(self, force: bool = False) -> None:
        pass


class _Client:
    def __init__(self, known: set[str]):
        self.known = known
        self.run_kwargs: dict[str, Any] = {}
        self.containers = SimpleNamespace(run=self._run, get=self._get)
        self.images = SimpleNamespace(get=self._image)

    def _run(self, **kwargs: Any) -> _Container:
        self.run_kwargs = kwargs
        return _Container()

    def _get(self, name: str) -> Any:
        raise RuntimeError(name)

    def _image(self, name: str) -> object:
        if name not in self.known:
            raise RuntimeError(f"No such image: {name}")
        return object()


@pytest.fixture
def docker_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    settings = SimpleNamespace(
        repo_root=tmp_path,
        docker_host_alias="host.docker.internal",
        host_repo_root="/host/repo",
        runner_image="harness-lab-runner:latest",
        execution_backend="docker",
        data_runs_dir=tmp_path / "data" / "runs",
        validation_network="none",
    )
    monkeypatch.setattr(executors, "get_settings", lambda: settings)
    return settings


def test_the_executor_runs_the_image_it_is_given(docker_settings) -> None:
    client = _Client({"harness-lab-runner:latest", GODOT_RUNNER})
    DockerRunExecutor(client_factory=lambda: client).start(
        "r", ["true"], image=GODOT_RUNNER
    )
    assert client.run_kwargs["image"] == GODOT_RUNNER


def test_without_an_image_the_executor_runs_the_shared_runner(docker_settings) -> None:
    client = _Client({"harness-lab-runner:latest"})
    DockerRunExecutor(client_factory=lambda: client).start("r", ["true"])
    assert client.run_kwargs["image"] == "harness-lab-runner:latest"


def test_a_missing_case_image_names_its_own_build_command(docker_settings) -> None:
    client = _Client({"harness-lab-runner:latest"})
    with pytest.raises(RuntimeError, match="make images-3d"):
        DockerRunExecutor(client_factory=lambda: client).start(
            "r", ["true"], image=GODOT_RUNNER
        )


# --- Validation -----------------------------------------------------------------


def test_the_validation_spec_carries_the_case_image() -> None:
    case = SimpleNamespace(
        type=CaseType.build_from_scratch,
        validation_commands=["cd {workspace} && test -f project.godot"],
        metadata_json={"runner_image": GODOT_RUNNER},
    )
    assert build_validation_spec(case, "run-1")["image"] == GODOT_RUNNER
    # A target's own gates are the repository's commands: the shared runner.
    assert build_validation_spec(case, "run-1", ["make test"])["image"] == ""
    plain = SimpleNamespace(**{**vars(case), "metadata_json": {}})
    assert build_validation_spec(plain, "run-1")["image"] == ""


class _RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def start(self, run_id: str, command: list[str], **kwargs: Any):
        self.calls.append(kwargs)
        return SimpleNamespace(
            iter_lines=lambda: iter(()), wait=lambda: 0, stop=lambda: None
        )


def test_validation_runs_every_check_and_its_setup_in_the_case_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = tmp_path / "data" / "runs"
    monkeypatch.setattr(
        vs_module, "get_settings", lambda: SimpleNamespace(data_runs_dir=runs)
    )
    engine = make_engine()
    with Session(engine) as session:
        seed(
            session,
            "game-1",
            {
                "runner_image": GODOT_RUNNER,
                "validation_requirements": ["chess==1.11.2"],
            },
        )
        (runs / "game-1").mkdir(parents=True)
        executor = _RecordingExecutor()
        ValidationService(session, executor=executor).validate_run("game-1")
    assert executor.calls, "nothing was run"
    assert {call.get("image") for call in executor.calls} == {GODOT_RUNNER}
    assert {call.get("phase") for call in executor.calls} == {"setup", "validate"}


def test_a_case_on_the_shared_image_reaches_the_executor_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = tmp_path / "data" / "runs"
    monkeypatch.setattr(
        vs_module, "get_settings", lambda: SimpleNamespace(data_runs_dir=runs)
    )
    engine = make_engine()
    with Session(engine) as session:
        seed(session, "plain-1", {})
        (runs / "plain-1").mkdir(parents=True)
        executor = _RecordingExecutor()
        ValidationService(session, executor=executor).validate_run("plain-1")
    assert all("image" not in call for call in executor.calls)


# --- The agent's run --------------------------------------------------------------


def test_the_agent_runs_in_the_case_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = make_engine()
    with Session(engine) as session:
        seed(session, "game-run-1", {"runner_image": GODOT_RUNNER})

    class Settings:
        run_timeout_seconds = 3600
        run_stall_seconds = 3600
        run_model_wait_seconds = 3600
        run_max_stream_errors = 0
        data_runs_dir = tmp_path / "runs"

    monkeypatch.setattr(tasks, "get_settings", lambda: Settings())
    seen: dict[str, Any] = {}

    class Executor:
        def start(self, run_id: str, command: list[str], **kwargs: Any):
            seen.update(kwargs)
            return SimpleNamespace(
                iter_lines=lambda: iter(()), wait=lambda: 0, stop=lambda: None
            )

    tasks._execute_run_body("game-run-1", Executor(), db_engine=engine)
    assert seen.get("image") == GODOT_RUNNER


def test_a_case_naming_a_foreign_image_fails_before_any_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = make_engine()
    with Session(engine) as session:
        seed(session, "game-run-2", {"runner_image": "ubuntu:24.04"})

    class Settings:
        run_timeout_seconds = 3600
        run_stall_seconds = 3600
        run_model_wait_seconds = 3600
        run_max_stream_errors = 0
        data_runs_dir = tmp_path / "runs"

    monkeypatch.setattr(tasks, "get_settings", lambda: Settings())
    started: list[str] = []

    class Executor:
        def start(self, run_id: str, command: list[str], **kwargs: Any):
            started.append(run_id)
            raise AssertionError("must not start")

    tasks._execute_run_body("game-run-2", Executor(), db_engine=engine)
    assert started == []
    with Session(engine) as session:
        run = RunService(session).get_run("game-run-2")
        assert "not a lab image" in (run.failure_reason or "")


# --- The sandbox ------------------------------------------------------------------


def test_play_is_a_sandbox_kind() -> None:
    assert "play" in VALID_KINDS


class _SandboxContainer:
    def __init__(self) -> None:
        self.ports = {
            "6080/tcp": [{"HostPort": "49999"}],
            "7681/tcp": [{"HostPort": "49998"}],
        }

    def reload(self) -> None:
        pass


def _sandbox_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: tuple[str, str]
):
    workspace = tmp_path / "data" / "runs" / "r1" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "project.godot").write_text("")
    settings = SimpleNamespace(
        host_repo_root=str(tmp_path),
        repo_root=tmp_path,
        data_runs_dir=tmp_path / "data" / "runs",
        sandbox_image="harness-lab-sandbox:latest",
        sandbox_dir=lambda run_id, kind: tmp_path
        / "data"
        / "runs"
        / run_id
        / f"sandbox-{kind}",
    )
    monkeypatch.setattr(tasks, "get_settings", lambda: settings)
    monkeypatch.setattr(tasks, "_sandbox_profile", lambda _run_id: profile)
    calls: dict[str, Any] = {}

    def run(**kwargs: Any) -> _SandboxContainer:
        calls.update(kwargs)
        return _SandboxContainer()

    client = SimpleNamespace(
        containers=SimpleNamespace(
            run=run, get=lambda name: (_ for _ in ()).throw(RuntimeError(name))
        )
    )
    return client, calls


def test_play_opens_the_case_sandbox_and_starts_its_game(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, calls = _sandbox_env(
        tmp_path, monkeypatch, (GODOT_SANDBOX, "godot --path /work")
    )
    _name, port, url = tasks._launch_sandbox(client, "r1", "play")
    assert calls["image"] == GODOT_SANDBOX
    assert calls["environment"] == {
        "MODE": "desktop",
        "PLAY_COMMAND": "godot --path /work",
    }
    assert port == 49999
    assert url.endswith("/vnc.html?autoconnect=1&resize=remote")


def test_desktop_and_terminal_open_in_the_case_image_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, calls = _sandbox_env(
        tmp_path, monkeypatch, (GODOT_SANDBOX, "godot --path /work")
    )
    tasks._launch_sandbox(client, "r1", "desktop")
    assert calls["image"] == GODOT_SANDBOX
    # Only Play launches the game; Desktop is the plain desktop.
    assert calls["environment"] == {"MODE": "desktop"}


def test_play_without_a_play_command_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _calls = _sandbox_env(tmp_path, monkeypatch, ("", ""))
    with pytest.raises(RuntimeError, match="no play_command"):
        tasks._launch_sandbox(client, "r1", "play")


def test_a_case_without_a_sandbox_image_keeps_the_shared_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, calls = _sandbox_env(tmp_path, monkeypatch, ("", ""))
    tasks._launch_sandbox(client, "r1", "terminal")
    assert calls["image"] == "harness-lab-sandbox:latest"
    assert calls["environment"] == {"MODE": "terminal"}


def test_the_sandbox_entrypoint_starts_the_play_command_without_reparsing_it() -> None:
    """The command is run once by bash -c; the exit message is shown with cat, so a
    quote in the command can never break, or inject into, a second shell."""
    script = (REPO_ROOT / "scripts" / "sandbox_entrypoint.sh").read_text()
    assert 'bash -c "$PLAY_COMMAND"' in script
    assert "cat /tmp/play-exit.txt" in script
    assert (
        subprocess.run(
            ["bash", "-n", str(REPO_ROOT / "scripts" / "sandbox_entrypoint.sh")]
        ).returncode
        == 0
    )


# --- References stay out of the repository ------------------------------------------


def test_calibration_references_are_never_tracked() -> None:
    """An agent reads the lab's history; a committed reference is a leaked answer."""
    ignored = subprocess.run(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "check-ignore",
            "-q",
            "data/reference/chess3d-v1/full/rules.gd",
        ],
        check=False,
    )
    assert ignored.returncode == 0
    tracked = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "data/reference"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert tracked == ["data/reference/swebench_official.json"]


def test_the_chess_grader_has_one_command_per_check() -> None:
    raw = yaml.safe_load((REPO_ROOT / "benchmarks/chess3d-v1/task.yaml").read_text())
    commands = raw["validation_commands"]
    names = [c.split(" check ", 1)[1].split()[0] for c in commands if " check " in c]
    assert len(names) == len(set(names)) == 39
    assert " harvest " in commands[1]
    assert raw["validation_requirements"] == ["chess==1.11.2"]
    assert json.dumps(commands).count("{run_id}") == len(commands) - 1
