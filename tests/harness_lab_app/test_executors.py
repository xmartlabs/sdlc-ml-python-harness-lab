from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from harness_lab_app import executors
from harness_lab_app.executors import (
    GOVERNANCE_MASK_ALL,
    GOVERNANCE_MASK_ANCESTORS,
    GOVERNANCE_MASK_NONE,
    RUNNER_GOVERNANCE_FILES,
    RUNNER_MASKED_SKILL_DIRS,
    RUNNER_SKILLS_DIR,
    DockerRunExecutor,
    SubprocessRunExecutor,
    build_run_environment,
    container_name_for_run,
    rewrite_localhost,
    runner_governance_masks,
    runner_masks,
    runner_skill_masks,
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://localhost:4000/v1", "http://host.docker.internal:4000/v1"),
        ("http://127.0.0.1:1234/v1", "http://host.docker.internal:1234/v1"),
        (
            "http://localhost:4000/localhost",
            "http://host.docker.internal:4000/host.docker.internal",
        ),
        ("http://example.com:4000/v1", "http://example.com:4000/v1"),
        ("", ""),
    ],
)
def test_rewrite_localhost(url: str, expected: str) -> None:
    assert rewrite_localhost(url, "host.docker.internal") == expected


def test_container_name_for_run_sanitizes_id() -> None:
    assert container_name_for_run("tetris-v1-K-001") == "harness-run-tetris-v1-K-001"
    assert container_name_for_run("weird id/x") == "harness-run-weird-id-x"


class FakeSettings:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.docker_host_alias = "host.docker.internal"
        self.host_repo_root = "/host/repo"
        self.runner_image = "harness-lab-runner:latest"
        self.execution_backend = "docker"
        self.data_runs_dir = repo_root / "data" / "runs"


def test_build_run_environment_rewrites_host_urls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(
        "# comment\n"
        "GATEWAY_BASE_URL=http://localhost:4000/v1\n"
        "GATEWAY_TOKEN=sk-test\n"
        "LMSTUDIO_BASE_URL=http://127.0.0.1:1234/v1\n"
        "UNRELATED=keep-me\n"
    )
    monkeypatch.setattr(executors, "get_settings", lambda: FakeSettings(tmp_path))

    env = build_run_environment()

    assert env["GATEWAY_BASE_URL"] == "http://host.docker.internal:4000/v1"
    assert env["LMSTUDIO_BASE_URL"] == "http://host.docker.internal:1234/v1"
    assert env["GATEWAY_TOKEN"] == "sk-test"
    assert env["UNRELATED"] == "keep-me"
    assert env["PYTHONUNBUFFERED"] == "1"
    # Claude Code refuses --dangerously-skip-permissions as root without this.
    assert env["IS_SANDBOX"] == "1"


def test_build_run_environment_without_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(executors, "get_settings", lambda: FakeSettings(tmp_path))

    env = build_run_environment()

    # The SDL pair is part of the baseline, not something a case opts into: the
    # run container has no display, and leaving that for the agent to remember
    # is what let pygame windows hang whole runs (K-036, K-038).
    assert env == {
        "PYTHONUNBUFFERED": "1",
        "IS_SANDBOX": "1",
        "SDL_VIDEODRIVER": "dummy",
        "SDL_AUDIODRIVER": "dummy",
        # Tells bench_run it is in the runner lane, where codex must rely on
        # the container instead of its own bubblewrap sandbox (ADR-0094).
        "HARNESS_LAB_IN_RUNNER": "1",
    }


def test_subprocess_executor_streams_and_waits() -> None:
    handle = SubprocessRunExecutor().start(
        "stream-1",
        ["python3", "-c", "print('one'); print('two')"],
    )
    messages = [message for _, message in handle.iter_lines()]
    assert handle.wait() == 0
    assert messages.index("one") < messages.index("two")


def test_subprocess_executor_stop_terminates_process() -> None:
    handle = SubprocessRunExecutor().start(
        "stop-1",
        ["python3", "-c", "import time; time.sleep(30); print('late')"],
    )
    handle.stop()
    messages = [message for _, message in handle.iter_lines()]
    assert handle.wait() != 0
    assert "late" not in messages


