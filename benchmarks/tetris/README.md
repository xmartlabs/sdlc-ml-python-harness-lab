# tetris-v1

**¿Qué corro?** Un Tetris en Python. El modelo lo construye desde cero; tú lo corrés, lo observás, y le ponés un score de 0 a 10.

**¿Cómo arranco una corrida?** Ver sección [Corridas listas para copiar](#corridas-listas-para-copiar) abajo.

**¿Cómo la cierro?** `make bench-close RUN=tetris-v1-A-003 SCORE=7 INTERVENTIONS=1`

**¿Dónde veo el resultado?** `make bench-results`

> **`bench-run` es el camino principal.** `bench-start` y `bench-close` son primitivas de nivel bajo — usarlas solo para flujos manuales avanzados. `bench-run` hace todo: prepara, ejecuta y cierra en un solo comando.

---

## Corridas listas para copiar

Primero, ver qué corridas están pendientes:

```bash
make bench-results
```

### Qwen 3.6 35B local (Celda A) — opencode puro

```bash
# Un solo incremento, score interactivo al terminar
make bench-run \
  CASE=tetris-v1 \
  RUN=tetris-v1-A-003 \
  PROMPT=incremental \
  EXECUTOR_MODEL=gateway/lmstudio-qwen3.6-35b-build \
  SCORE_MODE=interactive

# Múltiples incrementos automáticos (hasta 6 pasos encadenados)
make bench-run \
  CASE=tetris-v1 \
  RUN=tetris-v1-A-003 \
  PROMPT=incremental \
  EXECUTOR_MODEL=gateway/lmstudio-qwen3.6-35b-build \
  MAX_STEPS=6 \
  SCORE_MODE=interactive

# Preparar workspace y prompt, ejecutar manualmente con make opencode, cerrar luego
make bench-run \
  CASE=tetris-v1 \
  RUN=tetris-v1-A-003 \
  PROMPT=incremental \
  EXECUTOR_MODEL=gateway/lmstudio-qwen3.6-35b-build \
  SCORE_MODE=manual
# → luego: make opencode → pegá el prompt → make bench-close RUN=... SCORE=7 INTERVENTIONS=0

# Retomar una corrida que se interrumpió (workspace ya existe)
make bench-run \
  CASE=tetris-v1 \
  RUN=tetris-v1-A-003 \
  PROMPT=incremental \
  EXECUTOR_MODEL=gateway/lmstudio-qwen3.6-35b-build \
  MAX_STEPS=3 \
  RESUME=1 \
  SCORE_MODE=interactive

# Dry run: ver todo lo que pasaría sin escribir nada
make bench-run \
  CASE=tetris-v1 \
  RUN=tetris-v1-A-003 \
  PROMPT=incremental \
  EXECUTOR_MODEL=gateway/lmstudio-qwen3.6-35b-build \
  DRY_RUN=1
```

### Nemotron Super 120B planner + Qwen 35B executor (Celda F)

```bash
# Un solo paso — conductor prompt, score interactivo
make bench-run \
  CASE=tetris-v1 \
  RUN=tetris-v1-F-001 \
  PROMPT=conductor \
  PLANNER_MODEL=gateway/nemotron-120b-planner \
  EXECUTOR_MODEL=gateway/lmstudio-qwen3.6-35b-build \
  SCORE_MODE=interactive

# Múltiples incrementos encadenados
make bench-run \
  CASE=tetris-v1 \
  RUN=tetris-v1-F-001 \
  PROMPT=conductor \
  PLANNER_MODEL=gateway/nemotron-120b-planner \
  EXECUTOR_MODEL=gateway/lmstudio-qwen3.6-35b-build \
  MAX_STEPS=6 \
  SCORE_MODE=interactive

# Dry run
make bench-run \
  CASE=tetris-v1 \
  RUN=tetris-v1-F-001 \
  PROMPT=conductor \
  PLANNER_MODEL=gateway/nemotron-120b-planner \
  EXECUTOR_MODEL=gateway/lmstudio-qwen3.6-35b-build \
  DRY_RUN=1
```

### Nemotron 120B planner + Qwen 3.5 9B executor (Celda G)

```bash
make bench-run \
  CASE=tetris-v1 \
  RUN=tetris-v1-G-001 \
  PROMPT=conductor \
  PLANNER_MODEL=gateway/nemotron-120b-planner \
  EXECUTOR_MODEL=gateway/qwen9b-executor \
  MAX_STEPS=6 \
  SCORE_MODE=interactive
```

### Cerrar manualmente (si usaste SCORE_MODE=manual)

```bash
make bench-close RUN=tetris-v1-A-003 SCORE=7 INTERVENTIONS=0
make bench-close RUN=tetris-v1-F-001 SCORE=8 INTERVENTIONS=1 NOTES="planner dominó bien"
```

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
# 1. Ver estado de todas las corridas
make bench-results

# 2. Ejecutar un run end-to-end con score interactivo
make bench-run \
  CASE=tetris-v1 \
  RUN=tetris-v1-B-001 \
  PROMPT=conductor \
  PLANNER_MODEL=gateway/gpt-5.5 \
  EXECUTOR_MODEL=gateway/lmstudio-qwen3.6-35b-build \
  MAX_STEPS=6 \
  SCORE_MODE=interactive

# 3. Ver estado actualizado
make bench-results
```

El runner encadena automáticamente hasta MAX_STEPS sesiones de opencode, genera los prompts de continuación para cada incremento, y al terminar te pide el score. No hace falta abrir OpenCode ni pegar el prompt a mano.

---

## Workflow por corrida

### Camino principal (headless automático)

1. `make bench-results` — identificar la próxima corrida en estado `pending`
2. `make bench-run CASE=... RUN=... PROMPT=... EXECUTOR_MODEL=... MAX_STEPS=6 SCORE_MODE=interactive`
   — prepara, ejecuta hasta 6 incrementos encadenados, pide score al terminar
3. El runner actualiza `runs.yaml` y `results.csv` automáticamente
4. Cuando las 3 reps de una celda están completas, actualizar `reports/2026-06-tetris-v1.md`

### Camino manual (si preferís controlar la ejecución)

1. `make bench-results` — identificar la próxima corrida
2. `make bench-run ... SCORE_MODE=manual` — prepara workspace, prompt, metadata; copia prompt al clipboard
3. `make opencode` — abrí el TUI con el entorno configurado
4. Verificar modelo activo, pegar el prompt con `Cmd+V`
5. Dejar correr el modelo, observar el resultado en el workspace
6. `python workspace/<run_id>/main.py` — validar contra `scoring.md`
7. `make bench-close RUN=<run_id> SCORE=<n> INTERVENTIONS=<n>` — graba artefactos, actualiza `runs.yaml` y `results.csv`, hace commit

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
