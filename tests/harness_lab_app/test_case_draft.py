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


# A check the lab writes and runs; the agent never authors it (ADR-0071).
LAB_CHECK = (
    "python3 -c \"import sys; sys.path.insert(0, '{workspace}'); "
    'import snake; sys.exit(0 if snake.step(1) == 2 else 1)"'
)

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
        [HEREDOC, "Run the game and check it works", LAB_CHECK]
    )
    assert accepted == [LAB_CHECK]
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

    def fake_chat(model: str, system: str, user: str, *, target_id: str = "") -> str:
        calls.append((model, system, user))
        if len(calls) == 1:
            return _draft([HEREDOC], subject="first")
        return _draft([LAB_CHECK], subject="second")

    with patch("harness_lab_app.case_draft_service._call_model", side_effect=fake_chat):
        res = generate_case_draft(CaseDraftRequest(description="snake game"))

    assert res.subject == "second"
    assert res.validation_commands == [LAB_CHECK]
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

    def fake_chat(
        model,
        system,
        user,
        *,
        timeout,
        temperature,
        trace_kind="",
        trace_metadata=None,
    ):
        seen.update(model=model, system=system, user=user, timeout=timeout)
        return _draft([LAB_CHECK])

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

    assert res.validation_commands == [LAB_CHECK]
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

    def _fake_call(model, system, user, *, target_id=""):
        seen.update(model=model, system=system, user=user, target_id=target_id)
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

    def _fake_call(model, system, user, *, target_id=""):
        seen.update(model=model, system=system, user=user, target_id=target_id)
        return json.dumps(
            {
                "subject": "s",
                "instruction": "i",
                "validation_commands": [LAB_CHECK],
                "max_steps": 5,
            }
        )

    monkeypatch.setattr(case_draft_service, "_call_model", _fake_call)
    case_draft_service.generate_case_draft(
        CaseDraftRequest(description="Build a thing")
    )
    assert "Repository context" not in seen["user"]


# --- The whole case shape, for the Cases form (one press saves everything) ---