class FakeContainer:
    def __init__(self, log_chunks: list[bytes], status_code: int = 0):
        self._log_chunks = log_chunks
        self._status_code = status_code
        self.stopped = False
        self.removed = False
        self.status = "running"

    def logs(self, **_kwargs: Any):
        yield from self._log_chunks

    def stop(self, timeout: int = 10) -> None:
        self.stopped = True

    def wait(self) -> dict[str, int]:
        return {"StatusCode": self._status_code}

    def remove(self, force: bool = False) -> None:
        self.removed = True


class FakeContainers:
    def __init__(self, container: FakeContainer):
        self._container = container
        self.run_kwargs: dict[str, Any] = {}

    def run(self, **kwargs: Any) -> FakeContainer:
        self.run_kwargs = kwargs
        return self._container

    def get(self, name: str) -> FakeContainer:
        raise RuntimeError(f"No such container: {name}")


class FakeImages:
    def __init__(self, known: bool = True):
        self.known = known

    def get(self, name: str) -> Any:
        if not self.known:
            raise RuntimeError(f"No such image: {name}")
        return object()


class FakeDockerClient:
    def __init__(self, container: FakeContainer, image_known: bool = True):
        self.containers = FakeContainers(container)
        self.images = FakeImages(image_known)


def test_docker_executor_configures_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("GATEWAY_BASE_URL=http://localhost:4000/v1\n")
    monkeypatch.setattr(executors, "get_settings", lambda: FakeSettings(tmp_path))
    container = FakeContainer([b"step one\nstep two\n"])
    client = FakeDockerClient(container)
    executor = DockerRunExecutor(client_factory=lambda: client)

    handle = executor.start("tetris-v1-K-001", ["python", "scripts/bench_run.py"])
    messages = [message for _, message in handle.iter_lines()]
    exit_code = handle.wait()

    kwargs = client.containers.run_kwargs
    assert kwargs["image"] == "harness-lab-runner:latest"
    assert kwargs["name"] == "harness-run-tetris-v1-K-001"
    assert kwargs["volumes"] == {"/host/repo": {"bind": "/app", "mode": "rw"}}
    assert kwargs["extra_hosts"] == {"host.docker.internal": "host-gateway"}
    assert (
        kwargs["environment"]["GATEWAY_BASE_URL"]
        == "http://host.docker.internal:4000/v1"
    )
    assert kwargs["labels"] == {"harness-lab.run-id": "tetris-v1-K-001"}
    # One skill catalog reaches the agent; the per-tool mirrors are masked.
    assert kwargs["tmpfs"] == {
        "/app/.agents/skills": "",
        "/app/.opencode/skills": "",
        "/app/.codex/skills": "",
        "/app/.gemini/skills": "",
    }
    assert f"/app/{RUNNER_SKILLS_DIR}" not in kwargs["tmpfs"]
    # An unmasked run reaches Docker exactly as it did before ADR-0049.
    assert "mounts" not in kwargs
    assert messages == ["step one", "step two"]
    assert exit_code == 0
    assert container.removed is True


def test_docker_executor_raises_clear_error_when_image_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(executors, "get_settings", lambda: FakeSettings(tmp_path))
    client = FakeDockerClient(FakeContainer([]), image_known=False)
    executor = DockerRunExecutor(client_factory=lambda: client)

    with pytest.raises(RuntimeError, match="was not found locally"):
        executor.start("tetris-v1-K-001", ["python", "scripts/bench_run.py"])


def test_docker_executor_stop_stops_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(executors, "get_settings", lambda: FakeSettings(tmp_path))
    container = FakeContainer([], status_code=137)
    client = FakeDockerClient(container)
    executor = DockerRunExecutor(client_factory=lambda: client)

    handle = executor.start("stop-run", ["python", "scripts/bench_run.py"])
    handle.stop()

    assert container.stopped is True
    assert handle.wait() == 137
    assert container.removed is True


def test_runner_skill_masks_never_hides_the_catalog_in_use() -> None:
    masks = runner_skill_masks()

    assert f"/app/{RUNNER_SKILLS_DIR}" not in masks
    assert all(path.startswith("/app/") for path in masks)
    assert set(masks.values()) == {""}


