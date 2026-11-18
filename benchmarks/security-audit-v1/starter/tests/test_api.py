def test_login_returns_a_token_and_me_reads_it(world):
    me = world["client"].get("/users/me", headers=world["alice"])
    assert me.status_code == 200
    assert me.json()["email"] == "alice@example.com"
    assert me.json()["role"] == "patient"


def test_requests_without_a_token_are_refused(world):
    assert world["client"].get("/users/me").status_code == 401


def test_a_wrong_password_is_refused(world):
    response = world["client"].post(
        "/auth/login", json={"email": "alice@example.com", "password": "nope"}
    )
    assert response.status_code == 401


def test_repeated_failures_lock_the_email(world):
    for _ in range(5):
        world["client"].post(
            "/auth/login", json={"email": "alice@example.com", "password": "x"}
        )
    response = world["client"].post(
        "/auth/login", json={"email": "alice@example.com", "password": "alice-pass-1"}
    )
    assert response.status_code == 429


def test_a_patient_books_lists_reads_and_cancels(world):
    client = world["client"]
    booked = client.post(
        "/appointments",
        json={
            "doctor": "Dr. Ana Pérez",
            "starts_at": "2026-12-01T09:00",
            "reason": "checkup",
        },
        headers=world["alice"],
    )
    assert booked.status_code == 201
    appointment_id = booked.json()["id"]
    assert [
        a["id"] for a in client.get("/appointments", headers=world["alice"]).json()
    ] == [appointment_id]
    assert client.get("/appointments", headers=world["bob"]).json() == []
    assert (
        client.get(f"/appointments/{appointment_id}", headers=world["alice"]).json()[
            "reason"
        ]
        == "checkup"
    )
    cancelled = client.post(
        f"/appointments/{appointment_id}/cancel", headers=world["alice"]
    )
    assert cancelled.json()["status"] == "cancelled"


def test_a_patient_cannot_cancel_someone_elses_appointment(world):
    client = world["client"]
    booked = client.post(
        "/appointments",
        json={
            "doctor": "Dr. Ana Pérez",
            "starts_at": "2026-12-01T09:00",
            "reason": "checkup",
        },
        headers=world["alice"],
    ).json()
    assert (
        client.post(
            f"/appointments/{booked['id']}/cancel", headers=world["bob"]
        ).status_code
        == 404
    )


def test_a_patient_updates_their_name_and_phone(world):
    response = world["client"].patch(
        "/users/me",
        json={"name": "Alice B.", "phone": "+598 99 000 000"},
        headers=world["alice"],
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Alice B."
    assert response.json()["phone"] == "+598 99 000 000"


def test_staff_see_any_patients_schedule_and_patients_do_not(world):
    client = world["client"]
    alice_id = world["users"]["alice"].id
    client.post(
        "/appointments",
        json={
            "doctor": "Dr. Ana Pérez",
            "starts_at": "2026-12-01T09:00",
            "reason": "checkup",
        },
        headers=world["alice"],
    )
    assert (
        len(
            client.get(
                f"/staff/appointments?patient_id={alice_id}", headers=world["desk"]
            ).json()
        )
        == 1
    )
    assert (
        client.get(
            f"/staff/appointments?patient_id={alice_id}", headers=world["bob"]
        ).status_code
        == 403
    )


def test_doctor_search_is_a_case_insensitive_substring(world):
    assert world["client"].get("/doctors?q=silva").json() == ["Dr. Bruno Silva"]
    assert world["client"].get("/doctors?q=Dr.").json() != []


def test_a_webhook_is_stored_and_pinged(world):
    client = world["client"]
    client.put(
        "/webhooks",
        json={"url": "https://alice.example.com/hook"},
        headers=world["alice"],
    )
    response = client.post("/webhooks/test", headers=world["alice"])
    assert response.json() == {"delivered": True, "status": 200}
    assert world["calls"] == ["https://alice.example.com/hook"]
