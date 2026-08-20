from __future__ import annotations

from types import SimpleNamespace

from sqlmodel import Session, SQLModel, create_engine

from harness_lab_app import tasks
from harness_lab_app.sandbox_service import SandboxService


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_sandbox_service_lifecycle():
    with _session() as session:
        service = SandboxService(session)
        assert service.get_sandbox("r1", "terminal") is None

        starting = service.mark_starting("r1", "terminal")
        assert starting.status == "starting"

        running = service.mark_running(
            "r1", "terminal", "c1", 40001, "http://localhost:40001/"
        )
        assert running.status == "running"
        assert running.host_port == 40001
        assert running.url.endswith(":40001/")

        stopped = service.mark_stopped("r1", "terminal")
        assert stopped.status == "stopped"
        assert stopped.host_port is None

        # Same row reused across transitions (unique-ish per run+kind).
        assert service.get_sandbox("r1", "terminal").id == starting.id


def test_sandbox_service_error():
    with _session() as session:
        service = SandboxService(session)
        failed = service.mark_error("r2", "desktop", "boom")
        assert failed.status == "failed"
        assert failed.error == "boom"


def test_sandbox_container_name_sanitizes():
    name = tasks._sandbox_container_name("pallets__flask-4992-K-001", "terminal")
    assert name.startswith("harness-sandbox-terminal-")
    assert " " not in name


class _FakeContainer:
    def __init__(self, host_port: int):
        self.ports = {
            "7681/tcp": [{"HostPort": str(host_port)}],
            "6080/tcp": [{"HostPort": str(host_port)}],
        }

    def reload(self):
        pass


class _FakeClient:
    def __init__(self, host_port: int):
        self._host_port = host_port
        self.run_kwargs = None

    class _Containers:
        def __init__(self, outer):
            self._outer = outer

        def get(self, name):
            raise Exception("not found")

        def run(self, **kwargs):
            self._outer.run_kwargs = kwargs
            return _FakeContainer(self._outer._host_port)

    @property
    def containers(self):
        return _FakeClient._Containers(self)


def test_launch_sandbox_builds_terminal_url(tmp_path, monkeypatch):
    (tmp_path / "run-a" / "workspace").mkdir(parents=True)
    (tmp_path / "run-a" / "workspace" / "main.py").write_text("print('hi')\n")

    settings = SimpleNamespace(
        host_repo_root=str(tmp_path),
        repo_root=tmp_path,
        data_runs_dir=tmp_path,
        sandbox_image="img",
        sandbox_dir=lambda run_id, kind: tmp_path / run_id / f"sandbox-{kind}",
    )
    monkeypatch.setattr(tasks, "get_settings", lambda: settings)

    client = _FakeClient(40007)
    _name, port, url = tasks._launch_sandbox(client, "run-a", "terminal")
    assert port == 40007
    assert url == "http://localhost:40007/"
    assert client.run_kwargs["environment"] == {"MODE": "terminal"}
    assert client.run_kwargs["ports"] == {"7681/tcp": None}
    # Ephemeral copy created, artifact untouched.
    assert (tmp_path / "run-a" / "sandbox-terminal" / "main.py").exists()


def test_launch_sandbox_builds_desktop_url(tmp_path, monkeypatch):
    (tmp_path / "run-b" / "workspace").mkdir(parents=True)
    settings = SimpleNamespace(
        host_repo_root=str(tmp_path),
        repo_root=tmp_path,
        data_runs_dir=tmp_path,
        sandbox_image="img",
        sandbox_dir=lambda run_id, kind: tmp_path / run_id / f"sandbox-{kind}",
    )
    monkeypatch.setattr(tasks, "get_settings", lambda: settings)

    _, _, url = tasks._launch_sandbox(_FakeClient(6080), "run-b", "desktop")
    assert url.endswith("/vnc.html?autoconnect=1&resize=remote")
