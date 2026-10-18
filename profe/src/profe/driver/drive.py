"""The generic drive loop (§5): monitor, one-attempt flow, adjudication.

Mechanism-only: caps/patience come from CapPolicy (values live in the
campaign manifest), patterns are arguments (§4.6 stays campaign-side,
A8), and every campaign-flavored decision — sheet rows, dispositions
beyond the terminal class, comparisons — stays with the campaign loop.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from profe.bundle import BundleFormatError, read_bundle
from profe.driver.models import (
    AttemptState,
    CapPolicy,
    Sample,
    SlotKey,
    parse_sample,
)
from profe.driver.sandbox import (
    AttemptSandbox,
    AttemptSpec,
    ClearsStaleEndpoints,
    SnapshotsEnvironment,
    endpoint_label_of,
)
from profe.driver.tracker import AttemptTracker


@dataclass
class KillFlags:
    """Why/how a monitor ended (today's flags dict, typed)."""

    sshfail: int = 0
    hit_cap: bool = False
    wall: bool = False
    infra: bool = False
    killed: bool = False
    done: bool = False
    notes: list[str] = field(default_factory=list)


def _say_default(_: str) -> None:
    pass


def scan_text(text: str, patterns: Sequence[str]) -> list[str]:
    """Report which calibration patterns appear in the given text.

    §4.6 rule 3's mechanism; the PATTERNS are the caller's — this
    ships the mechanism, never the campaign's list.
    """
    return [p for p in patterns if p in text]


@dataclass(frozen=True)
class Adjudication:
    """One terminal-class decision over a recovered bundle."""

    usable: bool  # False: not a usable attempt (unreadable bundle)
    state: str | None  # accepted | kept-itt-failed | None when unusable
    termination: str  # completed | incomplete | cap | wall
    attempt: int
    sidecar: str  # the bundle's own manifest label ("" = absent/pre-v3)
    contamination: list[str]
    reasons: list[str]


def classify_termination(rec) -> str:
    """Map one bundle record onto the §4.6 termination class.

    completed | incomplete | cap | wall — from bundle evidence only:
    runner-status and capture.early_exit. Cap/wall kills carry a
    runner-status of done? No: the driver's kill leaves runner-status
    absent/incomplete and early_exit records the profe-side reason; the
    runner itself never marks a kill 'done'. (Ported verbatim from the
    campaign's lib/bundle.py — the mechanism moves into profe; the
    record it reads was already profe's.)
    """
    if rec.runner_status and rec.runner_status != "done":
        return "incomplete"
    ee = rec.early_exit
    if ee:
        reason = ee.get("reason", "") if isinstance(ee, dict) else str(ee)
        if "budget" in reason or "cap" in reason:
            return "cap"
        if "wall" in reason or "time" in reason:
            return "wall"
        return "incomplete"
    if rec.calls is None:
        return "incomplete"
    return "completed"


def adjudicate(
    bundle_path: Path, contamination_patterns: Sequence[str]
) -> Adjudication:
    """Make one terminal-class decision over a bundle.

    profe.bundle read (BundleFormatError -> not a usable attempt),
    classify_termination -> accepted / kept-itt-failed, pattern-in-text
    over the session for §4.6.
    """
    try:
        rec = read_bundle(bundle_path)
    except BundleFormatError as exc:
        return Adjudication(
            usable=False,
            state=None,
            termination="",
            attempt=0,
            sidecar="",
            contamination=[],
            reasons=[str(exc)],
        )
    term = classify_termination(rec)
    state = "accepted" if term == "completed" else "kept-itt-failed"
    session = Path(bundle_path) / "session.jsonl"
    text = session.read_text(errors="replace") if session.is_file() else ""
    return Adjudication(
        usable=True,
        state=state,
        termination=term,
        attempt=rec.attempt or 1,
        sidecar=rec_sidecar_manifest(bundle_path),
        contamination=scan_text(text, contamination_patterns),
        reasons=[],
    )


def rec_sidecar_manifest(bundle_path: Path) -> str:
    """Return the sidecar's manifest label.

    Empty string when absent / pre-v3 attempt.
    """
    try:
        capture = json.loads((Path(bundle_path) / "capture.json").read_text())
        meta = capture.get("extra_metadata") or {}
        manifest = meta.get("manifest", "")
        # capture.json is free-form evidence: a non-str label is absent.
        return manifest if isinstance(manifest, str) else ""
    except Exception:  # noqa: BLE001 (provenance is best-effort)
        return ""


def monitor_attempt(  # noqa: PLR0912, PLR0913, PLR0917 (the §4.4 envelope, one call)
    tracker: AttemptTracker,
    sandbox: AttemptSandbox,
    key: SlotKey,
    endpoint: str,
    spec: AttemptSpec,
    t0: int,
    caps: CapPolicy,
    say: Callable[[str], None] = _say_default,
) -> KillFlags:
    """Run the §4.4 monitor loop, mechanism-only.

    Poll sample(); an unreachable/empty read counts the sshfail streak
    (streak >= sshfail_max -> infra); cap-hit -> terminate + hit_cap
    (keep-ITT); stream-age > zact_s -> terminate + infra; elapsed >
    wall_s -> terminate + wall (keep-ITT); monitor_bound giveup ->
    terminate. The completion-marker gate: done is ONLY the runtime's
    own marker, never alive==0.
    """
    flags = KillFlags()
    while True:
        nows = int(time.time())
        raw = sandbox.sample(endpoint, spec)
        miss = (
            raw is None
            or getattr(raw, "unreachable", False)
            or (isinstance(raw, str) and not raw.strip())
        )
        if miss:
            flags.sshfail += 1
            say(f"   [{key.slot}] ssh fail {flags.sshfail}/{caps.sshfail_max}")
            if flags.sshfail >= caps.sshfail_max:
                flags.infra = True
                tracker.note(
                    key,
                    "infra-note",
                    detail=f"ssh unreachable x{flags.sshfail}",
                )
                return flags
        else:
            flags.sshfail = 0
            sample = raw if isinstance(raw, Sample) else parse_sample(str(raw))
            say(
                f"   [{key.slot}] calls={sample.calls} tok={sample.tokens} "
                f"${sample.cost_usd:.4f} age={sample.stream_age_s}s "
                f"alive={int(sample.alive)}"
            )
            if sample.alive:
                if (
                    sample.cost_usd >= caps.cost_usd
                    or sample.tokens >= caps.total_tokens
                    or sample.calls >= caps.calls
                ):
                    sandbox.terminate(endpoint, spec)
                    flags.hit_cap = True
                    flags.killed = True
                    tracker.note(
                        key,
                        "cap-kill",
                        detail=f"cost={sample.cost_usd} tokens={sample.tokens} "
                        f"calls={sample.calls}",
                    )
                elif (
                    sample.stream_age_s is not None
                    and sample.stream_age_s > caps.zact_s
                ):
                    sandbox.terminate(endpoint, spec)
                    flags.infra = True
                    flags.killed = True
                    tracker.note(
                        key,
                        "zact-kill",
                        detail=f"stream_age={sample.stream_age_s}s (infra: hang)",
                    )
                elif nows - t0 > caps.wall_s:
                    sandbox.terminate(endpoint, spec)
                    flags.wall = True
                    flags.killed = True
                    tracker.note(
                        key,
                        "wall-kill",
                        detail=f"elapsed={nows - t0}s (kept ITT)",
                    )
            else:
                # the runner writes its terminal status only when
                # finished — during startup the file is absent, so
                # "alive=0" alone must NOT mean done (c2 mark_done gate)
                status = sandbox.completion_marker(endpoint, spec)
                if status:
                    flags.done = True
                    tracker.note(key, "runner-done", detail=status[:120])
                else:
                    say(f"   [{key.slot}] alive=0, runner-status pending")
        if flags.done or flags.infra:
            return flags
        if nows - t0 > caps.monitor_bound_s:
            tracker.note(key, "giveup", detail="monitor bound exceeded")
            sandbox.terminate(endpoint, spec)
            flags.killed = True
            return flags
        time.sleep(caps.poll_s)


def _force_flags() -> KillFlags:
    return KillFlags(killed=True)


def drive_attempt(  # noqa: PLR0911, PLR0912, PLR0913, PLR0915, PLR0917
    # (the merged fresh+reattach flow, rehearsal-validated; ported
    # verbatim, not restructured)
    tracker: AttemptTracker,
    sandbox: AttemptSandbox,
    key: SlotKey,
    spec: AttemptSpec,
    caps: CapPolicy,
    contamination_patterns: Sequence[str] = (),
    say: Callable[[str], None] = _say_default,
) -> str:
    """Advance one slot to a terminal state.

    Fresh and reattach are ONE flow: record_of(key) says what already
    happened; the loop only performs the next unobserved effect.
    Resume priority: bundle evidence (recovered, readable) outranks the
    log outranks a fresh plan.
    """
    rec = tracker.record_of(key)
    arm = spec.arm

    # -- resume priority 1: bundle evidence ----------------------------
    prior_getter = getattr(sandbox, "prior_bundles", None)
    if prior_getter and not tracker.is_terminal(key):
        for b in prior_getter(spec):
            adj = adjudicate(b, contamination_patterns)
            if adj.usable and adj.state:
                tracker.record(
                    key,
                    AttemptState(adj.state),
                    arm=arm,
                    bundle=str(b),
                    attempt=adj.attempt,
                )
                say(f"   [{key.slot}] resume: ({arm}) -> {adj.state}")
                return adj.state
            say(f"   [{key.slot}] resume: bundle invalid: {'; '.join(adj.reasons)}")

    # -- resume priority 2: a live job from the log ---------------------
    reattach = bool(rec.job) and rec.state in (
        AttemptState.SUBMITTED,
        AttemptState.ENDPOINT_READY,
        AttemptState.RUNNING,
    )
    if reattach:
        job = rec.job or ""
        attempt = rec.attempt or 1
        if rec.state == AttemptState.SUBMITTED and getattr(
            sandbox, "reprovisions_on_resubmit", False
        ):
            rec.endpoint = rec.t0 = None  # a stale endpoint cannot survive
        say(
            f"   [{key.slot}] reattach job {job} (state={rec.state}, attempt {attempt})"
        )
    else:
        # -- fresh attempt ------------------------------------------------
        if not sandbox.lease_ready(spec):
            tracker.note(key, "infra-note", detail="lease not ready — deferring")
            say(f"   [{key.slot}] capacity gate closed — deferring")
            return "deferred"
        if rec.job:
            sandbox.release(rec.job)  # infra retry: release old lease
        try:
            job = sandbox.submit(spec)
        except Exception as exc:  # noqa: BLE001 (submit failure = infra, rig parity)
            tracker.record(
                key,
                AttemptState.INFRA_FAILED,
                arm=arm,
                attempt=rec.attempt + 1,
                detail=str(exc),
            )
            return "infra-failed"
        rec.endpoint = rec.t0 = None
        attempt = rec.attempt + 1
        tracker.note(key, "submit", job=job, queue=spec.queue, arm=arm, attempt=attempt)
        tracker.record(key, AttemptState.SUBMITTED, job=job, arm=arm, attempt=attempt)
        say(f"   [{key.slot}] submitted {arm}: {job} (attempt {attempt})")

    # -- endpoint ------------------------------------------------------
    # optional lab-side fast-fail capability (manifest v4, 2026-10-18):
    # a terminally failed job (failed MAAS deploy etc.) must not burn
    # the ip_wait/rig_wait windows — the signal rides the results
    # payload the driver already polls. Absent capability = old shape.
    fail_getter = getattr(sandbox, "job_failed", None)
    endpoint = rec.endpoint
    if not endpoint:
        deadline = time.time() + caps.ip_wait_s
        failed_fast = False
        while time.time() < deadline:
            endpoint = sandbox.resolve(job)
            if endpoint:
                tracker.note(key, "ip", ip=endpoint, job=job)  # legacy wire name
                break
            if fail_getter and fail_getter(job):
                failed_fast = True
                break
            time.sleep(caps.endpoint_poll_s)
        if endpoint and isinstance(sandbox, ClearsStaleEndpoints):
            # fresh resolve only, rig parity: keygen-r fires when the ip
            # is new (a reattach's recorded key is already trusted)
            sandbox.clear_stale_endpoint(endpoint)
        if not endpoint:
            sandbox.release(job)
            tracker.record(
                key,
                AttemptState.INFRA_FAILED,
                job=job,
                arm=arm,
                attempt=attempt,
                detail=(
                    "job terminally failed (fast-check)"
                    if failed_fast
                    else "no endpoint in lease window"
                ),
            )
            return "infra-failed"
    assert endpoint is not None

    # -- prepared environment ------------------------------------------
    if not rec.t0:
        deadline = time.time() + caps.rig_wait_s
        ready = False
        failed_fast = False
        while time.time() < deadline:
            if sandbox.endpoint_ready(endpoint):
                tracker.note(key, "rig-ready", ip=endpoint)  # legacy wire name
                ready = True
                break
            if fail_getter and fail_getter(job):
                failed_fast = True
                break
            time.sleep(caps.ready_poll_s)
        if not ready:
            sandbox.release(job)
            tracker.record(
                key,
                AttemptState.INFRA_FAILED,
                job=job,
                endpoint=endpoint,
                arm=arm,
                attempt=attempt,
                detail=(
                    "job terminally failed (fast-check)"
                    if failed_fast
                    else "endpoint not ready in window"
                ),
            )
            return "infra-failed"
        machine = endpoint_label_of(sandbox, endpoint)
        tracker.record(
            key,
            AttemptState.ENDPOINT_READY,
            job=job,
            endpoint=endpoint,
            arm=arm,
            attempt=attempt,
            machine=machine,
        )
        try:
            t0 = sandbox.launch(endpoint, spec, attempt)
        except Exception as exc:  # noqa: BLE001 (launch failure = infra)
            sandbox.release(job)
            tracker.record(
                key,
                AttemptState.INFRA_FAILED,
                job=job,
                endpoint=endpoint,
                arm=arm,
                attempt=attempt,
                detail=f"launch failed: {exc}",
            )
            return "infra-failed"
        tracker.record(
            key,
            AttemptState.RUNNING,
            job=job,
            endpoint=endpoint,
            arm=arm,
            attempt=attempt,
            t0=t0,
        )
        flags = monitor_attempt(tracker, sandbox, key, endpoint, spec, t0, caps, say)
    else:
        # mid-attempt reattach: the events carry the live job/endpoint/t0
        t0 = rec.t0 or 0
        if sandbox.endpoint_ready(endpoint):
            say(f"   [{key.slot}] reattached mid-attempt (t0={t0})")
            flags = monitor_attempt(
                tracker, sandbox, key, endpoint, spec, t0, caps, say
            )
        else:
            try:
                rec_dir = sandbox.harvest(endpoint, job, spec, force=True)
            except Exception:
                tracker.note(key, "infra-note", detail="endpoint gone on reattach")
                tracker.record(
                    key,
                    AttemptState.INFRA_FAILED,
                    job=job,
                    endpoint=endpoint,
                    arm=arm,
                    attempt=attempt,
                    detail="endpoint gone on reattach",
                )
                return "infra-failed"
            return _settle(
                tracker,
                sandbox,
                key,
                spec,
                rec_dir,
                _force_flags(),
                attempt,
                job,
                arm,
                contamination_patterns,
                say,
            )

    # -- post-monitor: snapshot, harvest, settle, release ---------------
    if isinstance(sandbox, SnapshotsEnvironment):
        sandbox.snapshot_environment(spec, endpoint)  # §9 ground truth
    rec_dir = sandbox.harvest(endpoint, job, spec, force=flags.killed)
    return _settle(
        tracker,
        sandbox,
        key,
        spec,
        rec_dir,
        flags,
        attempt,
        job,
        arm,
        contamination_patterns,
        say,
    )


def _settle(  # noqa: PLR0913, PLR0917 (the whole attempt context travels)
    tracker: AttemptTracker,
    sandbox: AttemptSandbox,
    key: SlotKey,
    spec: AttemptSpec,
    rec_dir: Path,
    flags: KillFlags,
    attempt: int,
    job: str,
    arm: str,
    contamination_patterns: Sequence[str],
    say: Callable[[str], None],
) -> str:
    """Settle a harvested bundle: adjudicate, record, release.

    The terminal state is recorded and the lease released in EVERY
    path (harvest never releases — the split that makes the
    premature-done bug unrepresentable).
    """
    adj = adjudicate(rec_dir, contamination_patterns)
    try:
        if adj.sidecar and adj.sidecar != spec.manifest_sha8:
            tracker.note(
                key,
                "manifest-mismatch",
                sidecar=adj.sidecar,
                driver=spec.manifest_sha8,
            )
            raise SystemExit(
                f"PREFLIGHT REFUSAL (exit 2): {key.pair}/{key.slot} sidecar "
                f"manifest {adj.sidecar} != driver manifest "
                f"{spec.manifest_sha8} — the attempt ran under a different "
                "pre-registration (B2)."
            )
        if not adj.sidecar:
            tracker.note(
                key,
                "note",
                detail="sidecar carries no manifest label (pre-v3 attempt)",
            )
        if adj.contamination:
            tracker.note(
                key,
                "contamination-suspect",
                patterns=adj.contamination,
                bundle=str(rec_dir),
            )
            tracker.record(
                key,
                AttemptState.REJECTED_CONTAMINATION,
                job=job,
                arm=arm,
                attempt=attempt,
                bundle=str(rec_dir),
            )
            raise SystemExit(
                f"OPERATOR STOP (exit 1): {key.pair}/{key.slot} contamination "
                f"suspects {adj.contamination} — §4.6 rule 3: any occurrence "
                "is a rig integrity failure; pause and audit."
            )
        if adj.usable and adj.state:
            detail = (
                f"terminal={adj.state} attempt={attempt} "
                f"hit_cap={int(flags.hit_cap)} wall={int(flags.wall)} "
                f"infra={int(flags.infra)}"
            )
            tracker.note(
                key,
                "done",
                state=adj.state,
                job=job,
                arm=arm,
                attempt=attempt,
                bundle=str(rec_dir),
                hit_cap=int(flags.hit_cap),
                wall=int(flags.wall),
                infra=int(flags.infra),
                detail=detail,
            )
            tracker.record(
                key,
                AttemptState(adj.state),
                job=job,
                arm=arm,
                attempt=attempt,
                bundle=str(rec_dir),
            )
            return adj.state
        tracker.note(key, "note", detail=f"bundle invalid: {'; '.join(adj.reasons)}")
        tracker.record(
            key,
            AttemptState.INFRA_FAILED,
            job=job,
            arm=arm,
            attempt=attempt,
            bundle=str(rec_dir),
            detail="invalid bundle after attempt",
        )
        return "infra-failed"
    finally:
        sandbox.release(job)
