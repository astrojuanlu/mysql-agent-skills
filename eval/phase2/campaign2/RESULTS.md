# RESULTS — campaign 2 (c2): mr-234 paired cost campaign (FINAL, wrapped 2026-10-16)

*Ported to main 2026-10-16 from the development branch. The port notes at
the end list the differences vs the committed branch analysis (two
corrections, one ported addendum, one addition). The full campaign tree —
deviations log,
per-pair bundles, leak-check battery records — is archived on the
development branch; `run-sheet.csv` (26 accepted rows) is copied alongside
this file.*

Wrapped at the full pre-registered budget: **13/13 pairs**, order per the
seeded pair coin, one accepted sample per slot. Instrument: driver v2.2 +
`run-cost-campaign-c2.sh`, skills frozen (juju-cli 0.12.0,
mysql-charms-source 0.6.0, plus the three charmed-mysql skills), model
`openrouter/z-ai/glm-5.3-flash:nitro`, pi 1.2.0, frozen prices
0.0261/0.90/0.0108 $/Mtok (in/out/cache). Convention:
**d = cost(on) − cost(off)**; negative ⇒ the skills arm is cheaper.

## Headline

**The skills-on arm reduced per-slot model cost on average** — d̄ = −$0.0329
(≈ 38% of the off-arm mean $0.0859), median −$0.0176, 9/13 pairs favouring
on, paired-t one-sided p = 0.036. The two-sided 95% CI [−$0.0692, +$0.0034]
still touches zero: the effect is borderline-significant, tail-carried, and
cut-dependent — but its direction and median are stable in every
pre-committed sensitivity cut. **The on arm never mutated the host; every
host mutation and all baseline drift in the campaign originated from
off-arm attempts.**

## Primary endpoint (ITT, n = 13 pairs)

| stat | value |
|---|---|
| d̄ (mean) | **−$0.0329** |
| median | −$0.0176 |
| IQR | [−$0.0681, +$0.0004] |
| σ_d | $0.0601 |
| paired t | −1.974 (df 12), **one-sided p = 0.036** (two-sided 0.072) |
| 95% two-sided CI | [−$0.0692, +$0.0034] |
| one-sided 95% upper bound | −$0.0032 |
| pairs favouring on | 9/13 |

## Per-pair data (frozen-price costs, USD)

| pair | on | off | d = on − off | order (coin) |
|---|---|---|---|---|
| 1 | 0.0500 | 0.0813 | −0.0313 | off-first |
| 2 | 0.0670 | 0.0666 | +0.0004 | off-first |
| 3 | 0.0346 | 0.1060 | −0.0714 | off-first |
| 4 | 0.0422 | 0.2268 | −0.1846 | on-first |
| 5 | 0.0469 | 0.1273 | −0.0804 | off-first |
| 6 | 0.1098 | 0.0474 | +0.0625 | on-first |
| 7 | 0.0406 | 0.1087 | −0.0681 | on-first |
| 8 | 0.0460 | 0.0635 | −0.0176 | on-first |
| 9 | 0.0763 | 0.0621 | +0.0142 | on-first |
| 10 | 0.0423 | 0.0312 | +0.0111 | on-first |
| 11 | 0.0365 | 0.0523 | −0.0157 | off-first |
| 12 | 0.0512 | 0.0873 | −0.0361 | on-first |
| 13 | 0.0458 | 0.0564 | −0.0107 | off-first |

> d₉ sign correction vs the interim ledger: the raw rows give on $0.076288 −
> off $0.062126 = **+$0.0142** (on more expensive). All final statistics are
> recomputed from the raw sheet.

## Secondary endpoints

- **Output tokens** (the dominant cost component at the frozen prices):
  mean 43,830 (off) vs 34,250 (on) = **1.28×**.
- **Tool calls**: mean 104 (off) vs 56 (on) = **1.85×** *(port addition;
  computed from the same sheet — the strongest effect in the external
  reviews of the interim n = 10 data)*.
- **Schema-valid verdicts**: off 12/13, on 11/13 — pairs 12/off, 9/on and
  13/on gave up short of a valid contract and are kept as slot samples per
  the pre-registered ITT policy; all 23 valid verdicts are `charm-bug`.
- **Dispersion**: all 13 on-costs lie inside [$0.035, $0.110]; the off arm
  has two > $0.10 outliers (pairs 4, 5).