def _full_draft(**overrides) -> str:
    payload = {
        "subject": "JSON config merger",
        "instruction": "Write {workspace}/merge.py that merges two config files.",
        "validation_commands": [LAB_CHECK],
        "max_steps": 10,
        "case_id": "json-config-merger",
        "description": "Measures whether the agent can merge nested configs.",
        "case_type": "single_prompt",
        "step_prompts": [],
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_a_draft_carries_the_whole_case_not_just_the_instruction():
    """The Cases form saves a case in one press, so the model shapes all of it."""
    with patch(
        "harness_lab_app.case_draft_service._call_model", return_value=_full_draft()
    ):
        res = generate_case_draft(CaseDraftRequest(description="merge configs"))
    assert res.case_id == "json-config-merger"
    assert res.description == "Measures whether the agent can merge nested configs."
    assert res.case_type == "single_prompt"
    assert res.step_prompts == []


def test_a_multi_step_draft_keeps_its_plan_and_a_budget_that_fits_it():
    """A budget below 1 + len(step_prompts) stops the run mid-plan."""
    with patch(
        "harness_lab_app.case_draft_service._call_model",
        return_value=_full_draft(
            case_type="multi_step",
            step_prompts=["Implement the merge.", "Write the tests.", "Run them."],
            max_steps=2,
        ),
    ):
        res = generate_case_draft(CaseDraftRequest(description="merge configs"))
    assert res.case_type == "multi_step"
    assert len(res.step_prompts) == 3
    assert res.max_steps == 4


def test_step_prompts_are_dropped_on_a_type_that_never_reads_them():
    """The runner only opens step_prompts for multi_step cases."""
    with patch(
        "harness_lab_app.case_draft_service._call_model",
        return_value=_full_draft(case_type="bugfix", step_prompts=["step two"]),
    ):
        res = generate_case_draft(CaseDraftRequest(description="fix the bug"))
    assert res.case_type == "bugfix"
    assert res.step_prompts == []


def test_a_drafted_case_can_never_claim_a_swe_bench_type():
    """Those types carry dataset test lists the lab scores against."""
    with patch(
        "harness_lab_app.case_draft_service._call_model",
        return_value=_full_draft(case_type="swe_bench_instance"),
    ):
        res = generate_case_draft(CaseDraftRequest(description="fix flask"))
    assert res.case_type == "single_prompt"


def test_the_proposed_case_id_avoids_ids_the_catalog_already_has():
    """Otherwise the person meets the collision by pressing Create."""
    with patch(
        "harness_lab_app.case_draft_service._call_model", return_value=_full_draft()
    ):
        res = generate_case_draft(
            CaseDraftRequest(
                description="merge configs",
                existing_case_ids=["json-config-merger", "json-config-merger-2"],
            )
        )
    assert res.case_id == "json-config-merger-3"


def test_a_missing_case_id_is_derived_from_the_subject():
    with patch(
        "harness_lab_app.case_draft_service._call_model",
        return_value=_full_draft(case_id="  ", subject="Retry Policy: HTTP client"),
    ):
        res = generate_case_draft(CaseDraftRequest(description="retry policy"))
    assert res.case_id == "retry-policy-http-client"


def test_a_draft_carries_the_rubric_the_model_wrote():
    fake = (
        '{"subject": "s", "instruction": "i", "validation_commands": ["test -s {workspace}/x"], '
        '"scoring_rubric": "10 when x exists and says hello; 0 otherwise."}'
    )
    with patch("harness_lab_app.case_draft_service._call_model", return_value=fake):
        res = generate_case_draft(
            CaseDraftRequest(description="d", model="m", selected_skills=[])
        )
    assert res.scoring_rubric == "10 when x exists and says hello; 0 otherwise."


def test_a_draft_without_a_rubric_gets_one_that_can_be_saved():
    """A case with commands and no rubric is refused at save time (ADR-0057);
    a draft the wizard cannot save is not a draft."""
    from harness_lab_app.case_validation import check_case

    fake = '{"subject": "notion export", "instruction": "i", "validation_commands": ["test -s {workspace}/x"]}'
    with patch("harness_lab_app.case_draft_service._call_model", return_value=fake):
        res = generate_case_draft(
            CaseDraftRequest(description="d", model="m", selected_skills=[])
        )
    assert "notion export" in res.scoring_rubric
    assert "Score 10" in res.scoring_rubric and "Score 0" in res.scoring_rubric
    assert check_case(res.validation_commands, res.scoring_rubric, {}) == []


def test_the_draft_prompt_asks_for_a_rubric():
    from harness_lab_app.case_draft_service import _SYSTEM_PROMPT

    assert '"scoring_rubric"' in _SYSTEM_PROMPT


# --- What the grade is made of (ADR-0071, ADR-0076) --------------------------


@pytest.mark.parametrize(
    "command",
    [
        "pytest {workspace}/test_pipeline.py -q",
        "cd {workspace} && python -m pytest -q",
        "python3 -m pytest {workspace}/tests -q",
        "node {workspace}/snake.test.js",
        "npx jest {workspace}",
        "cd {workspace} && npm test",
    ],
)
def test_a_drafted_check_that_runs_the_agents_own_tests_is_dropped(command):
    """eda-ml-iris-categ-numerico graded every run on the tests it wrote.

    The workspace of a case with no repository starts empty, so any test file
    in it is the agent's. Six easy tests then pass where twenty-five demanding
    ones fail one: the grade measures the subject's own exam (ADR-0071).
    """
    accepted, dropped = screen_commands([command, LAB_CHECK])
    assert accepted == [LAB_CHECK]
    assert [item.command for item in dropped] == [command]
    assert "agent" in dropped[0].reason and "wrote" in dropped[0].reason


def test_inside_a_repository_its_own_test_suite_is_the_grade():
    """A repo_task runs against a clone whose tests the agent did not write."""
    accepted, dropped = screen_commands(
        ["cd {workspace} && python -m pytest -q", "make -C {workspace} test"],
        in_repository=True,
    )
    assert accepted == [
        "cd {workspace} && python -m pytest -q",
        "make -C {workspace} test",
    ]
    assert dropped == []


def test_a_check_that_imports_a_named_test_of_the_lab_is_not_mistaken_for_one():
    """Only running a test the agent wrote is refused, not the word "test"."""
    command = "python3 -c \"import json; d=json.load(open('{workspace}/test_results.json')); assert d\""
    accepted, dropped = screen_commands([command])
    assert accepted == [command]
    assert dropped == []


def test_the_draft_prompt_forbids_grading_with_the_agents_tests():
    assert "never" in _SYSTEM_PROMPT.lower()
    assert "tests the agent" in _SYSTEM_PROMPT
    # The old advice, which produced exactly that, is gone.
    assert "pytest {workspace}/test_*.py" not in _SYSTEM_PROMPT
    assert "have the instruction ask for it and run it" not in _SYSTEM_PROMPT


def test_the_draft_prompt_asks_for_one_check_per_behaviour():
    """Per-step progress is the share of checks passing (ADR-0076).

    One `pytest file.py` makes every step read 0 or 1, so the curve shows
    nothing of how the agent got there.
    """
    assert "one check per behaviour" in _SYSTEM_PROMPT
    assert "per-step" in _SYSTEM_PROMPT


def test_a_draft_declares_what_its_graders_import():
    """eda-ml-iris-categ-numerico-K-003: `No module named 'numpy'` in the runner.

    The checks run offline in a clean runner that ships no scientific stack;
    what they import is installed first only when the case declares it.
    """
    fake = _draft([LAB_CHECK])
    payload = json.loads(fake)
    payload["validation_requirements"] = ["numpy==2.4.6", "pandas>=2.2"]
    with patch(
        "harness_lab_app.case_draft_service._call_model",
        return_value=json.dumps(payload),
    ):
        res = generate_case_draft(CaseDraftRequest(description="iris pipeline"))
    assert res.validation_requirements == ["numpy==2.4.6", "pandas>=2.2"]
    assert '"validation_requirements"' in _SYSTEM_PROMPT


def test_a_requirement_the_lab_would_not_install_is_dropped_from_the_draft():
    """The install line is a shell command: only a plain name and version passes."""
    payload = json.loads(_draft([LAB_CHECK]))
    payload["validation_requirements"] = [
        "numpy==2.4.6",
        "numpy==2.4.6",
        "pandas; rm -rf /",
        "git+https://example.invalid/pkg.git",
        "",
        42,
    ]
    with patch(
        "harness_lab_app.case_draft_service._call_model",
        return_value=json.dumps(payload),
    ):
        res = generate_case_draft(CaseDraftRequest(description="d"))
    assert res.validation_requirements == ["numpy==2.4.6"]


def test_a_draft_with_no_requirements_says_so_with_an_empty_list():
    with patch(
        "harness_lab_app.case_draft_service._call_model",
        return_value=_draft([LAB_CHECK]),
    ):
        res = generate_case_draft(CaseDraftRequest(description="d"))
    assert res.validation_requirements == []


def test_a_draft_for_a_repository_keeps_its_test_suite_as_the_grade(monkeypatch):
    """Even when the checkout could not be read: the workspace is still a clone."""
    from harness_lab_app import case_draft_service

    monkeypatch.setattr(case_draft_service, "_load_repo_context", lambda _t: "")
    with patch(
        "harness_lab_app.case_draft_service._call_model",
        return_value=_draft(["cd {workspace} && python -m pytest -q"]),
    ):
        res = generate_case_draft(
            CaseDraftRequest(description="add a refund endpoint", target_id="payments")
        )
    assert res.validation_commands == ["cd {workspace} && python -m pytest -q"]
    assert res.dropped_commands == []
