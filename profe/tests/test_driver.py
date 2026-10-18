"""profe.driver tests — the machine's spec, ported from the rig suite.

The rig suite (eval/phase2/campaign3/rig/test-driver-v3.py) pins the
campaign driver's state machine; the proposal's §6 table maps those
semantics onto profe.driver. Ported here: the tracker fold, the ledger,
the resume gates, the monitor's completion-marker gate, the settle
classification + provenance checks, the manifest-stamp store rule. The
two tests newly possible with the split (harvest never releases; a full
LocalOneShotSandbox end-to-end) close the file.

Campaign-side tests (manifest preflight refusal, skills hash, coin
EPOCH application, sheet wiring) stay in rig/test-driver-v3.py — A8:
campaign judgment stays campaign-side. B2's manifest-hash preflight is
pinned THERE; only the store's manifest-stamp rule is profe's.

Fixtures: ListEventStore (in-memory) for the tracker; a FakeSandbox
that deliberately does NOT import profe (the typing.Protocol payoff:
backends need not import profe to conform — it returns wire strings
and a duck sentinel).
"""

import json
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from profe.driver import (
    TERMINAL,
    Adjudication,
    AttemptEvent,
    AttemptSpec,
    AttemptState,
    AttemptTracker,
    CapPolicy,
    JsonlEventStore,
    LedgerPolicy,
    ListEventStore,
    LocalOneShotSandbox,
    Sample,
    SlotKey,
    adjudicate,
    classify_termination,
    coin_drift,
    drive_attempt,
    fair_coin,
    monitor_attempt,
    scan_text,
)
from profe.driver.store import _parse_line

SHA = "b35f5c36"  # manifest-c3.yaml v2 sha8 (the rehearsal stamp)
QUEUE = "__TWIN_QUEUE__"  # pool mode: one shared queue


# ------------------------------------------------------------ fixtures


def _assistant(usage=None, tool_calls=()):
    content = [{"type": "toolCall", "arguments": a} for a in tool_calls]
    m = {"role": "assistant", "content": content}
    if usage is not None:
        m["usage"] = usage
    return {"message": m}


def make_bundle(  # noqa: PLR0913 (fixture knobs, kwargs-only)
    base,
    pair,
    arm,
    *,
    runner_status="done",  # noqa: PLR0913
    early_exit=None,
    manifest=None,
    session_text=None,
    graded=True,
    attempt="1",
    machine=None,
):
    """One minimal valid attempt bundle (the rehearsal shape, rig-parity)."""
    b = base / "recovered-20261018T000000Z" / "runs" / f"pair-{pair}" / arm
    b.mkdir(parents=True, exist_ok=True)
    md = {"campaign": "c3", "pair": str(pair), "arm": arm, "attempt": attempt}
    if manifest:
        md["manifest"] = manifest
    if machine:
        md["machine"] = machine
    capture = {
        "task_id": "smoke-rig-tour",
        "run": pair,
        "dur_seconds": 100.0,
        "pi_exit_code": 0,
        "skills": [],
        "early_exit": early_exit,
        "extra_metadata": md,
    }
    (b / "capture.json").write_text(json.dumps(capture))
    if session_text is None:
        rows = [
            _assistant(
                usage={"input": 10, "output": 2, "cacheRead": 5, "totalTokens": 17},
                tool_calls=[{"path": "/x/skills/juju-cli/SKILL.md"}],
            )
        ]
        session_text = "".join(json.dumps(r) + "\n" for r in rows)
    (b / "session.jsonl").write_text(session_text)
    if graded:
        g = {
            "schema_ok": True,
            "verdict": {"verdict": "charm-bug", "root_cause": "rev916"},
        }
        (b / "graded.jsonl").write_text(json.dumps(g) + "\n")
    if runner_status is not None:
        (b / "runner-status").write_text(runner_status)
    return b


CAPS = CapPolicy(
    cost_usd=0.50,
    total_tokens=200_000,
    calls=60,
    wall_s=3600,
    zact_s=1200,
    sshfail_max=5,
    monitor_bound_s=1,  # tests: the giveup fires fast
    poll_s=0,  # tests: no sleeping between polls
    ip_wait_s=2,  # open (short) windows: fakes resolve immediately
    rig_wait_s=2,
    endpoint_poll_s=0,
    ready_poll_s=0,
)
CAPS_CLOSED = replace(CAPS, ip_wait_s=0, rig_wait_s=0)  # windows never open
CAPS_IP_ONLY = replace(CAPS, rig_wait_s=0)  # rig-wait window never opens
CAPS_E2E = replace(CAPS, monitor_bound_s=30)  # the local runner needs headroom


