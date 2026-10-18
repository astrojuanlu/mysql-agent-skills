# Campaign 3 — retrospective and feasibility post-mortem

2026-10-18 · written after the stop decision (PR canonical/mysql-operators#514
comment, same day). This document is a kit artifact, not a frozen
pre-registration — it is the honest record of why the campaign stopped and
what a feasible successor looks like.

## 1. Outcome

**Stopped before any paired observation existed.** The campaign ran two
DUT attempts, neither of which produced a completed arm:

- pair 1/A (skills-ON): attempt 1 INFRA_FAILED in 12 s — the lab job had
  failed at MAAS; the v4 fast-check amendment detected it (the one
  unambiguous operational win of the campaign: seconds instead of the
  45-minute silent burn the pre-amendment harness would have suffered);
  attempt 2 was cancelled at the stop decision before any DUT activity.
- pair 1/B (skills-OFF): HIT_CAP at 181 calls / ~10.0 M tokens /
  $0.1778 frozen after ~1.7 h — the OFF arm blew through half the token
  budget and 72% of the call budget without resolving the bug.

Live campaign wall time: ~2 h. Total effort including rehearsals
(v1 rig rehearsal, v3 rehearsal, pool smoke pairs 5–7), the v4 amendment,
and the statistics/design pass: 2026-10-16 → 10-18, i.e. the better part
of three days, essentially all of it babysitting-and-building rather than
measuring.

**ITT ledger**: 2 attempts, 0 completions. **Statistical yield: none.**
The campaign never reached the first futility look, let alone the first
paired difference.

## 2. What the two attempts suggest (n=1, no statistics attached)

The only completed DUT observation is a skills-OFF run capping out at
~10 M tokens on mr-234 — while every rehearsal skills-ON run on the same
task class resolved within 85 calls / ~$0.07 / ~1 h. Direction is
consistent with campaign-2's finding (geometric cost ratio ON/OFF 0.663,
p=0.049 at n=13, paired t on ln-cost — i.e. the skills effect was already
*borderline established* before campaign 3 started). But one capped run is
an anecdote, and cap-hits are exactly why the ITT policy freezes cost at
min(observed, cap): we cannot distinguish "OFF is 3× worse" from "OFF is
1.5× worse" from a single truncated observation. No conclusions are drawn.

## 3. Root-cause analysis: where the cost actually went

The design needed ~30 paired runs because the effect, while real, is
moderate (a ~34% geometric reduction, not a 5× one), and each observation
was engineered to be maximally trustworthy:

1. **Each observation = a full-resolution DUT run on a leased physical
   machine.** Submit → MAAS deploy (~10–15 min) → rig build (~8 min) →
   agent run (~1–2.3 h) → recovery → blind grading. ~3 h wall and
   ~$0.10–0.18 of model spend per attempt, × 2 arms, × 30 pairs,
   sequentially = **5–8 days of continuous, attended operation**.
