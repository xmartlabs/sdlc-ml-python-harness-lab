"""The LLM reading of a finished experiment: evidence in, prose out.

Everything the model is told comes from what the lab already computed —
:meth:`ExperimentService.summarize`, the per-run validation, scores and
attribution rows. The service assembles that into one evidence document, asks
for a fixed-section summary, and persists both. Nothing here judges; the
descriptive facts are the facts, and the model's job is to say them in order.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from sqlmodel import Session, select

from harness_lab_app.attribution_service import AttributionService
from harness_lab_app.experiment_service import ExperimentService
from harness_lab_app.models import ExperimentAudit, RunState, Validation, utc_now
from harness_lab_app.run_service import RunService
from harness_lab_app.score_service import ScoreService

if TYPE_CHECKING:
    from collections.abc import Callable

# States a run can be in once it has stopped executing. A run awaiting its
# manual score has a validation and cost record; it is finished for the
# purposes of reading the experiment, even if a human has not graded it.
TERMINAL_STATES = frozenset(
    {
        RunState.completed,
        RunState.failed,
        RunState.cancelled,
        RunState.awaiting_manual_score,
    }
)

# The score rows that describe an arm to a reader, in the order they read best.
RUN_METRICS = (
    "task_validation_pass",
    "test_pass_rate",
    "rubric_score",
    "cost_usd",
    "billed_tokens",
    "wall_time_minutes",
    "agent_turns",
    "agent_tool_calls",
    "diff_lines_changed",
    "skill_invoked",
    "skill_invocations",
    "governance_read_coverage",
)

DETAIL_CLIP = 400

EXPERIMENT_AUDIT_SYSTEM_PROMPT = (
    "Sos el auditor de un laboratorio que mide si un harness de agentes de código "
    "(gobernanza, skills, prompt, hooks) ayuda a un modelo. Te dan la evidencia de "
    "un experimento terminado: qué brazos se compararon, qué resultado y costo tuvo "
    "cada corrida, qué documentos y skills usó cada brazo, y el veredicto de "
    "validez que el laboratorio ya calculó. Escribí un informe corto en español, "
    "en Markdown, con EXACTAMENTE estas secciones y en este orden:\n"
    "## Veredicto\n"
    "Una o dos frases: ¿el harness ayudó, en cuánto, y se puede confiar en esta "
    "comparación? Si el laboratorio la marcó inválida, decilo primero y por qué.\n"
    "## Qué se comparó\n"
    "Los brazos y qué difiere entre ellos, en una línea cada uno.\n"
    "## Resultado\n"
    "Éxito y costo por brazo con los números tal cual vienen (pasa/no pasa, "
    "test_pass_rate, costo en USD, tokens facturados, minutos). Una tabla corta "
    "está bien.\n"
    "## Qué se usó y qué no\n"
    "Por brazo: qué documentos de gobernanza leyó y cuáles no; qué skills invocó, "
    "cuáles se le pidieron y no invocó, y cuáles tenía disponibles y nunca abrió. "
    "Listas explícitas.\n"
    "## Por qué (no) se puede confiar\n"
    "Cada problema de validez en lenguaje llano, y qué implica para la lectura.\n"
    "## Qué hacer ahora\n"
    "Dos o tres pasos concretos, derivados de lo anterior.\n\n"
    "Reglas: usá SOLO la evidencia dada — no inventes causas ni números; copiá los "
    "números tal cual; nunca uses 'significativo' ni vocabulario de prueba "
    "estadística; con 1 repetición decí que es exploratorio, con 2-4 que es un "
    "juicio, con 5 o más una validación; no repitas el mismo dato en dos secciones; "
    "no incluyas secretos, tokens ni rutas del sistema de archivos."
)


#: How many times the reading's single gateway call is attempted, and how long
#: it waits between attempts. A transport error is a fact about the network,
#: not about the experiment: the `audit-talos` reading died on one and was
#: never retried, leaving a `failed` row beside a finished experiment nobody
#: had read. A 4xx from the gateway is *not* retried — the request itself is
#: wrong, and asking again three times only makes that slower.
AUDIT_ATTEMPTS = 3
AUDIT_BACKOFF_SECONDS: tuple[float, ...] = (5.0, 20.0)


def call_with_retry(
    call: Callable[[], str],
    *,
    attempts: int = AUDIT_ATTEMPTS,
    backoff: tuple[float, ...] = AUDIT_BACKOFF_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Run the reading's gateway call, retrying only what a retry can fix.

    Raises the last error with the attempt count attached, so a stored failure
    says whether the gateway was asked once or three times.
    """
    from harness_lab_app.llm_transport import GatewayHTTPError, TransportError

    last: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return call()
        except GatewayHTTPError as exc:
            if 400 <= exc.code < 500:
                raise  # the request is wrong; repeating it will not fix it
            last = exc
        except TransportError as exc:
            last = exc
        if attempt < attempts:
            index = min(attempt - 1, len(backoff) - 1) if backoff else -1
            sleep(backoff[index] if index >= 0 else 0.0)
    assert last is not None
    raise type(last)(f"{last} (after {attempts} attempts)") from last


