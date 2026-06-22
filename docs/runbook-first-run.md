# Runbook: primera corrida (Cell A — opencode local Qwen 35B)

Esto cubre exactamente una corrida completa de `tetris-v1-A-001`.
Seguí los pasos en orden; cada uno tiene un comando concreto y una señal de éxito.

---

## Cómo funciona la atribución en Langfuse

OpenCode no sabe nada de session_ids ni tags — manda llamadas OpenAI-compatibles al gateway
sin metadata. La solución: `create_run.py` crea una **virtual key específica del run** en
LiteLLM con `session_id` y `tags` bakeados adentro. Cuando OpenCode usa esa key como
`GATEWAY_TOKEN`, LiteLLM propaga esos valores a Langfuse en cada traza — automáticamente.

```
OpenCode → LiteLLM (key bench-tetris-v1-A-001)
                 ↳ agrega session_id + tags a cada llamada
                            → Langfuse  (todas las trazas agrupadas bajo esa session)
```

---

## Prerequisitos (hacer una sola vez)

- [ ] Gateway activo en `http://localhost:4000`
- [ ] Langfuse OTEL activo (ya funciona)
- [ ] LM Studio o Ollama sirviendo `qwen3.6-35b` con **contexto ≥ 32k**
- [ ] `LITELLM_MASTER_KEY` en el `.env` de este repo (mismo valor que en ai-gateway)
- [ ] `make opencode-doctor` verde en este repo

---

## Paso 1 — Congelar el `base_commit`

Solo una vez por batch de runs. Fija el punto de partida del sujeto.

```bash
git -C /ruta/a/este/repo rev-parse HEAD
```

Pegar el SHA en `benchmarks/tetris/runs.yaml`:

```yaml
base_commit: abc1234   # reemplazá TBD
```

---

## Paso 2 — Registrar la corrida y crear la virtual key

```bash
uv run python scripts/create_run.py --benchmark tetris-v1 --run tetris-v1-A-001
```

**Señal de éxito:** imprime el `GATEWAY_TOKEN=sk-...` generado — ese es el key del run.

El script:
- Crea `data/runs/tetris-v1-A-001/report.md` pre-llenado
- Marca `status: running` en `runs.yaml`
- Llama a `POST /key/generate` en el gateway y retorna la key con la session ya bakeada

Si falla con "LITELLM_MASTER_KEY not set": agregar en `.env` (ver `.env.example`).

---

## Paso 3 — Aislar el sujeto en un branch

```bash
git worktree add ../bench-tetris-v1-A-001 -b bench/tetris-v1-A-001 <base_commit>
cd ../bench-tetris-v1-A-001
cp .env.example .env
```

Editar el `.env` del worktree con el snippet que imprimió `create_run.py`:

```bash
OPENCODE_MODEL=gateway/lmstudio-qwen3.6-35b-build
OPENCODE_MODEL_PLAN=gateway/lmstudio-qwen3.6-35b-build
GATEWAY_BASE_URL=http://localhost:4000/v1
GATEWAY_TOKEN=sk-<key-generada-por-create_run>    # ← la key del run, NO la master key
```

Verificar que llega:

```bash
make opencode-doctor
```

**Señal de éxito:** todos los checks verdes.

---

## Paso 4 — Correr la corrida

```bash
make opencode
```

Pegá el prompt de `benchmarks/tetris/prompt_incremental.md`.

Reglas:
- **Un incremento por turno.** Validá que corre antes de continuar.
- No modificar el prompt entre los 3 runs de la celda.
- Anotar cada intervención manual (desbloquear, corregir, reintentar) — es dato.

Verificar en Langfuse: todas las trazas de esta sesión deben aparecer agrupadas bajo
`session_id = bench-tetris-v1-A-001`.

---

## Paso 5 — Capturar los artefactos

```bash
# Desde el worktree del sujeto:
git log --oneline HEAD ^<base_commit>
git diff <base_commit> HEAD > /ruta/lab/data/runs/tetris-v1-A-001/diff.patch
git rev-parse HEAD   # result_commit — guardarlo
```

Guardar también screenshot del juego si llegó a correr.

---

## Paso 6 — Score y cerrar

Completar `data/runs/tetris-v1-A-001/report.md` con los valores de `benchmarks/tetris/scoring.md`.

Subir scores a Langfuse contra `session_id=bench-tetris-v1-A-001`:
- `tetris_functional_level` (0–10)
- `human_interventions` (entero)
- `runnable` (0/1)
- `scope_adherence` (0–5)

Actualizar `benchmarks/tetris/runs.yaml`:

```yaml
status: completed
result_level: <score>
human_interventions: <n>
notes: "resumen breve"
```

Limpiar el worktree:

```bash
git worktree remove ../bench-tetris-v1-A-001
```

---

## Flujo resumido

```
1. base_commit en runs.yaml (una vez)
2. uv run python scripts/create_run.py ...   → imprime GATEWAY_TOKEN del run
3. git worktree add  →  .env con esa key  →  make opencode-doctor
4. make opencode  →  pegar prompt_incremental.md  →  un incremento por turno
5. diff.patch + result_commit + screenshot → data/runs/tetris-v1-A-001/
6. report.md + scores en Langfuse  →  status: completed en runs.yaml
7. git worktree remove
```

Para A-002: repetir desde el paso 2 con `--run tetris-v1-A-002`.
