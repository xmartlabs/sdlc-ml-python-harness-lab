"""Classify why a run failed — and whether it measured anything at all.

A benchmark's credibility rests on not confusing "the model did badly" with
"the lab broke". Runs tetris-v1-K-043/044/045 are the cautionary case: the
runner image had gone missing, all three died in under a second having executed
nothing, and they still appeared in the run list as a bare "failed" and counted
as three attempts on the leaderboard. Three phantom attempts against a model
that was never asked anything.

The failure reason is a string written by :mod:`harness_lab_app.tasks`, so the
prefixes below are a closed set defined in one place and read by both the API
and the leaderboard rather than re-derived per consumer.

The second half of the module is the live half: the markers the run watchdog
reads off opencode's own log to tell a stuck agent from a slow model endpoint
while the run is still in flight. Both halves answer the same question — who
failed, and did anything get measured — so they are kept together.
"""

from __future__ import annotations

import re

# kind -> (prefix, human label). Order matters only for readability; the
# prefixes are mutually exclusive.
_KINDS: tuple[tuple[str, str, str], ...] = (
    # Never started: image missing, docker unreachable, bad configuration.
    ("lab", "Executor error:", "lab error"),
    # Killed by a restart while in flight; the run itself said nothing about it.
    ("lab", "Worker restarted", "worker restarted"),
    # The provider kept refusing the request — see `tasks._endpoint_failure`.
    ("lab", "Model endpoint failing:", "endpoint down"),
    # The provider accepted the request and never answered — see
    # `tasks._model_silence_failure`. A budget the lab chose ran out; the model
    # was never given the chance to be wrong, so this measures nothing either.
    ("lab", "No model response:", "no response"),
    # Ran, then went silent with no model request in flight — see
    # `tasks._stall_failure`. That silence is the agent's own doing (a tool call
    # that never returns), so unlike the two above it *is* a result.
    ("stalled", "Run stalled:", "stalled"),
    ("timeout", "Run timed out", "timed out"),
)

# Phrases anywhere in an agent-output tail that mean the run never reached the
# model. Prefixes cannot catch these: the reason is the tail of the CLI's own
# output. con-o-sin-harness (2026-10-23) recorded eight "agent error"s for a
# model the gateway had no card for. Checked in order; the first match wins.
_LAB_MARKERS: tuple[tuple[str, str], ...] = (
    # LiteLLM / Anthropic-shaped refusals of an unknown model id.
    ("invalid model name", "model not served"),
    ("model_not_found", "model not served"),
    ("does not exist or you do not have access", "model not served"),
    # The API refusing the harness CLI itself, not the request it sent.
    ("claude_code_version_too_old", "CLI too old"),
    # bench_run's own verdict when the harness died before the model answered.
    ("stop_reason=harness_error", "harness error"),
)

# Kinds where nothing can be concluded about the model or the harness, so the
# run is not an attempt and must not be counted as one.
UNMEASURED_KINDS = frozenset({"lab"})


# A server-side status from the provider or a proxy in front of it (Cloudflare
# 52x, Anthropic 529 overloaded). The run reached a gateway that could not
# answer, which says nothing about the model.
_SERVER_ERROR_RE = re.compile(r"API Error: 5\d\d\b")


def _lab_marker(text: str) -> str | None:
    lowered = text.lower()
    for marker, label in _LAB_MARKERS:
        if marker in lowered:
            # A server-side outage outranks the generic harness_error verdict
            # that always accompanies it.
            if label == "harness error" and _SERVER_ERROR_RE.search(text):
                return "endpoint down"
            return label
    if _SERVER_ERROR_RE.search(text):
        return "endpoint down"
    return None


def classify_failure(reason: str | None) -> str | None:
    """Return ``"lab"``, ``"stalled"``, ``"timeout"``, ``"agent"`` or ``None``.

    ``None`` means the run did not fail (or recorded no reason). ``"agent"`` is
    the fallback: the reason is the tail of the agent's own output, which is a
    real result — a bad one, but a result — unless that tail says the model was
    never reached.
    """
    text = (reason or "").strip()
    if not text:
        return None
    for kind, prefix, _label in _KINDS:
        if text.startswith(prefix):
            return kind
    if _lab_marker(text):
        return "lab"
    return "agent"


def failure_label(reason: str | None) -> str | None:
    """A two-word label for the run list, or ``None`` when nothing failed."""
    text = (reason or "").strip()
    if not text:
        return None
    for _kind, prefix, label in _KINDS:
        if text.startswith(prefix):
            return label
    return _lab_marker(text) or "agent error"


def is_unmeasured(reason: str | None) -> bool:
    """True when the run produced no evidence about the model or the harness."""
    return classify_failure(reason) in UNMEASURED_KINDS


