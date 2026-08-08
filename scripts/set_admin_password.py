#!/usr/bin/env python3
"""Turn on (or off) the web UI's single-user authentication.

The lab ships in *open mode*: no password, no session gate — which is right for
a laptop and wrong for a box that stays on. This writes the two values that flip
the gate on, into ``.env``, atomically and with a backup:

  HARNESS_LAB_ADMIN_PASSWORD_HASH   PBKDF2-SHA256, salted (never the password)
  HARNESS_LAB_SESSION_SECRET        generated once; rotating it logs you out

Both are read from ``.env`` on every request, so the change applies to the next
request with no restart.

Run with:
  make set-password                                  # prompts twice, hidden
  uv run python scripts/set_admin_password.py --off   # back to open mode
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from harness_lab_app.auth import (  # noqa: E402
    AuthError,
    generate_secret,
    hash_password,
)
from harness_lab_app.env_file import (  # noqa: E402
    BACKUP_DIRNAME,
    values,
    write_env,
)

HASH_KEY = "HARNESS_LAB_ADMIN_PASSWORD_HASH"
SECRET_KEY = "HARNESS_LAB_SESSION_SECRET"
MIN_LENGTH = 8


def _check_length(password: str) -> str:
    if len(password) < MIN_LENGTH:
        raise AuthError(f"Password must be at least {MIN_LENGTH} characters.")
    return password


def _prompt_password() -> str:
    first = _check_length(getpass.getpass("New admin password: "))
    if first != getpass.getpass("Repeat password: "):
        raise AuthError("Passwords do not match.")
    return first


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--off",
        action="store_true",
        help="clear the password hash and return to open mode",
    )
    parser.add_argument(
        "--password",
        default="",
        help="password (non-interactive; prefer the prompt — this lands in your "
        "shell history)",
    )
    parser.add_argument(
        "--rotate-secret",
        action="store_true",
        help="issue a fresh session secret, invalidating every existing session",
    )
    args = parser.parse_args()

    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        print(f"error: {env_path} does not exist — copy .env.example first.")
        return 1

    existing = values(env_path)
    updates: dict[str, str] = {}

    if args.off:
        updates[HASH_KEY] = ""
        print("Authentication disabled — the UI is open to anyone who can reach it.")
    else:
        try:
            password = (
                _check_length(args.password) if args.password else _prompt_password()
            )
            updates[HASH_KEY] = hash_password(password)
        except AuthError as exc:
            print(f"error: {exc}")
            return 1
        except (EOFError, KeyboardInterrupt):
            print("\naborted.")
            return 1

    if args.rotate_secret or not existing.get(SECRET_KEY):
        updates[SECRET_KEY] = generate_secret()

    backup = write_env(env_path, updates, REPO_ROOT / "data" / "app" / BACKUP_DIRNAME)
    print(f"wrote {', '.join(sorted(updates))} to {env_path}")
    if backup:
        print(f"backup: {backup}")
    if not args.off:
        print("Authentication is on. It applies to the next request — no restart.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