def test_runner_skill_masks_cover_every_tool_in_the_registry() -> None:
    """The mask list must not drift from ``adapters/registry.toml``.

    Adding an AI tool there materializes a fifth skill catalog into the repo,
    which the runner would then load on top of the one it is measuring. The
    registry is the source of truth; this test is the tripwire that says so
    when a tool is added and :data:`RUNNER_MASKED_SKILL_DIRS` is not updated.
    """
    import tomllib

    registry = tomllib.loads(
        (Path(__file__).resolve().parents[2] / "adapters" / "registry.toml").read_text()
    )
    materialized = {
        entry["native_skills_dir"]
        for entry in registry["tool"]
        if entry.get("native_skills_dir")
        and entry.get("link_strategy", "none") != "none"
    }

    assert materialized - {RUNNER_SKILLS_DIR} == set(RUNNER_MASKED_SKILL_DIRS)


# --------------------------------------------------------------------------
# Governance masks (ADR-0049)
# --------------------------------------------------------------------------


def test_an_unmasked_run_hides_nothing_but_the_skill_mirrors() -> None:
    tmpfs, files = runner_governance_masks(GOVERNANCE_MASK_NONE)

    assert tmpfs == {}
    assert files == []
    assert runner_masks(GOVERNANCE_MASK_NONE)[0] == runner_skill_masks()


def test_an_in_target_mask_keeps_the_governed_sources_readable() -> None:
    """`native` and `injected` hide the lab from the agent, not from the runner.

    bench_run.py discovers governed skills from `.github/skills` and projects
    the injected harness from `.github/`. Masking it would make an injected arm
    project nothing and turn `--disable-skill X` into "unknown skill" — the arm
    would silently equal its own control.
    """
    tmpfs, files = runner_governance_masks(GOVERNANCE_MASK_ANCESTORS)
    targets = {target for target, _kind in files}

    assert "/app/.github" not in tmpfs
    assert "/app/.claude" in tmpfs
    assert "/app/memory" in tmpfs
    assert "/app/CLAUDE.md" in targets
    # Proven to leak from an ancestor directory in two archived native runs.
    assert "/app/.mcp.json" in targets
    # Routing, not governance: blanking it would also blank the gateway
    # provider block and drift `gateway_overlay_hash` on every masked run.
    assert "/app/opencode.json" not in targets


def test_a_bare_mask_hides_the_governed_sources_too() -> None:
    tmpfs, _files = runner_governance_masks(GOVERNANCE_MASK_ALL)

    assert "/app/.github" in tmpfs
    assert set(tmpfs.values()) == {""}
    assert all(path.startswith("/app/") for path in tmpfs)


def test_a_bare_mask_covers_every_document_the_surface_counts() -> None:
    """The masks must not drift from what `governance.py` calls governance.

    That module defines the reading surface an audit scores a run against. If a
    document is in the surface but not in the masks, a bare run can read it —
    and the arm that exists to see nothing sees something.
    """
    from harness_lab_app import governance

    tmpfs, files = runner_governance_masks(GOVERNANCE_MASK_ALL)
    targets = {target for target, _kind in files}

    for name in governance._SINGLE_FILES:
        covered = f"/app/{name}" in targets or any(
            f"/app/{name}".startswith(f"{masked}/") for masked in tmpfs
        )
        assert covered, f"{name} is in the governance surface but is not masked"
    for directory, _pattern in governance._GLOBS:
        target = f"/app/{directory}"
        covered = target in tmpfs or any(
            target.startswith(f"{masked}/") for masked in tmpfs
        )
        assert covered, f"{directory} is in the governance surface but is not masked"


def test_a_bare_mask_covers_every_adapter_file_in_the_registry() -> None:
    """Adding an AI tool adds an adapter file the runner must also hide."""
    import tomllib

    registry = tomllib.loads(
        (Path(__file__).resolve().parents[2] / "adapters" / "registry.toml").read_text()
    )
    tmpfs, files = runner_governance_masks(GOVERNANCE_MASK_ALL)
    targets = {target for target, _kind in files}

    for entry in registry["tool"]:
        adapter = entry.get("adapter_file")
        if not adapter:
            continue
        covered = f"/app/{adapter}" in targets or any(
            f"/app/{adapter}".startswith(f"{masked}/") for masked in tmpfs
        )
        assert covered, f"adapter file {adapter} is not masked for a bare run"


