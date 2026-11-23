"""The Antigravity CLI on the operator's own Google sign-in (ADR-0097).

The sign-in is agy's own interactive "Google OAuth" login, driven in a lab
container (``scripts/agy_login.py``); these tests cover what the lab decides
around it: which files form the session, that a run gets a private copy and
returns only a refreshed token, that the code reaches the worker without the
broker, and that a run on the sign-in has no API key and is billed as a plan.

The sign-in screen fixture is agy 1.5.14's real terminal output (no account),
with the PKCE ``state`` and ``code_challenge`` replaced.
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import agy_login  # noqa: E402
import bench_run  # noqa: E402

from harness_lab_app import (  # noqa: E402
    agy_account,
    api_key_auth,
    executors,
    native_connect,
)
from harness_lab_app.executors import DockerRunExecutor  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "agy"
KEY = "AIzaTESTtestTESTtestTESTtestTESTte1234"
TOKEN = b'{"opaque":"agy-wrote-this"}'


def _signed_in_home(home: Path) -> Path:
    """What a sign-in leaves in agy's home: the token, the scope, and work."""
    files = {
        agy_account.TOKEN_FILE: TOKEN,
        agy_account.SCOPE_FILE: b"user",
        ".gemini/antigravity-cli/installation_id": b"abc",
        ".gemini/antigravity-cli/jetski_state.pbtxt": b"installation_uuid: x",
        # Work, never part of a session:
        ".gemini/antigravity-cli/cli.log": b"log",
        ".gemini/antigravity-cli/conversations/c1.pb": b"conversation",
        ".gemini/antigravity-cli/builtin/skills/x/SKILL.md": b"skill",
        ".gemini/config/projects/default-cli-project.json": b"{}",
        ".gemini/GEMINI.md": b"a memory",
    }
    for relative, data in files.items():
        path = home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return home


# --- the session: which files, saved and staged -----------------------------------


def test_only_credential_files_form_the_session(tmp_path: Path) -> None:
    home = _signed_in_home(tmp_path / "home")

    files = agy_account.session_files(home)

    assert agy_account.TOKEN_FILE in files and agy_account.SCOPE_FILE in files
    for work in ("cli.log", "conversations/", "builtin/", "projects/", "GEMINI.md"):
        assert not any(work in f for f in files), work
    for bad in ("/etc/passwd", "../x", ".gemini/../../x"):
        assert agy_account.is_session_file(bad) is False


def test_a_sign_in_is_saved_private_and_without_its_work(tmp_path: Path) -> None:
    auth = tmp_path / "auth"
    home = _signed_in_home(tmp_path / "login-home")

    status = agy_account.save_account(home, email="me@example.com", root=auth)

    assert status["connected"] is True and status["email"] == "me@example.com"
    stored = agy_account.account_home(auth)
    assert (stored / agy_account.TOKEN_FILE).read_bytes() == TOKEN
    assert not (stored / ".gemini/antigravity-cli/conversations").exists()
    assert not (stored / ".gemini/GEMINI.md").exists()
    mode = stat.S_IMODE((stored / agy_account.TOKEN_FILE).stat().st_mode)
    assert mode == 0o600
    assert stat.S_IMODE(agy_account.account_dir(auth).stat().st_mode) == 0o700


def test_a_sign_in_without_a_token_is_refused(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".gemini").mkdir(parents=True)
    with pytest.raises(ValueError, match="did not leave a token"):
        agy_account.save_account(home, root=tmp_path / "auth")
    assert agy_account.account_status(tmp_path / "auth")["connected"] is False


