# Skills Catalog

Operational skills must receive explicit input and return structured output.

Each internal skill file must include YAML frontmatter with:

- `name`: file name without `.md`
- `description`: semantic trigger description for native skill discovery

## Skill Sources

- Internal curated skills: `.github/skills/`
- External synced skills: `.github/skills-external/`
- Claude Code native generated links: `.claude/skills/`
- Antigravity native generated copies: `.agents/skills/`
- OpenCode native generated links: `.opencode/skills/`

Precedence rule:

- If a skill exists in both places, prefer `.github/skills/`.

Refresh all native adapter views after internal or external skill changes:

```bash
make sync-skills
```

Or refresh individually:

```bash
make setup-claude-skills
make setup-antigravity-skills
make setup-opencode-skills
```

## Available Skills

- `bootstrap_project`
- `brainstorm_quick`
- `create_domain_contract`
- `create_mle_agent_package`
- `generate_e2e_tests`
- `generate_implementation_docs`
- `refactor_to_clean_architecture`
- `validate_module_structure`
- `generate_migration_plan`
- `plan_and_execute_feature`
- `research_current_info`

## Governance Dependency

All skills must comply with:

- `.github/architecture.md`
- `.github/standards.md`
- `.github/domain-boundaries.md`

For complex tasks, also comply with:

- `.github/orchestration.md`