- **Skill adoption**: all 13 on-arms listed and read `juju-cli` +
  `mysql-charms-source` within the first few tool calls.

## Sensitivity analyses (pre-committed cuts)

| cut | n | d̄ | median | t | one-sided p |
|---|---|---|---|---|---|
| ITT (primary) | 13 | −$0.0329 | −$0.0176 | −1.97 | **0.036** |
| clean-ops (excl 3, 4) | 11 | −$0.0156 | −$0.0157 | −1.31 | 0.110 |
| healthy-rig (excl 3) | 12 | −$0.0297 | −$0.0167 | −1.67 | 0.062 |
| schema-valid-only (excl 9, 12, 13) | 10 | −$0.0395 | −$0.0244 | −1.87 | 0.047 |

The point estimate is negative in every cut (−$0.016…−$0.040) and the median
is stable (−$0.016…−$0.024); significance is borderline and cut-dependent
(ITT and schema-valid-only pass one-sided α = 0.05; clean-ops does not).
The two stormiest off-arms (pairs 3, 4) carry much of the mean — the tail,
not the centre.

## Pre-registered vs realised power

Committed: n = 13 for 80% power at δ = $0.05 assuming σ_d = $0.057.
Realised σ_d = $0.0601 → post-hoc power at the hypothesised δ ≈ 88%
(one-sided α) / 79% (two-sided). The realised effect (−$0.0329) was smaller
than hypothesised, so the campaign lands just inside one-sided significance
rather than comfortably.

## Incidence (attempt level and accepted-sample level)

| event | off arm | on arm |
|---|---|---|
| attempts that mutated the host | 6 (p3 r1 chmod /etc/passwd — quarantined; p4 microk8s; p7 faketime; p8 kubectl; p11 r1+r2 strace; p12 r1 strace + docker — INFRA-killed) | **0** |
| accepted samples with own-agent landed mutation | 4 (p4, p7, p8, p11) | **0** |
| accepted samples run on drifted baseline (strace left by 12/off r1) | 2 (12/off r2, 13/off) | 1 (13/on — symmetric within pair 13; pairing absorbs) |
| genuine leak-FAIL → rerun | 0 | 3 (5/on real-tree read; 10/on aliased real-tree; 11/on campaign-model teardown) |
| false-positive leak-FAIL (re-judged CLEAN under v2.8, 28-bundle regression battery) | 1 (2/off r1) | 1 (11/on r2) |
| INFRA-FAIL (watchdog stall), not consumed | 2 (11/off r1, 12/off r1 — both left drift) | 0 |
| TIMEOUT-KEEP (7200 s cap, §10.5) | 1 (12/off r2, $0.0873) | 0 |
| schema-invalid kept as slot sample | 1 (12/off) | 2 (9/on, 13/on) |

Every host mutation and all baseline drift originated from off-arm
attempts; the on arm's accepted samples show zero own-agent mutations
(0/13).

## Disclosures

- §10.5 ITT extensions (wall-clock hits kept; schema-invalid completions
  kept as slot samples) and §10.6 (leak-check v2.8: standalone-token
  mentions, 28-bundle regression battery — 20 accepted bundles re-stamped
  CLEAN, 5 genuine FAILs unchanged, 2 false positives re-judged CLEAN) were
  followed throughout.
- **strace carryover**: installed by 12/off r1 (watchdog-killed before the
  attempt-end restore could fire — the known restore-on-kill procedure
  gap); present at the baselines of 12/off r2, 13/off, 13/on; classified
  mild (passive tracer, no channel impact, symmetric across pair 13) —
  runs not invalidated, cost counted (ITT), purged post-campaign.
- **docker snap**: installed by 12/off r1, never used (read-only inventory
  probes only); purged mid-attempt by operator order under a
  pre-registered degradation rule (moot — the run died as a watchdog
  stall).
- **06:22Z monitor error**: a monitoring agent killed the live driver on
  a misread instruction; recovered by procedure, no data affected.
  Standing rule: monitor turns are read-only.
- **Time trend**: the paired difference attenuated over the campaign
  (pairs 1–5 mean −$0.074; pairs 6–10 ≈ $0.000; pairs 11–13 −$0.021;
  Spearman ρ(d, pair index) = +0.31) — treated as a candidate
  rig/template-drift finding, addressed by the frozen-block design in
  `../campaign3/DESIGN.md`.