def tracker(store=None, max_infra=3):
    """A tracker over a ListEventStore with the default ledger policy."""
    return AttemptTracker.replay(store or ListEventStore(), LedgerPolicy(max_infra))


def spec(pair, arm, **kw):
    """The campaign's fill of AttemptSpec for a fake attempt."""
    kw.setdefault("payload_ref", "payload-dir/sha")
    kw.setdefault("queue", QUEUE)
    return AttemptSpec(run_id=pair, arm=arm, manifest_sha8=SHA, **kw)


class FakeSandbox:
    """Scriptable sandbox: conforms WITHOUT importing profe.

    Returns wire strings and a duck sentinel (SimpleNamespace with
    `unreachable=True`) — exactly what a non-importing backend may do.
    The drive loop must accept both (structural conformance, no ABC).

    prior=True models a YieldsPriorBundles backend (method shape, like
    the testflinger adapter); prior=False removes the attribute so the
    drive loop takes the fresh/reattach/settle path (the resume gate
    must be exercised BOTH ways).
    """

    def __init__(  # noqa: PLR0913 (fixture knobs)
        self,
        samples=(),
        statuses=(),
        *,
        ready=True,
        ready_endpoint=True,
        endpoint="10.0.0.1",
        prior=True,
        fails_fast=None,
    ):
        self.samples = list(samples)
        self.statuses = list(statuses)
        self.endpoint = endpoint
        self.ready = ready
        self.ready_endpoint = ready_endpoint
        self.calls: list[tuple[str | int | bool, ...]] = []
        self.releases: list[str] = []
        self.bundle_dir = None
        self.reprovisions_on_resubmit = False  # capability data
        # job_failed: None = capability absent; True/False = the lab's
        # terminal-failure answer (the drive loop reads it via getattr,
        # like prior_bundles — declared here so ty sees the union).
        self.job_failed = (
            (lambda job: bool(fails_fast)) if fails_fast is not None else None
        )
        # prior_bundles is a plain instance attribute: a bound method
        # when the capability exists, None when it doesn't (getattr
        # finds None -> falsy -> resume skipped — the same treatment
        # the drive loop gives an absent capability). Not a class
        # method shadowed with None: the Protocol member's declared
        # type and a None instance attr are incompatible under ty.
        self.prior_bundles = self._prior_bundles if prior else None

    def _prior_bundles(self, spec):
        return [self.bundle_dir] if self.bundle_dir else []

    def lease_ready(self, spec):
        return self.ready

    def submit(self, spec):
        self.calls.append(("submit", spec.queue, spec.arm))
        return f"job-{spec.run_id}-{spec.arm}"

    def resolve(self, job):
        self.calls.append(("resolve", job))
        return self.endpoint

    def endpoint_ready(self, endpoint):
        self.calls.append(("ready", endpoint))
        return self.ready_endpoint

    def launch(self, endpoint, spec, attempt):
        self.calls.append(("launch", endpoint, spec.arm, attempt))
        return int(time.time())

    def sample(self, endpoint, spec):
        self.calls.append(("sample", endpoint))
        return self.samples.pop(0) if self.samples else "0 0 0 0 0 0.0000 -1 0"

    def completion_marker(self, endpoint, spec):
        self.calls.append(("status", endpoint))
        return self.statuses.pop(0) if self.statuses else ""

    def terminate(self, endpoint, spec):
        self.calls.append(("kill", endpoint, spec.arm))

    def harvest(self, endpoint, job, spec, force):
        self.calls.append(("harvest", endpoint, job, spec.arm, force))
        return self.bundle_dir

    def release(self, job):
        self.calls.append(("release", job))
        self.releases.append(job)


def settled_sandbox(tmp_path, pair, arm, *, prior=False, **bundle_kw):
    """A FakeSandbox whose harvest/settle path returns a real bundle.

    prior=False: the bundle is reached through harvest (the live settle
    path), not the resume gate — pass prior=True for resume tests.
    """
    sb = FakeSandbox(prior=prior)
    sb.bundle_dir = make_bundle(tmp_path, pair, arm, **bundle_kw)
    return sb


def drive(trk, sb, pair, arm, caps=CAPS, patterns=()):  # noqa: PLR0913, PLR0917
    """drive_attempt on slot A of `pair` (the tests' single-slot shape)."""
    return drive_attempt(
        trk,
        sb,
        SlotKey(pair, "A"),
        spec(pair, arm),
        caps,
        contamination_patterns=patterns,
    )


# ------------------------------------------------------------ P7 coin


