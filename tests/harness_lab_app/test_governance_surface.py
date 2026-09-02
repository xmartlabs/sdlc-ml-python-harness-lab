"""The governance surface a run was judged against (ADR-0031).

An auditor that reads the lab's own rulebook while judging a `native` run is
measuring the lab, not the run. These tests pin the resolution rule so that
never becomes true again by accident.
"""

from __future__ import annotations

from harness_lab_app.governance import (
    DEFAULT_RULES_FILE,
    MAX_FILES,
    collect_files,
    fingerprint_files,
    reading_surface,
    resolve_surface,
    rules_file_for,
)


def make_lab(tmp_path):
    lab = tmp_path / "lab"
    (lab / ".github").mkdir(parents=True)
    (lab / ".github" / "standards.md").write_text("lab standards\n")
    (lab / "CLAUDE.md").write_text("lab rules\n")
    return lab


def make_workspace(run_dir, files):
    for relative, body in files.items():
        path = run_dir / "workspace" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    return run_dir


def test_rules_file_per_harness():
    assert rules_file_for("claude") == ("CLAUDE.md", "")
    assert rules_file_for("codex") == ("AGENTS.md", "")
    assert rules_file_for("opencode") == ("OPENCODE.md", "")


def test_unknown_harness_defaults_and_says_so():
    name, warning = rules_file_for("something-else")
    assert name == DEFAULT_RULES_FILE
    # Silence here would make a wrong rulebook look like the right one.
    assert "no mapped rules file" in warning
    assert rules_file_for(None)[1]


def test_native_reads_the_target_workspace(tmp_path):
    run_dir = tmp_path / "runs" / "r1"
    make_workspace(run_dir, {"AGENTS.md": "target\n", "CLAUDE.md": "target claude\n"})

    surface = resolve_surface(
        lab_root=make_lab(tmp_path),
        run_dir=run_dir,
        harness="claude",
        harness_source="native",
    )

    assert surface.source == "target_workspace"
    assert surface.rules_file == "CLAUDE.md"
    assert surface.files == ["AGENTS.md", "CLAUDE.md"]
    assert ".github/standards.md" not in surface.files
    assert surface.warning == ""


def test_injected_reads_the_projected_workspace(tmp_path):
    run_dir = tmp_path / "runs" / "r2"
    make_workspace(
        run_dir,
        {"AGENTS.md": "target\n", ".github/standards.md": "projected standards\n"},
    )

    surface = resolve_surface(
        lab_root=make_lab(tmp_path),
        run_dir=run_dir,
        harness="codex",
        harness_source="injected",
    )

    assert surface.source == "target_workspace"
    assert surface.harness_source == "injected"
    assert surface.files == [".github/standards.md", "AGENTS.md"]


def test_lab_root_reads_the_labs_repository(tmp_path):
    surface = resolve_surface(
        lab_root=make_lab(tmp_path),
        run_dir=tmp_path / "runs" / "r3",
        harness="claude",
        harness_source="lab_root",
    )

    assert surface.source == "lab_root"
    assert surface.files == [".github/standards.md", "CLAUDE.md"]


def test_missing_workspace_falls_back_but_warns(tmp_path):
    """A degraded record, and it has to look like one."""
    surface = resolve_surface(
        lab_root=make_lab(tmp_path),
        run_dir=tmp_path / "runs" / "gone",
        harness="claude",
        harness_source="native",
    )

    assert surface.source == "lab_root"
    # The condition is preserved, so a reader can see the disagreement.
    assert surface.harness_source == "native"
    assert "archived" in surface.warning


def test_fingerprint_tracks_content_not_just_paths(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "CLAUDE.md").write_text("v1\n")
    first = fingerprint_files(root, ["CLAUDE.md"])
    (root / "CLAUDE.md").write_text("v2\n")
    second = fingerprint_files(root, ["CLAUDE.md"])

    assert first != second
    # Same content, same hash — two runs of one condition must agree.
    (root / "CLAUDE.md").write_text("v1\n")
    assert fingerprint_files(root, ["CLAUDE.md"]) == first


def test_fingerprint_of_an_empty_surface_is_stable(tmp_path):
    assert fingerprint_files(tmp_path, []) == fingerprint_files(tmp_path, [])


def test_collect_files_truncates_loudly(tmp_path):
    root = tmp_path / "big"
    (root / ".github").mkdir(parents=True)
    for index in range(MAX_FILES + 5):
        (root / ".github" / f"doc-{index:04d}.md").write_text("x")

    files, truncated = collect_files(root)

    assert truncated is True
    assert len(files) == MAX_FILES


def test_reading_surface_drops_the_on_demand_catalogue(tmp_path):
    root = tmp_path / "repo"
    (root / ".claude" / "skills" / "writing-plans").mkdir(parents=True)
    (root / ".claude" / "skills" / "writing-plans" / "SKILL.md").write_text("s\n")
    (root / "docs" / "adr").mkdir(parents=True)
    (root / "docs" / "adr" / "0001-x.md").write_text("adr\n")
    (root / "memory").mkdir(parents=True)
    (root / "memory" / "context.md").write_text("ctx\n")
    (root / "CLAUDE.md").write_text("rules\n")

    surface = resolve_surface(
        lab_root=root,
        run_dir=tmp_path / "runs" / "none",
        harness="claude",
        harness_source="lab_root",
    )

    # The full surface records everything the run could reach…
    assert ".claude/skills/writing-plans/SKILL.md" in surface.files
    assert "docs/adr/0001-x.md" in surface.files
    # …but read coverage is a ratio over the standing rulebook only. A skill
    # nobody needed is not an unread document.
    assert reading_surface(surface) == ("CLAUDE.md", "memory/context.md")
