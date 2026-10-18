"""The testflinger machine boundary as an AttemptSandbox (PROFE-DRIVE).

The thin adapter the campaign wires into profe.driver: every machine
interaction stays ONE boundary-testflinger.sh op (B3), and every
effect-surface verb (lease_ready, submit, resolve, launch, sample,
completion_marker, terminate, harvest, release) maps onto a boundary
verb. Conformance is STRUCTURAL: this module imports profe only in a
comment's imagination — no import — so the sandbox stays usable as
wire-fidelity evidence that profe's Protocol is duck-shapeable.

Capability data (read by the drive loop via getattr, never by type):
- reprovisions_on_resubmit = True  (rig re-image truth: a stale job's
  endpoint cannot survive a fresh submit)
- recover_releases_lease = True  (recover-bundle.sh cancels the job as
  its step 5 — the drive loop's release() becomes a no-op afterwards,
  but it still calls it: idempotent, and the split is what keeps the
  premature-done bug unrepresentable)
- endpoint_label / queue_state / prior_bundles are read similarly.

The boundary scripts remain VERBATIM (the verified chokepoints): this
file only re-shapes what they return into the profe protocol's types.
"""

import os
import re
import subprocess
import time
from pathlib import Path

RIG = Path(__file__).resolve().parent
BOUNDARY = RIG / "boundary-testflinger.sh"


