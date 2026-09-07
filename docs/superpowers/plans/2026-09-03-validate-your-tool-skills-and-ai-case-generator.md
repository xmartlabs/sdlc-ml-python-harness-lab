# Validate Your Tool: Unified Skills Multi-Selection & AI-Assisted Case Generator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enhance the evaluation wizard (`/evaluate/subject`) to allow multi-selecting any available skill (Staged or Governed) with optional isolation, and provide an inline AI Assistant that drafts validation cases using structured JSON output from the Gateway/LLM.

**Architecture:** A new backend service (`case_draft_service.py`) enriches the user's scenario description with `SKILL.md` context from selected skills and calls the Gateway (`/chat/completions`) requesting a structured JSON schema. The FastAPI endpoint `POST /api/cases/generate-draft` exposes this to the frontend. In `SubjectValidation.tsx`, Step 1 renders unified searchable tabs for Staged and Governed skills with an isolation toggle, and Step 2 renders an inline AI drafting card that auto-populates the case fields.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, TypeScript, React, Vite.

---

### Task 1: Backend Schemas and Case Draft Service

**Files:**
- Create: `src/harness_lab_app/case_draft_service.py`
- Modify: `src/harness_lab_app/schemas.py:440-470`
- Test: `tests/harness_lab_app/test_case_draft.py`

- [ ] **Step 1: Write the failing unit test for `case_draft_service`**

```python
# tests/harness_lab_app/test_case_draft.py
from unittest.mock import MagicMock, patch
import pytest
from harness_lab_app.case_draft_service import generate_case_draft
from harness_lab_app.schemas import CaseDraftRequest, CaseDraftResponse

def test_generate_case_draft_success():
    fake_json_response = (
        '{"subject": "notion-export skill + export validation", '
        '"instruction": "Export page 123 and save to notes.txt", '
        '"validation_commands": ["test -s {workspace}/notes.txt"], '
        '"max_steps": 10}'
    )
    with patch("harness_lab_app.case_draft_service._call_gateway_chat", return_value=fake_json_response):
        req = CaseDraftRequest(
            description="Test notion export page 123",
            model="gpt-5.4-nano",
            selected_skills=[]
        )
        res = generate_case_draft(req)
        assert isinstance(res, CaseDraftResponse)
        assert res.subject == "notion-export skill + export validation"
        assert res.instruction == "Export page 123 and save to notes.txt"
        assert res.validation_commands == ["test -s {workspace}/notes.txt"]
        assert res.max_steps == 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/harness_lab_app/test_case_draft.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness_lab_app.case_draft_service'`

- [ ] **Step 3: Implement Schemas and Service**

In `src/harness_lab_app/schemas.py`:
```python
class CaseDraftRequest(BaseModel):
    description: str
    model: str | None = None
    selected_skills: list[str] = Field(default_factory=list)

class CaseDraftResponse(BaseModel):
    subject: str
    instruction: str
    validation_commands: list[str] = Field(default_factory=list)
    max_steps: int = 10
```

In `src/harness_lab_app/case_draft_service.py`:
Implement `generate_case_draft(req: CaseDraftRequest) -> CaseDraftResponse` resolving Gateway credentials, loading skill descriptions/markdown if available, calling the gateway with `response_format={"type": "json_object"}`, and parsing the structured response.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/harness_lab_app/test_case_draft.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/harness_lab_app/schemas.py src/harness_lab_app/case_draft_service.py tests/harness_lab_app/test_case_draft.py
git commit -m "feat(api): add case draft generation service and schemas"
```

---

### Task 2: Backend API Route `POST /api/cases/generate-draft`

**Files:**
- Modify: `src/harness_lab_app/api.py:750-780`
- Test: `tests/harness_lab_app/test_case_draft.py`

- [ ] **Step 1: Write the failing API integration test**

```python
# In tests/harness_lab_app/test_case_draft.py
from fastapi.testclient import TestClient

