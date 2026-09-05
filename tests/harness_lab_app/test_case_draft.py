import json
from unittest.mock import patch

import pytest

from harness_lab_app.case_draft_service import (
    _SYSTEM_PROMPT,
    REASON_MULTILINE,
    REASON_NOT_RUNNABLE,
    generate_case_draft,
    screen_commands,
)
from harness_lab_app.schemas import CaseDraftRequest, CaseDraftResponse


def test_generate_case_draft_success():
    fake_json_response = (
        '{"subject": "notion-export skill + export validation", '
        '"instruction": "Export page 123 and save to notes.txt", '
        '"validation_commands": ["test -s {workspace}/notes.txt"], '
        '"max_steps": 10}'
    )
    with patch(
        "harness_lab_app.case_draft_service._call_model",
        return_value=fake_json_response,
    ):
        req = CaseDraftRequest(
            description="Test notion export page 123",
            model="gpt-5.4-nano",
            selected_skills=[],
        )
        res = generate_case_draft(req)
        assert isinstance(res, CaseDraftResponse)
        assert res.subject == "notion-export skill + export validation"
        assert res.instruction == "Export page 123 and save to notes.txt"
        assert res.validation_commands == ["test -s {workspace}/notes.txt"]
        assert res.max_steps == 10


def test_generate_case_draft_markdown_fences():
    fenced_json = (
        "```json\n"
        '{"subject": "fenced case", "instruction": "do task", "validation_commands": ["exit 0"], "max_steps": 5}\n'
        "```"
    )
    with patch(
        "harness_lab_app.case_draft_service._call_model",
        return_value=fenced_json,
    ):
        req = CaseDraftRequest(description="Test something")
        res = generate_case_draft(req)
        assert res.subject == "fenced case"
        assert res.max_steps == 5


HEREDOC = (
    "node - <<'NODE'\n"
    "const assert = require('assert');\n"
    "const mod = require('{workspace}/snake.js');\n"
    "assert(typeof mod.createGame === 'function');\n"
    "NODE"
)


def _draft(commands: list[str], subject: str = "s") -> str:
    return json.dumps(
        {
            "subject": subject,
            "instruction": "do the task",
            "validation_commands": commands,
            "max_steps": 5,
        }
    )


def test_screen_commands_drops_multiline_and_prose_with_reasons():
    accepted, dropped = screen_commands(
        [HEREDOC, "Run the game and check it works", "pytest {workspace}/test_x.py -q"]
    )
    assert accepted == ["pytest {workspace}/test_x.py -q"]
    assert [item.command for item in dropped] == [
        HEREDOC,
        "Run the game and check it works",
    ]
    assert dropped[0].reason == REASON_MULTILINE
    assert dropped[1].reason == REASON_NOT_RUNNABLE


def test_generate_case_draft_reports_dropped_commands_next_to_kept_ones():
    with patch(
        "harness_lab_app.case_draft_service._call_model",
        return_value=_draft([HEREDOC, "node -e \"require('{workspace}/snake.js')\""]),
    ) as chat:
        res = generate_case_draft(CaseDraftRequest(description="snake game"))
    assert res.validation_commands == ["node -e \"require('{workspace}/snake.js')\""]
    assert [item.command for item in res.dropped_commands] == [HEREDOC]
    assert res.attempts == 1
    assert chat.call_count == 1


def test_generate_case_draft_retries_once_when_nothing_is_runnable():
    calls: list[tuple[str, str, str]] = []

    def fake_chat(model: str, system: str, user: str) -> str:
        calls.append((model, system, user))
        if len(calls) == 1:
            return _draft([HEREDOC], subject="first")
        return _draft(["pytest {workspace}/test_snake.py -q"], subject="second")

    with patch("harness_lab_app.case_draft_service._call_model", side_effect=fake_chat):
        res = generate_case_draft(CaseDraftRequest(description="snake game"))

    assert res.subject == "second"
    assert res.validation_commands == ["pytest {workspace}/test_snake.py -q"]
    assert res.dropped_commands == []
    assert res.attempts == 2
    # The retry carries the model's own answer and the reasons it was refused,
    # inside the one (system, user) pair the transport accepts.
    _, system, user = calls[1]
    assert system == _SYSTEM_PROMPT
    assert "Your previous answer:" in user
    assert '"subject": "first"' in user
    assert REASON_MULTILINE in user
    assert "ONE line" in user


def test_generate_case_draft_gives_up_after_the_retry_but_says_why():
    with patch(
        "harness_lab_app.case_draft_service._call_model",
        return_value=_draft([HEREDOC]),
    ) as chat:
        res = generate_case_draft(CaseDraftRequest(description="snake game"))
    assert res.validation_commands == []
    assert [item.reason for item in res.dropped_commands] == [REASON_MULTILINE]
    assert res.attempts == 2
    assert chat.call_count == 2


def test_generate_case_draft_string_commands_are_one_entry_not_split():
    heredoc_as_string = json.dumps(
        {"subject": "s", "instruction": "i", "validation_commands": HEREDOC}
    )
    with patch(
        "harness_lab_app.case_draft_service._call_model",
        return_value=heredoc_as_string,
    ):
        res = generate_case_draft(CaseDraftRequest(description="snake game"))
    # Splitting the string on newlines would have let `node - <<'NODE'` through
    # on its own — a command that exits 0 with an empty stdin.
    assert res.validation_commands == []
    assert len(res.dropped_commands) == 1


