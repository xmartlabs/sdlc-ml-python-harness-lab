from __future__ import annotations

import os
import stat

import pytest

from harness_lab_app.env_file import (
    MANAGED_HEADER,
    EnvFileError,
    duplicate_keys,
    parse_env,
    read_all,
    render_env,
    validate_assignment,
    values,
    write_env,
)

SAMPLE = """\
# Gateway
GATEWAY_BASE_URL=http://localhost:4000/v1
GATEWAY_TOKEN=sk-old

# Models
OPENCODE_MODEL=gateway/qwen
"""


def test_parse_preserves_every_raw_line():
    lines = parse_env(SAMPLE)
    assert [ln.raw for ln in lines] == SAMPLE.splitlines()
    kinds = [ln.kind for ln in lines]
    assert kinds == [
        "comment",
        "assignment",
        "assignment",
        "blank",
        "comment",
        "assignment",
    ]


def test_render_updates_in_place_and_keeps_comments():
    lines = parse_env(SAMPLE)
    rendered = render_env(lines, {"GATEWAY_TOKEN": "sk-new"})
    assert rendered.splitlines() == [
        "# Gateway",
        "GATEWAY_BASE_URL=http://localhost:4000/v1",
        "GATEWAY_TOKEN=sk-new",
        "",
        "# Models",
        "OPENCODE_MODEL=gateway/qwen",
    ]


def test_render_appends_unknown_keys_under_a_managed_header():
    rendered = render_env(parse_env(SAMPLE), {"NVIDIA_API_KEY": "nv-1"})
    tail = rendered.splitlines()[-2:]
    assert tail == [MANAGED_HEADER, "NVIDIA_API_KEY=nv-1"]


def test_render_updates_the_last_occurrence():
    """Shell sourcing is last-wins, so the writer must target the same line."""
    text = "TOKEN=first\nTOKEN=second\n"
    rendered = render_env(parse_env(text), {"TOKEN": "third"})
    assert rendered == "TOKEN=first\nTOKEN=third\n"


def test_duplicate_keys_are_reported():
    assert duplicate_keys(parse_env("A=1\nB=2\nA=3\n")) == ["A"]
    assert duplicate_keys(parse_env(SAMPLE)) == []


@pytest.mark.parametrize(
    "value",
    ["has space", 'has"quote', "has'quote", "has#hash", "has\\backslash"],
)
def test_unquotable_values_are_rejected(value):
    with pytest.raises(EnvFileError):
        validate_assignment("TOKEN", value)


@pytest.mark.parametrize("key", ["lower", "1LEADING", "WITH-DASH", ""])
def test_invalid_keys_are_rejected(key):
    with pytest.raises(EnvFileError):
        validate_assignment(key, "ok")


def test_write_is_atomic_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text(SAMPLE)
    write_env(path, {"GATEWAY_TOKEN": "sk-new"}, tmp_path / "backups")

    assert values(path)["GATEWAY_TOKEN"] == "sk-new"
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".env.")] == []


def test_write_is_owner_only(tmp_path):
    path = tmp_path / ".env"
    path.write_text(SAMPLE)
    write_env(path, {"GATEWAY_TOKEN": "sk-new"}, tmp_path / "backups")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_write_backs_up_the_previous_content(tmp_path):
    path = tmp_path / ".env"
    path.write_text(SAMPLE)
    backup = write_env(path, {"GATEWAY_TOKEN": "sk-new"}, tmp_path / "backups")

    assert backup is not None
    assert backup.read_text() == SAMPLE
    assert stat.S_IMODE(os.stat(backup).st_mode) == 0o600


def test_a_rejected_value_leaves_the_file_untouched(tmp_path):
    path = tmp_path / ".env"
    path.write_text(SAMPLE)
    with pytest.raises(EnvFileError):
        write_env(path, {"GATEWAY_TOKEN": "sk new"}, tmp_path / "backups")

    assert path.read_text() == SAMPLE
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".env.")] == []


def test_creating_a_missing_file_reports_no_backup(tmp_path):
    path = tmp_path / ".env"
    backup = write_env(path, {"GATEWAY_TOKEN": "sk-1"}, tmp_path / "backups")

    assert backup is None
    assert values(path) == {"GATEWAY_TOKEN": "sk-1"}


def test_read_all_of_a_missing_file_is_empty(tmp_path):
    assert read_all(tmp_path / "nope") == []
