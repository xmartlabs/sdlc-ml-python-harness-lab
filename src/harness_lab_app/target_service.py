"""Target repositories: the repo a run works against, resolved to a commit.

A ``TargetRepository`` is paired with a case at the run rather than pinned to it
(ADR-0029), so one case can be evaluated across many repositories and a client
repository can be paired with a case derived from its own history.

The load-bearing function here is :func:`resolve_ref`. A run recorded against a
moving ref like ``main`` is not reproducible — the commit it used becomes
unrecoverable the moment someone pushes — so the ref is resolved to an immutable
SHA at run creation, before anything is persisted, and it is the SHA that the
run carries.
"""

from __future__ import annotations

import re
import subprocess
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlmodel import Session, select

from harness_lab_app.models import TargetRepository, TargetStatus

if TYPE_CHECKING:
    from harness_lab_app.schemas import (
        TargetRepositoryCreate,
        TargetRepositoryUpdate,
    )


class TargetError(ValueError):
    """A target repository could not be used as asked."""


class TargetNotFoundError(TargetError):
    """No target repository with that id."""


class TargetRefError(TargetError):
    """A ref could not be resolved to a commit on the remote."""


# Accepted URL forms. The list is a security boundary, not a convenience: a URL
# beginning with `-` would be read by git as an option (`--upload-pack=...` is
# remote command execution), so anything that is not recognisably one of these
# shapes is refused rather than passed through.
_URL_SCHEMES = ("https://", "http://", "ssh://", "git://")
_SCP_LIKE = re.compile(r"^[A-Za-z0-9_.+-]+@[A-Za-z0-9_.\-]+:[^\s]+$")
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_SHORT_SHA = re.compile(r"^[0-9a-f]{7,40}$")

# Fail fast instead of hanging on a credential prompt: a private repository with
# no configured credentials must return an error the operator can read, not park
# the API worker on a blocking read from a terminal that does not exist.
_GIT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_SSH_COMMAND": "ssh -oBatchMode=yes",
    "GIT_ASKPASS": "",
}


def validate_repository_url(url: str) -> str:
    """Return the URL if git can be asked about it safely, else raise."""
    cleaned = (url or "").strip()
    if not cleaned:
        raise TargetError("Repository URL is required.")
    if cleaned.startswith("-"):
        raise TargetError(
            "Repository URL may not start with '-': git would read it as an option."
        )
    if cleaned.startswith(_URL_SCHEMES) or _SCP_LIKE.match(cleaned):
        return cleaned
    raise TargetError(
        "Repository URL must be https://, http://, ssh://, git:// or "
        f"user@host:path — got {cleaned!r}."
    )


def _git_ls_remote(url: str, ref: str, timeout: int) -> list[tuple[str, str]]:
    try:
        # List form, and the URL was validated above, so nothing here can
        # reach a shell or be read by git as an option.
        result = subprocess.run(
            ["git", "ls-remote", url, ref],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_GIT_ENV | {"PATH": "/usr/bin:/bin:/usr/local/bin"},
            check=False,
        )
    except FileNotFoundError as exc:  # pragma: no cover - git is in every image
        raise TargetRefError("git is not available to resolve the ref.") from exc
    except subprocess.TimeoutExpired as exc:
        raise TargetRefError(
            f"Timed out after {timeout}s asking {url} for ref {ref!r}."
        ) from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-400:]
        raise TargetRefError(f"git ls-remote failed for {url}: {detail}")
    rows: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 2 and parts[0].strip():
            rows.append((parts[0].strip(), parts[1].strip()))
    return rows


def resolve_ref(repository_url: str, ref: str, *, timeout: int = 30) -> str:
    """Resolve ``ref`` on ``repository_url`` to a full commit SHA.

    A ref that is already a full SHA is returned unchanged: it is immutable by
    construction, and `ls-remote` cannot list an arbitrary commit anyway, so
    asking would fail a pin that is in fact perfectly valid.

    Preference order matters. An annotated tag's own object is not the commit it
    points at, so the peeled ``^{}`` entry wins when present — otherwise pinning
    to a release tag would record the tag object and the checkout would differ
    from what was recorded.
    """
    url = validate_repository_url(repository_url)
    wanted = (ref or "").strip()
    if not wanted:
        raise TargetError("A ref is required (branch, tag or commit SHA).")
    if _FULL_SHA.match(wanted):
        return wanted
    rows = _git_ls_remote(url, wanted, timeout)
    if not rows:
        if _SHORT_SHA.match(wanted):
            raise TargetRefError(
                f"{wanted!r} looks like an abbreviated commit. Git cannot resolve "
                "an abbreviated SHA on a remote — use the full 40-character SHA, "
                "a branch, or a tag."
            )
        raise TargetRefError(f"No ref matching {wanted!r} on {url}.")
    by_name = {name: sha for sha, name in rows}
    for candidate in (
        f"refs/tags/{wanted}^{{}}",
        f"refs/heads/{wanted}",
        f"refs/tags/{wanted}",
        wanted,
    ):
        if candidate in by_name:
            return by_name[candidate]
    return rows[0][0]


class TargetService:
    """CRUD over target repositories."""

    def __init__(self, session: Session):
        self.session = session

    def list_targets(self, include_archived: bool = False) -> list[TargetRepository]:
        statement = select(TargetRepository)
        if not include_archived:
            statement = statement.where(TargetRepository.status == TargetStatus.active)
        rows = list(self.session.exec(statement).all())
        return sorted(rows, key=lambda row: row.target_id)

    def get_target(self, target_id: str) -> TargetRepository:
        row = self.session.exec(
            select(TargetRepository).where(TargetRepository.target_id == target_id)
        ).first()
        if row is None:
            raise TargetNotFoundError(f"Unknown target repository: {target_id}")
        return row

    def create_target(self, data: TargetRepositoryCreate) -> TargetRepository:
        target_id = data.target_id.strip()
        if not target_id:
            raise TargetError("target_id is required.")
        existing = self.session.exec(
            select(TargetRepository).where(TargetRepository.target_id == target_id)
        ).first()
        if existing is not None:
            raise TargetError(f"Target repository already exists: {target_id}")
        row = TargetRepository(
            **data.model_dump(exclude={"target_id", "repository_url"}),
            target_id=target_id,
            repository_url=validate_repository_url(data.repository_url),
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def update_target(
        self, target_id: str, data: TargetRepositoryUpdate
    ) -> TargetRepository:
        row = self.get_target(target_id)
        payload = data.model_dump(exclude_unset=True)
        if "repository_url" in payload:
            payload["repository_url"] = validate_repository_url(
                payload["repository_url"]
            )
        for field, value in payload.items():
            setattr(row, field, value)
        row.updated_at = datetime.now(UTC)
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def delete_target(self, target_id: str) -> None:
        """Archive the target. Runs that used it keep a readable provenance.

        Deleting the row would leave historical runs pointing at an id that
        resolves to nothing, which is exactly the provenance hole ADR-0029
        exists to close. Archiving hides it from the run form and keeps it
        readable.
        """
        row = self.get_target(target_id)
        row.status = TargetStatus.archived
        row.updated_at = datetime.now(UTC)
        self.session.add(row)
        self.session.commit()
