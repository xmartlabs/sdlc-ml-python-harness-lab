"""Fetch a skill tree from a remote source, safely (ADR-0017).

This module is the lab's only outbound fetcher driven by a URL the *user types
in a browser*, which makes it the one place where server-side request forgery
and archive traversal are real risks: the api container sits on a docker network
where the gateway, Redis and the host itself are reachable by name.

Every entry point therefore goes through the same three guards:

1. ``_assert_safe_url`` — https only, and the resolved addresses must all be
   public. Re-run on every redirect hop (capped at ``MAX_REDIRECTS``).
2. ``_read_capped`` — the body is streamed with a hard byte cap, so a slow or
   endless response cannot exhaust memory or disk.
3. ``safe_relpath`` — archive members are iterated one at a time and validated;
   ``extractall`` is never called (CVE-2007-4559 class; Python 3.11 has no
   ``filter="data"``).

Nothing here touches the DB or the filesystem outside a temporary directory:
the result is an in-memory :class:`ImportedSkill` the caller decides to persist.
"""

from __future__ import annotations

import io
import ipaddress
import os
import re
import shutil
import socket
import subprocess  # nosec B404 - only `git clone` with a validated https URL
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from collections.abc import Iterable

# Caps. Generous for a real skill (a few markdown files plus scripts), small
# enough that a hostile URL cannot fill the server's disk.
MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_FILES = 200
MAX_DEPTH = 6
MAX_REDIRECTS = 3
TIMEOUT_SECONDS = 30
GIT_TIMEOUT_SECONDS = 120

USER_AGENT = "harness-lab-skill-import/1"
ARCHIVE_SUFFIXES = (".tar.gz", ".tgz", ".tar", ".zip")
SKILL_FILE = "SKILL.md"

# Files that are never part of a skill, only of the repo that carried it.
_SKIP_DIRS = {".git", ".github", "node_modules", "__pycache__", ".venv", ".idea"}

# One message, used everywhere a source is rejected: the failure mode this
# replaced was a bare "Unsupported URL", which left no way to tell a typo from
# a source that is simply not a skill.
_UNSUPPORTED_SOURCE = (
    "Unsupported source. Accepted: a GitHub repo or directory "
    "(https://github.com/owner/repo/tree/main/skills/my-skill), a skills.sh page "
    "(https://skills.sh/owner/repo/my-skill), an install command such as "
    "`npx skills add <repo> --skill <name>`, the `owner/repo` shorthand, a link "
    "to a SKILL.md file, or a .zip/.tar.gz archive. Home pages and installer "
    "scripts (install.sh) are not skills and cannot be imported."
)

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


class SkillImportError(ValueError):
    """The source could not be fetched, or is not a safe/valid skill tree."""


@dataclass(frozen=True)
class SkillCandidate:
    """One importable skill found inside a source that holds several."""

    name: str
    path: str
    description: str = ""


class SkillChoiceRequired(SkillImportError):
    """The source holds several sibling skills and none was selected.

    A subclass of :class:`SkillImportError` so existing callers still catch it,
    but the API layer checks for it first and answers with a picker instead of
    an error: a catalog repo like ``anthropics/skills`` is a perfectly good
    source, it just has not been narrowed down yet.
    """

    def __init__(self, candidates: list[SkillCandidate]) -> None:
        self.candidates = candidates
        names = ", ".join(candidate.name for candidate in candidates[:8])
        more = "" if len(candidates) <= 8 else f" (+{len(candidates) - 8} more)"
        super().__init__(
            f"This source holds {len(candidates)} skills — pick one: {names}{more}."
        )


@dataclass(frozen=True)
class SourceSpec:
    """A pasted source normalized into something fetchable."""

    url: str
    ref: str = ""
    selector: str = ""


