# Runbook: primera corrida (Cell A — opencode local Qwen 35B)

Esto cubre exactamente una corrida completa de `tetris-v1-A-001`.
Seguí los pasos en orden; cada uno tiene un comando concreto y una señal de éxito.

---

## Prerequisitos (hacer una sola vez)

- [ ] Gateway activo en `http://localhost:4000`
- [ ] Langfuse activo (OTEL activo — ya funciona)
- [ ] LM Studio o Ollama sirviendo `qwen3.6-35b` con **contexto ≥ 32k**
- [ ] Virtual key `bench-opencode-local` creada en el gateway
  → ver `docs/GATEWAY_CHANGES.md` secciones 1–3 y el prompt en `harnesses/opencode-local.md`
- [ ] `make opencode-doctor` verde en este repo (confirma que el gateway y el modelo se alcanzan)

---

## Paso 1 — Congelar el `base_commit`

Esto fija el punto de partida del sujeto. Solo se hace una vez por batch de runs.

```bash
git -C /ruta/a/ml-python-base rev-parse HEAD
```

Copiar el SHA resultante y editarlo en `benchmarks/tetris/runs.yaml`:

```yaml
base_commit: <SHA>    # reemplazá TBD con el sha real
```

---

## Paso 2 — Registrar la corrida

```bash
uv run python scripts/create_run.py --benchmark tetris-v1 --run tetris-v1-A-001
```

**Señal de éxito:** imprime el `session_id`, el branch, y el snippet `.env`.
Crea `data/runs/tetris-v1-A-001/report.md` y marca `status: running` en `runs.yaml`.

Guardá el output — vas a necesitar el `session_id` y el snippet.

---

## Paso 3 — Aislar el sujeto en un branch

```bash
# Desde el directorio del sujeto (ml-python-base / ai-agentic-harness-lab):
git worktree add ../bench-tetris-v1-A-001 -b bench/tetris-v1-A-001 <base_commit>
cd ../bench-tetris-v1-A-001
```

El código del sujeto vive en esa carpeta temporal. No commitear nada de esto al lab.

---

## Paso 4 — Configurar el `.env` del worktree

```bash
cp .env.example .env
```

Editá `.env` con el snippet que imprimió `create_run.py`:

```bash
OPENCODE_MODEL=gateway/lmstudio-qwen3.6-35b-build
OPENCODE_MODEL_PLAN=gateway/lmstudio-qwen3.6-35b-build   # mismo modelo (local-only)
GATEWAY_BASE_URL=http://localhost:4000/v1
GATEWAY_TOKEN=<tu virtual key bench-opencode-local>
```

Verificar que llega:

```bash
make opencode-doctor
```

**Señal de éxito:** todos los checks verdes (gateway reachable, modelo responde).

---

## Paso 5 — Correr la corrida

```bash
make opencode
```

OpenCode abre el TUI. Pegá el prompt de `benchmarks/tetris/prompt_incremental.md`.

Reglas mientras corrés:
- **Un incremento por turno.** Después de cada respuesta, validá que el código corre antes de continuar.
- No modifiques el prompt entre los 3 runs de la celda.
- Anotá cada vez que intervenís manualmente (desbloqueás, corregís, reintentás).
- Si el modelo loopeó o rompió scope, eso es dato — no lo "arreglés" fuera del benchmark.

---

## Paso 6 — Capturar los artefactos

Cuando la corrida termina (o se estanca):

```bash
# Desde el worktree del sujeto:
git log --oneline HEAD ^<base_commit>   # commits del run
git diff <base_commit> HEAD > /ruta/al/lab/data/runs/tetris-v1-A-001/diff.patch
git rev-parse HEAD                      # result_commit
```

Guardá también:
- Screenshot del juego corriendo (si llegó a correr).
- El log del terminal de OpenCode si lo capturaste.

---

## Paso 7 — Scoring manual

```bash
uv run python scripts/score_tetris.py --run tetris-v1-A-001
```

(Stub — todavía imprime TODO. Mientras tanto: completá `data/runs/tetris-v1-A-001/report.md`
a mano con los valores de scoring de `benchmarks/tetris/scoring.md`.)

Scores a registrar en Langfuse contra `session_id=bench-tetris-v1-A-001`:
- `tetris_functional_level`: 0–10
- `human_interventions`: cuántas veces interveniste
- `runnable`: 0 o 1
- `scope_adherence`: 0–5

---

## Paso 8 — Cerrar la corrida

En `benchmarks/tetris/runs.yaml`, completar la entrada `tetris-v1-A-001`:

```yaml
status: completed
result_level: <score 0–10>
human_interventions: <n>
notes: "resumen breve del resultado"
```

Limpiar el worktree:

```bash
git worktree remove ../bench-tetris-v1-A-001
```

---

## Flujo completo resumido

```
1. base_commit en runs.yaml (una vez)
2. uv run python scripts/create_run.py --benchmark tetris-v1 --run tetris-v1-A-001
3. git worktree add  →  cd worktree  →  editar .env  →  make opencode-doctor
4. make opencode  →  pegar prompt_incremental.md  →  un incremento por turno
5. capturar diff + result_commit + screenshot
6. completar report.md + subir scores a Langfuse
7. cerrar en runs.yaml + git worktree remove
```

Para la siguiente repetición (A-002): repetir desde el paso 2.
No toques el prompt entre repeticiones.