def test_fair_coin_matches_rehearsal_draws():
    """P7 pin: pairs 2/3 (seeds 20261032/33) drew A=off — the draw is
    stable across processes and equals the rehearsal record."""
    assert fair_coin(20261030 + 2) is False
    assert fair_coin(20261030 + 3) is False


def test_coin_replay_fans_out_and_never_redraws():
    """The coin event belongs to BOTH slots; refolding the same stream
    yields the same coin (idempotent fold, no re-emission).

    The full arms dict (queues included) is stored — rig-parity: a
    drift check compares the whole draw.
    """
    arms = {"A": {"queue": "q1", "arm": "on"}, "B": {"queue": "q2", "arm": "off"}}
    store = ListEventStore()
    trk = tracker(store)
    trk.note(None, "coin", seed=20261036, arms=arms)
    trk2 = AttemptTracker.replay(store, trk.policy)
    a = trk2.record_of(SlotKey(0, "A"))
    b = trk2.record_of(SlotKey(0, "B"))
    assert a.coin == arms and b.coin == arms


def test_coin_drift_is_detected():
    """Recorded arms disagreeing with the seed's draw = drift (True)."""
    on_a = fair_coin(20261035)
    good = {
        "A": {"queue": "q", "arm": "on" if on_a else "off"},
        "B": {"queue": "q", "arm": "off" if on_a else "on"},
    }
    flipped = {
        k: {**v, "arm": ("off" if v["arm"] == "on" else "on")} for k, v in good.items()
    }
    assert coin_drift(good, 20261035) is False
    assert coin_drift(flipped, 20261035) is True


def test_coin_drift_ignores_queues():
    """Queue names may legitimately differ across manifests (the v2 pool
    move kept the arms): only ARM assignments are compared."""
    on_a = fair_coin(20261035)
    arms = {
        "A": {"queue": "old", "arm": "on" if on_a else "off"},
        "B": {"queue": "old", "arm": "off" if on_a else "on"},
    }
    moved = {k: {**v, "queue": QUEUE} for k, v in arms.items()}
    assert coin_drift(moved, 20261035) is False


# ------------------------------------------------------------ B9 ledger


def _infra_events(store, pair, n, arm="off"):
    trk = AttemptTracker(store, LedgerPolicy(max_infra_per_slot=3))
    for i in range(1, n + 1):
        trk.record(
            SlotKey(pair, "A"),
            AttemptState.INFRA_FAILED,
            job=f"j{i}",
            arm=arm,
            attempt=i,
        )
    return trk


def test_ledger_infra_fails_consume_no_slot():
    """B9: an infra failure counts attempts but consumes no slot."""
    trk = _infra_events(ListEventStore(), 4, 1)
    rec = trk.record_of(SlotKey(4, "A"))
    assert rec.n_infra == 1 and rec.attempt == 1
    assert rec.state == AttemptState.INFRA_FAILED  # not terminal -> retried


def test_ledger_cap_stops_for_operator():
    """B9: three infra failures = the §4.3 cap; the slot is exhausted."""
    trk = _infra_events(ListEventStore(), 4, 3)
    assert trk.infra_exhausted(SlotKey(4, "A")) is True
    assert trk.infra_exhausted(SlotKey(4, "B")) is False  # per-slot ledger


def test_ledger_agent_failure_consumes_the_slot():
    """B9: a kept-itt failure is terminal — the slot is consumed (ITT
    keep), no retry — the opposite of the infra path."""
    trk = tracker()
    trk.record(
        SlotKey(4, "A"),
        AttemptState.KEPT_ITT_FAILED,
        job="j1",
        arm="off",
        attempt=1,
    )
    assert trk.is_terminal(SlotKey(4, "A")) is True
    assert trk.in_loop([SlotKey(4, "A")]) == []


# ------------------------------------------------------------ B1 resume


def test_resume_skips_accepted_bundle(tmp_path):
    """c2 bug 1: a valid bundle is terminal, never re-run.

    The drive loop's resume priority 1: prior bundle evidence outranks
    the log and any fresh plan; the sandbox must NOT be submitted to.
    """
    sb = settled_sandbox(tmp_path, 4, "off", prior=True)
    trk = tracker()
    out = drive(trk, sb, 4, "off")
    assert out == "accepted"
    rec = trk.record_of(SlotKey(4, "A"))
    assert rec.state == AttemptState.ACCEPTED
    assert rec.bundle is not None  # the union narrows: a settled slot has one
    assert rec.bundle.endswith("/off")
    assert not any(c[0] == "submit" for c in sb.calls)  # never re-run