def runs_finished(session: Session, experiment_id: str) -> tuple[bool, int, int]:
    """(every run stopped, finished count, total count) for an experiment."""
    runs = ExperimentService(session).runs(experiment_id)
    finished = sum(1 for run in runs if run.state in TERMINAL_STATES)
    return (bool(runs) and finished == len(runs), finished, len(runs))


def build_evidence(session: Session, experiment_id: str) -> dict[str, Any]:
    """Everything the auditor is allowed to read, as one JSON-able document."""
    experiments = ExperimentService(session)
    experiment = experiments.get_experiment(experiment_id)
    summary = experiments.summarize(experiment_id)
    runs = RunService(session)
    attribution = AttributionService(session)
    scores = ScoreService(session)

    arms: list[dict[str, Any]] = []
    for condition in experiments.conditions(experiment_id):
        arms.append(
            {
                "label": condition.label,
                "role": str(condition.role),
                "description": condition.description,
                "overrides": dict(condition.overrides),
            }
        )

    run_rows: list[dict[str, Any]] = []
    for run in experiments.runs(experiment_id):
        config = runs.get_run_config(run.run_config_id)
        validation = session.exec(
            select(Validation).where(Validation.run_id == run.run_id)
        ).first()
        objective = (validation.objective if validation else None) or {}
        subjective = (validation.subjective if validation else None) or {}
        effort = (validation.effort if validation else None) or {}
        failed_checks = [
            {
                # `command` first: rows written before validation emitted it
                # only carry `name`, and both hold the same string since.
                "command": str(check.get("command") or check.get("name") or "")[-120:],
                "exit_code": check.get("exit_code"),
                "conclusive": check.get("conclusive", True),
                "detail": str(check.get("detail") or "").strip()[-DETAIL_CLIP:],
            }
            for check in objective.get("checks") or []
            if not check.get("passed")
        ]
        values: dict[str, Any] = {}
        for score in scores.list_scores(run.run_id):
            if score.name in RUN_METRICS:
                value = (
                    score.value_num
                    if score.value_num is not None
                    else score.value_bool
                    if score.value_bool is not None
                    else score.value_text
                )
                values[score.name] = value
        usage: dict[str, Any] = {}
        attributed = attribution.get_attribution(run.run_id)
        if attributed is not None:
            h = attributed.harness_usage or {}
            available_docs = list(h.get("governance_docs_available") or [])
            read_docs = list(h.get("governance_docs_read") or [])
            available_skills = list(h.get("skills_available") or [])
            consulted = list(h.get("skills_consulted") or [])
            usage = {
                "governance_docs_read": read_docs,
                "governance_docs_not_read": [
                    d for d in available_docs if d not in read_docs
                ],
                "skills_invoked": consulted,
                "skills_asked_for": sorted(config.skills_invoke),
                "skills_asked_for_but_not_invoked": [
                    s for s in sorted(config.skills_invoke) if s not in consulted
                ],
                "skills_available_never_opened": [
                    s for s in available_skills if s not in consulted
                ],
                "governance_source": h.get("governance_source", ""),
            }
        run_rows.append(
            {
                "run_id": run.run_id,
                "arm": run.condition_label,
                "case_id": config.case_id,
                "state": str(run.state),
                "stop_reason": effort.get("stop_reason"),
                "harness_source": str(config.harness_source),
                "prompt_variant": config.prompt_variant,
                "executor_model": config.executor_model,
                "objective": objective.get("summary"),
                "failed_checks": failed_checks,
                "judge": {
                    "score": subjective.get("functional_score"),
                    "source": subjective.get("score_source"),
                    "rationale": str(subjective.get("notes") or "")[:DETAIL_CLIP],
                },
                "scores": values,
                "usage": usage,
            }
        )

    verdict = summary.get("verdict") or {}
    return {
        "experiment_id": experiment_id,
        "question": experiment.question,
        "mode": str(experiment.mode),
        "repetitions": experiment.repetitions,
        "case_ids": list(experiment.case_ids),
        "arms": arms,
        "validity": summary.get("validity"),
        "verdict": {
            key: verdict.get(key)
            for key in ("headline", "claim", "observed", "strength", "highlights")
        },
        "comparisons": [
            {
                "control": c.get("control"),
                "treatment": c.get("treatment"),
                "success": c.get("success"),
                "case_matrix": c.get("case_matrix"),
                "deltas": [
                    {
                        "name": d.get("name"),
                        "control": d.get("control"),
                        "treatment": d.get("treatment"),
                        "direction": d.get("direction"),
                        "exceeds_spread": d.get("exceeds_spread"),
                    }
                    for d in c.get("deltas") or []
                ],
            }
            for c in summary.get("comparisons") or []
        ],
        "runs": run_rows,
    }


