# Receta: cómo correr un experimento

Una corrida = un modelo + un prompt + un worktree aislado. Las corridas no se mezclan.

---

## Una vez por benchmark (setup inicial)

```bash
# 1. Congelar el punto de partida del sujeto
git rev-parse HEAD
# → copiar el SHA a benchmarks/tetris/runs.yaml → base_commit: <sha>

# 2. Verificar que el gateway responde
make opencode-doctor
```

---

## Por cada corrida (ej: tetris-v1-A-001)

### A) Registrar y crear el worktree

```bash
# Registra la corrida en runs.yaml, genera data/runs/<id>/report.md
uv run python scripts/create_run.py --benchmark tetris-v1 --run tetris-v1-A-001

# Crear worktree aislado (el código del benchmark vive acá, NO en main)
git worktree add ../bench-tetris-v1-A-001 -b bench/tetris-v1-A-001 <base_commit>
cd ../bench-tetris-v1-A-001
cp .env.example .env
```

### B) Configurar `.env` del worktree

```bash
# Editar el .env con los valores de esta corrida:
OPENCODE_MODEL=gateway/lmstudio-qwen3.6-35b-build
OPENCODE_MODEL_PLAN=gateway/lmstudio-qwen3.6-35b-build
GATEWAY_TOKEN=sk-...   # la key de esta corrida (creada en LiteLLM UI con key_alias=tetris-v1-A-001)
GATEWAY_BASE_URL=https://ai.msinnovatech.com.ar/v1
```

### C) Correr OpenCode con el prompt

```bash
make opencode
# → pegar el contenido de benchmarks/tetris/prompt_incremental.md
# → responder "yes, proceed" o "proceed" después de cada increment validado
# → correr python main.py para validar cada increment antes de continuar
```

### D) Capturar el resultado

```bash
# Desde el worktree, cuando termina:
git add -A && git commit -m "bench: tetris-v1-A-001 result"
RESULT_COMMIT=$(git rev-parse HEAD)

# Guardar diff en el lab
git diff <base_commit> HEAD > /ruta/al/lab/data/runs/tetris-v1-A-001/diff.patch

# Volver al lab y limpiar el worktree
cd /ruta/al/lab
git worktree remove ../bench-tetris-v1-A-001
```

### E) Scorear y cerrar

Abrir `data/runs/tetris-v1-A-001/report.md` y completar:

```markdown
- Result commit: <RESULT_COMMIT>
- Total requests: (ver LiteLLM Usage → key tetris-v1-A-001)
- Total tokens: 
- Total cost: 

## Functional score
- Level achieved: X/10   ← según benchmarks/tetris/scoring.md
- Window opens: sí/no
- Piece falls: sí/no
...

## Engineering score
- Human interventions: N
```

Actualizar `benchmarks/tetris/runs.yaml`:

```yaml
status: completed
result_level: X
human_interventions: N
notes: "breve descripción"
```

---

## Segunda corrida (tetris-v1-A-002)

Mismo modelo, mismo prompt — **no cambiar nada entre corridas de la misma celda**.

```bash
# Volver al lab (main branch)
cd /ruta/al/lab

uv run python scripts/create_run.py --benchmark tetris-v1 --run tetris-v1-A-002
# Nueva key en LiteLLM UI con key_alias=tetris-v1-A-002
git worktree add ../bench-tetris-v1-A-002 -b bench/tetris-v1-A-002 <mismo base_commit>
# ... repetir pasos B → E
```

---

## Cambiar modelo/metodología (celda B, C, D, E)

Distinto modelo = distinto run_id + distinta key + distinto OPENCODE_MODEL en el .env.
El prompt puede ser distinto según la celda (ver `benchmarks/tetris/`).
El `base_commit` es el mismo para todas las celdas del mismo benchmark.

---

## Cheatsheet de scoring (benchmarks/tetris/scoring.md)

| Nivel | Qué logró |
|-------|-----------|
| 0 | No produjo código útil |
| 1 | Genera archivos pero no corre |
| 2 | Corre sin crashear |
| 3 | Abre ventana ← **estás acá** |
| 4 | Muestra pieza estática |
| 5 | Pieza cae |
| 6 | Movimiento lateral |
| 7 | Rotación |
| 8 | Colisiones y lock |
| 9 | Limpia líneas + score |
| 10 | Jugable completo |
