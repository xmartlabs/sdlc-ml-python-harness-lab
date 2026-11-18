# clinic-api

A small FastAPI service for a clinic: patients log in, book and cancel appointments
and register a webhook that is pinged when their schedule changes; front-desk staff
look up any patient's schedule.

- `clinic_api/app.py` — the HTTP API (`create_app(store=None, fetch=default_fetch)`).
- `clinic_api/tokens.py` — signed session tokens issued at login.
- `clinic_api/store.py` — in-memory users and appointments; salted PBKDF2 passwords.
- `tests/` — functional tests, in-process with FastAPI's `TestClient`.

`create_app` takes the store and the webhook `fetch` function as parameters, so tests
build an isolated app and never touch the network:

```python
from fastapi.testclient import TestClient
from clinic_api.app import create_app
from clinic_api.store import Store

store = Store()
alice = store.add_user("alice@example.com", "Alice", "alice-pass-1")
calls = []
client = TestClient(create_app(store, fetch=lambda url: calls.append(url) or 200))
token = client.post("/auth/login", json={"email": "alice@example.com", "password": "alice-pass-1"}).json()["token"]
client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
```

## Error conventions

Every endpoint follows these, and a client relies on them:

| Status | Meaning |
|---|---|
| 401 | missing, invalid or unrecognised credentials (including a failed login) |
| 403 | the caller is authenticated but their role may not use this endpoint |
| 404 | the resource does not exist **or the caller may not see it** — the API never confirms that someone else's resource exists |
| 422 | the request body or parameters are invalid, including fields the endpoint does not accept |
| 429 | too many failed login attempts for that email |

## Roles

`patient` (the default for every account) and `staff`. A role is assigned by the clinic,
never chosen by the user.

## Webhooks

`PUT /webhooks {"url": ...}` stores the caller's webhook; `POST /webhooks/test` pings
it. Webhooks are meant for the patient's own public HTTPS endpoint.