def render_audit_prompt(evidence: dict[str, Any]) -> str:
    """The user message: the evidence, verbatim, as JSON the model can quote."""
    return (
        "Evidencia del experimento (JSON). Todo número que cites tiene que salir "
        "de acá.\n\n```json\n"
        + json.dumps(evidence, indent=1, ensure_ascii=False, default=str)
        + "\n```"
    )


class ExperimentAuditService:
    def __init__(self, session: Session):
        self.session = session

    def get(self, experiment_id: str) -> ExperimentAudit | None:
        return self.session.exec(
            select(ExperimentAudit).where(
                ExperimentAudit.experiment_id == experiment_id
            )
        ).first()

    def _upsert(self, experiment_id: str) -> ExperimentAudit:
        audit = self.get(experiment_id)
        if audit is None:
            audit = ExperimentAudit(experiment_id=experiment_id)
            self.session.add(audit)
        return audit

    def mark_running(
        self, experiment_id: str, auditor_model: str = ""
    ) -> ExperimentAudit:
        audit = self._upsert(experiment_id)
        audit.status = "running"
        audit.auditor_model = auditor_model
        audit.error = None
        audit.updated_at = utc_now()
        self.session.add(audit)
        self.session.commit()
        self.session.refresh(audit)
        return audit

    def store_result(
        self, experiment_id: str, markdown: str, evidence: dict[str, Any], model: str
    ) -> ExperimentAudit:
        audit = self._upsert(experiment_id)
        audit.status = "done"
        audit.markdown = markdown
        audit.evidence_json = evidence
        audit.auditor_model = model
        audit.error = None
        audit.updated_at = utc_now()
        self.session.add(audit)
        self.session.commit()
        self.session.refresh(audit)
        return audit

    def store_error(self, experiment_id: str, error: str) -> ExperimentAudit:
        audit = self._upsert(experiment_id)
        audit.status = "failed"
        audit.error = error[:2000]
        audit.updated_at = utc_now()
        self.session.add(audit)
        self.session.commit()
        self.session.refresh(audit)
        return audit
