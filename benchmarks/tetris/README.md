# tetris-v1

**¿Qué corro?** Un Tetris en Python. El modelo lo construye desde cero; tú lo corres, lo observás, y le ponés un score de 0 a 10.

**¿Cómo arranco una corrida?** `make bench-start RUN=tetris-v1-A-003`

**¿Cómo la cierro?** `make bench-close RUN=tetris-v1-A-003 SCORE=7 INTERVENTIONS=1`

**¿Dónde veo el resultado?** `make bench-results`

---

## Qué prompt pegar en OpenCode

Después de `make bench-start RUN=<run_id>`, el prompt final de la corrida queda en:

```
data/runs/<run_id>/prompt.md
```

`make bench-start` lo copia al clipboard automáticamente. En OpenCode, pegá con `Cmd+V`.

Si el clipboard no funcionó, copialo manualmente:

```bash
cat data/runs/tetris-v1-A-003/prompt.md | pbcopy
```

**No pegar directamente `prompt_incremental.md`, `prompt_naive.md` ni `prompt_conductor.md`.**
Esos son los templates fuente. `data/runs/<run_id>/prompt.md` es el prompt final de la corrida,
con el workspace y el run_id ya inyectados.

---

## Quickstart

```bash
# Ver el estado de todas las corridas (pending / running / completed + score)
make bench-results

# Arrancar la próxima corrida pendiente
make bench-start RUN=tetris-v1-A-003

# [Correr el benchmark manualmente. Scorear contra scoring.md.]

# Cerrar la corrida y registrar el score
make bench-close RUN=tetris-v1-A-003 SCORE=7 INTERVENTIONS=1

# Después de cerrar: agregar la fila scored a results.csv
# Luego traer tokens/costo de Langfuse:
#   uv run python scripts/export_langfuse_usage.py --session bench-tetris-v1-A-003
```

---

## Workflow por corrida

1. `make bench-results` — identificar la próxima corrida en estado `pending`
2. Verificar el `prompt_variant` de esa corrida en `runs.yaml`
3. `make bench-start RUN=<run_id>` — registra la corrida como `running`, abre el workspace
4. Ejecutar el benchmark manualmente (el modelo construye el Tetris)
5. Correr `python main.py` en el workspace y observar el resultado
6. Scorear contra `scoring.md` — anotar el nivel funcional (0–10)
7. `make bench-close RUN=<run_id> SCORE=<n> INTERVENTIONS=<n>` — guarda artefactos, actualiza `runs.yaml`, hace commit
8. Agregar una fila a `results.csv` con los datos scored (solo corridas completadas)
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
runs.yaml           ← registro de las 27 corridas planificadas (fuente de verdad)
     ↓
make bench-start    ← marca la corrida como running, abre workspace con el prompt
     ↓
[ejecución manual + score contra scoring.md]
     ↓
make bench-close    ← graba score en runs.yaml, commit de artefactos
     ↓
results.csv         ← agregar fila manualmente (solo corridas scored)
     ↓
reports/2026-06-tetris-v1.md  ← actualizar cuando una celda completa las 3 reps
```

`make bench-results` muestra una vista en vivo de `runs.yaml` — incluye corridas pending.
`results.csv` es el artefacto de comparación limpio para corridas scored únicamente.
Son dos cosas distintas.

---

## Notas operativas

**Prompt injection:** `make bench-start` inyecta `prompt_incremental.md` para todas las corridas tetris-v1. Para celdas conductor y Nemotron (B–I), verificar manualmente que el prompt correcto esté activo en el workspace antes de correr. Esto se automatizará en una iteración posterior.

**Tokens y costo:** los campos de tokens y costo en `results.csv` se completan después de cerrar la corrida, trayendo datos de Langfuse:
```bash
uv run python scripts/export_langfuse_usage.py --session bench-tetris-v1-A-003
```
Dejar esos campos vacíos hasta que se traigan. No estimar ni inferir costos.

**Artefactos por corrida:** los reportes individuales viven en `data/runs/<run_id>/report.md`.
El template canónico es `templates/run-report-template.md`.
