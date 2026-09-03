from unittest.mock import patch
import pytest

from harness_lab_app.case_draft_service import generate_case_draft
from harness_lab_app.schemas import CaseDraftRequest, CaseDraftResponse


def test_generate_case_draft_success():
    fake_json_response = (
        '{"subject": "notion-export skill + export validation", '
        '"instruction": "Export page 123 and save to notes.txt", '
        '"validation_commands": ["test -s {workspace}/notes.txt"], '
        '"max_steps": 10}'
    )
    with patch("harness_lab_app.case_draft_service._call_gateway_chat", return_value=fake_json_response):
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
        '```json\n'
        '{"subject": "fenced case", "instruction": "do task", "validation_commands": ["exit 0"], "max_steps": 5}\n'
        '```'
    )
    with patch("harness_lab_app.case_draft_service._call_gateway_chat", return_value=fenced_json):
        req = CaseDraftRequest(description="Test something")
        res = generate_case_draft(req)
        assert res.subject == "fenced case"
        assert res.max_steps == 5


def test_generate_case_draft_empty_desc():
    with pytest.raises(ValueError, match="Description cannot be empty"):
        generate_case_draft(CaseDraftRequest(description="   "))


def test_generate_case_draft_missing_gateway():
    with patch("harness_lab_app.case_draft_service._gateway", return_value=None):
        with pytest.raises(ValueError, match="Gateway not configured"):
            generate_case_draft(CaseDraftRequest(description="valid desc"))

