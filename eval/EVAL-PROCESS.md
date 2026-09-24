# How we evaluate the skills (process summary)

Durable methodology for evaluating the skills in `skills/`. The per-protocol
numbers, incident history and current next steps live in `eval/EVAL-STATUS.md`
(and the V1–V5 detail in `EVAL-STATUS.1.md`); this document is the stable
"how and why", incorporating what the first five protocol generations and the
V6 matrix taught us. Grounded in `SKILL_CREATION_PLAN.md`, the agentskills.io
guides, pi's skill mechanics, and our own measurement experience.

## 1. Why triggering is the first thing we measure

Per the agentskills.io guides ("Optimizing skill descriptions", "Best
practices"): the `description` frontmatter is the **only** thing the model
sees before deciding to load a skill. A skill that is never loaded cannot
help, no matter how good its body is. So:

- **Phase 1 (triggering)** — does the skill load when it should, and stay
  unloaded when it shouldn't? This is what the harness measures.
- **Phase 2 (output quality)** — when loaded, does the agent reach correct
  conclusions? The real work; see §7.

**Phase 1 is a router test, and is priced like one.** It measures a single
boolean per (query, skill) — the load decision — which the model makes in
its first few tool calls. Everything after that decision (the actual task,
the answer, live-cluster work) is Phase-2 material and must not cost Phase-1
time. The protocol below is designed around this: runs exit early, run
counts escalate only on ambiguity, and heavy live-infrastructure work is
out of scope for Phase 1 together. A description-iteration cycle should
cost tens of minutes, not
hours; if it costs more, the protocol has drifted from this spec.

The design constraints from `SKILL_CREATION_PLAN.md` (body ≤150 lines / ≤6k
tokens, description <1024 chars, progressive disclosure, reference files
loaded on demand) exist to keep both phases feasible on the weakest target
model; Phase 1 also validates the *description* against the <1024-char rule.

## 2. How skills trigger in pi (the mechanics we rely on)

- At startup pi shows the agent only skill **names + descriptions** (skills
  passed via `--skill <dir>` or discovered under `--skill-dir skills` —
  the harness auto-discovers, so new skills join every run automatically).
- A "trigger" = the agent reads `<skill>/SKILL.md` with its read/bash tool,
  detected from pi's `--mode json` event stream
  (`tool_execution_start` events).
- **A trigger decision happens in the first few tool calls.** This is the
  property the whole Phase-1 protocol is built on, twice over:
  - *Early exit:* once a trigger is observed, the run can be killed
    immediately — nothing after the load decision (the task, the answer,
    live work) affects Phase 1. A should-trigger run therefore costs only
    the agent's first tool calls, not the full task.
  - *Kill safety:* trigger state must survive any kill, so events are
    streamed and recorded as they happen (V3 lesson: buffering output at
    the kill boundary destroyed exactly this evidence).
- Consequences: the description carries the entire Phase-1 burden; anything
  the description doesn't name (synonyms, phrasings, task verbs) is a
  potential missed trigger; and skills with overlapping domains must have
  descriptions that disambiguate *between* them.

## 3. What Phase 1 measures

(This section is the spec the harness converges to; the currently
implemented harness covers a subset — full runs, fixed N=3, no early exit —
and the divergence is closed incrementally.)

### Invocation

`pi --mode json --no-session --no-extensions -nc --skill <each skill dir>
-p "<query>"`, run from a **neutral cwd (`/tmp`)** with **no evaluator
hint**. Both protocol decisions are empirically grounded: the repo's own
files in context and the hint measurably helped or polluted results.

### Run lifecycle: early exit

Phase-1 runs never execute the task:

- **Should-trigger queries:** kill the run as soon as the first skill read
  is observed in the event stream. Post-trigger behavior (the answer, live
  work) is Phase-2 material and carries no Phase-1 information.
- **Should-not queries:** absence of a trigger can only be observed by
  watching; exit after the agent makes **K tool calls** with no skill read.
  K is set from the observed trigger-call-index distribution (measurable
  from recorded runs) to cover ~99% of triggers; a trigger that would have
  occurred after call K is a documented approximation and counts as a miss.
- Because no run reaches live work, Phase 1 requires **no live
  infrastructure**: no scratch models, no cluster-health gating, no
  per-skill timeout table sized for full task execution. All skills are
  measured on the same footing, and the heavy-lift hygiene in §8 applies
  to Phase 2, not Phase 1.

### Pass semantics: adaptive run counts

Fixed N=3 over-counts: most queries decide 1–0 or 0–1 on the first run.
Instead:

- Start each query at **one run**; escalate only when the outcome is not
  decisive — a should-trigger run that didn't trigger, a should-not run
  that did, an errored or timed-out attempt, or a rate that could still
  cross the 0.5 threshold. Cap at **three runs**.
- Pass rates: should-trigger ≥ 0.5 over the runs taken; should-not ≤ 0.5.
- Transient errors are retried once (`--retries 1`); the **first clean
  attempt per run** drives pass accounting.

### Cross-trigger accounting

All skills are loaded into every run; the trigger state of **all** of them
is recorded, so misrouting is diagnosable (e.g. log-autopsy queries pulled
to nightly-triage by "Test Observer"/"nightly CI" keywords). New skills
join via auto-discovery — e.g. `juju-cli` joined after V6, so V6 numbers
are the 4-skill baseline and later matrices are 5-skill; note the skill
count with every reported number.

### Query set design

`eval/queries.json`, ~78 queries, ~60/40 train/validation per skill, with
three design principles:

1. Should-trigger queries vary **phrasing, explicitness, complexity** — not
   just paraphrases of the description's own words.
2. Should-not queries are **keyword-sharing near-misses** (the hard case:
   "slow query" tuning vs cluster diagnosis), because easy negatives teach
   nothing about the description's boundaries. These are part of the set
   design for every skill (see the samples in §6; materializing them into
   the per-skill sets is tracked as curation work).
3. Overlap queries must be **curated deliberately**: Test-Observer-execution
   queries sit legitimately between log-autopsy and nightly-triage; such
   sets need an explicit dual-trigger policy, decided *before* iterating.

### Model selection

Phase-1 runs use pi's configured default model. Validations and
confirmations may be run against a different model with the harness's
`MODEL=` override — trigger behavior is model-dependent, so every reported
number records which model produced it (RESULTS.md). Running a validation
with a second model doubles as a robustness check: a description that only
triggers on one model is fragile.

**Zero false positives so far** across all protocols — all observed noise
has been one-directional (missed triggers). This asymmetry is itself
information: it means descriptions err on the side of specificity, and the
iteration phase should focus on broadening trigger surfaces, not narrowing.

## 4. Measurement reliability (the four noise sources)

Every surprising result must be traced to one of four causes before it is
believed. These are standing properties of the protocol, not history — the
detailed incidents live in §8:

| Cause | Symptom | Standing mitigation |
|---|---|---|
| (a) evidence loss at kill boundaries | "triggered early, killed late" became a miss | streaming capture — trigger state survives any kill, including early-exit kills |
| (b) API concurrency degradation | fast single-turn completions with zero skill reads, at parallel ≥ 3 | fully serial; relaxing the serial rule requires a fresh validation against a known-clean set (every relaxation so far has failed) |
| (c) transient API/network errors | all-errored batches | `--retries 1` + first-clean-attempt semantics + error signatures per attempt |
| (d) timeouts misfit to the workload | runs "failing" only by timeout | with early exit, Phase-1 runs are first-tool-call sized, so this class mostly disappears; residual timeouts indicate hangs/stalls and are retried, not graded |

Standing corollaries:

- **A set is only as valid as its infrastructure was.** Verify host and
  cluster health for the whole run before trusting a number (V4's
  juju-inspect numbers were artifacts of a degraded cluster; V6 reproduced
  the same queries at 12/12 and 8/8).
- **Suspicious numbers get re-runs, not conclusions.** Re-run the suspect
  set before theorizing; repeated exact reproductions are what upgrade a
  number to "known".
- **Runs are resumable.** The JSONL format skips already-clean (query, run)
  pairs on re-run, and cleanup after an interrupted run is idempotent — an
  interrupted batch costs at most its in-flight attempt.

## 5. Iteration discipline (fixing what fails)

Phase-1 iteration is deliberately conservative, to avoid overfitting
descriptions to the query set — and fast enough that the conservatism costs
little:

1. **Train-only signal.** Description edits are judged on the train split;
   validation is held out untouched until a candidate is finalized.
2. **≤5 iterations** per skill — beyond that, suspect the query curation or
   the skill's scope, not the phrasing.
3. **Fix only corroborated failures.** A missed trigger that appeared once
   is not a defect. Failures must survive (a) a clean re-run and (b) a
   second-model check (`MODEL=` override), applied only to failing or
   0.3–0.7-band queries.
4. **Bookkeeping** in `eval/RESULTS.md`: per-description-version pass rates
   per model, so the best iteration is selected by *validation* performance,
   not train, and the model behind every number is on record.
5. **Scope disputes go to scope, not wording.** If a query keeps failing
   because it legitimately belongs to a different skill (the TO-overlap
   class), the fix is a dual-trigger policy decision or a query
   reclassification — not a description tweak.

## 6. What one attempt looks like (worked examples)

Every attempt is one `pi` invocation from `/tmp` with all skills loaded. The
harness builds the command from its skill discovery; conceptually:

```bash
pi --mode json --no-session --no-extensions -nc \
   --skill skills/charmed-mysql-log-autopsy \
   --skill skills/charmed-mysql-juju-inspect \
   --skill skills/charmed-mysql-nightly-triage \
   --skill skills/charmed-mysql-fault-injection \
   -p "<query>"
```

What is **checked**: only triggering (did any skill's SKILL.md get read,
via `tool_execution_start` events) and completion health (exit, timeout,
errors). The answer text is captured but **not graded** in Phase 1.

**Case A — happy path** (should-trigger query, e.g. *"Our mysql-k8s unit is
stuck in blocked status — figure out what's wrong"*): pi exits 0; early in
the event stream, `tool_execution_start` shows a read of
`skills/charmed-mysql-juju-inspect/SKILL.md`; the answer is substantive.
JSONL record: `triggered[juju-inspect]=true`, `exit_code=0`,
`timed_out=false`, `dur_s≈45`. Query passes if ≥2 of 3 runs look like this.

**Case B — finished but not triggered** (should-trigger, false negative):
pi exits 0 with a normal answer, but no skill read appears — the agent
answered from general knowledge. All `triggered` flags false; the query
fails only if the rate stays ≤ 0.5 over 3 runs. When investigating, read
the captured answer: it distinguishes "close but not triggered" from
"totally off-topic" (the latter suggests query curation, not description).

**Case C — correct non-trigger** (should-not query, e.g. the slow-query
tuning near-miss): pi exits 0, no skill reads at all — the expected
outcome; passes.

**Case D — timeout/hang, trigger preserved**: with early exit, Phase-1 runs
are first-tool-call sized, so genuine timeouts are rare; when one occurs
(provider stall, hung session) the watchdog kills pi (exit −9, folded into
`timed_out`), but the streamed events already recorded any trigger, so the
attempt contributes its trigger state; `--retries 1` gives one more
attempt. Only the *first clean* attempt per run drives pass rates. (In the
pre-early-exit protocol, V6's live-skill sets routinely hit the 600s task
budget — Case D was common there; it is the exception now.)

**Case E — pi reported error**: final message `stopReason` is
`error`/`aborted` (e.g. `Request timed out.` from an API blip), or
`auto_retry` events, or a non-zero exit. Recorded as `errored` with the
error string and stderr tail; the retry replaces it in pass accounting.
A batch that is *all* Case E is infrastructure, not signal (V5's network
incident) — re-run, don't conclude.

### Real query samples

From `eval/queries.json` / the per-skill train-validation files — should
trigger, showing the intended variety:

- *juju-inspect:* "Is the Charmed MySQL cluster in model testing healthy
  right now? Check the topology and tell me who is primary." (indirect
  health check, no symptom named)
- *juju-inspect:* "The customer says their database is 'down' but juju shows
  active. Connect to their model and establish what's actually going on."
  (report vs reality contradiction)
- *log-autopsy:* "i got these logs from a customer deployment, the mysql-k8s
  unit crashed with KeyError 'logs_synced' during scale down, take a look
  and tell me whats going on" (deliberately informal phrasing)
- *log-autopsy:* "Our QA team sent me a juju-crashdump from a failed charm QA
  run of mysql-k8s. The requirer app has been waiting forever with
  'Incorrect/incomplete data found in relation relational-db'..." (artifact-
  driven, specific error string)
