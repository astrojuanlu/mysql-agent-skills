#!/usr/bin/env python3
"""status.py — READ-ONLY state viewer for a c3 campaign state dir.

Retro A7's campaign-side half (the profe-core `profe status` remains
deferred profe-core work): one command that says where every attempt
stands, derived from the bundles via lib/bundle.py — never from the run
sheet, never from driver memory. Writes NOTHING (the anti-rogue-monitor
property: a status tool that cannot write cannot lie).

Usage:
  status.py [--state DIR] [--json]

Default --state: <this script's dir>/state. Shows, per latest recovered
attempt (pair, arm, attempt): termination class (lib.bundle), validity
(lib.bundle.validate — a driver may key skip/resume on this), depth
grade, mechanical verdict, usage/cost, and the validating reasons. Also:
  - events heartbeat: age of the newest events.jsonl entry, if present
  - manifest: sha16 from state/manifest.json if one exists; normally
    ABSENT — the manifest hash travels on every event line instead
"""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import bundle as bundle_mod


def scan(state: Path):
    """Latest recovery per (pair, arm, attempt) → list of records."""
    best = {}
    for pd in sorted(state.glob("recovered-*/runs/pair-*")):
        if not pd.name.removeprefix("pair-").isdigit():
            continue
        for out in sorted(pd.iterdir()):
            if out.name not in ("on", "off") or not out.is_dir():
                continue
            try:
                rec = bundle_mod.read(out)
            except bundle_mod.BundleFormatError:
                # not a current-format bundle (pre-sidecar / five-probe-era
                # shape): display as a refused attempt instead of crashing
                rec = {
                    "bundle": str(out),
                    "pair": None,
                    "arm": None,
                    "attempt": 1,
                    "machine": "",
                    "dur": None,
                    "pi_exit": None,
                    "calls": None,
                    "cost": None,
                    "verdict": "",
                    "schema_ok": None,
                    "depth": None,
                    "evidence_strength": None,
                    "skills_read": [],
                    "run_status": "",
                    "early_exit": None,
                }
            pair = (
                rec["pair"]
                if rec["pair"] is not None
                else pd.name.removeprefix("pair-")
            )
            arm = rec["arm"] or out.name
            key = (int(pair), arm, int(rec["attempt"] or 1))
            rkey = out.parts[-4]  # recovered-<ts>
            if key not in best or rkey > best[key][0]:
                best[key] = (rkey, out, rec)
    rows = []
    for pair, arm, attempt in sorted(best):
        _, out, rec = best[(pair, arm, attempt)]
        ok, reasons = bundle_mod.validate(out)
        rows.append(
            {
                "pair": pair,
                "arm": arm,
                "attempt": attempt,
                "recovery": best[(pair, arm, attempt)][0],
                "termination": bundle_mod.classify_termination(rec),
                "valid": ok,
                "invalid_reasons": reasons,
                "machine": rec["machine"],
                "dur": rec["dur"],
                "pi_exit": rec["pi_exit"],
                "calls": rec["calls"],
                "cost": rec["cost"],
                "verdict": rec["verdict"],
                "schema_ok": rec["schema_ok"],
                "depth": rec["depth"],
                "evidence_strength": rec["evidence_strength"],
                "skills_read": rec["skills_read"],
                "bundle": rec["bundle"],
            }
        )
    return rows


def heartbeat(state: Path):
    """(age_seconds, path) of the newest events.jsonl, or (None, None)."""
    newest, t = None, None
    for ev in state.glob("**/events.jsonl"):
        try:
            m = ev.stat().st_mtime
        except OSError:
            continue
        if t is None or m > t:
            newest, t = ev, m
    if t is None:
        return None, None
    return time.time() - t, str(newest)


def main():
    """Print the pair-level terminal-state board over the latest attempt logs."""
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--state", default=str(Path(__file__).resolve().parent / "state"))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    state = Path(a.state).expanduser()
    rows = scan(state)
    hb_age, hb_path = heartbeat(state)
    man = state / "manifest.json"
    man_sha = None
    if man.is_file():
        man_sha = hashlib.sha256(man.read_bytes()).hexdigest()[:16]

    if a.json:
        print(
            json.dumps(
                {
                    "state": str(state),
                    "attempts": rows,
                    "heartbeat_age_s": hb_age,
                    "heartbeat": hb_path,
                    "manifest_sha16": man_sha,
                },
                indent=2,
            )
        )
        return
    print(f"state: {state}")
    if hb_age is not None:
        print(f"events heartbeat: {hb_age:.0f} s old  ({hb_path})")
    print(
        "manifest: "
        + (
            f"present, sha16={man_sha}"
            if man_sha
            else "absent (normal: the "
            "manifest hash rides every event line, not a state-dir file)"
        )
    )
    print()
    hdr = (
        f"{'PAIR':>4} {'ARM':<4} {'ATT':>3} {'TERM':<10} {'OK':<3} {'DEPTH':>5}"
        f" {'CALLS':>5} {'COST':>8}  VERDICT / NOTES"
    )
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        notes = r["verdict"] or "-"
        if not r["valid"]:
            notes += "  [" + "; ".join(r["invalid_reasons"]) + "]"
        cost = f"{r['cost']:.4f}" if r["cost"] is not None else "-"
        calls = str(r["calls"]) if r["calls"] is not None else "-"
        depth = str(r["depth"]) if r["depth"] is not None else "-"
        print(
            f"{r['pair']:>4} {r['arm']:<4} {r['attempt']:>3} "
            f"{r['termination']:<10} {'y' if r['valid'] else 'n':<3} "
            f"{depth:>5} {calls:>5} {cost:>8}  {notes}"
        )


if __name__ == "__main__":
    main()
