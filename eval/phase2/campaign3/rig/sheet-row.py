#!/usr/bin/env python3
"""Host-side run-sheet row for the c3 rig kit (driver-v3 direction).

The campaign sheet lives host-side (DESIGN §4.7). Reads a recovered bundle
and appends one c3-extended row:

    pair,slot,machine,arm,attempt,ts,dur_s,pi_exit,calls,in_tok,out_tok,
    cache_tok,total_tok,cost_frozen_usd,schema_ok,verdict,skills_read,
    leak_check (legacy c2 value, kept for comparability),incidents,
    hit_cap,wall_killed,infra,job_id

Usage:
  sheet-row.py <out_dir> <pair> <slot> <machine> <arm> <attempt> <job_id> \
               [hit_cap=0] [wall_killed=0] [infra=0] [--sheet=PATH]

Rebuild mode (B8: the sheet is a VIEW of the bundles — regenerate rows
from a state dir; the live driver keeps appending rows itself):

  sheet-row.py --regen <state_dir> [--sheet=PATH]

  Scans <state_dir>/recovered-*/runs/pair-<n>/{on,off}, keeps the latest
  recovery per (pair, arm, attempt), and writes rows in (pair, arm,
  attempt) order. hit_cap/wall_killed/infra are DERIVED from bundle
  evidence (capture.early_exit reason / runner-status); slot, machine and
  job_id are driver state, not bundle facts, and are written as "-".
"""

import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# A4 canonical cost accounting: the price vector lives in ../lib/
# (frozen-prices.json + cost.py), the usage walk in profe.bundle (the
# session-schema owner; stdlib-only, re-exported by lib/cost.py) — this
# file carries neither. The derivation is byte-identical to the
# pre-refactor walk.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cost as cost_mod  # noqa: E402


def _legacy_contract(texts):
    """Legacy mr-234 contract re-parse (pre-graded.jsonl bundles only)."""
    import yaml  # lazy: pyyaml is on the operator host, not the DUT runner

    for t in reversed(texts):
        mm = re.findall(r"```yaml\n(.*?)```", t, re.S)
        if mm:
            try:
                data = yaml.safe_load(mm[-1])
            except Exception:
                continue
            fields = {
                "verdict",
                "root_cause",
                "reproduction",
                "evidence_quotes",
                "affected_component",
            }
            if isinstance(data, dict) and fields <= set(data):
                return True, str(data["verdict"])
    return False, ""


def row_for(
    out,
    pair,
    slot,
    machine,
    arm,
    attempt,
    job_id,
    hit_cap="0",
    wall_killed="0",
    infra="0",
):
    """Derive one sheet row from a bundle (the single row derivation)."""
    out = Path(out)
    capture = json.loads((out / "capture.json").read_text())
    _u = cost_mod.session_usage(out / "session.jsonl")
    tot, calls, skill_reads = _u["components"], _u["calls"], set(_u["skill_reads"])
    texts = []
    for line in open(out / "session.jsonl"):
        r = json.loads(line)
        m = r.get("message") or {}
        if m.get("role") != "assistant":
            continue
        for c in m.get("content") or []:
            if isinstance(c, dict) and c.get("type") == "text":
                texts.append(c.get("text", ""))
    frozen_cost = cost_mod.cost(tot)

    # contract: the grade step is the authoritative parse (schema_ok +
    # fields) — sheet-row re-parsing with hardcoded mr-234 fields broke
    # the smoke task's contract (2026-10-17 pool rehearsal: the
    # run graded schema_ok True, the row said False). Fallback re-parse
    # kept ONLY for legacy bundles with no graded.jsonl.
    gd = out / "graded.jsonl"
    if gd.is_file():
        schema_ok, verdict = False, ""
        try:
            rec = json.loads(gd.read_text().splitlines()[0])
            schema_ok = bool(rec.get("schema_ok"))
            v = rec.get("verdict")
            verdict = (
                str(v["verdict"])
                if isinstance(v, dict) and v.get("verdict") is not None
                else ""
            )
        except Exception:
            pass
    else:
        schema_ok, verdict = _legacy_contract(texts)

    leak_status = ""
    if (out / "leak-status").exists():
        leak_status = (out / "leak-status").read_text().strip()

    # incidents cell (c3 §4.6 record-only). Bundles from the current runner
    # carry incidents.json; legacy bundles (c2-shaped runner, leak-status only)
    # fall back so old bundles still row cleanly.
    inc_cell = "-"
    if (out / "incidents.json").exists():
        inc = json.loads((out / "incidents.json").read_text())
        mut = (
            len(inc.get("model_destroy", []))
            + len(inc.get("add_model", []))
            + len(inc.get("bootstrap", []))
        )
        other = int(inc.get("gh_commands") or 0) + len(inc.get("projects_paths", []))
        # contract anomaly: run ended normally but produced no valid contract
        # block (cap/wall kills legitimately lack one — flagged in their own
        # columns, not double-counted here)
        contract = 0 if (schema_ok or hit_cap != "0" or wall_killed != "0") else 1
        inc_cell = (
            f"mut={mut};other={other};contract={contract}"
            f";hw={inc.get('home_writes', 0)}"
        )
    elif leak_status:
        inc_cell = f"legacy-leak:{leak_status}"

    row = [
        pair,
        slot,
        machine,
        arm,
        attempt,
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
        capture.get("dur_seconds"),
        capture.get("pi_exit_code"),
        calls,
        tot["input"],
        tot["output"],
        tot["cacheRead"],
        tot["totalTokens"],
        f"{frozen_cost:.6f}",
        schema_ok,
        verdict,
        ",".join(sorted(skill_reads)) or "-",
        leak_status or "-",
        inc_cell,
        hit_cap,
        wall_killed,
        infra,
        job_id,
    ]
    return row