def test_masks_never_collide_on_one_destination() -> None:
    """Docker refuses two mounts with the same destination."""
    for level in (GOVERNANCE_MASK_ANCESTORS, GOVERNANCE_MASK_ALL):
        tmpfs, files = runner_masks(level)
        targets = [target for target, _kind in files]

        assert len(targets) == len(set(targets))
        assert not set(tmpfs) & set(targets)
        for path in tmpfs:
            assert not any(
                path.startswith(f"{other}/") for other in tmpfs if other != path
            )


def test_docker_executor_binds_an_empty_file_over_every_governance_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Only files that exist are masked: a bind whose target is missing makes
    # Docker create it, and the target sits inside the /app bind — an empty
    # `.cursorrules` would land in the operator's own checkout.
    present = ["CLAUDE.md", "AGENTS.md", ".mcp.json"]
    for name in present:
        (tmp_path / name).write_text("real content\n")
    monkeypatch.setattr(executors, "get_settings", lambda: FakeSettings(tmp_path))
    client = FakeDockerClient(FakeContainer([]))
    executor = DockerRunExecutor(client_factory=lambda: client)

    executor.start(
        "tetris-v1-K-100",
        ["python", "scripts/bench_run.py"],
        governance_mask=GOVERNANCE_MASK_ALL,
    )

    kwargs = client.containers.run_kwargs
    mounts = kwargs["mounts"]
    assert {mount["Target"] for mount in mounts} == {f"/app/{name}" for name in present}
    assert set(present) <= set(RUNNER_GOVERNANCE_FILES)
    assert all(mount["ReadOnly"] for mount in mounts)
    assert all(
        mount["Source"].startswith("/host/repo/data/runs/tetris-v1-K-100/masks/")
        for mount in mounts
    )
    assert "/app/.github" in kwargs["tmpfs"]
    # An empty `.mcp.json` is invalid JSON, not "no config" — a tool may refuse
    # to start on it, so the JSON masks carry an empty object.
    masks = tmp_path / "data" / "runs" / "tetris-v1-K-100" / "masks"
    assert (masks / "empty.json").read_text().strip() == "{}"
    assert (masks / "empty.md").read_text() == ""


def test_the_host_lane_refuses_to_pretend_it_masked_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(executors, "get_settings", lambda: FakeSettings(tmp_path))

    with pytest.raises(RuntimeError, match="docker backend"):
        SubprocessRunExecutor().start(
            "r-1", ["true"], governance_mask=GOVERNANCE_MASK_ALL
        )