def test_system_prompt_states_the_one_line_rule():
    assert "exactly ONE line" in _SYSTEM_PROMPT
    assert "No heredocs" in _SYSTEM_PROMPT


def test_generate_case_draft_empty_desc():
    with pytest.raises(ValueError, match="Description cannot be empty"):
        generate_case_draft(CaseDraftRequest(description="   "))


def test_generate_case_draft_refuses_without_any_llm_transport():
    """No gateway and no Claude credential is a configuration error, not a draft."""
    from harness_lab_app import llm_transport

    with (
        patch.object(
            llm_transport,
            "chat",
            side_effect=llm_transport.TransportNotConfigured("none"),
        ),
        pytest.raises(ValueError, match="No LLM transport is configured"),
    ):
        generate_case_draft(CaseDraftRequest(description="valid desc"))


def test_generate_case_draft_rides_the_shared_transport(monkeypatch):
    """The assistant is a fifth caller of llm_transport, not a fifth HTTP client.

    A subscription-only lab (ADR-0043) drafts cases the same way it audits
    runs: whatever transport is active answers, with the summary-tier default
    of that transport when the request names no model.
    """
    from harness_lab_app import case_draft_service, llm_transport

    seen: dict = {}

    def fake_chat(model, system, user, *, timeout, temperature):
        seen.update(model=model, system=system, user=user, timeout=timeout)
        return _draft(["pytest {workspace}/test_x.py -q"])

    monkeypatch.delenv("CASE_DRAFT_MODEL", raising=False)
    monkeypatch.delenv("GATEWAY_DEFAULT_MODEL", raising=False)
    monkeypatch.setattr(case_draft_service, "read_env_file_value", lambda _key: None)
    monkeypatch.setattr(llm_transport, "chat", fake_chat)
    monkeypatch.setattr(
        llm_transport,
        "default_summary_model",
        lambda _transport=None: "claude-haiku-4-5",
    )

    res = case_draft_service.generate_case_draft(
        CaseDraftRequest(description="Build a thing")
    )

    assert res.validation_commands == ["pytest {workspace}/test_x.py -q"]
    assert seen["model"] == "claude-haiku-4-5"
    assert seen["system"] == _SYSTEM_PROMPT
    assert "Build a thing" in seen["user"]
    assert seen["timeout"] == case_draft_service.DRAFT_TIMEOUT_SECONDS


def test_api_generate_case_draft_route():
    from harness_lab_app.api import create_app

    app = create_app()
    found = False
    for route in app.router.routes:
        if getattr(route, "path", None) == "/api/cases/generate-draft":
            found = True
            endpoint = route.endpoint
            break
    assert found, "Route /api/cases/generate-draft not found"

    with patch("harness_lab_app.api.generate_case_draft") as mock_gen:
        mock_gen.return_value = CaseDraftResponse(
            subject="Test subject",
            instruction="Do task",
            validation_commands=["test -f {workspace}/out.txt"],
            max_steps=5,
        )
        res = endpoint(
            req=CaseDraftRequest(
                description="Test case description", model="gpt-5.4-nano"
            )
        )
        assert res.subject == "Test subject"
        assert res.max_steps == 5


def test_repository_context_reaches_the_draft_prompt(monkeypatch, tmp_path):
    """A case drafted blind names files the repository does not have.

    So when the request carries a target, the assistant is shown the checkout
    the run will actually use (ADR-0042).
    """
    from harness_lab_app import case_draft_service, checkout_service, config
    from harness_lab_app.models import TargetRepository

    settings = config.Settings(repo_root=tmp_path)
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(checkout_service, "get_settings", lambda: settings)
    checkout = tmp_path / "data" / "checkouts" / "payments"
    checkout.mkdir(parents=True)
    (checkout / ".git").mkdir()
    (checkout / "README.md").write_text("# Payments\n")
    (checkout / "src").mkdir()

    target = TargetRepository(
        target_id="payments",
        name="Payments",
        repository_url="https://example.invalid/payments.git",
        checkout_path=str(checkout),
        validation_commands=["make check"],
    )
    monkeypatch.setattr(
        case_draft_service,
        "_load_repo_context",
        lambda _target_id: checkout_service.repo_context(target).as_prompt_block(),
    )

    seen: dict = {}

    def _fake_call(model, system, user):
        seen.update(model=model, system=system, user=user)
        return json.dumps(
            {
                "subject": "payments: add a refund endpoint",
                "instruction": "Add a refund endpoint.",
                "validation_commands": ["make check"],
                "max_steps": 10,
            }
        )

    monkeypatch.setattr(case_draft_service, "_call_model", _fake_call)

    case_draft_service.generate_case_draft(
        CaseDraftRequest(description="Add a refund endpoint", target_id="payments")
    )

    prompt = seen["user"]
    assert "Repository context" in prompt
    assert "make check" in prompt
    assert "# Payments" in prompt


def test_a_draft_without_a_target_carries_no_repository_context(monkeypatch):
    """The assistant still works for a repository nobody has cloned yet."""
    from harness_lab_app import case_draft_service

    seen: dict = {}

    def _fake_call(model, system, user):
        seen.update(model=model, system=system, user=user)
        return json.dumps(
            {
                "subject": "s",
                "instruction": "i",
                "validation_commands": ["pytest {workspace}/test_x.py -q"],
                "max_steps": 5,
            }
        )

    monkeypatch.setattr(case_draft_service, "_call_model", _fake_call)
    case_draft_service.generate_case_draft(
        CaseDraftRequest(description="Build a thing")
    )
    assert "Repository context" not in seen["user"]
