"""Whether a case's validation commands would measure anything.

A badly formed case does not fail loudly. It runs, it reports a number, and the
number is about the case rather than about the agent — which is worse than no
measurement, because it looks like one. Two real cases, written from a talos
audit and executed in two experiments each, made the argument:

- ``for t in {workspace}/tests/test_*.sh; do bash "$t"; done`` returns the exit
  status of the *last* script only. Fifty-four failures ahead of one pass is a
  green check. Both arms scored ``task_validation_pass = 1`` while the run's own
  stored detail read ``rc=2 CONFIG ERROR``.
- ``python -c "... any(p in text for p in patterns) ..."`` over a literal list
  matches any README containing the word "cost". It cannot fail, so it says
  nothing about whether the agent wrote anything.
- A command naming ``benchmark/model/run-model.sh`` with no ``{workspace}``
  resolves against the lab's own repository root and exits 127. The file
  existed; the path did not.

Each rule below refuses one of those shapes at the moment the case is written,
while its author is still on the page and can fix it. The rules are heuristics
and are deliberately narrow: they reject shapes that are wrong under every
reading, and stay quiet about anything arguable.

Pure — string in, reasons out. The dry run against the clean checkout
(:func:`harness_lab_app.case_service.CaseService.dry_run_case`) is the empirical
half of the same question, and catches what no heuristic can.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

from harness_lab_app.validation import (
    is_executable_command,
    names_a_document,
    requirement_is_installable,
)

REASON_MULTILINE = (
    "spans several lines; the case stores one shell command per line, so a "
    "heredoc or script body would be split and each line run on its own"
)
REASON_NOT_RUNNABLE = (
    "does not look like a runnable command: it must start with a known binary "
    "(python3, node, pytest, sh, bash, make, npm…) or reference {workspace}"
)
REASON_UNANCHORED = (
    "names a repository path but nothing says which repository. Validation runs "
    "from the lab's own root, so a bare relative path resolves there and the "
    "check exits 127 having tested nothing. Prefix the path with {workspace}"
)
REASON_IS_A_DOCUMENT = (
    "is a path to a file, not a command to run it. A markdown or config file "
    "handed to the shell exits 126 and says nothing about the agent. If the "
    "check is that the file exists or says something, write it as a command: "
    "`test -s {workspace}/path` or `grep -q 'text' {workspace}/path`"
)
REASON_TAUTOLOGICAL = (
    "cannot fail: there is no input under which this check returns non-zero, so "
    "it reports a pass whatever the agent did"
)
REASON_GLOB_RUNS_ONE_FILE = (
    "hands a glob to an interpreter, which runs only the FIRST matching file "
    "and passes the rest to it as positional arguments. A suite of 55 scripts "
    "is measured by whichever one sorts first"
)
REASON_LOOP_SWALLOWS_FAILURES = (
    "runs several commands in a loop but returns only the last one's status, so "
    "any number of failures ahead of one pass is reported as a pass. Add "
    "`|| exit 1` inside the loop body"
)
REASON_INTERPRETER_MISMATCH = (
    "hands a {kind} file to `{interpreter}`, which cannot run it: the check "
    "exits with a collection or syntax error before anything is measured, so "
    "it fails on every run whatever the agent did"
)
REASON_RUNS_THE_AGENTS_TESTS = (
    "runs a test suite inside the workspace, and a case with no repository "
    "starts from an empty one: every test there is one the agent wrote, so "
    "the grade becomes its own exam (ADR-0071). Write the check yourself — "
    'an inline `python3 -c "..."` that imports the agent\'s module and '
    "asserts one behaviour"
)
REASON_NO_RUBRIC = (
    "has validation commands but no scoring rubric, so nothing can grade the "
    "run: it would park in awaiting_manual_score with no scale to grade against"
)

#: Extensions that make a bare token a repository path rather than a word.
_PATH_SUFFIXES = (
    ".sh",
    ".bash",
    ".py",
    ".js",
    ".ts",
    ".md",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
    ".cfg",
    ".ini",
    ".txt",
)
_PATH_NAMES = frozenset({"Makefile", "makefile", "justfile", "Dockerfile"})

#: What tells a command where it is. Any one of them, and rule 3 stays quiet.
_ANCHORS = ("{workspace}", "{run_id}", "/app", " -C ", "--directory")

_LOOP = re.compile(r"\bfor\b.+?\bdo\b.+?\bdone\b", re.DOTALL)

#: Interpreters that take a script path as their first argument, so a glob
#: expanding to several files silently becomes "run the first one".
_SCRIPT_INTERPRETERS = frozenset({"bash", "sh", "zsh", "python", "python3", "node"})
# What each interpreter cannot run. `pytest tests/test_x.sh` collected nothing
# and exited 4 on both arms of a talos experiment; nothing refused it, and the
# 0.5 pass rate it produced read as the agents' doing. The suggestion names the
# interpreter that runs that kind of file, so the fix is one edit, not a hunt.
_INTERPRETER_CANNOT_RUN: dict[str, tuple[tuple[str, ...], str, str]] = {
    "pytest": ((".sh", ".bash", ".js", ".ts", ".mjs"), "shell/JS", "bash"),
    "python": ((".sh", ".bash", ".js", ".ts", ".mjs"), "shell/JS", "bash"),
    "python3": ((".sh", ".bash", ".js", ".ts", ".mjs"), "shell/JS", "bash"),
    "node": ((".sh", ".bash", ".py"), "shell/Python", "bash"),
    "bash": ((".py", ".js", ".ts", ".mjs"), "Python/JS", "python3"),
    "sh": ((".py", ".js", ".ts", ".mjs"), "Python/JS", "python3"),
    "zsh": ((".py", ".js", ".ts", ".mjs"), "Python/JS", "python3"),
}
_LOOP_GUARDS = ("|| exit", "||exit", "set -e", "|| return", "&& exit")

# A test runner pointed at the workspace: `pytest {workspace}/…`, `python -m
# pytest` after a `cd {workspace}`, `node {workspace}/x.test.js`, `npm test`.
# Read per shell segment, so `cd {workspace} && pytest -q` is caught and a
# `python3 -c` check that merely opens `test_results.json` is not.
_TEST_RUNNERS = frozenset({"pytest", "jest", "vitest", "mocha"})
_PYTHONS = frozenset({"python", "python3"})
_PYTHON_TEST_MODULES = frozenset({"pytest", "unittest", "nose2"})
# Make targets that conventionally run the project's tests. `check` is in: a
# drafted case with no repository has no Makefile but the agent's.
_MAKE_TEST_TARGETS = frozenset({"test", "tests", "check", "unittest", "unit"})
_TEST_FILE_RE = re.compile(
    r"(^|/)(test_[^/]*\.py|[^/]*_test\.py|[^/]*\.(test|spec)\.[cm]?[jt]sx?)$"
)
_SEGMENT_SPLIT_RE = re.compile(r"&&|\|\||;|\|")

_ALWAYS_TRUE = frozenset({"true", ":", "exit 0", "/bin/true"})
_PY_FILE_READS = ("open(", "read_text", "Path(", "glob(", "listdir", "walk(", "stat(")
_PY_PREDICATES = ("any(", "all(", "re.search", "re.match", "in text", "in content")
_PY_WORKSPACE_USES = ("sys.path", "import_module", "runpy", "subprocess", "exec(")


@dataclass(frozen=True)
class CommandProblem:
    """One command, and the single reason the lab refuses to store it."""

    command: str
    reason: str
    suggestion: str = ""

    def message(self) -> str:
        text = f"{self.command!r} {self.reason}."
        return f"{text} Try: {self.suggestion}" if self.suggestion else text


def _tokens(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _looks_like_path(token: str) -> bool:
    if not token or token.startswith(("-", "/", "~", "$")) or "://" in token:
        return False
    if token in _PATH_NAMES:
        return True
    if token.endswith(_PATH_SUFFIXES):
        return True
    # `tests/`, `benchmark/model/run-model.sh`, `docs/adr/`
    return "/" in token and not token.startswith("{")


def _interpreter_mismatch(command: str) -> tuple[str, str, str, str] | None:
    """``(interpreter, path, kind, better)`` when a command hands an
    interpreter a file it cannot run, else ``None``.

    Only bare path tokens count: ``bash -c "python3 x.py"`` carries the path
    inside a quoted script, and that is bash running python, not bash
    reading a Python file.
    """
    tokens = _tokens(command)
    for index, token in enumerate(tokens):
        rule = _INTERPRETER_CANNOT_RUN.get(token)
        if rule is None:
            continue
        suffixes, kind, better = rule
        for path in tokens[index + 1 :]:
            if any(ch.isspace() for ch in path) or path.startswith("-"):
                continue
            if path.endswith(suffixes):
                right = "python3" if path.endswith(".py") else better
                if path.endswith((".js", ".ts", ".mjs")):
                    right = "node"
                return token, path, kind, right
        return None
    return None


def _unanchored_paths(command: str) -> list[str]:
    """Repo-relative paths in a command that says nothing about where it runs."""
    if any(anchor in command for anchor in _ANCHORS):
        return []
    return [token for token in _tokens(command) if _looks_like_path(token)]


def _is_tautological(command: str) -> bool:
    text = command.strip().rstrip(";").strip()
    lowered = text.lower()
    if lowered in _ALWAYS_TRUE:
        return True
    tokens = _tokens(text)
    if not tokens:
        return False
    # `echo something` on its own: it prints, it exits 0, it checks nothing.
    if tokens[0] in {"echo", "printf"} and not any(
        operator in text for operator in ("&&", "||", "|", ";")
    ):
        return True
    # `test -e {workspace}` — the workspace is created before validation runs,
    # so its existence is a fact about the lab, not about the agent.
    if tokens[0] in {"test", "["} and any(
        token in {"{workspace}", "{workspace}/", "."} for token in tokens
    ):
        return True
    # A Python one-liner that only matches patterns it carries itself, with no
    # file ever read: it is a statement about its own literals.
    if tokens[0].startswith("python") and "-c" in tokens:
        body = tokens[tokens.index("-c") + 1] if "-c" in tokens[:-1] else ""
        reads_a_file = any(marker in body for marker in _PY_FILE_READS)
        # Importing or running what the agent wrote is reading it too: a
        # predicate over the module it returned can fail. The workspace has to
        # be named, or `import sys` alone would excuse a check of literals.
        uses_the_workspace = "{workspace}" in body and any(
            marker in body for marker in _PY_WORKSPACE_USES
        )
        only_predicates = any(marker in body for marker in _PY_PREDICATES)
        if only_predicates and not (reads_a_file or uses_the_workspace):
            return True
    return False


def _loop_swallows_failures(command: str) -> bool:
    if not _LOOP.search(command):
        return False
    return not any(guard in command for guard in _LOOP_GUARDS)


def _loop_suggestion(command: str) -> str:
    """The same loop, with the failure propagated."""
    return re.sub(r";\s*done\b", " || exit 1; done", command, count=1)


def _globbed_script(command: str) -> str:
    """The glob an interpreter was handed as its script argument, or ''.

    `bash tests/test_*.sh` reads as "run the suite" and means "run whichever
    file sorts first, with the others as its arguments". Verified: two scripts,
    the second never executes and the exit code is the first one's.
    """
    if _LOOP.search(command) or any(op in command for op in ("&&", "||", "|", ";")):
        return ""  # a loop or a pipeline is a different shape
    tokens = _tokens(command)
    if len(tokens) < 2 or tokens[0].rsplit("/", 1)[-1] not in _SCRIPT_INTERPRETERS:
        return ""
    argument = next((token for token in tokens[1:] if not token.startswith("-")), "")
    return argument if "*" in argument or "?" in argument else ""


def _glob_suggestion(interpreter: str, glob: str) -> str:
    """The loop that actually runs every file the glob names, and fails on any."""
    return f'for t in {glob}; do {interpreter} "$t" || exit 1; done'


def anchor_workspace(command: str) -> str:
    """The same command with every repo-relative path anchored to the workspace.

    Validation executes from the lab's own root, so a bare relative path
    resolves *there*. The audit's hint contract asks for a command relative to
    the repository root, and `{workspace}` is what that root is called at
    validation time — so this is a translation between two conventions, not a
    guess. A command that already says where it is comes back unchanged.
    """
    text = (command or "").strip()
    if not text or not _unanchored_paths(text):
        return text
    anchored = text
    for token in _unanchored_paths(text):
        anchored = re.sub(
            rf"(?<![\w/{{]){re.escape(token)}(?![\w])",
            "{workspace}/" + token.lstrip("./"),
            anchored,
            count=1,
        )
    return anchored


def check_command(command: str) -> CommandProblem | None:
    """The first reason this command would not measure anything, or None."""
    if "\n" in command:
        return CommandProblem(command, REASON_MULTILINE)
    if not command.strip():
        return None
    # Before "is it runnable": `true` and `echo ok` run perfectly well, and
    # saying they do not is the wrong complaint about them.
    if _is_tautological(command):
        return CommandProblem(command, REASON_TAUTOLOGICAL)
    if names_a_document(command):
        return CommandProblem(command, REASON_IS_A_DOCUMENT)
    if not is_executable_command(command):
        return CommandProblem(command, REASON_NOT_RUNNABLE)
    mismatch = _interpreter_mismatch(command)
    if mismatch:
        interpreter, path, kind, better = mismatch
        return CommandProblem(
            command,
            REASON_INTERPRETER_MISMATCH.format(kind=kind, interpreter=interpreter),
            anchor_workspace(f"{better} {path}"),
        )
    if _loop_swallows_failures(command):
        return CommandProblem(
            command, REASON_LOOP_SWALLOWS_FAILURES, _loop_suggestion(command)
        )
    glob = _globbed_script(command)
    if glob:
        interpreter = _tokens(command)[0]
        return CommandProblem(
            command,
            REASON_GLOB_RUNS_ONE_FILE,
            anchor_workspace(_glob_suggestion(interpreter, glob)),
        )
    if _unanchored_paths(command):
        return CommandProblem(command, REASON_UNANCHORED, anchor_workspace(command))
    return None


def _runs_a_workspace_test_suite(command: str) -> bool:
    """Whether this command hands the workspace's own tests to a test runner.

    A segment counts when it runs a test runner (``pytest``, ``python -m
    pytest``, ``jest``…, ``npm test``) or an interpreter on a test file, and
    either names ``{workspace}`` or follows a ``cd {workspace}``. A
    ``python3 -c`` check is never one: its argument is the lab's own code,
    whatever file names appear inside it.
    """
    in_workspace = False
    for segment in _SEGMENT_SPLIT_RE.split(command):
        tokens = _tokens(segment.strip())
        if not tokens:
            continue
        tokens = _strip_launch_prefix(tokens)
        if not tokens:
            continue
        head = tokens[0].rsplit("/", 1)[-1]
        names_workspace = "{workspace}" in segment
        if head == "cd":
            in_workspace = names_workspace
            continue
        if head in _PYTHONS and "-c" in tokens:
            continue
        if _is_test_runner(head, tokens) and (names_workspace or in_workspace):
            return True
    return False


def _strip_launch_prefix(tokens: list[str]) -> list[str]:
    """``tokens`` without what only changes *how* the command is launched.

    Leading ``VAR=value`` assignments, and ``timeout`` with its own options and
    duration. Only those: dropping every ``-…`` token after ``timeout`` also
    dropped the ``-m`` of ``python -m pytest`` and hid the runner.
    """
    while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
        tokens = tokens[1:]
    if tokens[:1] == ["timeout"]:
        rest = tokens[1:]
        while rest and rest[0].startswith("-"):
            # `-k 5` / `-s KILL` take a value; `--foreground` does not.
            takes_value = rest[0] in {"-k", "-s", "--kill-after", "--signal"}
            rest = rest[2:] if takes_value else rest[1:]
        if rest and re.match(r"^\d+(\.\d+)?[smhd]?$", rest[0]):
            rest = rest[1:]
        tokens = rest
    return tokens


def _is_test_runner(head: str, tokens: list[str]) -> bool:
    """Whether this segment's program runs a test suite."""
    args = tokens[1:]
    if head in _TEST_RUNNERS:
        return True
    if head in _PYTHONS:
        module = args[args.index("-m") + 1] if "-m" in args[:-1] else ""
        return module in _PYTHON_TEST_MODULES or any(
            _TEST_FILE_RE.search(arg) for arg in args
        )
    if head in {"node", "deno", "bun"}:
        return any(_TEST_FILE_RE.search(arg) for arg in args)
    if head in {"npx", "pnpm", "yarn"} and set(args[:1]) & _TEST_RUNNERS:
        return True
    if head in {"npm", "pnpm", "yarn"}:
        return args[:1] == ["test"] or args[:2] == ["run", "test"]
    if head in {"make", "gmake"}:
        # The target, not the options and their values: `make -C dir test`.
        targets = [
            arg
            for position, arg in enumerate(args)
            if not arg.startswith("-")
            and "=" not in arg
            and not (position > 0 and args[position - 1] in {"-C", "-f"})
        ]
        return bool(set(targets) & _MAKE_TEST_TARGETS)
    return False