def test_a_run_gets_a_private_copy_and_returns_only_a_refreshed_token(
    tmp_path: Path,
) -> None:
    auth = tmp_path / "auth"
    agy_account.save_account(_signed_in_home(tmp_path / "login"), root=auth)
    staged = agy_account.stage_run_home(tmp_path / "run-home", root=auth)

    # The run refreshes its token, writes a memory, a conversation, and tries
    # to add a file to the sign-in.
    (staged / agy_account.TOKEN_FILE).write_bytes(b'{"opaque":"refreshed"}')
    (staged / ".gemini/GEMINI.md").write_text("remember this")
    (staged / ".gemini/antigravity-cli/conversations").mkdir(parents=True)
    (staged / ".gemini/antigravity-cli/conversations/c.pb").write_text("x")
    (staged / ".config/antigravity/extra-planted").write_text("x")

    assert agy_account.return_run_home(staged, root=auth) is True

    stored = agy_account.account_home(auth)
    assert (stored / agy_account.TOKEN_FILE).read_bytes() == b'{"opaque":"refreshed"}'
    assert not (stored / ".gemini/GEMINI.md").exists()
    assert not (stored / ".config/antigravity/extra-planted").exists()
    assert not staged.exists()  # the run's copy is gone


def test_an_empty_or_linked_token_never_replaces_the_stored_one(tmp_path: Path) -> None:
    auth = tmp_path / "auth"
    agy_account.save_account(_signed_in_home(tmp_path / "login"), root=auth)
    for tamper in ("empty", "symlink"):
        staged = agy_account.stage_run_home(tmp_path / f"run-{tamper}", root=auth)
        token = staged / agy_account.TOKEN_FILE
        token.unlink()
        if tamper == "empty":
            token.write_bytes(b"")
        else:
            token.symlink_to("/etc/hostname")
        assert agy_account.return_run_home(staged, root=auth) is False
    assert (
        agy_account.account_home(auth) / agy_account.TOKEN_FILE
    ).read_bytes() == TOKEN


def test_a_disconnect_during_a_run_wins(tmp_path: Path) -> None:
    auth = tmp_path / "auth"
    agy_account.save_account(_signed_in_home(tmp_path / "login"), root=auth)
    staged = agy_account.stage_run_home(tmp_path / "run", root=auth)
    agy_account.remove_account(auth)

    assert agy_account.return_run_home(staged, root=auth) is False
    assert agy_account.account_status(auth)["connected"] is False


def test_the_sign_in_wins_over_a_key_and_either_alone_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = tmp_path / "auth"
    monkeypatch.setenv("HARNESS_LAB_AUTH_DIR", str(auth))
    assert agy_account.auth_mode() == ""
    api_key_auth.save_key(KEY)
    assert agy_account.auth_mode() == agy_account.AUTH_API_KEY
    agy_account.save_account(_signed_in_home(tmp_path / "login"))
    assert agy_account.auth_mode() == agy_account.AUTH_ACCOUNT
    agy_account.remove_account()
    assert agy_account.auth_mode() == agy_account.AUTH_API_KEY


# --- the sign-in screen and the code -----------------------------------------------


def test_the_full_url_is_read_from_agys_real_sign_in_screen() -> None:
    from urllib.parse import parse_qs, urlsplit

    raw = (FIXTURES / "signin_screen.pty.bin").read_bytes()

    url = agy_login.sign_in_url(raw)

    query = parse_qs(urlsplit(url).query)
    assert url.startswith("https://accounts.google.com/o/oauth2/auth?")
    # Exactly the parameters agy sent, none glued from the wrapped screen text.
    assert set(query) == {
        "access_type",
        "client_id",
        "code_challenge",
        "code_challenge_method",
        "prompt",
        "redirect_uri",
        "response_type",
        "scope",
        "state",
    }
    assert query["redirect_uri"] == ["https://antigravity.google/oauth-callback"]
    assert query["state"] == ["TESTstateTESTstate12"]
    assert agy_login.MENU_RE.search(agy_login.clean(raw))


def test_a_url_cut_by_a_read_boundary_is_not_shown_yet() -> None:
    raw = (FIXTURES / "signin_screen.pty.bin").read_bytes()
    cut = raw[: raw.find(b"&state=") + 3]
    assert agy_login.sign_in_url(cut) == ""


def test_agys_refusal_is_reported_in_its_own_words() -> None:
    screen = (
        'M Got an error: token exchange failed: oauth2: "invalid_grant" "Bad Request"'
    )
    assert agy_login.last_error(screen).startswith(
        "Got an error: token exchange failed"
    )
    assert agy_login.last_error("Signing in...") == ""


