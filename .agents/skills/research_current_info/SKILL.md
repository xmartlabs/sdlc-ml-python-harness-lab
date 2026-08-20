---
name: research_current_info
description: Use when the user asks for up-to-date or current information, to confirm something is still accurate, or when a task depends on facts that may have changed since training (library versions, APIs, pricing, releases, news, current best practices). Runs a governed web search with a curated domain allow/deny policy and cited, recency-checked results.
---

# Skill: research_current_info

## Purpose

Ground answers in current, verifiable web sources instead of stale memory, applying a
curated domain policy (prefer trustworthy/primary sources, avoid noisy ones) and always
citing what was found with its retrieval date.

## When To Use

- The user says "latest", "current", "today", "still true?", "has this changed?".
- A task depends on volatile facts: library/tool versions, API signatures, prices,
  release dates, security advisories, current best practices.
- **Skip** for timeless/internal knowledge — do not search when stable reasoning or the
  repo's own code already answers it.

## Required Input

- The question or claim to verify (topic + what "current" means here).
- Optional: time window (e.g. "last 12 months"), and per-query domains to prefer/avoid
  on top of the policy below.

## Domain Policy (EDIT THIS — it is your governance)

This section is the curated allow/deny list. Keep it under version control and tune it
to your stack. The engine propagates it to every tool on `make sync-skills`.

**Prefer (allow-list — primary / authoritative):**

- `docs.python.org`, `peps.python.org`
- `docs.astral.sh` (uv, ruff), official docs of the libraries in `pyproject.toml`
- `github.com` (source, releases, issues), `pypi.org`
- `developer.mozilla.org`, official cloud/provider docs, `arxiv.org`

**Avoid (deny-list — noisy / low-signal for authoritative detail):**

- SEO content farms and auto-generated mirror sites.
- Examples often deprioritized for API correctness (keep or drop as you see fit):
  `w3schools.com`, `geeksforgeeks.org` — fine for a quick snippet, weak as a source of truth.

**Rule of precedence:** primary/official source > reputable secondary (well-known blogs,
Stack Overflow) > everything else. If a query genuinely needs a domain not on the
allow-list, use it but flag that it is outside the policy.

## Execution Rules

1. Use the runtime's web search tool. On Claude Code, pass the policy to `WebSearch`
   via `allowed_domains` / `blocked_domains`; on runtimes without domain filters, apply
   the policy by filtering/ranking results manually.
2. Cross-check every load-bearing claim against **at least two** independent sources.
3. Prefer primary/official sources; treat blogs and forums as secondary corroboration.
4. Cite every external claim: title — URL — retrieval date. Never fabricate a URL.
5. Distinguish verified facts from speculation; call out when sources disagree or when
   information may already be stale.
6. If search returns nothing usable within the policy, say so explicitly rather than
   guessing — then ask whether to widen the allow-list.
7. Keep all artifacts in English.

## Output Format

- A concise answer grounded in the sources.
- `Sources:` a list of `title — URL — date`.
- `Confidence:` high/medium/low with any caveats, conflicts, or staleness notes.