- *nightly-triage:* "PR #501 shows 10 failing integration checks. Are these
  failures related to the change or can we merge? Do the triage."
- *fault-injection:* "Break the database on purpose: we want the app to see
  a connection failure for about two minutes, then automatic recovery, to
  test our app's retry logic." (outcome-specified, no juju vocabulary)

Should-not near-misses (designed examples; the keyword-sharing hard cases —
only the first is in `eval/smoke.json` today, none in the per-skill sets
yet):

- "Optimize this MySQL query: SELECT * FROM orders WHERE created_at >
  NOW() - INTERVAL 30 DAY; it's slow and I need it faster." — plain MySQL
  tuning; *no* skill should fire.
- "Our nightly backup job failed last night with a timeout. Can you restore
  from the previous backup?" — "nightly" keyword must not pull
  nightly-triage; this is an ops task.
- "Write a Python script that parses MySQL slow query logs and extracts the
  top 10 queries by time." — "logs"/"MySQL" must not pull log-autopsy.

## 7. Phase 2 — outcomes and effort across the (model × skills) matrix

Spec of the desired Phase-2 behavior. One measurement instrument, two
parameters, four cells, mechanical grading, one comparison layer. (An
earlier framing put effect measurement in a separate "Phase 3"; that is
dropped — no new apparatus is needed. Effect measures are Phase-2 runs
repeated over the parameter grid and compared; see the §8 reflection.)