# --- Live log-line semantics -------------------------------------------------
#
# The watchdog in `harness_lab_app.tasks` has to decide, while the run is still
# going, *why* it has gone quiet — and its only evidence is the last line the
# harness printed. Two of the three answers are lab failures, so the markers
# that separate them live here beside the taxonomy they feed rather than inline
# in the watchdog loop.
#
# The markers are **per harness**, because they are that harness's own words.
# They used to be opencode's literals applied to every run, which meant a
# `--harness claude` run never once looked like it was waiting on the model: a
# gateway that accepted a request and never answered was charged to the agent
# as a stall, and appeared on the leaderboard as an attempt the model failed.
# Four talos runs spanning 122 minutes were recorded that way.
#
# A harness this table does not know gets the conservative answer — see
# `_UNKNOWN`. Silence there is never charged to the agent, because a stall we
# cannot distinguish from a hung endpoint is not evidence about the agent.


class HarnessSignals:
    """What one harness's log lines mean, for the watchdog that reads them."""

    def __init__(
        self,
        *,
        request: tuple[str, ...] = (),
        wait: tuple[str, ...] = (),
        stream_error: tuple[str, ...] = (),
        retry_noise: tuple[str, ...] = (),
        silence_awaits_model: bool = False,
    ) -> None:
        self.request = request
        self.wait = wait
        self.stream_error = stream_error
        self.retry_noise = retry_noise
        #: True when this harness prints nothing at the moment it hands a
        #: request to the provider, so a quiet run cannot be told apart from a
        #: waiting one. The watchdog then treats silence as waiting on the
        #: model — a lab failure — rather than inventing an agent stall.
        self.silence_awaits_model = silence_awaits_model


# Emitted once per request opencode hands to the provider, retries included.
# The trailing space is load-bearing: `message="stream error"` is the opposite
# situation. Counting these against the stream errors is what lets the end of a
# run answer "did the model ever speak?" — a run whose every request failed
# measured nothing, whatever its exit code says.
_MODEL_REQUEST_MARKER = "message=stream "

#: What `scripts/bench_run.py::render_claude_event` prints the moment a tool
#: result goes back to the model, i.e. the moment a request is in flight. It
#: exists so the claude lane can answer the same question opencode's log
#: already answered; the two must move together.
CLAUDE_AWAITING_MARKER = "# awaiting model"

_HARNESS_SIGNALS: dict[str, HarnessSignals] = {
    "opencode": HarnessSignals(
        request=(_MODEL_REQUEST_MARKER,),
        # Nothing follows these until the first token comes back, so a run
        # sitting on one of them is waiting on the model, not stuck in a tool.
        wait=(_MODEL_REQUEST_MARKER, "llm runtime selected"),
        # A provider request that failed outright. opencode retries these
        # forever, so a run of them is the signature of one that will spend its
        # whole time budget going nowhere (run tetris-v1-K-029: 123 of these in
        # 70 minutes, every one "Cannot connect to API", recorded in the end as
        # a 2-hour timeout).
        stream_error=('message="stream error"',),
        # What opencode prints while setting up the *next* attempt after a
        # failure. None of it is progress, so none of it may reset the
        # consecutive-error count.
        retry_noise=(
            "message=stream ",
            "llm runtime selected",
            "message=process ",
            "message=loop ",
        ),
    ),
    "claude": HarnessSignals(
        request=(CLAUDE_AWAITING_MARKER,),
        wait=(CLAUDE_AWAITING_MARKER, "# claude session init"),
        stream_error=("# stream error",),
        retry_noise=(CLAUDE_AWAITING_MARKER, "# claude session init"),
    ),
}

#: Any harness this module has not been taught. It names no marker, so every
#: silence is a silence nobody can attribute — and an unattributable silence is
#: unmeasured, not the agent's fault.
_UNKNOWN = HarnessSignals(silence_awaits_model=True)


def signals_for(harness: str | None) -> HarnessSignals:
    """The log-line vocabulary of one harness, or the conservative default."""
    return _HARNESS_SIGNALS.get((harness or "").strip().lower(), _UNKNOWN)


def known_harnesses() -> tuple[str, ...]:
    """Harnesses whose log lines this module can read."""
    return tuple(sorted(_HARNESS_SIGNALS))


def awaits_model_response(line: str, harness: str | None = None) -> bool:
    """True when this line leaves a model request in flight."""
    marks = signals_for(harness)
    if any(marker in line for marker in marks.wait):
        return True
    # A harness that never announces a request cannot be shown to be waiting,
    # and cannot be shown not to be. `silence_awaits_model` is which way that
    # ignorance is resolved.
    return marks.silence_awaits_model and not marks.wait


def is_model_request(line: str, harness: str | None = None) -> bool:
    """True when this line hands a fresh request to the provider."""
    return any(marker in line for marker in signals_for(harness).request)


def is_stream_error(line: str, harness: str | None = None) -> bool:
    """True when this line reports a provider request that failed."""
    return any(marker in line for marker in signals_for(harness).stream_error)


def is_retry_noise(line: str, harness: str | None = None) -> bool:
    """True when this line is a retry being set up rather than work getting done."""
    return any(marker in line for marker in signals_for(harness).retry_noise)
