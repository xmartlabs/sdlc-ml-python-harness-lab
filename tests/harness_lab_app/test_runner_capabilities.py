"""What the runner can execute, and what one command asks of it.

The point of these tests is that none of them needs a container. Classification
is what decides whether a repository can be evaluated at all, so it must reach
the same verdict in this file, in the API process (which has no docker socket,
ADR-0002) and in the worker. Anything that needs a real runtime is parsed from
a literal banner instead of measured.
"""

from __future__ import annotations

import pytest

from harness_lab_app import runner_capabilities as caps

# --------------------------------------------------------------------------
# What the runner offers
# --------------------------------------------------------------------------


def test_the_declared_capabilities_need_no_container():
    declared = caps.declared_capabilities()

    assert set(declared) == set(caps.DECLARED_RUNTIMES)
    assert {"python", "node", "bash"} <= set(declared)
    assert all(capability.available for capability in declared.values())
    # A version nobody measured is a guess dressed as a fact (ADR-0048).
    assert all(capability.version == "" for capability in declared.values())
    assert all(capability.source == "declared" for capability in declared.values())


def test_a_probe_answer_is_parsed_into_versions():
    text = (
        "python3\t/opt/venv/bin/python3\tPython 3.11.9\n"
        "node\t/usr/bin/node\tv20.11.0\n"
        "bash\t/usr/bin/bash\tGNU bash, version 5.2.37(1)-release "
        "(x86_64-pc-linux-gnu)\n"
        "sh\t/bin/sh\t\n"
        "git\t/usr/bin/git\tgit version 2.39.5\n"
    )

    probed = caps.parse_probe_output(text)

    assert probed["python"].version == "3.11.9"
    assert probed["node"].version == "20.11.0"
    assert probed["bash"].version == "5.2.37"
    assert probed["bash"].executable == "/usr/bin/bash"
    assert probed["git"].version == "2.39.5"
    # `sh` (dash) answers `--version` on stderr with no dotted number; it is
    # still available, it just cannot say what it is.
    assert probed["sh"].available is True
    assert probed["sh"].version == ""
    assert all(capability.source == "probed" for capability in probed.values())


def test_a_runtime_the_probe_never_mentions_is_simply_absent():
    probed = caps.parse_probe_output("bash\t/usr/bin/bash\tGNU bash, version 5.2.37\n")

    assert set(probed) == {"bash"}


@pytest.mark.parametrize("text", ["", "garbage", "python3\n", "\t\t\n"])
def test_a_probe_that_says_nothing_useful_is_not_an_error(text: str):
    assert caps.parse_probe_output(text) == {}


def test_probing_never_downgrades_what_the_image_declares(monkeypatch):
    """A probe that cannot run leaves the declared answer standing.

    The API process has no docker socket, and the subprocess backend on a macOS
    host would answer for the host rather than for the runner. Either way the
    audit must still know the runner ships bash.
    """
    monkeypatch.setattr(caps, "_probe_versions", lambda **_kwargs: {})

    resolved = caps.runner_capabilities()

    assert set(resolved) == set(caps.DECLARED_RUNTIMES)
    assert resolved["bash"].source == "declared"


def test_probed_versions_enrich_the_declared_table(monkeypatch):
    monkeypatch.setattr(
        caps,
        "_probe_versions",
        lambda **_kwargs: caps.parse_probe_output(
            "bash\t/usr/bin/bash\tGNU bash, version 5.2.37\n"
        ),
    )

    resolved = caps.runner_capabilities()

    assert resolved["bash"].version == "5.2.37"
    assert resolved["bash"].source == "probed"
    assert resolved["python"].source == "declared"


def test_capabilities_survive_a_round_trip_through_evidence():
    original = caps.parse_probe_output(
        "bash\t/usr/bin/bash\tGNU bash, version 5.2.37\n"
    )

    rebuilt = caps.capabilities_from_dicts(caps.capabilities_as_dicts(original))

    assert rebuilt == original


