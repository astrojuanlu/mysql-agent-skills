# Profe

![Warning: Vibe Coded](https://img.shields.io/badge/%E2%9A%A0%EF%B8%8F_warning-vibe_coded-orange?style=flat)

Evaluate skills following a structured methodology.

First, validate that the skills follow the [Agent Skills spec]
and probe whether they are triggered by the prompts you expect
(validate → probe → summarize).

Next, grade the performance of an agent using the skills
according to some rubrics, and compare the performance of different
models (grade → compare → report).

[Agent Skills spec]: https://agentskills.io/specification

## Reference

### `profe validate`: structure check

Thin wrapper over `skills-ref validate`: frontmatter, name/description
limits, body budget. Validates *structure only* - it cannot assess
trigger behavior (that's `probe`'s job) or answer quality (`grade`).

```
profe validate --skills <path>...
```

### `profe probe`: execute trigger queries, record attempts

Runs every query in the queries file serially against the loaded skills.
Measures **triggering only**.

```
profe probe --skills <path>... [--queries eval/queries.json]
            [--out eval/runs/<name>.jsonl]
            [--model MODEL] [--timeout 300] [--retries 1]
            [--max-tool-calls 12] [--num-runs :auto:|N]
```

- `--skills <path>...` loads skill directories or files (variadic); at
  least one is required. Record names come from each skill's frontmatter;
  an invalid path is a hard error, since a silently missing competitor
  corrupts cross-trigger accounting. Loading everything is a shell glob
  away: `--skills skills/*/`.
- `--model` overrides pi's configured default model (e.g.
  `--model z-ai/glm-5.3-flash`); the model is recorded in every record.
- `--timeout` is a single global hang-guard, not a task-duration budget.

### `profe summarize` - aggregate a probe runs file into a report