### 7.1 Questions the matrix answers

1. **Correctness:** with the skills, does the agent reach correct,
   grounded conclusions? (Phase-2 rubrics vs ground truth.)
2. **Effect:** do the skills make the process better, faster, or cheaper
   than the same model without them?
3. **Uplift:** can a weaker model, given pi + these skills, perform
   debugging tasks it otherwise cannot?

### 7.2 Parameters

- **model** — chosen by the operator per run (harness `MODEL=` override or
  pi's default); recorded with every attempt and every reported number.
- **skills present** — yes (harness default: auto-discovery loads all
  skills) or no (run without `--skill`; the agent never sees descriptions
  or bodies).

### 7.3 The 2×2 matrix and the role of each cell

| cell | model | skills | role |
|---|---|---|---|
| A | strong | no | baseline: what a capable model does with pi alone — the bar the skills must beat |
| B | strong | yes | improvement measures over A: correctness and effort deltas |
| C | weak | no | demonstrates the limitations that motivate the skills |
| D | weak | yes | the promising result: does C's gap close? |

Strong/weak are operator choices (e.g. the current default vs DeepSeek
V4.1 Flash); no cell is run without its identity recorded (cell, model,
protocol version, run dates).

### 7.4 Measurements (all quantitative, per attempt)

**Outcomes** — from the graded transcript:
- rubric score (fraction of rubric items passed);
- verdict-class correctness (exact match on the required enum);
- evidence-grounding pass (quotes found verbatim in artifacts);
- action-sequence checks for live skills (assertions on the recorded
  tool-call transcript).

**Effort proxies:**
- input tokens and output tokens;
- tool-call count;
- wall time.

**Validity filters (not outcomes):** exit code, timed-out, errored. A
timed-out or errored attempt contributes no outcome or effort data and is
retried per §4; its partials stay in the JSONL for diagnosis.

The harness shall record all of the above per attempt. Token counts depend
on pi exposing usage in the event stream (to be verified; if absent, keep
tool-call count + wall time and label any transcript-derived token
approximation as such).

### 7.5 Grading devices (no LLM anywhere in grading)

1. **Constrained output.** The task prompt requires a fenced YAML/JSON
   verdict block with enumerable fields; grading = schema validation +
   exact/regex matches on fields. Free prose is ignored.
2. **Gold facts.** Rubrics of atomic checkable facts, derived *before* the
   run from ground truth that exists independently of the agent (TO API
   results, issue resolutions, case studies, live-cluster probes).
3. **Evidence grounding.** Every quoted log line must appear verbatim in
   the artifact (`grep -F`); a correct class with fabricated evidence
   still fails.
4. **Actions, not prose.** For live skills the rubric asserts on the
   recorded command sequence: right primitive chosen, post-state verified,
   documented dead ends avoided.

The task prompt — including the verdict contract — is **identical across
all cells**; skills presence is the only treatment difference. A skills-off
run must not leak any skill content.

### 7.6 Worked example: log-autopsy on issue #327

Ground truth (`inspiration/server-327.md`, the issue's own resolution): a
relation-setup race — provider creates the MySQL user, then fails; the
retry hits `CREATE USER` error 1396 (already exists); credentials are never
published; the requirer waits forever. Not an infra failure. Verdict
contract (part of the task prompt, all cells):

```yaml
verdict: charm-bug            # bug | flake | infra | user-error
root_cause: >
  <one sentence>
evidence_quotes:              # each must appear verbatim in the artifacts
  - "<quoted log line>"
affected_component: <charm file or relation name>
```

Rubric — score = fraction passed; every item graded mechanically:

| # | Check | Grader implementation |
|---|---|---|
| 1 | `verdict == charm-bug` | exact match on enum |
| 2 | root cause names the 1396/retry race | regex on `root_cause`: `1396` AND (`race` or `retry`) |
| 3 | evidence quote exists in bundle | `grep -F` of each quote against the attached `debug-log.txt` / `db-dump.yaml` |
| 4 | quotes show the ordering (user created → failure → retry) | quote set matches pre-derived gold line IDs |
| 5 | does not blame infra/operator | negative check on `root_cause` |

Buys: reproducible, free, instant grading; every failed item has a one-line
mechanical justification. Costs: rubric engineering per task; nuance is
graded only as far as the verdict contract captures it. When a task seems
to need nuance beyond the contract, transform the task — do not admit a
judge.

Per-skill rubrics, same devices:
- **log-autopsy** — rubrics from TO execution 659755 / artefact 409577 and
  the `inspiration/` case studies (the #327 example generalizes).
- **nightly-triage** — per-failure classification against gold labels
  derived from issue resolutions (later-fixed = bug, retry-passed = flake,
  infra-caused = infra) + evidence grounding on artefact bundles.
- **juju-inspect** — the report's factual claims checked against a
  live-cluster probe script run *after* the session (primary unit, member
  states, versions are machine-readable). Baseline: the current `testing`
  deployment (re-created 2026-09-28); re-probe at grading time. Optionally
  one approved reversible perturbation.
- **fault-injection** — action-sequence grading + state verification via a
  fixed probe (`replication_group_members`: member UNREACHABLE and staying
  down, then recovery).

### 7.7 Comparison machinery

The harness shall provide a comparison pass over the cell JSONLs
(concretely: a `compare` subcommand reading two or more `runs-*.jsonl`
files):

- **Per cell × skill:** outcome (pass rate; rubric-score mean/median),
  effort distributions (input/output tokens, tool calls, wall time — median
  and p95), and cost estimate (tokens × model price).
- **Paired per-query deltas** across cells (same query, two conditions):
  Δrubric, Δeffort, reported as counts of improved/flat/worse plus medians —
  sign-based summaries, not only means; the sets are small and skewed.
- **Headline tables:** B−A (do the skills improve a strong model, and at
  what effort delta), D−C (does the skill close the weak-model gap), and
  B vs D (how close does weak+skills get to strong alone).
- **Overhead accounting:** skills cost input tokens even when they help
  (descriptions in every run's context; loaded SKILL.md bodies when
  triggered). Net utility = benefit − overhead; the report therefore
  includes the no-skills cells' input-token baseline explicitly.

Output: a machine-readable comparison report (JSON) plus a rendered table
in `eval/RESULTS.md`.

### 7.8 Validity and safety conditions

- **Comparability.** Cells are only comparable under the same protocol
  version and the same query files. Interleave conditions within a set
  (alternate query-blocks across cells) to decorrelate infrastructure
  drift — never run whole cells back-to-back on different days (V4-vs-V6 is
  the cautionary example: identical queries, 4/12 then 12/12).
- **Serial.** §4 applies to every cell, without exception.
- **Format confound.** A weak model may fail at *producing the verdict
  block* rather than at the substance. Grade actions too, and report
  schema-compliance separately from correctness so the two failure modes
  remain distinguishable.
- **Safety in the skills-off FI cell.** Without the fault-injection
  skill's guidance the agent may improvise riskier primitives on a live
  cluster. FI cells run against the harness-owned scratch model only, and
  skills-off FI runs are supervised, never unattended.

## 8. Operational lessons from running evaluations

Eval sessions are themselves agents operating live infrastructure, and they
leave the same debris any agent does. The process-level rules:

- **The harness owns the lifecycle of anything it creates.** Live-skill sets
  (fault-injection) run against a scratch model the script deploys and
  destroys; `testing` is never the target. If the harness didn't create it,
  it doesn't clean it — but it *sweeps* for orphaned debris before and
  after (status-sampler loops, stray juju clients; see `JUJU-LESSONS.md`
  for the mechanics).
- **Resource pre-flight before heavy sets.** Verify the shared cluster is
  healthy, the host has RAM headroom, and only the expected workload
  processes exist. The V6 incident (host saturation, load ~394) came from
  stacked leftovers, not from any single run.
- **Timeout economics matter at set scale.** A timeout that a whole class of
  runs exceeds turns every run into timeout→retry, doubling per-query cost
  and load (this doubled the FI-validation cost at 300s). Set per-skill
  timeouts above the *observed* p95, not the median.
- **Runs are serial, unattended runs are babysat or resumable.** A full
  matrix takes many hours; write the run so that interruption loses at most
  the current attempt (streaming JSONL), and have a human or agent check in
  periodically.
- **Don't perturb shared infrastructure during live sets** — no interactive
  juju work against `testing` while live-skill sets are running, and no
  unattended matrix while the host carries unreconciled experiment state.
- **Cleanup is part of the protocol, not an afterthought.** Every eval
  artifact that touches infrastructure (scratch models, samplers, credentials
  in env) has an owner and a teardown step.

### Where the noise actually came from (bottleneck taxonomy)

Four distinct culprits produced every "bad number" in the project's history;
they are worth separating because each had a different owner and fix:

1. **The LLM API provider — dominant.** OpenRouter-side concurrency
   degradation: requests issued in parallel returned degraded completions
   (fast, zero tool calls) — nightly-triage scored 0/10 yet triggered 100%
   re-run manually; serializing fixed it completely. On top, plain transient
   errors (`Request timed out.`, a real network incident mid-V5). We cannot
   fix the provider; we design around it (serialize, retry, first-clean-
   attempt semantics).
2. **Our own harness code — V3 only.** `subprocess.run(timeout=...)`
   discarded the child's stdout on kill, converting "triggered early, killed
   late" into a miss. Pure measurement-tool defect; fixed in code. Lesson:
   when numbers look wrong, suspect the measuring instrument first.
3. **Host resources — once, and for identifiable reasons.** The V6
   fault-injection-validation saturation (load ~394, RAM pinned) was not
   caused by the eval process itself: pi sessions and API streaming are
   network-bound and light. It was leftover experiment models (7 mysqlds ≈
   14GiB), daemonized status-sampler loops, and heavy FI live-recon stacking
   on top — symptom: TLS handshake timeouts to the *loopback* k8s API. Fix
   was structural (harness-owned scratch models + debris sweeps), not more
   hardware.
4. **Shared cluster state — health, not load.** juju-inspect's V4 numbers
   (4/12, 3/8) were measured partly while `testing` was degraded. Triggers
   were still detected, but live sessions misbehaved and the numbers weren't
   comparable; V6 on a verified-healthy cluster gave 12/12 and 8/8. Hence
   cluster-health pre-flight before live sets.

Timeouts were not a bottleneck themselves but a force multiplier: a
mis-set threshold turned normal slowness into false failures and — at 300s
for fault-injection — into a retry storm that *then* became a local load
problem.

### Reflection: serial execution + model hygiene are non-negotiable

The two structural decisions that made the numbers trustworthy are also the
two that are easiest to "optimize away" under time pressure — resist that:

- **Serial execution** costs wall-clock (a full matrix takes many hours),
  and every generation that relaxed it (V1, V2, V4 parallel-3) paid in
  noise. The API provider degrades concurrent request batches in ways that
  look exactly like genuine skill failures (fast, confident, wrong), and
  serialized re-runs of the same queries scored 10/10. If wall-clock ever
  forces concurrency back, it must be re-validated against a known-clean
  set before any new number is trusted.
- **Model hygiene** (harness-owned scratch models, teardown, debris sweeps,
  no unreconciled state) is what keeps *live-skill* sets meaningful at all:
  juju-inspect and fault-injection grade behavior on a real cluster, so a
  dirty cluster doesn't just slow runs down — it changes what is being
  measured. The V4 juju-inspect numbers are the cautionary example: they
  were recorded, reported, and only later discovered to be artifacts.

Both decisions trade speed for *interpretability* of results. Since the
entire output of this process is a small set of numbers per skill, a number
we can't interpret is worth nothing — which is the final argument for
paying the serial/hygiene cost every time.

### Reflection: effect measurement is a grid over one instrument

When "do the skills actually help?" first came up, it was framed as a new
experimental phase with its own setup (paired runs, counterfactual
conditions, model matrix). On examination it collapses: the procedure —
same invocation, same protocol, same rubrics — is already Phase 2, and the
only additions are *which cells to run* (the model × skills grid) and a
*comparison layer* over the collected cells. The genuine deltas are
scheduling and analysis, not apparatus. The lesson generalizes: before
adding a new phase, check whether it is the existing instrument applied to
more cells — separate "phases" should exist only where the procedure itself
changes, not where the parameter grid does. (What did survive as real
requirements: interleaving conditions to decorrelate drift, effort telemetry
in the harness, and the skills-off FI safety rule — all folded into §7.)

## 9. Key references

- `SKILL_CREATION_PLAN.md` — skill design constraints (body budget,
  description cap) and the model-limit table (OpenRouter `models-store.json`).
- agentskills.io: "Best practices for skill creators", "Optimizing skill
  descriptions" — the two Phase-1-relevant guides (descriptions carry
  triggering; bodies are instructions for the agent, not human docs).
- bm629 agent-skills `skill-forge` — body budget guidance (≤150 lines / ≤6k
  tokens / <1024-char description).
- DeepMind science-skills `workflow_skill_creator` — structure template
  (frontmatter → overview → when to use → workflow → output).
- pi skill mechanics — `--skill`/`--skill-dir`, `--mode json` event stream,
  `tool_execution_start` as the trigger signal.
- `eval/EVAL-STATUS.md` — current numbers, protocol history, next steps.
- `JUJU-LESSONS.md` + `skills/juju-cli` — the Juju operational knowledge the
  eval sessions accumulated (kept out of this document deliberately).
