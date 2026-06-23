# Runbook: experimentos de benchmark

Dos comandos por run. Un modelo a la vez.

---

## Setup (una sola vez)

```bash
cp .env.example .env
# Editar .env: GATEWAY_BASE_URL, LITELLM_MASTER_KEY
# LITELLM_MASTER_KEY = master key de LiteLLM, necesaria para crear keys automáticas por run
```

Verificar que el gateway responde:

```bash
make opencode-doctor
```

---

## El mapa de experimentos (celdas A–E)

Cada **celda** = una condición experimental fija (modelo + metodología + prompt).  
Cada **run** (001, 002, 003) = una repetición de esa condición.

| Celda | Nombre | Harness | Planner | Executor | Prompt |
|-------|--------|---------|---------|----------|--------|
| **A** | local puro — Qwen 35B | opencode | Qwen 35B | Qwen 35B | incremental |
| **B** | conductor cloud+local grande | opencode-conductor | GPT-5.5 | Qwen 35B | conductor |
| **C** | conductor cloud+local chico | opencode-conductor | GPT-5.5 | Qwen 9B | conductor |
| **D** | conductor local+local | opencode-conductor | Qwen 35B | Qwen 9B | conductor small |
| **E** | baseline cloud puro | opencode | GPT-5.5 | GPT-5.5 | incremental |

**Regla clave:** no cambiar nada entre runs de la misma celda (mismo modelo, misma key, mismo prompt). El objetivo es medir varianza, no cambiar condiciones.

Ver tabla en vivo (qué está completado, scores, promedios por celda):

```bash
make bench-results
```

---

## Flujo por run

### 1. Elegir el run_id

Seguir el orden natural dentro de cada celda: A-001, A-002, A-003, luego B-001, etc.  
Para ver cuáles están pendientes:

```bash
make bench-results
```

### 2. Iniciar

```bash
make bench-start RUN=tetris-v1-A-002
```

Si la creación automática de key falla (Cloudflare), pasarla manualmente:

```bash
make bench-start RUN=tetris-v1-A-002 KEY=sk-tu-key
```

Esto hace todo de una vez:
- Registra el run (`runs.yaml` → running)
- Crea `workspace/tetris-v1-A-002/` — aquí escribe OpenCode
- Genera `data/runs/tetris-v1-A-002/prompt.md` con el workspace path inyectado
- Escribe `BENCH_CONTEXT.md` (OpenCode lo lee y sabe dónde escribir)
- Copia el prompt al clipboard
- Lanza OpenCode

### 3. Correr en OpenCode

```
Cmd+V → Enter    ← pega el prompt que ya está en el clipboard
```

Validar cada increment antes de aprobar el siguiente:

```bash
python workspace/tetris-v1-A-002/main.py
```

OpenCode escribe en `workspace/tetris-v1-A-002/` — nunca en el root.

### 4. Cerrar (desde otra terminal, o después de cerrar OpenCode)

```bash
make bench-close RUN=tetris-v1-A-002 SCORE=5 INTERVENTIONS=0
```

Con notas:

```bash
make bench-close RUN=tetris-v1-A-002 SCORE=7 INTERVENTIONS=1 NOTES="Llegó a rotación, falló lock"
```

Esto:
- Archiva `workspace/tetris-v1-A-002/` → `data/runs/tetris-v1-A-002/workspace/`
- Genera `diff.patch`
- Actualiza `runs.yaml` con score e interventions
- Hace commit

### 5. Ver resultados actualizados

```bash
make bench-results
```

---

## Orden sugerido de ejecución

Completar las 3 repeticiones de cada celda antes de pasar a la siguiente (para tener
varianza medible). Si tenés dos máquinas, podés correr celdas en paralelo:

```
A-001 → A-002 → A-003 → (ver avg A) → B-001 → B-002 → B-003 → ... → make bench-results
```

---

## Agregar un modelo nuevo al comparativo

1. Añadir una nueva sección de celda en `benchmarks/tetris/runs.yaml`:

```yaml
  # --- Cell F: nuevo modelo — descripción ---------------------------------
  - run_id: tetris-v1-F-001
    methodology: opencode-local
    harness: opencode
    planner_model: <alias-del-modelo>
    executor_model: <alias-del-modelo>
    gateway_models: [gateway/<alias>]
    executor_context: 32768
    temperature: 0.2
    prompt_variant: tetris_incremental_v1
    langfuse_session_id: bench-tetris-v1-F-001
    virtual_key: bench-opencode-<nombre>
    branch: bench/tetris-v1-F-001
    status: pending
    result_level:
    human_interventions:
    notes: ""
  - run_id: tetris-v1-F-002
    ... (copiar y ajustar F-002, F-003)
```

2. Agregar el modelo al gateway si no está: editar `opencode.json` en la sección
   `provider.gateway.models` para que aparezca en el selector de OpenCode.

3. Correr normalmente:

```bash
make bench-start RUN=tetris-v1-F-001
make bench-close RUN=tetris-v1-F-001 SCORE=X INTERVENTIONS=N
make bench-results   # ya aparece en la tabla
```

---

## Keys de LiteLLM (una por run)

Crear manualmente en la UI del gateway (`GATEWAY_BASE_URL/ui → Keys → New Key`):
- **Key alias**: el run_id exacto (ej. `tetris-v1-A-002`)
- **Metadata**: `session_id = bench-tetris-v1-A-002` (para agrupar en Langfuse)

Pasar al comando:

```bash
make bench-start RUN=tetris-v1-A-002 KEY=sk-tu-key-de-litellm
```

Una key distinta por run es lo correcto: LiteLLM muestra el uso por key y Langfuse
agrupa las trazas por `session_id` baked en la key.

---

## Cheatsheet de scoring

| Score | Qué logró |
|-------|-----------|
| 0 | No produjo código útil |
| 1 | Genera archivos pero no corre |
| 2 | Corre sin crashear |
| 3 | Abre ventana pygame |
| 4 | Muestra pieza estática |
| 5 | Pieza cae |
| 6 | Movimiento lateral |
| 7 | Rotación |
| 8 | Colisiones y lock |
| 9 | Limpia líneas + score |
| 10 | Jugable completo (game over, reinicio) |

---

## Dónde ver resultados

| Fuente | Qué muestra |
|--------|-------------|
| `make bench-results` | tabla comparativa en vivo: todos los runs, scores, promedios por celda |
| `data/runs/<run-id>/report.md` | detalle de un run individual |
| `data/runs/<run-id>/diff.patch` | qué escribió el modelo |
| LiteLLM UI → Keys | tokens y costo por run (buscar por key alias = run_id) |
| Langfuse → Sessions | trazas agrupadas por session_id (si la key tiene session_id baked in) |
| `reports/tetris-v1-results.md` | reporte final narrativo (rellenar cuando todas las celdas terminen) |