class TestflingerSandbox:
    """AttemptSandbox over rig/boundary-testflinger.sh."""

    # capability data (drive loop reads these via getattr)
    reprovisions_on_resubmit = True
    recover_releases_lease = True

    def __init__(self, state_dir: Path, dry: bool, task_file: str, task_id: str):
        """`dry` gates every mutating op; state_dir feeds recover/dump-gt.

        state_dir is RESOLVED to absolute: the boundary scripts cd into
        the rig dir before each op, so a relative STATE_DIR would
        resolve against the WRONG cwd (2026-10-18 rehearsal pair 7: a
        relative --state doubled the recovered-stamp path and the
        driver adjudicated an empty dir — infra-failed). Absolute
        in, absolute out.
        """
        self.state_dir = Path(state_dir).resolve()
        self.dry = dry
        self.task_file = task_file  # frozen task file name (manifest)
        self.task_id = task_id  # frozen task id (manifest)

    def _run(self, *args: str, timeout: int = 120) -> tuple[int, str]:
        p = subprocess.run(
            ["bash", str(BOUNDARY), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={
                "PATH": "/usr/local/bin:/usr/bin:/bin:/snap/bin:"
                + str(Path.home() / ".local/bin"),
                "STATE_DIR": str(self.state_dir),
                "HOME": str(Path.home()),
                # the rig authorizes the host key via ssh-agent (lp: key in
                # reserve_data); without the socket every ssh op fails fast
                # and wait_rig burns its whole window silently
                "SSH_AUTH_SOCK": os.environ.get("SSH_AUTH_SOCK", ""),
            },
        )
        return p.returncode, p.stdout.strip()

    # -- AttemptSandbox verbs ------------------------------------------
    def lease_ready(self, spec) -> bool:
        """Check the machine's own queue for the agent waiting/idle."""
        return self.queue_state(spec.queue) in ("waiting", "idle")

    def queue_state(self, queue: str) -> str:
        """Read the queue's agent state (empty string = unparseable -> unknown)."""
        return self._run("agent-state", queue, timeout=45)[1] or "unknown"

    def submit(self, spec) -> str:
        """Submit the rig job to the machine's own queue -> job uuid."""
        rc, out = self._run("submit", spec.queue, timeout=90)
        if rc != 0 or not re.fullmatch(r"[0-9a-f-]{36}", out):
            raise RuntimeError(f"submit {spec.queue} failed: {out[:200]}")
        return out

    def resolve(self, job: str):
        """ONE poll of the lease results for device_ip (None while granting)."""
        return self._run("device-ip", job, timeout=45)[1] or None

    def job_failed(self, job: str) -> bool:
        """Lab-side fast-fail: the job terminally FAILED (provision/test).

        rc 0 = failed — the drive loop treats it as immediate infra
        instead of burning the ip_wait/rig_wait windows against a dead
        node (manifest v4 amendment, 2026-10-18). A healthy job is
        never `complete` during those windows; `complete` + a nonzero
        phase status is the failure signature. Unreadable results =
        unknown = not failed (the windows stay the backstop).
        """
        return self._run("job-failed", job, timeout=45)[0] == 0

    def clear_stale_endpoint(self, endpoint: str) -> None:
        """Clear a stale host key (persistent deploy rotates keys, §3.2)."""
        self._run("keygen-r", endpoint)

    def endpoint_ready(self, endpoint: str) -> bool:
        """Check ~/rig-ready over ssh (single check)."""
        return self._run("rig-ready", endpoint, timeout=30)[0] == 0

    def endpoint_label(self, endpoint: str) -> str:
        """Pool mode (§3.7 v2): the DUT's hostname == machine name."""
        return self._run("hostname", endpoint, timeout=30)[1]

    def launch(self, endpoint: str, spec, attempt: int) -> int:
        """Stage payload + start the attempt (launch-payload.sh) -> t0.

        RuntimeError propagates: the drive loop records the infra
        (rig-parity: a launch failure is an attempt-level infra-failed,
        not a daemon death).
        """
        rc, out = self._run(
            "launch",
            endpoint,
            str(spec.run_id),
            spec.arm,
            str(attempt),
            spec.manifest_sha8,
            self.task_file,
            self.task_id,
            timeout=600,
        )
        if rc != 0:
            raise RuntimeError(f"launch failed: {out[-200:]}")
        return int(time.time())

    def sample(self, endpoint: str, spec):
        """Live session sample line, or the SSHFAIL sentinel.

        Returns the 8-field wire line (str) — the monitor parses it;
        on ssh failure returns a duck sentinel with unreachable=True
        (no profe import needed to say 'the transport missed').
        """
        rc, out = self._run("sample", endpoint, str(spec.run_id), spec.arm, timeout=45)
        if rc != 0 or not out or out.strip() == "SSHFAIL":
            return _Miss()
        return out

    def completion_marker(self, endpoint: str, spec) -> str:
        """Read the runner's terminal marker ("" while still starting)."""
        return self._run("runner-status", endpoint, str(spec.run_id), spec.arm)[1]

    def terminate(self, endpoint: str, spec) -> None:
        """TERM the profe process group on the DUT."""
        self._run("kill-run", endpoint, str(spec.run_id), spec.arm, timeout=45)

    def harvest(self, endpoint: str, job: str, spec, force: bool) -> Path:
        """Pull + scrub + release-side-effects (recover-bundle.sh).

        The boundary rsyncs ~/payload/runs/ into DEST/runs/ and CANCELS
        the lease (step 5) — recover_releases_lease declares that; the
        drive loop still calls release() and the no-op is idempotent.
        Returns the attempt's bundle dir (DEST/runs/pair-N/<arm>), the
        shape profe's adjudication reads.
        """
        if self.dry:
            raise RuntimeError("dry-run: harvest refused")
        rc, out = self._run(
            "recover",
            endpoint,
            job,
            *(["force"] if force else []),
            timeout=1800,
        )
        if rc != 0 or not out:
            raise RuntimeError(f"recover failed: {out[-200:]}")
        rec_dir = Path(out.splitlines()[-1])
        return rec_dir / "runs" / f"pair-{spec.run_id}" / spec.arm

    def release(self, job: str) -> None:
        """Idempotent lease release (cancel)."""
        if not self.dry:
            self._run("cancel", job)

    def snapshot_environment(self, spec, endpoint: str) -> None:
        """Ground-truth/egress dump into the state dir (§9).

        Label = pair-arm: pool mode gives both slots one queue name.
        Gated on dry (dump-gt touches the state dir).
        """
        if not self.dry:
            self._run("dump-gt", f"{spec.run_id}-{spec.arm}", endpoint, timeout=90)

    def prior_bundles(self, spec) -> list[Path]:
        """All recovered bundle dirs for (pair, arm), oldest first.

        Rig find_recovered parity: glob the state dir's recovered-*
        stamp dirs; the drive loop adjudicates the first valid one.
        """
        return [
            rec / spec.arm
            for rec in sorted(
                self.state_dir.glob(f"recovered-*/runs/pair-{spec.run_id}")
            )
        ]


class _Miss:
    """Duck sentinel for 'the transport missed' (no profe import)."""

    unreachable = True
