"""Projecting a harness out of a runner whose adapter files are masked.

The `injected` arm has to satisfy two rules that pull against each other. The
agent's cwd lives under the lab root, so the lab's `CLAUDE.md` must be masked
to an empty stub or the arm reads its harness twice (ADR-0049). And the
projection has to splice the generated-skills region into that very file, so it
needs the real bytes. These tests pin the resolution: the adapter files are
read from a root the mask does not cover, and a source that cannot be spliced
is refused by name instead of failing four frames deeper about the *target's*
file.
"""

from __future__ import annotations

import pytest

from ml_python_base.skills_sync.errors import SkillsSyncError
from ml_python_base.skills_sync.projection import project_harness
from ml_python_base.skills_sync.renderer import BEGIN_MARKER, END_MARKER

ADAPTER = f"# Lab rules\n\n{BEGIN_MARKER}\n(old)\n{END_MARKER}\n"


@pytest.fixture
def registry(tmp_path):
    """A one-tool registry whose adapter file is `CLAUDE.md`."""
    from ml_python_base.skills_sync.config import load_registry

    (tmp_path / "adapters").mkdir(parents=True, exist_ok=True)
    (tmp_path / "adapters" / "registry.toml").write_text(
        "[governance]\n"
        "files = []\n"
        'automation = ""\n'
        'orchestration = ""\n'
        "\n"
        "[[tool]]\n"
        'id = "claude"\n'
        'adapter_file = "CLAUDE.md"\n'
        'adapter_template = "claude.md.j2"\n'
    )
    return load_registry(tmp_path / "adapters" / "registry.toml")


@pytest.fixture
def source(tmp_path):
    """A lab root with the Jinja templates the renderer needs."""
    root = tmp_path / "lab"
    (root / "adapters" / "templates").mkdir(parents=True)
    (root / "adapters" / "templates" / "claude.md.j2").write_text(
        "Skills: {{ internal_skills | length }} internal."
    )
    return root


@pytest.fixture
def target(tmp_path):
    """A client repository that already has governance of its own."""
    root = tmp_path / "client"
    root.mkdir()
    (root / "CLAUDE.md").write_text("# Their own rules, no sentinels\n")
    return root


def _project(source, target, registry, adapter_root=None):
    return project_harness(
        source,
        target,
        registry,
        skills=[],
        tool_ids=("claude",),
        governance_files=(),
        adapter_root=adapter_root,
    )


def test_a_masked_adapter_file_is_refused_by_name(source, target, registry):
    """The empty stub a governance mask leaves must not be projected.

    Copying it over the client's own `CLAUDE.md` and then trying to splice it
    is how this used to fail: the error named the target's file, which is the
    one file that is not at fault.
    """
    (source / "CLAUDE.md").write_text("")

    with pytest.raises(SkillsSyncError) as error:
        _project(source, target, registry)

    message = str(error.value)
    assert "claude" in message
    assert "ADR-0049" in message
    assert "CLAUDE.md" in message


def test_a_refused_projection_leaves_the_target_untouched(source, target, registry):
    """A half-projected harness is a silently different experiment."""
    (source / "CLAUDE.md").write_text("")

    with pytest.raises(SkillsSyncError):
        _project(source, target, registry)

    assert (target / "CLAUDE.md").read_text() == "# Their own rules, no sentinels\n"
    assert not (target / "CLAUDE.md.native.bak").exists()


def test_a_source_that_was_never_synced_says_to_sync_it(source, target, registry):
    """Missing and masked are different repairs, so they get different words."""
    with pytest.raises(SkillsSyncError, match="make sync-skills"):
        _project(source, target, registry)


def test_a_source_without_sentinels_is_refused_before_the_splice(
    source, target, registry
):
    (source / "CLAUDE.md").write_text("# Lab rules with no managed region\n")

    with pytest.raises(SkillsSyncError, match="sentinels"):
        _project(source, target, registry)


def test_the_adapter_root_supplies_what_the_mask_hides(
    source, target, registry, tmp_path
):
    """The whole point: masked lab root, real adapter bytes, one projection."""
    (source / "CLAUDE.md").write_text("")
    staged = tmp_path / "harness_source"
    staged.mkdir()
    (staged / "CLAUDE.md").write_text(ADAPTER)

    result = _project(source, target, registry, adapter_root=staged)

    projected = (target / "CLAUDE.md").read_text()
    assert projected.startswith("# Lab rules")
    assert "Skills: 0 internal." in projected
    assert result.collisions == ["CLAUDE.md"]
    # The client's own governance is displaced, never destroyed.
    assert (target / "CLAUDE.md.native.bak").read_text().startswith("# Their own")


def test_without_an_adapter_root_the_source_root_is_still_used(
    source, target, registry
):
    """An unmasked lane must behave exactly as it did before the parameter."""
    (source / "CLAUDE.md").write_text(ADAPTER)

    _project(source, target, registry)

    assert "Skills: 0 internal." in (target / "CLAUDE.md").read_text()
