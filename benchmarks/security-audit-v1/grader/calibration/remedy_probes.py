"""Proof tests written for each correct remedy of the entry with alternative fixes.

Lab calibration, hidden from the agent with the rest of ``grader/``: each test is
how a different agent would assert the secure behaviour, committed to one remedy.
Every one must pass as a proof (fails on the starter, passes on some accepted fix).
"""


def _escalate(world):
    client = world["client"]
    response = client.patch("/users/me", json={"role": "staff"}, headers=world["bob_h"])
    role = client.get("/users/me", headers=world["bob_h"]).json()["role"]
    return response.status_code, role


def test_ignored(world):
    status, role = _escalate(world)
    assert (status, role) == (200, "patient")


def test_refused(world):
    status, role = _escalate(world)
    assert (status, role) == (403, "patient")


def test_rejected(world):
    status, role = _escalate(world)
    assert (status, role) == (422, "patient")