Generate pass rates from trigger probes.
The table format renders one row per skill and query class;
the JSON format carries the same aggregates plus a **per-query section**
(every query's per-run trigger state and decision)
so a misroute or a near-miss that fired is diagnosable from the report alone,
without re-reading the runs file.
Both formats include effort medians.

```
profe summarize eval/runs/<name>.jsonl [--out eval/reports/<name>.json]
                [--format table|json]
```

### `profe grade`: launch rubric tasks, score the attempts (**WIP**)

### `profe compare`: rank performance of different models and skill configurations (**WIP**)

### `profe report`: turn graded runs into a structured report (**WIP**)

## How to run a typical session

`queries.json` declares its format version and carries the query objects
under `queries`. `expected_skills` names the skill(s) the query should
trigger — always a **list**, any-of semantics; an **empty list** marks the
near-misses every skill must stay away from (should-not). The schema
declaration is mandatory.

```json
{
  "schema": 1,
  "queries": [
    {"query": "Is the Charmed MySQL cluster in model testing healthy right now? Check the topology and tell me who is primary.",
     "expected_skills": ["charmed-mysql-juju-inspect"]},
    {"query": "Optimize this MySQL query: SELECT * FROM orders WHERE created_at > NOW() - INTERVAL 30 DAY; it's slow and I need it faster.",
     "expected_skills": []},
    {"query": "Test Observer execution 659755 failed on artefact 409577 — pull the artefact and tell me what happened.",
     "expected_skills": ["charmed-mysql-log-autopsy", "charmed-mysql-nightly-triage"]}
  ]
}
```

Running it produces one JSONL record per attempt:

```json
{"schema": 2, "index": 0, "run": 0, "attempt": 0, "expected_skills": ["charmed-mysql-juju-inspect"], "model": null, "loaded_skills": ["charmed-mysql-log-autopsy", "charmed-mysql-juju-inspect", "charmed-mysql-nightly-triage", "charmed-mysql-fault-injection", "juju-cli"], "triggered": {"charmed-mysql-log-autopsy": false, "charmed-mysql-juju-inspect": true, "charmed-mysql-nightly-triage": false, "charmed-mysql-fault-injection": false, "juju-cli": false}, "first_trigger_skill": "charmed-mysql-juju-inspect", "early_exit": "trigger", "trigger_call_index": 1, "stderr_tail": [], "clean": true, "usage": {"input": 43151, "output": 2224, "cost": {"input": 0.0192, "output": 0.0021, "cacheRead": 0.0, "cacheWrite": 0.0, "total": 0.0213}}, "dur_s": 24}
{"schema": 2, "index": 1, "run": 0, "attempt": 0, "expected_skills": [], "model": null, "loaded_skills": ["charmed-mysql-log-autopsy", "charmed-mysql-juju-inspect", "charmed-mysql-nightly-triage", "charmed-mysql-fault-injection", "juju-cli"], "triggered": {"charmed-mysql-log-autopsy": false, "charmed-mysql-juju-inspect": false, "charmed-mysql-nightly-triage": false, "charmed-mysql-fault-injection": false, "juju-cli": false}, "first_trigger_skill": null, "early_exit": "max_tool_calls", "trigger_call_index": null, "stderr_tail": [], "clean": true, "usage": {"input": 38102, "output": 812, "cost": {"input": 0.0169, "output": 0.0008, "cacheRead": 0.0, "cacheWrite": 0.0, "total": 0.0177}}, "dur_s": 41}
```

`index` indexes the query, `run` the adaptive escalation attempt,
`attempt` the retry index; the full attempt core is listed under
[Probe](#probe).

```
$ profe validate --skills skills/*/
$ profe probe --skills skills/*/ --queries eval/smoke.json \
    --out eval/runs/smoke.jsonl
$ profe summarize eval/runs/smoke.jsonl --format table
```

```
skill                          class            runs  pass-rate  decision
charmed-mysql-juju-inspect     should-trigger      1       1.00  PASS
(all others)                   cross-trigger       1       0.00  —
(should-not queries)           —                   1       0.00  PASS

protocol: serial, neutral cwd, no hint | model: default | skills: 5
early exit: on first skill read / max_tool_calls=12 | runs: auto | retries: 1
effort (median/attempt): 43k tok total, 1.1k out, $0.02, 1 tool call, 9s
```

```
# one queries file per purpose: smoke, per-skill train sets,
# corroboration subsets, fresh baselines — point --queries at the one
# you need

# iterate: re-probe one skill's train file after a description edit
$ profe probe --skills skills/*/ \
    --queries eval/charmed-mysql-log-autopsy-train.json \
    --out eval/runs/la-train-v3.jsonl
$ profe summarize eval/runs/la-train-v3.jsonl --format table

# corroborate with a second model (a dedicated file with just the
# failing / 0.3–0.7-band queries)
$ profe probe --skills skills/*/ \
    --queries eval/charmed-mysql-log-autopsy-train.json \
    --model deepseek/deepseek-v4.1-flash \
    --out eval/runs/la-train-v3-ds.jsonl

# fresh 5-skill baseline
$ profe probe --skills skills/*/ --queries eval/queries.json \
    --out eval/runs/v7-baseline.jsonl
$ profe summarize eval/runs/v7-baseline.jsonl --out eval/reports/v7-baseline.json
```

## Explanation

Profe has one core concept, an **attempt**
(one headless `pi --mode json` invocation with the selected skills
loaded),
and a verb per job around it.

Some design considerations were taken into account:

- **Serial execution.** Relaxed parallelism has repeatedly produced
  degraded completions from the API provider that look exactly like skill
  failures (fast, confident, wrong); every generation that tried it paid
  for it in bad numbers. If wall-clock ever forces concurrency back, it
  must be re-validated against a known-clean set first.
- **Neutral working directory, no hint, no session, no extensions.**
  The query text is the only per-run variable; anything else in context
  measurably pollutes or biases the trigger decision.
- **Skill loading is pinned.** Profe forwards the explicitly listed
  `--skills` paths with `--no-skills`, so the recorded `loaded_skills`
  list is exactly what ran: an implicitly discovered skill cannot enter a
  run and steal a trigger decision.
- **Trigger detection is mechanical.** A trigger is a skill's SKILL.md
  read, detected from the headless agent's event stream
  (`tool_execution_start` with a read/bash tool whose arguments contain
  `<skill-name>/SKILL.md`). It is never inferred from the answer text.
- **Evidence survives every kill.** Events are streamed and recorded as
  they happen, so a run killed at the timeout or by early exit keeps its
  trigger state and usage.
- **Every reported number records its protocol, model, skill count and
  cutoff.** Trigger behavior is model-dependent and skill-set-dependent;
  a number without these is not interpretable.
- **All state is resumable.** An interrupted batch costs at most its
  in-flight attempt.

### Probe

Every query names the skill(s) it is meant to trigger via `expected_skills`
— always a **list**, any-of semantics; a single expectation is a
one-element list:

```json
{"query": "...", "expected_skills": ["charmed-mysql-juju-inspect"]}
{"query": "...", "expected_skills": ["charmed-mysql-log-autopsy", "charmed-mysql-nightly-triage"]}
```

An **empty list** is a should-not query: no skill should fire. The shape
makes the "no free-floating should-trigger queries" rule structural —
there is no way to say "should trigger" without naming who, which is
exactly the point: an "any skill triggered" fallback would silently pass
the misroutes that are the failures worth catching (e.g. an autopsy
query deterministically pulled to a triage skill by a keyword).

Startup checks `probe` runs on every queries file: the schema
declaration must be present and 1, unrecognized fields are forbidden
(an input the format did not design for is rejected rather than guessed
at), every `expected_skills` name must be among the `--skills` paths
(a query expecting a skill that is not loaded would silently never
pass), and no field may hold a bare string where a list is required.

A run is a **hit** when any expected skill triggered; a run that triggers
only some *other* skill is a misroute, i.e. a miss, and escalates. The
expectations drive the adaptive escalation and the per-skill pass rates
in `summarize`; a should-not run passes only when nothing triggered.

Implements the simplified Phase-1 lifecycle:

- Trigger early exit: a run is killed the moment its trigger question is
  answered — on the first skill read (`early_exit: "trigger"`, whatever
  the query class: for a should-not query that is a false positive), or,
  with no skill read, after `--max-tool-calls` tool calls
  (`early_exit: "max_tool_calls"`; the cutoff applies to both classes —
  a should-trigger run with no read within the cutoff is non-trigger
  evidence modulo the documented cutoff approximation). Phase-1 answers
  are truncated by design and never graded.
- Adaptive runs (`--num-runs :auto:`, the default): one run per query;
  escalate (cap 3) only on non-decisive outcomes — a miss that could still
  pass (should-trigger runs that have not triggered any expected skill
  yet, while a pass is still reachable within the cap), a should-not run
  that triggered (corroborate a false positive before concluding), an
  errored or timed-out run (errors are not trigger evidence, even when a
  trigger was preserved), or a boundary rate (exactly 0.5) that one more
  run could flip. Most clean queries conclude on the first run; boundary
  cases reproduce fixed-N=3 semantics. Transient errors are retried once
  within a run. An explicit `--num-runs N` fixes N runs per query with no
  escalation — for instrument validation and per-(query, run) agreement
  checks against a previously produced runs file.
- `--max-tool-calls` is a protocol constant calibrated from recorded runs
  (every observed trigger on the default model fired at tool-call #1,
  66/66 across two clean sets; 12 keeps headroom for models that explore
  before reading — see the calibration note in the project's eval
  directory). Pick it once per model, record it in every report footer and
  in every record's condition metadata, and change it only deliberately.
- JSONL output: one record per attempt, carrying the **attempt core**:
  `schema` (record/report format version; 2 = nested cost breakdown, 1 = legacy flat cost — both readable), `index`, `query`,
  `expected_skills` (verbatim echo of the query's expectations — the
  same list; `[]` for should-not), `run`, `attempt`, `clean`,
  `triggered` map for all loaded skills, `first_trigger_skill`,
  `trigger_call_index` (1-based index counting ALL tool calls, not just
  skill reads), `early_exit` (`trigger` | `max_tool_calls` | null),
  `timed_out`, `errored`, `exit_code`, `error`, `retries_seen`,
  `stderr_tail` (drained pi stderr, for Case-E diagnosis), `answer`
  (truncated to 2000 chars — Phase-1 answers are truncated by design
  and never graded), `n_tool_calls`, `usage` (tokens/cost, summed over
  streamed messages — Phase-2 overhead accounting reads this even from
  Phase-1 files), `dur_s`, and condition metadata (`model`,
  `loaded_skills` list, `queries_file`, `max_tool_calls`, `num_runs`,
  `ts`). Crash-safe and resumable: a query with a decisive conclusion is
  skipped on re-run. Input and output match by name: every record
  carries `expected_skills` and `loaded_skills`, and the gap between
  them is exactly what the run measured.

### Summarize

First clean attempt per run drives every rate. A run is a **hit** when
any expected skill triggered; a run that triggers only non-expected
skills is a **misroute** and counts as a miss. Queries with a non-empty
`expected_skills` pass when the hit rate is ≥ 0.5; empty ones (should-not)
pass when nothing triggered, at rate ≤ 0.5. The two classes are derived
from whether `expected_skills` is empty (not stored separately), and
every reported number records the protocol, model, skill count and
cutoff it was produced under.

Expectations drive three views over the same runs:

- **Per-skill rows** (table and JSON): for each skill, over the queries
  that list it, the rate of runs where *that* skill triggered; PASS at
  ≥ 0.5. A one-element list makes the skill's row identical to the query
  decision — misroutes show up as the failures they are. An any-of list
  passes the query on any member, but each listed skill's own row counts
  the query by whether *that* skill triggered, so a persistently
  one-sided routing shows up as a 0-rate for the other member: that is
  the data a dual-trigger policy decision needs.
- **Cross-trigger rows**: for each skill, the rate at which it triggered
  on runs where it was not expected — should-not runs included, where
  any trigger is a false positive. One rate per skill and class: the
  misroute table.
- **Per-query entries** (JSON only): query, expectations, per-run
  trigger state, hit/misroute outcome, decision — the view that
  description iteration actually works from.

Effort medians over the first-clean attempts (input/output tokens, cost,
tool calls, wall time) are reported alongside the rates, overall and per
query class. Under early exit these measure the cost of the *trigger
decision*, not of the task (Phase-1 answers are truncated by design): they
say how expensive it is to get this model to route, and what the
measurement itself cost. Phase-2 effort accounting reads the full
attempts instead.

### Grade, compare, report (WIP)

Same attempt concept, full lifecycle: no early exit (the whole attempt is
what gets scored), the task's verdict contract injected into the prompt by
the harness, mechanical grading against versioned rubrics. `compare`
computes paired per-query deltas between cells (skills off/on, model
variants); `report` renders graded runs into the durable deliverable.
Every record keeps the shared attempt core plus condition metadata (model,
loaded_skills, hashes), so files stay self-describing, resumable and
comparable across phases.