def check_drafted_command(
    command: str, *, in_repository: bool
) -> CommandProblem | None:
    """:func:`check_command`, plus the rule only a drafted case can break.

    A drafted case with no repository starts from an empty workspace, so a
    check that runs a test suite there runs the agent's own tests. Kept out of
    :func:`check_command` on purpose: a hand-written case that ships its tests
    in a starter, or one written for a repository, runs a suite the agent did
    not write, and the editor has no way to tell those apart.
    """
    problem = check_command(command)
    if problem is not None:
        return problem
    if not in_repository and _runs_a_workspace_test_suite(command):
        return CommandProblem(command, REASON_RUNS_THE_AGENTS_TESTS)
    return None


def check_commands(commands: list[str]) -> list[CommandProblem]:
    """Every reason the lab refuses this set of validation commands."""
    problems = [check_command(command) for command in commands]
    return [problem for problem in problems if problem is not None]


REASON_REQUIREMENT_NOT_INSTALLABLE = (
    "is not a requirement the validation setup step will install: it takes a "
    "plain distribution name with an optional version (`numpy==2.4.6`, "
    "`pandas>=2.2`, `requests[socks]`) and skips anything else, so this line "
    "would be shown on the case and never installed, and every check that "
    "imports it would end inconclusive"
)


def check_requirements(metadata: object) -> list[str]:
    """Every ``validation_requirements`` entry the setup step would skip.

    ``build_validation_spec`` drops what it cannot pass to pip, silently: the
    case would show the dependency and the run would never install it. Refused
    here instead, where the person typing it can still fix it.
    """
    raw = (
        metadata.get("validation_requirements") if isinstance(metadata, dict) else None
    )
    if raw is None:
        return []
    if not isinstance(raw, list):
        return [
            "validation_requirements must be a list of requirements, one per entry."
        ]
    return [
        f"{str(item)!r} {REASON_REQUIREMENT_NOT_INSTALLABLE}."
        for item in raw
        if not isinstance(item, str) or not requirement_is_installable(item)
    ]


