#!/usr/bin/env python3
"""c3 campaign loop — the campaign layer over profe.driver (PROFE-DRIVE).

Was rig/driver-v3.py. The attempt state machine (states, ledger,
monitor, settle, resume adjudication) moved into profe.driver (the L1
library); this file keeps the campaign judgment (A8): the manifest
pre-registration (B2), the pair plan, the coin epoch + queue mapping,
block boundaries, the run sheet, dispositions, and the §4.3 skip
decision. The machine boundary is rig/testflinger_sandbox.py — the
thin adapter; boundary-testflinger.sh stays VERBATIM (B3).

Runbook command line (unchanged in shape; the ledger dir is per-run —
rehearsals used `state-v3pool`, the campaign uses `state-campaign`):

    python3 rig/campaign.py --state rig/state-campaign [--pairs 6] [--dry-run]

§4.3 (2026-10-18 operator decision): an exhausted slot no longer
pauses the campaign — the slot is skipped (a ledger-skip note goes to
the event log), the pair proceeds with the other slot, and the pair
loop moves on once both slots are terminal or exhausted.

One deliberate divergence from driver-v3 (documented): an invalid
bundle's infra-failed record names the attempt's arm dir inside the
recover stamp (the adapter resolves the rig layout), while driver-v3
named the recover stamp root. Diagnostics-only; the row and the
terminal path are unchanged.
"""

import argparse
import csv
import hashlib
import math
import random
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml

RIG = Path(__file__).resolve().parent
CAMPAIGN = RIG.parent  # eval/phase2/campaign3
REPO = CAMPAIGN.parents[2]  # repo root (skills tree; same
# SRC launch-payload.sh stages)
sys.path.insert(0, str(REPO / "profe" / "src"))

from profe.driver import (  # noqa: E402  after the sys.path insert
    TERMINAL,
    AttemptSpec,
    AttemptState,
    AttemptTracker,
    CapPolicy,
    JsonlEventStore,
    LedgerPolicy,
    SlotKey,
    adjudicate,
    coin_drift,
    drive_attempt,
)

from testflinger_sandbox import TestflingerSandbox  # noqa: E402

CONTAMINATION_PATTERNS = [  # §4.6 rule 3: rig internals in the session —
    # calibrated 2026-10-17: absent from rehearsal pairs 2/3 bundles;
    # git_sources is a recorded COVARIATE, not a signal (1-8 is normal)
    "eval/rubrics",
    "run-attempt.sh",
    "incidents.py",
    "runner-status",
    "ground-truth",
    "driver-v3",
    "run-sheet",
    "manifest-c3",
]


def now() -> str:
    """UTC timestamp, the event-log wire format."""
    return datetime.now(UTC).strftime("%FT%TZ")


def say(msg: str) -> None:
    """Progress line to stdout (the operator's driver log)."""
    print(msg, flush=True)


# ---------------------------------------------------------------- manifest


def load_manifest(path: Path) -> dict:
    """B2: parse + hash + verify the frozen inputs against the tree."""
    raw = path.read_bytes()
    m = yaml.safe_load(raw)
    m["_raw_sha256"] = hashlib.sha256(raw).hexdigest()
    m["_sha8"] = m["_raw_sha256"][:8]
    problems = []
    for key in ("prices", "rubric", "task"):
        spec = m["frozen_inputs"][key]
        f = (path.parent / spec["file"]).resolve()
        if not f.is_file():
            problems.append(f"{key}: missing {f}")
        elif hashlib.sha256(f.read_bytes()).hexdigest() != spec["sha256"]:
            problems.append(f"{key}: hash drift {f}")
    sk = m["frozen_inputs"]["skills"]
    h = skills_hash(REPO / "skills")
    if h != sk["sha256"]:
        problems.append(
            f"skills: hash drift (manifest {sk['sha256'][:12]}, tree {h[:12]})"
        )
    if problems:
        raise SystemExit(preflight_refuse(m, problems))
    return m


