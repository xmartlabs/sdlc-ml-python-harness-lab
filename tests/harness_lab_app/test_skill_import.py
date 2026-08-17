"""Guards on the one fetcher a browser URL can drive (ADR-0017).

These tests are the reason skill_import exists as its own module: each hostile
archive shape and each private address family gets an explicit refusal, so a
future refactor cannot quietly widen the surface.
"""

from __future__ import annotations

import io
import socket
import tarfile
import zipfile

import pytest

from harness_lab_app import skill_import
from harness_lab_app.skill_import import (
    ImportedSkill,
    SkillChoiceRequired,
    SkillImportError,
    build_skill,
    import_upload,
    list_archive_members,
    parse_frontmatter,
    parse_source,
    read_archive,
    read_directory,
    resolve_within,
    safe_relpath,
)

SKILL_MD = b"""---
name: context-shrink
description: Trims the working context.
---

# Context shrink
"""


def _tar(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, blob in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(blob)
            archive.addfile(info, io.BytesIO(blob))
    return buffer.getvalue()


def _zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, blob in members.items():
            archive.writestr(name, blob)
    return buffer.getvalue()


def _resolves_to(monkeypatch, address: str) -> None:
    """Pin DNS so the SSRF check is tested, not the network."""
    monkeypatch.setattr(
        skill_import.socket,
        "getaddrinfo",
        lambda *_a, **_k: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 443))
        ],
    )


# -- SSRF ---------------------------------------------------------------------


def test_http_is_refused():
    with pytest.raises(SkillImportError, match="Only https"):
        skill_import._assert_safe_url("http://example.com/SKILL.md")


@pytest.mark.parametrize(
    "scheme_url",
    ["file:///etc/passwd", "ftp://example.com/x", "gopher://example.com/x"],
)
def test_non_http_schemes_are_refused(scheme_url):
    with pytest.raises(SkillImportError, match="Only https"):
        skill_import._assert_safe_url(scheme_url)


# Not a bind address here — an input the SSRF guard has to refuse.
_UNSPECIFIED = "0.0.0.0"  # nosec B104


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",  # loopback
        "10.1.2.3",  # private
        "192.168.1.10",  # private (the LAN the lab itself sits on)
        "172.17.0.2",  # private (the docker bridge: redis, the gateway)
        "169.254.169.254",  # link-local: the cloud metadata endpoint
        _UNSPECIFIED,
        "224.0.0.1",  # multicast
    ],
)
def test_private_addresses_are_refused(monkeypatch, address):
    _resolves_to(monkeypatch, address)
    with pytest.raises(SkillImportError, match="non-public"):
        skill_import._assert_safe_url("https://evil.example.com/SKILL.md")


def test_public_address_is_allowed(monkeypatch):
    _resolves_to(monkeypatch, "140.82.121.4")
    skill_import._assert_safe_url("https://github.com/o/r")


def test_redirect_hops_are_re_checked(monkeypatch):
    """A public host redirecting to 127.0.0.1 is the classic SSRF bypass."""
    _resolves_to(monkeypatch, "127.0.0.1")
    handler = skill_import._CheckedRedirectHandler()
    with pytest.raises(SkillImportError, match="non-public"):
        handler.redirect_request(
            None, None, 302, "Found", {}, "https://evil.example.com/next"
        )


def test_redirects_are_capped():
    assert skill_import._CheckedRedirectHandler.max_redirections == 3


# -- path traversal -----------------------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "../../../etc/passwd",
        "/etc/passwd",
        "skill/../../escape.md",
        "C:\\windows\\system32\\evil",
        "..\\..\\evil",
        "with\x00nul",
        "",
    ],
)
def test_safe_relpath_rejects_traversal(hostile):
    assert safe_relpath(hostile) is None


def test_safe_relpath_does_not_judge_depth():
    """Depth is a resource cap, not a traversal signal.

    It used to be checked here, which meant one deep file anywhere in an archive
    made the whole thing 'unsafe' and refused the import — including imports of
    a shallow, unrelated skill beside it. build_skill caps depth instead.
    """
    deep = "/".join(["a"] * 20) + "/SKILL.md"
    assert safe_relpath(deep) == deep