@pytest.mark.parametrize(
    ("pasted", "expected"),
    [
        ("  4/0AbCdEf-GhIj_KlMn  ", "4/0AbCdEf-GhIj_KlMn"),
        (
            "https://antigravity.google/oauth-callback?code=4%2F0AbCdEf-Gh&scope=x",
            "4/0AbCdEf-Gh",
        ),
    ],
)
def test_the_pasted_code_or_page_address_yields_the_code(
    pasted: str, expected: str
) -> None:
    assert agy_account.validate_code(pasted) == expected


@pytest.mark.parametrize("bad", ["", "short", "4/0Ab cd ef gh ij", "4/0Ab\n\x1b[2J"])
def test_a_malformed_code_is_refused_without_echoing_it(bad: str) -> None:
    with pytest.raises(ValueError) as refused:
        agy_account.validate_code(bad)
    assert bad.strip() == "" or bad not in str(refused.value)


def test_helper_records_are_told_apart_from_agys_screen() -> None:
    line = '@@agy-login {"state": "waiting", "url": "https://accounts.google.com/x"}'
    assert agy_account.parse_helper_line(line) == {
        "state": "waiting",
        "url": "https://accounts.google.com/x",
    }
    assert agy_account.parse_helper_line("Signing in...") is None
    assert agy_account.parse_helper_line("@@agy-login {broken") is None


def test_the_code_reaches_the_worker_once_as_a_private_file(tmp_path: Path) -> None:
    state = tmp_path / "state"
    native_connect.write_code_drop("antigravity_cli", "0a1b2c3d", "4/0Abc", state)
    drop = state / "antigravity_cli.code.0a1b2c3d"
    assert stat.S_IMODE(drop.stat().st_mode) == 0o600

    assert (
        native_connect.take_code_drop("antigravity_cli", "0a1b2c3d", state) == "4/0Abc"
    )
    assert not drop.exists()
    assert native_connect.take_code_drop("antigravity_cli", "0a1b2c3d", state) == ""
    # A forged attempt id never names a path.
    with pytest.raises(ValueError):
        native_connect.write_code_drop("antigravity_cli", "../../x", "c", state)
    assert native_connect.take_code_drop("antigravity_cli", "../../x", state) == ""


# --- the runner container -----------------------------------------------------------


class _Settings:
    def __init__(self, root: Path):
        self.repo_root = root
        self.docker_host_alias = "host.docker.internal"
        self.host_repo_root = "/host/repo"
        self.runner_image = "harness-lab-runner:latest"
        self.execution_backend = "docker"
        self.data_runs_dir = root / "data" / "runs"
        self.validation_network = "none"


class _Container:
    def logs(self, **_kwargs: Any):
        return iter([b"ok\n"])

    def wait(self) -> dict:
        return {"StatusCode": 0}

    def remove(self, **_kwargs: Any) -> None:
        return None

    def stop(self, **_kwargs: Any) -> None:
        return None


class _Client:
    def __init__(self) -> None:
        self.run_kwargs: dict[str, Any] = {}
        client = self

        class _Containers:
            def run(self, **kwargs: Any) -> _Container:
                client.run_kwargs = kwargs
                return _Container()

            def get(self, _name: str):
                raise LookupError

        class _Images:
            def get(self, _name: str) -> object:
                return object()

        self.containers = _Containers()
        self.images = _Images()


def _lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, key: bool = True) -> None:
    monkeypatch.setattr(executors, "get_settings", lambda: _Settings(tmp_path))
    monkeypatch.setenv("HARNESS_LAB_AUTH_DIR", str(tmp_path / "auth"))
    (tmp_path / ".env").write_text(
        "GATEWAY_TOKEN=sk-gw\nOPENAI_API_KEY=sk-openai\nGEMINI_API_KEY=AIza-dotenv\n"
    )
    if key:
        api_key_auth.save_key(KEY)
    agy_account.save_account(_signed_in_home(tmp_path / "login"))


def _mounts(client: _Client) -> dict[str, Any]:
    return {m["Target"]: m for m in client.run_kwargs.get("mounts") or []}


