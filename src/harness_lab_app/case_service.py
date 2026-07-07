from __future__ import annotations

from typing import TYPE_CHECKING

import yaml
from sqlmodel import Session, select

from harness_lab_app.config import get_settings
from harness_lab_app.models import Case, CaseStatus, CaseSyncState, CaseType, utc_now

if TYPE_CHECKING:
    from pathlib import Path

    from harness_lab_app.schemas import CaseCreate


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

    def seed_tetris_case(self) -> Case:
        existing = self.session.exec(
            select(Case).where(Case.case_id == "tetris-v1")
        ).first()
        if existing is not None:
            return existing
        settings = get_settings()
        task_path = settings.repo_root / "benchmarks" / "tetris" / "task.yaml"
        return self.import_task_yaml(task_path)

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
            validation_commands=[str(raw.get("validation_note", "")).strip()]
            if raw.get("validation_note")
            else [],
            max_steps=int(raw.get("target_level", 1)),
            metadata_json={"source_path": str(task_path)},
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
        return CaseType.build_from_scratch