def test_build_skill_skips_over_deep_files_instead_of_failing():
    deep = "repo/" + "/".join(["a"] * 10) + "/buried.txt"
    blob = _zip(
        {"repo/SKILL.md": SKILL_MD, "repo/scripts/run.sh": b"#!/bin/sh\n", deep: b"x"}
    )
    skill = build_skill(read_archive(blob, "x.zip"))
    assert set(skill.files) == {"SKILL.md", "scripts/run.sh"}
    assert any("nested deeper" in warning for warning in skill.warnings)


def test_a_deep_sibling_does_not_refuse_the_archive():
    """The exact shape that broke importing anthropics/skills.

    A file seven levels down inside *another* skill made the whole tarball
    unsafe, so even the canonical `/tree/main/skills/<name>` URL failed.
    """
    members = {
        "repo/skills/wanted/SKILL.md": SKILL_MD,
        "repo/skills/other/scripts/a/b/c/d/deep.py": b"x",
    }
    files = read_archive(_zip(members), "x.zip", prefix="repo/skills/wanted")
    assert set(files) == {"repo/skills/wanted/SKILL.md"}


def test_safe_relpath_normalizes_the_benign_case():
    assert safe_relpath("./scripts/run.sh") == "scripts/run.sh"


def test_zip_slip_is_refused():
    blob = _zip({"skill/SKILL.md": SKILL_MD, "../../../tmp/pwned": b"x"})
    with pytest.raises(SkillImportError, match="unsafe member path"):
        read_archive(blob, "skill.zip")


def test_tar_slip_is_refused():
    blob = _tar({"skill/SKILL.md": SKILL_MD, "../../etc/cron.d/pwn": b"x"})
    with pytest.raises(SkillImportError, match="unsafe member path"):
        read_archive(blob, "skill.tar.gz")


def test_tar_symlink_member_is_refused():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("skill/SKILL.md")
        info.size = len(SKILL_MD)
        archive.addfile(info, io.BytesIO(SKILL_MD))
        link = tarfile.TarInfo("skill/passwd")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        archive.addfile(link)
    with pytest.raises(SkillImportError, match="not a regular file"):
        read_archive(buffer.getvalue(), "skill.tar.gz")


def test_zip_symlink_member_is_refused():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("skill/SKILL.md", SKILL_MD)
        info = zipfile.ZipInfo("skill/link")
        info.external_attr = (0o120777 << 16) | 0o120000
        archive.writestr(info, "/etc/passwd")
    with pytest.raises(SkillImportError, match="symlink"):
        read_archive(buffer.getvalue(), "skill.zip")


def test_resolve_within_rejects_escapes(tmp_path):
    with pytest.raises(SkillImportError, match="outside the skill root"):
        resolve_within(tmp_path, "../escape")


def test_resolve_within_allows_nested(tmp_path):
    assert resolve_within(tmp_path, "scripts/run.sh").name == "run.sh"


# -- caps ---------------------------------------------------------------------


def test_file_count_cap(monkeypatch):
    monkeypatch.setattr(skill_import, "MAX_FILES", 3)
    members = {"skill/SKILL.md": SKILL_MD}
    members.update({f"skill/f{i}.txt": b"x" for i in range(10)})
    with pytest.raises(SkillImportError, match="more than 3 files"):
        read_archive(_zip(members), "skill.zip")


def test_per_file_cap(monkeypatch):
    monkeypatch.setattr(skill_import, "MAX_FILE_BYTES", 1024)
    blob = _zip({"skill/SKILL.md": SKILL_MD, "skill/big.bin": b"x" * 4096})
    with pytest.raises(SkillImportError, match="per-file limit"):
        read_archive(blob, "skill.zip")


def test_total_size_cap(monkeypatch):
    monkeypatch.setattr(skill_import, "MAX_TOTAL_BYTES", 2048)
    members = {"skill/SKILL.md": SKILL_MD}
    members.update({f"skill/f{i}.bin": b"x" * 900 for i in range(5)})
    with pytest.raises(SkillImportError, match="expands past"):
        read_archive(_zip(members), "skill.zip")


def test_read_capped_refuses_instead_of_truncating():
    with pytest.raises(SkillImportError, match="exceeds"):
        skill_import._read_capped(io.BytesIO(b"x" * 5000), 1000, "Download")


