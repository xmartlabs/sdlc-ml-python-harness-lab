"""HTTP surface of multi-file staged skills (ADR-0017).

Endpoints are called directly, like every other test in this package — the
point here is the contract: what a preview returns, what an unconfirmed import
must *not* write, and which failures map to 404 vs 422.
"""

from __future__ import annotations

import asyncio
import io
import tarfile
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine

from harness_lab_app import (
    skill_import,
    staged_skill_service as module,
)
from harness_lab_app.api import create_app
from harness_lab_app.schemas import (
    StagedSkillCreate,
    StagedSkillFileWrite,
    StagedSkillImportRequest,
)
from harness_lab_app.skill_import import (
    ImportedSkill,
    SkillCandidate,
    SkillChoiceRequired,
    SkillImportError,
)

CONTENT = "---\nname: probe\ndescription: probes things\n---\nDo the probe.\n"


def _route_endpoint(app, path: str, method: str | None = None):
    for route in app.router.routes:
        if getattr(route, "path", None) != path:
            continue
        if method is not None and method not in getattr(route, "methods", set()):
            continue
        return route.endpoint
    raise AssertionError(f"Route not found: {path} ({method})")


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


@pytest.fixture
def service_env(tmp_path, monkeypatch):
    monkeypatch.setattr(
        module, "get_settings", lambda: SimpleNamespace(repo_root=tmp_path)
    )
    monkeypatch.setattr(module, "known_skill_names", lambda: {"brainstorming"})
    return tmp_path


def _imported(**kwargs) -> ImportedSkill:
    defaults = {
        "name": "probe",
        "description": "probes things",
        "files": {
            "SKILL.md": CONTENT.encode(),
            "scripts/run.sh": b"#!/bin/sh\necho hi\n",
        },
        "source_url": "https://github.com/o/r",
        "source_ref": "refs/heads/main",
        "source_kind": "url",
    }
    return ImportedSkill(**{**defaults, **kwargs})


class _Upload:
    """The two attributes the endpoint touches on an UploadFile."""

    def __init__(self, payload: bytes, filename: str) -> None:
        self._payload = payload
        self.filename = filename

    async def read(self, size: int = -1) -> bytes:
        return self._payload if size < 0 else self._payload[:size]


def _tar(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, blob in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(blob)
            archive.addfile(info, io.BytesIO(blob))
    return buffer.getvalue()


# -- import from URL ----------------------------------------------------------


def test_import_without_confirm_previews_and_writes_nothing(service_env, monkeypatch):
    app = create_app()
    endpoint = _route_endpoint(app, "/api/staged-skills/import")
    monkeypatch.setattr(
        "harness_lab_app.skill_import.fetch_skill", lambda *_args: _imported()
    )

    with _session() as session:
        result = endpoint(
            StagedSkillImportRequest(url="https://github.com/o/r"), session
        )

    assert result["preview"] is True
    assert [f["path"] for f in result["files"]] == ["SKILL.md", "scripts/run.sh"]
    assert result["source_kind"] == "url"
    # Nothing on disk, nothing in the DB.
    assert not (service_env / "data" / "skills_staging").exists()


def test_import_with_confirm_persists_the_tree(service_env, monkeypatch):
    app = create_app()
    endpoint = _route_endpoint(app, "/api/staged-skills/import")
    monkeypatch.setattr(
        "harness_lab_app.skill_import.fetch_skill", lambda *_args: _imported()
    )

    with _session() as session:
        skill = endpoint(
            StagedSkillImportRequest(url="https://github.com/o/r", confirm=True),
            session,
        )

    assert skill.name == "probe"
    root = service_env / "data" / "skills_staging" / "probe"
    assert (root / "scripts" / "run.sh").read_text() == "#!/bin/sh\necho hi\n"


def test_import_maps_a_fetch_failure_to_422(service_env, monkeypatch):
    app = create_app()
    endpoint = _route_endpoint(app, "/api/staged-skills/import")

    def _boom(*_args):
        raise SkillImportError("Refusing to fetch 'localhost': non-public address")

    monkeypatch.setattr("harness_lab_app.skill_import.fetch_skill", _boom)

    with _session() as session, pytest.raises(HTTPException) as exc:
        endpoint(StagedSkillImportRequest(url="https://localhost/x"), session)

    assert exc.value.status_code == 422
    assert "non-public" in exc.value.detail


def test_import_preview_honors_the_requested_name(service_env, monkeypatch):
    app = create_app()
    endpoint = _route_endpoint(app, "/api/staged-skills/import")
    monkeypatch.setattr(
        "harness_lab_app.skill_import.fetch_skill", lambda *_args: _imported()
    )

    with _session() as session:
        result = endpoint(
            StagedSkillImportRequest(url="https://github.com/o/r", name="my-probe"),
            session,
        )

    assert result["name"] == "my-probe"


def test_a_catalog_source_answers_with_choices_not_an_error(service_env, monkeypatch):
    """Pasting a repo of many skills is a normal thing to do, not a failure."""

    def _ambiguous(*_args):
        raise SkillChoiceRequired(
            [
                SkillCandidate(name="alpha", path="skills/alpha", description="First."),
                SkillCandidate(name="beta", path="skills/beta", description="Second."),
            ]
        )

    app = create_app()
    endpoint = _route_endpoint(app, "/api/staged-skills/import")
    monkeypatch.setattr("harness_lab_app.skill_import.fetch_skill", _ambiguous)

    with _session() as session:
        result = endpoint(
            StagedSkillImportRequest(url="https://github.com/anthropics/skills"),
            session,
        )

    assert result["preview"] is False
    assert [choice["name"] for choice in result["choices"]] == ["alpha", "beta"]
    assert result["choices"][0]["description"] == "First."
    assert not (service_env / "data" / "skills_staging").exists()


def test_the_picked_skill_reaches_the_fetcher(service_env, monkeypatch):
    seen: dict[str, str] = {}

    def _capture(url, ref, skill):
        seen.update(url=url, ref=ref, skill=skill)
        return _imported()

    app = create_app()
    endpoint = _route_endpoint(app, "/api/staged-skills/import")
    monkeypatch.setattr("harness_lab_app.skill_import.fetch_skill", _capture)

    with _session() as session:
        endpoint(
            StagedSkillImportRequest(
                url="https://github.com/anthropics/skills", skill="frontend-design"
            ),
            session,
        )

    assert seen["skill"] == "frontend-design"


# -- upload -------------------------------------------------------------------


def test_upload_previews_an_archive(service_env):
    app = create_app()
    endpoint = _route_endpoint(app, "/api/staged-skills/upload")
    payload = _tar({"probe/SKILL.md": CONTENT.encode(), "probe/notes.md": b"x\n"})

    with _session() as session:
        # Form defaults are FastAPI markers when the endpoint is called
        # directly, so every form field is passed explicitly here.
        result = asyncio.run(
            endpoint(
                file=_Upload(payload, "probe.tar.gz"),
                name="",
                skill="",
                confirm=False,
                replace=False,
                session=session,
            )
        )

    assert result["preview"] is True
    # Re-rooted at the directory holding SKILL.md, so the `probe/` prefix is gone.
    assert [f["path"] for f in result["files"]] == ["SKILL.md", "notes.md"]


def test_upload_rejects_an_oversized_payload(service_env, monkeypatch):
    app = create_app()
    endpoint = _route_endpoint(app, "/api/staged-skills/upload")
    monkeypatch.setattr(skill_import, "MAX_DOWNLOAD_BYTES", 16)

    with _session() as session, pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(file=_Upload(b"x" * 64, "probe.md"), session=session))

    assert exc.value.status_code == 413


