# ADR-0015 — Single-user session authentication

- **Status:** Accepted
- **Date:** 2026-08-07
- **Deciders:** Marcos Soto
- **Related:** ADR-0013 (web backoffice and overlay model), ADR-0014 (web-editable
  `.env`), ADR-0019 (always-on local deployment)

## Context

The lab is moving from a laptop the operator opens and closes to a machine that stays
on. That changes the threat model in three ways at once:

- The web UI now **creates containers, edits `.env` and reads credentials**
  (ADR-0014). Reaching port 5173 is equivalent to a shell on the box.
- It listens on **all interfaces** — `docker-compose.yml` publishes `8008` and `5173`
  without a bind address — so every device on the LAN can reach it.
- The only gate that existed was `POST /api/auth/login` comparing against
  `local_username` / `local_password`, defaulting to `admin`/`admin` in plaintext, and
  setting **no cookie**. Nothing consumed the result: every other endpoint was open.

There is exactly one operator. Users, roles, password resets and OAuth are all cost
without benefit here.

Two constraints from the existing code shape the design:

- **The test suite never uses `TestClient`.** `tests/harness_lab_app/test_api.py`
  resolves a route's endpoint function and calls it directly, so a `Depends`-based
  guard would be invisible to ~520 existing tests — and, worse, would be silently
  absent from any route where it was forgotten.
- **No HTTP client dependency is available.** `uv.lock` has neither `httpx`,
  `passlib` nor `itsdangerous`.

## Decision

We will add single-user, password-plus-session authentication, off by default.

1. **`src/harness_lab_app/auth.py`, stdlib only.** PBKDF2-HMAC-SHA256 at 480 000
   iterations with a per-password salt, stored as a self-describing
   `pbkdf2_sha256$iterations$salt$hash` string so the cost factor can be raised later
   without invalidating existing hashes. Sessions are stateless: an HMAC-SHA256
   signature over `{"iat": <unix seconds>}`, keyed by a generated secret. Every
   comparison goes through `hmac.compare_digest`.
2. **A middleware, not a dependency.** `SessionMiddleware` gates every path under
   `/api/` except an explicit public set: `GET /api/health` (the compose healthcheck
   depends on it), `POST /api/auth/login`, `POST /api/auth/logout`,
   `GET /api/auth/session`, and every `OPTIONS` preflight. A route added tomorrow is
   protected by construction. `/api/health/detail` is deliberately **not** public — it
   reports gateway, worker and drift state.
3. **Open mode is the default.** With no password hash in `.env`, the middleware is a
   pass-through and `GET /api/auth/session` answers
   `{"authenticated": true, "auth_enabled": false}`. The dev flow and every existing
   test keep working unchanged, and the UI shows a persistent banner until the
   operator runs `make set-password`. Enabling auth is an explicit act, not a
   migration everyone has to survive.
4. **`.env` holds the material, read per request.**
   `HARNESS_LAB_ADMIN_PASSWORD_HASH` and `HARNESS_LAB_SESSION_SECRET` are read with
   `read_env_file_value()` on each request rather than from the `lru_cache`d settings,
   so `make set-password` applies to the next request with no restart. Both keys are
   in `settings_service.PROTECTED_KEYS`: the Settings page can never read or write
   them, so a stolen session cannot rewrite the password from the browser.
   `HARNESS_LAB_SESSION_COOKIE_SECURE` is a normal cached setting, since it only
   changes when the deployment changes.
5. **CSRF: `SameSite=Lax` plus an `Origin` host check** on `POST`/`PUT`/`PATCH`/
   `DELETE`. `Lax` alone already stops a cross-site form post from carrying the
   cookie; the `Origin` check is the second lock, and the one that still holds if a
   browser loosens that default. A missing `Origin` is allowed — browsers omit it only
   for same-origin navigations, and non-browser clients carry no ambient cookie.
6. **The cookie is `HttpOnly`, `SameSite=Lax`, `Secure=false` by default.** The lab is
   served over plain HTTP on a LAN address; a `Secure` cookie would be silently
   dropped by the browser and the operator would see an unexplained login loop.
   Operators terminating TLS set `HARNESS_LAB_SESSION_COOKIE_SECURE=true`.
7. **An in-memory login throttle.** Five consecutive failures per client address block
   further attempts, doubling from 30s up to 15 minutes. In-memory is adequate: the
   API is a single process, and a restart clearing the counter is not a meaningful
   bypass when each attempt already costs a full PBKDF2 round.
8. **The plaintext backdoor is removed.** `local_username` and `local_password` are
   deleted from `Settings` rather than repurposed — leaving an `admin`/`admin` default
   in a codebase is how it survives into production.

## Threat model — explicitly LAN-only

This design defends against **someone else on your network reaching the lab**: a
housemate, a guest device, a compromised IoT box, a colleague on the office subnet. It
assumes the operator, the server, and `.env` are trusted.

It does **not** defend against:

- **Network observers.** Plain HTTP means the password crosses the LAN in the clear on
  login and the cookie on every request. Exposing the lab beyond a trusted network
  requires TLS in front of it (and `SESSION_COOKIE_SECURE=true`).
- **Anyone with the `.env` file or a shell on the host.** They hold the session secret
  and every gateway credential; auth is irrelevant at that point.
- **Session revocation before expiry.** Tokens are stateless, so the only way to
  invalidate outstanding sessions is `make set-password ROTATE=1`, which changes the
  secret and logs everyone out. This is the deliberate trade for surviving a restart
  without a session store.
- **XSS in the UI.** `HttpOnly` keeps the cookie out of reach of injected script, but
  script running on the page can still call the API as the operator.

**Do not port-forward this to the internet.**

## Consequences

- `POST /api/auth/login` changes shape: `{password}`, no `username`. The frontend is
  the only client; the one test asserting the old default credentials is replaced.
- The API answers `401` instead of serving data when the session is missing or
  expired. The frontend turns any `401` into a `harness-lab:session-expired` event and
  re-renders the login form, so an expired 7-day session degrades to "log in again"
  rather than every page showing an error.
- The compose healthcheck keeps working because `GET /api/health` stays public. The
  cost is that an unauthenticated caller can confirm the service is alive — an
  acceptable trade for a liveness probe that returns nothing but `{"status": "ok"}`.
- Forgetting the password means running `make set-password` on the server. There is no
  recovery flow and no email; that is the point of a single-user design.
- The middleware runs on **every** `/api/` request, so `read_env_file_value` is called
  once per request in open mode too. It is a small file read on a local disk, and
  keeping it uncached is what makes `make set-password` take effect immediately.
