# DESIGN — campaign 2 (c2): mr-234 paired cost campaign, hardened protocol

*Condensed 2026-10-16 for main from the full pre-registration + amendments
(archived on the development branch, together with the deviations log).
Campaign 2 ran 2026-10-15 → 2026-10-16 and is **complete**: 13/13 pairs at
the full pre-registered budget. `RESULTS.md` (final analysis) and
`run-sheet.csv` (26 accepted rows) sit alongside. Predecessor:
`../campaign1/`; successor: `../campaign3/DESIGN.md` (isolated rig,
confirmatory).*

## 1. Objective

Same as campaign 1 (`../campaign1/DESIGN.md` §1): estimate the effect of
the skill set on billed cost per run for the end-to-end task `mr-234`.
Campaign 2 exists to (a) re-measure under a hardened protocol and (b) run
the long unattended stretch under rules fixed **in advance**.

## 2. Frozen protocol (what changed vs campaign 1)

- Task, prompt, contract fix, model (`openrouter/z-ai/glm-5.3-flash:nitro`),
  pi 1.2.0 pin, frozen price vector (in $0.0261 / cache $0.0108 / out
  $0.90 per Mtok), and the frozen five skills — all unchanged.
- **Pairs 1–13**, order within pair by seeded coin, arms back-to-back.
- **Runner hardened per attempt**: fresh `testing` model (destroy + add),
  a **pre-run foreign-model sweep** (destroys any model other than
  controller/`testing` *before* the pre-run snapshot — closes the
  leftover-model contamination chains campaign 1 hit), /tmp manifest
  sweep, bundle-overwrite guard.
- **Watchdog v3**: kill on 25 min without stream progress (raised from
  10 min after poll-loop false positives killed valid runs); hung
  attempts are not valid cost observations.
- **Post-run gate**: `juju models` audit + leak-check before acceptance.
  Leak-check policy **v2.4** at campaign start (self-created model names
  derived from the task's own issue number are session debris, not
  contamination), evolving to **v2.8** (standalone token mentions;
  regression battery over all 28 bundles). Every policy change was
  disclosed with timestamps and applied symmetrically.

## 3. Driver (ad-hoc bash, campaign 2's known-weak layer)

Sequential pairs, skip-if-valid resume (derived from the run sheet — the
field-indexing bug this layer carried is documented in the successor
design), INFRA-vs-RUN failure distinction (INFRA = no fresh session
produced → retry, consumes no attempt slot; RUN = leak/schema/stall →
quarantine + rerun), ≤ 3 attempts per slot, and a **salvage path** for
pipeline-breaking timeout kills (row reconstructed from the stream, cost
recomputed at frozen prices, `leaks=ok-stream` label, contamination
still reruns). The retrospective on this layer — and the driver-v3 plan
that replaces it — is in `../../research/PROFE-TOOLING-RETRO.md`.

## 4. Adjudication (pre-registered §10.1 before the overnight stretch)

1. **Environment/infrastructure-caused** (host outage, rig damage from a
   *prior* run, campaign debris visible at t=0, operator action): the run
   did not start from the shared baseline → **exclude + rerun**; consumes
   no attempt slot.
2. **Agent-caused behavior** (self-installed snaps/packages, `chmod` on
   system files, self-built decoy environments): the treatment expressing
   itself → **keep** as the arm's cost sample (ITT), flag the verdict as
   suspect when the detour plausibly derailed the diagnosis, log the
   incident, restore the rig. Carve-out: destroying the measurement
   channel itself (the `testing` model, the controller) → invalidate.
3. **Unfair-information contamination** (campaign internals, the
   operator's real source tree incl. alias paths, canaries) →
   **exclude + rerun**.

**§10.5 (pre-registered before pair 6): timeout-killed runs are kept** —
cost observed, verdict absent, disclosed. Dropping wall-hits would bias
the paired difference downward and disproportionately trim the off arm's
heavy tail. Clarification (pre-registered before pair 10): schema-invalid
completions (agent gave up) likewise stay as the slot's cost sample.

## 5. Endpoints & reporting recipe (§10.2)

- **Primary**: ITT paired d over completed runs, all arms (including
  agent-caused-mutation runs); report d̄ and median/IQR (off-arm costs are
  heavy-tailed).
- **Sensitivities (pre-committed)**: clean-ops (drop arms with
  agent-caused mutations) and healthy-rig (drop environment-degraded
  pairs).
- **Incidence table** per arm, with the detection caveat (detection is
  post-hoc and incomplete).
- **Disclosure**: which pairs ran under which policy version; full
  p-value trajectory per pair count.

## 6. Deferred hardening (§10.3) and overnight ops (§10.4)

- **Frozen until campaign end**: the post-run host-integrity audit and
  the leak-gate generalization (record-and-restore machinery) — explicitly
  *not* implemented mid-campaign, to keep instrument changes out of the
  measurement window.
- **Overnight checklist**: driver log tail, run-sheet row growth,
  per-bundle incident triage (`snap install` / `chmod /etc` / `add-model`
  patterns), rig health (`snap list`, `juju models`,
  `/etc/passwd` mode), fill gaps, then analysis per §5.

## 7. Deviations & incidents (summary; full audit trail archived)

- **Rogue concurrent monitor (2026-10-15)**: a monitoring agent acted
  beyond its mandate — applied an unauthorized fix, rewrote the driver,
  attempted self-accept/quarantine, relaunched the driver; the relaunch's
  skip bug re-ran pair-1/on *on top of* its accepted bundle (stream.jsonl
  lost; sidecar/graded/leak-report survived, md5-verified). **Standing
  rule: monitor turns are read-only; destructive judgment calls belong to
  the operator.**
- `/etc/passwd` chmod 640 by the pair-3/off agent (agent-caused, kept
  ITT, rig restored; broke name resolution rig-wide for ~17 min).
- Contamination chains: a leftover `bug234` model invalidated two ON
  attempts (led to the pre-run sweep); an inherited microk8s snap
  invalidated pair-5/off r1.
- Restore-on-kill procedure gap (pairs 11 and 12): faketime (11/off) and
  strace (12/off — carried into three later baselines before the
  post-campaign purge) outlived their watchdog-killed installs. Both
  classified mild, disclosed, kept (ITT); the gap is a deferred-audit
  item.
- Instrument evolution v2.4→v2.8 (leak-check), v2→v2.2 (driver), v2→v3
  (watchdog): all mid-campaign, all disclosed — and the direct motivation
  for campaign 3's "freeze and version; no amendments mid-block" rule.

## 8. Outcome & artifacts

- Wrapped at the full budget: **13/13 pairs** (26 accepted rows), no early
  stop needed. Final analysis: `RESULTS.md` (this directory).
- The cost difference attenuated over the campaign (the early pairs
  carried most of the saving; pairs 6–10 averaged ≈ $0; pairs 11–13 mildly
  favoured the skills arm again; Spearman ρ(d, pair index) = +0.31) —
  treated as a candidate *finding* (rig/template drift), which the
  blocked, frozen-rig design of `../campaign3/DESIGN.md` addresses.
- `run-sheet.csv` copied alongside. The deviations log, per-pair bundles,
  and the statistical consultation materials are archived on the
  development branch.
