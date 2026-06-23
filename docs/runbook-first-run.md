# Receta: cómo correr un experimento

Dos comandos por run. El resto es automático.

---

## Setup (una sola vez)

```bash
cp .env.example .env
# Editar .env: GATEWAY_BASE_URL, GATEWAY_TOKEN (key inicial), LITELLM_MASTER_KEY
# LITELLM_MASTER_KEY es la master key de LiteLLM — se usa para crear keys por run
```

Verificar que el gateway responde:

```bash
make opencode-doctor
```

---

## Por cada corrida

### 1. Iniciar

```bash
make bench-start RUN=tetris-v1-A-002
```

Esto hace todo de una vez:
- Registra el run en `runs.yaml` (status → running)
- Crea `workspace/tetris-v1-A-002/` — ahí va a escribir OpenCode
- Genera `data/runs/tetris-v1-A-002/prompt.md` con el workspace path ya inyectado
- Crea una virtual key en LiteLLM con `session_id` baked in y parchea `.env`
- Escribe `BENCH_CONTEXT.md` con las reglas de workspace (OpenCode lo lee al arrancar)
- Copia el prompt al clipboard (Cmd+V en OpenCode)
- Lanza OpenCode

Si la creación automática de key falla (Cloudflare bloquea el script), pásala manualmente:

```bash
make bench-start RUN=tetris-v1-A-002 KEY=sk-tu-key-aqui
```

### 2. Correr el experimento en OpenCode

```
Cmd+V → Enter   (pega el prompt que ya está en el clipboard)
```

Validar cada increment antes de aprobar el siguiente:

```bash
python workspace/tetris-v1-A-002/main.py
```

OpenCode escribe en `workspace/tetris-v1-A-002/` — nunca en el root del repo.

### 3. Cerrar (desde otra terminal)

```bash
make bench-close RUN=tetris-v1-A-002 SCORE=5 INTERVENTIONS=0
```

Con notas opcionales:

```bash
make bench-close RUN=tetris-v1-A-002 SCORE=7 INTERVENTIONS=1 NOTES="Llegó a rotación, falló lock"
```

Esto:
- Copia `workspace/tetris-v1-A-002/` → `data/runs/tetris-v1-A-002/workspace/`
- Genera `data/runs/tetris-v1-A-002/diff.patch`
- Elimina `workspace/tetris-v1-A-002/`
- Actualiza `runs.yaml` con score e interventions
- Hace un commit con todo

### 4. Completar el reporte (opcional pero recomendado)

Abrir `data/runs/tetris-v1-A-002/report.md` y completar los campos de uso:

```
- Total requests: (ver LiteLLM UI → key tetris-v1-A-002)
- Total tokens:
- Total cost USD:
```

---

## Segunda corrida del mismo experimento

Mismo modelo, mismo prompt — no cambiar nada entre runs de la misma celda:

```bash
make bench-start RUN=tetris-v1-A-003
# ... correr ...
make bench-close RUN=tetris-v1-A-003 SCORE=6 INTERVENTIONS=0
```

---

## Cambiar de celda (modelo diferente)

Solo cambia el `RUN` — la key y el modelo están en `runs.yaml` y se aplican automáticamente:

```bash
make bench-start RUN=tetris-v1-B-001   # conductor GPT-5.5 + Qwen 35B
make bench-start RUN=tetris-v1-E-001   # cloud full GPT-5.5
```

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
