"""In-memory storage. Passwords are stored as salted PBKDF2 hashes."""

from __future__ import annotations

import hashlib
import hmac
import itertools
import secrets
from dataclasses import dataclass, field


def hash_password(password: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    salt = salt or secrets.token_bytes(16)
    return salt, hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)


@dataclass
class User:
    id: int
    email: str
    name: str
    role: str  # "patient" or "staff"
    salt: bytes
    password_hash: bytes
    phone: str = ""

    def check_password(self, password: str) -> bool:
        _, candidate = hash_password(password, self.salt)
        return hmac.compare_digest(candidate, self.password_hash)


@dataclass
class Appointment:
    id: int
    patient_id: int
    doctor: str
    starts_at: str
    reason: str
    status: str = "booked"


@dataclass
class Store:
    users: dict[int, User] = field(default_factory=dict)
    appointments: dict[int, Appointment] = field(default_factory=dict)
    webhooks: dict[int, str] = field(default_factory=dict)
    _ids: itertools.count = field(default_factory=lambda: itertools.count(1))

    def add_user(
        self, email: str, name: str, password: str, role: str = "patient"
    ) -> User:
        salt, digest = hash_password(password)
        user = User(next(self._ids), email.lower(), name, role, salt, digest)
        self.users[user.id] = user
        return user

    def user_by_email(self, email: str) -> User | None:
        return next((u for u in self.users.values() if u.email == email.lower()), None)

    def add_appointment(
        self, patient_id: int, doctor: str, starts_at: str, reason: str
    ) -> Appointment:
        appointment = Appointment(
            next(self._ids), patient_id, doctor, starts_at, reason
        )
        self.appointments[appointment.id] = appointment
        return appointment