- Nothing redacted; the full deviations log is archived on the development
  branch.

## Scale and sidedness — reconciling the p-values

The same 13 paired differences support four summary statistics (2 scales ×
2 sidedness); three of them circulate, which reads as disagreement but is
not:

| scale | sidedness | statistic | p | pre-registration |
|---|---|---|---|---|
| ln-cost (geometric) | two-sided | t = −2.19, gm ratio ON/OFF **0.663**, 95% CI [0.441, 0.998] | **0.049** | campaign-1 primary (`../campaign1/DESIGN.md` §4) |
| ln-cost | one-sided | same t | 0.025 | — |
| absolute USD (this file's primary) | two-sided | t = −1.97, CI [−$0.0692, +$0.0034] | 0.072 | campaign-2 protocol (`DESIGN.md` §5 in this directory) |
| absolute USD | one-sided | same t | 0.036 | — (2 × 0.036 = 0.072) |

Why the scales differ: costs are multiplicative and the pair baselines are
heterogeneous ($0.03–$0.23) — the very heterogeneity that made the
campaign-1 follow-up refinement freeze absolute USD (relative-scale
σ ≈ 38%). On the ln scale the big-savings pairs (3, 4) contribute heavily
(ratios 0.33, 0.19) and the anti-on pair 6 (×2.32) is the main brake →
t = −2.19. On the USD scale pair 4's −$0.185 dominates the mean while
pair 6's +$0.0625 is proportionally smaller → t = −1.97. Same direction,
same 9/13 win count; the ln scale is mildly more significant because it
weights the multiplicative evidence symmetrically.

Governing rule: **absolute USD is the campaign-2 primary** (the
pre-registered protocol this campaign ran under), so 0.072/0.036 are this
campaign's primary values. The ln-cost analysis (0.049 two-sided / 0.025
one-sided, geometric-mean ratio 0.663) is the campaign-1 pre-registered
scale run on campaign-2 data for continuity — and it is the *stronger* of
the two, so the USD-led presentation above is the conservative one.

## Verdict (per the design's §10.2 recipe)

The skills-on arm reduced per-slot model cost on average (d̄ = −$0.0329,
≈ 38% of the off-arm mean $0.0859), median −$0.0176, 9/13 pairs favouring
on, one-sided paired-t p = 0.036. Direction and magnitude are consistent
across all four analysis cuts; formal significance holds in the ITT and
schema-valid-only cuts but not in clean-ops. Zero on-arm host mutations vs
four off-arm accepted samples with own-agent landed mutations. The
campaign's rig-hygiene incidents (two watchdog INFRA-kills with carryover,
one TIMEOUT-KEEP, three schema-invalid keeps) are documented and do not
change the conclusion, though they widen the uncertainty band the CI
already shows.

Spend: **$1.8062** accepted samples (26 runs) + ≈ $0.36 in
rejected/unconsumed attempts — inside the $5.40 budget.

## Port notes (differences vs the committed branch analysis)

1. **Corrected**: the branch verdict reads "≈ 32% of the off-arm mean
   $0.0977" — the off mean of the 26 accepted rows is **$0.0859**, making
   the saving ≈ **38%**. The $0.0977 figure does not reproduce from the
   sheet (and is inconsistent with the doc's own $1.8062 spend line).
   Recommend the same correction on the development branch.
2. **Corrected**: the branch incidence table's "accepted samples with host
   mutation" lists p3's chmod (that attempt was quarantined and the arm
   rerun under the pre-registered taxonomy) and omits p11's strace (which
   landed in the accepted r2) — same count, different membership. Its
   "drifted baseline" row also shows on-arm 0 while the strace disclosure
   itself names 13/on as affected; corrected here to 1 (symmetric within
   pair 13). The branch power line "≈ 84%" is restated here as the two
   code-verified readings (88% one-sided / 79% two-sided).
3. **Ported from the branch's follow-up addendum**: the scale × sidedness
   reconciliation section (above), numbers identical — only the
   pre-registration pointers are adapted to this tree's documents.
4. **Added**: mean tool calls (104 vs 56, 1.85×) under secondary
   endpoints — absent from the branch analysis, computed from the same
   sheet.