def test_run_environment_carries_the_claude_settings_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Routing must not depend on where the agent's cwd happens to be.

    Claude Code reads `.claude/settings*.json` from its project directory, so a
    run launched from the workspace lost ANTHROPIC_BASE_URL — the LiteLLM
    gateway and its metrics headers — along with the governance it was meant to
    lose (ADR-0049, and its erratum on what `apiKeySource` does not prove).
    """
    (tmp_path / ".env").write_text("ANTHROPIC_MODEL=from-env-file\n")
    claude = tmp_path / ".claude"
    claude.mkdir()
    (claude / "settings.json").write_text('{"env": {"ANTHROPIC_MODEL": "shared"}}')
    (claude / "settings.local.json").write_text(
        '{"env": {"ANTHROPIC_BASE_URL": "http://localhost:4000",'
        ' "ANTHROPIC_MODEL": "local-wins"}}'
    )
    monkeypatch.setattr(executors, "get_settings", lambda: FakeSettings(tmp_path))

    env = build_run_environment()

    assert env["ANTHROPIC_BASE_URL"] == "http://host.docker.internal:4000"
    # settings.local.json wins over settings.json, and both over .env — the
    # same order Claude Code itself applies when it reads them from the cwd.
    assert env["ANTHROPIC_MODEL"] == "local-wins"


def test_a_broken_settings_file_never_fails_a_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claude = tmp_path / ".claude"
    claude.mkdir()
    (claude / "settings.json").write_text("{ not json")
    monkeypatch.setattr(executors, "get_settings", lambda: FakeSettings(tmp_path))

    assert "ANTHROPIC_BASE_URL" not in build_run_environment()


def test_each_agent_container_writes_its_own_bench_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two runs at once must not share the file that names "the active run".

    `bench_run.py` writes `BENCH_CONTEXT.md` ("all files go in workspace/<id>/")
    at the repo root, and opencode reads it on every session. With two runs in
    parallel the second would overwrite the first's, and the first agent could
    start writing into the other's workspace. Each agent container binds a copy
    under its own run directory over that path, writable, so it writes there.
    """
    (tmp_path / "BENCH_CONTEXT.md").write_text("placeholder\n")
    monkeypatch.setattr(executors, "get_settings", lambda: FakeSettings(tmp_path))
    seen: dict[str, Any] = {}
    for run_id in ("chess3d-v1-K-020", "chess3d-v1-K-021"):
        client = FakeDockerClient(FakeContainer([]))
        DockerRunExecutor(client_factory=lambda c=client: c).start(
            run_id, ["python", "scripts/bench_run.py"], hidden=[]
        )
        mounts = client.containers.run_kwargs["mounts"]
        context = [m for m in mounts if m["Target"] == "/app/BENCH_CONTEXT.md"]
        assert len(context) == 1
        assert context[0]["ReadOnly"] is False
        seen[run_id] = context[0]["Source"]
        # The copy exists before the container starts (Docker would otherwise
        # create a directory at the source and fail the mount).
        assert (tmp_path / "data" / "runs" / run_id / "bench_context.md").is_file()
    assert seen["chess3d-v1-K-020"] == (
        "/host/repo/data/runs/chess3d-v1-K-020/bench_context.md"
    )
    assert seen["chess3d-v1-K-020"] != seen["chess3d-v1-K-021"]
    # The repo's own file is never touched.
    assert (tmp_path / "BENCH_CONTEXT.md").read_text() == "placeholder\n"


def test_a_clean_checkout_still_gives_each_run_its_own_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`BENCH_CONTEXT.md` is gitignored, so a fresh clone has none.

    Skipping the bind then would let `bench_run.py` create the shared file
    through the `/app` bind, and two runs at once would share it again. The
    placeholder is written first, so the bind target exists and stays unused.
    """
    monkeypatch.setattr(executors, "get_settings", lambda: FakeSettings(tmp_path))
    client = FakeDockerClient(FakeContainer([]))
    DockerRunExecutor(client_factory=lambda: client).start(
        "chess3d-v1-K-030", ["python", "scripts/bench_run.py"], hidden=[]
    )
    mounts = client.containers.run_kwargs["mounts"]
    context = [m for m in mounts if m["Target"] == "/app/BENCH_CONTEXT.md"]
    assert len(context) == 1 and context[0]["ReadOnly"] is False
    assert (tmp_path / "BENCH_CONTEXT.md").read_text() == (
        executors.BENCH_CONTEXT_PLACEHOLDER
    )
    own = tmp_path / "data" / "runs" / "chess3d-v1-K-030" / "bench_context.md"
    assert own.read_text() == executors.BENCH_CONTEXT_PLACEHOLDER


def test_a_masked_bench_context_stays_masked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under a governance mask the file is already an empty read-only bind."""
    (tmp_path / "BENCH_CONTEXT.md").write_text("placeholder\n")
    monkeypatch.setattr(executors, "get_settings", lambda: FakeSettings(tmp_path))
    client = FakeDockerClient(FakeContainer([]))
    DockerRunExecutor(client_factory=lambda: client).start(
        "tetris-v1-K-101",
        ["python", "scripts/bench_run.py"],
        governance_mask=GOVERNANCE_MASK_ALL,
        hidden=[],
    )
    mounts = client.containers.run_kwargs["mounts"]
    context = [m for m in mounts if m["Target"] == "/app/BENCH_CONTEXT.md"]
    assert len(context) == 1 and context[0]["ReadOnly"] is True


