"""The sandbox interface + the local reference backend (§4).

Effect surface for ONE attempt lifecycle. Implementations must not
import profe (structural conformance); profe never imports a backend
(the campaign wires them). No ABC, no base hierarchy: optional verbs
are separate small protocols; backend lifecycle truths are capability
DATA (e.g. `reprovisions_on_resubmit`), never hooks in the drive loop.

The two design insights the rehearsal paid for (§4):
- `harvest` and `release` are SEPARATE verbs — today's recover
  conflated pull+scrub+release and released a live lease inside the
  harvest (the premature-done bug's amplifier). With the split the bug
  is unrepresentable.
- `resolve -> None` and `completion_marker -> ""` encode the
  poll-effects contract: the drive loop owns patience, and "done" is
  only ever the attempt runtime's own marker.
"""

from __future__ import annotations

import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from profe.driver.models import Sample, parse_sample


@dataclass(frozen=True)
class AttemptSpec:
    """What the sandbox must know to run ONE attempt.

    The campaign fills it; the sandbox interprets it.
    """

    run_id: int  # pair today
    arm: str  # "on"/"off" today
    payload_ref: str  # what to stage (dir/hash today)
    manifest_sha8: str  # provenance to bake into the attempt
    queue: str = ""  # where to submit (pool mode: one shared queue;
    # campaign judgment fills it — A8 — the sandbox only interprets it)


type SampleOrMiss = Sample | SandboxUnreachable | str
"""What sample() may return: a Sample, the 8-field wire line (str —
for backends that don't import profe), or the unreachable sentinel.
"""


@dataclass(frozen=True)
class SandboxUnreachable:
    """Sentinel returned by sample().

    The monitor counts consecutive misses (today's sshfail counter).
    Not an exception: unreachability is a normal poll result, not a
    control-flow break. Backends that don't import profe return any
    object with a truthy `unreachable` attribute — the monitor duck-
    checks it, so conformance stays structural.
    """

    unreachable: bool = True


class AttemptSandbox(Protocol):
    """Effect surface for ONE attempt lifecycle."""

    def lease_ready(self, spec: AttemptSpec) -> bool:
        """Capacity gate before submitting (queue shows a free agent).

        Per-attempt: pool mode gates on the spec's queue (2026-10-18
        decision — no machine pinning, both slots share one queue).
        """
        ...

    def submit(self, spec: AttemptSpec) -> str:
        """Acquire a lease / start a job -> opaque handle."""
        ...

    def resolve(self, job: str) -> str | None:
        """Return the attempt's reachability, or None while granting.

        Returning None (not raising) is the poll-effect contract: the
        drive loop owns patience via CapPolicy windows, never via
        sandbox-reported states.
        """
        ...

    def endpoint_ready(self, endpoint: str) -> bool:
        """Report whether the attempt environment declared itself prepared."""
        ...

    def launch(self, endpoint: str, spec: AttemptSpec, attempt: int) -> int:
        """Stage inputs and start the attempt.

        Returns t0, the attempt-envelope start (§4.4 wall base).
        """
        ...

    def sample(self, endpoint: str, spec: AttemptSpec) -> SampleOrMiss:
        """Live session read.

        Returns a Sample, the 8-field wire line (str — backends that
        don't import profe), or the unreachable sentinel; an empty
        string also counts as unreachable (the ssh transport's miss).
        """
        ...

    def completion_marker(self, endpoint: str, spec: AttemptSpec) -> str:
        """Return the attempt runtime's own terminal marker.

        Empty string while booting. The done-gate reads THIS, never
        alive==0 alone — the premature-done lesson is baked into the
        interface.
        """
        ...

    def terminate(self, endpoint: str, spec: AttemptSpec) -> None:
        """Kill the attempt process group (cap/wall/zact/giveup)."""
        ...

    def harvest(self, endpoint: str, job: str, spec: AttemptSpec, force: bool) -> Path:
        """Pull the attempt bundle out.

        endpoint = where the artifacts live; job = the lease handle
        (backends whose recovery cancels or cancels-as-a-side-effect
        need both, e.g. the testflinger recover verb). MUST NOT release
        the lease — harvesting and releasing are separate effects; a
        backend whose recovery releases must say so via capability
        data (e.g. `recover_releases_lease = True`).
        """
        ...

    def release(self, job: str) -> None:
        """Give the lease back. Idempotent."""
        ...


@runtime_checkable
class ClearsStaleEndpoints(Protocol):
    """Backend capability: endpoints die on re-submit.

    Testflinger persistent deploy reimages the disk and rotates the
    host key.
    """

    def clear_stale_endpoint(self, endpoint: str) -> None:
        """Forget a stale endpoint mapping (testflinger: keygen-r)."""
        ...  # keygen-r


@runtime_checkable
class SnapshotsEnvironment(Protocol):
    """§9 ground-truth dump; optional (Gen10 today)."""

    def snapshot_environment(self, spec: AttemptSpec, endpoint: str) -> None:
        """Dump the environment ground truth (§9, optional capability).

        The spec supplies the label (run_id-arm); the endpoint is where
        the dump is taken.
        """
        ...


@runtime_checkable
class YieldsPriorBundles(Protocol):
    """Backend capability: name bundles previous attempts produced.

    Oldest first, first valid wins (rig find_recovered parity: junk
    dirs sort before the valid one and are skipped by adjudication).
    """

    def prior_bundles(self, spec: AttemptSpec) -> list[Path]:
        """Return prior recovered bundle dirs for this spec, oldest first."""
        ...


