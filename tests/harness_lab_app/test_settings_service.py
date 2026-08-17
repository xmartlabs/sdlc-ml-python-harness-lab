from __future__ import annotations

import pytest

from harness_lab_app import settings_service
from harness_lab_app.config import Settings
from harness_lab_app.env_file import values
from harness_lab_app.settings_service import (
    UNCHANGED_SENTINEL,
    SettingsError,
    apply_settings,
    mask_secret,
    read_runtime_facts,
    read_settings,
)

SAMPLE = """\
# Gateway
GATEWAY_BASE_URL=http://localhost:4000/v1
GATEWAY_TOKEN=sk-supersecret-token
OPENCODE_MODEL=gateway/qwen
"""


@pytest.fixture
def env_repo(tmp_path, monkeypatch):
    """Point the settings service at a throwaway repo root."""
    (tmp_path / ".env").write_text(SAMPLE)
    monkeypatch.setattr(
        settings_service, "get_settings", lambda: Settings(repo_root=tmp_path)
    )
    return tmp_path


def _item(payload: dict, key: str) -> dict:
    return next(item for item in payload["items"] if item["key"] == key)


def test_read_masks_secrets_and_never_echoes_them(env_repo):
    payload = read_settings()
    token = _item(payload, "GATEWAY_TOKEN")

    assert token["is_secret"] is True
    assert token["is_set"] is True
    assert "supersecret" not in token["value"]
    assert token["value"] == mask_secret("sk-supersecret-token")


def test_read_returns_plain_values_for_non_secrets(env_repo):
    payload = read_settings()
    assert _item(payload, "GATEWAY_BASE_URL")["value"] == "http://localhost:4000/v1"
    assert _item(payload, "NVIDIA_API_KEY")["is_set"] is False


def test_read_warns_about_duplicate_keys(env_repo):
    (env_repo / ".env").write_text(SAMPLE + "GATEWAY_TOKEN=sk-other\n")
    payload = read_settings()
    assert any("GATEWAY_TOKEN" in warning for warning in payload["warnings"])


def test_non_allowlisted_keys_are_rejected(env_repo):
    with pytest.raises(SettingsError) as exc:
        apply_settings({"HARNESS_LAB_DATABASE_URL": "sqlite:///evil.db"})

    assert "HARNESS_LAB_DATABASE_URL" in str(exc.value)
    assert values(env_repo / ".env") == {
        "GATEWAY_BASE_URL": "http://localhost:4000/v1",
        "GATEWAY_TOKEN": "sk-supersecret-token",
        "OPENCODE_MODEL": "gateway/qwen",
    }


def test_protected_auth_keys_are_not_editable():
    for key in settings_service.PROTECTED_KEYS:
        assert key not in settings_service.EDITABLE_KEYS


def test_the_sentinel_leaves_a_secret_untouched(env_repo):
    result = apply_settings(
        {"GATEWAY_TOKEN": UNCHANGED_SENTINEL, "OPENCODE_MODEL": "gateway/other"}
    )

    assert result["written"] == ["OPENCODE_MODEL"]
    stored = values(env_repo / ".env")
    assert stored["GATEWAY_TOKEN"] == "sk-supersecret-token"
    assert stored["OPENCODE_MODEL"] == "gateway/other"


def test_a_sentinel_only_update_writes_nothing(env_repo):
    before = (env_repo / ".env").read_text()
    result = apply_settings({"GATEWAY_TOKEN": UNCHANGED_SENTINEL})

    assert result == {"written": [], "backup": None}
    assert (env_repo / ".env").read_text() == before


def test_gateway_url_must_end_in_v1(env_repo):
    with pytest.raises(SettingsError, match="/v1"):
        apply_settings({"GATEWAY_BASE_URL": "http://localhost:4000"})


def test_model_ids_must_be_provider_qualified(env_repo):
    with pytest.raises(SettingsError, match="gateway/"):
        apply_settings({"OPENCODE_MODEL": "qwen"})


def test_writing_preserves_comments_and_backs_up(env_repo):
    result = apply_settings({"GATEWAY_TOKEN": "sk-rotated"})

    text = (env_repo / ".env").read_text()
    assert text.startswith("# Gateway\n")
    assert "sk-rotated" in text
    assert result["backup"] is not None


def test_empty_values_clear_a_key_without_tripping_validators(env_repo):
    apply_settings({"GATEWAY_BASE_URL": "", "OPENCODE_MODEL": ""})
    stored = values(env_repo / ".env")
    assert stored["GATEWAY_BASE_URL"] == ""
    assert stored["OPENCODE_MODEL"] == ""


def test_runtime_facts_are_read_only_and_exclude_secrets(env_repo):
    facts = read_runtime_facts()
    assert facts["repo_root"] == str(env_repo)
    assert set(facts) & set(settings_service.EDITABLE_KEYS) == set()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("", ""), ("short", "••••"), ("sk-abcdefghijkl", "sk-…ijkl")],
)
def test_mask_secret(raw, expected):
    assert mask_secret(raw) == expected
