"""Mining candidate cases from a repository's own history (ADR-0032).

The rules are tested against parsed log text rather than a live repository:
what makes a candidate good is a judgement encoded in path and subject
heuristics, and those must stay reviewable without a network.
"""

from __future__ import annotations

import pytest

from harness_lab_app.case_derivation import (
    CaseCandidate,
    derive_candidates,
    is_noise_path,
    is_test_path,
    parse_log,
    select_candidates,
)
from harness_lab_app.target_service import TargetError


def _commit(sha: str, subject: str, files: list[str], when="2026-01-01T00:00:00Z"):
    return f"\x1e{sha}\x00{subject}\x00{when}\n" + "\n".join(files)


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_api.py",
        "src/spec/thing_spec.rb",
        "app/test_helpers.py",
        "web/Button.test.tsx",
        "web/Button.spec.ts",
        "tests/conftest.py",
        "internal/foo_test.go",
    ],
)
def test_recognises_the_shapes_a_test_file_takes(path):
    assert is_test_path(path)


@pytest.mark.parametrize("path", ["src/app.py", "lib/protest.py", "src/contest/x.go"])
def test_does_not_mistake_source_for_tests(path):
    assert not is_test_path(path)


@pytest.mark.parametrize(
    "path", ["CHANGELOG.md", "uv.lock", ".github/workflows/ci.yml", "pyproject.toml"]
)
def test_treats_bookkeeping_files_as_noise(path):
    assert is_noise_path(path)


def test_a_commit_is_split_into_its_change_and_its_verification():
    raw = _commit(
        "a" * 40,
        "feat: add customer status endpoint",
        ["src/api/customers.py", "tests/test_customers.py", "CHANGELOG.md"],
    )
    (candidate,) = parse_log(raw)

    assert candidate.source_files == ["src/api/customers.py"]
    assert candidate.test_files == ["tests/test_customers.py"]
    # Bookkeeping is neither the change nor its verification.
    assert "CHANGELOG.md" not in candidate.source_files


def test_only_commits_that_carry_their_own_tests_qualify():
    raw = "".join(
        [
            _commit("a" * 40, "feat: with tests", ["src/a.py", "tests/test_a.py"]),
            _commit("b" * 40, "feat: no tests", ["src/b.py"]),
            _commit("c" * 40, "test: only tests", ["tests/test_c.py"]),
        ]
    )
    picked = select_candidates(parse_log(raw))
    assert [c.subject for c in picked] == ["feat: with tests"]


@pytest.mark.parametrize(
    "subject",
    ["Merge pull request #12", 'Revert "feat: x"', "bump deps", "chore: tidy"],
)
def test_drops_commits_that_are_not_a_task(subject):
    raw = _commit("a" * 40, subject, ["src/a.py", "tests/test_a.py"])
    assert select_candidates(parse_log(raw)) == []


def test_drops_a_commit_too_large_to_be_one_task():
    """A forty-file commit is a release, not a case."""
    files = [f"src/mod_{i}.py" for i in range(20)] + ["tests/test_all.py"]
    raw = _commit("a" * 40, "feat: the whole quarter", files)
    assert select_candidates(parse_log(raw)) == []
    assert len(select_candidates(parse_log(raw), max_source_files=25)) == 1


def test_a_candidate_is_traceable_to_the_commit_it_came_from():
    candidate = CaseCandidate(
        commit="abc1234" + "0" * 33,
        subject="feat: Add Customer Status!",
        authored_at="2026-01-01T00:00:00Z",
        source_files=["src/a.py"],
        test_files=["tests/test_a.py"],
    )
    # The id carries the sha so a case cannot be quietly rewritten to fit a
    # result and still claim the same lineage.
    assert candidate.suggested_case_id == "feat-add-customer-status-abc1234"
    assert candidate.as_dict()["commit"] == candidate.commit


def test_the_suggested_prompt_states_the_task_without_handing_over_the_answer():
    candidate = CaseCandidate(
        commit="a" * 40,
        subject="feat: add customer status endpoint",
        authored_at="2026-01-01T00:00:00Z",
        source_files=["src/api/customers.py"],
        test_files=["tests/test_customers.py"],
    )
    prompt = candidate.suggested_prompt()

    assert "feat: add customer status endpoint" in prompt
    assert "src/api/customers.py" in prompt
    assert "tests/test_customers.py" in prompt
    # A case that quotes the original diff measures transcription, not work.
    assert "diff" not in prompt.lower()
    assert "+++" not in prompt


def test_a_url_git_would_read_as_an_option_never_reaches_git(tmp_path):
    with pytest.raises(TargetError):
        derive_candidates("--upload-pack=touch /tmp/pwned", "main", tmp_path, "evil")