#: Case types whose score comes from the objective verdict, not from a rubric.
#: SWE-bench's official score is binary and fully automatic (resolved iff every
#: FAIL_TO_PASS passes and no PASS_TO_PASS regresses), so demanding a written
#: scale for one would be demanding a second opinion on a fact.
OBJECTIVELY_SCORED_TYPES = frozenset({"swe_bench_instance", "swe_pro_instance"})


def check_case(
    commands: list[str],
    scoring_rubric: str,
    success_criteria: dict[str, str],
    case_type: str = "",
) -> list[str]:
    """Everything wrong with a case's measurable half, as messages.

    Empty means the case can be stored. A case with commands and no rubric is
    refused here because the alternative is silence: all four talos runs parked
    in ``awaiting_manual_score`` with an empty rubric and no record of why, and
    that read as a scoring backlog rather than as the case defect it was.
    """
    messages = [problem.message() for problem in check_commands(commands)]
    runnable = [command for command in commands if command.strip()]
    if case_type in OBJECTIVELY_SCORED_TYPES:
        return messages
    if runnable and not (scoring_rubric or "").strip() and not success_criteria:
        messages.append(
            f"The case {REASON_NO_RUBRIC}. Write a scoring rubric, or state "
            "success_criteria the judge can read."
        )
    return messages
