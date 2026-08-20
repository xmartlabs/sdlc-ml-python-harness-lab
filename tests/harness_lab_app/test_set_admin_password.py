"""`make set-password` — the only way auth is turned on (ADR-0015)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from harness_lab_app.auth import verify_password
from harness_lab_app.env_file import values

# scripts/ is not a package; make the module importable the same way it runs.
_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import set_admin_password  # noqa: E402

PASSWORD = "correct horse battery"


@pytest.fixture
def env_path(tmp_path, monkeypatch):
    """Point the script at a throwaway repo root so the real .env is safe."""
    monkeypatch.setattr(set_admin_password, "REPO_ROOT", tmp_path)
    path = tmp_path / ".env"
    path.write_text("")
    return path


def _run(monkeypatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["set_admin_password.py", *argv])
    return set_admin_password.main()


def test_writes_a_hash_and_a_secret_never_the_password(env_path, monkeypatch):
    env_path.write_text("# keep me\nGATEWAY_TOKEN=sk-existing\n")

    assert _run(monkeypatch, "--password", PASSWORD) == 0

    stored = values(env_path)
    assert verify_password(PASSWORD, stored[set_admin_password.HASH_KEY]) is True
    assert PASSWORD not in env_path.read_text()
    assert len(stored[set_admin_password.SECRET_KEY]) > 20
    # Unrelated content survives the rewrite.
    assert stored["GATEWAY_TOKEN"] == "sk-existing"
    assert "# keep me" in env_path.read_text()


def test_keeps_the_existing_session_secret(env_path, monkeypatch):
    """Changing the password must not sign the operator out everywhere."""
    env_path.write_text(f"{set_admin_password.SECRET_KEY}=keep-this-secret\n")

    _run(monkeypatch, "--password", PASSWORD)

    assert values(env_path)[set_admin_password.SECRET_KEY] == "keep-this-secret"


def test_rotate_secret_invalidates_every_session(env_path, monkeypatch):
    env_path.write_text(f"{set_admin_password.SECRET_KEY}=old-secret\n")

    _run(monkeypatch, "--password", PASSWORD, "--rotate-secret")

    assert values(env_path)[set_admin_password.SECRET_KEY] != "old-secret"


def test_off_returns_to_open_mode(env_path, monkeypatch):
    env_path.write_text(f"{set_admin_password.HASH_KEY}=pbkdf2_sha256$1$a$b\n")

    assert _run(monkeypatch, "--off") == 0

    assert values(env_path)[set_admin_password.HASH_KEY] == ""


def test_rejects_a_short_password_without_writing(env_path, monkeypatch):
    assert _run(monkeypatch, "--password", "short") == 1

    assert values(env_path) == {}


def test_refuses_when_there_is_no_env_file(tmp_path, monkeypatch):
    monkeypatch.setattr(set_admin_password, "REPO_ROOT", tmp_path)

    assert _run(monkeypatch, "--password", PASSWORD) == 1
