# Phase-1 results (profe)

**Status: CONCLUDED (2026-09-29).** All 5 skills trigger on their surface;
the only skill edit (log-autopsy d1) is train+validation verified; the
instrument was re-validated after the profe rework (schema 2, strict
typed-event parsing) by re-running juju-inspect-train — 0 parse errors,
mechanics identical, 12/12 queries still PASS
(`runs/juju-inspect-train-instrcheck.jsonl`; one one-off juju-cli misroute
on the mysql-router query, corroborated non-repeating 2/3, consistent with
the juju-cli FP pattern in "Near-miss coverage" below). Known carried debt:
the three boundary false positives and the juju-cli validation split are
recorded but deliberately not iterated on; next phase is Phase 2.

Date: 2026-09-28. Harness: `profe` (validate → probe → summarize). Model: pi
default (`z-ai/glm-5.3-flash`). Skills loaded: **5** per run (all skills under
`skills/`, listed via `find` — a skill without a curated set can still appear
in cross-trigger accounting). Serial, neutral cwd (`/tmp`), no hint, early
exit on first skill read / `max_tool_calls=12`, runs `:auto:` (cap 3),
retries 1. Inputs: the schema-1 query sets under `eval/` produced with
`profe/scripts/migrate_queries.py` (`expected_skills` per query).

**Trigger policy (part of the corpus design):** TO-execution/artefact
root-cause queries are dual-trigger (any-of log-autopsy + nightly-triage) —
3 such queries in `charmed-mysql-log-autopsy-{train,validation}.json`
(EVAL-STATUS §6.2 items 1, 2, 5). Triage-shaped TO queries stay
nightly-triage-only. Live-skill queries whose juju-CLI mechanics are
plausibly on the path are dual-trigger with `juju-cli` (5 queries:
juju-inspect's primary-from-cluster / cluster-status-from-deployment /
mysqld_exporter diagnosis; fault-injection's kill-mysqld-failover / durable
mysqld death).

## Per-skill pass rates (first-clean attempts, adaptive runs)

| Skill | train | validation | Notes |
|---|---|---|---|
| charmed-mysql-juju-inspect | 12/12 | 8/8 | clean |
| charmed-mysql-log-autopsy | 10/12 | 8/8 | 2 known corroborated failures (below) |
| charmed-mysql-nightly-triage | 10/10 | 8/8 | 1 query concluded 2/3 (boundary PASS) |
| charmed-mysql-fault-injection | 12/12 | 8/8 | clean |
| juju-cli | 8/8 | — | hits at tool-call #1 on every should-trigger query |
| smoke (near-miss) | — | 1/1 should-not clean | "slow query" tuning did not fire anything |

**Misroute-free on the original corpus** (all cross-trigger rates 0.00);
false positives appear only once keyword-sharing near-misses were added —
see the "Near-miss coverage" section below. All observed triggers fired at
tool-call #1–#2 (K=12 cutoff has ample headroom).

On the dual-trigger queries the non-primary members never fired first — early
exit masks them (a re-verification of the 5 juju-cli-widened queries under
their new expectations is in `runs/juju-cli-cross-delta.jsonl`: 5/5 still
PASS, original owner first each time). The widening's value is that a
juju-cli-first route can no longer count as a misroute; juju-cli's own
trigger surface is measured by its train set.

## Corroborated failures (log-autopsy train, both re-confirmed this pass)

1. **mysql-router git-archaeology / AI-triaged regression skepticism**
   (0 triggers; corroborated by 2 more clean misses on top of the earlier
   0/3). Description fix candidate: "judge an issue", "AI-triaged
   regression", "git archaeology" phrasing — next: train-only description
   iteration.
2. **Pure-Python traceback / peer-relation query** (0 triggers, same
   history). Plausible cause: no charm/juju keyword in the trigger surface;
   the query reads as Python debugging.

Both are Phase-1 misses only after clean re-runs — per §5.3 they qualify for
a second-model (`MODEL=`) corroboration before any description edit.

## Boundary cases

