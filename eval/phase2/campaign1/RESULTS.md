# RESULTS — mr-234 paired cost campaign (FINAL, wrapped 2026-10-15 ~08:40 CEST)

*Ported to main 2026-10-16. The full campaign tree — pre-registration,
deviations log, invalid-run bundles, runner logs, transcript-mining
companions — is archived on the development branch; `run-sheet.csv` is
copied alongside this file.*

Experiment wrapped at the user's decision after pair 10 (pair 11
abandoned mid-arm; see Stopping). Frozen prices: p_in $0.0261/M,
p_cacheRead $0.0108/M, p_out $0.90/M. Model `openrouter/z-ai/glm-5.3-flash:nitro`,
pi 1.2.0, `--timeout 7200`.

## Headline

**No statistically significant cost effect at n=10.** Point estimate
favours skills-ON (off/on ratio 1.23×, i.e. the on-arm runs ~19%
cheaper), but the paired t-test is far from significance
(p=0.183) and the 95% CI [0.89, 1.70] includes 1. The large early-pair
effect (pairs 1/3/6: d≈+0.80–0.86) did not replicate in pairs 7–10
(all d ≈ 0 or negative); the pooled estimate shrank monotonically as
pairs accumulated.

## Primary endpoint (ln-cost, paired t, pre-registered)

| statistic | value |
|---|---|
| pairs (complete, both arms valid) | 10 |
| d̄ = mean(ln cost_off − ln cost_on) | **+0.206** |
| σ_d | 0.451 |
| t(9) | 1.443 |
| p (two-sided) | **0.183** |
| sign-flip permutation p (sensitivity, B=200k) | 0.209 |
| geometric ratio off/on | 1.229× (on/off 0.814) |
| 95% CI on ratio | [0.890, 1.697] |
| approx power at observed effect, n=10 | 0.30 |
| n needed for 80% power at observed effect | ≈ 38 (unreachable; cap was 14) |

p trajectory (amended §8 reporting): n=4 → 0.22, n=5 → 0.21,
n=6 → 0.095, n=7 → 0.151, n=8 → 0.133, n=9 → 0.132, n=10 → 0.183.

## Per-pair data (frozen-price costs, USD)

| pair | on | off | d = ln(off/on) | order (coin) |
|---|---|---|---|---|
| 1 | 0.0543 | 0.1211 | +0.803 | off-first |
| 2 | 0.1248 | 0.1441 | +0.144 | off-first |
| 3 | 0.1001 | 0.2370 | +0.862 | off-first |
| 4 | 0.0545 | 0.0438 | −0.217 | off-first |
| 5 | 0.1183 | 0.1193 | +0.008 | off-first |
| 6 | 0.0625 | 0.1434 | +0.831 | on-first |
| 7 | 0.0993 | 0.0782 | −0.239 | on-first |
| 8 | 0.0630 | 0.0685 | +0.083 | on-first |
| 9 | 0.0699 | 0.0699 | −0.001 | on-first |
| 10 | 0.0558 | 0.0450 | −0.216 | on-first |

## Secondary endpoints

- **Schema contract:** off-arm failures 2/10 (pairs 2, 5 — the
  dropped `- ` dash mode), on-arm failures **0/10**.
- **Verdict agreement:** 18/18 graded runs `charm-bug` (the 2
  schema-failed runs produced no verdict; no disagreements anywhere).
- **skills_read:** on-arm runs read `mysql-operators-source` and/or
  `juju-cli` as designed; off arms `-` (no skills).

## Cost & time

- Valid-run API spend: **$1.873** (on $0.802, off $1.070) — above the
  $0.30–0.50 design budget, driven by the extension to 10 pairs and by
  off-arm variance (off range $0.044–$0.237, 5.4×).
- Valid run time: **15.7 h** of agent wall time, serial.
- Quarantined attempts added partial costs (archived: `invalid-runs/`).

## Stopping

The user wrapped the experiment after pair 10. Under the amended §8
rule (pair until 80% power / equivalence / 14-pair cap) the campaign
would have continued: power was 0.30 at n=10 and the effect estimate
was *shrinking* (d̄: 0.405 → 0.206 from n=6 → n=10), making 80% power
unreachable within the cap. Pair 11 was abandoned mid-arm after four
failed attempts (1 leak-invalid + 2 watchdog false positives + 1
user-interrupted); it contributes no data and its single sheet row
(leaks=FAILED) is excluded from all statistics above.

## Interpretation (with caveats)

1. The skills' measurable cost benefit is **small and unstable** at
   this task/difficulty: the strong early signal (3 of the first 6
   pairs) decayed to noise-equivalent by pairs 7–10.
2. Off-arm cost variance (5.4× spread) dominates the design; a
   paired-capped study at n=10 cannot resolve a ~1.2× effect against
   σ_d≈0.45. Resolving it would need ~38 pairs at these variances.
3. Heterogeneity note: the two schema-failed off runs (2, 5) had d≈0 —
   failing-fast converges cost; the big positive d's came from
   off-runs that worked *harder* (more calls, more tokens).
4. Provider-side drift across the ~14 h window (cheaper later runs in
   both arms) is absorbed by pairing, as designed.
5. No contamination: every graded run passed leak-check (v2.x series);
   one mid-run harness mutation (pair-11/off r1) was caught by the
   instrument and excluded.

## Run integrity ledger

- 20 valid graded runs (10 pairs × 2 arms), all `leaks=ok`.
- Quarantined/excluded attempts (all archived in `invalid-runs/`, rows
  marked): pair-1/off pi-1.2 parse (historical), pair-4/off r1 (7200 s
  timeout, gh-rule held), pair-4/off r2 (stream truncation),
  pair-5/off r1 (7200 s hang), pair-6/off r1 (order violation),
  pair-6/on r2 (provider hang), pair-6/off r2 (pre-v2.6 audit →
  superseded), pair-9/off r1 (stall — retrospectively a watchdog false
  positive), pair-11/off r1 (agent destroyed harness model —
  leak-invalid), pair-11/off r2/r3 (watchdog false positives),
  pair-11/off r4 (user-interrupted wrap-up).
- Runner hardenings shipped during the campaign: watchdog v2→v3
  (10→25 min; poll-loop false positives), pre-flight force-destroy,
  self-model cleanup, sweep tolerance. All disclosed in the campaign
  deviations log (archived on the development branch).

## Artifacts

- `run-sheet.csv` (copied alongside) — 24 attempt rows, 20 valid cells
  (last-row-per-cell).
- Full pre-registration, `DEVIATIONS.md` (event log),
  `PENDING-SKILL-EDITS.md`, `invalid-runs/`, `logs/`, and the
  transcript-mining companions — archived on the development branch.
- Provenance: coins reproducible via
  `python3 -c "import random; random.seed(<pair#>+20261013); print(random.choice(['off-first','on-first']))"`.