def test_api_generate_case_draft_route(client: TestClient):
    with patch("harness_lab_app.case_draft_service.generate_case_draft") as mock_gen:
        mock_gen.return_value = CaseDraftResponse(
            subject="Test subject",
            instruction="Do task",
            validation_commands=["test -f {workspace}/out.txt"],
            max_steps=5
        )
        resp = client.post(
            "/api/cases/generate-draft",
            json={"description": "Test case description", "model": "gpt-5.4-nano"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["subject"] == "Test subject"
        assert data["max_steps"] == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/harness_lab_app/test_case_draft.py::test_api_generate_case_draft_route -v`
Expected: FAIL with status 404

- [ ] **Step 3: Register route in `src/harness_lab_app/api.py`**

```python
@app.post("/api/cases/generate-draft", response_model=CaseDraftResponse)
def generate_case_draft_endpoint(
    req: CaseDraftRequest,
    _user: User = Depends(require_user)
) -> CaseDraftResponse:
    try:
        return generate_case_draft(req)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/harness_lab_app/test_case_draft.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/harness_lab_app/api.py tests/harness_lab_app/test_case_draft.py
git commit -m "feat(api): expose POST /api/cases/generate-draft endpoint"
```

---

### Task 3: Frontend API Types and Client

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`

- [ ] **Step 1: Add types in `frontend/src/types.ts`**

```typescript
export interface CaseDraftRequest {
  description: string;
  model?: string;
  selected_skills: string[];
}

export interface CaseDraftResponse {
  subject: string;
  instruction: string;
  validation_commands: string[];
  max_steps: number;
}
```

- [ ] **Step 2: Add API call in `frontend/src/api.ts`**

```typescript
generateCaseDraft: (payload: CaseDraftRequest) =>
  request<CaseDraftResponse>("/api/cases/generate-draft", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }),
```

- [ ] **Step 3: Run TypeScript compiler check**

Run: `npm --prefix frontend run check`
Expected: PASS with no errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types.ts frontend/src/api.ts
git commit -m "feat(frontend): add CaseDraft types and api client call"
```

---

### Task 4: Unified Skills Multi-Selection & Isolation in Step 1 (`SubjectValidation.tsx`)

**Files:**
- Modify: `frontend/src/pages/SubjectValidation.tsx`

- [ ] **Step 1: Update state and data fetching in `SubjectValidation.tsx`**
  - Fetch both `api.listStagedSkills()` and `api.listHarnessSkills()`.
  - Add state for `governedSkills: HarnessSkill[]`.
  - Add state for `filterTab: "all" | "staged" | "harness"`.
  - Add state for `searchSkill: string`.
  - Add state for `isolateGoverned: boolean`.
  - Store selected skill names in `selectedSkills: string[]`.

- [ ] **Step 2: Render Tabs, Search, and Multi-Select Cards**
  - Filter pills: `Todas (${total})`, `Staged (${stagedCount})`, `Harness (${harnessCount})`.
  * Search input to filter by skill name or description.
  * Pick list with check icon and badge `[staged]` or `[harness]`.
  * Checkbox: *"Aislar ejecución: deshabilitar en el harness las demás skills no seleccionadas"*.

- [ ] **Step 3: Update Readiness Gate & Experiment Launch**
  - Ready if `selectedSkills.length > 0` OR `alreadyGoverned` is checked.
  - On launch:
    - `skills_extra`: staged skills selected.
    - `skills_disabled`: if `isolateGoverned`, all harness skills minus selected harness skills.

- [ ] **Step 4: Run frontend verification**

Run: `npm --prefix frontend run check`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/SubjectValidation.tsx
git commit -m "feat(frontend): support unified staged and governed skills selection with isolation"
```

---

### Task 5: Inline AI Assistant in Step 2 (`SubjectValidation.tsx`)

**Files:**
- Modify: `frontend/src/pages/SubjectValidation.tsx`

- [ ] **Step 1: Add AI Assistant State**
  - `aiDescription: string`
  - `aiModel: string` (default to `gatewayDefault`)
  - `aiNotice: string`
  - `aiBusy: boolean`

- [ ] **Step 2: Render Inline Smart Assistant Card**
  - Placed at the top of Step 2 panel (`What is being tested...`).
  - Shows tags for skills in context: `Contexto: ${selectedSkills.join(", ") || "Ninguna seleccionada"}`.
  - Textarea for `aiDescription`.
  - Model select dropdown using `modelOptions`.
  - Button `✨ Generar caso con IA` with spinner and disabled states.

- [ ] **Step 3: Implement Generation Handler**
  - Call `api.generateCaseDraft({ description: aiDescription, model: aiModel, selected_skills: selectedSkills })`.
  - Populate `subject`, `prompt`, `commandText`, and `maxSteps`.
  - Set `aiNotice("✨ Caso completado con IA. Puedes revisar y editar los campos abajo.")`.
  - Handle errors gracefully showing inline alert.

- [ ] **Step 4: Run frontend verification**

Run: `npm --prefix frontend run check && npm --prefix frontend run build`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/SubjectValidation.tsx
git commit -m "feat(frontend): integrate inline AI case assistant in Step 2"
```

---

### Task 6: Full Quality Gate & Verification

**Files:**
- Run: `make check`
- Run: `pytest tests/harness_lab_app/test_case_draft.py`

- [ ] **Step 1: Run backend tests**
Run: `pytest tests/harness_lab_app/test_case_draft.py -v`
Expected: PASS

- [ ] **Step 2: Run repository quality gate**
Run: `make check`
Expected: PASS (quality gate green)

- [ ] **Step 3: Commit any final fixes if needed**
```bash
git commit -m "chore: verify quality gate for validate your tool improvements"
```