- nightly-triage-validation "Summarize this failed PR run for the standup…":
  miss → hit → hit (2/3 PASS). In the 0.3–0.7 band; candidate for
  corroboration if it recurs.

## Effort (medians over first-clean attempts)

Should-trigger runs conclude in ~3–4 s at ~2.6k tok total (5 skill
descriptions + prompt + query; trigger at call #1); log-autopsy-train median
14 s (agents skim issue text before reading). Whole corpus (88 queries + 5
cross-trigger re-verifications): ~16 min wall clock, 0 errored attempts.

Instrument note (verified against raw `pi --mode json` streams 2026-09-29):
- pi's per-message `usage.input` is the **uncached** token count; the same
  context re-served from cache is reported under `cacheRead`. Medians of
  `input` alone are therefore meaningless (observed 76 vs 2,576 for the same
  context shape); the reliable per-attempt figure is `totalTokens` (~2.6k),
  which is what the summarize effort line reports.
- pi reports `usage.cost` as a nested dict; profe originally summed only
  numeric values, silently dropping it (`$0.00` everywhere). Fixed by
  evolving the record schema to **2** (schema 1 = legacy flat cost float,
  still readable; schema 2 = pi's cost breakdown verbatim — the
  input-vs-cacheRead split is the Phase-2 overhead signal). Verified with
  `runs/smoke-schema2.jsonl` (real per-attempt cost ≈ $0.0004–0.0005;
  renders as `~$0.00` at 2 decimals). Runs recorded before the fix carry
  `cost: 0.0` (schema 1) — use `totalTokens` for those.

## Description-version history (log-autopsy, per §5.4 bookkeeping)

Model: pi default. Train-only signal per iteration; validation probed once
per finalized candidate.

| Description | Train | Validation | Change |
|---|---|---|---|
| d0 (v0.1.0) | 10/12 | 8/8 PASS | — |
| d1 (v0.1.1) | 12/12 | 8/8 PASS | added "pastes a bare Python traceback from a charm unit" and "judging whether a bug report or AI-triaged regression claim is credible, by git archaeology over the charm source" (896 chars) |

d1 selected (train fixed with no regression; validation held). runs:
`log-autopsy-{train,validation}-d1.jsonl`.

## Near-miss coverage (first false-positive measurements)

`near-misses.json` (8 keyword-sharing near-misses) and
`juju-cli-validation.json` (6: 4 should-trigger + 2 near-miss) were added to
close Phase 1; previously only 1 of ~89 corpus queries was a should-not.
Runs: `runs/near-misses.jsonl`, `runs/juju-cli-validation.jsonl`.

**juju-cli: 4/4 validation should-trigger, near-misses clean except the
upgrade-planning query** ("Plan the upgrade sequence for mysql-k8s 8.0→8.4…"
→ juju-cli fired 2/2; upgrade planning is not CLI mechanics — known boundary,
description-narrowing candidate if it ever matters).

**Boundary false positives found (corroborated 2/2 each, recorded, not
iterated on):**
- "Explain what Test Observer is and how artefacts and executions relate to
  PRs and nightlies" → nightly-triage fired (conceptual question; TO-keyword
  over-breadth).
- "Cluster lost quorum last night and recovered on its own… retro" →
  fault-injection 1/2, juju-inspect 1/2 (retro discussion, no injection or
  live ask; "quorum" keyword pull).
- "Plan the upgrade sequence…" → juju-cli (above).

**Curation correction:** the "draft a standup note" query was misclassified
by us as should-trigger nightly-triage (missed 2/2 — triage rightly did not
fire on a writing task); reclassified to should-not in `near-misses.json`.

## Run files

`eval/runs/{juju-inspect,log-autopsy,nightly-triage,fault-injection}-{train,validation}.jsonl`,
`eval/runs/log-autopsy-{train,validation}-d1.jsonl`,
`eval/runs/juju-cli-{train,validation,cross-delta}.jsonl`,
`eval/runs/near-misses.jsonl`, `eval/runs/smoke.jsonl` (JSON reports mirroring these names under
`eval/reports/`); per-query detail via `profe summarize <file> --format json`.
