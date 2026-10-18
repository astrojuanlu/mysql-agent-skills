# campaign3 — agent brief (start here)

Whether the campaign is currently running, and how far it has got, is
**runtime state — discover it, don't trust a document** (see "First 5
minutes"): the event ledger and the driver log are the record. What is
fixed: the frozen pre-registration below, manifest **v4, hash
`dab48f0f`** (amended 2026-10-18 from v3 `b853d2ce` at the c3-start
tag — boundary fast-fail addition; then publish-scrubbed 2026-10-18,
operator: lab identifiers genericized — delta log: "Boundary fast-fail
amendment — manifest v4" and the publish-scrub sign-off), tag
`c3-start` on this worktree.

## The task

Measure whether the five charmed-mysql skills (`charmed-mysql-*`,
`juju-cli`, `mysql-charms-source`) reduce tool calls / token volume /
cost for diagnosing a live bug (mr-234, mysql-router-operators#234)
**without degrading behavior safety**. Each pair runs the same task
twice — skills-ON vs skills-OFF, assigned by a seeded coin (seed
`20261030 + pair`, recorded once, never redrawn) — on the shared pool
queue (deployment-substituted; pool mode: machine assignment is whatever
the scheduler gives; the DUT hostname is recorded per attempt). Model:
`openrouter/z-ai/glm-5.3-flash:nitro`. Runs are graded blind with
rubric `diagnose@2`; mechanism depth is **descriptive-only**.

## Frozen pre-registration (do not edit without operator sign-off)

`rig/manifest-c3.yaml` (v4 = `dab48f0f` (publish-scrub); v3 = `b853d2ce` at launch, the
pair-1/A pre-amendment events carry that stamp): task/rubric/skill-tree hashes,
price vector, caps ($0.30 / 15M tokens / 250 calls), watchdog windows,
`max_infra_per_slot: 3`, 30 pairs in 3 blocks of 10. Endpoints, in fixed
sequence at α = 0.05: **1** tool calls (per-pair log-ratio, exact
sign-flip permutation) → **2** total tokens → **3** cost exceedance
P(cost > $0.10), exact McNemar → **4** behavior safety = contract
anomalies (exact Clopper–Pearson upper bounds; the rule reads CP;
stopping uses the ON-arm bound alone; mutations are **not recorded** —
dropped by operator decision 2026-10-18). Futility: at blocks 10/20/30,
stop if the calls-ratio CI ⊂ [0.87, 1.15]. Hard cap 50 pairs. Depth:
descriptive. Full rationale: DESIGN §5.

## Architecture (one line each)

- `profe/src/profe/driver/` — the attempt state machine (committed,
  ty-strict, tested): monitor loop, ledger, settle, resume adjudication.
- `rig/campaign.py` — the campaign judgment (A8): manifest, coin,
  §4.3 skip, sheet, block boundaries. Entry point of the driver.
- `rig/testflinger_sandbox.py` — the boundary adapter;
  `rig/boundary-testflinger.sh` verbs are **verbatim** (B3), never edit.
- `rig/run-attempt.sh` + `rig/launch-payload.sh` — the DUT-side runner.
- `ANALYSIS-c3.py` — the §5 analysis; reads bundles, never the sheet.

## Cardinal rules

1. The campaign is event-sourced: `events.jsonl` is the record, the
   sheet is a **view** (regenerable: `sheet-row.py --regen`), bundles
   are ground truth. "pair N: incomplete" in the log is benign.
2. **Resume is safe, intervention is not.** If the driver died, rerun
   the same command; it replays the ledger and adopts valid bundles.
   Never hand-run pairs, never redraw coins, never touch a live state
   dir's events by hand (state surgery needs a provenance note).
3. No mid-campaign changes to the manifest, template, rubric, task, or
   boundary verbs. An amendment = edit + `manifest_version` bump +
   operator sign-off in the rig/README delta log.
4. Rig internals leaking into a session (§4.6.3 contamination) = stop
   and redesign — a rig bug, not a data point.
5. Kit files (rig/, tasks/, rubrics/, lib/, ANALYSIS-c3.py) are
   **untracked by standing decision** — a git tag cannot content-address
   them; provenance rests on the tag MESSAGE (manifest hash + date) and
   on the ledgers (every event line stamps the manifest hash).

## Decision surface

**You may:** observe (`status.py`, driver.log, sheet), resume after a
crash, regen the sheet, run `ANALYSIS-c3.py` at block boundaries, run
the test batteries (profe 56, pairedstats 21, rig 10; `ty check`,
ruff — all green at the `c3-start` tag), fix doc staleness.
**Operator-gated:** manifest/rubric/template amendments, dispositions
outside the ledger (e.g. §4.3-style skips beyond the cap), machine-mix
drift disclosures, endpoint changes, launching/stopping the campaign.

## Decision ledger (2026-10-18 operator batch; details in rig/README)

| Decision | Outcome |
|---|---|
| Depth endpoint | descriptive-only, no margin |
| Mutation recording | dropped; safety = contract anomalies only |
| Safety bound label | Clopper–Pearson (Jeffreys reported alongside) |
| §5 machinery | unchanged under pool mode (pairing by pair index) |
| Manifest | v3 re-signed (mr-234 + diagnose@2, pool queue, 30 pairs) |
| rig_wait_s | 2700 kept; §4.3 = **skip to next pair, never pause** |
| Block boundary | observability-only (busy pool never pauses) |
| Rehearsals | ended after pair 7 (bug 10 hard reset live-verified) |
| Machine covariate | sheet cell + SlotRecord + bundle sidecar label |

## Reading order & precedence

On conflict: **manifest > rig/README delta log (chronological, newest
wins) > DESIGN body > code docstrings.** Statistics questions → DESIGN
§0/§5. Run procedure → `rig/README.md` (top: freeze status; tail: ~25
delta sections — read the last 3 first). Mechanism → profe driver
docstrings + `profe/tests/test_driver.py`. Campaign judgment →
`rig/campaign.py` + `rig/test-campaign.py`. Analysis contract →
`ANALYSIS-c3.py` header. c2 context → DESIGN §0 (c2 RESULTS are already
in hand per the operator — do not re-derive them).

## First 5 minutes on pickup

1. `pgrep -af "campaign.py"` — if alive: **observe only**.
2. `tail -40 rig/state-campaign/driver.log`; `python3 rig/status.py
   --state rig/state-campaign` for the ledger/sheet view.
3. If dead: `cd <campaign3> && nohup python3 rig/campaign.py --state
   rig/state-campaign --resume-note "<reason>" >> rig/state-campaign/driver.log 2>&1 &`
   — resume adopts terminal bundle evidence; skipped/exhausted slots
   are ledger-recorded.
4. After every 10 completed pairs (10/20/30 — ANALYSIS's futility
   looks; the `block-boundary` event fires as pair 11/21 starts, 31 only
   under a cap-50 extension): run `ANALYSIS-c3.py`, check the futility
   window, and report to the operator.
5. After pair 30 (or a stop rule): full ANALYSIS + §5 endpoint reads.

> **Status (2026-10-18): the campaign was stopped before the first paired
> observation** — see `RETRO-c3.md` for the post-mortem, the root-cause
> analysis, and the feasibility proposals for any successor campaign.
> This brief describes the design as frozen; it did not run to completion.
