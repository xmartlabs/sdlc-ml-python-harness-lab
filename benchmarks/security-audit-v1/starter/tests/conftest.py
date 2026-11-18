import pytest
from clinic_api.app import create_app
from clinic_api.store import Store
from fastapi.testclient import TestClient


@pytest.fixture
def world():
    store = Store()
    alice = store.add_user("alice@example.com", "Alice", "alice-pass-1")
    bob = store.add_user("bob@example.com", "Bob", "bob-pass-1")
    desk = store.add_user(
        "desk@clinic.example", "Front desk", "desk-pass-1", role="staff"
    )
    calls: list[str] = []
    client = TestClient(create_app(store, fetch=lambda url: calls.append(url) or 200))

    def login(user, password):
        response = client.post(
            "/auth/login", json={"email": user.email, "password": password}
        )
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['token']}"}

    return {
        "store": store,
        "client": client,
        "calls": calls,
        "alice": login(alice, "alice-pass-1"),
        "bob": login(bob, "bob-pass-1"),
        "desk": login(desk, "desk-pass-1"),
        "users": {"alice": alice, "bob": bob, "desk": desk},
    }
