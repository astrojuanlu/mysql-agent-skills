#!/usr/bin/env python3
"""ANALYSIS-c3.py — the pre-registered c3 analysis (DESIGN §5), committed
before pair 1 (§9/§12; retro P3/B10).

Reads BUNDLES, never the run sheet.

Inputs per attempt bundle (recovered-*/runs/pair-<N>/{on,off}/):
  session.jsonl     usage events → component tokens (walk owned by
                    profe.bundle) + frozen dollars via lib/cost.py (the
                    A4 canonical; prices from lib/frozen-prices.json)
  capture.json      duration, exit, staged skills, attempt metadata
  graded.jsonl      verdict + schema_ok (profe's mechanical grade)
  incidents.json    record-only forensics (§5.1.4 safety inputs)
  grade-depth.json  blind depth grade (rubric diagnose@2) — optional;
                    analysis reports depth availability per arm

Termination classes come from bundle evidence (runner-status, capture
early_exit), NOT from driver heuristics. Attempts without a readable
session are excluded from the paired tests and reported separately (§4.6:
infra reruns vs agent behavior).

Statistics (scipy/numpy-backed via profe.stats — the canonical helper,
retro A4/B10; deterministic; exactness guarantees unchanged — the exact
permutation keeps a meet-in-the-middle enumerator):
  - Family 1/2: exact within-pair sign-flip permutation on the mean of
    ln(off)−ln(on), two-sided, via meet-in-the-middle enumeration —
    EXACT for any n ≤ 40 (no Monte Carlo, no seed). Paired t and
    Wilcoxon (Pratt, tie-corrected, normal approx) as agreement checks.
  - Family 3: exact McNemar on the discordant pairs of
    cost_frozen > $0.10, c3 pairs only.
  - Family 4: per-arm rates with Jeffreys 95% upper bounds; between-arm
    rate difference via the Miettinen–Nurminen unconditional score CI.
    Safety stopping reads the ON-arm bound alone.
  - §5.3 futility looks at n = 10/20/30 (block boundaries): family-1
    ratio CI inside [0.87, 1.15] ⇒ stop. No efficacy stopping; the
    p-trajectory per cumulative pair count is printed.
  - §5.4: leave-one-out, per-block estimates + pooled-with-stratum,
    between-block contrast, price-sensitivity vector, cost decomposition,
    dispersion (MAD ratio, ES50, trimmed variance) described, never
    claimed as tail elimination.

Usage (stats now live in profe — run under the profe env, from the
profe/ directory):
  cd <repo>/profe && uv run python \
    ../eval/phase2/campaign3/ANALYSIS-c3.py \
    [--state RIG_STATE_DIR] [bundle_dir ...] [--json OUT]
  cd <repo>/profe && uv run python \
    ../eval/phase2/campaign3/ANALYSIS-c3.py --selftest

--state scans state/recovered-*/runs/pair-*/  (latest recovery per
pair/arm wins when a pair was recovered more than once).
"""

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "profe" / "src"))
import bundle as bundle_mod
import cost as cost_mod

# the B10 stats helper is the pairedstats workspace member now (2026-10-17
# consolidation: profe/packages/pairedstats) — reached through profe's
# analysis dependency group (`uv run --group analysis`)
from pairedstats import (
    cp_upper,
    jeffreys_upper,
    mcnemar_exact,
    mn_ci_diff,
    paired_t,
    signflip_exact,
    t_quantile_975,
    wilcoxon_pratt,
)

COST_EXCEED = 0.10          # §5.1.3
FUTIL_LO, FUTIL_HI = 0.87, 1.15   # §5.3
BLOCK = 10                  # §5.2 block size
LOOKS = (10, 20, 30)


import pairedstats as stats  # selftest passthrough (retro B10 contract)

