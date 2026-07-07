def test_case_models_and_schemas_expose_expected_defaults():
    from harness_lab_app.models import Case, CaseSyncState, CaseType
    from harness_lab_app.schemas import CaseCreate

    case = Case(
        case_id="prompt-1",
        name="Prompt 1",
        type=CaseType.single_prompt,
    )
    create = CaseCreate(case_id="prompt-1", name="Prompt 1", type=CaseType.single_prompt)

    assert CaseType.single_prompt.value == "single_prompt"
    assert case.sync_state == CaseSyncState.not_synced
    assert case.prompt_variants == {}
    assert case.metadata_json == {}
    assert create.max_steps == 1
