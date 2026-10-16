# DESIGN — campaign 1: mr-234 paired cost campaign (skills-off vs skills-on)

*Condensed 2026-10-16 for main from the full pre-registration, which is
archived — with the campaign-2 amendments — on the development branch.
Design as run, 2026-10-13 → 2026-10-15. Results: `RESULTS.md` (this
directory). Successor designs: `../campaign2/DESIGN.md` (hardened
protocol), `../campaign3/DESIGN.md` (isolated rig, confirmatory).*

## 1. Objective

Estimate the effect of the skill set on **billed cost per run** — and
secondarily tokens, wall time, and attempt quality — for the full
end-to-end task `mr-234` (a mysql-router-k8s rev916 logrotate/promtail CPU
bug: the agent diagnoses a live juju-deployed MySQL stack and emits a
fenced YAML verdict), and test H₀: identical cost distribution vs H₁: a
difference.

## 2. Frozen protocol

- **Skills frozen** at the campaign-start commit. ON = exactly five skills
  (`juju-cli`, `mysql-charms-source`, `charmed-mysql-fault-injection`,
  `charmed-mysql-juju-inspect`, `charmed-mysql-nightly-triage`); OFF = the
  same runner invocation without `--skills`. No per-skill cherry-picking.
- **Instrument**: model `openrouter/z-ai/glm-5.3-flash:nitro`; pi **1.2.0
  pinned** for every run (sidecar records version + path; any mismatch =
  protocol violation → rerun). `--timeout 7200`; uncapped tool calls (a
  call cap would censor the off arm).
- **P0 before pair 1**: fix the verdict-contract YAML trap (unquoted
  colons burned 2 of 4 anchor runs) — a both-arms change, not a deviation.
- **Environment per run**: fresh juju `testing` model (destroy + add),
  /tmp manifest sweep before every run, pairs back-to-back in one session,
  order by seeded coin (`seed = pair# + 20261013`).
- **Cost** = in×p_in + cacheRead×p_cache + out×p_out at frozen prices
  (p_in $0.0261, p_cacheRead $0.0108, p_out $0.90 per Mtok) — recomputed
  from session usage fields, never provider billing.
- **Post-run leak checks** on the session JSONL (v2 series: campaign-token
  hard patterns, cross-model ops, `gh` commands, real-tree reads), amended
  v2.1–v2.3 mid-campaign, every change disclosed with symmetric re-judging
  of affected bundles.

## 3. Design

**Paired blocks.** One pair = one fresh environment + one skills-off + one
skills-on run, order randomized per pair. Rationale: block effects
(image-cache warmth, provider load, cluster state) cancel exactly in the
within-pair log-cost difference d = ln(cost_off) − ln(cost_on); the
intraclass-ρ argument (pairing halves the required run count at ρ = 0.5)
and the distribution-free floor (minimum attainable two-sided
sign-flip p = 2/2ⁿ, so ≥ 6 pairs are needed to reach α = 0.05) were both
verified against scipy 1.18.1 (exact noncentral-t power cross-checked
against the `scipy.stats.power` simulator, max deviation 0.002 over 27
cells). Costs of pairing: df drops 2n−2 → n−1, and a crashed run orphans
its partner (rerun-the-pair policy).

## 4. Endpoints (pre-registered)

- **Primary**: ln(cost) per run, paired t on d̄, two-sided α = 0.05;
  report the ratio exp(d̄) with a 95% CI.
- **Secondary (descriptive)**: total tokens, wall time, tool calls, skills
  read, `schema_ok`, verdict. Failed-schema runs **stay in** — they are
  paid attempts.
- **Interim rule (amended 2026-10-14, user-directed)**: continue pairs
  until (a) ≥ 80% power at the observed |d̄|, or (b) the 95% CI lies within
  ±ln(1.35) (equivalence), or (c) the 14-pair cap; sequential looks are
  disclosed via the full p-value trajectory per pair count.

## 5. Sample-size plan

Anchor: off/on geometric-mean cost ratio ≈ 4.35× from four historical full
runs (A–D; those used pi 0.85.1 — acceptable, because pairing holds the
instrument constant *within* the campaign); on-arm log-SD ≈ 0.43 as an
upper bound. Grid over σ_d 0.45/0.60 and true ratios 2–5×: 3–10 pairs for
80–90% power. **Plan: 6 pairs (12 runs), interim at 4**, budget ≈ $0.30–0.50
API spend, 5–9 h serial wall time.

## 6. What campaign 1 established

See `RESULTS.md` for the full report. In short:

- At n=10: off/on ratio 1.23× (ON ≈ 19% cheaper), 95% CI [0.89, 1.70],
  p = 0.183 — **favorable, not significant**. The large early-pair effect
  (pairs 1/3/6) did not replicate in pairs 7–10; the pooled estimate shrank
  monotonically as pairs accumulated.
- Off-arm variance dominates the design (5.4× spread across runs);
  resolving a ~1.2× effect at σ_d ≈ 0.45 would need ~38 pairs.
- **Refined parameters for follow-ups** (the §9 amendment, written after
  transcript mining): σ_d ≈ $0.057 absolute; d is a three-regime mixture
  (large savings / ON ~25% costlier / dead-even); 6–13 pairs for 80% power
  at δ ∈ [$0.075, $0.05]. The two cost levers: call hygiene (≈ 5–10% of ON
  cost) and output-token discipline (≥ 10–20%, untested, quality-risky).
- Lessons that shaped campaign 2: failing-fast converges cost (the
  schema-failed runs were d ≈ 0); big positive d's came from off arms that
  *worked harder*; provider drift across the ~14 h window is absorbed by
  pairing, as designed.

## 7. Artifacts

- `RESULTS.md` (this directory) — final wrapped report.
- `run-sheet.csv` (copied alongside) — 24 attempt rows, 20 valid cells.
- Full pre-registration, the deviations log, `invalid-runs/`, `logs/`, and
  the transcript-mining companions — archived on the development branch.
