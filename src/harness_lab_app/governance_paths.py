"""Where an agent harness lives in a repository, in one place.

Four modules used to carry their own copy of this taxonomy — the readiness
classifier, the audit evidence collector, the run's governance surface, and the
case drafter's skill lookup. They drifted, and the drift was not academic: a
repository that keeps its skills in a root-level ``skills/`` directory rather
than ``.claude/skills/`` was reported as having none. The audit then told a
reader that a repository with sixteen skills and four hooks had no harness at
all, which is a worse answer than "unknown".

Two rules shape what is listed here:

**Dotted roots are trusted; bare roots must prove themselves.** ``.claude/`` and
``.agents/`` exist for one purpose, so anything under them counts. A bare
``skills/`` or ``hooks/`` at the repository root is a directory name anybody
might use, so it counts only when it carries the marker that makes it
unambiguous — a ``SKILL.md`` inside, a script shaped like a hook. A root
``agents/`` full of markdown is *not* included for exactly this reason: the
name alone does not distinguish an agent harness from a product directory, and
:func:`harness_present` would then claim a harness the repository does not have.

**Depth is not a signal.** The audit used to require an agent definition to sit
exactly one level below its root (``path.count("/") == 2``), which silently
dropped ``.claude/agents/review/deep.md``. Nesting says nothing about whether a
file addresses an agent.

Pure: constants and predicates over path strings, no I/O, no app imports.
"""

from __future__ import annotations

import re

#: Marker that makes a directory a skill directory, whatever it is called.
SKILL_FILE = "/SKILL.md"

#: Roots whose purpose is unambiguous: everything markdown under them counts.
DOTTED_SKILL_ROOTS: tuple[str, ...] = (
    ".claude/skills/",
    ".github/skills/",
    ".github/skills-external/",
    ".agents/skills/",
)

#: Roots that only count when the ``SKILL.md`` marker is present.
BARE_SKILL_ROOTS: tuple[str, ...] = ("skills/",)

SKILL_ROOTS: tuple[str, ...] = DOTTED_SKILL_ROOTS + BARE_SKILL_ROOTS

#: Agent definitions. No bare root — see the module note.
AGENT_ROOTS: tuple[str, ...] = (
    ".claude/agents/",
    ".github/agents/",
    ".agents/agents/",
)

#: Claude Code plugins, as a plugin marketplace repository lays them out: each
#: plugin under ``plugins/<plugin>/`` carries its own ``skills/`` and
#: ``agents/``. So a repository that ships its harness as plugins has one
#: skills root *per plugin*, which a fixed prefix cannot name. Missing them is
#: not hypothetical: audits of ``xmartlabs/agent-skills`` were shown 2 of its
#: skills and none of its agents, and five repeated audits said nothing about
#: the eleven under ``plugins/``.
#:
#: The layout is the marker, the way ``SKILL.md`` is for a bare ``skills/``:
#: a markdown file two named levels below ``plugins/`` in an ``agents/`` or
#: ``skills/<name>/SKILL.md`` slot is that plugin's harness, not a product
#: directory that happens to be called ``agents``.
PLUGINS_ROOT = "plugins/"

_PLUGIN_SKILL = re.compile(r"^plugins/([^/]+)/skills/(.+)/SKILL\.md$")
_PLUGIN_AGENT = re.compile(r"^plugins/([^/]+)/agents/(.+)\.md$")

#: The same two slots as filesystem globs, relative to :data:`PLUGINS_ROOT`,
#: for code that walks a checkout rather than a path list.
PLUGIN_SKILL_GLOB = "*/skills/*/SKILL.md"
PLUGIN_AGENT_GLOB = "*/agents/**/*.md"


def is_plugin_skill_path(path: str) -> bool:
    """Whether a path is a skill shipped inside a Claude Code plugin."""
    return _PLUGIN_SKILL.match(path) is not None


def is_plugin_agent_path(path: str) -> bool:
    """Whether a path is an agent shipped inside a Claude Code plugin."""
    return _PLUGIN_AGENT.match(path) is not None


#: Slash commands / prompts an agent runtime exposes.
COMMAND_ROOTS: tuple[str, ...] = (
    ".claude/commands/",
    ".github/commands/",
    ".agents/commands/",
)

#: Hook directories. The dotted ones belong to an agent runtime; the bare ones
#: are where a team keeps git-side hooks, and only script-shaped files there
#: count (a policy YAML sitting beside them is configuration, not a hook).
DOTTED_HOOK_ROOTS: tuple[str, ...] = (".claude/hooks/", ".agents/hooks/")
BARE_HOOK_ROOTS: tuple[str, ...] = ("hooks/", "githooks/", ".githooks/")
HOOK_ROOTS: tuple[str, ...] = DOTTED_HOOK_ROOTS + BARE_HOOK_ROOTS