def test_a_run_on_the_sign_in_has_no_key_and_its_own_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lab(tmp_path, monkeypatch)
    client = _Client()

    handle = DockerRunExecutor(client_factory=lambda: client).start(
        "tetris-v1-K-950", ["python", "scripts/bench_run.py"], harness="antigravity_cli"
    )

    env = client.run_kwargs["environment"]
    # The plan pays, never the key: with a key agy would bill it instead.
    assert "GEMINI_API_KEY" not in env
    assert env["HARNESS_LAB_AGY_AUTH"] == "account"
    assert env["HOME"] == agy_account.CONTAINER_ACCOUNT_HOME
    for name in ("GATEWAY_TOKEN", "OPENAI_API_KEY"):
        assert name not in env
    mount = _mounts(client)[agy_account.CONTAINER_ACCOUNT_HOME]
    assert mount["Source"] == "/host/repo/data/runs/tetris-v1-K-950/agy-home"
    copy = tmp_path / "data/runs/tetris-v1-K-950/agy-home"
    assert (copy / agy_account.TOKEN_FILE).read_bytes() == TOKEN

    # The run refreshed its token: it returns to the store, the copy goes.
    (copy / agy_account.TOKEN_FILE).write_bytes(b'{"opaque":"new"}')
    assert handle.wait() == 0
    stored = agy_account.account_home() / agy_account.TOKEN_FILE
    assert stored.read_bytes() == b'{"opaque":"new"}'
    assert not copy.exists()


