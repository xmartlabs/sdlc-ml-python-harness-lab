# Validate Your Tool: Unified Skills Multi-Selection & AI-Assisted Case Generator Design

## Summary

Enhance the primary evaluation wizard (**Home $\rightarrow$ Validate your tool** / `/evaluate/subject`) to streamline both the tool-under-test declaration and the case definition steps:
1. **Bring your tool (Step 1):** Allow browsing and multi-selecting from **all available skills**—both custom *Staged skills* and the harness's *Governed skills* (`ml-python-base`)—with instant tab filtering, search, and an optional execution isolation toggle (`--disable-skill` for unselected governed skills).
2. **Declare what success is (Step 2):** Provide an optional, inline **AI Case Assistant** that accepts a natural language description and a chosen LLM model (pre-selected with the system default), passes the selected skills' context to the LLM, and produces structured outputs filling the `subject`, `instruction` (prompt), `validation_commands`, and `max_steps` fields for user review and fine-tuning.

## Problem

* **Staging-only restriction in Step 1:** Currently, `/evaluate/subject` only lists skills from staging (`api.listStagedSkills()`). Users wishing to validate, benchmark, or ablate skills already integrated into the governed harness (`ml-python-base`) have to bypass the picker via the *"My skill is already part of the governed harness"* checkbox without being able to select them or isolate them from other skills.
* **Manual friction in Step 2:** Creating a validation case currently requires authoring every field from scratch: the exact subject string, the full prompt instruction, the bash verification command(s), and the max step limit. Without assistance, crafting realistic prompts and non-trivial exit-code-verified bash commands is slow and prone to formatting syntax errors.

## Governing Decisions

1. **Overlay & Backward Compatibility:** Staged skills continue to be injected via `skills_extra` (`--extra-skill`). Governed skills remain part of the base harness. When the user enables the optional **Execution Isolation** toggle, unselected governed skills are passed to `skills_disabled` (`--disable-skill`), ensuring pure ablative isolation.
2. **AI Assistance as a Draft Accelerator, Not a Gate:** The AI Case Assistant is completely optional and strictly drafts content directly into standard form inputs. The user retains full review, edit, and validation control prior to committing the case (`POST /api/cases`).
3. **Structured JSON Output & Graceful Degradation:** The AI drafting endpoint uses structured JSON (`response_format={"type": "json_object"}`) via the Gateway or configured model provider. If the Gateway is unreachable, offline, or returns invalid syntax, a friendly inline error is displayed without blocking manual entry or losing existing input.
4. **Rich Prompt Context:** When generating a draft, the backend enriches the user's natural language request with the active context (name, description, and `SKILL.md` content) of the skills selected in Step 1, ensuring accurate instructions and verification commands.

## Architecture & Detailed Design

### 1. Step 1: Unified Skills Selection (`SubjectValidation.tsx`)

#### Data Retrieval
On mount, `SubjectValidation.tsx` fetches both:
* `api.listStagedSkills()` $\rightarrow$ active custom staged skills.
* `api.listHarnessSkills()` $\rightarrow$ canonical governed skills from the harness.

#### UI Structure
* **Quick Filter Tabs:** `Todas (All)` | `Staged` | `Harness` with live counts.
* **Search Input:** Real-time text filtering against skill name and description.
* **Unified Pick List:** Interactive cards/chips showing:
  * Checkmark icon when selected.
  * Skill name and description.
  * Source badge: `[staged]` or `[harness]`.
* **Execution Isolation Toggle:**
  ```tsx
  <label className="checkbox">
    <input
      type="checkbox"
      checked={isolateGoverned}
      onChange={(e) => setIsolateGoverned(e.target.checked)}
    />
    <span>Aislar ejecución: deshabilitar en el harness las demás skills no seleccionadas</span>
  </label>
  ```
* **Readiness & Execution Parameters:**
  * Step 1 is cleared if `selectedSkills.length > 0` OR `alreadyGoverned` is checked.
  * When launching the experiment / arm runs:
    * `skills_extra = selectedSkills.filter(name => isStaged(name))`
    * `skills_disabled = isolateGoverned ? allGovernedSkillNames.filter(name => !selectedSkills.includes(name)) : []`

---

### 2. Step 2: AI-Assisted Case Drafting

#### Backend Endpoint: `POST /api/cases/generate-draft`
* **Route:** Defined in `src/harness_lab_app/api.py`.
* **Payload (`CaseDraftRequest`):**
  ```python
  class CaseDraftRequest(BaseModel):
      description: str                  # Free-form description of test scenario
      model: str | None = None          # Target model identifier (fallback to gateway default)
      selected_skills: list[str] = []   # Skills chosen in Step 1
  ```
* **Response (`CaseDraftResponse`):**
  ```python
  class CaseDraftResponse(BaseModel):
      subject: str                      # Concise name of tested tool/capability
      instruction: str                  # Agent prompt/task instruction
      validation_commands: list[str]    # Bash commands verifying success via exit code
      max_steps: int                    # Recommended execution step budget
  ```

#### Backend Service Logic (`case_draft_service.py`):
1. Resolve Gateway endpoint (`_gateway()`) and target model.
2. If `selected_skills` are provided:
   * Load `SKILL.md` content from staging directories (`data/skills_staging/<name>/SKILL.md`) or governed paths (`.agents/skills/<name>/SKILL.md` / `.github/skills/`).
   * Assemble skill excerpts into system context.
3. Call Gateway chat completion with temperature `0.2` and system prompt instructing the model to generate a structured JSON object.
4. Clean and parse JSON response into `CaseDraftResponse`. Ensure `validation_commands` contains non-empty commands and `max_steps` defaults to 10 if missing or out of standard range [5, 25].

#### Frontend Integration (`frontend/src/pages/SubjectValidation.tsx`):
* **Inline Smart Assistant Card:**
  * Displays active skill context tags (e.g. `Contexto: notion-export, verify_changes`).
  * Textarea for natural language prompt description.
  * Model selector populated with `modelOptions`, defaulting to `gatewayDefault`.
  * Button `✨ Generar caso con IA` with active spinner state (`busy === "case-draft"`).
* **Auto-Fill Behavior:**
  * Updates `subject`, `prompt`, `commandText` (joined by `\n`), and `maxSteps`.
  * Runs existing `looksExecutable` checks immediately so the user sees executable vs. rejected command validation.
  * Notifies user with an informative callout banner indicating successful draft generation.

## Error Handling & Resilience

* **Gateway Offline / Auth Failures:** Returns HTTP 400 with actionable error detail: `"No se pudo conectar con el modelo. Puedes completar los campos manualmente o verificar tu conexión en Settings."`
* **Malformed JSON from LLM:** Sanitizes potential markdown wrapping (````json ... ````) before calling `json.loads`. Falls back gracefully with HTTP 422 if unparseable.
* **Skill Collision / Missing Files:** Missing `SKILL.md` files during context building are ignored gracefully without breaking the draft flow.

## Verification & Testing Plan (TDD)

1. **Backend Unit & Integration Tests (`tests/harness_lab_app/test_case_draft.py`):**
   * Test successful case generation with a mocked Gateway response.
   * Test skill context extraction for both staged and governed skills.
   * Test error handling when Gateway returns 500 or malformed non-JSON text.
   * Test skill isolation logic (`skills_disabled` computation when `isolateGoverned` is enabled).
2. **Frontend Quality & Type Safety:**
   * Run TypeScript check (`npm run check`) in `frontend/`.
   * Run frontend production build check (`npm run build`).
3. **Full Repository Quality Gate:**
   * Run `make check` to verify linting, formatting, domain boundaries, and test suite across the repository.