# -- tree shaping -------------------------------------------------------------


def test_frontmatter_parsing():
    fields = parse_frontmatter(SKILL_MD.decode())
    assert fields["name"] == "context-shrink"
    assert fields["description"] == "Trims the working context."


def test_frontmatter_absent_is_not_an_error():
    assert parse_frontmatter("# Just a heading\n") == {}


def test_build_skill_reroots_and_reads_metadata():
    blob = _zip(
        {
            "context-shrink-main/SKILL.md": SKILL_MD,
            "context-shrink-main/scripts/run.sh": b"#!/bin/sh\n",
            "context-shrink-main/references/notes.md": b"notes\n",
        }
    )
    skill = build_skill(read_archive(blob, "x.zip"))
    assert skill.name == "context-shrink"
    assert skill.description == "Trims the working context."
    assert set(skill.files) == {"SKILL.md", "scripts/run.sh", "references/notes.md"}
    assert any("supporting file" in w for w in skill.warnings)


def test_build_skill_picks_the_shallowest_skill_md():
    """A repo vendoring other skills must not import an arbitrary nested one."""
    blob = _zip(
        {
            "repo/SKILL.md": SKILL_MD,
            "repo/vendor/other/SKILL.md": b"---\nname: other\n---\n",
        }
    )
    skill = build_skill(read_archive(blob, "x.zip"))
    assert skill.name == "context-shrink"
    assert "vendor/other/SKILL.md" in skill.files


CATALOG = {
    "repo/skills/alpha/SKILL.md": b"---\nname: alpha\ndescription: First.\n---\n",
    "repo/skills/alpha/scripts/run.sh": b"#!/bin/sh\n",
    "repo/skills/beta/SKILL.md": b"---\nname: beta\ndescription: Second.\n---\n",
    "repo/README.md": b"# catalog\n",
}


def test_a_catalog_of_sibling_skills_asks_which_one():
    """anthropics/skills has 18 — importing the first alphabetically is a guess."""
    with pytest.raises(SkillChoiceRequired) as excinfo:
        build_skill(read_archive(_zip(CATALOG), "x.zip"))
    names = [candidate.name for candidate in excinfo.value.candidates]
    assert names == ["alpha", "beta"]
    assert excinfo.value.candidates[0].description == "First."
    assert excinfo.value.candidates[0].path == "repo/skills/alpha"


def test_a_selector_picks_one_skill_out_of_the_catalog():
    skill = build_skill(read_archive(_zip(CATALOG), "x.zip"), selector="beta")
    assert skill.name == "beta"
    assert set(skill.files) == {"SKILL.md"}


def test_an_unknown_selector_lists_what_is_available():
    with pytest.raises(SkillImportError, match="Available: alpha, beta"):
        build_skill(read_archive(_zip(CATALOG), "x.zip"), selector="gamma")


def test_scoping_the_read_keeps_the_caps_on_the_skill_not_the_repo(monkeypatch):
    """A catalog is hundreds of files; the skill inside it is two."""
    monkeypatch.setattr(skill_import, "MAX_FILES", 5)
    members = {f"repo/noise/f{i}.txt": b"x" for i in range(50)}
    members.update(
        {"repo/skills/alpha/SKILL.md": SKILL_MD, "repo/skills/alpha/notes.md": b"n"}
    )
    blob = _zip(members)
    with pytest.raises(SkillImportError, match="more than 5 files"):
        read_archive(blob, "x.zip")
    scoped = read_archive(blob, "x.zip", prefix="repo/skills/alpha")
    assert len(scoped) == 2


def test_listing_members_reads_no_payload():
    names = list_archive_members(_zip(CATALOG), "x.zip")
    assert "repo/skills/beta/SKILL.md" in names
    assert "repo/README.md" in names


def test_build_skill_without_skill_md_fails():
    with pytest.raises(SkillImportError, match=r"No SKILL\.md"):
        build_skill({"readme.md": b"hi"})


def test_build_skill_rejects_an_underivable_name():
    with pytest.raises(SkillImportError, match="valid skill name"):
        build_skill({"SKILL.md": b"# no frontmatter\n"})