def derive_termination_flags(out):
    """(hit_cap, wall_killed, infra) from bundle evidence, for --regen."""
    out = Path(out)
    cap = wall = infra = "0"
    rs = out / "runner-status"
    if rs.is_file() and rs.read_text().strip() != "done":
        infra = "1"
    capf = out / "capture.json"
    if capf.is_file():
        try:
            ee = json.loads(capf.read_text()).get("early_exit")
            reason = ee.get("reason", "") if isinstance(ee, dict) else str(ee or "")
            if "budget" in reason or "cap" in reason:
                cap = "1"
            elif "wall" in reason or "time" in reason:
                wall = "1"
        except Exception:
            infra = "1"
    if not (out / "session.jsonl").is_file():
        infra = "1"
    return cap, wall, infra


def write_row(target, row):
    header = [
        "pair",
        "slot",
        "machine",
        "arm",
        "attempt",
        "ts",
        "dur_s",
        "pi_exit",
        "calls",
        "in_tok",
        "out_tok",
        "cache_tok",
        "total_tok",
        "cost_frozen_usd",
        "schema_ok",
        "verdict",
        "skills_read",
        "leak_check",
        "incidents",
        "hit_cap",
        "wall_killed",
        "infra",
        "job_id",
    ]
    new = not target.exists()
    # csv.writer quotes fields containing commas (skills_read lists two skill
    # names) — manual ",".join silently shifted every later field (c2 bug,
    # found when the twin-rehearsal comparison mis-parsed its own sheet)
    with open(target, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerow(row)
    print("row:", "; ".join(f"{k}={v}" for k, v in zip(header, map(str, row))))


def _ledger_identity(state):
    """(pair, arm) -> {slot, machine, job} from the state events ledger.

    Pool mode (§3.7 v2): bundles carry only arm, but the events carry
    the coin (slot→arm), the DUT hostname (rig-ready state) and the job
    (submit state) — the regen path reconstructs row identity from them.
    """
    ident: dict = {}
    ev = state / "events.jsonl"
    if not ev.is_file():
        return ident
    slots: dict = {}  # (pair, slot) -> {arm, machine, job}
    for line in ev.read_text().splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("event") == "coin" and "arms" in e:
            for s, a in e["arms"].items():
                slots[(e["pair"], s)] = {"arm": a.get("arm"), "slot": s}
        elif e.get("event") == "state":
            k = (e.get("pair"), e.get("slot"))
            if k in slots:
                if e.get("machine"):
                    slots[k]["machine"] = e["machine"]
                if e.get("job"):
                    slots[k]["job"] = e["job"]
    for k, v in slots.items():
        if v.get("arm"):
            ident[(k[0], v["arm"])] = v
    return ident


def main():
    args = sys.argv[1:]
    sheet = None
    if args and args[-1].startswith("--sheet="):
        sheet = Path(args[-1].split("=", 1)[1])
        args = args[:-1]
    target = sheet or Path(__file__).resolve().parent / "state" / "run-sheet.csv"

    if args and args[0] == "--regen":
        if len(args) < 2:
            sys.exit("usage: sheet-row.py --regen <state_dir> [--sheet PATH]")
        state = Path(args[1]).expanduser()
        ident = _ledger_identity(state)
        best = {}  # (pair, arm, attempt) -> (recovery_key, out_dir)
        for pd in sorted(state.glob("recovered-*/runs/pair-*")):
            if not pd.name.removeprefix("pair-").isdigit():
                continue
            for out in sorted(pd.iterdir()):
                if out.name not in ("on", "off") or not out.is_dir():
                    continue
                try:
                    md = (
                        json.loads((out / "capture.json").read_text()).get(
                            "extra_metadata"
                        )
                        or {}
                    )
                    attempt = int(md.get("attempt") or 1)
                    arm = md.get("arm") or out.name
                    pair = md.get("pair") or pd.name.removeprefix("pair-")
                except Exception:
                    attempt, arm, pair = 1, out.name, pd.name.removeprefix("pair-")
                key = (int(pair), arm, attempt)
                rkey = out.parts[-4]  # recovered-<ts>
                if key not in best or rkey > best[key][0]:
                    best[key] = (rkey, out)
        n = 0
        for pair, arm, attempt in sorted(best):
            out = best[(pair, arm, attempt)][1]
            cap, wall, infra = derive_termination_flags(out)
            idn = ident.get((pair, arm), {})
            row = row_for(
                out,
                pair,
                idn.get("slot", "-"),
                idn.get("machine", "-"),
                arm,
                attempt,
                idn.get("job", "-"),
                cap,
                wall,
                infra,
            )
            write_row(target, row)
            n += 1
        print(f"regen: {n} rows from {state}")
        return

    if len(args) < 7:
        sys.exit(__doc__)
    out, pair, slot, machine, arm, attempt, job_id = args[:7]
    hit_cap, wall_killed, infra = (args[7:10] + ["0", "0", "0"])[:3]
    row = row_for(
        out, pair, slot, machine, arm, attempt, job_id, hit_cap, wall_killed, infra
    )
    write_row(target, row)


if __name__ == "__main__":
    main()