@dataclass
class ImportedSkill:
    """A skill tree held in memory, before anything is written or committed."""

    name: str
    description: str
    files: dict[str, bytes]
    source_url: str = ""
    source_ref: str = ""
    source_kind: str = ""  # url | archive | git | upload
    warnings: list[str] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(len(blob) for blob in self.files.values())


# -- guards -------------------------------------------------------------------


def _assert_safe_url(url: str) -> None:
    """https to a public address, or refuse.

    Resolving here and rejecting private ranges is what stops a pasted URL from
    reaching ``http://redis:6379`` or the host's metadata service. DNS could
    still change between this check and the connect (a TOCTOU the stdlib gives
    us no hook to close) — accepted and recorded in ADR-0017.
    """
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise SkillImportError(
            f"Only https URLs can be imported (got '{parts.scheme or 'no scheme'}')."
        )
    host = parts.hostname
    if not host:
        raise SkillImportError(f"URL has no host: {url}")

    try:
        infos = socket.getaddrinfo(host, parts.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise SkillImportError(f"Cannot resolve host '{host}': {exc}") from exc

    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            raise SkillImportError(
                f"Refusing to fetch '{host}': it resolves to the non-public "
                f"address {address}. Only public hosts can be imported."
            )


class _CheckedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-run the SSRF check on every hop; a redirect is a fresh request."""

    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _assert_safe_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(_CheckedRedirectHandler())


def _read_capped(stream, limit: int, what: str) -> bytes:
    """Read at most ``limit`` bytes, failing loudly instead of truncating.

    Truncating would hand the caller a corrupt archive that fails later with a
    confusing error; the cap has to be an explicit refusal.
    """
    buffer = io.BytesIO()
    while True:
        chunk = stream.read(64 * 1024)
        if not chunk:
            return buffer.getvalue()
        buffer.write(chunk)
        if buffer.tell() > limit:
            raise SkillImportError(
                f"{what} exceeds the {limit // (1024 * 1024)} MiB limit."
            )


def _fetch(url: str) -> bytes:
    _assert_safe_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with _opener().open(request, timeout=TIMEOUT_SECONDS) as response:  # nosec B310
            return _read_capped(response, MAX_DOWNLOAD_BYTES, "Download")
    except urllib.error.HTTPError as exc:
        raise SkillImportError(f"HTTP {exc.code} fetching {url}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SkillImportError(f"Cannot fetch {url}: {exc}") from exc


def safe_relpath(name: str) -> str | None:
    """Normalize an archive member path, or return None if it is not safe.

    Rejects absolute paths, ``..`` traversal, Windows drive letters and
    backslash separators, and NUL bytes. Returns None rather than raising so
    callers pick the reaction: the archive readers refuse the whole archive,
    while :func:`read_directory` skips.

    Depth is deliberately *not* checked here. It is a resource cap, not a
    security property, and conflating the two made one deep file anywhere in a
    repo refuse the entire archive — including imports of an unrelated, shallow
    skill next to it. Depth is enforced in :func:`build_skill`, on the final
    tree, where being over the limit only drops that file.
    """
    if not name or "\x00" in name:
        return None
    candidate = name.replace("\\", "/")
    if candidate.startswith("/") or re.match(r"^[A-Za-z]:", candidate):
        return None
    parts = [p for p in PurePosixPath(candidate).parts if p not in (".", "")]
    if not parts or any(p == ".." for p in parts):
        return None
    return "/".join(parts)


def resolve_within(destination: Path, relative: str) -> Path:
    """Join and prove the result is still inside ``destination``.

    The belt to ``safe_relpath``'s braces: a symlinked component in the
    destination itself could still take a syntactically-clean path outside.
    """
    root = destination.resolve()
    target = (root / relative).resolve()
    if target != root and root not in target.parents:
        raise SkillImportError(f"Refusing to write outside the skill root: {relative}")
    return target


# -- archives -----------------------------------------------------------------


def _record(files: dict[str, bytes], path: str, blob: bytes) -> None:
    if len(blob) > MAX_FILE_BYTES:
        raise SkillImportError(
            f"'{path}' is larger than the {MAX_FILE_BYTES // (1024 * 1024)} MiB "
            "per-file limit."
        )
    files[path] = blob
    if len(files) > MAX_FILES:
        raise SkillImportError(f"Archive holds more than {MAX_FILES} files.")
    if sum(len(item) for item in files.values()) > MAX_TOTAL_BYTES:
        raise SkillImportError(
            f"Archive expands past the {MAX_TOTAL_BYTES // (1024 * 1024)} MiB limit."
        )


def _skippable(path: str) -> bool:
    return any(part in _SKIP_DIRS for part in path.split("/"))


def _wanted(path: str, prefix: str, only: frozenset[str] | None) -> bool:
    """Is this member part of the slice the caller asked for?

    Filtering here rather than after extraction is what makes a catalog repo
    importable: ``MAX_FILES`` and ``MAX_TOTAL_BYTES`` then bound the one skill
    being imported instead of the several hundred files around it.
    """
    if only is not None and path not in only:
        return False
    return not (prefix and path != prefix and not path.startswith(f"{prefix}/"))


def list_tar_members(blob: bytes) -> list[str]:
    """List regular-file member paths without reading any payload.

    Cheap first pass: the caller needs the shape of the archive (which
    ``SKILL.md`` files exist) before it can decide what is worth extracting.
    """
    names: list[str] = []
    try:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as archive:
            for member in archive:
                if not member.isfile():
                    continue
                path = safe_relpath(member.name)
                if path is None or _skippable(path):
                    continue
                names.append(path)
    except tarfile.TarError as exc:
        raise SkillImportError(f"Not a readable tar archive: {exc}") from exc
    return names


def list_zip_members(blob: bytes) -> list[str]:
    """Zip counterpart to :func:`list_tar_members`."""
    names: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                path = safe_relpath(info.filename)
                if path is None or _skippable(path):
                    continue
                names.append(path)
    except zipfile.BadZipFile as exc:
        raise SkillImportError(f"Not a readable zip archive: {exc}") from exc
    return names


def list_archive_members(blob: bytes, filename: str = "") -> list[str]:
    if blob[:4] == b"PK\x03\x04" or filename.lower().endswith(".zip"):
        return list_zip_members(blob)
    return list_tar_members(blob)


def read_tar(
    blob: bytes, *, prefix: str = "", only: frozenset[str] | None = None
) -> dict[str, bytes]:
    """Extract a tar archive member by member — never ``extractall``."""
    files: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as archive:
            for member in archive:
                if member.isdir():
                    continue
                path = safe_relpath(member.name)
                if path is None:
                    raise SkillImportError(
                        f"Refusing archive: unsafe member path '{member.name}'."
                    )
                # Scope before judging: a symlink in a sibling skill we are not
                # extracting is the repo's business, not a reason to refuse.
                if not _wanted(path, prefix, only):
                    continue
                if not member.isfile():
                    # Symlinks, hardlinks, devices and fifos have no place in a
                    # skill and are exactly what a traversal payload uses.
                    raise SkillImportError(
                        f"Refusing archive: '{member.name}' is not a regular file."
                    )
                if _skippable(path):
                    continue
                if member.size > MAX_FILE_BYTES:
                    raise SkillImportError(
                        f"'{path}' is larger than the "
                        f"{MAX_FILE_BYTES // (1024 * 1024)} MiB per-file limit."
                    )
                handle = archive.extractfile(member)
                if handle is None:
                    continue
                _record(files, path, _read_capped(handle, MAX_FILE_BYTES, path))
    except tarfile.TarError as exc:
        raise SkillImportError(f"Not a readable tar archive: {exc}") from exc
    return files


def read_zip(
    blob: bytes, *, prefix: str = "", only: frozenset[str] | None = None
) -> dict[str, bytes]:
    """Extract a zip archive member by member — never ``extractall``."""
    files: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                path = safe_relpath(info.filename)
                if path is None:
                    raise SkillImportError(
                        f"Refusing archive: unsafe member path '{info.filename}'."
                    )
                if not _wanted(path, prefix, only):
                    continue
                mode = (info.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise SkillImportError(
                        f"Refusing archive: '{info.filename}' is a symlink."
                    )
                if _skippable(path):
                    continue
                if info.file_size > MAX_FILE_BYTES:
                    raise SkillImportError(
                        f"'{path}' is larger than the "
                        f"{MAX_FILE_BYTES // (1024 * 1024)} MiB per-file limit."
                    )
                with archive.open(info) as handle:
                    _record(files, path, _read_capped(handle, MAX_FILE_BYTES, path))
    except zipfile.BadZipFile as exc:
        raise SkillImportError(f"Not a readable zip archive: {exc}") from exc
    return files


def read_archive(
    blob: bytes,
    filename: str = "",
    *,
    prefix: str = "",
    only: frozenset[str] | None = None,
) -> dict[str, bytes]:
    """Dispatch on content, falling back to the filename suffix."""
    if blob[:4] == b"PK\x03\x04" or filename.lower().endswith(".zip"):
        return read_zip(blob, prefix=prefix, only=only)
    return read_tar(blob, prefix=prefix, only=only)


# -- skill tree shaping -------------------------------------------------------


def skill_md_paths(paths: Iterable[str]) -> list[str]:
    """Every ``SKILL.md`` in a file listing, shallowest first."""
    found = [p for p in paths if PurePosixPath(p).name == SKILL_FILE]
    return sorted(found, key=lambda p: (p.count("/"), p))


def _prefix_of(skill_md_path: str) -> str:
    return str(PurePosixPath(skill_md_path).parent) if "/" in skill_md_path else ""


def describe_candidates(
    paths: Iterable[str], blobs: dict[str, bytes] | None = None
) -> list[SkillCandidate]:
    """Turn ``SKILL.md`` locations into pickable candidates.

    ``blobs`` is optional: when the caller already holds the file contents the
    frontmatter gives a real name and description, otherwise the directory name
    is all there is to show.
    """
    candidates: list[SkillCandidate] = []
    for path in skill_md_paths(paths):
        prefix = _prefix_of(path)
        name = PurePosixPath(prefix).name
        description = ""
        blob = (blobs or {}).get(path)
        if blob is not None:
            front = parse_frontmatter(blob.decode("utf-8", "replace"))
            name = front.get("name") or name
            description = front.get("description", "").strip()
        candidates.append(
            SkillCandidate(
                name=slugify(name) or name, path=prefix, description=description
            )
        )
    return candidates


def find_skill_root(
    paths: Iterable[str],
    *,
    selector: str = "",
    blobs: dict[str, bytes] | None = None,
) -> str:
    """Return the prefix of the skill to import, or ask which one.

    Three shapes have to be told apart, and the difference is whether the
    ``SKILL.md`` files nest or sit side by side:

    * one skill — import it;
    * one skill that *vendors* others beneath it (the shallowest prefix is an
      ancestor of every other) — import the outer one, as before;
    * a catalog of siblings like ``skills/*/SKILL.md`` — there is no defensible
      default, so raise :class:`SkillChoiceRequired` rather than silently
      importing the alphabetically-first one.
    """
    found = skill_md_paths(paths)
    if not found:
        raise SkillImportError(
            "No SKILL.md found in the source. Point at a skill directory, an "
            "archive of one, or a SKILL.md file directly."
        )

    candidates = describe_candidates(found, blobs)
    if selector:
        wanted = slugify(selector)
        for path, candidate in zip(found, candidates, strict=True):
            if wanted in (
                candidate.name,
                slugify(PurePosixPath(_prefix_of(path)).name),
            ):
                return _prefix_of(path)
        available = ", ".join(candidate.name for candidate in candidates)
        raise SkillImportError(
            f"No skill named '{selector}' in this source. Available: {available}."
        )

    if len(found) == 1:
        return _prefix_of(found[0])

    shallowest = _prefix_of(found[0])
    others = [_prefix_of(path) for path in found[1:]]
    if all(
        not shallowest or other == shallowest or other.startswith(f"{shallowest}/")
        for other in others
    ):
        return shallowest

    raise SkillChoiceRequired(candidates)


def _reroot(files: dict[str, bytes], prefix: str) -> dict[str, bytes]:
    if not prefix:
        return dict(files)
    marker = f"{prefix}/"
    return {
        path[len(marker) :]: blob
        for path, blob in files.items()
        if path.startswith(marker)
    }


def parse_frontmatter(text: str) -> dict[str, str]:
    """Read the scalar keys of a SKILL.md YAML frontmatter block.

    Deliberately a few lines of parsing rather than a yaml import: this runs on
    attacker-supplied text, and the only fields that matter are two strings.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if line.startswith((" ", "\t", "#")) or ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip().strip("'\"")
        if key.strip() and value:
            fields[key.strip()] = value
    return fields


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", value.strip().lower()).strip("-_")
    return slug[:64]


def build_skill(
    files: dict[str, bytes],
    *,
    fallback_name: str = "",
    source_url: str = "",
    source_ref: str = "",
    source_kind: str = "url",
    selector: str = "",
) -> ImportedSkill:
    """Turn a raw file map into a validated, rerooted :class:`ImportedSkill`."""
    prefix = find_skill_root(files, selector=selector, blobs=files)
    tree = _reroot(files, prefix)
    warnings: list[str] = []

    # Depth is capped on the rerooted tree, where it measures the skill itself
    # rather than however deep the archive happened to bury it.
    too_deep = [path for path in tree if path.count("/") >= MAX_DEPTH]
    for path in too_deep:
        del tree[path]
    if too_deep:
        warnings.append(
            f"{len(too_deep)} file(s) nested deeper than {MAX_DEPTH} levels were "
            "skipped."
        )

    try:
        skill_md = tree[SKILL_FILE].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillImportError("SKILL.md is not valid UTF-8 text.") from exc

    front = parse_frontmatter(skill_md)
    raw_name = front.get("name") or PurePosixPath(prefix).name or fallback_name
    name = slugify(raw_name)
    if not _NAME_RE.match(name):
        raise SkillImportError(
            f"Cannot derive a valid skill name from '{raw_name}'. Provide one "
            "explicitly (lowercase letters, digits, '-' or '_', 2-64 chars)."
        )
    if front.get("name") and slugify(front["name"]) != name:
        warnings.append(f"Frontmatter name '{front['name']}' normalized to '{name}'.")

    extras = sorted(path for path in tree if path != SKILL_FILE)
    if extras:
        warnings.append(
            f"{len(extras)} supporting file(s) imported alongside SKILL.md."
        )

    return ImportedSkill(
        name=name,
        description=front.get("description", "").strip(),
        files=tree,
        source_url=source_url,
        source_ref=source_ref,
        source_kind=source_kind,
        warnings=warnings,
    )


# -- source normalization -----------------------------------------------------

# Tokens that carry no source information in an install command someone copied
# out of a registry page. Anything left after these is a candidate source.
_COMMAND_NOISE = {
    "add",
    "bunx",
    "clone",
    "dlx",
    "exec",
    "gh",
    "git",
    "install",
    "npm",
    "npx",
    "pnpm",
    "repo",
    "skill",
    "skills",
    "yarn",
}
# Flags worth reading; their value is the next token.
_SELECTOR_FLAGS = {"--skill", "--name"}
_REF_FLAGS = {"--branch", "-b", "--ref", "--tag"}

_SOURCE_RE = re.compile(r"^[\w.-]+/[\w.-]+(?:/[\w./-]*)?$")
_SCP_RE = re.compile(r"^(?:[\w.-]+@)?([\w.-]+):(.+)$")

_SKILLS_SH_HOSTS = {"skills.sh", "www.skills.sh"}


def _github_url(owner: str, repo: str) -> str:
    return f"https://github.com/{owner}/{repo.removesuffix('.git')}"


def _from_skills_sh(url: str) -> SourceSpec | None:
    """Map a skills.sh catalog page to the GitHub repo it indexes.

    ``skills.sh/<owner>/<repo>/<skill>`` mirrors ``github.com/<owner>/<repo>``;
    the third segment names a skill, not a repo path, so it becomes a selector
    and the layout inside the repo does not have to be guessed.
    """
    parts = urlsplit(url)
    if parts.hostname not in _SKILLS_SH_HOSTS:
        return None
    segments = [s for s in parts.path.split("/") if s]
    if len(segments) < 2:
        raise SkillImportError(
            "A skills.sh URL needs an owner and a repo, e.g. "
            "https://skills.sh/anthropics/skills/frontend-design."
        )
    owner, repo = segments[0], segments[1]
    selector = segments[2] if len(segments) >= 3 else ""
    return SourceSpec(url=_github_url(owner, repo), selector=selector)


def parse_source(text: str, ref: str = "", selector: str = "") -> SourceSpec:
    """Normalize whatever was pasted into a fetchable source.

    Registry pages hand out install commands, not URLs, and people paste what
    they are given. Accepting `npx skills add ... --skill x`, a skills.sh page
    or a bare `owner/repo` costs a few lines here and removes the guesswork of
    translating them by hand.
    """
    text = text.strip()
    if not text:
        raise SkillImportError("Provide a URL to import from.")

    tokens = text.split()
    source = ""
    pending: str | None = None
    for token in tokens:
        if pending:
            if pending in _SELECTOR_FLAGS:
                selector = selector or token
            else:
                ref = ref or token
            pending = None
            continue
        flag, _, inline = token.partition("=")
        if flag in _SELECTOR_FLAGS or flag in _REF_FLAGS:
            if inline:
                if flag in _SELECTOR_FLAGS:
                    selector = selector or inline
                else:
                    ref = ref or inline
            else:
                pending = flag
            continue
        if token.startswith("-") or token.lower() in _COMMAND_NOISE:
            continue
        if not source and (
            "://" in token or _SCP_RE.match(token) or _SOURCE_RE.match(token)
        ):
            source = token

    if not source:
        raise SkillImportError(_UNSUPPORTED_SOURCE)

    scp = _SCP_RE.match(source) if "://" not in source else None
    if scp is not None:
        source = f"https://{scp.group(1)}/{scp.group(2)}"
    elif "://" not in source:
        segments = [s for s in source.split("/") if s]
        if "." in segments[0]:
            # A URL pasted without its scheme (github.com/o/r, skills.sh/o/r/s).
            source = f"https://{source}"
        else:
            # Bare `owner/repo` shorthand. A trailing segment names a skill
            # rather than a repo path — the same convention skills.sh uses, and
            # it resolves without having to know the repo's internal layout.
            source = _github_url(segments[0], segments[1])
            if len(segments) > 2:
                selector = selector or segments[-1]

    from_registry = _from_skills_sh(source)
    if from_registry is not None:
        return SourceSpec(
            url=from_registry.url,
            ref=ref,
            selector=selector or from_registry.selector,
        )
    return SourceSpec(url=source, ref=ref, selector=selector)


# -- URL shapes ---------------------------------------------------------------


def _github_parts(url: str) -> tuple[str, str, str, str] | None:
    """Split a github.com URL into (owner, repo, ref, subdir), or None."""
    parts = urlsplit(url)
    if parts.hostname not in ("github.com", "www.github.com"):
        return None
    segments = [s for s in parts.path.split("/") if s]
    if len(segments) < 2:
        return None
    owner, repo = segments[0], segments[1].removesuffix(".git")
    if len(segments) >= 4 and segments[2] in ("tree", "blob"):
        return owner, repo, segments[3], "/".join(segments[4:])
    return owner, repo, "", ""


def _codeload_url(owner: str, repo: str, ref: str) -> str:
    return f"https://codeload.github.com/{owner}/{repo}/tar.gz/{ref}"


def _fetch_github(
    owner: str, repo: str, ref: str, subdir: str, selector: str = ""
) -> ImportedSkill:
    """Prefer the codeload tarball over `git clone`: one request, no subprocess.

    The tarball is read twice: once for member names, to decide which skill is
    being imported, and once for the bytes of that skill alone. A catalog repo
    is hundreds of files, and extracting all of them would hit the size caps
    long before the two-file skill inside was ever reached.
    """
    refs = [ref] if ref else ["refs/heads/main", "refs/heads/master"]
    last: SkillImportError | None = None
    for candidate in refs:
        try:
            blob = _fetch(_codeload_url(owner, repo, candidate))
        except SkillImportError as exc:
            last = exc
            continue

        names = list_tar_members(blob)
        # codeload wraps everything in `<repo>-<ref>/`; drop that layer so the
        # requested subdir is addressable the way the user typed it.
        top = {path.split("/", 1)[0] for path in names}
        wrapper = top.pop() if len(top) == 1 else ""
        scope = f"{wrapper}/{subdir.rstrip('/')}" if subdir else wrapper
        scoped = [
            name
            for name in names
            if not scope or name == scope or name.startswith(f"{scope}/")
        ]
        if subdir and not scoped:
            raise SkillImportError(f"Path '{subdir}' does not exist in {owner}/{repo}.")

        # Read only the candidate SKILL.md files, so their frontmatter can name
        # and describe the choices without pulling in the rest of the repo.
        relative = [name[len(scope) + 1 :] if scope else name for name in scoped]
        heads = frozenset(
            name for name in scoped if PurePosixPath(name).name == SKILL_FILE
        )
        blobs = read_tar(blob, only=heads) if heads else {}
        root = find_skill_root(
            relative,
            selector=selector,
            blobs={
                (path[len(scope) + 1 :] if scope else path): data
                for path, data in blobs.items()
            },
        )

        prefix = "/".join(part for part in (scope, root) if part)
        files = _reroot(read_tar(blob, prefix=prefix), prefix)
        return build_skill(
            files,
            fallback_name=PurePosixPath(root or subdir).name or repo,
            source_url=f"https://github.com/{owner}/{repo}",
            source_ref=candidate,
            source_kind="url",
        )
    raise last or SkillImportError(f"Cannot download {owner}/{repo}.")


def _raw_github_url(url: str) -> str:
    """Rewrite a `blob` page URL to raw content; other URLs pass through."""
    parts = urlsplit(url)
    if parts.hostname not in ("github.com", "www.github.com"):
        return url
    segments = [s for s in parts.path.split("/") if s]
    if len(segments) >= 5 and segments[2] == "blob":
        rest = "/".join(segments[4:])
        return (
            f"https://raw.githubusercontent.com/{segments[0]}/{segments[1]}/"
            f"{segments[3]}/{rest}"
        )
    return url


def _clone_git(url: str, ref: str, selector: str = "") -> ImportedSkill:
    """Last resort for non-GitHub hosts: a shallow clone into a temp dir."""
    if shutil.which("git") is None:
        raise SkillImportError("git is not available in this container.")
    _assert_safe_url(url)
    workdir = Path(tempfile.mkdtemp(prefix="skill-import-"))
    try:
        command = ["git", "clone", "--depth", "1", "--quiet"]
        if ref:
            command += ["--branch", ref]
        command += ["--", url, str(workdir / "repo")]
        try:
            subprocess.run(  # nosec B603 - fixed argv, https URL already validated
                command,
                check=True,
                capture_output=True,
                timeout=GIT_TIMEOUT_SECONDS,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
        except subprocess.TimeoutExpired as exc:
            raise SkillImportError(
                f"git clone timed out after {exc.timeout}s."
            ) from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or b"").decode("utf-8", "replace").strip()[:300]
            raise SkillImportError(f"git clone failed: {detail}") from exc
        files = read_directory(workdir / "repo")
        return build_skill(
            files,
            fallback_name=PurePosixPath(urlsplit(url).path).stem,
            source_url=url,
            source_ref=ref,
            source_kind="git",
            selector=selector,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def read_directory(root: Path) -> dict[str, bytes]:
    """Read a directory tree into a file map, skipping symlinks and junk."""
    files: dict[str, bytes] = {}
    for current, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for filename in sorted(filenames):
            source = Path(current) / filename
            if source.is_symlink() or not source.is_file():
                continue
            relative = safe_relpath(str(source.relative_to(root)))
            if relative is None or _skippable(relative):
                continue
            _record(files, relative, source.read_bytes())
    return files


def fetch_skill(source: str, ref: str = "", skill: str = "") -> ImportedSkill:
    """Import a skill from any supported source.

    Order matters: whatever was pasted is normalized first, then GitHub
    tree/repo URLs go through codeload (cheap, no subprocess), single files and
    archives are fetched directly, and `git clone` is only reached for a `.git`
    URL on another host.
    """
    spec = parse_source(source, ref=ref, selector=skill)
    url, ref, selector = spec.url, spec.ref, spec.selector

    parts = urlsplit(url)
    path_lower = parts.path.lower()

    if path_lower.endswith(".git"):
        return _clone_git(url, ref, selector)

    github = _github_parts(url)
    if github is not None:
        owner, repo, gh_ref, subdir = github
        segments = [s for s in parts.path.split("/") if s]
        is_blob = len(segments) >= 5 and segments[2] == "blob"
        if not is_blob:
            return _fetch_github(owner, repo, ref or gh_ref, subdir, selector)

    is_archive = path_lower.endswith(ARCHIVE_SUFFIXES)
    is_skill_md = PurePosixPath(parts.path).name.lower() == SKILL_FILE.lower()
    if not is_archive and not is_skill_md:
        # Refuse before fetching. A home page or an install.sh is not a skill,
        # and there is nothing to learn from downloading it.
        raise SkillImportError(_UNSUPPORTED_SOURCE)

    blob = _fetch(_raw_github_url(url))

    if is_archive:
        return build_skill(
            read_archive(blob, path_lower),
            fallback_name=PurePosixPath(parts.path).name.split(".")[0],
            source_url=url,
            source_ref=ref,
            source_kind="archive",
            selector=selector,
        )

    return build_skill(
        {SKILL_FILE: blob},
        fallback_name=PurePosixPath(parts.path).parent.name,
        source_url=url,
        source_ref=ref,
        source_kind="url",
    )


def import_upload(data: bytes, filename: str, skill: str = "") -> ImportedSkill:
    """Import from a file the user uploaded: an archive, or a bare SKILL.md."""
    name = PurePosixPath(filename or "").name
    if name.lower().endswith(ARCHIVE_SUFFIXES):
        if len(data) > MAX_DOWNLOAD_BYTES:
            raise SkillImportError(
                f"Upload exceeds the {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MiB limit."
            )
        files = read_archive(data, name.lower())
    elif name.lower().endswith(".md"):
        files = {SKILL_FILE: data}
    else:
        raise SkillImportError(
            "Upload a .zip / .tar.gz archive of the skill directory, or a "
            "SKILL.md file."
        )
    return build_skill(
        files,
        fallback_name=PurePosixPath(name).stem,
        source_url="",
        source_ref="",
        source_kind="upload",
        selector=skill,
    )
