# ADR-0070: The interface speaks English and Spanish

- **Status:** Accepted
- **Date:** 2026-10-22
- **Deciders:** Marcos Soto
- **Related:** [ADR-0064](0064-an-experiment-shows-its-uncertainty.md),
  [ADR-0069](0069-a-model-is-judged-by-the-tier-it-does-reliably.md)

## Context

The lab is being opened to teams whose working language is Spanish. Its interface was
English only, and the repository rule — code artifacts in English, user interaction in
the user's language (`.github/standards.md`) — already allows a bilingual interface as
long as identifiers, comments and docs stay in English.

The interface holds roughly 1,500 strings across 50 components. A lab whose words carry
statistical meaning — *reliable*, *established*, *95% interval* — cannot afford a
translation where the same term is rendered three ways, nor a page where a new string
ships in one language only.

## Decision

1. **An EN | ES switch at the top of the sidebar**, under the brand, sets the interface language. The choice is
   remembered per browser; the first visit follows the browser's language.
2. **No i18n library.** `frontend/src/i18n.tsx` holds a React context and
   `defineMessages({ en, es })`. Each component keeps its strings beside its markup.
   The Spanish block is typed against the English one: a missing key, an extra key or
   a string where a function of a count is expected fails `make check-frontend`.
3. **Strings that take values are functions** (`(count: number) => string`), so each
   language owns its word order and plurals instead of concatenating fragments.
4. **One glossary** fixes the Spanish for every term of the method (below). Product
   names, model ids, harness names, file paths, commands and the names *Harness Lab*,
   *skill*, *benchmark* and *pass^k* are not translated. Neither is any value the API
   keeps as an identifier — condition labels, experiment and case ids, enum values —
   because run records and the archive key on it; an experiment's name, question and
   descriptions are prose and follow the interface language.
5. **Scope of this decision: the interface only.** Text the backend composes — verdict
   headlines, readings, reasons, check results — and the markdown guides stay in English.
   Moving backend prose to message codes is a later step, starting with what a demo
   shows.

### Glossary

| English | Spanish |
|---|---|
| run / runs | corrida / corridas |
| case | caso |
| experiment | experimento |
| arm / condition | brazo / condición |
| harness | arnés |
| harness level | nivel de arnés |
| finding | hallazgo |
| evidence | evidencia |
| repository | repositorio |
| audit | auditoría |
| pass rate | tasa de éxito |
| passed / failed | pasó / falló |
| reliable | confiable |
| capable | capaz |
| not established / established | no establecido / establecido |
| 95% interval | intervalo del 95% |
| lower bound | cota inferior |
| effect | efecto |
| interaction | interacción |
| tier | nivel |
| frontier | frontera |
| score / scoring | puntaje / puntuación |
| judge | juez |
| gateway | gateway |
| prompt | prompt |

Spanish strings use the Rioplatense *voseo* the team speaks (*elegí*, *corré*), sentence
case, and keep the English text's register: plain, specific, no hype.

## Consequences

- Every new string needs both languages; the type check makes that the path of least
  resistance rather than a review comment.
- A Spanish interface still shows English verdicts and guides until the backend step
  lands; the switch says so nowhere, which is a known gap.
- Spanish runs about a fifth longer than English. Narrow controls (badges, segmented
  buttons, table headers) were checked at both lengths.