def test_resume_ignores_sheet(tmp_path):
    """The sheet is a VIEW: profe's drive API has no sheet input at all
    (the c2 tail-1 identity bug is unrepresentable); an INCOMPLETE
    bundle is a keep (ITT), never a resubmit."""
    sb = settled_sandbox(
        tmp_path,
        4,
        "on",
        prior=True,
        runner_status="incomplete",
        early_exit={"reason": "budget cap"},
    )
    trk = tracker()
    out = drive(trk, sb, 4, "on")
    assert out == "kept-itt-failed"
    assert not any(c[0] == "submit" for c in sb.calls)


def test_invalid_bundle_does_not_terminalize(tmp_path):
    """A format-invalid bundle (no marker) stays in the plan: resume
    falls through to a fresh submit (deferred — capacity gate closed)."""
    sb = settled_sandbox(tmp_path, 4, "off", runner_status=None)
    sb.ready = False
    trk = tracker()
    out = drive(trk, sb, 4, "off")
    assert out == "deferred"
    assert trk.record_of(SlotKey(4, "A")).state == AttemptState.PENDING


def test_reattach_reconstructs_midrun_state(tmp_path):
    """B1: replay rebuilds running state (job/endpoint/t0/arm) and the
    drive loop REATTACHES to it (no fresh submit)."""
    store = ListEventStore()
    trk = AttemptTracker(store, LedgerPolicy())
    key = SlotKey(6, "A")
    trk.record(key, AttemptState.SUBMITTED, job="jB", arm="off", attempt=1)
    trk.record(
        key,
        AttemptState.RUNNING,
        job="jB",
        ip="10.0.0.9",
        arm="off",
        attempt=1,
        t0=int(time.time()) - 5,
    )
    trk2 = AttemptTracker.replay(store, trk.policy)
    rec = trk2.record_of(key)
    assert rec.state == AttemptState.RUNNING
    assert rec.arm == "off"
    assert rec.job == "jB" and rec.endpoint == "10.0.0.9" and rec.t0
    sb = FakeSandbox(samples=["0 0 0 0 0 0.0000 -1 0"], statuses=["done"], prior=False)
    sb.bundle_dir = make_bundle(tmp_path, 6, "off")
    out = drive(trk2, sb, 6, "off")
    assert out == "accepted"
    assert not any(c[0] == "submit" for c in sb.calls)  # reattached


def test_replay_reads_legacy_wire():
    """Byte-compatibility: legacy `rig-ready` maps to ENDPOINT_READY and
    `ip` folds into endpoint (the rehearsal record stays readable)."""
    store = ListEventStore()
    trk = AttemptTracker(store, LedgerPolicy())
    key = SlotKey(6, "A")
    trk.record(
        key,
        AttemptState.ENDPOINT_READY,
        job="jA",
        ip="10.0.0.1",
        arm="on",
        attempt=1,
    )
    trk2 = AttemptTracker.replay(store, trk.policy)
    rec = trk2.record_of(key)
    assert rec.state == AttemptState.ENDPOINT_READY
    assert rec.endpoint == "10.0.0.1"
    line = json.dumps(
        {
            "ts": "2026-10-17T00:00:00Z",
            "pair": 6,
            "event": "state",
            "slot": "A",
            "manifest": "legacy",
            "state": "rig-ready",
            "job": "jX",
            "ip": "10.0.0.2",
            "arm": "on",
            "attempt": 1,
        }
    )
    trk2.apply(_parse_line(line))
    rec = trk2.record_of(key)
    assert rec.state == AttemptState.ENDPOINT_READY and rec.endpoint == "10.0.0.2"


# ------------------------------------------------------------ classification


def test_classify_termination_mapping():
    """c2 bug 3: classify from bundle evidence, not heuristics.

    done -> completed; done + budget/cap early_exit -> cap; wall reason
    -> wall; unreadable calls -> incomplete. (accepted/kept-itt mapping
    is adjudicate's, tested next.)
    """
    assert (
        classify_termination(
            SimpleNamespace(runner_status="done", early_exit=None, calls=5)
        )
        == "completed"
    )
    assert (
        classify_termination(
            SimpleNamespace(
                runner_status="done", early_exit={"reason": "budget cap"}, calls=5
            )
        )
        == "cap"
    )
    assert (
        classify_termination(
            SimpleNamespace(
                runner_status="done", early_exit={"reason": "wall time"}, calls=5
            )
        )
        == "wall"
    )
    assert (
        classify_termination(
            SimpleNamespace(runner_status="incomplete", early_exit=None, calls=None)
        )
        == "incomplete"
    )