# ---------------------------------------------------------------- assembly
def read_bundle(bundle: Path):
    """One attempt record: facts from lib/bundle.py (the one reader).

    Exclusion policy here (paired tests need completed attempts; §4.6).
    Non-current-format bundles (pre-sidecar / five-probe-era shapes)
    cannot be read at all (profe.bundle raises BundleFormatError) — they
    are recorded as excluded with the refusal reasons, never silently
    dropped (ITT bookkeeping).
    """
    try:
        rec = bundle_mod.read(bundle)
    except bundle_mod.BundleFormatError as e:
        pair = None
        if bundle.parent.name.startswith("pair-"):
            tag = bundle.parent.name[len("pair-"):]
            if tag.isdigit():
                pair = tag
        return {"bundle": str(bundle), "pair": pair, "arm": bundle.name,
                "attempt": 1, "machine": "", "dur": None, "pi_exit": None,
                "calls": None, "cost": None, "verdict": "",
                "schema_ok": None, "run_status": "", "early_exit": None,
                "excluded": True, "exclusion": f"unreadable: {e}"}
    rec["excluded"] = False
    rec["exclusion"] = ""
    sess = Path(rec["bundle"]) / "session.jsonl"
    if not sess.is_file():
        rec["excluded"], rec["exclusion"] = True, "no session.jsonl"
        return rec
    if rec["calls"] is None:
        rec["excluded"], rec["exclusion"] = True, "session unreadable"
        return rec
    term = bundle_mod.classify_termination(rec)
    if term != "completed":
        rec["excluded"] = True
        if rec["run_status"] and rec["run_status"] != "done":
            rec["exclusion"] = f"runner-status={rec['run_status']}"
        else:
            ee = rec["early_exit"]
            reason = ee.get("reason", "") if isinstance(ee, dict) else str(ee)
            if "budget" in reason or "cap" in reason:
                rec["exclusion"] = f"cap hit: {reason}"
            elif "wall" in reason or "time" in reason:
                rec["exclusion"] = f"wall: {reason}"
            else:
                rec["exclusion"] = f"early_exit: {reason}"
    return rec


def collect(state=None, bundles=()):
    """Gather attempt records from state dirs and/or explicit bundles."""
    recs = []
    if state:
        for rec_dir in sorted(Path(state).glob("recovered-*")):
            for arm_dir in sorted(rec_dir.glob("runs/pair-*/*")):
                if not arm_dir.is_dir():
                    continue
                if arm_dir.parent.name.startswith("pair-"):
                    tag = arm_dir.parent.name[len("pair-"):]
                    if not tag.isdigit():
                        continue  # rehearsal strays (pair-R etc.) — not c3 data
                recs.append(read_bundle(arm_dir))
    for b in bundles:
        recs.append(read_bundle(Path(b)))
    # dedupe (pair, arm): latest recovery wins (recovered-<TS> sorts by name)
    best = {}
    for r in recs:
        if r["pair"] is None or r["arm"] is None:
            continue
        key = (str(r["pair"]), r["arm"], str(r["attempt"]))
        cur = best.get(key)
        if cur is None or r["bundle"] > cur["bundle"]:
            best[key] = r
    out = [best[k] for k in sorted(best, key=lambda k: (int(k[0]), k[1], k[2]))]
    return out


def pairs_table(recs):
    """Paired (off, on) rows for complete pairs, both arms included."""
    by_pair = {}
    for r in recs:
        if r["excluded"]:
            continue
        by_pair.setdefault(int(r["pair"]), {})[r["arm"]] = r
    rows = []
    for p in sorted(by_pair):
        d = by_pair[p]
        if "off" in d and "on" in d:
            rows.append((p, d["off"], d["on"]))
    return rows


def dvalues(rows, key):
    """§5.1 convention: d = ln(off) − ln(on) per pair.

    Positive = the ON arm used FEWER.
    """
    return [math.log(off[key]) - math.log(on[key])
            for _, off, on in rows if off[key] and on[key]]


def ratio_ci(d):
    """t-based 95% CI on the geometric ratio exp(mean(d))."""
    n = len(d)
    if n < 2:
        return float("nan"), float("nan")
    m = sum(d) / n
    s = math.sqrt(sum((v - m) ** 2 for v in d) / (n - 1))
    half = t_quantile_975(n - 1) * s / math.sqrt(n)
    return math.exp(m - half), math.exp(m + half)


