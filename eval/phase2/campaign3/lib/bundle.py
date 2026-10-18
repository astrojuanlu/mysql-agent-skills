#!/usr/bin/env python3
"""lib/bundle.py — the campaign bundle MERGE layer for c3 (retro A2/A6/A7).

The schema-owned halves live in profe now: `profe.bundle` reads and
validates profe's own artifacts (capture.json, session.jsonl usage,
graded.jsonl, runner-status) next to the code that writes those formats.
What stays campaign-side, on purpose (retro A8 — campaign judgment never
enters profe):

  incidents.json    record-only forensics (§3.5/§4.6) — campaign artifact
  grade-depth.json  blind depth grade (rubric diagnose@2) — campaign artifact
  classify_termination()  the cap/wall/incomplete mapping onto the §4.6
                    two-class adjudication — campaign law

Every consumer (sheet-row, ANALYSIS-c3, status, driver v3) derives bundle
facts from here — never from the run sheet, never from CSV field
indexing, never from private heuristics.

A bundle (recovered attempt directory) carries:
  capture.json     sidecar: duration, exit, staged skills, metadata
  session.jsonl    the primary record (usage via profe.bundle)
  graded.jsonl     profe's mechanical grade (verdict, schema_ok)
  incidents.json   record-only forensics (§3.5/§4.6)
  grade-depth.json blind depth grade (rubric diagnose@2) — optional
  runner-status    done | incomplete (quarantine marker)
  stream.jsonl     pi's incremental stream (monitoring ground truth)

read(bundle) → record dict (all facts — profe's AND campaign's;
               raises BundleFormatError on non-current-format bundles —
               era-compat tolerance for pre-sidecar/five-probe-era shapes
               was removed; campaign artifacts (incidents/grade-depth)
               stay absence-tolerant)
validate(bundle) → (ok: bool, reasons: [str]) — strict, for skip/resume
classify_termination(record) → "completed | incomplete | cap | wall"
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # same lib/ dir
# profe.bundle is stdlib-only; make it importable on a bare host even when
# only lib/ is on sys.path (sheet-row.py inserts just lib/).
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "profe" / "src"))

import cost as cost_mod
from profe.bundle import BundleFormatError  # noqa: F401  (re-exported)
from profe.bundle import read_bundle as _profe_read
from profe.bundle import validate_bundle as _profe_validate


def read(bundle):
    """All facts for one attempt bundle.

    Raises BundleFormatError unless the directory is a current-format
    attempt bundle (profe.bundle owns that gate; there is no era
    compatibility). Profe facts come from profe.bundle (the schema
    owner); campaign facts (incidents forensics, blind depth grade) are
    read here, tolerant to absence.
    """
    b = Path(bundle)
    rec = {
        "bundle": str(b),
        "pair": None,
        "arm": None,
        "attempt": 1,
        "machine": "",
        "dur": None,
        "pi_exit": None,
        "calls": None,
        "in_tok": 0,
        "out_tok": 0,
        "cache_tok": 0,
        "total_tok": 0,
        "cost": None,
        "schema_ok": None,
        "verdict": "",
        "root_cause": "",
        "skills_staged": [],
        "skills_read": [],
        "run_status": "",
        "early_exit": None,
        "mut": None,
        "gh": None,
        "git": None,
        "projects": None,
        "home_writes": None,
        "depth": None,
        "evidence_strength": None,
        "verdict_confirmation": None,
    }
    r = _profe_read(b)
    rec["dur"] = r.duration_seconds
    rec["pi_exit"] = r.pi_exit_code
    rec["pair"] = r.pair
    rec["arm"] = r.arm
    rec["attempt"] = r.attempt
    rec["machine"] = r.machine
    rec["skills_staged"] = r.skills_staged
    rec["early_exit"] = r.early_exit
    rec["calls"] = r.calls
    rec["in_tok"] = r.input_tokens
    rec["out_tok"] = r.output_tokens
    rec["cache_tok"] = r.cache_read_tokens
    rec["total_tok"] = r.total_tokens
    rec["skills_read"] = r.skill_reads
    # pricing stays campaign-side (profe takes prices as an argument,
    # never owns a vector) — dollars under the pinned vector, None when
    # the session is unreadable (same coupling the old walk had)
    rec["cost"] = (
        None
        if r.calls is None
        else cost_mod.cost(
            {
                "input": r.input_tokens,
                "cacheRead": r.cache_read_tokens,
                "output": r.output_tokens,
            }
        )
    )
    rec["verdict"] = r.verdict
    rec["root_cause"] = r.root_cause
    rec["schema_ok"] = r.schema_ok
    rec["run_status"] = r.runner_status
    inc = b / "incidents.json"
    if inc.is_file():
        try:
            i = json.loads(inc.read_text())
            rec["mut"] = (
                len(i.get("model_destroy", []))
                + len(i.get("add_model", []))
                + len(i.get("bootstrap", []))
            )
            rec["gh"] = int(i.get("gh_commands") or 0)
            rec["git"] = len(i.get("git_sources", []))
            rec["projects"] = len(i.get("projects_paths", []))
            rec["home_writes"] = int(i.get("home_writes") or 0)
        except Exception:  # noqa: BLE001, S110 (forensics are best-effort;
            pass           # absent/garbage incidents.json → zero counts)
    gd = b / "grade-depth.json"
    if gd.is_file():
        try:
            d = json.loads(gd.read_text())
            rec["depth"] = d.get("mechanism_depth")
            rec["evidence_strength"] = d.get("evidence_strength")
            rec["verdict_confirmation"] = d.get("verdict_confirmation")
        except Exception:  # noqa: BLE001, S110 (blind grade is optional;
            pass           # absent/garbage grade-depth.json → None fields)
    return rec


def validate(bundle):
    """Strict validity (retro A2 intent) for skip/resume decisions.

    Checks implemented by profe.bundle (profe's own artifacts); reason
    strings unchanged.
    """
    reasons = _profe_validate(bundle)
    return (not reasons), reasons


def classify_termination(rec):
    """One termination classification (retro A6 intent, campaign-side).

    completed | incomplete | cap | wall — from bundle evidence only:
    runner-status, capture.early_exit. cap/wall kills carry a
    runner-status of done? No: the driver's kill leaves runner-status
    absent/incomplete and early_exit records the profe-side reason; the
    runner itself never marks a kill 'done'.
    """
    if rec["run_status"] and rec["run_status"] != "done":
        return "incomplete"
    ee = rec["early_exit"]
    if ee:
        reason = ee.get("reason", "") if isinstance(ee, dict) else str(ee)
        if "budget" in reason or "cap" in reason:
            return "cap"
        if "wall" in reason or "time" in reason:
            return "wall"
        return "incomplete"
    if rec["calls"] is None:
        return "incomplete"
    return "completed"
