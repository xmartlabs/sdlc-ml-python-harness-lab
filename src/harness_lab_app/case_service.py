from __future__ import annotations

import json
from typing import TYPE_CHECKING

import yaml
from sqlmodel import Session, select

from harness_lab_app.config import get_settings
from harness_lab_app.models import Case, CaseStatus, CaseSyncState, CaseType, utc_now
from harness_lab_app.skill_import import safe_relpath

if TYPE_CHECKING:
    from pathlib import Path

    from harness_lab_app.schemas import CaseCreate

# Treatment-arm preamble for the harness A/B (variant ``swe_harness``): directs
# the agent through the repo's governed loop before the actual task. Shared by
# the SWE-bench (ADR-0009) and SWE-bench Pro prompt builders — the paths resolve
# from the agent's working directory (the runner launches with cwd=REPO_ROOT).
_HARNESS_PREAMBLE = (
    "# Engineering harness (read before you touch code)\n\n"
    "You are operating inside a governed repository. Before editing, ground "
    "yourself in its harness — this is mandatory, not optional:\n\n"
    "1. Read the governance docs at the repo root:\n"
    "   - `.github/architecture.md`\n"
    "   - `.github/standards.md`\n"
    "   - `.github/domain-boundaries.md`\n"
    "2. Read project memory for prior context:\n"
    "   - `memory/context.md`\n"
    "   - `memory/learnings.md`\n"
    "3. Follow the working loop in `OPENCODE.md`: Ground → Plan → "
    "Delegate → Verify → Compound.\n\n"
    "Drive the fix with the repo's skills, not ad-hoc improvisation:\n"
    "- Use the **systematic_debugging** skill to reproduce → isolate → "
    "hypothesize → fix → verify (no blind edits, no thrashing).\n"
    "- Use the **verify_changes** skill to confirm the change before you "
    "consider it done.\n\n"
    "The concrete SWE-bench task follows. The subject repository is a real "
    "third-party library checked out under your working directory — apply "
    "the harness to *how* you solve it, but keep the fix minimal and scoped "
    "to that library's source, exactly as the task states.\n\n"
    "---\n\n"
)


# Conductor-arm preamble (variant ``swe_conductor``): the tetris conductor
# methodology adapted to SWE tasks — a planner model orchestrates small
# verifiable steps, the executor model implements them. The opencode conductor
# plugin + --planner-model provide the two-model wiring; this prompt provides
# the roles.
_CONDUCTOR_PREAMBLE = (
    "# Conductor mode (planner orchestrates, executor implements)\n\n"
    "You are the **conductor**. You do not write the final code yourself. You:\n"
    "1. Hold the goal: resolve the GitHub issue below — make the target "
    "failing test(s) pass with **no regressions**.\n"
    "2. Ground first: read the failing tests' source and locate the relevant "
    "code before planning.\n"
    "3. Break the fix into the smallest independently-verifiable steps.\n"
    "4. Hand **one** step at a time to the executor as a precise, "
    "self-contained instruction (which file, what change, how to check). "
    "Wait for the result.\n"
    "5. Review each result. If correct, advance; if not, send a tighter "
    "correction — never expand scope.\n"
    "6. Keep each instruction within a small output budget; never ask for a "
    "full-file rewrite.\n\n"
    "## Executor role (executor model)\n\n"
    "You receive one precise step at a time. Implement exactly that step with "
    "a minimal targeted edit, report the result, and stop. Do not anticipate "
    "later steps.\n\n"
    "---\n\n"
)


class CaseExistsError(ValueError):
    """A case with that id is already in the catalog."""


def resolve_prompt_variant_text(case: Case, variant: str) -> str | None:
    """The literal prompt text for a variant, whatever form the case stores it in.

    Cases created from the web (and every SWE-bench import) store the prompt
    *text* in ``prompt_variants``. Cases imported from a ``benchmarks/<id>/
    task.yaml`` store a repo-relative *path* instead, because that is what the
    YAML says (mirrored by the runner in ``tasks._case_prompt_text``, which
    falls back to the raw path string on failure — this function instead
    returns ``None`` so a caller like the API can 404 rather than display a
    path as if it were content).

    ``case.prompt_variants`` is editable via the case editor, so the stored
    value is treated as untrusted input: ``safe_relpath`` rejects traversal
    and absolute paths, and the resolved path must still land inside
    ``repo_root``.
    """
    raw = case.prompt_variants.get(variant)
    if raw is None:
        return None
    if "\n" in raw:
        return raw
    relpath = safe_relpath(raw)
    if relpath is None:
        return None
    repo_root = get_settings().repo_root.resolve()
    candidate = (repo_root / relpath).resolve()
    if candidate != repo_root and repo_root not in candidate.parents:
        return None
    try:
        if candidate.is_file():
            return candidate.read_text()
    except OSError:
        return None
    return None