def test_adjudication_mapping(tmp_path):
    """accepted | kept-itt-failed over the same evidence."""
    cases = [("done", None, "accepted"), ("incomplete", None, "kept-itt-failed")]
    for rs, ee, want in cases:
        b = make_bundle(
            tmp_path / f"c-{rs}-{ee is not None}",
            7,
            "off",
            runner_status=rs,
            early_exit=ee,
        )
        adj = adjudicate(b, ())
        assert isinstance(adj, Adjudication) and adj.usable
        assert adj.state == want, (rs, ee)


def test_adjudicate_invalid_bundle(tmp_path):
    """BundleFormatError -> not a usable attempt (no state, reasons set)."""
    b = make_bundle(tmp_path, 8, "on", runner_status=None)
    adj = adjudicate(b, ())
    assert adj.usable is False
    assert adj.state is None
    assert adj.reasons and isinstance(adj.reasons[0], str)


# ------------------------------------------------------------ monitor gate


def test_monitor_done_requires_runner_status():
    """c2 gate: alive=0 with no runner-status is NOT done; the marker's
    appearance IS. No kill on a clean done."""
    sb = FakeSandbox(
        samples=["0 0 0 0 0 0.0000 -1 0"] * 3,
        statuses=["", "", "done"],
    )
    trk = tracker()
    flags = monitor_attempt(
        trk, sb, SlotKey(4, "A"), "10.0.0.1", spec(4, "on"), int(time.time()), CAPS
    )
    assert flags.done is True
    assert not any(c[0] == "kill" for c in sb.calls)


def test_monitor_boot_polls_not_done():
    """alive=0 + empty marker must poll, never done; the giveup bound
    fires the kill (no infra)."""
    sb = FakeSandbox(samples=["0 0 0 0 0 0.0000 -1 0"], statuses=[""])
    trk = tracker()
    t0 = int(time.time()) - CAPS.monitor_bound_s - 10
    flags = monitor_attempt(
        trk, sb, SlotKey(4, "A"), "10.0.0.1", spec(4, "on"), t0, CAPS
    )
    assert flags.done is False
    assert flags.infra is False
    assert flags.killed is True
    assert sum(1 for c in sb.calls if c[0] == "kill") == 1


def test_monitor_cap_kill_keeps_itt():
    """§4.4: cap-hit -> terminate + hit_cap (keep-ITT), NOT infra."""
    sb = FakeSandbox(samples=["3 0 0 0 300000 0.60 5 1"], statuses=[""])
    trk = tracker()
    t0 = int(time.time()) - CAPS.monitor_bound_s - 10  # giveup ends the loop
    flags = monitor_attempt(
        trk, sb, SlotKey(4, "A"), "10.0.0.1", spec(4, "on"), t0, CAPS
    )
    assert flags.hit_cap is True
    assert flags.killed is True
    assert flags.infra is False
    assert any(c[0] == "kill" for c in sb.calls)


def test_monitor_zact_kill_is_infra():
    """stream-age > zact_s -> terminate + infra (a hang, not a cap)."""
    sb = FakeSandbox(samples=["3 0 0 0 100 0.01 9999 1"], statuses=[""])
    trk = tracker()
    flags = monitor_attempt(
        trk, sb, SlotKey(4, "A"), "10.0.0.1", spec(4, "on"), int(time.time()), CAPS
    )
    assert flags.infra is True
    assert flags.killed is True
    assert any(c[0] == "kill" for c in sb.calls)


def test_monitor_wall_kill_keeps_itt():
    """elapsed > wall_s while alive -> terminate + wall (keep-ITT)."""
    sb = FakeSandbox(samples=["1 0 0 0 10 0.001 5 1"], statuses=[""])
    trk = tracker()
    t0 = int(time.time()) - CAPS.wall_s - 10
    flags = monitor_attempt(
        trk, sb, SlotKey(4, "A"), "10.0.0.1", spec(4, "on"), t0, CAPS
    )
    assert flags.wall is True
    assert flags.killed is True
    assert flags.infra is False


def test_monitor_sshfail_streak_is_infra():
    """Unreachable streak >= sshfail_max -> infra, without a kill (the
    machine is already gone)."""
    sb = FakeSandbox(samples=[SimpleNamespace(unreachable=True)] * CAPS.sshfail_max)
    trk = tracker()
    flags = monitor_attempt(
        trk, sb, SlotKey(4, "A"), "10.0.0.1", spec(4, "on"), int(time.time()), CAPS
    )
    assert flags.infra is True
    assert flags.sshfail == CAPS.sshfail_max
    assert not any(c[0] == "kill" for c in sb.calls)