def family_report(name, rows, key):
    """One §5.1 family member: statistic, p, CI, interpretation inputs."""
    d = dvalues(rows, key)
    n = len(d)
    out = {"endpoint": name, "key": key, "n_pairs": n}
    if n == 0:
        out["note"] = "no evaluable pairs"
        return out, None
    mean_d = sum(d) / n
    perm_p = signflip_exact(d)
    tp, _, _ = paired_t(d)
    wx_p = wilcoxon_pratt(d)
    lo, hi = ratio_ci(d) if n >= 2 else (None, None)
    gm = math.exp(mean_d) if abs(mean_d) < 700 else float("nan")
    out.update(mean_log_ratio=mean_d, geo_mean_ratio=gm,
               ratio_ci95=[lo, hi], perm_p=perm_p, paired_t_p=tp,
               wilcoxon_p=wx_p)
    if n < 2:
        out["note"] = "n<2: ratio CI undefined (no df); point estimate only"
    return out, d


def price_sensitivity(rows):
    """Recompute per-pair cost ratios under alternative vectors (§5.4)."""
    base = cost_mod.load_prices()
    alts = {
        "frozen": base,
        "cache_x3": {**base, "cache_read": base["cache_read"] * 3},
        "cache_as_input": {**base, "cache_read": base["input"]},
        "out_x0.5": {**base, "output": base["output"] * 0.5},
        "out_x2": {**base, "output": base["output"] * 2},
        "volume_only": {"input": base["input"], "cache_read": 0.0,
                        "output": 0.0},
    }
    table = {}
    for name, pr in alts.items():
        def c(r, pr=pr, vol_only=(name == "volume_only")):
            if vol_only:
                return r["total_tok"] * pr["input"]
            return (r["in_tok"] * pr["input"]
                    + r["cache_tok"] * pr["cache_read"]
                    + r["out_tok"] * pr["output"])
        ds = [math.log(c(off)) - math.log(c(on))
              for _, off, on in rows if c(off) > 0 and c(on) > 0]
        table[name] = {"mean_log_ratio": sum(ds) / len(ds) if ds else None,
                       "n": len(ds)}
    return table


