# tetris-v1

El modelo construye un Tetris en Python desde cero. Un solo comando lo ejecuta todo end-to-end y al final pedís el score.

```bash
make bench-results       # ver qué corridas están pendientes
```

---

## Corridas end-to-end por modelo

Cada comando prepara el workspace, ejecuta hasta 10 sesiones encadenadas de opencode, y al terminar te pide un score interactivo (0–10). **No hace falta abrir OpenCode ni pegar prompts a mano.**

---

### Qwen3.6 35B local — Cell K ✅ funciona ahora (modo SDLC)

**Prerequisito:** LM Studio corriendo con el modelo `qwen3.6-35b` cargado.

```bash
# K-001
make bench-run \
  CASE=tetris-v1 \
  RUN=tetris-v1-K-001 \
  PROMPT=sdlc \
  EXECUTOR_MODEL=gateway/lmstudio-qwen3.6-35b-build \
  MAX_STEPS=10 \
  SCORE_MODE=interactive

# K-002 / K-003: cambiar solo RUN=tetris-v1-K-002  (o K-003)
```

---

### Nemotron Super 120B — Cell J ✅ vía AI gateway (modo SDLC)

```bash
# J-002  (J-001 cerrada con error de config anterior)
make bench-run \
  CASE=tetris-v1 \
  RUN=tetris-v1-J-002 \
  PROMPT=sdlc \
  EXECUTOR_MODEL=gateway/nim-nemotron-super-120b \
  MAX_STEPS=10 \
  SCORE_MODE=interactive

# J-003: cambiar RUN=tetris-v1-J-003
```

---

### GPT-5.5 cloud — Cell E ⚠️ requiere GATEWAY_TOKEN

**Prerequisito:** `GATEWAY_TOKEN` en `.env` con acceso al gateway LiteLLM configurado.

```bash
# E-001  (modo incremental, baseline)
make bench-run \
  CASE=tetris-v1 \
  RUN=tetris-v1-E-001 \
  PROMPT=incremental \
  EXECUTOR_MODEL=gateway/gpt-5.5 \
  MAX_STEPS=6 \
  SCORE_MODE=interactive
```

---

### Nemotron planner + Qwen executor — Cell F ✅ vía AI gateway

**Prerequisito:** LM Studio corriendo con qwen3.6-35b.

```bash
# F-001
make bench-run \
  CASE=tetris-v1 \
  RUN=tetris-v1-F-001 \
  PROMPT=conductor \
  PLANNER_MODEL=gateway/nim-nemotron-super-120b \
  EXECUTOR_MODEL=gateway/lmstudio-qwen3.6-35b-build \
  MAX_STEPS=6 \
  SCORE_MODE=interactive
```

---

### GPT-4o-mini cloud — Cell L ⚠️ requiere GATEWAY_TOKEN

**Prerequisito:** `GATEWAY_TOKEN` en `.env` con acceso al gateway LiteLLM para OpenAI.

```bash
# L-001  (modo sdlc)
make bench-run \
  CASE=tetris-v1 \
  RUN=tetris-v1-L-001 \
  PROMPT=sdlc \
  EXECUTOR_MODEL=openai/gpt-4o-mini \
  MAX_STEPS=10 \
  SCORE_MODE=interactive

# L-002 / L-003: cambiar solo RUN=tetris-v1-L-002  (o L-003)
```

---

### Kimi-K2.6 cloud — Cell M ⚠️ requiere GATEWAY_TOKEN

**Prerequisito:** `GATEWAY_TOKEN` en `.env` con acceso al gateway LiteLLM para Moonshot AI.

```bash
# M-001  (modo sdlc)
make bench-run \
  CASE=tetris-v1 \
  RUN=tetris-v1-M-001 \
  PROMPT=sdlc \
  EXECUTOR_MODEL=openai/moonshotai/kimi-k2.6 \
  MAX_STEPS=10 \
  SCORE_MODE=interactive

# M-002 / M-003: cambiar solo RUN=tetris-v1-M-002  (o M-003)
```

---

## Resetear y empezar de cero

```bash
# Resetear una corrida a pending (mantiene artefactos anteriores)
make bench-reset RUN=tetris-v1-K-001

# Resetear + borrar artefactos (workspace, logs, report)
make bench-reset RUN=tetris-v1-K-001 CLEAN=1

# Resetear todas las corridas de un caso
make bench-reset CASE=tetris-v1

# Nuclear: resetear todo + borrar todos los artefactos
make bench-reset CASE=tetris-v1 CLEAN=1

# Sin confirmación interactiva
make bench-reset RUN=tetris-v1-K-001 FORCE=1
```

`bench-reset` pone `status: pending` en `runs.yaml`, elimina la fila de `results.csv`, y con `CLEAN=1` borra `data/runs/<id>/` y `workspace/<id>/`.

---

## Verificar configuración antes de correr (dry run)

```bash
make bench-run \
  CASE=tetris-v1 \
  RUN=tetris-v1-K-001 \
  PROMPT=incremental \
  EXECUTOR_MODEL=gateway/lmstudio-qwen3.6-35b-build \
  MAX_STEPS=10 \
  DRY_RUN=1
```

Con `DRY_RUN=1` el comando muestra todo lo que haría sin escribir ni ejecutar nada.

---

## Cómo scorear al terminar

Al final de la ejecución el runner muestra el rubric y pide:

```
Functional score (0-10): _
Human interventions: _
```

| Score | Estado del juego |
|-------|-----------------|
| 0 | Sin código útil |
| 1 | Archivos generados, no corre |
| 2 | Corre sin crash |
| 3 | Abre ventana |
| 4 | Muestra una pieza |
| 5 | Pieza cae |
| 6 | Movimiento lateral |
| 7 | Rotación |
| 8 | Colisiones + lock |
| 9 | Limpia líneas + score |
| 10 | Jugable completo |

Para testear: `python workspace/<run_id>/main.py`

---

## Ver resultados

```bash
make bench-results           # todas las corridas (pending + completed)
cat benchmarks/tetris/results.csv  # solo scored
```

---

## Archivos de referencia

| Archivo | Rol |
|---------|-----|
| `task.yaml` | Definición congelada: descripción, base commit, prompts |
| `runs.yaml` | Registro de todas las corridas (fuente de verdad) |
| `scoring.md` | Rubric detallado |
| `prompt_incremental.md` | 8 incrementos step-by-step (el más efectivo) |
| `prompt_conductor.md` | Conductor/planner para arquitecturas de dos modelos |
| `prompt_naive.md` | Baseline: entrega todo en una sola respuesta |
| `results.csv` | Comparación de corridas scored |
