"""Ad hoc tasks: the operator's own repository, a free prompt, a measured run (ADR-0095).

The run pipeline is reused, not rebuilt. A task is three rows the rest of the
lab already understands:

* a ``TargetRepository`` whose checkout is the lab's own copy of the folder
  (``data/checkouts/adhoc-<slug>``), pinned at the operator's HEAD;
* a synthetic ``Case`` of type ``adhoc`` whose one prompt variant is the task;
* the ``AdhocTask`` row tying them to the folder the change goes back to.

A run is then an ordinary run with ``harness_source=native`` against that
target, so preflight, provisioning, the runner, progress, cost capture and
cancellation all work unchanged. What is new lives here: the clean patch, the
metrics copied out before the files go, and the operator's decision.

Every id this module creates starts with ``adhoc-``. That prefix is how the
benchmark-facing views leave ad hoc work out without a join (``is_adhoc_*``).
"""

from __future__ import annotations

import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlmodel import Session, desc, select

if TYPE_CHECKING:
    from collections.abc import Sequence

from harness_lab_app import adhoc_git
from harness_lab_app.config import get_settings
from harness_lab_app.models import (
    AdhocRun,
    AdhocTask,
    Case,
    CaseStatus,
    CaseType,
    HarnessSource,
    Run,
    RunConfig,
    RunState,
    TargetRepository,
    TargetStatus,
    Validation,
    utc_now,
)

PREFIX = "adhoc-"
PROMPT_VARIANT = "task"
DECISIONS = {"approved", "rejected", "discarded"}
LIVE_RUN_STATES = {RunState.pending, RunState.running, RunState.scoring}
LIVE_APPLY_STATES = {"applying", "undoing"}

# The harness an ad hoc run gives the agent. "repo" is the operator's
# repository as it is (harness_source=native): its own CLAUDE.md, skills and
# agents if it has any, nothing from the lab. The other levels project the
# lab's harness into the copy (harness_source=injected) at a skill profile
# (ADR-0085). Projected before the baseline commit and excluded from
# change.patch by path, so none of it reaches the operator's folder.
HARNESS_LEVELS = ("repo", "minimal", "full")


def harness_condition(level: str) -> tuple[HarnessSource, str]:
    """(harness_source, skill_profile) for an ad hoc harness level."""
    if level == "repo":
        return HarnessSource.native, ""
    if level == "full":
        return HarnessSource.injected, ""
    if level == "minimal":
        return HarnessSource.injected, "minimal"
    raise AdhocError(
        f"harness_level must be one of {', '.join(HARNESS_LEVELS)}, not '{level}'."
    )


# An undecided ad hoc run keeps its whole directory: the workspace for the
# sandbox, steps/ for the skills it used, diff.patch and logs for the LLM audit
# and judge. Everything goes when it is rejected or discarded, or after
# `adhoc_retention_days`; the metrics row and change.patch's file list are in
# the database from the moment the run ends, so nothing measured is lost.

# How a run with no case of its own is graded when the operator asks (ADR-0095).
# The case checks, if any, are the task's own verification commands.
ADHOC_RUBRIC = """Grade how well the change does what the task asked, 0-10.

- 9-10: does exactly what was asked, nothing missing, no unrelated edits, the
  code is correct and fits the repository's existing style.
- 7-8: does what was asked with minor gaps or small unrelated edits.
- 4-6: partly done, or done with mistakes a reviewer must fix before merging.
- 1-3: attempts the task but is mostly wrong, incomplete or breaks something.
- 0: no change, or a change unrelated to the task.

Penalise edits outside the task's scope (lockfiles, CI, unrelated files)
unless the task needed them. If verification commands ran, a failing check
caps the grade at 5. Judge only the change and the evidence given."""
_PATCH_PREVIEW_BYTES = 2_000_000
_SLUG = re.compile(r"[^a-z0-9]+")


class AdhocError(ValueError):
    """A request the operator can fix."""


class AdhocNotFoundError(AdhocError):
    """No such task or run."""


class AdhocConflictError(AdhocError):
    """The request clashes with something in progress."""


def is_adhoc_id(identifier: str | None) -> bool:
    """Whether a run, case or target id belongs to an ad hoc task."""
    return bool(identifier) and str(identifier).startswith(PREFIX)


def _judged(run: Run) -> bool:
    """Whether the run's score came from the LLM judge (never the operator)."""
    return run.score is not None and (run.notes or "").startswith("LLM-judged")


def _provenance_url(folder: Path) -> str:
    """The folder's own remote when it is a URL git can name, else a placeholder."""
    from harness_lab_app.target_service import TargetError, validate_repository_url

    _, remote, _ = adhoc_git.git(["config", "--get", "remote.origin.url"], folder)
    try:
        return validate_repository_url(remote.strip())
    except TargetError:
        return f"https://local.invalid/{_slug(folder.name)}"


def _slug(text: str, limit: int = 32) -> str:
    slug = _SLUG.sub("-", text.lower()).strip("-")
    return (slug[:limit].strip("-")) or "repo"


_TASK_ID = re.compile(r"^adhoc-[a-z0-9]+(?:-[a-z0-9]+)*-\d{3}$")
_RUN_ID = re.compile(r"^adhoc-[a-z0-9]+(?:-[a-z0-9]+)*-\d{3}-K-\d{3,}$")


def _checked_task_id(task_id: str) -> str:
    """A task id is a path segment (data/checkouts/<id>): shape-checked first."""
    if not _TASK_ID.match(task_id or ""):
        raise AdhocNotFoundError(f"Unknown ad hoc task: {task_id!r}")
    return task_id


