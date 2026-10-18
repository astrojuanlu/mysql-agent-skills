"""boundary-testflinger.sh verb pins — the lab-job fast-fail signal.

The `job-failed` verb (manifest v4 amendment, 2026-10-18 operator GO):
rc 0 iff the job's results show the terminal-failure signature
(job_state=complete AND a nonzero provision/test/cleanup status); rc 1
otherwise. This is the detection the pair-1/A incident lacked — a
failed MAAS deploy still reported device_ip, so the old verb set gave
the drive loop no way to learn the job was already dead.

No lab contact: testflinger-cli is stubbed on PATH with canned results
payloads (the real failure payload observed 2026-10-18T08:30Z is the
template for FAILED).
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

RIG = Path(__file__).resolve().parent
BOUNDARY = RIG / "boundary-testflinger.sh"

# The observed real failure (pair 1/A, trimmed to the fields
# the verb reads):
FAILED = json.dumps(
    {
        "job_state": "complete",
        "provision_status": 1,
        "test_status": 0,
        "cleanup_status": 0,
    }
)
# A healthy mid-flight job: never `complete` while the drive loop waits.
RUNNING = json.dumps(
    {
        "job_state": "running",
        "provision_status": 0,
        "test_status": 0,
        "cleanup_status": 0,
    }
)


def _run(results: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Run the job-failed verb with a stubbed testflinger-cli."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    payload = tmp_path / "results.json"
    payload.write_text(results)
    stub = bindir / "testflinger-cli"
    stub.write_text(f'#!/bin/sh\ncat "{payload}"\n')
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    return subprocess.run(
        ["bash", str(BOUNDARY), "job-failed", "job-x"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_job_failed_true_on_failed_provision(tmp_path, monkeypatch):
    """The signature that burned pair 1/A: complete + provision_status 1."""
    assert _run(FAILED, tmp_path, monkeypatch).returncode == 0


def test_job_failed_true_on_failed_test_phase(tmp_path, monkeypatch):
    """A failed test phase is terminal too (same rule, different status)."""
    body = json.loads(RUNNING) | {"job_state": "complete", "test_status": 2}
    assert _run(json.dumps(body), tmp_path, monkeypatch).returncode == 0


def test_job_failed_false_while_job_alive(tmp_path, monkeypatch):
    """A running/reserve job is NOT failed — the windows stay the backstop."""
    assert _run(RUNNING, tmp_path, monkeypatch).returncode == 1


def test_job_failed_false_on_unreadable_results(tmp_path, monkeypatch):
    """Garbage/empty results = unknown = NOT failed (never a false infra)."""
    assert _run("", tmp_path, monkeypatch).returncode == 1
    assert _run("not json at all", tmp_path, monkeypatch).returncode == 1