class CaseService:
    def __init__(self, session: Session):
        self.session = session

    def list_cases(self) -> list[Case]:
        statement = (
            select(Case).where(Case.status != CaseStatus.deleted).order_by(Case.case_id)
        )
        return list(self.session.exec(statement))

    def get_case(self, case_id: str) -> Case:
        case = self.session.exec(select(Case).where(Case.case_id == case_id)).first()
        if case is None:
            raise ValueError(f"Case not found: {case_id}")
        return case

    def list_case_run_ids(self, case_id: str) -> list[str]:
        settings = get_settings()
        case_dir = settings.benchmarks_dir / case_id
        if not case_dir.exists():
            fallback = self._find_case_dir_by_task_id(case_id, settings.benchmarks_dir)
            if fallback is None:
                return []
            case_dir = fallback

        runs_path = case_dir / "runs.yaml"
        if not runs_path.exists():
            return []

        raw = yaml.safe_load(runs_path.read_text()) or {}
        runs = raw.get("runs", [])
        if not isinstance(runs, list):
            return []

        run_ids: list[str] = []
        for item in runs:
            if isinstance(item, dict) and isinstance(item.get("run_id"), str):
                run_ids.append(item["run_id"])
        return run_ids

    def _find_case_dir_by_task_id(
        self, case_id: str, benchmarks_dir: Path
    ) -> Path | None:
        for child in benchmarks_dir.iterdir():
            if not child.is_dir():
                continue
            task_path = child / "task.yaml"
            if not task_path.exists():
                continue
            raw = yaml.safe_load(task_path.read_text()) or {}
            if raw.get("task_id") == case_id:
                return child
        return None

    def create_case(self, data: CaseCreate) -> Case:
        # Named refusal, not an IntegrityError 500: "already exists" is an
        # ordinary answer (the audit flow re-offers candidates a past session
        # already accepted), and the caller needs to tell it apart from a real
        # failure to treat it as the non-event it is.
        existing = self.session.exec(
            select(Case).where(Case.case_id == data.case_id)
        ).first()
        if existing is not None:
            raise CaseExistsError(f"Case already exists: {data.case_id}")
        case = Case(**data.model_dump())
        self.session.add(case)
        self.session.commit()
        self.session.refresh(case)
        return case

    def update_case(self, case_id: str, data: CaseCreate) -> Case:
        case = self.get_case(case_id)
        payload = data.model_dump()
        for field_name, value in payload.items():
            setattr(case, field_name, value)
        case.updated_at = utc_now()
        # "Modified since sync" only means something for a case that was synced
        # once: a case that lives only in the DB stays `not_synced`, otherwise
        # every edit raises a drift alarm about a YAML file that never existed.
        if case.sync_state == CaseSyncState.synced:
            case.sync_state = CaseSyncState.modified_since_sync
        self.session.add(case)
        self.session.commit()
        self.session.refresh(case)
        return case

    def clone_case(self, source_case_id: str, new_case_id: str, new_name: str) -> Case:
        source = self.get_case(source_case_id)
        clone = Case(
            case_id=new_case_id,
            name=new_name,
            description=source.description,
            type=source.type,
            status=CaseStatus.active,
            sync_state=CaseSyncState.not_synced,
            tags=list(source.tags),
            subject_repo=source.subject_repo,
            base_commit=source.base_commit,
            prompt_variants=dict(source.prompt_variants),
            step_prompts=list(source.step_prompts),
            success_criteria=dict(source.success_criteria),
            scoring_rubric=source.scoring_rubric,
            constraints=list(source.constraints),
            validation_commands=list(source.validation_commands),
            timeout_seconds=source.timeout_seconds,
            max_steps=source.max_steps,
            allowed_files=list(source.allowed_files),
            metadata_json=dict(source.metadata_json),
        )
        self.session.add(clone)
        self.session.commit()
        self.session.refresh(clone)
        return clone

    def deactivate_case(self, case_id: str) -> Case:
        case = self.get_case(case_id)
        case.status = CaseStatus.inactive
        case.updated_at = utc_now()
        self.session.add(case)
        self.session.commit()
        self.session.refresh(case)
        return case

    def delete_case(self, case_id: str) -> Case:
        case = self.get_case(case_id)
        case.status = CaseStatus.deleted
        case.updated_at = utc_now()
        self.session.add(case)
        self.session.commit()
        self.session.refresh(case)
        return case

    def seed_benchmark_cases(self) -> list[Case]:
        """Seed every ``benchmarks/*/task.yaml``, refreshing on re-seed.

        The authored corpus is the source of truth for its cases (ADR-0025):
        a fresh database gets all of them at startup, and an existing one has
        its variant map and graded commands rebuilt from the file — a stale
        variant list would keep offering prompts the benchmark no longer
        declares. Only those two fields are rebuilt; name, description, tags
        and status stay as the operator left them, because those are the
        fields the case editor exists to change.
        """
        root = get_settings().repo_root / "benchmarks"
        seeded: list[Case] = []
        for task_path in sorted(root.glob("*/task.yaml")):
            raw = yaml.safe_load(task_path.read_text()) or {}
            case_id = raw.get("task_id")
            if not case_id:
                continue
            existing = self.session.exec(
                select(Case).where(Case.case_id == case_id)
            ).first()
            if existing is None:
                seeded.append(self.import_task_yaml(task_path))
                continue
            existing.prompt_variants = dict(raw.get("prompts", {}))
            if raw.get("validation_commands"):
                existing.validation_commands = [
                    str(cmd) for cmd in raw["validation_commands"]
                ]
            existing.updated_at = utc_now()
            self.session.add(existing)
            self.session.commit()
            self.session.refresh(existing)
            seeded.append(existing)
        return seeded

    def import_task_yaml(self, task_path: Path) -> Case:
        raw = yaml.safe_load(task_path.read_text())
        prompts = dict(raw.get("prompts", {}))
        case_type = self._case_type_from_task(raw.get("category", ""))
        case = Case(
            case_id=raw["task_id"],
            name=raw.get("task_id", raw["task_id"]),
            description=str(raw.get("description", "")).strip(),
            type=case_type,
            status=CaseStatus.active,
            sync_state=CaseSyncState.imported_from_yaml,
            tags=list(raw.get("tags", [])),
            subject_repo=raw.get("subject_repo"),
            base_commit=raw.get("base_commit"),
            prompt_variants=prompts,
            success_criteria=dict(raw.get("success_criteria", {})),
            scoring_rubric=str(raw.get("scoring_rubric", "")),
            constraints=list(raw.get("constraints", [])),
            # Prefer executable validation_commands; fall back to the prose
            # validation_note only as a manual reminder.
            validation_commands=(
                [str(cmd) for cmd in raw["validation_commands"]]
                if raw.get("validation_commands")
                else (
                    [str(raw.get("validation_note", "")).strip()]
                    if raw.get("validation_note")
                    else []
                )
            ),
            max_steps=int(raw.get("target_level", 1)),
            metadata_json={"source_path": str(task_path)},
        )
        self.session.add(case)
        self.session.commit()
        self.session.refresh(case)
        return case

    @staticmethod
    def _build_swebench_prompt(spec: dict) -> str:
        """Bare SWE-bench task prompt (variant ``swe_default``).

        Self-contained: it frames the issue and the pass/no-regress objective and
        says nothing about this repo's engineering harness. This is the control
        arm — running it measures the model *without* governance or skills, which
        is why its harness-attribution coverage is expected to be near zero.
        """
        f2p = spec["fail_to_pass"]
        repo = spec["repo"]
        test_hint = f"python -m pytest {' '.join(f2p)}"
        return (
            f"# Task: resolve a GitHub issue in {repo}\n\n"
            f"The repository `{repo}` is checked out at commit "
            f"`{spec['base_commit'][:12]}` in your working directory. A failing "
            f"test that encodes the desired behavior has already been added to the "
            f"test suite.\n\n"
            f"## Issue\n{spec['problem_statement'].strip()}\n\n"
            f"## Your goal\n"
            f"Make the failing test(s) pass **without breaking any currently-passing "
            f"test**. The target failing test(s):\n"
            + "\n".join(f"  - {t}" for t in f2p)
            + f"\n\nRun the target test with:\n    {test_hint}\n\n"
            f"Work incrementally: plan → locate the relevant source → implement a "
            f"minimal fix → run the target test → run the surrounding test file to "
            f"check for regressions. Edit the **library source**, not the test files."
        )

    @staticmethod
    def _build_swebench_harness_prompt(spec: dict) -> str:
        """Harness-governed SWE-bench prompt (variant ``swe_harness``).

        The treatment arm for the A/B: same objective as ``swe_default`` wrapped
        in a preamble that directs the agent through this repo's governed loop —
        read the governance docs and memory, then drive the fix with the
        ``systematic_debugging`` and ``verify_changes`` skills. The paths below
        resolve from the agent's working directory (the runner launches with
        ``cwd=REPO_ROOT``; the subject repo lives under ``workspace/<run_id>/``),
        so this is exactly the "reading surface" the attribution panel scores.
        """
        return _HARNESS_PREAMBLE + CaseService._build_swebench_prompt(spec)

    def import_swebench_instance(self, spec: dict) -> Case:
        """Create a Case from a normalized SWE-bench instance spec (no network).

        `spec` keys: instance_id, repo, base_commit, problem_statement,
        test_patch, fail_to_pass (list), pass_to_pass (list), version, repo_url,
        python, install_pins (list). The SWE-bench-specific payload is stored in
        `metadata_json` (JSON-encoded), so no schema migration is needed; the
        worker reads it to write case_spec.json for the runner (ADR-0009).
        """
        instance_id = spec["instance_id"]
        existing = self.session.exec(
            select(Case).where(Case.case_id == instance_id)
        ).first()
        if existing is not None:
            return existing
        case = Case(
            case_id=instance_id,
            name=instance_id,
            description=spec["problem_statement"].strip()[:500],
            type=CaseType.swe_bench_instance,
            status=CaseStatus.active,
            sync_state=CaseSyncState.imported_from_yaml,
            tags=["swe-bench", spec["repo"]],
            subject_repo=spec["repo"],
            base_commit=spec["base_commit"],
            prompt_variants={
                "swe_default": self._build_swebench_prompt(spec),
                "swe_harness": self._build_swebench_harness_prompt(spec),
            },
            success_criteria={
                "fail_to_pass": json.dumps(spec["fail_to_pass"]),
                "pass_to_pass": json.dumps(spec["pass_to_pass"]),
            },
            constraints=["Edit library source only; do not modify test files."],
            # Validation runs in the worker's container; the helper reads
            # case_spec.json, installs the archived repo and runs the subset.
            validation_commands=[
                "python /app/scripts/swebench_validate.py --run {run_id}"
            ],
            max_steps=int(spec.get("max_steps", 10)),
            metadata_json={
                "instance_id": instance_id,
                "repo_url": spec["repo_url"],
                "version": str(spec.get("version", "")),
                "python": spec.get("python", "3.11"),
                "install_pins": json.dumps(spec.get("install_pins", [])),
                "test_patch": spec["test_patch"],
                "fail_to_pass": json.dumps(spec["fail_to_pass"]),
                "pass_to_pass": json.dumps(spec["pass_to_pass"]),
            },
        )
        self.session.add(case)
        self.session.commit()
        self.session.refresh(case)
        return case

    @staticmethod
    def _build_swebench_pro_prompt(spec: dict) -> str:
        """Bare SWE-bench Pro task prompt (variant ``swe_default``).

        Same control-arm role as ``_build_swebench_prompt``, extended with the
        Pro-only ``requirements`` and ``interface`` sections (the official
        scaffold feeds both to the agent) and a test-environment note: the
        suite only runs inside the instance's prebuilt Docker image, so the
        in-loop check is a sidecar ``docker run``, not host pytest.
        """
        f2p = spec["fail_to_pass"]
        repo = spec["repo"]
        image = spec["dockerhub_image"]
        test_hint = f"python -m pytest {' '.join(f2p[:3])}"
        parts = [
            f"# Task: resolve a GitHub issue in {repo}\n\n"
            f"The repository `{repo}` is checked out at commit "
            f"`{spec['base_commit'][:12]}` in your working directory. Failing "
            f"tests that encode the desired behavior have already been added to "
            f"the test suite.\n\n"
            f"## Issue\n{spec['problem_statement'].strip()}\n"
        ]
        if spec.get("requirements", "").strip():
            parts.append(f"\n## Requirements\n{spec['requirements'].strip()}\n")
        if spec.get("interface", "").strip():
            parts.append(f"\n## Interface\n{spec['interface'].strip()}\n")
        parts.append(
            "\n## Your goal\n"
            "Make the failing test(s) pass **without breaking any "
            "currently-passing test**. The target failing test(s):\n"
            + "\n".join(f"  - {t}" for t in f2p)
            + "\n\n## Test environment\n"
            f"This project's test environment lives in a prebuilt Docker image "
            f"(`{image}`), with the repo at `/app` inside it. If `docker` is "
            f"available, run the target tests against your edits with:\n\n"
            f'    docker run --rm -v "$(pwd)/<workspace dir>":/app {image} '
            f"bash -lc 'cd /app && {test_hint}'\n\n"
            f"If `docker` is NOT available: this sandbox does **not** have the "
            f"project's dependencies and cannot run its test suite. Do NOT "
            f"spend turns installing packages, building the environment, or "
            f"searching for interpreters — that effort is wasted. Read the "
            f"failing tests' source and reason about correctness instead; the "
            f"official containerized validation runs automatically after you "
            f"finish.\n\n"
            "Work incrementally: plan → locate the relevant source → implement "
            "a minimal fix → verify. Edit the **project source**, not the test "
            "files."
        )
        return "".join(parts)

    @staticmethod
    def _build_swebench_pro_harness_prompt(spec: dict) -> str:
        """Harness-governed Pro prompt (variant ``swe_harness``) — treatment arm."""
        return _HARNESS_PREAMBLE + CaseService._build_swebench_pro_prompt(spec)

    @staticmethod
    def _build_swebench_pro_conductor_prompt(spec: dict) -> str:
        """Conductor-orchestrated Pro prompt (variant ``swe_conductor``)."""
        return _CONDUCTOR_PREAMBLE + CaseService._build_swebench_pro_prompt(spec)

    @staticmethod
    def _swebench_pro_display(spec: dict, polish: bool) -> tuple[str, str]:
        """Human-readable (name, description) for a Pro instance.

        The raw problem statements are issue-template mush; the heuristic
        title/summary extraction always works, and with ``polish`` a single
        best-effort gateway call rewrites the description into clean prose
        (silently falling back to the heuristic when no gateway is available).
        """
        from harness_lab_app.swebench_pro_client import title_and_summary

        title, summary = title_and_summary(spec["problem_statement"])
        name = title or spec["instance_id"]
        description = ""
        if polish:
            from harness_lab_app.case_summary import polish_case_description

            description = (
                polish_case_description(title, spec["problem_statement"]) or ""
            )
        if not description:
            description = summary or spec["problem_statement"].strip()[:500]
        return name, description

    def import_swebench_pro_instance(self, spec: dict, polish: bool = False) -> Case:
        """Create a Case from a normalized SWE-bench Pro spec.

        `spec` is the dict produced by ``swebench_pro_client.normalize_instance``.
        Mirrors ``import_swebench_instance``: the Pro payload (docker image,
        run_script/parser assets, test sets) is JSON-encoded into
        ``metadata_json`` — no schema migration; the worker stages it as
        case_spec.json for the docker-based provision/validate helpers
        (docs/design-swe-bench-pro-lane.md). ``polish`` enables the one-call
        LLM cleanup of name/description (network; API/CLI imports use it).
        """
        instance_id = spec["instance_id"]
        existing = self.session.exec(
            select(Case).where(Case.case_id == instance_id)
        ).first()
        if existing is not None:
            # Re-import = refresh: prompts are generated deterministically from
            # the spec, so rebuild them (picks up prompt-template improvements);
            # display fields refresh only for pre-cleanup imports.
            existing.prompt_variants = {
                "swe_default": self._build_swebench_pro_prompt(spec),
                "swe_harness": self._build_swebench_pro_harness_prompt(spec),
                "swe_conductor": self._build_swebench_pro_conductor_prompt(spec),
            }
            if existing.name == existing.case_id:
                name, description = self._swebench_pro_display(spec, polish)
                existing.name = name
                existing.description = description
            existing.updated_at = utc_now()
            self.session.add(existing)
            self.session.commit()
            self.session.refresh(existing)
            return existing
        name, description = self._swebench_pro_display(spec, polish)
        case = Case(
            case_id=instance_id,
            name=name,
            description=description,
            type=CaseType.swe_pro_instance,
            status=CaseStatus.active,
            sync_state=CaseSyncState.imported_from_yaml,
            tags=["swe-bench-pro", spec["repo"], spec["repo_language"]],
            subject_repo=spec["repo"],
            base_commit=spec["base_commit"],
            prompt_variants={
                "swe_default": self._build_swebench_pro_prompt(spec),
                "swe_harness": self._build_swebench_pro_harness_prompt(spec),
                "swe_conductor": self._build_swebench_pro_conductor_prompt(spec),
            },
            success_criteria={
                "fail_to_pass": json.dumps(spec["fail_to_pass"]),
                "pass_to_pass": json.dumps(spec["pass_to_pass"]),
            },
            constraints=["Edit project source only; do not modify test files."],
            # Validation runs in the worker (docker access): containerized
            # run_script + parser against the archived workspace.
            validation_commands=[
                "python /app/scripts/swebench_pro_validate.py --run {run_id}"
            ],
            max_steps=int(spec.get("max_steps", 10)),
            metadata_json={
                "instance_id": instance_id,
                "repo_language": spec["repo_language"],
                "dockerhub_image": spec["dockerhub_image"],
                "requirements": spec.get("requirements", ""),
                "interface": spec.get("interface", ""),
                "test_patch": spec["test_patch"],
                "fail_to_pass": json.dumps(spec["fail_to_pass"]),
                "pass_to_pass": json.dumps(spec["pass_to_pass"]),
                "selected_test_files_to_run": json.dumps(
                    spec.get("selected_test_files_to_run", [])
                ),
                "before_repo_set_cmd": spec.get("before_repo_set_cmd", ""),
                "run_script": spec["run_script"],
                "parser": spec["parser"],
            },
        )
        self.session.add(case)
        self.session.commit()
        self.session.refresh(case)
        return case

    def sync_case_to_yaml(self, case_id: str) -> Path:
        case = self.get_case(case_id)
        settings = get_settings()
        case_dir = settings.benchmarks_dir / case.case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        task_path = case_dir / "task.yaml"

        task_data = {
            "task_id": case.case_id,
            "version": "1",
            "status": "active" if case.status == CaseStatus.active else "inactive",
            "category": self._category_for_case_type(case.type),
            "tags": case.tags,
            "description": case.description,
            "subject_repo": case.subject_repo,
            "base_commit": case.base_commit,
            "target_level": case.max_steps,
            "complexity": case.metadata_json.get("complexity", "medium"),
            "validation": "manual",
            "validation_note": case.validation_commands[0]
            if case.validation_commands
            else "",
            "constraints": case.constraints,
            "success_criteria": case.success_criteria,
            "prompts": case.prompt_variants,
            "experiment_matrix": str(case_dir / "runs.yaml"),
            "scoring_rubric": str(case_dir / "scoring.md"),
            "results_table": str(case_dir / "results.csv"),
            "report": case.metadata_json.get("report", ""),
        }
        task_path.write_text(
            yaml.safe_dump(task_data, sort_keys=False, allow_unicode=False)
        )

        for prompt_name, prompt_text in case.prompt_variants.items():
            prompt_path = case_dir / f"prompt_{prompt_name}.md"
            prompt_path.write_text(prompt_text)

        case.sync_state = CaseSyncState.synced
        case.updated_at = utc_now()
        self.session.add(case)
        self.session.commit()
        self.session.refresh(case)
        return task_path

    def _category_for_case_type(self, case_type: CaseType) -> str:
        match case_type:
            case CaseType.single_prompt:
                return "single-prompt"
            case CaseType.multi_step:
                return "multi-step"
            case CaseType.build_from_scratch:
                return "game-build"
            case CaseType.bugfix:
                return "bugfix"
            case CaseType.repo_task:
                return "repo-task"
            case CaseType.swe_bench_instance:
                return "swe-bench"
            case CaseType.swe_pro_instance:
                return "swe-bench-pro"

    def _case_type_from_task(self, category: str) -> CaseType:
        if category == "single-prompt":
            return CaseType.single_prompt
        if category == "multi-step":
            return CaseType.multi_step
        if category == "bugfix":
            return CaseType.bugfix
        if category == "repo-task":
            return CaseType.repo_task
        if category == "swe-bench":
            return CaseType.swe_bench_instance
        if category == "swe-bench-pro":
            return CaseType.swe_pro_instance
        return CaseType.build_from_scratch