def endpoint_label_of(sandbox: AttemptSandbox, endpoint: str) -> str | None:
    """Read the backend's machine-identity label, if it provides one.

    Pool mode records identity, never pins it. Capability data via
    getattr — the loop never branches on backend type.
    """
    getter = getattr(sandbox, "endpoint_label", None)
    if getter is None:
        return None
    label = getter(endpoint)
    # capability data is duck-typed (backends need not import profe): a
    # non-str label — including a backend's own "unknown" None — reads
    # as no label.
    return label if isinstance(label, str) else None


class LocalOneShotSandbox:
    """Reference backend: the poc-bwrap contract as a lease (~60 lines).

    submit = scratch dir (prepared by that act — the poc contract:
    a fresh dir IS a prepared environment), resolve = its path,
    launch = spawn the runner, sample = artifact reads + spawned-process
    liveness, completion_marker = the runner's status file, harvest =
    copy the bundle out, release = rm the scratch. Gives profe a self-
    contained test path (the whole suite runs with zero external
    providers) and is the natural backend for podman/lxd/bwrap
    campaigns — the research poc scripts ARE this backend in bash.
    """

    reprovisions_on_resubmit = False  # capability data: a re-submit into
    # a fresh scratch dir starts clean; nothing is reused behind the loop

    def __init__(self, root: Path, runner_cmd: list[str]):
        """Bind the scratch root and the runner command to spawn."""
        self.root = Path(root)
        self.runner_cmd = runner_cmd
        # Popen[bytes]: the runner's streams are redirected to files, not
        # pipes — the type parameter is the conventional default here.
        self.procs: dict[str, subprocess.Popen[bytes]] = {}  # by endpoint dir

    def _dir(self, spec: AttemptSpec) -> Path:
        return self.root / f"attempt-{spec.run_id}-{spec.arm}"

    def lease_ready(self, spec: AttemptSpec) -> bool:
        """Local capacity is the host itself: always ready."""
        return True

    def submit(self, spec: AttemptSpec) -> str:
        """Prepare the scratch dir; the handle IS the dir path."""
        d = self._dir(spec)
        d.mkdir(parents=True, exist_ok=True)
        d.joinpath("prepared").touch()  # a fresh dir IS the prepared env
        return str(d)

    def resolve(self, job: str) -> str | None:
        """Return the dir itself once it exists."""
        return job if Path(job).is_dir() else None

    def endpoint_ready(self, endpoint: str) -> bool:
        """Report readiness via the prepared marker."""
        return (Path(endpoint) / "prepared").exists()

    def launch(self, endpoint: str, spec: AttemptSpec, attempt: int) -> int:
        """Spawn the runner detached (cwd = endpoint); return t0."""
        proc = subprocess.Popen(
            self.runner_cmd,
            cwd=endpoint,
            stdout=open(Path(endpoint) / "attempt.log", "w"),  # noqa: SIM115
            start_new_session=True,
        )
        self.procs[endpoint] = proc
        return int(time.time())

    def sample(self, endpoint: str, spec: AttemptSpec) -> SampleOrMiss:
        """Read the session artifacts; liveness from the spawned process.

        Unreachable only when the endpoint dir is GONE (local dirs are
        always readable — there is no ssh here to fail). Without a
        session yet, a still-running runner reads as a live empty
        sample, exactly as a booting pi would over the wire.
        """
        d = Path(endpoint)
        if not d.is_dir():
            return SandboxUnreachable()
        proc = self.procs.get(endpoint)
        alive = 0 if proc is None or proc.poll() is not None else 1
        session = d / "session.jsonl"
        stream = d / "stream.jsonl"
        calls, tokens = 0, 0
        if session.is_file():
            for line in session.read_text().splitlines():
                if line.strip():
                    calls += 1
                    tokens += len(line)
        age = int(time.time() - stream.stat().st_mtime) if stream.is_file() else 0
        # mirror sample-session.py's wire line, then parse it — one
        # interpretation path for the format
        return parse_sample(f"{calls} 0 0 0 {tokens} 0.0 {age} {alive}")

    def completion_marker(self, endpoint: str, spec: AttemptSpec) -> str:
        """Read the runner's status file (empty while absent)."""
        f = Path(endpoint) / "runner-status"
        return f.read_text().strip() if f.is_file() else ""

    def terminate(self, endpoint: str, spec: AttemptSpec) -> None:
        """TERM the spawned runner process group."""
        proc = self.procs.get(endpoint)
        if proc is not None:
            proc.send_signal(signal.SIGTERM)
            self.procs.pop(endpoint, None)

    def harvest(self, endpoint: str, job: str, spec: AttemptSpec, force: bool) -> Path:
        """Copy the bundle artifacts out; the lease is untouched.

        job is meaningless locally (the endpoint IS the lease) but the
        signature stays protocol-shaped so drivers can't special-case.
        """
        dest = self.root / "recovered" / f"pair-{spec.run_id}" / spec.arm
        dest.mkdir(parents=True, exist_ok=True)
        for name in (
            "session.jsonl",
            "stream.jsonl",
            "capture.json",
            "runner-status",
            "graded.jsonl",
        ):
            src = Path(endpoint) / name
            if src.is_file():
                shutil.copy2(src, dest / name)
        return dest

    def release(self, job: str) -> None:
        """Remove the scratch dir (the local lease teardown)."""
        shutil.rmtree(job, ignore_errors=True)