def test_upload_rejects_an_unsupported_extension(service_env):
    app = create_app()
    endpoint = _route_endpoint(app, "/api/staged-skills/upload")

    with _session() as session, pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(file=_Upload(b"MZ", "payload.exe"), session=session))

    assert exc.value.status_code == 422


# -- per-file endpoints -------------------------------------------------------


def _create(session, service_env):
    create = _route_endpoint(create_app(), "/api/staged-skills", "POST")
    return create(
        StagedSkillCreate(name="probe", description="", content=CONTENT), session
    )


def test_file_endpoints_round_trip(service_env):
    app = create_app()
    write = _route_endpoint(app, "/api/staged-skills/{name}/files/{path:path}", "PUT")
    read = _route_endpoint(app, "/api/staged-skills/{name}/files/{path:path}", "GET")
    listing = _route_endpoint(app, "/api/staged-skills/{name}/files", "GET")
    remove = _route_endpoint(
        app, "/api/staged-skills/{name}/files/{path:path}", "DELETE"
    )

    with _session() as session:
        _create(session, service_env)
        write(
            "probe",
            "scripts/run.sh",
            StagedSkillFileWrite(content="echo hi\n"),
            session,
        )

        assert read("probe", "scripts/run.sh", session).content == "echo hi\n"
        assert [row.path for row in listing("probe", session)] == [
            "SKILL.md",
            "scripts/run.sh",
        ]

        remove("probe", "scripts/run.sh", session)
        assert [row.path for row in listing("probe", session)] == ["SKILL.md"]


def test_reading_a_missing_file_is_404(service_env):
    app = create_app()
    read = _route_endpoint(app, "/api/staged-skills/{name}/files/{path:path}", "GET")

    with _session() as session:
        _create(session, service_env)
        with pytest.raises(HTTPException) as exc:
            read("probe", "nope.md", session)

    assert exc.value.status_code == 404


def test_writing_a_traversal_path_is_422(service_env):
    app = create_app()
    write = _route_endpoint(app, "/api/staged-skills/{name}/files/{path:path}", "PUT")

    with _session() as session:
        _create(session, service_env)
        with pytest.raises(HTTPException) as exc:
            write("probe", "../escape.md", StagedSkillFileWrite(content="x"), session)

    assert exc.value.status_code == 422


def test_deleting_skill_md_is_422(service_env):
    app = create_app()
    remove = _route_endpoint(
        app, "/api/staged-skills/{name}/files/{path:path}", "DELETE"
    )

    with _session() as session:
        _create(session, service_env)
        with pytest.raises(HTTPException) as exc:
            remove("probe", "SKILL.md", session)

    assert exc.value.status_code == 422


def test_new_routes_are_behind_the_session_middleware():
    """Every new route must be protected; only the documented ones are public."""
    from harness_lab_app.session_middleware import PUBLIC_PATHS

    assert not any(path.startswith("/api/staged-skills") for path in PUBLIC_PATHS)
