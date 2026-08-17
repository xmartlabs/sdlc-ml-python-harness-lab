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

# Kinds where nothing can be concluded about the model or the harness, so the
# run is not an attempt and must not be counted as one.
UNMEASURED_KINDS = frozenset({"lab"})


def classify_failure(reason: str | None) -> str | None:
    """Return ``"lab"``, ``"stalled"``, ``"timeout"``, ``"agent"`` or ``None``.

    ``None`` means the run did not fail (or recorded no reason). ``"agent"`` is
    the fallback: the reason is the tail of the agent's own output, which is a
    real result — a bad one, but a result.
    """
    text = (reason or "").strip()
    if not text:
        return None
    for kind, prefix, _label in _KINDS:
        if text.startswith(prefix):
            return kind
    return "agent"


def failure_label(reason: str | None) -> str | None:
    """A two-word label for the run list, or ``None`` when nothing failed."""
    text = (reason or "").strip()
    if not text:
        return None
    for _kind, prefix, label in _KINDS:
        if text.startswith(prefix):
            return label
    return "agent error"


def is_unmeasured(reason: str | None) -> bool:
    """True when the run produced no evidence about the model or the harness."""
    return classify_failure(reason) in UNMEASURED_KINDS


# --- Live log-line semantics -------------------------------------------------
#
# The watchdog in `harness_lab_app.tasks` has to decide, while the run is still
# going, *why* it has gone quiet — and its only evidence is the last line
# opencode printed. Two of the three answers are lab failures, so the markers
# that separate them live here beside the taxonomy they feed rather than inline
# in the watchdog loop.

# Emitted once per request opencode hands to the provider, retries included.
# The trailing space is load-bearing: `message="stream error"` is the opposite
# situation. Counting these against the stream errors is what lets the end of a
# run answer "did the model ever speak?" — a run whose every request failed
# measured nothing, whatever its exit code says.
_MODEL_REQUEST_MARKER = "message=stream "

# Emitted when opencode hands a request to the provider. Nothing follows until
# the first token comes back, so a run sitting on one of these is waiting on the
# model, not stuck in a tool call.
_MODEL_WAIT_MARKERS = (_MODEL_REQUEST_MARKER, "llm runtime selected")

# A provider request that failed outright. opencode retries these forever, so a
# run of them is the signature of one that will spend its whole time budget
# going nowhere (run tetris-v1-K-029: 123 of these in 70 minutes, every one
# "Cannot connect to API", recorded in the end as a 2-hour timeout).
_STREAM_ERROR_MARKER = 'message="stream error"'

# What opencode prints while setting up the *next* attempt after a failure.
# None of it is progress, so none of it may reset the consecutive-error count.
_RETRY_NOISE_MARKERS = (
    "message=stream ",
    "llm runtime selected",
    "message=process ",
    "message=loop ",
)


def awaits_model_response(line: str) -> bool:
    """True when this line leaves a model request in flight."""
    return any(marker in line for marker in _MODEL_WAIT_MARKERS)


def is_model_request(line: str) -> bool:
    """True when this line hands a fresh request to the provider."""
    return _MODEL_REQUEST_MARKER in line


def is_stream_error(line: str) -> bool:
    """True when this line reports a provider request that failed."""
    return _STREAM_ERROR_MARKER in line


def is_retry_noise(line: str) -> bool:
    """True when this line is a retry being set up rather than work getting done."""
    return any(marker in line for marker in _RETRY_NOISE_MARKERS)