def test_monitor_accepts_wire_strings_and_duck_sentinel():
    """The duck contract: wire strings poll like Samples; a namespace
    with unreachable=True counts a miss (no profe import needed)."""
    sb = FakeSandbox(
        samples=[
            SimpleNamespace(unreachable=True),
            "0 0 0 0 0 0.0000 -1 0",
            "0 0 0 0 0 0.0000 -1 0",
        ],
        statuses=["", "done"],
    )
    trk = tracker()
    flags = monitor_attempt(
        trk, sb, SlotKey(4, "A"), "10.0.0.1", spec(4, "on"), int(time.time()), CAPS
    )
    assert flags.done is True  # one counted miss did not break the streak


# ------------------------------------------------------------ fresh flow


def test_deferred_when_lease_not_ready():
    """§3.7 gate: no free machine -> deferred, NOTHING consumed."""
    sb = FakeSandbox(ready=False)
    trk = tracker()
    out = drive(trk, sb, 4, "off")
    assert out == "deferred"
    rec = trk.record_of(SlotKey(4, "A"))
    assert rec.attempt == 0 and rec.n_infra == 0
    assert not any(c[0] == "submit" for c in sb.calls)


def test_infra_failed_slot_submits_fresh(tmp_path):
    """A stale job id (infra-failed) must NOT reattach — submit anew,
    releasing the old lease first; the attempt increments.

    Reattach-forever on a released lease was the runaway's fuel."""
    store = ListEventStore()
    trk = AttemptTracker(store, LedgerPolicy())
    key = SlotKey(4, "A")
    trk.record(key, AttemptState.INFRA_FAILED, job="stale-job", arm="off", attempt=1)
    sb = settled_sandbox(tmp_path, 4, "off")
    out = drive(trk, sb, 4, "off")
    assert out == "accepted"
    assert ("release", "stale-job") in sb.calls  # old lease released first
    assert any(c[0] == "submit" and c[2] == "off" for c in sb.calls)
    rec = trk.record_of(key)
    assert rec.attempt == 2 and rec.n_infra == 1


def test_submit_failure_is_infra():
    """A submit raise is infra (attempt recorded), never a crash."""
    trk = tracker()

    class _Boom(FakeSandbox):
        def submit(self, spec):
            raise RuntimeError("submit failed: boom")

    out = drive(trk, _Boom(), 4, "off")
    assert out == "infra-failed"
    rec = trk.record_of(SlotKey(4, "A"))
    assert rec.attempt == 1 and rec.n_infra == 1


def test_fresh_submit_note_carries_queue(tmp_path):
    """Pool mode: the spec's queue travels to submit (both slots share
    one queue — 2026-10-18 decision) and lands in the submit note."""
    store = ListEventStore()
    trk = AttemptTracker(store, LedgerPolicy())
    sb = settled_sandbox(tmp_path, 4, "on")
    drive(trk, sb, 4, "on")
    submits = [c for c in sb.calls if c[0] == "submit"]
    assert submits == [("submit", QUEUE, "on")]
    notes = [e for e in store.events if e.kind == "submit"]
    assert notes and notes[0].fields["queue"] == QUEUE


# ------------------------------------------------------------ B2 provenance


def test_manifest_stamp_refused_in_store():
    """The store refuses to append an event stamped with another
    manifest (the stamp rule lives in the store, not the writers — the
    tracker stamps from its own store, so the check guards direct
    appends)."""
    store = ListEventStore(manifest_sha8=SHA)
    foreign = AttemptEvent(
        ts="2026-10-18T00:00:00Z",
        pair=4,
        kind="state",
        slot="A",
        manifest="deadbeef",
        fields={"state": "submitted"},
    )
    with pytest.raises(ValueError, match="event manifest"):
        store.append(foreign)
    assert store.events == []


def test_jsonl_store_roundtrip(tmp_path):
    """JsonlEventStore: byte-compatible wire (event/slot/manifest keys),
    replay returns the same stream."""
    path = tmp_path / "events.jsonl"
    store = JsonlEventStore(path, SHA)
    trk = AttemptTracker(store, LedgerPolicy())
    trk.record(SlotKey(4, "A"), AttemptState.SUBMITTED, job="j1", arm="on", attempt=1)
    line = json.loads(path.read_text().splitlines()[0])
    assert (line["event"], line["slot"], line["manifest"]) == ("state", "A", SHA)
    assert line["schema"] == 1  # new logs carry the schema (readers tolerate)
    trk2 = AttemptTracker.replay(JsonlEventStore(path, SHA), LedgerPolicy())
    assert trk2.record_of(SlotKey(4, "A")).job == "j1"


def test_sidecar_manifest_mismatch_refused(tmp_path):
    """B2: a sidecar label mismatch refuses the attempt (SystemExit)."""
    sb = settled_sandbox(tmp_path, 8, "on", manifest="deadbeef")
    trk = tracker()
    with pytest.raises(SystemExit, match="different pre-registration"):
        drive(trk, sb, 8, "on")


