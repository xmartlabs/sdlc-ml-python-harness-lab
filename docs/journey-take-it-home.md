# Take it home

The lab is where a harness is measured; your project is where it is used. When an
experiment says a skill, an agent or the whole governance helps, copy it into your
repository — and take only what earned its place.

## Three sizes

| Take | Gets you | Command |
|---|---|---|
| **One skill** | the skill file in your tool's skill folder, nothing else | `make harness-export REPO=../my-project SKILLS=systematic_debugging GOVERNANCE=0` |
| **Some skills + the rules** | those skills, the rules file (`CLAUDE.md` / `AGENTS.md`), the agents and the governance documents | `make harness-export REPO=../my-project SKILLS=a,b` |
| **The whole harness** | every skill, agent and rule the lab runs with | `make harness-export REPO=../my-project` |

Add `TOOL=opencode` (or `codex`, `antigravity`) for a tool other than Claude Code —
each reads its skills from its own folder, and the export writes the right one.

## It previews first

Without `APPLY=1` nothing is written. The export copies your repository to a
throwaway folder, projects the harness there, and prints exactly which files it
would add or change — and which of *yours* it would replace:

```
Would write into /home/me/my-project for claude:
  skills (1): systematic_debugging
  agents: 0
  files: 1
    .claude/skills/systematic_debugging/SKILL.md

Nothing was written. Add APPLY=1 to write it.
```

Run it again with `APPLY=1` to write. Three guarantees make that safe:

- **It refuses a repository with uncommitted work**, so the export arrives as its
  own diff: `git diff` reviews it, `git checkout -- . && git clean -fd` undoes it.
- **A file of yours it replaces is kept** next to the new one as
  `<file>.native.bak` — your own `CLAUDE.md` is never lost.
- **It never writes into the lab itself.**

It is the same projection the `injected` experiment arm uses, so what you copy is
exactly what was measured.

## Which skill names exist

```bash
make harness-export LIST=1
```

prints every skill you can export, with its kind. A skill you staged and
**promoted** on the Skills page joins that list, so it exports the same way.

## Starting a new project instead

A new project is better started from the template itself, which brings the
governance, the CI gate and the working loop together:
[`ml-python-base`](https://github.com/xmartlabs/sdlc-ml-python-base) →
*Use this template* → `make init`. To keep a project born from the template up to
date with its later releases: [updating existing projects](updating-existing-projects.md)
and [template sync](template-sync.md).

## Then measure it there

Copying a harness that helped on the lab's cases is a hypothesis about your
repository, not a result. Register your repository and run **Native vs the lab
harness** before and after
([bring your repo or skill](journey-bring-your-repo-or-skill.md)).

**Next:** [improve the template](journey-improve-the-template.md).
