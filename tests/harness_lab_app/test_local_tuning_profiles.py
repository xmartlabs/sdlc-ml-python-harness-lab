from __future__ import annotations

from harness_lab_app.local_tuning.profile_service import ProfileService


def _machine_data(name="M1") -> dict:
    return {"name": name, "memory_kind": "discrete", "gpu_gb": 24, "ram_gb": 64}


def _profile_data(machine_id: int, model_id: str = "prism-ml/bonsai-27b") -> dict:
    return {
        "machine_id": machine_id,
        "server_kind": "lmstudio",
        "server_url": "http://localhost:1234",
        "model_id": model_id,
        "label": "default",
    }


def test_create_and_list_machine(session):
    service = ProfileService(session)
    machine = service.create_machine(_machine_data())
    assert machine.id is not None
    assert [m.name for m in service.list_machines()] == ["M1"]


def test_update_and_delete_machine(session):
    service = ProfileService(session)
    machine = service.create_machine(_machine_data())
    updated = service.update_machine(machine.id, {"gpu_gb": 32})
    assert updated.gpu_gb == 32
    assert service.delete_machine(machine.id) is True
    assert service.list_machines() == []


def test_recommended_exclusivity_within_same_machine_and_model(session):
    service = ProfileService(session)
    machine = service.create_machine(_machine_data())
    profile_a = service.create_profile(
        {**_profile_data(machine.id), "label": "a", "recommended": True}
    )
    profile_b = service.create_profile(
        {**_profile_data(machine.id), "label": "b", "recommended": True}
    )
    refreshed_a = service.get_profile(profile_a.id)
    refreshed_b = service.get_profile(profile_b.id)
    assert refreshed_a.recommended is False
    assert refreshed_b.recommended is True


def test_recommended_exclusivity_does_not_affect_other_models(session):
    service = ProfileService(session)
    machine = service.create_machine(_machine_data())
    profile_a = service.create_profile(
        {**_profile_data(machine.id, model_id="model-a"), "recommended": True}
    )
    service.create_profile(
        {**_profile_data(machine.id, model_id="model-b"), "recommended": True}
    )
    refreshed_a = service.get_profile(profile_a.id)
    assert refreshed_a.recommended is True


def test_update_profile_sets_recommended_and_clears_siblings(session):
    service = ProfileService(session)
    machine = service.create_machine(_machine_data())
    profile_a = service.create_profile(
        {**_profile_data(machine.id), "recommended": True}
    )
    profile_b = service.create_profile({**_profile_data(machine.id)})
    service.update_profile(profile_b.id, recommended=True)
    assert service.get_profile(profile_a.id).recommended is False
    assert service.get_profile(profile_b.id).recommended is True


def test_compare_refuses_different_machines(session):
    service = ProfileService(session)
    machine_a = service.create_machine(_machine_data("M1"))
    machine_b = service.create_machine(_machine_data("M2"))
    profile_a = service.create_profile(_profile_data(machine_a.id))
    profile_b = service.create_profile(_profile_data(machine_b.id))
    result = service.compare(profile_a.id, profile_b.id)
    assert result["comparable"] is False
    assert "machine" in result["reason"].lower()


def test_compare_refuses_different_models(session):
    service = ProfileService(session)
    machine = service.create_machine(_machine_data())
    profile_a = service.create_profile(_profile_data(machine.id, model_id="model-a"))
    profile_b = service.create_profile(_profile_data(machine.id, model_id="model-b"))
    result = service.compare(profile_a.id, profile_b.id)
    assert result["comparable"] is False
    assert "model" in result["reason"].lower()


def test_compare_ok_same_machine_and_model(session):
    service = ProfileService(session)
    machine = service.create_machine(_machine_data())
    profile_a = service.create_profile(_profile_data(machine.id))
    profile_b = service.create_profile(_profile_data(machine.id))
    result = service.compare(profile_a.id, profile_b.id)
    assert result["comparable"] is True
    assert "noise" in result["reason"]  # identical profiles: no winner


def test_delete_profile(session):
    service = ProfileService(session)
    machine = service.create_machine(_machine_data())
    profile = service.create_profile(_profile_data(machine.id))
    assert service.delete_profile(profile.id) is True
    assert service.get_profile(profile.id) is None


def test_compare_explains_which_profile_is_better(session):
    from harness_lab_app.local_tuning.profile_service import ProfileService

    service = ProfileService(session)
    machine = service.create_machine(
        {"name": "rig", "memory_kind": "discrete", "gpu_gb": 24, "ram_gb": 64}
    )

    def results(step):
        return [
            {
                "name": "speed",
                "verdict": "ok",
                "reading": "",
                "detail": {
                    "seconds_per_step": step,
                    "read_tokens_per_second": 14000 / step,
                    "write_tokens_per_second": 70.0,
                },
            },
        ]

    a = service.create_profile(
        {
            "machine_id": machine.id,
            "server_kind": "lmstudio",
            "server_url": "http://h:1234",
            "model_id": "m",
            "label": "a",
            "results": results(14.0),
        }
    )
    b = service.create_profile(
        {
            "machine_id": machine.id,
            "server_kind": "lmstudio",
            "server_url": "http://h:1234",
            "model_id": "m",
            "label": "b",
            "results": results(7.0),
        }
    )
    result = service.compare(a.id, b.id)
    assert result["comparable"] is True
    assert result["comparison"]["overall"] == "b"
    history = service.history(machine.id)
    assert [row["profile"].label for row in history] == ["b", "a"]
    assert history[0]["summary"]["seconds_per_step"] == 7.0