def test_a_masked_run_on_a_clean_checkout_still_gets_its_own_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mask lists `BENCH_CONTEXT.md`, but a fresh clone has no such file.

    `_mask_mounts` drops a bind whose target is absent, so the mask never
    materialised, and the private bind was skipped because the mask *listed*
    the file. The run then had neither, and `bench_run.py` wrote the shared file
    through the `/app` bind. What counts is the mounts actually made.
    """
    monkeypatch.setattr(executors, "get_settings", lambda: FakeSettings(tmp_path))
    client = FakeDockerClient(FakeContainer([]))
    DockerRunExecutor(client_factory=lambda: client).start(
        "tetris-v1-K-103",
        ["python", "scripts/bench_run.py"],
        governance_mask=GOVERNANCE_MASK_ALL,
        hidden=[],
    )
    mounts = client.containers.run_kwargs["mounts"]
    context = [m for m in mounts if m["Target"] == "/app/BENCH_CONTEXT.md"]
    assert len(context) == 1
    own = tmp_path / "data" / "runs" / "tetris-v1-K-103" / "bench_context.md"
    assert (
        context[0]["Source"] == "/host/repo/data/runs/tetris-v1-K-103/bench_context.md"
    )
    assert own.is_file()


def test_a_container_that_is_not_the_agents_gets_no_private_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validation containers (`hidden` None) never read the context: no bind."""
    (tmp_path / "BENCH_CONTEXT.md").write_text("placeholder\n")
    monkeypatch.setattr(executors, "get_settings", lambda: FakeSettings(tmp_path))
    client = FakeDockerClient(FakeContainer([]))
    DockerRunExecutor(client_factory=lambda: client).start(
        "tetris-v1-K-102-validate", ["true"], phase="validate"
    )
    assert "mounts" not in client.containers.run_kwargs


def test_the_agent_cannot_rewrite_its_own_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case's folder is a read-only bind in the agent's container (ADR-0087).

    `pr-review-v1-K-002` could not find its (masked) grader, wrote one, and
    appended checks to its own task.yaml: the one write that reached the host.
    """
    (tmp_path / "benchmarks" / "pr-review-v1").mkdir(parents=True)
    monkeypatch.setattr(executors, "get_settings", lambda: FakeSettings(tmp_path))
    client = FakeDockerClient(FakeContainer([]))
    DockerRunExecutor(client_factory=lambda: client).start(
        "pr-review-v1-K-003",
        ["python", "scripts/bench_run.py"],
        hidden=["benchmarks/pr-review-v1/grader/"],
        case_dir="benchmarks/pr-review-v1",
    )
    kwargs = client.containers.run_kwargs
    case = [
        m for m in kwargs["mounts"] if m["Target"] == "/app/benchmarks/pr-review-v1"
    ]
    assert len(case) == 1 and case[0]["ReadOnly"] is True
    assert case[0]["Source"] == "/host/repo/benchmarks/pr-review-v1"
    # The grader inside it is still masked.
    assert "/app/benchmarks/pr-review-v1/grader" in kwargs["tmpfs"]


@pytest.mark.parametrize(
    "case_dir",
    [
        "",
        "benchmarks",
        "benchmarks/../src",
        "src/harness_lab_app",
        "benchmarks/missing",
    ],
)
def test_a_case_dir_that_is_not_a_benchmark_folder_mounts_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case_dir: str
) -> None:
    (tmp_path / "src" / "harness_lab_app").mkdir(parents=True)
    monkeypatch.setattr(executors, "get_settings", lambda: FakeSettings(tmp_path))
    client = FakeDockerClient(FakeContainer([]))
    DockerRunExecutor(client_factory=lambda: client).start(
        "x-K-001", ["python", "scripts/bench_run.py"], hidden=[], case_dir=case_dir
    )
    targets = [m["Target"] for m in client.containers.run_kwargs.get("mounts", [])]
    assert not [t for t in targets if t.startswith("/app/benchmarks") or "/src" in t]


def test_a_validation_container_never_gets_the_case_bind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`hidden` is None outside the agent's container: the grader lane reads as before."""
    (tmp_path / "benchmarks" / "pr-review-v1").mkdir(parents=True)
    monkeypatch.setattr(executors, "get_settings", lambda: FakeSettings(tmp_path))
    client = FakeDockerClient(FakeContainer([]))
    DockerRunExecutor(client_factory=lambda: client).start(
        "pr-review-v1-K-003-validate",
        ["sh", "-c", "true"],
        case_dir="benchmarks/pr-review-v1",
    )
    targets = [m["Target"] for m in client.containers.run_kwargs.get("mounts", [])]
    assert "/app/benchmarks/pr-review-v1" not in targets