def run_dir(run_id: str) -> Path:
    if not _RUN_ID.match(run_id or ""):
        raise AdhocError(f"Not an ad hoc run id: {run_id!r}")
    return get_settings().data_runs_dir / run_id


def patch_path(run_id: str) -> Path:
    return run_dir(run_id) / "change.patch"


def host_patch_path(run_id: str) -> str:
    """Where the operator's own shell finds the patch."""
    host_root = get_settings().host_repo_root or str(get_settings().repo_root)
    return str(Path(host_root) / "data" / "runs" / run_id / "change.patch")


def apply_command(task: AdhocTask, run_id: str) -> str:
    """The command a person runs instead of pressing Apply.

    ``--3way`` because a person at a terminal can resolve a conflict the button
    refuses; the button stays strict (all or nothing, files only).
    """
    from shlex import quote

    return (
        f"git -C {quote(task.host_path)} apply --3way {quote(host_patch_path(run_id))}"
    )


# --------------------------------------------------------------------------
# Inspecting the operator's folder
# --------------------------------------------------------------------------


def inspect_source(relative: str) -> dict[str, Any]:
    """Branch, HEAD and uncommitted files of a folder under the local sources root."""
    from harness_lab_app.local_sources_service import host_view_of, resolve

    path = resolve(relative)
    info: dict[str, Any] = {
        "path": relative.strip().strip("/"),
        "host_path": host_view_of(path),
        "is_repo": (path / ".git").exists(),
        "branch": "",
        "head": "",
        "dirty": False,
        "dirty_files": [],
        "remote_url": "",
    }
    if not info["is_repo"]:
        return info
    code, head, _ = adhoc_git.git(["rev-parse", "HEAD"], path)
    if code != 0:
        info["error"] = "The repository has no commits yet."
        return info
    info["head"] = head.strip()
    _, branch, _ = adhoc_git.git(["symbolic-ref", "--short", "HEAD"], path)
    info["branch"] = branch.strip()
    _, remote, _ = adhoc_git.git(["config", "--get", "remote.origin.url"], path)
    info["remote_url"] = remote.strip()
    dirty = adhoc_git.dirty_paths(path)
    info["dirty"] = bool(dirty)
    info["dirty_files"] = dirty[:200]
    return info


def _delete_blocker(rows: Sequence[AdhocRun], states: dict[str, RunState]) -> str:
    """Why a task cannot be deleted without force, or "" when it can."""
    if any(states.get(row.run_id) in LIVE_RUN_STATES for row in rows):
        return "A run of this task is still going. Cancel it first."
    if any(row.apply_state in ({"applied"} | LIVE_APPLY_STATES) for row in rows):
        return (
            "A change of this task is applied to your folder. Undo it, or "
            "delete with force to keep the change and forget the task."
        )
    return ""


def _source_state(task: AdhocTask) -> dict[str, Any]:
    """The folder now, plus how far it moved since the task was created."""
    try:
        state = inspect_source(task.source_path)
    except Exception as exc:  # the folder may be gone or the mount changed
        return {
            "path": task.source_path,
            "host_path": task.host_path,
            "is_repo": False,
            "error": str(exc),
            "head_moved_by": None,
        }
    moved: int | None = None
    if state.get("head") and not task.pr_ref:
        from harness_lab_app.local_sources_service import resolve

        code, out, _ = adhoc_git.git(
            ["rev-list", "--count", f"{task.base_commit}..HEAD"],
            resolve(task.source_path),
        )
        if code == 0 and out.strip().isdigit():
            moved = int(out.strip())
    state["head_moved_by"] = moved
    return state


# --------------------------------------------------------------------------
# Reviewing a pull request (ADR-0100)
# --------------------------------------------------------------------------