def skills_hash(skills_dir: Path) -> str:
    """sha256 over sorted (relpath, file-sha) of the staged skills tree."""
    SKILLS = [
        "charmed-mysql-fault-injection",
        "charmed-mysql-juju-inspect",
        "charmed-mysql-nightly-triage",
        "juju-cli",
        "mysql-charms-source",
    ]
    lines = []
    for s in SKILLS:
        for f in sorted((skills_dir / s).rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                lines.append(
                    f"{f.relative_to(skills_dir)}  "
                    + hashlib.sha256(f.read_bytes()).hexdigest()
                )
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def preflight_refuse(manifest: dict, problems: list) -> str:
    """Build the B2 refusal message (frozen-input drift)."""
    return (
        f"PREFLIGHT REFUSAL (exit 2): frozen-input drift — the manifest "
        f"is a pre-registration; fix the tree or re-sign the manifest "
        f"(bump manifest_version, operator sign-off in the delta log). "
        f"manifest_version={manifest['manifest_version']}; problems: "
        + "; ".join(problems)
    )


# ---------------------------------------------------------------- campaign


class Campaign:
    """The pair loop + campaign judgment; the attempt machinery is profe's."""

    def __init__(
        self,
        manifest_path: Path,
        state_dir: Path,
        pairs,
        dry: bool,
        resume_note: str = "",
    ):
        """Load the manifest, bind state, replay the event log."""
        self.m = load_manifest(manifest_path)
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.dry = dry
        self.resume_note = resume_note
        self.all_pairs = list(range(1, self.m["blocks"]["total_pairs"] + 1))
        self.pairs = pairs if pairs else self.all_pairs
        self.sheet = self.state_dir / "run-sheet.csv"
        self.wd = self.m["watchdog"]
        self.events = JsonlEventStore(self.state_dir / "events.jsonl", self.m["_sha8"])
        self.tracker = AttemptTracker.replay(
            self.events,
            LedgerPolicy(max_infra_per_slot=self.m["attempts"]["max_infra_per_slot"]),
        )
        tspec = self.m["frozen_inputs"]["task"]
        self.sandbox = TestflingerSandbox(
            self.state_dir, dry, Path(tspec["file"]).name, tspec["id"]
        )
        self.caps = CapPolicy(
            cost_usd=self.m["caps"]["cost_usd"],
            total_tokens=self.m["caps"]["total_tokens"],
            calls=self.m["caps"]["calls"],
            ip_wait_s=self.wd["ip_wait_s"],
            rig_wait_s=self.wd["rig_wait_s"],
            monitor_bound_s=self.wd["monitor_bound_s"],
            poll_s=120,
            sshfail_max=self.wd["sshfail_max"],
            zact_s=self.wd["zact_s"],
            wall_s=self.wd["wall_s"],
        )
        self._skipped: set[tuple[int, str]] = set()  # ledger-skip announce guard

    # -- campaign judgment: coin + queue mapping (P7) ------------------
    def coin_arms(self, pair: int) -> dict:
        """P7: deterministic seeded draw, emitted once, never redrawn.

        Same draw as run-pair.sh: random.Random(seed).random() < 0.5 -> slot A on).
        Emitted once; afterwards always the recorded arms (a recorded coin
        is never redrawn — and never re-emitted).
        """
        seed = 20261030 + pair
        qa, qb = self.m["twin_slots"][0]["a"], self.m["twin_slots"][0]["b"]
        on_a = random.Random(seed).random() < 0.5  # noqa: PLR2004 (P7: FAIR coin — 0.5 is the design)
        arms = {
            "A": {"queue": qa, "arm": "on" if on_a else "off"},
            "B": {"queue": qb, "arm": "off" if on_a else "on"},
        }
        rec = self.tracker.record_of(SlotKey(pair, "A")).coin
        if rec:
            if coin_drift(rec, seed):
                raise SystemExit(
                    f"PREFLIGHT REFUSAL (exit 2): pair {pair} coin drift — "
                    f"events say {rec}, seed {seed} draws {arms}. "
                    f"A recorded coin is honored, never redrawn (§3.1 P7); "
                    f"a mismatch means the record or the seed formula was "
                    f"tampered with — stop and audit before rerunning."
                )
            return rec
        self.tracker.note(
            SlotKey(pair, "both"),
            "coin",
            seed=seed,
            arms={k: dict(v) for k, v in arms.items()},
        )
        return arms

    def spec(self, pair: int, slot: str, arm: str) -> AttemptSpec:
        """Build the attempt spec for one slot (queue mapping is campaign data)."""
        return AttemptSpec(
            run_id=pair,
            arm=arm,
            payload_ref=f"skills:{self.m['_sha8']}",  # launch-payload stages the tree
            manifest_sha8=self.m["_sha8"],
            queue=self.coin_arms(pair)[slot]["queue"],
        )

    # -- preflight ----------------------------------------------------
    def preflight(self) -> None:
        """Banner + ledger report + resume scan (no mutations in dry-run)."""
        say(
            f"== campaign | manifest {self.m['_sha8']} "
            f"(v{self.m['manifest_version']}, campaign {self.m['campaign']})"
        )
        say(f"   model={self.m['model']} caps={self.m['caps']}")
        say(
            f"   attempts.max_infra_per_slot="
            f"{self.m['attempts']['max_infra_per_slot']}  twin slot: "
            f"A={self.m['twin_slots'][0]['a']} B={self.m['twin_slots'][0]['b']}"
        )
        say(
            f"   state: {self.state_dir}  pairs: "
            f"{self.pairs if self.pairs else 'all 30'}"
            f"{'  DRY-RUN' if self.dry else ''}"
        )
        if self.resume_note:
            say(f"   resume note: {self.resume_note}")
        for key, rec in sorted(
            self.tracker.st.items(), key=lambda kv: (kv[0].pair, kv[0].slot)
        ):
            if rec.state != AttemptState.PENDING or rec.n_infra or rec.attempt:
                say(
                    f"   ledger pair {key.pair}/{key.slot}: state={rec.state} "
                    f"attempt={rec.attempt or 1} infra_fails={rec.n_infra}"
                    + (f" job={rec.job}" if rec.job else "")
                )
        for pair in self.pairs:
            for slot in ("A", "B"):
                self.resume_scan(pair, slot, announce=True)

    # -- resume (B1): bundle evidence outranks the log -----------------
    def resume_scan(self, pair: int, slot: str, announce: bool = False) -> None:
        """Terminal state from a valid recovered bundle (B1 resume gate).

        A valid bundle is NEVER re-run regardless of what the sheet or
        any CSV says (the c2 $16/$NF regression).
        """
        key = SlotKey(pair, slot)
        if self.tracker.is_terminal(key):
            return
        arm = self.tracker.record_of(key).arm or self.coin_arms(pair)[slot]["arm"]
        for b in self.sandbox.prior_bundles(self.spec(pair, slot, arm)):
            adj = adjudicate(b, CONTAMINATION_PATTERNS)
            if adj.usable and adj.state:
                self.tracker.record(
                    key,
                    AttemptState(adj.state),
                    arm=arm,
                    bundle=str(b),
                    attempt=adj.attempt,
                )
                if announce:
                    say(f"   resume: pair {pair}/{slot} ({arm}) -> {adj.state}")
                return
            if announce:
                say(
                    f"   resume: pair {pair}/{slot} bundle invalid: "
                    f"{'; '.join(adj.reasons)}"
                )

    # -- one attempt on one slot ---------------------------------------
    def run_attempt(self, pair: int, slot: str) -> str:
        """One attempt: submit -> rig -> launch -> monitor -> settle.

        All of it is profe.driver.drive_attempt; the campaign supplies
        the spec (coin + queue), the caps (manifest), and the §4.6
        patterns (campaign data, A8).
        """
        key = SlotKey(pair, slot)
        arm = self.tracker.record_of(key).arm or self.coin_arms(pair)[slot]["arm"]
        return drive_attempt(
            self.tracker,
            self.sandbox,
            key,
            self.spec(pair, slot, arm),
            self.caps,
            contamination_patterns=CONTAMINATION_PATTERNS,
            say=say,
        )

    # -- the sheet (a view) ---------------------------------------------
    def write_row(self, pair: int, slot: str, bundle: str) -> None:
        """Append the sheet row (sheet-row.py; the sheet is a view).

        Kill-class flags travel in the tracker's done note (profe
        records them with the terminal); this reads the slot's last
        done event — the campaign's own provenance — for the row.
        """
        if self.dry:
            return
        flags = {"hit_cap": "0", "wall": "0", "infra": "0"}
        attempt = 1
        job = ""
        for e in self.events.stream():
            if e.kind == "done" and e.pair == pair and e.slot == slot:
                flags["hit_cap"] = str(e.fields.get("hit_cap", 0))
                flags["wall"] = str(e.fields.get("wall", 0))
                flags["infra"] = str(e.fields.get("infra", 0))
                attempt = e.fields.get("attempt", 1)
                job = e.fields.get("job", "")
        rec = self.tracker.record_of(SlotKey(pair, slot))
        # pool mode: the machine cell records the DUT that actually ran
        # (hostname at rig-ready), not the queue — fallback = queue name
        machine = rec.machine or self.coin_arms(pair)[slot]["queue"]
        rc = subprocess.run(
            [
                "python3",
                str(RIG / "sheet-row.py"),
                bundle,
                str(pair),
                slot,
                machine,
                rec.arm or "",
                str(attempt),
                job,
                flags["hit_cap"],
                flags["wall"],
                flags["infra"],
                f"--sheet={self.sheet}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if rc.returncode != 0:
            say(f"   [{slot}] row FAILED: {rc.stderr[-300:]}")
            self.tracker.note(SlotKey(pair, slot), "note", detail="sheet row failed")
        else:
            self.tracker.note(
                SlotKey(pair, slot), "row", arm=rec.arm or "", bundle=bundle
            )

    def compare(self, pair: int) -> None:
        """Operator-facing pair print (sheet is the record, this the view)."""
        if not self.sheet.is_file():
            return
        rows = [r for r in csv.DictReader(self.sheet.open()) if r["pair"] == str(pair)]
        off = next((r for r in rows if r["arm"] == "off"), None)
        on = next((r for r in rows if r["arm"] == "on"), None)
        if not (off and on):
            say(f"   (pair {pair}: incomplete — see run-sheet)")
            return
        say(
            f"   pair {pair}: calls {off['calls']}/{on['calls']}  "
            f"tok {off['total_tok']}/{on['total_tok']}  "
            f"cost ${off['cost_frozen_usd']}/${on['cost_frozen_usd']}"
        )
        if float(off["calls"]) and float(on["calls"]):
            d = math.log(float(off["calls"]) / float(on["calls"]))
            say(f"   paired calls log-ratio d = ln(off)-ln(on) = {d:+.3f}")
        for r, lab in [(off, "off"), (on, "on")]:
            say(
                f"   [{lab}] verdict={r['verdict'] or '-'} "
                f"schema_ok={r['schema_ok']} skills_read={r['skills_read']} "
                f"hit_cap={r['hit_cap']} wall={r['wall_killed']} "
                f"infra={r['infra']} machine={r['machine']}"
            )

    def settle(self, pair: int, slot: str) -> str:
        """Campaign tail of a settled attempt: the row + the comparison.

        The attempt itself already recorded the terminal state (and
        released the lease) in profe's settle; the sheet is the
        campaign's view of that record. driver-v3 parity: the row and
        the comparison fire only for a TERMINAL state — an
        infra-failed slot writes no row (the ledger is its record).
        """
        rec = self.tracker.record_of(SlotKey(pair, slot))
        if rec.state in TERMINAL:
            self.write_row(pair, slot, rec.bundle or "")
            self.compare(pair)
        return rec.state.value if rec.state else "pending"

    # -- the campaign loop ---------------------------------------------
    def block_boundary(self, pair: int) -> None:
        """§3.1/§3.7: pool snapshot at each block boundary (11, 21, ...).

        Observability-only (2026-10-18 decision corollary): under pool
        mode the fleet's state is not the campaign's failure — a busy or
        degraded pool is recorded and the loop continues; the per-attempt
        capacity gate (lease_ready → defer) owns availability.
        """
        if pair <= 1 or (pair - 1) % self.m["blocks"]["size"] != 0:
            return
        block = (pair - 1) // self.m["blocks"]["size"] + 1
        self.tracker.note(SlotKey(pair, "both"), "block-boundary", block=block)
        states = []
        for slot in ("A", "B"):
            q = self.coin_arms(pair)[slot]["queue"]
            state = self.sandbox.queue_state(q)
            say(f"   {slot} {q}: agent {state}")
            states.append(state)
        if any(s not in ("waiting", "idle") for s in states):
            say(
                "   [!] pool not idle at the boundary — recorded, "
                "continuing (the per-attempt gate handles capacity)"
            )
            self.tracker.note(
                SlotKey(pair, "both"),
                "boundary-pool-busy",
                detail=f"queue snapshot: {states}",
            )

    def ledger_exhausted(self, pair: int, slot: str) -> bool:
        """§4.3 cap check (2026-10-18 decision: skip, never pause)."""
        return self.tracker.infra_exhausted(SlotKey(pair, slot))

    def ledger_skip(self, pair: int, slot: str) -> None:
        """Announce + note a §4.3 skip once per process run."""
        if not self.ledger_exhausted(pair, slot):
            return
        if (pair, slot) in self._skipped:
            return
        self._skipped.add((pair, slot))
        cap = self.m["attempts"]["max_infra_per_slot"]
        n = self.tracker.record_of(SlotKey(pair, slot)).n_infra
        say(f"   [{slot}] §4.3: {n} infra attempts (cap {cap}) — slot skipped")
        self.tracker.note(
            SlotKey(pair, slot),
            "ledger-skip",
            detail=f"infra attempts {n} >= cap {cap}: skipped to next work",
        )

    def run(self) -> int:
        """Run the campaign loop over the planned pairs."""
        self.preflight()
        if self.dry:
            for pair in self.pairs:
                arms = self.coin_arms(pair)
                self.resume_scan(pair, "A")
                self.resume_scan(pair, "B")
                ra, rb = (
                    self.tracker.record_of(SlotKey(pair, "A")),
                    (self.tracker.record_of(SlotKey(pair, "B"))),
                )
                say(
                    f"== DRY plan pair {pair}: "
                    f"A={arms['A']['queue']}:{arms['A']['arm']}"
                    f"[{ra.state}]  "
                    f"B={arms['B']['queue']}:{arms['B']['arm']}"
                    f"[{rb.state}]"
                )
            say("== DRY-RUN: plan above; no machine ops were performed")
            return 0
        for pair in self.pairs:
            self.block_boundary(pair)
            say(f"== PAIR {pair}")
            arms = self.coin_arms(pair)
            say(
                f"   coin: A={arms['A']['queue']}:{arms['A']['arm']} "
                f"B={arms['B']['queue']}:{arms['B']['arm']}"
            )
            while True:
                for slot in ("A", "B"):
                    self.ledger_skip(pair, slot)
                    self.resume_scan(pair, slot)
                todo = [
                    s
                    for s in ("A", "B")
                    if not self.tracker.is_terminal(SlotKey(pair, s))
                    and not self.ledger_exhausted(pair, s)
                ]
                if not todo:
                    break
                results = {}
                for slot in todo:
                    r = self.run_attempt(pair, slot)
                    results[slot] = r
                    self.settle(pair, slot)
                if all(r == "deferred" for r in results.values()):
                    say("   all slots deferred (agents busy) — sleeping 5 min")
                    time.sleep(300)
                elif any(r == "deferred" for r in results.values()):
                    time.sleep(60)
            ra, rb = (
                self.tracker.record_of(SlotKey(pair, "A")),
                (self.tracker.record_of(SlotKey(pair, "B"))),
            )
            say(f"== PAIR {pair} COMPLETE ({ra.state} / {rb.state})")
        say("== campaign: planned pairs complete")
        return 0


def main() -> int:
    """CLI entry: parse args, build the campaign, run the planned pairs."""
    ap = argparse.ArgumentParser(description="c3 campaign loop (B1/B2/B3/§4.3)")
    ap.add_argument("--manifest", type=Path, default=RIG / "manifest-c3.yaml")
    ap.add_argument("--state", type=Path, default=RIG / "state")
    ap.add_argument(
        "--pairs",
        type=str,
        default="",
        help="comma-separated pair numbers (default: all 30)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="replay + plan only: no submits, no mutations",
    )
    ap.add_argument(
        "--resume-note",
        type=str,
        default="",
        help="operator note recorded in the events log",
    )
    a = ap.parse_args()
    pairs = [int(x) for x in a.pairs.split(",") if x.strip()] if a.pairs else None
    camp = Campaign(a.manifest, a.state, pairs, a.dry_run, a.resume_note)
    if a.resume_note:
        camp.tracker.note(None, "note", detail=f"resume-note: {a.resume_note}")
    return camp.run()


if __name__ == "__main__":
    sys.exit(main())