def test_sidecar_manifest_match_passes(tmp_path):
    """A matching sidecar label settles normally."""
    sb = settled_sandbox(tmp_path, 8, "on", manifest=SHA)
    trk = tracker()
    assert drive(trk, sb, 8, "on") == "accepted"


def test_pre_v3_bundle_without_label_tolerated(tmp_path):
    """Rehearsal-era bundles (no label) are noted, not refused."""
    sb = settled_sandbox(tmp_path, 9, "off")  # no manifest key
    trk = tracker()
    assert drive(trk, sb, 9, "off") == "accepted"


# ------------------------------------------------------------ §4.6 rule 3


def test_scan_text_suspects_rig_internals():
    """§4.6 rule 3: the mechanism scans; the PATTERNS are arguments."""
    text = json.dumps(
        _assistant(
            tool_calls=[
                {"command": "cat /home/ubuntu/payload/eval/rubrics/diagnose.yaml"}
            ]
        )
    )
    assert scan_text(text, ["eval/rubrics"]) == ["eval/rubrics"]


def test_scan_text_clean_on_normal_session():
    """Skill reads are the treatment, not contamination."""
    text = json.dumps(
        _assistant(tool_calls=[{"command": "cat /x/skills/juju-cli/SKILL.md"}])
    )
    assert scan_text(text, ["eval/rubrics"]) == []


def test_contamination_rejects_and_pauses(tmp_path):
    """A suspect bundle records rejected-contamination and stops the
    driver (operator audit); the lease is still released."""
    text = json.dumps(
        _assistant(
            tool_calls=[{"command": "cat /home/ubuntu/payload/eval/rubrics/x.yaml"}]
        )
    )
    sb = settled_sandbox(tmp_path, 10, "off", session_text=text + "\n")
    trk = tracker()
    with pytest.raises(SystemExit, match="OPERATOR STOP"):
        drive(trk, sb, 10, "off", patterns=["eval/rubrics"])
    rec = trk.record_of(SlotKey(10, "A"))
    assert rec.state == AttemptState.REJECTED_CONTAMINATION
    assert rec.state in TERMINAL
    assert sb.releases  # released in EVERY path


# ------------------------------------------------------------ the split


def test_harvest_never_releases(tmp_path):
    """The interface pin: release happens ONCE, after adjudication —
    harvesting is not releasing (the premature-done bug is
    unrepresentable)."""
    sb = settled_sandbox(tmp_path, 11, "on")
    trk = tracker()
    out = drive(trk, sb, 11, "on")
    assert out == "accepted"
    rel = [i for i, c in enumerate(sb.calls) if c[0] == "release"]
    har = [i for i, c in enumerate(sb.calls) if c[0] == "harvest"]
    assert len(rel) == 1
    assert har and har[0] < rel[0]  # harvest strictly before the one release


def test_release_happens_on_infra_paths():
    """Infra paths release the lease in EVERY branch (rig-wait window
    closed -> infra-failed, lease released)."""
    sb = FakeSandbox(ready_endpoint=False)
    trk = tracker()
    out = drive(trk, sb, 12, "off", caps=CAPS_IP_ONLY)
    assert out == "infra-failed"
    assert len(sb.releases) == 1


def test_release_happens_on_wait_ip_miss():
    """The wait-ip miss releases the lease too (attempt recorded)."""
    sb = FakeSandbox(endpoint="")  # resolve never yields an endpoint
    trk = tracker()
    out = drive(trk, sb, 12, "off", caps=CAPS_CLOSED)
    assert out == "infra-failed"
    assert len(sb.releases) == 1
    rec = trk.record_of(SlotKey(12, "A"))
    assert rec.attempt == 1 and rec.n_infra == 1


# ------------------------------------------------------------ fast-fail


def test_fast_fail_in_wait_ip():
    """A lab-side terminal job failure ends wait_ip immediately.

    The real shape (2026-10-18 pair 1/A, b853d2ce): a failed MAAS deploy
    still reported device_ip, the driver granted it, and the rig-wait
    burned the full 45-min window against a released node. The
    job_failed capability (manifest v4 amendment) must cut the window
    short — infra recorded, lease released, in-process counter bumped."""
    sb = FakeSandbox(endpoint="", fails_fast=True)  # resolve never yields
    trk = tracker()
    out = drive(trk, sb, 13, "off", caps=CAPS)  # open window: must NOT burn it
    assert out == "infra-failed"
    assert len(sb.releases) == 1
    rec = trk.record_of(SlotKey(13, "A"))
    assert rec.attempt == 1 and rec.n_infra == 1