2. **Every component in the loop was unreliable, so the harness grew
   reliability machinery.** MAAS deploy failures, 10-minute machine
   handout variance, dirty-machine bugs (reused machines keeping the
   previous attempt's k8s cluster — bug 10), agent restarts mid-attempt,
   cap-hit recovery, stream stalls. Each rehearsal found new failure
   modes; the harness's answer was more pre-registration, more watchdogs,
   more recovery paths. The campaign3 kit alone is ~3.4 k SLOC — and the
   complexity was not gratuitous, it was *load-bearing*: every piece
   anticipated a failure we actually hit.
3. **The statistical machinery was chosen for confirmatory rigor beyond
   the question's needs.** Exact sign-flip permutation, McNemar,
   Clopper–Pearson bounds, Miettinen–Nurminen — to confirm an effect that
   campaign 2 had already measured with a plain paired t-test at n=13.
   None of it was ever exercised: the campaign died before the first
   analysis read.
4. **The futility design had no realistic early exit.** Stopping for
   futility required the calls-ratio CI to fall entirely inside
   [0.87, 1.15] (±13%) at n=10 — with the observed per-pair variance that
   bound was unreachable, so the plan's only real exits were "finish 30
   pairs" or "abort". A design that cannot stop early cannot de-risk
   itself.

The deeper lesson, matching the PR comment: **the measurement instrument
(leased machines + full runs + confirmatory stats) was an order of
magnitude more expensive than the question being asked.** The skills
either help an agent use fewer calls or they don't; that question does
not intrinsically require a physical-machine lab.

## 4. What worked (keep in any successor)

- **Freeze-then-run**: pre-registration with manifest hashing,
  distinguishable stamps, delta-log sign-offs. Zero ambiguity afterwards
  about what was promised.
- **The smoke-task rehearsal discipline**: caught the dirty-machine bug
  and the runbook drift before the campaign, cheaply.
- **The fast-check amendment**: the single best hour spent in the
  campaign — converted a 45-minute silent failure into a 12-second
  detectable one.
- **The ledger/ITT discipline**: cap-hits and infra failures were recorded
  truthfully and would have stayed in the analysis.
- **Paired twins as a variance-reduction idea** — right idea, wrong price
  point (see below).

## 5. What didn't (kill list)

- **Physical-machine leasing per attempt** — 100% of the operational
  failures and most of the wall time; zero contribution to the validity
  of the *agent-behavior* endpoint.
- **Full-resolution endpoint** (fix + verify + recover) — the effect
  lives in call counts, which are visible long before resolution.
- **Confirmatory exact-test stack** — the knowledge cost alone was
  disqualifying, and it was never used.
- **Hand-built babysitting** (monitoring cycles, recovery loops,
  amendment workflows) — symptom of 1–3, not a separate problem.

## 6. Simplification proposals, ordered by leverage

**P1 — Take the fleet out of the measurement loop.** Run the DUT against
a local VM rig instead of a leased Testflinger machine. This removes
the leasing layer only: MAAS deploy failures, 10-minute handout
variance, queue contention, cross-machine hygiene (bug 10), the
45-minute silent-failure class. That is real leverage — but it is the
*smaller* half of the environment problem, and an earlier draft of this
retro overstated it. What P1 does **not** remove, confirmed against the
record: (a) **the DUT dirties the rig by design** — campaign 2 logged 6
host-mutating attempts (chmod /etc/passwd, microk8s, faketime, kubectl,
strace, docker) and 2 accepted samples on a drifted baseline; bug 10 is
the reuse collision that motivated the hard-reset stage. Every attempt
therefore needs a reset-to-clean, no matter where it runs. (b) A
**container boundary is strictly worse** here: the DUT's host-level
mutations (snap installs, /etc/passwd, strace) would poison the shared
host for every later attempt. (c) A **VM snapshot restore** amortizes
the juju/k8s bootstrap into image builds rather than eliminating it,
and swaps known costs for new drift surfaces: juju controller-DB
consistency after revert, certificate/clock staleness (the faketime
incident shows time state was already a live hazard), snap revisions
frozen against a moving store, image rot between rebuilds. Realistic
budget: ~2–5 min restore per attempt plus a periodic snapshot-rebuild
tax, and a drift audit between attempts (the kit's rig-audit idea,
§10.3 of the tooling retro) as a first-class stage — not "unattended
minutes". The successor pilot must measure the reset flake rate before
teing anything on it: same rehearsal discipline as the fleet, new
surface, same expectation that every rehearsal finds a new bug. The
honest claim is narrower than "removes the lab": P1 trades a fleet
babysitting problem for a snapshot-hygiene problem — smaller, local,
and parallelizable, but not free, and it must be rehearsed on paper
and in practice before a single measured pair runs.

**P2 — Shorten the observation, not the experiment.** Endpoint =
**first correct root-cause diagnosis**, judged cheaply, not full
resolution. Campaign-2's effect concentrates in call counts, which
diverge early; on the rehearsal data the diagnosis moment arrives well
inside 30 minutes. Truncating there cuts per-attempt spend by ~4–5× and,
as a bonus, removes the cap-hit pathology (a run that won't diagnose in
30 min is recorded as a failure at bounded cost, not truncated at $0.18).

**P3 — Small calibrated task suite.** 3–5 tasks of known difficulty
(the smoke task + two hard ones), fixed per pair, run in restored VM rigs.
Paired design stays — pairing is the right variance reduction — but a
pair now costs ~1 h of unattended time, not 6 h of attended time.

**P4 — Plain statistics, computed on paper first.** One paired t-test on
ln(calls-to-diagnosis) (the same analysis that produced campaign-2's
p=0.049), sequential look after every 5 pairs with a simple alpha-spending
bound, and **n computed once, on paper, from a 5-pair pilot's observed
σ** — before any harness is written. Drop McNemar / Clopper–Pearson /
Miettinen–Nurminen unless a rate endpoint is deliberately chosen. If the
pilot's σ implies n > 20, that is itself the answer: *the effect is too
small to confirm economically*, and the campaign stops for $5 of spend
instead of a week.

**P5 — Reframe what needs proving.** The publishable claim does not
require a 30-pair confirmation of an effect campaign 2 already measured.
A defensible write-up is: campaign-2's n=13 result (borderline
significant, direction consistent), a mechanism-level account of *where*
the savings come from (which skills fire, which tool calls disappear —
readable directly from the session transcripts we already have), and a
small VM-based confirmation run if reviewers ask for one. Measure
to learn, not to certify.

**P6 — If certification is genuinely required**, power it on the P2/P3
metric: at a ~20 min endpoint, even n=30 is one unattended overnight run
(~10 h) instead of a week of babysitting. That is the only regime in
which a confirmatory campaign is feasible at all.

## 7. Recommended successor (concrete sketch)

15 pairs × (skills-ON / skills-OFF) × 3 calibrated tasks; local VM rig
with snapshot restore per attempt (bootstrap amortized into image
builds, drift-audit stage included, reset flake rate measured in the
pilot); endpoint = calls-to-first-correct-diagnosis; paired t on ln
counts; looks after each 5-pair block; pilot of 5 pairs first to fix σ
and n. Total: a day-class run, <$3 model spend, one small analysis
script (~100 SLOC) instead of a kit — and note campaign 2's own
teaching that pairing absorbed baseline drift: the endpoint needs a
clean *juju/k8s/mysql* environment for the agent to debug, not
production-grade host fidelity, which is what makes the VM trade
defensible at all.

The pen-and-paper exercise the PR comment calls for is §6/P4: write down
the endpoint, the estimator, the variance estimate and the required n
*before* writing a line of harness. Campaign 3's design was correct by
its own lights — its lights said "spend a week to confirm", and that
verdict should have been reached on paper, not discovered in a lab.

## 8. Operational addendum (for the record)

The campaign also consumed non-measurement effort worth recording: a
mid-campaign amendment cycle (v3→v4, boundary fast-fail) with delta-log
sign-off; a git worktree rename + `worktree prune` incident that severed
the checkout's registration (fixed by re-registration; lesson: `repair`
after renames, never `prune`); and the freeze/verify battery discipline
(80+14 tests, ty strict) that kept every edit honest. Those practices
carried their weight; the lab did not.
