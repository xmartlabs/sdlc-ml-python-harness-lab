# tetris-v1

**¿Qué corro?** Un Tetris en Python. El modelo lo construye desde cero; tú lo corres, lo observás, y le ponés un score de 0 a 10.

**¿Cómo arranco una corrida?** `make bench-run CASE=tetris-v1 RUN=tetris-v1-A-003 PROMPT=incremental EXECUTOR_MODEL=gateway/lmstudio-qwen3.6-35b-build SCORE_MODE=manual`

**¿Cómo la cierro?** `make bench-close RUN=tetris-v1-A-003 SCORE=7 INTERVENTIONS=1`

**¿Dónde veo el resultado?** `make bench-results`

---

## Qué prompt pegar en OpenCode

Después de `make bench-run`, el prompt final de la corrida queda en:

```
data/runs/<run_id>/prompt.md
```

`bench-run` lo copia al clipboard automáticamente. En OpenCode, pegá con `Cmd+V`.

Si el clipboard no funcionó, copialo manualmente:

```bash
cat data/runs/tetris-v1-A-003/prompt.md | pbcopy
```

**No pegar directamente `prompt_incremental.md`, `prompt_naive.md` ni `prompt_conductor.md`.**
Esos son los templates fuente. `data/runs/<run_id>/prompt.md` es el prompt final de la corrida,
con el workspace, run_id y metadatos ya inyectados.

---

## Quickstart

```bash
# Ver el estado de todas las corridas (pending / running / completed + score)
make bench-results

# Preparar la corrida: genera workspace, prompt, metadata.json, run.env
# score-mode=manual significa: ejecutar manualmente y cerrar con bench-close
make bench-run \
  CASE=tetris-v1 \
  RUN=tetris-v1-A-003 \
  PROMPT=incremental \
  EXECUTOR_MODEL=gateway/lmstudio-qwen3.6-35b-build \
  SCORE_MODE=manual

# [Abrir OpenCode, pegar el prompt, dejar correr el modelo, observar el resultado]

# Cerrar la corrida y registrar el score (actualiza runs.yaml y results.csv)
make bench-close RUN=tetris-v1-A-003 SCORE=7 INTERVENTIONS=1

# Ver estado actualizado
make bench-results
```

Ver qué haría sin escribir nada:

```bash
make bench-run \
  CASE=tetris-v1 \
  RUN=tetris-v1-A-003 \
  PROMPT=incremental \
  EXECUTOR_MODEL=gateway/lmstudio-qwen3.6-35b-build \
  DRY_RUN=1
```

Score interactivo (te pregunta score/interventions después de correr):

```bash
make bench-run \
  CASE=tetris-v1 \
  RUN=tetris-v1-A-003 \
  PROMPT=incremental \
  EXECUTOR_MODEL=gateway/lmstudio-qwen3.6-35b-build \
  SCORE_MODE=interactive
```

---

## Workflow por corrida

1. `make bench-results` — identificar la próxima corrida en estado `pending`
2. `make bench-run CASE=... RUN=... PROMPT=... EXECUTOR_MODEL=... SCORE_MODE=manual`
   — genera workspace, prompt, metadata.json, run.env; copia prompt al clipboard
3. Abrir OpenCode: `make opencode`
4. Verificar modelo activo en OpenCode (ver `EXECUTOR_MODEL` en el output del paso 2)
5. Pegar el prompt con `Cmd+V`
6. Dejar correr el modelo, observar el resultado en el workspace
7. Correr `python workspace/<run_id>/main.py` y scorear contra `scoring.md`
8. `make bench-close RUN=<run_id> SCORE=<n> INTERVENTIONS=<n>` — guarda artefactos, actualiza `runs.yaml` y `results.csv`, hace commit
9. Cuando las 3 reps de una celda están completas, actualizar `reports/2026-06-tetris-v1.md`

---

## Archivos de este directorio

| Archivo | Rol |
|---------|-----|
| `task.yaml` | Definición congelada de la tarea (descripción, base commit, constraints, prompts) |
| `runs.yaml` | Registro autoritativo — 27 corridas planificadas en 9 celdas × 3 reps |
| `scoring.md` | Rubric de score — leer antes de scorear cualquier corrida |
| `prompt_naive.md` | Prompt baseline, una sola respuesta (variant id: `tetris_naive_v1`) |
| `prompt_incremental.md` | Prompt incremental con milestones (variant id: `tetris_incremental_v1`) |
| `prompt_conductor.md` | Prompt conductor/planner (variant id: `tetris_conductor_v1`) |
| `results.csv` | Tabla de comparación — solo corridas completadas y scored |
| `results/` | Reservado para artefactos futuros, vacío por ahora |

---

## Flujo de datos

```
runs.yaml                ← registro de las 27 corridas planificadas (fuente de verdad)
     ↓
make bench-run           ← prepara workspace, prompt, metadata.json, run.env
     ↓
[ejecución manual en OpenCode + score contra scoring.md]
     ↓
make bench-close         ← graba score en runs.yaml, actualiza results.csv, commit
     ↓
results.csv              ← actualizada automáticamente por bench-close (solo scored)
     ↓
reports/2026-06-tetris-v1.md  ← actualizar manualmente cuando una celda completa las 3 reps
```

`make bench-results` muestra una vista en vivo de `runs.yaml` — incluye corridas pending.
`results.csv` es el artefacto de comparación limpio para corridas scored únicamente.
Son dos cosas distintas.

---

## Notas operativas

**Prompt injection:** `make bench-run` resuelve el prompt correcto desde `task.yaml` usando el argumento `PROMPT=<nombre_corto>`. Siempre pasá `PROMPT=` explícito — `naive`, `incremental` o `conductor`. Si no se pasa, se intenta auto-detectar del campo `prompt_variant` en `runs.yaml`.

**Tokens y costo:** `bench-run` intenta traer datos de LiteLLM y Langfuse automáticamente después de ejecutar. Si no están disponibles, los campos quedan `null` en `usage.json` y `results.csv`. No estimar ni inferir costos — solo valores reales.

**Artefactos por corrida:** los reportes individuales viven en `data/runs/<run_id>/report.md`.
El template canónico es `templates/run-report-template.md`.