def test_junk_directories_are_skipped():
    blob = _zip(
        {
            "repo/SKILL.md": SKILL_MD,
            "repo/.git/config": b"[core]\n",
            "repo/node_modules/pkg/index.js": b"//\n",
        }
    )
    assert set(build_skill(read_archive(blob, "x.zip")).files) == {"SKILL.md"}


def test_read_directory_skips_symlinks(tmp_path):
    (tmp_path / "SKILL.md").write_bytes(SKILL_MD)
    (tmp_path / "evil").symlink_to("/etc/passwd")
    assert set(read_directory(tmp_path)) == {"SKILL.md"}


# -- source normalization -----------------------------------------------------

_GH = "https://github.com/anthropics/skills"


@pytest.mark.parametrize(
    ("pasted", "expected_url", "expected_selector"),
    [
        # What registries actually hand you to copy.
        (
            "npx skills add https://github.com/anthropics/skills --skill frontend-design",
            _GH,
            "frontend-design",
        ),
        ("npx skills add anthropics/skills --skill=mcp-builder", _GH, "mcp-builder"),
        ("pnpm dlx @skills/cli add anthropics/skills", _GH, ""),
        ("bunx skills add anthropics/skills --skill pdf", _GH, "pdf"),
        # Registry pages.
        (
            "https://www.skills.sh/anthropics/skills/frontend-design",
            _GH,
            "frontend-design",
        ),
        ("https://skills.sh/anthropics/skills", _GH, ""),
        ("skills.sh/anthropics/skills/pdf", _GH, "pdf"),
        # Shorthands and clone commands.
        ("anthropics/skills", _GH, ""),
        ("anthropics/skills/webapp-testing", _GH, "webapp-testing"),
        ("git clone https://github.com/anthropics/skills.git", f"{_GH}.git", ""),
        ("git@github.com:anthropics/skills.git", f"{_GH}.git", ""),
        ("gh repo clone anthropics/skills", _GH, ""),
        # Plain URLs still pass through untouched.
        (f"{_GH}/tree/main/skills/pdf", f"{_GH}/tree/main/skills/pdf", ""),
    ],
)
def test_parse_source_normalizes_what_people_paste(
    pasted, expected_url, expected_selector
):
    spec = parse_source(pasted)
    assert spec.url == expected_url
    assert spec.selector == expected_selector


def test_parse_source_reads_a_ref_flag():
    spec = parse_source("git clone --branch v2 https://github.com/o/r.git")
    assert spec.ref == "v2"


def test_an_explicit_argument_wins_over_the_pasted_command():
    spec = parse_source("npx skills add anthropics/skills --skill pdf", selector="docx")
    assert spec.selector == "docx"


@pytest.mark.parametrize("pasted", ["", "   ", "install my skill please"])
def test_parse_source_refuses_what_carries_no_source(pasted):
    with pytest.raises(SkillImportError):
        parse_source(pasted)


@pytest.mark.parametrize(
    "pasted",
    [
        "https://caveman.so/",
        "https://raw.githubusercontent.com/JuliusBrussee/caveman/main/install.sh",
    ],
)
def test_home_pages_and_installers_are_refused_without_fetching(monkeypatch, pasted):
    """These are plugin installers, not skills — and we must not even fetch them."""
    monkeypatch.setattr(
        skill_import,
        "_fetch",
        lambda *_a, **_k: pytest.fail("refused sources must not be fetched"),
    )
    with pytest.raises(SkillImportError, match="Unsupported source"):
        skill_import.fetch_skill(pasted)


# -- uploads ------------------------------------------------------------------


def test_upload_of_a_bare_skill_md():
    skill = import_upload(SKILL_MD, "SKILL.md")
    assert skill.source_kind == "upload"
    assert skill.files == {"SKILL.md": SKILL_MD}


def test_upload_of_an_archive():
    blob = _tar({"context-shrink/SKILL.md": SKILL_MD})
    skill = import_upload(blob, "context-shrink.tar.gz")
    assert skill.name == "context-shrink"


def test_upload_of_an_unsupported_type():
    with pytest.raises(SkillImportError, match="Upload a"):
        import_upload(b"whatever", "skill.exe")


def test_imported_skill_reports_its_size():
    skill = ImportedSkill(name="x", description="", files={"a": b"12345"})
    assert skill.total_bytes == 5
