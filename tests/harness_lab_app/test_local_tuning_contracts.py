"""Unit tests for pydantic serialization quirks in :mod:`harness_lab_app.local_tuning.contracts`."""

from __future__ import annotations

from harness_lab_app.local_tuning.contracts import ActiveRunRef, GuardReport


def test_guard_report_clear_is_serialized():
    """``clear`` must be a computed field so model_dump() always includes it.

    A plain ``@property`` is invisible to pydantic's serializer; the frontend
    reads ``clear`` straight off the JSON body and would otherwise treat every
    response as busy.
    """
    report = GuardReport(host="http://localhost:1234")
    dumped = report.model_dump()
    assert dumped["clear"] is True

    busy = GuardReport(
        host="http://localhost:1234",
        running=[ActiveRunRef(run_id="r1", state="running", model="m")],
    )
    assert busy.model_dump()["clear"] is False


def test_guard_report_routing_unknown_defaults_false():
    report = GuardReport(host="http://localhost:1234")
    assert report.routing_unknown is False
    assert report.model_dump()["routing_unknown"] is False