def test_fast_fail_in_rig_wait():
    """A failed lab job during rig-wait infra-fails immediately, not at
    the window deadline (same pair-1/A shape, one loop later)."""
    sb = FakeSandbox(ready_endpoint=False, fails_fast=True)
    trk = tracker()
    out = drive(trk, sb, 13, "off", caps=CAPS)  # open window: must NOT burn it
    assert out == "infra-failed"
    assert len(sb.releases) == 1
    rec = trk.record_of(SlotKey(13, "A"))
    assert rec.n_infra == 1


def test_fast_fail_absent_or_false_changes_nothing(tmp_path):
    """The capability is optional and must not disturb the healthy flow."""
    sb = settled_sandbox(tmp_path, 14, "on")
    sb.job_failed = lambda job: False  # capability present, lab healthy
    trk = tracker()
    out = drive(trk, sb, 14, "on")
    assert out == "accepted"
    har = next(i for i, c in enumerate(sb.calls) if c[0] == "harvest")
    assert not any(c[0] == "release" for c in sb.calls[:har])


# ------------------------------------------------------------ local backend


def _runner_script():
    """A local runner (cwd = scratch dir): emits the bundle artifacts
    then exits done. session.jsonl is touched first (the monitor's
    first poll must see a live-but-booting attempt)."""
    return [
        "python3",
        "-c",
        "import json, pathlib, time, sys\n"
        "d = pathlib.Path.cwd()\n"
        "(d / 'session.jsonl').write_text('')\n"
        "(d / 'stream.jsonl').write_text('')\n"
        "time.sleep(0.2)\n"
        "(d / 'session.jsonl').write_text(json.dumps({'message': "
        "{'role': 'assistant', 'content': []}}) + chr(10))\n"
        "(d / 'capture.json').write_text(json.dumps({'task_id': 'x', "
        "'run': 13, 'dur_seconds': 0.5, 'pi_exit_code': 0, 'skills': [], "
        "'early_exit': None, 'extra_metadata': {}}))\n"
        "(d / 'graded.jsonl').write_text(json.dumps({'schema_ok': True, "
        "'verdict': {'verdict': 'charm-bug'}}) + chr(10))\n"
        "(d / 'runner-status').write_text('done')\n"
        "sys.exit(0)\n",
    ]


def test_local_oneshot_end_to_end(tmp_path):
    """NEW with the split: a complete fake attempt on the test host via
    drive_attempt — submit -> prepared -> launch -> done -> harvest ->
    accepted. No external provider."""
    sandbox = LocalOneShotSandbox(tmp_path / "scratch", _runner_script())
    trk = tracker()
    out = drive(trk, sandbox, 13, "on", caps=CAPS_E2E)
    assert out == "accepted"
    rec = trk.record_of(SlotKey(13, "A"))
    assert rec.attempt == 1 and rec.state == AttemptState.ACCEPTED
    assert rec.bundle and "pair-13" in rec.bundle
    assert (tmp_path / "scratch" / "attempt-13-on").exists() is False  # released


def test_local_oneshot_terminate(tmp_path):
    """terminate TERMs the spawned runner (cap/wall path, no zombie)."""
    runner = [
        "python3",
        "-c",
        "import pathlib, time\n"
        "d = pathlib.Path.cwd()\n"
        "(d / 'session.jsonl').write_text('')\n"
        "(d / 'stream.jsonl').write_text('')\n"
        "time.sleep(60)\n",
    ]
    sandbox = LocalOneShotSandbox(tmp_path / "scratch", runner)
    spc = spec(14, "off")
    job = sandbox.submit(spc)
    assert sandbox.endpoint_ready(job) is True  # submit prepares the dir
    sandbox.launch(job, spc, 1)
    sandbox.terminate(job, spc)
    time.sleep(0.2)  # SIGTERM delivery
    sample = sandbox.sample(job, spc)
    assert isinstance(sample, Sample)  # the union narrows to the sample
    assert sample.alive is False  # runner is dead
    assert sandbox.completion_marker(job, spc) == ""  # runner never finished
    sandbox.release(job)
    assert not (tmp_path / "scratch" / "attempt-14-off").exists()


# ------------------------------------------------------------ misc


def test_terminal_set_excludes_infra():
    """INFRA_FAILED is deliberately NOT terminal: it stays in the loop
    until the ledger cap. That omission IS the §4.3 retry policy."""
    assert AttemptState.INFRA_FAILED not in TERMINAL
    assert AttemptState.PENDING not in TERMINAL
    assert {"accepted", "kept-itt-failed", "rejected-contamination"} <= {
        str(s) for s in TERMINAL
    }
