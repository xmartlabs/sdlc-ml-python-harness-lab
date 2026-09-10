"""The shape of a claim-mapped repository audit (ADR-0058).

The audit used to ask the model for *tasks an agent could do in this
repository*. Pointed at talos — a harness whose whole thesis is that the model
may invoke a deterministic layer but never author its outputs — that question
produced "run the test suite", and an experiment that spent $4 measuring
nothing about the thesis. The fix is upstream of every gate: ask a different
question, and give its answer a shape the lab can check.

The unit of analysis is a **harness component**. For each one the model says
what it *claims*, when it *fires*, what would be *observable* if it helped,
which lab component does the *same job*, and what the claim *needs* from the
outside world. Cases are then theorised from that map, and each one names the
components it exercises. The lab, not the model, decides what is evaluable
here and derives the experiment arms — those functions arrive with the phases
that own them; this module is the contract they all agree on.

Pure: JSON text in, plain dictionaries out, no I/O. The parsers are tolerant
the way :func:`harness_lab_app.repository_audit_service.parse_findings` is —
prose instead of JSON is a ``parse_error``, never an invented row — and a
value outside a closed vocabulary is kept as ``unknown`` with a warning, so a
reader sees what the model actually said instead of a silently dropped row.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

# --------------------------------------------------------------------------
# Closed vocabularies — the model may only pick from these
# --------------------------------------------------------------------------

COMPONENT_KINDS = ("skill", "hook", "rule", "agent", "command")
"""What a harness is made of. ``command`` covers a deterministic script the
harness invokes (talos's ``lib/classify.sh``), which is neither a skill nor a
hook but is where its thesis actually lives."""

NEEDS_VOCABULARY = (
    "jira",
    "github",
    "docker",
    "network",
    "target_repo",
    "browser",
    "none",
)
"""What a claim depends on outside the checkout. ``target_repo`` is the one an
installable harness needs: a project to be installed *into*."""

HARNESS_SHAPES = ("in_place", "installable", "both")
"""``in_place``: the harness governs work on this very repository.
``installable``: it is copied into another project and governs work there."""

CASE_MEASURES = ("harness_claim", "harness_development")
"""``harness_claim``: the case tests what the harness says it does.
``harness_development``: the case tests working *on* the harness's own code.
Both are legitimate; the report must never confuse them."""

UNKNOWN = "unknown"

_MAX_COMPONENTS = 60
_MAX_CASES = 8
_MAX_NEEDS = 6
_MAX_EXERCISES = 8
_SHORT = 300
_LONG = 1200
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-]{0,119}$")


# --------------------------------------------------------------------------
# Rows
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ComponentRow:
    """One harness component and what the model read into it.

    ``evaluable_here``, ``evaluable_reason`` and ``counterpart_valid`` are
    lab-owned: the parser leaves them at their neutral defaults and the
    annotation step (a later phase) fills them from facts. A model that writes
    them is ignored on those fields.
    """

    kind: str
    name: str
    source_path: str
    claim: str
    trigger: str
    observable: str
    lab_counterpart: str
    needs: tuple[str, ...]
    evaluable_here: bool = False
    evaluable_reason: str = ""
    counterpart_valid: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["needs"] = list(self.needs)
        return data


@dataclass(frozen=True)
class HarnessShape:
    """Whether the harness governs this tree or is installed into another."""

    shape: str
    install_entry: str
    unit_of_work: str
    install_entry_exists: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CaseProposal:
    """One case the model theorised from the annotated map.

    ``dropped_reason`` is lab-owned and empty while the case is kept.
    """

    title: str
    exercises: tuple[str, ...]
    measures: str
    task: str
    observable: str
    validation_hint: str
    required_runtime_version: str = ""
    dropped_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["exercises"] = list(self.exercises)
        return data


@dataclass(frozen=True)
class DerivedCondition:
    """An experiment arm the lab derived from a case — never model-written.

    ``overrides`` uses exactly the vocabulary ``experimentcondition.overrides``
    already takes (``skills_disabled``, ``skills_extra``, ``harness_source``),
    so an arm proposed here is an arm the runner can execute unchanged.
    """

    label: str
    role: str
    overrides: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "role": self.role,
            "overrides": copy.deepcopy(self.overrides),
        }


# --------------------------------------------------------------------------
# Coercion helpers — narrow, and every deviation is a warning
# --------------------------------------------------------------------------


def _text(raw: object, limit: int) -> str:
    return str(raw or "").strip()[:limit]


def _name(raw: object) -> str:
    value = _text(raw, 120)
    return value if _NAME_RE.match(value) else ""


def _vocab(
    raw: object, vocabulary: tuple[str, ...], *, where: str, warnings: list[str]
) -> str:
    value = _text(raw, 40).lower()
    if value in vocabulary:
        return value
    warnings.append(
        f"{where}: {value or '(empty)'!r} is not one of {', '.join(vocabulary)}"
    )
    return UNKNOWN


def _needs(raw: object, *, where: str, warnings: list[str]) -> tuple[str, ...]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return ("none",)
    out: list[str] = []
    for item in raw[:_MAX_NEEDS]:
        value = _vocab(
            item, NEEDS_VOCABULARY, where=f"{where}.needs", warnings=warnings
        )
        if value not in out:
            out.append(value)
    if not out:
        return ("none",)
    # ``none`` beside a real need is a contradiction; the real need wins.
    if len(out) > 1 and "none" in out:
        out.remove("none")
    return tuple(out)


def _names(raw: object, limit: int) -> tuple[str, ...]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return ()
    seen: list[str] = []
    for item in raw[:limit]:
        name = _name(item)
        if name and name not in seen:
            seen.append(name)
    return tuple(seen)


def _extract_json(raw: str) -> str:
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        cleaned = cleaned[start : end + 1]
    return cleaned.strip()


def _object(raw: str) -> dict[str, Any] | str:
    """The model's answer as a dict, or the reason it is not one."""
    try:
        parsed = json.loads(_extract_json(raw))
    except json.JSONDecodeError as exc:
        return f"the auditor did not answer with JSON: {exc}"
    if not isinstance(parsed, dict):
        return "the auditor did not answer with a JSON object"
    return parsed


# --------------------------------------------------------------------------
# Pass 1 — the component map and the harness's shape
# --------------------------------------------------------------------------


def component_row(
    raw: object, *, index: int, warnings: list[str]
) -> ComponentRow | None:
    """One row, or ``None`` when it has no name to be a row about."""
    if not isinstance(raw, dict):
        warnings.append(f"component_map[{index}]: not an object, dropped")
        return None
    name = _name(raw.get("name"))
    if not name:
        warnings.append(f"component_map[{index}]: no usable name, dropped")
        return None
    where = f"component_map[{index}] ({name})"
    return ComponentRow(
        kind=_vocab(
            raw.get("kind"), COMPONENT_KINDS, where=f"{where}.kind", warnings=warnings
        ),
        name=name,
        source_path=_text(raw.get("source_path"), _SHORT),
        claim=_text(raw.get("claim"), _LONG),
        trigger=_text(raw.get("trigger"), _SHORT),
        observable=_text(raw.get("observable"), _LONG),
        lab_counterpart=_name(raw.get("lab_counterpart")),
        needs=_needs(raw.get("needs"), where=where, warnings=warnings),
    )


def harness_shape(raw: object, *, warnings: list[str]) -> HarnessShape:
    """The shape, or ``unknown`` with one warning when the model gave none."""
    if not isinstance(raw, dict):
        warnings.append("shape: missing or not an object")
        return HarnessShape(shape=UNKNOWN, install_entry="", unit_of_work="")
    return HarnessShape(
        shape=_vocab(
            raw.get("shape"), HARNESS_SHAPES, where="shape.shape", warnings=warnings
        ),
        install_entry=_text(raw.get("install_entry"), _SHORT),
        unit_of_work=_text(raw.get("unit_of_work"), _SHORT),
    )


def parse_component_map(raw: str) -> dict[str, Any]:
    """The first pass, coerced to the contract.

    Keeps the descriptive keys ``parse_findings`` already renders (``summary``,
    ``harness_verdict``, ``language_notes``, ``standards``, ``gaps``) so the
    report's opening sections do not change shape, and adds ``component_map``
    and ``shape``. ``warnings`` lists every coercion; an empty list means the
    model stayed inside the vocabulary.
    """
    parsed = _object(raw)
    if isinstance(parsed, str):
        return {"parse_error": parsed}
    warnings: list[str] = []
    rows_raw = parsed.get("component_map")
    if not isinstance(rows_raw, list):
        warnings.append("component_map: missing or not a list")
        rows_raw = []
    rows = [
        row
        for index, item in enumerate(rows_raw[:_MAX_COMPONENTS])
        if (row := component_row(item, index=index, warnings=warnings)) is not None
    ]
    verdict = _text(parsed.get("harness_verdict"), 20).lower()
    return {
        "summary": _text(parsed.get("summary"), 2000),
        "harness_verdict": verdict
        if verdict in ("complete", "partial", "none")
        else UNKNOWN,
        "language_notes": _text(parsed.get("language_notes"), 800),
        "standards": _strings(parsed.get("standards")),
        "gaps": _strings(parsed.get("gaps")),
        "component_map": [row.to_dict() for row in rows],
        "shape": harness_shape(parsed.get("shape"), warnings=warnings).to_dict(),
        "warnings": warnings,
    }


def _strings(raw: object, limit: int = 12) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [_text(item, _SHORT) for item in raw[:limit] if _text(item, _SHORT)]


# --------------------------------------------------------------------------
# Pass 2 — cases theorised from the annotated map
# --------------------------------------------------------------------------


def case_proposal(
    raw: object, *, index: int, warnings: list[str]
) -> CaseProposal | None:
    if not isinstance(raw, dict):
        warnings.append(f"cases[{index}]: not an object, dropped")
        return None
    title = _text(raw.get("title"), _SHORT)
    if not title:
        warnings.append(f"cases[{index}]: no title, dropped")
        return None
    where = f"cases[{index}] ({title})"
    return CaseProposal(
        title=title,
        exercises=_names(raw.get("exercises"), _MAX_EXERCISES),
        measures=_vocab(
            raw.get("measures"),
            CASE_MEASURES,
            where=f"{where}.measures",
            warnings=warnings,
        ),
        task=_text(raw.get("task"), 4000),
        observable=_text(raw.get("observable"), _LONG),
        validation_hint=_text(raw.get("validation_hint"), _LONG),
        required_runtime_version=_text(raw.get("required_runtime_version"), 20),
    )


def parse_case_proposals(raw: str) -> dict[str, Any]:
    """The second pass, coerced to the contract.

    A case with no ``exercises`` is *kept* here with an empty tuple: whether
    that drops it is the annotation step's call, and its reason belongs on the
    row where a reader will look for it.
    """
    parsed = _object(raw)
    if isinstance(parsed, str):
        return {"parse_error": parsed}
    warnings: list[str] = []
    rows_raw = parsed.get("cases")
    if not isinstance(rows_raw, list):
        warnings.append("cases: missing or not a list")
        rows_raw = []
    rows = [
        row
        for index, item in enumerate(rows_raw[:_MAX_CASES])
        if (row := case_proposal(item, index=index, warnings=warnings)) is not None
    ]
    return {"cases": [row.to_dict() for row in rows], "warnings": warnings}


# --------------------------------------------------------------------------
# The lab's half — facts decide what the model only proposed
# --------------------------------------------------------------------------


def annotate_map(
    findings: dict[str, Any],
    *,
    lab_skill_names: Iterable[str],
    services: Mapping[str, Mapping[str, Any]],
    installer_paths: Iterable[str] = (),
) -> dict[str, Any]:
    """Fill the lab-owned fields of a parsed map from facts.

    ``evaluable_here`` is true when every need the row names is a service the
    lab declares available (``none`` always is). ``counterpart_valid`` is true
    when the name is in the lab's catalogue; an unknown name is cleared and
    the clearing is a warning, so a reader sees the model's guess was refused
    rather than never made. ``install_entry_exists`` checks the entry against
    the installers the evidence actually found.
    """
    catalogue = set(lab_skill_names)
    installers = set(installer_paths)
    out = dict(findings)
    warnings = list(out.get("warnings") or [])
    rows: list[dict[str, Any]] = []
    for row in out.get("component_map") or []:
        row = dict(row)
        missing = [
            need
            for need in row.get("needs") or ["none"]
            if need != "none" and not bool((services.get(need) or {}).get("available"))
        ]
        row["evaluable_here"] = not missing
        row["evaluable_reason"] = (
            ""
            if not missing
            else "needs "
            + ", ".join(
                f"{need} ({(services.get(need) or {}).get('reason') or 'unavailable'})"
                for need in missing
            )
        )
        counterpart = row.get("lab_counterpart") or ""
        if counterpart and counterpart not in catalogue:
            warnings.append(
                f"{row.get('name')}: lab_counterpart {counterpart!r} is not a lab skill, cleared"
            )
            counterpart = ""
        row["lab_counterpart"] = counterpart
        row["counterpart_valid"] = bool(counterpart)
        rows.append(row)
    out["component_map"] = rows
    shape = dict(out.get("shape") or {})
    entry = shape.get("install_entry") or ""
    shape["install_entry_exists"] = bool(entry) and entry in installers
    if entry and not shape["install_entry_exists"]:
        warnings.append(
            f"shape.install_entry {entry!r} is not among the installers found"
        )
    out["shape"] = shape
    out["warnings"] = warnings
    return out


def annotate_cases(
    proposals: dict[str, Any], annotated_map: dict[str, Any]
) -> dict[str, Any]:
    """Drop, with a reason on the row, every case the lab could not measure.

    A case is kept only when it names at least one component, every name is
    in the map, and every named component is evaluable here. Dropped rows
    stay in the list with ``dropped_reason`` set: the report shows what the
    model proposed and why it will not run, which is itself a finding about
    the repository (three of talos's skills need Jira or GitHub).
    """
    by_name = {row["name"]: row for row in annotated_map.get("component_map") or []}
    out = dict(proposals)
    cases: list[dict[str, Any]] = []
    for case in out.get("cases") or []:
        case = dict(case)
        names = list(case.get("exercises") or [])
        unknown = [name for name in names if name not in by_name]
        blocked = [
            name
            for name in names
            if name in by_name and not by_name[name].get("evaluable_here")
        ]
        if not names:
            case["dropped_reason"] = "names no component of the map"
        elif unknown:
            case["dropped_reason"] = "names components not in the map: " + ", ".join(
                unknown
            )
        elif blocked:
            case["dropped_reason"] = "; ".join(
                f"{name} {by_name[name].get('evaluable_reason') or 'is not evaluable here'}"
                for name in blocked
            )
        else:
            case["dropped_reason"] = ""
        if not case["dropped_reason"]:
            derived = derive_conditions(case, annotated_map)
            case["conditions"] = [arm.to_dict() for arm in derived["conditions"]]
            case["conditions_note"] = derived["note"]
        cases.append(case)
    out["cases"] = cases
    return out


def derive_conditions(
    case: Mapping[str, Any], annotated_map: Mapping[str, Any]
) -> dict[str, Any]:
    """The experiment arms a kept case implies. Mechanical, model-free.

    Skills are the only components the runner can switch per arm today
    (``skills_disabled`` / ``skills_extra``), so a case exercising skills gets
    ``native-on``, ``native-off`` and — when a valid counterpart exists —
    ``lab-counterpart``. A case exercising only hooks, rules, agents or
    commands gets the ``native`` / ``injected`` pair the lab already runs,
    and the note says why that is all it gets.
    """
    by_name = {row["name"]: row for row in annotated_map.get("component_map") or []}
    skills = [
        name
        for name in case.get("exercises") or []
        if by_name.get(name, {}).get("kind") == "skill"
    ]
    counterparts: list[str] = []
    for name in skills:
        counterpart = by_name[name].get("lab_counterpart") or ""
        if by_name[name].get("counterpart_valid") and counterpart not in counterparts:
            counterparts.append(counterpart)
    if not skills:
        return {
            "conditions": [
                DerivedCondition("native", "control", {"harness_source": "native"}),
                DerivedCondition(
                    "injected", "treatment", {"harness_source": "injected"}
                ),
            ],
            "note": (
                "exercises no skill; hooks, rules, agents and commands cannot be "
                "switched per arm, so the arms compare the whole native harness "
                "against the lab's"
            ),
        }
    conditions = [
        DerivedCondition("native-on", "control", {}),
        DerivedCondition("native-off", "treatment", {"skills_disabled": list(skills)}),
    ]
    note = ""
    if counterparts:
        conditions.append(
            DerivedCondition(
                "lab-counterpart",
                "treatment",
                {"skills_disabled": list(skills), "skills_extra": list(counterparts)},
            )
        )
    else:
        note = (
            "no valid lab counterpart for the exercised skills; only the ablation arm"
        )
    return {"conditions": conditions, "note": note}
