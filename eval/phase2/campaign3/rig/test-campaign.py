"""Tests for the campaign layer (rig/campaign.py) — campaign judgment only.

The attempt machinery's negative suite lives in profe/tests/test_driver.py
(the ported rig/test-driver-v3.py); what stays here is the campaign's own
surface: the B2 manifest pre-registration, the P7 coin draw pinned against
the rehearsal pairs, and the §4.3 skip decision in the loop (2026-10-18
operator decision: an exhausted slot no longer pauses — it is skipped).

All fixtures are synthetic (tmp state dirs, a stub sandbox that imports
nothing from profe — the structural-conformance proof applies to the
campaign too).
"""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

RIG = Path(__file__).resolve().parent  # .../campaign3/rig
sys.path.insert(0, str(RIG))
_spec = importlib.util.spec_from_file_location("campaign_mod", RIG / "campaign.py")
camp_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(camp_mod)  # noqa: E402


# ------------------------------------------------------------ fixtures


class FailingSubmitSandbox:
    """A sandbox whose every submit fails (infra machine, §4.3 loop test).

    Imports nothing from profe: structural conformance is the point.
    """

    reprovisions_on_resubmit = True

    def __init__(self):
        self.releases = []

    def lease_ready(self, spec):
        return True

    def submit(self, spec):
        raise RuntimeError(f"submit {spec.queue} failed: boom")

    def resolve(self, job):
        return None

    def endpoint_ready(self, endpoint):
        return False

    def launch(self, endpoint, spec, attempt):
        raise RuntimeError("unreachable")

    def sample(self, endpoint, spec):
        raise RuntimeError("unreachable")

    def completion_marker(self, endpoint, spec):
        return ""

    def terminate(self, endpoint, spec):
        pass

    def harvest(self, endpoint, job, spec, force):
        raise RuntimeError("unreachable")

    def release(self, job):
        self.releases.append(job)

    def queue_state(self, queue):
        return "waiting"

    def prior_bundles(self, spec):
        return []


def campaign(tmp_path, *, dry=False):
    """Build a Campaign over a tmp state dir with the REAL manifest.

    Hash checks run against the live tree — they pass because nothing
    drifted.
    """
    return camp_mod.Campaign(RIG / "manifest-c3.yaml", tmp_path / "state", None, dry)


# ------------------------------------------------------------ P7 coin


def test_coin_matches_rehearsal_pairs(tmp_path):
    """Pin the P7 draw against the rehearsal pairs (A=off, both)."""
    # pair-2 and pair-3 rehearsals (run-pair.sh, 2026-10-17): slot A took
    # the OFF arm both times; the draw must be identical
    c = campaign(tmp_path)
    a2 = c.coin_arms(2)
    a3 = c.coin_arms(3)
    assert a2["A"]["arm"] == "off" and a2["B"]["arm"] == "on"
    assert a3["A"]["arm"] == "off" and a3["B"]["arm"] == "on"


def test_manifest_drift_refused(tmp_path):
    """B2: frozen-input drift is a preflight refusal (exit 2)."""
    m = yaml.safe_load((RIG / "manifest-c3.yaml").read_bytes())
    m["frozen_inputs"]["prices"]["sha256"] = "0" * 64
    # resolve prices/rubric relative to the manifest's own directory: keep
    # the relative paths valid by placing the copy in rig/
    p = RIG / f"manifest-drift-test-{id(tmp_path)}.yaml"
    yaml.safe_dump(m, p.open("w"), allow_unicode=True)
    try:
        with pytest.raises(SystemExit, match="PREFLIGHT REFUSAL"):
            camp_mod.load_manifest(p)
    finally:
        p.unlink(missing_ok=True)


def test_skills_hash_stable_and_exact():
    """Pin the tree-hash definition against the signed manifest."""
    # pins the tree-hash definition (sorted relpath+file-sha lines)
    h = camp_mod.skills_hash(camp_mod.REPO / "skills")
    m = yaml.safe_load((RIG / "manifest-c3.yaml").read_bytes())
    assert h == m["frozen_inputs"]["skills"]["sha256"]