#: Extensions a hook script may carry. An empty extension is included because
#: git's own hooks (``pre-push``, ``pre-commit``) have none.
HOOK_SCRIPT_SUFFIXES: tuple[str, ...] = (".sh", ".bash", ".zsh", ".py", ".js", ".ts")

#: Git hook names, so a bare root's extensionless file is recognised as one
#: rather than merely tolerated.
GIT_HOOK_NAMES: frozenset[str] = frozenset(
    {
        "applypatch-msg",
        "commit-msg",
        "post-checkout",
        "post-commit",
        "post-merge",
        "pre-applypatch",
        "pre-commit",
        "pre-merge-commit",
        "pre-push",
        "pre-rebase",
        "prepare-commit-msg",
        "update",
    }
)

CI_ROOTS: tuple[str, ...] = (".github/workflows/",)
ADR_ROOTS: tuple[str, ...] = ("docs/adr/", "docs/adrs/", "docs/decisions/")
MEMORY_ROOTS: tuple[str, ...] = ("memory/",)

#: Single files that address an agent at the repository root.
RULES_FILES: tuple[str, ...] = (
    "CLAUDE.md",
    "AGENTS.md",
    "OPENCODE.md",
    "GEMINI.md",
    "LOCAL_AGENT.md",
    ".cursorrules",
    ".windsurfrules",
    ".github/copilot-instructions.md",
    ".agents/rules/GEMINI.md",
)

#: Where a hook declaration may be configured rather than scripted.
CLAUDE_SETTINGS: tuple[str, ...] = (".claude/settings.json",)
PRE_COMMIT_CONFIG = ".pre-commit-config.yaml"
HUSKY_DIR = ".husky/"


def is_skill_path(path: str) -> bool:
    """Whether a path is a skill definition, at any depth under any root.

    Includes a skill shipped inside a plugin (``plugins/<p>/skills/<s>/``).
    """
    if not path.endswith(SKILL_FILE):
        return False
    return path.startswith(SKILL_ROOTS) or is_plugin_skill_path(path)


def is_agent_path(path: str) -> bool:
    """Whether a path is an agent definition, at any depth under any root.

    Includes an agent shipped inside a plugin (``plugins/<p>/agents/``).
    """
    if not path.endswith(".md"):
        return False
    return path.startswith(AGENT_ROOTS) or is_plugin_agent_path(path)


def is_command_path(path: str) -> bool:
    """Whether a path is a slash-command definition."""
    return path.endswith(".md") and path.startswith(COMMAND_ROOTS)


def is_hook_script(path: str) -> bool:
    """Whether a path is a hook the agent or git runtime would fire.

    Under a dotted root anything that is a file counts. Under a bare root the
    file has to look like a script, so a ``hooks/policy.yml`` beside three
    shell hooks is not reported as a fourth hook.
    """
    if path.endswith("/"):
        return False
    if path.startswith(DOTTED_HOOK_ROOTS):
        return True
    if not path.startswith(BARE_HOOK_ROOTS):
        return False
    name = path.rsplit("/", 1)[-1]
    if name in GIT_HOOK_NAMES or name.endswith(HOOK_SCRIPT_SUFFIXES):
        return True
    # No extension at all: git's own hooks look like this, and so do the
    # hand-rolled ones a team drops beside them.
    return "." not in name


def skill_name(path: str) -> str:
    """The skill's name: its directory, whatever root it sits under.

    A plugin skill is named ``<plugin>:<skill>``, the name Claude Code invokes
    it by, which also keeps two plugins' same-named skills apart.
    """
    plugin = _PLUGIN_SKILL.match(path)
    if plugin:
        return f"{plugin.group(1)}:{plugin.group(2)}"
    for root in SKILL_ROOTS:
        if path.startswith(root):
            return path[len(root) :].removesuffix(SKILL_FILE).strip("/")
    return path.removesuffix(SKILL_FILE)


def agent_name(path: str) -> str:
    """The agent's name: its path below the agents root, without ``.md``.

    A plugin agent is named ``<plugin>:<agent>``, as Claude Code addresses it.
    """
    plugin = _PLUGIN_AGENT.match(path)
    if plugin:
        return f"{plugin.group(1)}:{plugin.group(2)}"
    return relative_name(path, AGENT_ROOTS, ".md")


def relative_name(path: str, roots: tuple[str, ...], suffix: str = "") -> str:
    """A path's name relative to the first root that owns it."""
    for root in roots:
        if path.startswith(root):
            return path[len(root) :].removesuffix(suffix)
    return path.removesuffix(suffix)
