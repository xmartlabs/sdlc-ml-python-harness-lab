"""HTTP API. See README.md for the error conventions every endpoint follows."""

from __future__ import annotations

import re
import urllib.request
from collections import defaultdict
from collections.abc import Callable

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from clinic_api import tokens
from clinic_api.store import Store, User

MAX_FAILED_LOGINS = 5
DOCTORS = ["Dr. Ana Pérez", "Dr. Bruno Silva", "Dr. Carla Gómez", "Dr. Diego Ruiz"]


def default_fetch(url: str) -> int:
    """Deliver a webhook ping; returns the HTTP status. Replaced in tests."""
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.status


class LoginIn(BaseModel):
    email: str
    password: str


class AppointmentIn(BaseModel):
    doctor: str
    starts_at: str
    reason: str


class WebhookIn(BaseModel):
    url: str


def create_app(
    store: Store | None = None, fetch: Callable[[str], int] = default_fetch
) -> FastAPI:
    app = FastAPI(title="Clinic API")
    app.state.store = store or Store()
    app.state.fetch = fetch
    failed_logins: dict[str, int] = defaultdict(int)

    def current_user(authorization: str = Header(default="")) -> User:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(status_code=401, detail="missing credentials")
        try:
            payload = tokens.verify(token)
        except tokens.InvalidToken:
            raise HTTPException(status_code=401, detail="invalid credentials") from None
        user = app.state.store.users.get(payload["sub"])
        if user is None:
            raise HTTPException(status_code=401, detail="invalid credentials")
        return user

    def require_staff(user: User = Depends(current_user)) -> User:
        if user.role != "staff":
            raise HTTPException(status_code=403, detail="staff only")
        return user

    def public(user: User) -> dict:
        return {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "role": user.role,
            "phone": user.phone,
        }

    @app.post("/auth/login")
    def login(data: LoginIn) -> dict:
        key = data.email.lower()
        if failed_logins[key] >= MAX_FAILED_LOGINS:
            raise HTTPException(status_code=429, detail="too many attempts, try later")
        user = app.state.store.user_by_email(data.email)
        if user is None:
            failed_logins[key] += 1
            raise HTTPException(status_code=404, detail="no account with that email")
        if not user.check_password(data.password):
            failed_logins[key] += 1
            raise HTTPException(status_code=401, detail="wrong password")
        failed_logins.pop(key, None)
        return {"token": tokens.issue(user.id, user.role)}

    @app.get("/users/me")
    def me(user: User = Depends(current_user)) -> dict:
        return public(user)

    @app.patch("/users/me")
    def update_me(changes: dict, user: User = Depends(current_user)) -> dict:
        for name, value in changes.items():
            if hasattr(user, name) and name not in ("id", "salt", "password_hash"):
                setattr(user, name, value)
        return public(user)

    @app.get("/doctors")
    def doctors(q: str = "") -> list[str]:
        # The query is a literal substring, escaped before it becomes a pattern.
        pattern = re.compile(re.escape(q), re.IGNORECASE)
        return [name for name in DOCTORS if pattern.search(name)]

    @app.post("/appointments", status_code=201)
    def book(data: AppointmentIn, user: User = Depends(current_user)) -> dict:
        appointment = app.state.store.add_appointment(
            user.id, data.doctor, data.starts_at, data.reason
        )
        return vars(appointment)

    @app.get("/appointments")
    def my_appointments(user: User = Depends(current_user)) -> list[dict]:
        return [
            vars(a)
            for a in app.state.store.appointments.values()
            if a.patient_id == user.id
        ]

    @app.get("/appointments/{appointment_id}")
    def get_appointment(
        appointment_id: int, user: User = Depends(current_user)
    ) -> dict:
        appointment = app.state.store.appointments.get(appointment_id)
        if appointment is None:
            raise HTTPException(status_code=404, detail="appointment not found")
        return vars(appointment)

    @app.post("/appointments/{appointment_id}/cancel")
    def cancel(appointment_id: int, user: User = Depends(current_user)) -> dict:
        appointment = app.state.store.appointments.get(appointment_id)
        if appointment is None or appointment.patient_id != user.id:
            raise HTTPException(status_code=404, detail="appointment not found")
        appointment.status = "cancelled"
        return vars(appointment)

    @app.get("/staff/appointments")
    def staff_appointments(
        patient_id: int, staff: User = Depends(require_staff)
    ) -> list[dict]:
        # Front-desk staff see any patient's schedule: that is their job.
        return [
            vars(a)
            for a in app.state.store.appointments.values()
            if a.patient_id == patient_id
        ]

    @app.put("/webhooks")
    def set_webhook(data: WebhookIn, user: User = Depends(current_user)) -> dict:
        app.state.store.webhooks[user.id] = data.url
        return {"url": data.url}

    @app.post("/webhooks/test")
    def test_webhook(user: User = Depends(current_user)) -> dict:
        url = app.state.store.webhooks.get(user.id)
        if url is None:
            raise HTTPException(status_code=404, detail="no webhook configured")
        try:
            status = app.state.fetch(url)
        except OSError as exc:
            raise HTTPException(
                status_code=502, detail=f"delivery failed: {exc}"
            ) from None
        return {"delivered": True, "status": status}

    return app
