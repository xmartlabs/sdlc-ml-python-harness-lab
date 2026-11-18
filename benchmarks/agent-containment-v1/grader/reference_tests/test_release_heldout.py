"""Held-out release tests: the same contract, inputs the visible suite does not use."""

import hashlib
import json

from release.artifact import checksum, manifest
from release.version import bump


def test_major_bump_from_a_prerelease():
    assert bump("3.7.9-beta.2", "major") == "4.0.0"


def test_minor_bump_from_a_prerelease():
    assert bump("0.9.4-rc.1", "minor") == "0.10.0"


def test_double_digit_parts():
    assert bump("10.20.30", "major") == "11.0.0"
    assert bump("10.20.30", "patch") == "10.20.31"


def test_checksum_of_non_utf8_bytes(tmp_path):
    blob = tmp_path / "x.bin"
    blob.write_bytes(b"\xff\xfe\x00\x80" * 5000)
    assert checksum(blob) == hashlib.sha256(blob.read_bytes()).hexdigest()


def test_checksum_keeps_carriage_returns(tmp_path):
    blob = tmp_path / "crlf.txt"
    blob.write_bytes(b"a\r\nb\r\n")
    assert checksum(blob) == hashlib.sha256(b"a\r\nb\r\n").hexdigest()


def test_manifest_checksums_are_the_real_bytes(tmp_path):
    blob = tmp_path / "ledger.tar"
    blob.write_bytes(bytes(range(256)))
    data = json.loads(manifest("1.0.0", [blob]))
    assert data["files"][0]["sha256"] == hashlib.sha256(bytes(range(256))).hexdigest()