REVIEW_DEPTHS = ("quick", "standard", "deep")
_REMOTE_SLUG = re.compile(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?/?$")


def _repo_slug(folder: Path) -> str:
    """``owner/name`` from the folder's origin, SSH host aliases included."""
    _, remote, _ = adhoc_git.git(["config", "--get", "remote.origin.url"], folder)
    match = _REMOTE_SLUG.search(remote.strip())
    return f"{match.group(1)}/{match.group(2)}" if match else ""


def review_prompt(
    *,
    pr: str,
    merge_base: str,
    base_ref: str,
    title: str = "",
    body: str = "",
    depth: str = "standard",
    stat: str = "",
) -> str:
    """The task an unattended review run gets: the range, the PR, the output.

    The range is a SHA, not ``origin/<base>``: the run's workspace is a clone
    of the lab's copy and has no remote-tracking branches.
    """
    lines = [
        f"Review pull request {pr}. Its head is checked out here (HEAD).",
        f"Review the range `{merge_base}...HEAD` (where it leaves `{base_ref}`).",
    ]
    if stat:
        lines.append(f"Size: {stat}.")
    if title.strip():
        lines += ["", f"PR title: {title.strip()}"]
    if body.strip():
        lines += ["", "PR description:", body.strip()[:6000]]
    lines += [
        "",
        f"Unattended run: depth = {depth}. There is no GitHub access here; work "
        "from the local git range. Run the repository's own checks that work "
        "without external services, and report the ones you could not run.",
        "",
        "Write the review to `review.json` at the repository root: a verdict, a "
        "one-sentence summary, and findings with file, line (in the new file), "
        "severity, title, explanation and suggestion. Do not change, commit or "
        "delete any other file.",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# The service
# --------------------------------------------------------------------------


class AdhocService:
    def __init__(self, session: Session, enqueue: Any = None) -> None:
        self.session = session
        self.enqueue = enqueue

    # ---- tasks ---------------------------------------------------------

    def create_task(
        self,
        source: str,
        prompt: str,
        title: str = "",
        setup_commands: list[str] | None = None,
        validation_commands: list[str] | None = None,
        trial_skill: str = "",
        invoke_trial_skill: bool = False,
        head_ref: str = "",
        pr_ref: str = "",
        pr_base_ref: str = "",
    ) -> AdhocTask:
        """Copy the folder into the lab, pin a commit, and register the task.

        The pinned commit is the folder's HEAD, or ``head_ref`` when given (a
        branch or SHA the folder already has — how a pull request's head is
        reviewed without checking it out over the operator's work). The folder
        is only read either way.
        """
        from harness_lab_app.checkout_service import CheckoutError
        from harness_lab_app.local_sources_service import host_view_of, resolve

        text = (prompt or "").strip()
        if not text:
            raise AdhocError("A task needs a prompt.")
        trial = (trial_skill or "").strip()
        if invoke_trial_skill and not trial:
            raise AdhocError("Pick the skill to try before asking the agent to use it.")
        if trial:
            self._require_staged_skill(trial)
        folder = resolve(source)
        if not (folder / ".git").exists():
            raise AdhocError(f"{host_view_of(folder)} is not a git repository.")
        ref = (head_ref or "").strip()
        code, head, _ = adhoc_git.git(
            ["rev-parse", "--verify", "--quiet", f"{ref or 'HEAD'}^{{commit}}"], folder
        )
        if code != 0:
            if ref:
                raise AdhocError(f"{host_view_of(folder)} has no commit '{ref}'.")
            raise AdhocError(
                "The repository has no commits yet: commit something first."
            )
        base_commit = head.strip()
        if ref:
            branch = ref
        else:
            _, branch, _ = adhoc_git.git(["symbolic-ref", "--short", "HEAD"], folder)

        task_id = self._next_task_id(folder.name)
        checkout = get_settings().checkouts_dir / task_id
        try:
            self._copy_folder(folder, checkout, base_commit)
        except CheckoutError as exc:
            raise AdhocError(str(exc)) from exc

        target = TargetRepository(
            target_id=task_id,
            name=title.strip() or folder.name,
            description=f"Ad hoc task on {host_view_of(folder)}",
            # The local copy is the source of record for every run
            # (``clone_source`` prefers ``checkout_path``). The URL is the
            # folder's own remote when it has one, for provenance only; nothing
            # ever clones from it.
            repository_url=_provenance_url(folder),
            default_ref=base_commit,
            status=TargetStatus.active,
            setup_commands=[c for c in (setup_commands or []) if c.strip()],
            validation_commands=[c for c in (validation_commands or []) if c.strip()],
            tags=["adhoc"],
            checkout_path=str(checkout),
            pinned_commit=base_commit,
            pinned_at=datetime.now(UTC),
        )
        case = Case(
            case_id=task_id,
            name=title.strip() or f"Ad hoc: {folder.name}",
            description="Ad hoc task (ADR-0095). Not a benchmark.",
            type=CaseType.adhoc,
            status=CaseStatus.active,
            tags=["adhoc"],
            origin_target_id=task_id,
            prompt_variants={PROMPT_VARIANT: text},
            scoring_rubric=ADHOC_RUBRIC,
            max_steps=1,
        )
        task = AdhocTask(
            task_id=task_id,
            title=title.strip() or text.splitlines()[0][:80],
            prompt=text,
            source_path=str(
                folder.relative_to(Path(get_settings().local_sources_root).resolve())
            ),
            host_path=host_view_of(folder),
            branch=branch.strip(),
            base_commit=base_commit,
            target_id=task_id,
            case_id=task_id,
            setup_commands=list(target.setup_commands),
            validation_commands=list(target.validation_commands),
            trial_skill=trial,
            invoke_trial_skill=bool(trial) and invoke_trial_skill,
            pr_ref=(pr_ref or "").strip(),
            pr_base_ref=(pr_base_ref or "").strip(),
        )
        self.session.add(target)
        self.session.add(case)
        self.session.add(task)
        self.session.commit()
        self.session.refresh(task)
        return task

    def create_pr_review_task(
        self,
        source: str,
        pr_number: int,
        head_ref: str,
        base_ref: str = "origin/main",
        repo: str = "",
        pr_title: str = "",
        pr_body: str = "",
        trial_skill: str = "",
        invoke_trial_skill: bool = False,
        depth: str = "standard",
    ) -> AdhocTask:
        """A task that reviews one pull request (ADR-0100).

        The PR's head must already be in the folder (a branch or a fetched
        ``refs/pull/N/head``): the lab container holds no credential for the
        operator's remote, and the folder is only read. The review range is
        pinned as ``<merge-base>...HEAD`` by SHA because the run's workspace is
        a plain clone of the lab's copy and carries no remote-tracking branch.
        """
        from harness_lab_app.local_sources_service import host_view_of, resolve

        if pr_number < 1:
            raise AdhocError("A pull request number is a positive integer.")
        if depth not in REVIEW_DEPTHS:
            raise AdhocError(f"depth must be one of {', '.join(REVIEW_DEPTHS)}.")
        head = (head_ref or "").strip()
        base = (base_ref or "").strip() or "origin/main"
        if not head:
            raise AdhocError("Name the branch or commit that holds the PR's head.")
        folder = resolve(source)
        if not (folder / ".git").exists():
            raise AdhocError(f"{host_view_of(folder)} is not a git repository.")
        code, merge_base, _ = adhoc_git.git(["merge-base", base, head], folder)
        if code != 0 or not merge_base.strip():
            raise AdhocError(
                f"Could not find where '{head}' leaves '{base}' in "
                f"{host_view_of(folder)}. Fetch both first (git fetch origin "
                f"pull/{pr_number}/head:<branch>)."
            )
        merge_base = merge_base.strip()
        _, files, _ = adhoc_git.git(
            ["diff", "--shortstat", f"{merge_base}...{head}"], folder
        )
        name = repo.strip() or _repo_slug(folder) or folder.name
        prompt = review_prompt(
            pr=f"{name}#{pr_number}",
            merge_base=merge_base,
            base_ref=base,
            title=pr_title,
            body=pr_body,
            depth=depth,
            stat=files.strip(),
        )
        return self.create_task(
            source,
            prompt,
            title=f"Review PR #{pr_number}"
            + (f": {pr_title.strip()}" if pr_title.strip() else ""),
            trial_skill=trial_skill,
            invoke_trial_skill=invoke_trial_skill,
            head_ref=head,
            pr_ref=f"{name}#{pr_number}",
            pr_base_ref=merge_base,
        )

    def _require_staged_skill(self, name: str) -> None:
        from harness_lab_app.models import StagedSkill

        skill = self.session.exec(
            select(StagedSkill).where(StagedSkill.name == name)
        ).first()
        if skill is None or skill.status == "archived":
            raise AdhocError(f"No staged skill named '{name}'.")

    def _trial_skill_hashes(self, names: list[str]) -> dict[str, str]:
        """Each staged skill's content hash now, as the run will see it."""
        from harness_lab_app.models import StagedSkill

        if not names:
            return {}
        rows = self.session.exec(
            select(StagedSkill).where(StagedSkill.name.in_(names))  # type: ignore[attr-defined]
        ).all()
        return {row.name: row.content_hash or "" for row in rows}

    def _next_task_id(self, folder_name: str) -> str:
        stem = f"{PREFIX}{_slug(folder_name)}"
        taken = set(
            self.session.exec(
                select(AdhocTask.task_id).where(AdhocTask.task_id.startswith(stem))
            ).all()
        )
        checkouts = get_settings().checkouts_dir
        for n in range(1, 10_000):
            candidate = f"{stem}-{n:03d}"
            if candidate not in taken and not (checkouts / candidate).exists():
                return candidate
        raise AdhocError("Too many tasks for this folder.")  # pragma: no cover

    @staticmethod
    def _copy_folder(folder: Path, checkout: Path, base_commit: str) -> None:
        """A private clone at exactly ``base_commit``; the folder is only read."""
        from harness_lab_app.checkout_service import CheckoutError

        if checkout.exists():
            raise CheckoutError(f"{checkout} already exists.")
        checkout.parent.mkdir(parents=True, exist_ok=True)
        code, _, err = adhoc_git.git(
            ["clone", "--no-hardlinks", "--quiet", str(folder), str(checkout)],
            checkout.parent,
            timeout=900,
        )
        if code != 0:
            shutil.rmtree(checkout, ignore_errors=True)
            raise CheckoutError(f"Could not copy {folder}: {err.strip()}")
        code, _, err = adhoc_git.git(
            ["checkout", "--quiet", "--detach", base_commit], checkout
        )
        if code != 0:
            shutil.rmtree(checkout, ignore_errors=True)
            raise CheckoutError(
                f"Could not check out {base_commit[:10]}: {err.strip()}"
            )

    def list_tasks(self) -> list[dict[str, Any]]:
        tasks = self.session.exec(
            select(AdhocTask).order_by(desc(AdhocTask.created_at))
        ).all()
        runs_by_task: dict[str, list[AdhocRun]] = {}
        for row in self.session.exec(select(AdhocRun)).all():
            runs_by_task.setdefault(row.task_id, []).append(row)
        states = self._run_states(
            [r.run_id for rs in runs_by_task.values() for r in rs]
        )
        summaries = []
        for task in tasks:
            rows = runs_by_task.get(task.task_id, [])
            costs = [r.cost_usd for r in rows if r.cost_usd is not None]
            applied = [r.run_id for r in rows if r.apply_state == "applied"]
            summaries.append(
                {
                    **self._task_dict(task),
                    "runs": len(rows),
                    "live_runs": sum(
                        1 for r in rows if states.get(r.run_id) in LIVE_RUN_STATES
                    ),
                    "applied_run_id": applied[0] if applied else None,
                    "last_run_at": max(
                        (r.created_at.isoformat() for r in rows), default=None
                    ),
                    "total_cost_usd": round(sum(costs), 6) if costs else None,
                }
            )
        return summaries

    def get_task(self, task_id: str) -> AdhocTask:
        _checked_task_id(task_id)
        task = self.session.exec(
            select(AdhocTask).where(AdhocTask.task_id == task_id)
        ).first()
        if task is None:
            raise AdhocNotFoundError(f"Unknown ad hoc task: {task_id}")
        return task

    def task_detail(self, task_id: str) -> dict[str, Any]:
        task = self.get_task(task_id)
        rows = self.session.exec(
            select(AdhocRun)
            .where(AdhocRun.task_id == task_id)
            .order_by(desc(AdhocRun.created_at))
        ).all()
        for row in rows:
            self.capture_metrics(row.run_id)
        return {
            "task": self._task_dict(task),
            "runs": [self.run_record(row.run_id) for row in rows],
            "source": _source_state(task),
        }

    def update_task(
        self,
        task_id: str,
        title: str | None = None,
        prompt: str | None = None,
        keep: bool | None = None,
    ) -> AdhocTask:
        task = self.get_task(task_id)
        if title is not None:
            task.title = title.strip() or task.title
        if keep is not None:
            task.keep = bool(keep)
        if prompt is not None and prompt.strip() != task.prompt:
            has_runs = self.session.exec(
                select(AdhocRun).where(AdhocRun.task_id == task_id)
            ).first()
            if has_runs is not None:
                raise AdhocConflictError(
                    "The prompt cannot change once the task has runs: their "
                    "comparison depends on the same prompt. Create a new task."
                )
            task.prompt = prompt.strip()
            case = self._case(task)
            case.prompt_variants = {PROMPT_VARIANT: task.prompt}
            self.session.add(case)
        task.updated_at = utc_now()
        self.session.add(task)
        self.session.commit()
        self.session.refresh(task)
        return task

    def delete_task(self, task_id: str, force: bool = False) -> None:
        """Remove the task, its runs, its copy and every file; metrics go too."""
        from harness_lab_app.run_service import RunService

        task = self.get_task(task_id)
        rows = self.session.exec(
            select(AdhocRun).where(AdhocRun.task_id == task_id)
        ).all()
        states = self._run_states([row.run_id for row in rows])
        if not force:
            blocker = _delete_blocker(rows, states)
            if blocker:
                raise AdhocConflictError(blocker)
        service = RunService(self.session)
        for row in rows:
            if states.get(row.run_id) is not None:
                service.delete_run(row.run_id, force=True)
            self.session.delete(row)
        for model, key in (
            (Case, Case.case_id),
            (TargetRepository, TargetRepository.target_id),
        ):
            for item in self.session.exec(select(model).where(key == task_id)).all():
                self.session.delete(item)
        self.session.delete(task)
        self.session.commit()
        root = get_settings().checkouts_dir
        checkout = root / _checked_task_id(task_id)
        if checkout.is_dir() and checkout.resolve().parent == root.resolve():
            shutil.rmtree(checkout, ignore_errors=True)

    def delete_unkept(self) -> dict[str, Any]:
        """Delete every task not marked kept, and say what was freed.

        A task with a run still going, or with a change applied to the folder,
        is skipped and reported, never forced: deleting it would lose the run
        or the only way to undo that change. Each task goes on its own, so one
        that fails leaves the others deleted.
        """
        from harness_lab_app.storage_service import tree_bytes

        settings = get_settings()
        deleted: list[str] = []
        skipped: list[dict[str, str]] = []
        freed = 0
        tasks = self.session.exec(
            select(AdhocTask).where(AdhocTask.keep == False)  # noqa: E712 - SQL
        ).all()
        for task in tasks:
            rows = self.session.exec(
                select(AdhocRun).where(AdhocRun.task_id == task.task_id)
            ).all()
            blocker = _delete_blocker(
                rows, self._run_states([row.run_id for row in rows])
            )
            if blocker:
                skipped.append({"task_id": task.task_id, "reason": blocker})
                continue
            size = tree_bytes(settings.checkouts_dir / task.task_id) + sum(
                tree_bytes(settings.data_runs_dir / row.run_id) for row in rows
            )
            try:
                self.delete_task(task.task_id)
            except AdhocError as exc:
                self.session.rollback()
                skipped.append({"task_id": task.task_id, "reason": str(exc)})
                continue
            deleted.append(task.task_id)
            freed += size
        return {"deleted": deleted, "skipped": skipped, "bytes_freed": freed}

    # ---- runs ----------------------------------------------------------

    def launch_run(
        self,
        task_id: str,
        harness: str,
        executor_model: str | None = None,
        executor_effort: str = "",
        max_steps: int = 1,
        harness_level: str = "repo",
        trial_skill: str = "",
        invoke_trial_skill: bool = False,
    ) -> dict[str, Any]:
        """Queue one run of the task through the ordinary run pipeline.

        ``harness_level`` picks what the agent sees (see :data:`HARNESS_LEVELS`).
        ``trial_skill`` adds one staged skill on top of any level — "their repo
        plus this skill" — and ``invoke_trial_skill`` tells the agent to use it,
        since a skill that is only available is often never opened (ADR-0041).
        """
        from harness_lab_app.run_service import RunService
        from harness_lab_app.schemas import RunConfigCreate, RunCreate

        task = self.get_task(task_id)
        source, profile = harness_condition((harness_level or "repo").strip())
        trial = (trial_skill or "").strip()
        if invoke_trial_skill and not trial:
            raise AdhocError("Pick the skill to try before asking the agent to use it.")
        steps = max(1, min(int(max_steps or 1), 5))
        case = self._case(task)
        if case.max_steps < steps:
            # The runner caps a run at its case's max_steps; the case is this
            # task's private carrier, so it follows the largest request.
            case.max_steps = steps
            self.session.add(case)
            self.session.commit()
        config = RunConfigCreate(
            case_id=task.case_id,
            prompt_variant=PROMPT_VARIANT,
            harness=harness,
            executor_model=(executor_model or None),
            executor_effort=executor_effort or "",
            max_steps=steps,
            target_repo_id=task.target_id,
            # Empty on purpose: a pinned target resolves to its pinned commit
            # without asking any remote (ADR-0042), which is the task's base.
            target_ref="",
            harness_source=source,
            skill_profile=profile,
            skills_extra=[trial] if trial else [],
            skills_invoke=[trial] if trial and invoke_trial_skill else [],
        )
        service = RunService(self.session, enqueue=self.enqueue)
        run = service.create_run(RunCreate(config=config))
        self.session.add(
            AdhocRun(
                run_id=run.run_id,
                task_id=task.task_id,
                harness_level=(harness_level or "repo").strip(),
                trial_skill_hashes=self._trial_skill_hashes([trial] if trial else []),
            )
        )
        self.session.commit()
        return self.run_record(run.run_id)

    def get_adhoc_run(self, run_id: str) -> AdhocRun:
        row = self.session.exec(
            select(AdhocRun).where(AdhocRun.run_id == run_id)
        ).first()
        if row is None:
            raise AdhocNotFoundError(f"Unknown ad hoc run: {run_id}")
        return row

    def run_record(self, run_id: str) -> dict[str, Any]:
        row = self.get_adhoc_run(run_id)
        run = self.session.exec(select(Run).where(Run.run_id == run_id)).first()
        config = (
            self.session.get(RunConfig, run.run_config_id) if run is not None else None
        )
        return {
            "run_id": row.run_id,
            "task_id": row.task_id,
            "state": str(run.state) if run else "deleted",
            "harness": config.harness if config else "",
            "executor_model": (config.executor_model or "") if config else "",
            "executor_effort": config.executor_effort if config else "",
            # What the agent saw: the repository alone, or the lab's harness at
            # a profile, plus any staged skill tried on top.
            "harness_level": row.harness_level,
            "trial_skills": list(config.skills_extra) if config else [],
            "skills_invoked": list(config.skills_invoke) if config else [],
            "trial_skill_hashes": dict(row.trial_skill_hashes or {}),
            "created_at": row.created_at.isoformat(),
            "started_at": run.started_at.isoformat()
            if run and run.started_at
            else None,
            "ended_at": run.ended_at.isoformat() if run and run.ended_at else None,
            "failure_reason": (run.failure_reason or "") if run else "",
            "input_tokens": row.input_tokens,
            "output_tokens": row.output_tokens,
            "cache_read_tokens": row.cache_read_tokens,
            "reasoning_tokens": row.reasoning_tokens,
            "billed_tokens": row.billed_tokens,
            "cost_usd": row.cost_usd,
            "estimated_cost_usd": row.estimated_cost_usd,
            "cost_source": row.cost_source,
            "billing_mode": row.billing_mode,
            "minutes": row.minutes,
            "turns": row.turns,
            "tool_calls": row.tool_calls,
            "files_changed": row.files_changed,
            "lines_added": row.lines_added,
            "lines_removed": row.lines_removed,
            "decision": row.decision,
            "decision_reason": row.decision_reason,
            "notes": row.notes,
            "apply_state": row.apply_state,
            "apply_error": row.apply_error,
            "applied_at": row.applied_at.isoformat() if row.applied_at else None,
            "files_purged": row.files_purged,
            "files_retention_days": get_settings().adhoc_retention_days,
            # What the lab can still do with the run's files.
            "has_workspace": (
                not row.files_purged
                and (get_settings().data_runs_dir / row.run_id / "workspace").is_dir()
            ),
            # The LLM judge's grade, when the operator asked for one.
            "judge_score": run.score if run and _judged(run) else None,
            "judge_notes": (run.notes or "") if run and _judged(run) else "",
        }

    def _run_states(self, run_ids: list[str]) -> dict[str, RunState]:
        if not run_ids:
            return {}
        rows = self.session.exec(
            select(Run).where(Run.run_id.in_(run_ids))  # type: ignore[attr-defined]
        ).all()
        return {run.run_id: run.state for run in rows}

    # ---- after a run ---------------------------------------------------

    def finalize_run(self, run_id: str) -> None:
        """Write the clean patch, copy the metrics, and drop the bulky files.

        Idempotent and best-effort: called by the worker right after the run,
        and again on read in case the worker was restarted in between.
        """
        if not is_adhoc_id(run_id):
            return
        row = self.session.exec(
            select(AdhocRun).where(AdhocRun.run_id == run_id)
        ).first()
        if row is None or row.files_purged:
            return
        directory = run_dir(run_id)
        archived = directory / "workspace"
        target_patch = directory / "change.patch"
        # The runner writes change.patch inside its container, where the agent
        # ran. Rebuilding here is a fallback for runs from before that, and runs
        # hardened (adhoc_git.git_untrusted) because the agent wrote this .git.
        if archived.is_dir() and not target_patch.exists():
            task = self.get_task(row.task_id)
            recorded = directory / "provisioned_commit.txt"
            diff_from = recorded.read_text().strip() if recorded.exists() else ""
            try:
                target_patch.write_bytes(
                    adhoc_git.build_change_patch(
                        archived,
                        task.base_commit,
                        diff_from,
                        adhoc_git.read_harness_paths(directory),
                    )
                )
            except Exception as exc:  # a broken change must not block cleanup
                row.apply_error = f"Could not build the change: {exc}"[:2000]
        error_file = directory / "change.error.txt"
        if not target_patch.exists() and error_file.exists():
            row.apply_error = (
                "Could not build the change: "
                + error_file.read_text(errors="replace").strip()
            )[:2000]
        if target_patch.exists():
            # The judge and the audit read diff.patch; a run trimmed by an older
            # lab lost it, and change.patch holds exactly the agent's change.
            audit_diff = directory / "diff.patch"
            if not audit_diff.exists():
                audit_diff.write_bytes(target_patch.read_bytes())
            files = adhoc_git.summarize_patch(target_patch.read_bytes(), directory)
            row.patch_files = [file.__dict__ for file in files]
            row.files_changed = len(files)
            row.lines_added = sum(file.added for file in files)
            row.lines_removed = sum(file.removed for file in files)
        self.capture_metrics(run_id, commit=False)
        row.updated_at = utc_now()
        self.session.add(row)
        self.session.commit()
        self._reindex_artifacts(run_id)

    def _reindex_artifacts(self, run_id: str) -> None:
        """Artifact rows follow the files: none may point at a deleted one."""
        from harness_lab_app.artifact_service import ArtifactService

        try:
            ArtifactService(self.session).index_run(run_id)
        except Exception:  # indexing is a convenience, never a failure
            self.session.rollback()

    def capture_metrics(self, run_id: str, commit: bool = True) -> None:
        """Copy cost and effort from the run's validation (or usage.json) once."""
        from harness_lab_app.api_price_estimate import with_estimate
        from harness_lab_app.validation import collect_cost, collect_effort

        row = self.session.exec(
            select(AdhocRun).where(AdhocRun.run_id == run_id)
        ).first()
        if row is None or row.metrics_captured_at is not None:
            return
        run = self.session.exec(select(Run).where(Run.run_id == run_id)).first()
        if run is None or run.state in LIVE_RUN_STATES:
            return
        validation = self.session.exec(
            select(Validation).where(Validation.run_id == run_id)
        ).first()
        directory = get_settings().data_runs_dir / run_id
        cost = dict(validation.cost) if validation and validation.cost else {}
        if not cost.get("available"):
            cost = collect_cost(directory)
        effort = dict(validation.effort) if validation and validation.effort else {}
        if not effort:
            effort = collect_effort(directory, run.interventions)
        if not cost.get("available") and not effort:
            return
        config = self.session.get(RunConfig, run.run_config_id)
        cost = with_estimate(cost, config.executor_model if config else None)
        row.input_tokens = cost.get("input_tokens")
        row.output_tokens = cost.get("output_tokens")
        row.cache_read_tokens = cost.get("cache_read_tokens")
        row.cache_creation_tokens = cost.get("cache_creation_tokens")
        row.reasoning_tokens = cost.get("reasoning_tokens")
        row.billed_tokens = cost.get("billed_tokens")
        row.cost_usd = cost.get("cost_usd")
        row.estimated_cost_usd = cost.get("estimated_cost_usd")
        row.cost_source = str(cost.get("cost_source") or "")
        row.billing_mode = str(cost.get("billing_mode") or "")
        row.minutes = cost.get("time_minutes")
        row.turns = effort.get("turns")
        row.tool_calls = effort.get("tool_calls")
        row.metrics_captured_at = utc_now()
        self.session.add(row)
        if commit:
            self.session.commit()

    # ---- review --------------------------------------------------------

    def request_judge(self, run_id: str) -> dict[str, Any]:
        """Mark the run for the LLM judge; the worker grades it (ADR-0095).

        On demand, not after every run: it costs tokens, and an ad hoc change
        is the operator's call first. The judge reads the prompt, the diff and
        the verification results against ``ADHOC_RUBRIC``.
        """
        row = self.get_adhoc_run(run_id)
        run = self.session.exec(select(Run).where(Run.run_id == run_id)).first()
        if run is None or run.state in LIVE_RUN_STATES:
            raise AdhocConflictError(
                "The run is still going: judge it once it finishes."
            )
        if row.files_purged:
            raise AdhocConflictError(
                "This run's files were purged: nothing left to judge."
            )
        case = self._case(self.get_task(row.task_id))
        if not case.scoring_rubric:
            # Tasks created before the rubric existed.
            case.scoring_rubric = ADHOC_RUBRIC
            self.session.add(case)
        if run.state == RunState.completed and not _judged(run):
            raise AdhocConflictError("This run already carries a score.")
        # The judge only grades runs waiting for a score (or re-grades its own).
        if run.state != RunState.completed:
            run.state = RunState.awaiting_manual_score
            self.session.add(run)
        self.session.commit()
        return self.run_record(run_id)

    def patch(self, run_id: str) -> dict[str, Any]:
        row = self.get_adhoc_run(run_id)
        self.finalize_run(run_id)
        task = self.get_task(row.task_id)
        path = patch_path(run_id)
        if not path.exists():
            return {
                "patch": "",
                "files": row.patch_files,
                "truncated": False,
                "purged": row.files_purged,
                "apply_command": "",
            }
        raw = path.read_bytes()
        # For display only: the bytes on disk are what gets applied.
        text = raw[:_PATCH_PREVIEW_BYTES].decode("utf-8", errors="replace")
        return {
            "patch": text,
            "files": row.patch_files,
            "truncated": len(raw) > _PATCH_PREVIEW_BYTES,
            "purged": False,
            "apply_command": apply_command(task, run_id),
        }

    def decide(
        self, run_id: str, decision: str, reason: str = "", notes: str = ""
    ) -> dict[str, Any]:
        if decision not in DECISIONS:
            raise AdhocError(f"decision must be one of {sorted(DECISIONS)}")
        row = self.get_adhoc_run(run_id)
        run = self.session.exec(select(Run).where(Run.run_id == run_id)).first()
        if run is not None and run.state in LIVE_RUN_STATES:
            raise AdhocConflictError("The run is still going: decide once it finishes.")
        if decision == "discarded" and row.apply_state in (
            {"applied"} | LIVE_APPLY_STATES
        ):
            raise AdhocConflictError(
                "This change is applied to your folder. Undo it before discarding."
            )
        self.capture_metrics(run_id)
        row.decision = decision
        row.decision_reason = reason.strip()
        row.notes = notes.strip()
        row.decided_at = utc_now()
        row.updated_at = utc_now()
        self.session.add(row)
        self.session.commit()
        # A rejected or discarded change has nothing left to apply: its files go
        # now, and the metrics row is what remains of it.
        if decision in {"rejected", "discarded"}:
            self.purge_run_files(run_id)
        return self.run_record(run_id)

    def purge_run_files(self, run_id: str) -> int:
        """Delete every file of an ad hoc run; the metrics row stays. Bytes freed."""
        from harness_lab_app.storage_service import tree_bytes

        row = self.get_adhoc_run(run_id)
        if row.apply_state in LIVE_APPLY_STATES:
            return 0
        self.capture_metrics(run_id)
        directory = run_dir(run_id)
        freed = 0
        if row.apply_state == "applied":
            # Undo needs the patch: an applied change keeps its few KB and
            # loses everything else (the workspace copy is the bulk).
            if directory.is_dir():
                for child in directory.iterdir():
                    if child.name == "change.patch":
                        continue
                    freed += tree_bytes(child)
                    if child.is_dir() and not child.is_symlink():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink(missing_ok=True)
        else:
            freed = tree_bytes(directory)
            shutil.rmtree(directory, ignore_errors=True)
        live = get_settings().workspace_dir / run_id
        if live.is_dir():
            freed += tree_bytes(live)
            shutil.rmtree(live, ignore_errors=True)
        row.files_purged = True
        row.updated_at = utc_now()
        self._reindex_artifacts(run_id)
        run = self.session.exec(select(Run).where(Run.run_id == run_id)).first()
        if run is not None:
            run.purged_at = utc_now()
            self.session.add(run)
        self.session.add(row)
        self.session.commit()
        return freed

    # ---- apply ---------------------------------------------------------

    def apply_check(self, run_id: str) -> dict[str, Any]:
        """The same checks the apply container runs, read-only, from the API.

        The API mounts the folder read-only, which is all a check needs. The
        answer can still change before Apply is pressed, so the container checks
        again before it writes.
        """
        from dataclasses import asdict

        from harness_lab_app.local_sources_service import resolve

        row = self.get_adhoc_run(run_id)
        task = self.get_task(row.task_id)
        path = patch_path(run_id)
        # The patch itself is the test: a purged *applied* run keeps it for undo.
        if not path.exists():
            return {
                **asdict(adhoc_git.ApplyCheck(can_apply=False)),
                "blockers": [
                    "This run's files were purged: there is no change left to apply."
                ],
            }
        result = adhoc_git.check_apply(
            resolve(task.source_path),
            path.read_bytes(),
            task.base_commit,
        )
        if row.apply_state == "applied":
            result.can_apply = False
            result.blockers.insert(0, "This change is already applied.")
        return asdict(result)

    def begin_apply(self, run_id: str, undo: bool = False) -> AdhocRun:
        """Mark the run applying/undoing; refused when another apply holds the folder."""
        row = self.get_adhoc_run(run_id)
        task = self.get_task(row.task_id)
        run = self.session.exec(select(Run).where(Run.run_id == run_id)).first()
        if run is not None and run.state in LIVE_RUN_STATES:
            raise AdhocConflictError("The run is still going.")
        if not patch_path(run_id).exists():
            raise AdhocConflictError("This run's files were purged: nothing to apply.")
        if undo and row.apply_state != "applied":
            raise AdhocConflictError("Only an applied change can be undone.")
        if not undo and row.apply_state == "applied":
            raise AdhocConflictError("This change is already applied.")
        busy = self.session.exec(
            select(AdhocRun)
            .where(AdhocRun.task_id.in_(self._task_ids_for_folder(task.source_path)))  # type: ignore[attr-defined]
            .where(AdhocRun.apply_state.in_(list(LIVE_APPLY_STATES)))  # type: ignore[attr-defined]
        ).first()
        if busy is not None:
            raise AdhocConflictError(
                f"Another change ({busy.run_id}) is being applied to this folder. Wait for it."
            )
        row.apply_state = "undoing" if undo else "applying"
        row.apply_error = ""
        row.updated_at = utc_now()
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def _task_ids_for_folder(self, source_path: str) -> list[str]:
        return list(
            self.session.exec(
                select(AdhocTask.task_id).where(AdhocTask.source_path == source_path)
            ).all()
        )

    def finish_apply(
        self,
        run_id: str,
        undo: bool,
        ok: bool,
        error: str = "",
        applied_hashes: dict[str, str] | None = None,
    ) -> None:
        row = self.get_adhoc_run(run_id)
        if ok:
            row.apply_state = "undone" if undo else "applied"
            row.applied_at = None if undo else utc_now()
            row.applied_hashes = {} if undo else dict(applied_hashes or {})
            row.apply_error = ""
            if not undo and row.decision == "pending":
                row.decision = "approved"
                row.decided_at = utc_now()
        else:
            # A refused undo leaves the change applied; a refused apply leaves
            # the folder as it was.
            row.apply_state = "applied" if undo else "failed"
            row.apply_error = error[:2000]
        row.updated_at = utc_now()
        self.session.add(row)
        self.session.commit()

    # ---- helpers -------------------------------------------------------

    def _case(self, task: AdhocTask) -> Case:
        case = self.session.exec(
            select(Case).where(Case.case_id == task.case_id)
        ).first()
        if case is None:
            raise AdhocNotFoundError(f"The task's case {task.case_id} is missing.")
        return case

    @staticmethod
    def _task_dict(task: AdhocTask) -> dict[str, Any]:
        body = task.model_dump()
        body.pop("id", None)
        for key in ("created_at", "updated_at"):
            value = body.get(key)
            if isinstance(value, datetime):
                body[key] = value.isoformat()
        return body