# --------------------------------------------------------------------------
# What one command asks for
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "runtime"),
    [
        # Everything Python and Node repositories already run, unchanged.
        ("pytest -x -q", "python"),
        ("uv run pytest -q", "python"),
        ("python3 -m pytest", "python"),
        ("ruff check .", "python"),
        ("npm test", "node"),
        ("pnpm run test", "node"),
        ("npx vitest run", "node"),
        ("make test", "make"),
        # Shell, the runtime this model was built for.
        ("bash tests/test_example.sh", "bash"),
        ('for t in tests/test_*.sh; do bash "$t"; done', "bash"),
        ("bash benchmark/model/run-model.sh", "bash"),
        ("sh scripts/smoke.sh", "sh"),
        # Wrappers a validation command conventionally carries.
        ("timeout 300 pytest -q", "python"),
        ("timeout -k 5 300 bash tests/run.sh", "bash"),
        ("CI=1 npm test", "node"),
        ("cd data/runs/r-1/workspace && pytest -q", "python"),
        # Runtimes the runner does not ship: named, so they can be blocked.
        ("go test ./...", "go"),
        ("cargo test", "rust"),
        ("mvn verify", "java"),
        # Nothing recognizable is a plain shell line, not a blocked case.
        ("./scripts/check", "sh"),
        ("echo hello", "sh"),
        ("", ""),
    ],
)
def test_a_command_says_which_runtime_it_needs(command: str, runtime: str):
    assert caps.runtime_for_command(command) == runtime


def test_bash_syntax_asks_for_bash_even_without_naming_it():
    assert caps.runtime_for_command("[[ -f AGENTS.md ]] && echo ok") == "bash"
    assert caps.runtime_for_command("set -o pipefail; ./run") == "bash"
    assert caps.has_bashism("diff <(a) <(b)") is True
    assert caps.has_bashism("test -f AGENTS.md") is False


def test_the_shell_stays_sh_for_everything_python_and_node_already_ran():
    """The one behavioral promise of the interpreter change.

    `argv[0]` varies only when a command actually needs bash. Every command the
    lab ran under `sh -c` before this feature still runs under `sh -c`.
    """
    for command in ("make test", "pytest -q", "npm test", "uv run pytest -x -q"):
        assert caps.shell_for_command(command) == "sh"


def test_a_bash_command_is_handed_to_bash():
    # `/bin/sh` in the runner image is dash, which cannot parse `[[`.
    assert caps.shell_for_command("bash tests/test_parse.sh") == "bash"
    assert caps.shell_for_command("[[ -f AGENTS.md ]]") == "bash"


def test_the_shell_falls_back_rather_than_failing_when_bash_is_absent(monkeypatch):
    """Same portability rule `_portable_timeout` applies to GNU timeout."""
    monkeypatch.setattr(caps.shutil, "which", lambda _name: None)

    assert caps.shell_for_command("bash tests/test_parse.sh") == "sh"


# --------------------------------------------------------------------------
# Runtime versions
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("required", "available", "verdict"),
    [
        ("3.2", "3.2.57", "exact"),
        ("3.2.57", "3.2.57", "exact"),
        ("3", "3.11.9", "exact"),
        # talos targets bash 3.2; the runner ships 5.2.37. Not a certification.
        ("3.2", "5.2.37", "partial"),
        ("20", "18.19.0", "partial"),
        ("3.2.57", "3.2", "partial"),
        ("", "5.2.37", "unknown"),
        ("3.2", "", "unknown"),
        ("not-a-version", "5.2.37", "unknown"),
    ],
)
def test_a_required_version_is_compared_honestly(
    required: str, available: str, verdict: str
):
    assert caps.compare_runtime_version(required, available) == verdict


def test_a_model_supplied_version_is_clamped_to_a_shape():
    assert caps.clean_version("3.2") == "3.2"
    assert caps.clean_version(" 5.2.37 ") == "5.2.37"
    assert caps.clean_version("3.2; rm -rf /") == ""
    assert caps.clean_version("latest") == ""
    assert caps.clean_version(None) == ""