def dispersion(d):
    """Descriptive dispersion battery (§5.4) — never tail claims."""
    n = len(d)
    if n < 3:
        return {}
    s = sorted(d)
    m = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    dev = sorted(abs(v - m) for v in d)
    es50 = dev[n // 2] if n % 2 else (dev[n // 2 - 1] + dev[n // 2]) / 2
    mad = es50
    keep = s[int(0.1 * n): n - int(0.1 * n)] or s
    tm = sum(keep) / len(keep)
    tv = sum((v - tm) ** 2 for v in keep) / max(1, len(keep) - 1)
    return {"median_d": m, "MAD": mad, "MAD_sigma": mad / 0.6745,
            "ES50": es50, "trimmed_var_10pct": tv}


def leave_one_out(rows, key):
    """§5.4 leave-one-out stability for one family member."""
    flips = []
    for i in range(len(rows)):
        sub = rows[:i] + rows[i + 1:]
        if len(sub) < 2:
            continue
        p_full = signflip_exact(dvalues(rows, key))
        p_sub = signflip_exact(dvalues(sub, key))
        if (p_full < 0.05) != (p_sub < 0.05):
            flips.append(rows[i][0])
    return flips


# ---------------------------------------------------------------- main
def analyze(recs):
    """The full §5 report for one set of attempt records."""
    rep = {}
    rows = pairs_table(recs)
    excluded = [r for r in recs if r["excluded"]]
    rep["attempts_total"] = len(recs)
    rep["attempts_excluded"] = [
        {"pair": r["pair"], "arm": r["arm"], "attempt": r["attempt"],
         "reason": r["exclusion"]} for r in excluded]
    rep["complete_pairs"] = len(rows)

    fam1, d_calls = family_report("1 tool calls", rows, "calls")
    fam2, d_tok = family_report("2 total tokens", rows, "total_tok")
    rep["family"] = [fam1, fam2]

    # fixed sequence: 2 evaluated only if 1 passed (§5.1)
    if fam1.get("perm_p", 1) >= 0.05:
        rep["family_sequence_note"] = (
            "family member 2 not passed (fixed sequence: member 1 did not "
            "reject; member 2 is evaluated only after member 1 passes)")
        for f in rep["family"][1:]:
            f["sequence_gate"] = "not reached (reported for transparency)"

    # family 3: cost exceedance, McNemar (c3 pairs only)
    b = sum(1 for _, o, n in rows if (o["cost"] or 0) <= COST_EXCEED
            and (n["cost"] or 0) > COST_EXCEED)
    c = sum(1 for _, o, n in rows if (o["cost"] or 0) > COST_EXCEED
            and (n["cost"] or 0) <= COST_EXCEED)
    rep["family3_cost_exceedance"] = {
        "threshold_usd": COST_EXCEED,
        "off_only_exceeds": c, "on_only_exceeds": b,
        "discordant": b + c, "mcnemar_exact_p": mcnemar_exact(b, c),
        "note": "evaluated on c3 pairs only (§5.1.3)"}

    # family 4: behavior safety (§5.1.4) — the endpoint is CONTRACT
    # ANOMALIES. 2026-10-18 operator decision: in-machine environment
    # mutations are NOT recorded (machines freshly minted under pool
    # mode); the sheet's mut cell stays a legacy zero.
    def arm_stats(arm):
        rs = [r for r in recs if r["arm"] == arm and not r["excluded"]]
        n = len(rs)
        contra = sum(1 for r in rs if r["schema_ok"] is False)
        return {"n": n,
                "contract_anomalies": contra,
                "contract_anomaly_rate": contra / n if n else None,
                "jeffreys_upper_contract": jeffreys_upper(contra, n) if n else None,
                "cp_upper_contract": cp_upper(contra, n) if n else None}
    on_s, off_s = arm_stats("on"), arm_stats("off")
    mn = mn_ci_diff(on_s["contract_anomalies"], on_s["n"],
                    off_s["contract_anomalies"], off_s["n"]) \
        if on_s["n"] and off_s["n"] else (None, None)
    rep["family4_safety"] = {
        "on": on_s, "off": off_s,
        "rate_diff_contract_MN_CI": list(mn),
        "note": "endpoint = contract anomalies (mutations not recorded, "
                "2026-10-18); the rule reads CLOPPER–PEARSON (conservative), "
                "Jeffreys reported alongside; stopping reads the ON-arm "
                "bound alone (§5.1.4)"}

    # descriptives (§5.1 secondary)
    def per_arm(arm):
        rs = [r for r in recs if r["arm"] == arm and not r["excluded"]]
        if not rs:
            return {}
        cost_sum = sum(r["cost"] or 0 for r in rs)
        return {
            "n": len(rs),
            "cost_usd_frozen": cost_sum,
            "out_tok_per_call": (sum(r["out_tok"] for r in rs)
                                 / max(1, sum(r["calls"] or 0 for r in rs))),
            "pace_s_per_call": (sum(r["dur"] or 0 for r in rs)
                                / max(1, sum(r["calls"] or 0 for r in rs))),
            "usd_per_s": cost_sum / max(1e-9, sum(r["dur"] or 0 for r in rs)),
            "schema_ok_rate": (sum(1 for r in rs if r["schema_ok"])
                               / len(rs)),
            "verdicts": sorted({r["verdict"] for r in rs}),
            "skills_read_any": sorted({s for r in rs for s in r["skills_read"]}),
            "depth_grades": [r["depth"] for r in rs if r["depth"] is not None],
            "evidence_strength": [r["evidence_strength"] for r in rs
                                  if r["evidence_strength"] is not None],
        }
    rep["descriptive"] = {"on": per_arm("on"), "off": per_arm("off")}
    if rows:
        rep["descriptive"]["price_sensitivity"] = price_sensitivity(rows)
        # decomposition + per-pair table
        per_pair = []
        for p, o, n in rows:
            co, cn = o["cost"] or 0, n["cost"] or 0
            row = {"pair": p,
                   "calls_off": o["calls"], "calls_on": n["calls"],
                   "tok_off": o["total_tok"], "tok_on": n["total_tok"],
                   "cost_off": round(co, 6), "cost_on": round(cn, 6),
                   "dur_off": o["dur"], "dur_on": n["dur"],
                   "verdict_off": o["verdict"], "verdict_on": n["verdict"],
                   "depth_off": o["depth"], "depth_on": n["depth"]}
            if co > 0 and cn > 0 and o["calls"] and n["calls"]:
                row["log_cost_ratio"] = math.log(cn / co)
                row["log_calls_ratio"] = math.log(o["calls"] / n["calls"])
                row["log_percall_ratio"] = (row["log_cost_ratio"]
                                            - row["log_calls_ratio"])
            per_pair.append(row)
        rep["per_pair"] = per_pair
        # blocks + robustness
        blocks = {}
        for p, o, n in rows:
            blocks.setdefault((p - 1) // BLOCK, []).append(
                math.log(o["calls"]) - math.log(n["calls"]))
        bstats = {bk: {"n": len(v), "mean_d": sum(v) / len(v)}
                  for bk, v in sorted(blocks.items())}
        means = [v["mean_d"] for v in bstats.values()]
        if len(means) > 1:
            bstats["between_block_contrast"] = max(means) - min(means)
        rep["blocks_calls"] = bstats
        if len(d_calls or []) >= 3:
            rep["dispersion_calls"] = dispersion(d_calls)
            rep["leave_one_out_calls_flips"] = leave_one_out(rows, "calls")
        if len(d_tok or []) >= 3:
            rep["leave_one_out_tokens_flips"] = leave_one_out(rows, "total_tok")

    # §5.3 stopping: futility looks + p-trajectory (campaign order)
    traj = []
    for k in range(1, len(rows) + 1):
        sub = rows[:k]
        d1 = dvalues(sub, "calls")
        d2 = dvalues(sub, "total_tok")
        traj.append({"n": k,
                     "f1_perm_p": signflip_exact(d1) if d1 else None,
                     "f2_perm_p": signflip_exact(d2) if d2 else None})
    rep["p_trajectory"] = traj
    fut = []
    for k in LOOKS:
        if len(rows) >= k:
            lo, hi = ratio_ci(dvalues(rows[:k], "calls"))
            fut.append({"n": k, "ratio_ci95": [lo, hi],
                        "futility": (FUTIL_LO <= lo and hi <= FUTIL_HI)})
    rep["futility_looks"] = fut
    return rep


def selftest():
    sys.exit(0 if stats.selftest() else 1)


def main():
    if "--selftest" in sys.argv:
        selftest()
    args = [a for a in sys.argv[1:]]
    jout = None
    if "--json" in args:
        i = args.index("--json")
        jout = args[i + 1]
        del args[i:i + 2]
    state = None
    if "--state" in args:
        i = args.index("--state")
        state = args[i + 1]
        del args[i:i + 2]
    bundles = [a for a in args if not a.startswith("-")]
    recs = collect(state, bundles)
    if not recs:
        print("no bundles found (nothing to analyze)")
        sys.exit(1)
    rep = analyze(recs)
    rep["provenance"] = {
        "script": str(Path(__file__).resolve()),
        "prices": str(cost_mod.PRICES_PATH),
        "rule": "every reported number derives from bundle records via "
                "lib/cost.py — never the run sheet (retro B10/A4)",
    }
    try:
        import subprocess
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=Path(__file__).parent,
            capture_output=True, text=True, timeout=5, check=False).stdout.strip()
        rep["provenance"]["repo_head"] = sha or "uncommitted"
    except Exception:  # noqa: BLE001 (provenance is best-effort, never fatal)
        rep["provenance"]["repo_head"] = "unknown"
    text = json.dumps(rep, indent=1, default=str)
    if jout:
        Path(jout).write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
