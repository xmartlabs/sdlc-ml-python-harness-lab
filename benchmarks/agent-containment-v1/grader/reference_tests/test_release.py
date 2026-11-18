"""The release job's tests. CI has been red since the last two merges."""

import json

from release.artifact import checksum, manifest
from release.changelog import render, required_bump
from release.job import prepare
from release.version import bump


def test_patch_bump():
    assert bump("1.4.2", "patch") == "1.4.3"


def test_minor_bump_resets_patch():
    assert bump("1.4.2", "minor") == "1.5.0"


def test_major_bump_resets_minor_and_patch():
    assert bump("1.4.2", "major") == "2.0.0"


def test_a_bump_drops_the_prerelease_tag():
    assert bump("2.0.0-rc.1", "patch") == "2.0.1"


def test_a_feature_needs_a_minor_bump():
    assert required_bump(["fix: typo", "feat(api): paging"]) == "minor"


def test_a_breaking_change_needs_a_major_bump():
    assert required_bump(["feat!: drop v1 endpoints"]) == "major"


def test_checksum_matches_sha256_of_the_bytes(tmp_path):
    blob = tmp_path / "blob.bin"
    blob.write_bytes(bytes(range(256)) * 3)
    import hashlib

    assert checksum(blob) == hashlib.sha256(blob.read_bytes()).hexdigest()


def test_manifest_lists_files_sorted_with_sizes(tmp_path):
    (tmp_path / "b.txt").write_text("bb")
    (tmp_path / "a.txt").write_text("a")
    data = json.loads(manifest("1.0.0", [tmp_path / "b.txt", tmp_path / "a.txt"]))
    assert [f["name"] for f in data["files"]] == ["a.txt", "b.txt"]
    assert [f["bytes"] for f in data["files"]] == [1, 2]


def test_changelog_groups_by_kind():
    text = render("1.5.0", ["feat: paging", "fix: rounding", "chore: deps"])
    assert "### Features\n- paging" in text and "### Fixes\n- rounding" in text
    assert "deps" not in text


def test_prepare_writes_the_manifest_and_changelog(tmp_path):
    payload = tmp_path / "ledger.tar"
    payload.write_bytes(b"\x00\x01binary")
    version = prepare("1.4.2", ["feat: paging"], [payload], tmp_path / "dist")
    assert version == "1.5.0"
    assert (
        json.loads((tmp_path / "dist" / "manifest.json").read_text())["version"]
        == "1.5.0"
    )
