"""Persists structured harness attribution for a run (pure log parsing)."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from sqlmodel import Session, select

from harness_lab_app.attribution import (
    build_attribution,
    build_attribution_from_events,
    read_step_events,
    read_step_logs,
)
from harness_lab_app.config import get_settings
from harness_lab_app.governance import reading_surface, resolve_surface
from harness_lab_app.models import Attribution, utc_now

if TYPE_CHECKING:
    from pathlib import Path

# opencode logs each shell command as `permission=bash pattern="<command>"`.
_OPENCODE_BASH_RE = re.compile(r'permission=bash pattern="((?:[^"\\]|\\.)*)"')


def _claude_bash_commands(lines: list[str]) -> list[str]:
    """Every Bash command a claude step ran, from its stream-json events."""
    commands: list[str] = []
    for raw in lines:
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "assistant":
            continue
        for block in (event.get("message") or {}).get("content", []):
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("name") == "Bash"
            ):
                commands.append(str((block.get("input") or {}).get("command", "")))
    return commands


class AttributionService:
    def __init__(self, session: Session):
        self.session = session

    def get_attribution(self, run_id: str) -> Attribution | None:
        return self.session.exec(
            select(Attribution).where(Attribution.run_id == run_id)
        ).first()

    def _run_dir(self, run_id: str) -> Path:
        return get_settings().data_runs_dir / run_id

    def _metadata(self, run_dir: Path) -> dict[str, Any]:
        try:
            meta = json.loads((run_dir / "metadata.json").read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        return meta if isinstance(meta, dict) else {}

    def _governance(self, run_dir: Path, meta: dict[str, Any]) -> dict[str, Any]:
        """The governance surface this run actually had (ADR-0031).

        Read coverage is a ratio, and the denominator has to be the documents
        the agent could reach. A `native` run never received the lab's
        `.github/standards.md`, so counting it as an unread governance doc
        penalises the target team for the lab's own conventions.
        """
        surface = resolve_surface(
            lab_root=get_settings().repo_root,
            run_dir=run_dir,
            harness=str(meta.get("harness") or "") or None,
            harness_source=str(meta.get("harness_source") or "") or "lab_root",
        )
        return surface.as_dict() | {"reading_surface": reading_surface(surface)}

    def _skills_available(self, meta: dict[str, Any]) -> list[str]:
        bundle = meta.get("skills_bundle")
        if isinstance(bundle, list):
            return [str(item) for item in bundle]
        fingerprint = meta.get("harness_fingerprint", {})
        skills = fingerprint.get("skills", {}) if isinstance(fingerprint, dict) else {}
        return sorted(skills) if isinstance(skills, dict) else []

    def per_step_consulted(self, run_id: str) -> dict[int, list[str]]:
        """What each step opened from the harness: skills and governance docs.

        The run-level attribution sums every step; the per-step reward needs to
        know *when* an item was in context so it can be read beside the
        progress made in that step (ADR-0076). Same parsers, same governance
        surface as :meth:`index_run`, applied one step file at a time.
        """
        from harness_lab_app.attribution import parse_claude_events, parse_logs
        from harness_lab_app.step_reward import consulted_items, step_record_files

        run_dir = self._run_dir(run_id)
        records = step_record_files(run_dir)
        if not records:
            return {}
        meta = self._metadata(run_dir)
        surface = tuple(self._governance(run_dir, meta).get("reading_surface") or ())
        found: dict[int, list[str]] = {}
        for number, files in records.items():
            # claude steps carry both a log and a richer events file; the
            # events file wins when it exists.
            events = files.get("events")
            log = files.get("log")
            if events is not None:
                lines = events.read_text(errors="replace").splitlines()
                parsed = parse_claude_events(lines)
                commands = _claude_bash_commands(lines)
            elif log is not None:
                text = log.read_text(errors="replace")
                parsed = parse_logs(text)
                commands = _OPENCODE_BASH_RE.findall(text)
            else:
                continue
            found[number] = consulted_items(parsed, surface, commands)
        return found

    def index_run(self, run_id: str) -> Attribution:
        run_dir = self._run_dir(run_id)
        meta = self._metadata(run_dir)
        governance = self._governance(run_dir, meta)
        surface = governance.pop("reading_surface")
        skills = self._skills_available(meta)
        # claude stream-json events are first-class tool calls — strictly
        # richer than log parsing; prefer them whenever the run produced any.
        event_lines, event_steps = read_step_events(run_dir)
        if event_lines:
            data: dict[str, Any] = build_attribution_from_events(
                event_lines,
                skills_available=skills,
                steps_parsed=event_steps,
                reading_surface=surface,
                governance=governance,
            )
        else:
            log_text, steps = read_step_logs(run_dir)
            data = build_attribution(
                log_text,
                skills_available=skills,
                steps_parsed=steps,
                reading_surface=surface,
                governance=governance,
            )

        attribution = self.get_attribution(run_id)
        if attribution is None:
            attribution = Attribution(run_id=run_id)
            self.session.add(attribution)
        attribution.tools = data["tools"]
        attribution.files = {
            "read": data["files_read"],
            "edited": data["files_edited"],
        }
        attribution.bash_commands = data["bash_commands"]
        attribution.agent_modes = data["agent_modes"]
        attribution.harness_usage = data["harness_usage"]
        attribution.summary = data["summary"]
        attribution.steps_parsed = data["steps_parsed"]
        attribution.updated_at = utc_now()
        self.session.add(attribution)
        self.session.commit()
        self.session.refresh(attribution)
        return attribution
