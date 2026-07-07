# tetris-v1

El modelo construye un Tetris en Python desde cero. Un solo comando lo ejecuta todo end-to-end y al final pedís el score.

```bash
make bench-results       # ver qué corridas están pendientes
```

---

## Flujo desde la UI web (paridad con CLI sin bloqueo)

- La UI crea corridas con `score_mode=manual` y no soporta `score_mode=interactive`.
- Si se intenta crear una corrida por API con `score_mode=interactive`, el backend la rechaza.
- Al terminar el worker, la corrida queda en `awaiting_manual_score`; el score se carga desde el panel de detalle (`POST /api/runs/{run_id}/score`).
- Si el servicio `worker` no está corriendo, las corridas quedan en `pending` y no aparecen logs de ejecución.
- La UI ahora carga `run_id` válidos desde `benchmarks/tetris/runs.yaml` para evitar errores por IDs inventados (por ejemplo, usar `tetris-v1-K-001`).
- El runtime Docker no depende de la `.venv` del host para ejecutar corridas desde la UI.
- Para play-test de Tetris antes de puntuar, corré desde el host sobre el workspace compartido:

```bash
python workspace/<run_id>/main.py
```

El contenedor del worker no tiene display, por eso pygame no se puede validar dentro de Docker.

Para levantar UI + API + worker juntos:

```bash
make app-up
# equivalente directo:
# docker compose --profile worker up --build api worker frontend
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

## Auditar el comportamiento de una corrida

Un LLM auditor lee los artefactos que la corrida ya dejó en `data/runs/<id>/`
(`opencode.log` / `steps/step-*.log`, `diff.patch`, `usage.json`, `metadata.json`,
el score, y el corpus de reglas que el agente vio en `OPENCODE.md`) y escribe un
**diagnóstico de comportamiento descriptivo** en `data/runs/<id>/audit.md`.

Cubre cuatro dimensiones: uso de skills/reglas, eficiencia/loops, adherencia al SDLC,
y puntos de falla. Es descriptivo (no propone ediciones al harness).

**Prerequisito:** la corrida tiene que tener artefactos en `data/runs/<id>/` (ejecutala
primero con `make bench-run ...`). Necesita `GATEWAY_BASE_URL` y `GATEWAY_TOKEN` en `.env`.

```bash
# Auditar una corrida con el auditor por defecto (gateway/gpt-5.5)
make bench-audit RUN=tetris-v1-K-001

# Elegir el modelo auditor
make bench-audit RUN=tetris-v1-K-001 AUDITOR_MODEL=gateway/claude-sonnet-4.6

# Dry run: ver qué artefactos detecta y el request armado, sin llamar al gateway
make bench-audit RUN=tetris-v1-K-001 DRY_RUN=1
```

El modelo auditor también se puede fijar con `AUDITOR_MODEL` en `.env`.

---

## Correr con Antigravity (human-driven) — Celda N

Ciclo aparte para correr la tarea con **Google Antigravity** (IDE o panel) y mejorar las reglas
del caso Google (`.agents/rules/GEMINI.md` + skills). Antigravity es human-driven: no hay CLI
headless, la ejecución la maneja una persona. **No toca el flujo opencode.** Detalle completo en
[`harnesses/antigravity.md`](../../harnesses/antigravity.md).

La celda N usa por defecto el prompt **`sdlc_full`**: un solo prompt que instruye construir los
**8 milestones en una sesión autónoma** (Antigravity es agéntico y lo hace de corrido). Esto es el
"caso completo". Si querés solo Milestone 1 (como un step suelto del flujo opencode), pasá
`PROMPT=sdlc`.

```bash
# 1. Preparar: registra, workspace, prompt full (8 milestones), plantillas
make bench-antigravity RUN=tetris-v1-N-001
#    Solo Milestone 1:  make bench-antigravity RUN=tetris-v1-N-001 PROMPT=sdlc

# 2. (a mano) Abrir Antigravity, pegar el prompt, trabajar en workspace/<id>/,
#    pegar el transcript en data/runs/<id>/transcript.md y completar notes.md

# 3. Cerrar (genérico, igual que opencode)
make bench-close RUN=tetris-v1-N-001 SCORE=<0-10> INTERVENTIONS=<N>

# 4. Auditar las reglas .agents (prescriptivo)
make bench-agents-audit RUN=tetris-v1-N-001
```

### Auditar las reglas .agents (prescriptivo)

A diferencia de `make bench-audit` (descriptivo, sobre opencode), `make bench-agents-audit` es
**prescriptivo**: un LLM lee la evidencia de la corrida (`diff.patch`, `transcript.md`,
`notes.md`, score) contra las reglas actuales (`.agents/rules/GEMINI.md` + skills) y propone
ediciones concretas a `GEMINI.md` y a las skills, citando evidencia. Escribe
`data/runs/<id>/agents_audit.md`. Degrada elegante: usa lo que esté presente.

```bash
make bench-agents-audit RUN=tetris-v1-N-001 DRY_RUN=1   # ver qué detecta, sin gastar tokens
make bench-agents-audit RUN=tetris-v1-N-001 AUDITOR_MODEL=claude-sonnet-4.6
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