def test_the_sign_in_container_starts_empty_with_its_code_exchange(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lab(tmp_path, monkeypatch)
    client = _Client()
    run_id = f"{executors.AGY_LOGIN_PREFIX}-0a1b2c3d"

    DockerRunExecutor(client_factory=lambda: client).start(
        run_id, ["python3", "/app/scripts/agy_login.py"], harness="antigravity_cli"
    )

    env = client.run_kwargs["environment"]
    assert "GEMINI_API_KEY" not in env
    assert env["HOME"] == agy_account.CONTAINER_ACCOUNT_HOME
    assert env["AGY_LOGIN_EXCHANGE"] == agy_account.CONTAINER_EXCHANGE_DIR
    mounts = _mounts(client)
    assert agy_account.CONTAINER_EXCHANGE_DIR in mounts
    home = tmp_path / "data/runs" / run_id / "agy-home"
    # Empty: the stored sign-in is never handed to a new sign-in.
    assert home.is_dir() and not any(home.iterdir())


def test_another_runs_copy_of_the_sign_in_is_masked_in_every_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lab(tmp_path, monkeypatch)
    other = tmp_path / "data/runs/tetris-v1-K-951/agy-home"
    other.mkdir(parents=True)
    signing = tmp_path / f"data/runs/{executors.AGY_LOGIN_PREFIX}-ffff0000/agy-exchange"
    signing.mkdir(parents=True)

    for harness, run_id in (
        ("claude", "c-K-1"),
        ("codex", "x-K-1"),
        ("antigravity_cli", "tetris-v1-K-952"),
    ):
        client = _Client()
        DockerRunExecutor(client_factory=lambda c=client: c).start(
            run_id, ["true"], harness=harness
        )
        tmpfs = client.run_kwargs["tmpfs"]
        assert "/app/data/runs/tetris-v1-K-951/agy-home" in tmpfs, harness
        assert (
            f"/app/data/runs/{executors.AGY_LOGIN_PREFIX}-ffff0000/agy-exchange"
            in tmpfs
        ), harness


def test_on_a_key_alone_the_run_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lab(tmp_path, monkeypatch)
    agy_account.remove_account()
    client = _Client()

    DockerRunExecutor(client_factory=lambda: client).start(
        "tetris-v1-K-953", ["true"], harness="antigravity_cli"
    )

    env = client.run_kwargs["environment"]
    assert env["GEMINI_API_KEY"] == KEY
    assert "HARNESS_LAB_AGY_AUTH" not in env
    assert agy_account.CONTAINER_ACCOUNT_HOME not in _mounts(client)


# --- the run itself ------------------------------------------------------------------


def test_a_run_on_the_sign_in_forces_no_provider_and_is_billed_as_a_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HARNESS_LAB_AGY_AUTH", "account")
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-leaked-into-env")
    monkeypatch.setenv("HARNESS_LAB_AGENT_HOME", str(tmp_path / "home"))

    settings = json.loads(bench_run.agy_settings())
    assert "modelProvider" not in settings
    assert settings["permissions"]["allow"] == list(bench_run.AGY_ALLOW_RULES)
    assert "GEMINI_API_KEY" not in bench_run.agy_run_env()

    usage = bench_run.assemble_usage(
        tmp_path,
        tmp_path / "missing.log",
        run_id="r",
        session_id="s",
        harness="antigravity_cli",
        models=["gemini-4-neon-high"],
        harness_seconds=60,
        completed_steps=1,
        stop_reason=None,
        prompt_kinds=[],
    )
    assert usage["billing_mode"] == "subscription"
    assert usage["cost_source"] == "subscription_quota"
    assert usage["cost_usd"] is None


def test_a_run_on_a_key_keeps_the_gemini_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HARNESS_LAB_AGY_AUTH", raising=False)
    assert json.loads(bench_run.agy_settings())["modelProvider"] == "gemini"


def test_events_carry_the_runs_billing_mode() -> None:
    from harness_lab_app.harness_events import translate

    lines = (FIXTURES / "success_text.stream.jsonl").read_text().splitlines()

    def result(**kwargs: Any) -> dict:
        events = [json.loads(e) for e in translate("antigravity_cli", lines, **kwargs)]
        return next(e for e in events if e.get("type") == "result")

    assert result()["billing_mode"] == "api"  # unchanged default
    assert result(billing_mode="subscription")["billing_mode"] == "subscription"


def test_billing_mode_follows_the_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harness_lab_app.native_auth import billing_mode

    monkeypatch.setenv("HARNESS_LAB_AUTH_DIR", str(tmp_path / "auth"))
    assert billing_mode("antigravity_cli", {"HARNESS_LAB_AGY_AUTH": "account"}) == (
        "subscription"
    )
    assert billing_mode("antigravity_cli", {"HARNESS_LAB_IN_RUNNER": "1"}) == "api"
    assert billing_mode("antigravity_cli", {}) == "api"
    agy_account.save_account(_signed_in_home(tmp_path / "login"))
    assert billing_mode("antigravity_cli", {}) == "subscription"
    # Other harnesses are unaffected.
    assert billing_mode("codex", {}) == "subscription"
    assert billing_mode("opencode", {}) == "gateway"


# --- preflight, API, card, worker -----------------------------------------------------


def _preflight_service(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import harness_lab_app.health_service as hs
    import harness_lab_app.run_service as rs
    from harness_lab_app.run_service import RunService

    monkeypatch.setenv("HARNESS_LAB_AUTH_DIR", str(tmp_path / "auth"))
    monkeypatch.delenv(api_key_auth.ALLOW_UNTRUSTED_ENV, raising=False)
    monkeypatch.setattr(rs.get_settings(), "execution_backend", "docker", raising=False)
    monkeypatch.setattr(
        hs,
        "worker_facts",
        lambda **_k: {
            "runner_image_present": True,
            "cli_versions": {"antigravity_cli": "1.5.14"},
        },
    )

    class _Exec:
        def first(self) -> None:
            return None

    class _Session:
        def exec(self, _statement: Any) -> _Exec:
            return _Exec()

    service = RunService.__new__(RunService)
    service.session = _Session()  # type: ignore[assignment]
    monkeypatch.setattr(service, "_check_runner_image", lambda _config=None: None)
    monkeypatch.setattr(service, "_check_target", lambda _config=None: None)
    return service


def _config(model: str):
    from harness_lab_app.schemas import RunConfigCreate

    return RunConfigCreate(
        case_id="tetris-v1",
        prompt_variant="naive",
        harness="antigravity_cli",
        executor_model=model,
    )


def test_preflight_on_the_sign_in_accepts_a_plan_slug_the_api_map_lacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harness_lab_app.run_service import RunPreflightError

    service = _preflight_service(monkeypatch, tmp_path)
    with pytest.raises(RunPreflightError, match="has no credential"):
        service.preflight(_config("gemini-4-neon-high"))

    agy_account.save_account(_signed_in_home(tmp_path / "login"))
    # A plan-only slug (Claude via Antigravity, say) is the plan's to serve.
    service.preflight(_config("claude-sonnet-5-5-thinking"))
    with pytest.raises(RunPreflightError, match="Pick a model"):
        service.preflight(_config(""))
    with pytest.raises(RunPreflightError, match="not an agy model slug"):
        service.preflight(_config("gemini 4; rm -rf"))


def test_the_api_takes_a_code_only_for_a_waiting_sign_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import HTTPException

    from harness_lab_app.api import create_app
    from harness_lab_app.schemas import CliAuthCodeSubmit

    state = tmp_path / "state"
    monkeypatch.setenv("HARNESS_LAB_AUTH_DIR", str(tmp_path / "auth"))
    monkeypatch.setattr(native_connect, "state_dir", lambda _root=None: state)
    app = create_app()
    submit = next(
        r.endpoint
        for r in app.routes
        if getattr(r, "path", "") == "/api/harness/clis/{harness}/connect/code"
    )
    body = CliAuthCodeSubmit(code="4/0AbCdEfGhIjKl")
    assert "4/0AbCdEfGhIjKl" not in repr(body)

    with pytest.raises(HTTPException) as idle:
        submit("antigravity_cli", body)
    assert idle.value.status_code == 409

    record = native_connect.begin_login("antigravity_cli")
    attempt = str(record["attempt"])
    native_connect.write_login(
        "antigravity_cli",
        attempt,
        {
            "state": "waiting",
            "url": "https://accounts.google.com/o/oauth2/auth?state=x",
            "expires_at": 9e12,
        },
    )
    response = submit("antigravity_cli", body)

    assert response["code_submitted"] is True
    assert "4/0AbCdEfGhIjKl" not in json.dumps(response)
    assert (
        native_connect.take_code_drop("antigravity_cli", attempt) == "4/0AbCdEfGhIjKl"
    )
    with pytest.raises(HTTPException) as codex:
        submit("codex", body)
    assert codex.value.status_code == 404


def test_the_card_row_names_the_sign_in_and_never_its_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harness_lab_app import health_service

    monkeypatch.setenv("HARNESS_LAB_AUTH_DIR", str(tmp_path / "auth"))
    monkeypatch.setattr(
        native_connect, "state_dir", lambda _root=None: tmp_path / "state"
    )
    agy_account.save_account(
        _signed_in_home(tmp_path / "login"), email="me@example.com"
    )

    row = health_service._agy_row(
        "docker", {"cli_versions": {"antigravity_cli": "1.5.14"}}
    )

    assert row["agy_auth"] == "account" and row["session"] == "connected"
    assert row["billing_mode"] == "subscription"
    assert row["provider"] == "google_account"
    assert row["account"]["email"] == "me@example.com"
    assert row["default_test_model"] == ""
    assert TOKEN.decode() not in json.dumps(row)


def test_disconnect_forgets_the_sign_in_and_keeps_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harness_lab_app import tasks

    monkeypatch.setenv("HARNESS_LAB_AUTH_DIR", str(tmp_path / "auth"))
    monkeypatch.setattr(
        native_connect, "state_dir", lambda _root=None: tmp_path / "state"
    )
    api_key_auth.save_key(KEY)
    agy_account.save_account(_signed_in_home(tmp_path / "login"))

    tasks.disconnect_native_cli("antigravity_cli")

    assert agy_account.account_status()["connected"] is False
    assert api_key_auth.read_key() == KEY


def test_the_test_and_model_list_use_the_sign_in_without_a_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HARNESS_LAB_AUTH_DIR", str(tmp_path / "auth"))
    agy_account.save_account(_signed_in_home(tmp_path / "login"))

    command = native_connect.test_command("antigravity_cli")

    assert "--settings" not in command and "--model" not in command
    assert command[-4:] == [
        "-p",
        native_connect.TEST_PROMPT,
        "--output-format",
        "stream-json",
    ]
    picked = native_connect.test_command("antigravity_cli", "gemini-3.8-flash-low")
    assert picked[picked.index("--model") + 1] == "gemini-3.8-flash-low"


@pytest.mark.parametrize(
    ("labels", "refused"),
    [
        ({"antigravity_cli": ""}, True),  # built before agy existed
        ({"antigravity_cli": "1.5.14"}, False),
    ],
)
def test_a_case_image_built_before_the_cli_is_refused_at_preflight(
    labels: dict, refused: bool
) -> None:
    from harness_lab_app.run_service import RunPreflightError, RunService

    image = "harness-lab-runner-godot:latest"
    facts = {"case_image_cli_versions": {image: labels}}
    config = _config("gemini-4-hydrogen-xhigh")
    if refused:
        with pytest.raises(
            RunPreflightError, match="docker compose build runner-godot"
        ):
            RunService._check_case_image_cli(config, image, facts)
    else:
        RunService._check_case_image_cli(config, image, facts)
    # An older worker that reports no per-image labels is not second-guessed.
    RunService._check_case_image_cli(config, image, {})


def test_a_run_never_writes_an_older_sign_ins_token_over_a_new_one(
    tmp_path: Path,
) -> None:
    auth = tmp_path / "auth"
    agy_account.save_account(_signed_in_home(tmp_path / "a"), email="a@x.io", root=auth)
    staged = agy_account.stage_run_home(tmp_path / "run" / "agy-home", root=auth)
    (staged / agy_account.TOKEN_FILE).write_bytes(b'{"opaque":"account-a-refreshed"}')
    # Meanwhile the operator signs in again (another account, or a fix).
    second = _signed_in_home(tmp_path / "b")
    (second / agy_account.TOKEN_FILE).write_bytes(b'{"opaque":"account-b"}')
    agy_account.save_account(second, email="b@x.io", root=auth)

    assert agy_account.return_run_home(staged, root=auth) is False
    stored = agy_account.account_home(auth) / agy_account.TOKEN_FILE
    assert stored.read_bytes() == b'{"opaque":"account-b"}'
    assert not staged.exists()
    assert not (tmp_path / "run" / "agy-home.generation").exists()


def test_a_symlinked_directory_cannot_smuggle_a_file_into_the_store(
    tmp_path: Path,
) -> None:
    auth = tmp_path / "auth"
    agy_account.save_account(_signed_in_home(tmp_path / "login"), root=auth)
    staged = agy_account.stage_run_home(tmp_path / "run" / "agy-home", root=auth)
    outside = tmp_path / "outside" / "antigravity-cli"
    outside.mkdir(parents=True)
    (outside / "antigravity-oauth-token").write_bytes(b"read-from-elsewhere")
    cli_dir = staged / ".gemini" / "antigravity-cli"
    import shutil as _shutil

    _shutil.rmtree(cli_dir)
    cli_dir.symlink_to(outside, target_is_directory=True)

    agy_account.return_run_home(staged, root=auth)

    stored = agy_account.account_home(auth) / agy_account.TOKEN_FILE
    assert stored.read_bytes() == TOKEN


def test_credential_copies_are_never_indexed_as_artifacts() -> None:
    from harness_lab_app.artifact_service import is_indexable

    for credential in (
        "agy-home/.gemini/antigravity-cli/antigravity-oauth-token",
        "agy-exchange/code",
        "session/codex/auth.json",
    ):
        assert is_indexable(Path(credential)) is False, credential
    assert is_indexable(Path("report.md")) is True
    assert is_indexable(Path("steps/step-1.log")) is True


def test_the_validation_lane_cannot_read_a_runs_copy_of_the_sign_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lab(tmp_path, monkeypatch)
    (tmp_path / "data/runs/tetris-v1-K-960/agy-home").mkdir(parents=True)
    client = _Client()

    DockerRunExecutor(client_factory=lambda: client).start(
        "validate-K-1", ["true"], hardened=True, phase="validate"
    )

    assert "/app/data/runs/tetris-v1-K-960/agy-home" in client.run_kwargs["tmpfs"]