def test_lib_classify_parity_with_profe():
    """lib/bundle.py's classify must equal profe's ported copy.

    status.py keeps lib/bundle.py (stdlib-only, runs anywhere); the
    drive loop adjudicates through profe.driver's port. One mapping,
    two homes — this test forbids drift between them.
    """
    sys.path.insert(0, str(RIG.parent / "lib"))
    import bundle as bundle_mod  # noqa: E402  (lib/ insert above)

    from profe.driver import classify_termination

    cases = [
        ({"runner_status": "done", "early_exit": None, "calls": 5}, "completed"),
        ({"runner_status": "incomplete", "early_exit": None, "calls": 3}, "incomplete"),
        (
            {"runner_status": "done", "early_exit": {"reason": "budget"}, "calls": 5},
            "cap",
        ),
        (
            {"runner_status": "done", "early_exit": {"reason": "wall"}, "calls": 5},
            "wall",
        ),
        ({"runner_status": "done", "early_exit": None, "calls": None}, "incomplete"),
    ]
    for rec, want in cases:
        lib_rec = {
            "run_status": rec["runner_status"],
            "early_exit": rec["early_exit"],
            "calls": rec["calls"],
        }
        pf_rec = SimpleNamespace(
            runner_status=rec["runner_status"],
            early_exit=rec["early_exit"],
            calls=rec["calls"],
        )
        assert bundle_mod.classify_termination(lib_rec) == want, rec
        assert classify_termination(pf_rec) == want, rec


# ------------------------------------------------------------ §4.3 in the loop


def test_s43_exhaustion_skips_the_slot_and_ends_the_pair(tmp_path):
    """2026-10-18 decision: the cap skips the slot, never pauses the run.

    driver-v3 raised OPERATOR PAUSE at the cap; the campaign loop now
    records a ledger-skip note and moves to the next work. Both slots
    exhausted -> the pair ends (incomplete), the loop returns 0.
    """
    c = campaign(tmp_path)
    c.pairs = [1]
    c.sandbox = FailingSubmitSandbox()
    assert c.run() == 0
    for slot in ("A", "B"):
        rec = c.tracker.record_of(camp_mod.SlotKey(1, slot))
        assert rec.n_infra == 3, (slot, rec)
        assert rec.state == camp_mod.AttemptState.INFRA_FAILED
    kinds = [
        json.loads(l)["event"]
        for l in (tmp_path / "state/events.jsonl").read_text().splitlines()
    ]
    assert kinds.count("ledger-skip") == 2  # one announce per slot, exactly once
    assert c.sandbox.releases == []  # nothing to release: submit never granted


def test_block_boundary_is_observability_only(tmp_path):
    """2026-10-18 corollary: a busy/degraded pool never pauses the run.

    driver-v3 raised OPERATOR PAUSE at a failed boundary check; under
    pool mode the fleet's state is not the campaign's failure — the
    snapshot is recorded (one boundary-pool-busy note) and the loop
    continues to the pair's work.
    """

    class BusyPool(FailingSubmitSandbox):
        def queue_state(self, queue):
            return "down"

    c = campaign(tmp_path)
    c.pairs = [11]  # block boundary: (11 - 1) % 10 == 0
    c.sandbox = BusyPool()
    assert c.run() == 0
    kinds = [
        json.loads(l)["event"]
        for l in (tmp_path / "state/events.jsonl").read_text().splitlines()
    ]
    assert kinds.count("block-boundary") == 1
    assert kinds.count("boundary-pool-busy") == 1  # one note, not per-slot


def test_dry_run_touches_no_machines(tmp_path):
    """DRY-RUN plans only: no submits in the log, no sheet, no payloads."""
    c = campaign(tmp_path, dry=True)
    c.pairs = [1]
    assert c.run() == 0
    assert not (tmp_path / "state/run-sheet.csv").is_file()
    kinds = [
        json.loads(l)["event"]
        for l in (tmp_path / "state/events.jsonl").read_text().splitlines()
    ]
    assert "submit" not in kinds
    assert not any(k.startswith("infra") for k in kinds)
