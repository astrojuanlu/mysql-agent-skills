# Profe

![Warning: Vibe Coded](https://img.shields.io/badge/%E2%9A%A0%EF%B8%8F_warning-vibe_coded-orange?style=flat)

Evaluate skills following a structured methodology.

One lifecycle: `validate` → `compile` → `run` → `grade` → `report`.
`validate` checks that the skills follow the [Agent Skills spec].
`compile` turns trigger queries into tasks and rubrics (offline). `run`
collects one attempt into a capture bundle — on the host or inside any
sandbox boundary the caller provisions. `grade` scores that bundle
mechanically, offline. `report` renders graded runs into the durable
deliverable; given two runs files it becomes a paired comparison.

[Agent Skills spec]: https://agentskills.io/specification

## Usage

Two kinds of session, one lifecycle — every flag is in
[Reference](#reference), the reasoning in [Explanation](#explanation).
Worked format examples — a trigger-queries file, task definitions, and
rubrics — live in [`examples/`](examples/); the artifact files the example
tasks reference are deliberately not included.

### A typical trigger-cell session

A **queries file** declares its format version and carries the query
objects under `queries`. `expected_skills` names the skill(s) the query
should trigger — always a **list**, any-of semantics; an **empty list**
marks the near-misses every skill must stay away from (should-not). The
schema declaration is mandatory.

```json
{
  "schema": 1,
  "queries": [
    {"query": "Is the Charmed MySQL cluster in model testing healthy right now? Check the topology and tell me who is primary.",
     "expected_skills": ["charmed-mysql-juju-inspect"]},
    {"query": "Optimize this MySQL query: SELECT * FROM orders WHERE created_at > NOW() - INTERVAL 30 DAY; it's slow and I need it faster.",
     "expected_skills": []},
    {"query": "Test Observer execution 659755 failed on artefact 409577 — pull the artefact and tell me what happened.",
     "expected_skills": ["charmed-mysql-juju-inspect", "charmed-mysql-nightly-triage"]}
  ]
}
```

`compile` turns the queries file into tasks and rubrics, offline: one
task per query (the id is a content hash of the query text), one rubric
per distinct expectation set (`triggered_any_of` checks for the
should-trigger queries, a `triggered_none` check for the should-not
ones). Then the session is the same round trip as any task cell:

```
$ profe validate --skills skills/*/
$ profe compile --skills skills/*/ --queries eval/phase1/juju-cli-train.json
$ profe run --task-id q-3f2b… --tasks eval/phase1/tasks-trigger.jsonl \
    --stop-on skill-read --max-tool-calls 12 --timeout 300 \
    --skills skills/*/ --out OUT/bundle-smoke --extra-metadata env=host
$ profe grade OUT/bundle-smoke --tasks eval/phase1/tasks-trigger.jsonl \
    --rubrics eval/phase1/rubrics --out eval/runs-t2/smoke.jsonl
$ profe report eval/runs-t2/smoke.jsonl
```

The trigger rubrics carry no verdict contract: the run ends at the
first skill read (`--stop-on skill-read` — a should-not query that
reads a skill has already misrouted and is stopped too), and the
rubric's trigger checks read the replayed tool-call stream, so the
graded record carries the routing evidence: `triggered`,
`first_trigger_skill`, `trigger_call_index`, `expected_skills`. The
report renders the routing shapes — per-skill rates, cross-trigger
(misroute) rows, the should-not check and per-run verdicts — from the
records alone.

`--max-tool-calls` bounds a run that never reads a skill (the cutoff
applies to both classes — a should-trigger run with no read within the
cutoff is non-trigger evidence modulo the documented cutoff
approximation). Pick it once per model, record it via
`--extra-metadata`, and change it only deliberately (the calibration
note lives in the project's eval directory).

### A typical task grading session

A **task** (JSONL row) pairs a prompt with a verdict contract, a rubric
id and its artifacts (the contract mentions a ``` fence, so this block
uses four-backtick fencing):

````json
{"id": "la-327",
 "prompt": "Please analyze the debug-log.txt and db-dump.yaml attached (from canonical/mysql-operators#327, …). I need a root cause analysis.",
 "contract": "End your answer with a fenced ```yaml block (the only one in your answer) with exactly these fields:\nverdict: charm-bug | infra | user-error | flake\nroot_cause: a one-sentence root cause\nevidence_quotes: …",
 "rubric": "la-327",
 "artifacts": ["la-327/debug-log.txt", "la-327/db-dump.yaml"],
 "precheck": null}
````

The **rubric** (`eval/rubrics/<id>.yaml`, versioned) is mechanical —
verdict-block checks, evidence grounded in the artifacts; the check
types are listed under `profe grade` in [Reference](#reference).

One attempt = one capture bundle, the whole answer this time: no stop
condition, no yield. On the bare host (the control cell):

```
$ profe run --task-id la-327 --tasks eval/tasks/log-autopsy.jsonl \
    --artifacts-dir eval/tasks-artifacts --out OUT/bundle-host \
    --skills skills/*/ --extra-metadata env=host
```

Inside a boundary it is the same command — the caller provisions the
boundary (toolchain at the same absolute paths, sanitized pi config,
skills/artifacts pushed or mounted) and pulls the bundle out; working
recipes, one per boundary, live in `profe/scripts/`. The task
**precheck**, if declared, executes at run time — inside the boundary,
where live-cluster ground truth is reachable — and lands in the bundle;
`--pi PATH` pins the instrument, `--model M` overrides pi's default.
The boundary can be destroyed once the bundle is out.

Grading is offline — on the runner's host or anywhere else:

```
$ profe grade OUT/bundle-host --tasks eval/tasks/log-autopsy.jsonl \
    --rubrics eval/rubrics --artifacts-dir eval/tasks-artifacts \
    --out eval/runs/la-327-host.jsonl

$ profe report eval/runs/la-327-host.jsonl            # single-cell report
$ profe report eval/runs/la-327-off.jsonl eval/runs/la-327-host.jsonl
                                                      # A-vs-B comparison
```

`--skills` on grade is record metadata only — the bundle's sidecar
carries the skills that actually ran; the literal `none` (the default)
records the skills-off counterfactual condition for hand-assembled
bundles.

```
# a retry is a fresh boundary — label it --run N with a fresh --out
# and grade that bundle too

# the record format is the same whoever ran the attempt (host, VM, CI
# machine) — stamp the boundary with --extra-metadata env=… so cells
# stay attributable
```

## Reference

### `profe validate`: structure check

Thin wrapper over `skills-ref validate`: frontmatter, name/description
limits, body budget. Validates *structure only* — it cannot assess
trigger behavior (that's the trigger rubrics' job) or answer quality
(`grade`).

```
profe validate --skills <path>...
```

### `profe compile`: queries → trigger-cell tasks + rubrics (offline)

One task per query, one rubric per distinct expectation set. Pure
offline rewriting: no pi launch, no network. The tasks file feeds
`profe run`, the rubrics dir feeds `profe grade`.

```
profe compile --skills <path>... --queries eval/smoke.json
              [--tasks eval/tasks-trigger.jsonl]
              [--rubrics-dir eval/rubrics]
```

- `--skills <path>...` loads the skills so the expectations can be
  checked against them (every `expected_skills` name must be among the
  `--skills` paths — a task expecting a skill that is not loaded would
  silently never pass); at least one is required. Loading everything is
  a shell glob away: `--skills skills/*/`.
- Startup checks on the queries file: the schema declaration must be
  present and 1, unrecognized fields are forbidden (an input the format
  did not design for is rejected rather than guessed at), and no field
  may hold a bare string where a list is required.
- Task ids are content hashes (`q-<hash8(query)>`); rubric ids hash the
  expectation set (`trig-<hash8(expectations)>`). A query compiled
  twice compiles to the same id; identical expectation sets share one
  rubric file. Empty expectation lists get their own rubric id (a
  should-not class of its own).

### `profe run`: one attempt, evidence only

Runs a **single task attempt** and records everything about it as a
capture bundle — no rubric, no scoring, no retries (one invocation is
one attempt; batching and re-running after failure are the caller's
loop, ideally one boundary per attempt). This is the verb meant to run
*inside* a sandbox boundary (see
[the boundary-agnostic split](#the-boundary-agnostic-split)): it needs
only pi on PATH, the tasks file and the task's artifacts.

```
profe run --task-id mysql-511 --tasks eval/tasks/live-bugs.jsonl
          --artifacts-dir eval/tasks-artifacts --out OUT/bundle
          [--skills <path>...] [--model M] [--timeout 600]
          [--stop-on skill-read] [--max-tool-calls N]
          [--pi-config DIR] [--pi PATH] [--extra-metadata KEY=VALUE...]
          [--run 0]
```

- `--out DIR` receives the **capture bundle**: `prompt.txt` (the exact
  prompt, contract included), `stream.jsonl` (profe's own tee of pi's
  event stream — written live, flushed line by line, so it grows while
  the attempt runs), `session.jsonl` (pi's own session file, copied
  verbatim from pi's scratch home **after the attempt ends** — it does
  not exist mid-run; if pi dies before persisting one, the run fails
  loudly) and `capture.json` (sidecar: task/run identity, wall
  time, pi exit code and version, the forwarded skill paths, any stop
  reason, session source). When the task declares a `precheck`, it
  executes as part of the attempt and lands in the bundle too
  (`precheck.json`, `precheck-output.txt`) — the grader then needs no
  live cluster, which is what makes grading on a Testflinger machine or
  after the boundary is destroyed possible.
- `--skills` pins the skill dirs forwarded to pi (validated first) —
  the same loading discipline as compile/grade.
- `--stop-on skill-read` is the trigger-cell condition: the attempt is
  killed at the first SKILL.md read of any loaded skill, and the run
  ends there (a misroute stops too). Without it, the whole answer is
  collected.
- `--max-tool-calls N` is the tool-call cap, decoupled from the
  skill-read yield: it bounds a should-not run that never reads (the
  cutoff behind the trigger measurement) and any task attempt that
  wanders. A stopped attempt is recorded, not an error: the sidecar
  carries the stop reason (`early_exit`), the answer carries an inline
  marker.
- `--pi-config DIR` is a pi config directory copied into a fresh
  scratch HOME for the attempt; the `packages` settings key is stripped
  (pi auto-installs that list at startup, which both breaks restricted
  boundaries and makes attempts depend on the npm registry). Default:
  `~/.pi/agent`.
- `--pi PATH` pins the instrument explicitly (entirely optional;
  default: resolve `pi` on PATH, first entry wins — e.g. a pinned
  `/opt/pi/...` prefix in a VM). The flag exists because a PATH leak
  silently swaps instruments (a harness pi instead of the pinned one
  parses differently downstream); the resolved binary is validated and
  recorded in the sidecar (`pi_path`) either way.
- `--extra-metadata KEY=VALUE...` stamps free-form provenance labels
  (e.g. `--extra-metadata env=lxd-vm:ubuntu-24.04 provision=<hash>`) —
  how the caller records *which boundary* produced a bundle. Labels
  ride into the sidecar and are copied into the grade record
  (`extra_metadata`); absent labels are simply omitted. Duplicate or
  malformed items fail the run before pi starts.
- Artifacts are staged read-only into a fresh per-attempt temp dir, and
  the attempt runs in a neutral cwd, same protocol constants as grade.

### `profe grade`: score a capture bundle mechanically (offline)

Grades the attempt captured in a **capture bundle** — its composition
is listed under `profe run`. Bundles are `profe run`'s normal output
(or can be assembled by hand, same layout). A **task** (JSONL: `id`,
`prompt`, `contract`, `rubric`, `artifacts`, optional `precheck`) is
scored against its **rubric** (`eval/rubrics/<id>.yaml`: an id, a
version, and a list of checks). Grading is deterministic and LLM-free,
and launches nothing — no pi, no subprocess, no network:

- the **verdict block** is the last fenced ` ```yaml `/` ```json ` block
  of the answer, parsed with `yaml.safe_load`; `schema_ok` records
  format compliance separately from correctness (a weak model may fail
  at producing the block, not at the substance) — None for tasks with
  no contract;
- `field_equals` / `field_regex` / `field_not_regex` / `field_present`
  operate on the parsed verdict (dotted paths reach into nested maps);
- `evidence_in_artifacts` grounds every quoted line against the task's
  artifacts (word-subsequence semantics: wrapped lines, skipped caret
  lines, `" / "` joins and appended annotations are forgiven —
  paraphrase and fabrication are not);
- `skill_triggered` / `triggered_any_of` / `triggered_none` read the
  replayed tool-call stream (the trigger checks — grade-time `--skills`
  cannot influence them): a trigger is a skill's SKILL.md read, detected
  from the session's tool-call events (`tool_execution_start` with a
  read/bash tool whose arguments contain `<skill-name>/SKILL.md`), and
  every record carries the `triggered` map plus the rubric's
  `expected_skills`, so the report's routing shapes stay runs-file-only;
- `precheck_field_equals` compares a verdict field with a field of the
  task precheck's JSON output — recorded at run time inside the
  boundary, never baked into the rubric and never executed by the
  grader. A precheck-less bundle for a task that declares one is a hard
  error.

The model is not an input: it is extracted from the session's message
payloads — the record carries what actually ran, not what a caller
asked for (pi's own fallbacks included).

```
profe grade eval/runs-t2/<name>-bundle \
    --tasks eval/tasks/log-autopsy.jsonl \
    --rubrics eval/rubrics --artifacts-dir eval/tasks-artifacts \
    [--skills <dir>... | none] --out eval/runs-t2/<name>.jsonl
```

`--skills` is record metadata only, and only for hand-assembled bundles
without sidecar skills (the `loaded_skills` condition — the attempt
already ran); `none` records the skills-off condition. Output
is crash-safe and resumable (a (task, run) pair with a clean attempt is
skipped on re-run).

### `profe report`: render graded runs into the durable deliverable

Per-task scores with failed checks named, condition metadata, verdict
blocks and effort medians — the shareable record of a graded cell. A
trigger cell additionally gets the **routing** section: per-skill rows
(should-trigger rate at ≥ 0.5 = PASS), cross-trigger (misroute) rows,
the should-not check (any-trigger rate ≤ 0.5) and per-run verdicts
(hit / misroute / miss) per task in the JSON format.

Given exactly two runs files it becomes a paired comparison: score
deltas, per-check flips (gained/lost), schema-compliance deltas and
effort deltas (tokens, tool calls, wall time, cost) with aggregates.
Interleaved collection over back-to-back cells is preferred
(decorrelation); the comparison only records what it was given.

```
profe report eval/runs-t2/<name>.jsonl [--out FILE] [--format md|json]
profe report eval/runs-t2/a.jsonl eval/runs-t2/b.jsonl \
    [--out FILE.json] [--format md|json]
```

### The grade record

One capture bundle's outcome plus its condition (schema 5; the schema
may change between versions — old files are regenerated or converted as
needed): `task_id`, `rubric_id`, `rubric_version`, `contract_sha256` /
`rubric_sha256` (content hashes — rubric edits invalidate old scores by
construction), `schema_ok`, `scores` (per-check id → bool),
`rubric_score` (mean over checks), `verdict` (the parsed block),
`precheck_output` (the recorded precheck run), the trigger fields
(`triggered`, `first_trigger_skill`, `trigger_call_index`,
`expected_skills` — stamped by the rubric, `[]` for a should-not
class), the attempt facts (`query`, `answer`, `tool_calls`, `usage`,
`dur_s`, `clean`, `exit_code`, `early_exit`, …) and the conditions
(`model`, `loaded_skills`, `tasks_file`, `rubrics_dir`, `pi_version`,
caller-supplied `extra_metadata`).

Trigger-cell records come from the yielded attempt: the answer may be
empty (`--stop-on skill-read` ends the run at the read), and `tool_calls`
entries (`tool` + trimmed `args`, cap 500 calls / 400 chars per arg)
stop at the yield. Task records carry the full action transcript.
Answers are kept up to 30 000 chars — verdict blocks must survive.

### `profe.stats`

Implementation of paired-experiment statistics (exact sign-flip permutation,
paired t, Wilcoxon–Pratt, exact McNemar, Jeffreys + Clopper–Pearson bounds,
Miettinen–Nurminen score CI) on scipy/numpy, stdlib-deterministic at the API level.

```python
from profe.stats import signflip_exact, jeffreys_upper
```

The exact permutation keeps a meet-in-the-middle enumerator (scipy's
exact test enumerates all 2^n sign vectors and cannot honor the n ≤ 40
guarantee); the Miettinen-Nurminen CI keeps its own score root-finder.
Each function's docstring cites its source (paper, textbook, or
standard tables). `tests/test_stats.py` anchors every function to
EXTERNAL ground truth only — published tables, analytic closed forms
and identities, definitional brute-force enumeration, and defining
equations re-solved with an independent root-finder — and
`profe.stats.selftest()` re-runs the textbook/brute-force checklist.

### `profe.bundle`

Reading and validating attempt bundles — the artifacts profe itself
writes. `session_usage(path)` walks one `session.jsonl` (component
usage sums, tool-call count, skill reads); `read_bundle(dir)` returns a
`BundleRecord` of the sidecar, usage, grade and runner-status facts and
raises `BundleFormatError` on anything that is not a current-format
bundle (missing/unparseable required artifacts — era-compatibility
shims for older writers were removed); `validate_bundle(dir)` returns
the list of validity reasons (empty = valid) and defines that format.
Reading is strict about format, tolerant about value:
`runner-status=incomplete` reads fine (quarantine adjudication needs
the value) and a truncated session tail is skipped, not fatal.
Stdlib-only by design, so lightweight consumers —
recovery drivers, sheet tooling, monitors — parse bundles without
profe's third-party dependencies; `profe.models.CaptureSidecar` and
`GradeRecord` are the strict typed twins. Scope boundary: only profe's
own artifacts are read here — price vectors, forensics counters and
termination adjudication stay with the consuming campaign.

## Explanation

Profe has one core concept, an **attempt**
(one headless `pi --mode json` invocation with the selected skills
loaded), and a verb per job around it: compile turns trigger queries
into the task/rubric shape, run collects one attempt into a capture
bundle, grade scores that bundle offline into the one record format,
report renders the records.

Some design considerations were taken into account:

- **Serial execution.** Relaxed parallelism has repeatedly produced
  degraded completions from the API provider that look exactly like skill
  failures (fast, confident, wrong); every generation that tried it paid
  for it in bad numbers. If wall-clock ever forces concurrency back, it
  must be re-validated against a known-clean set first.
- **Neutral working directory, no hint, no session reuse, no
  extensions.** The query text is the only per-run variable; anything
  else in context measurably pollutes or biases the decision.
- **Skill loading is pinned.** Profe forwards the explicitly listed
  `--skills` paths with `--no-skills`, so the recorded `loaded_skills`
  list is exactly what ran: an implicitly discovered skill cannot enter a
  run and steal a trigger decision.
- **Trigger detection is mechanical.** A trigger is a skill's SKILL.md
  read, detected from the headless agent's event stream. It is never
  inferred from the answer text — so a yielded trigger-cell record
  grades identically whether or not the answer survived the stop.
- **Evidence survives every kill.** Events are streamed and recorded as
  they happen (`stream.jsonl`, flushed per line), so a run killed at the
  timeout or by a stop condition keeps its trigger state and usage.
  pi's own session persistence is always on — the canonical capture is
  the session file, copied out verbatim once the attempt ends. Grading
  replays the session file by default; when the runner killed the
  attempt, pi's session may have lost in-flight tool calls at the flush
  boundary, and the stream tee is the replay source instead.
- **Every reported number records its protocol, model, skill count and
  cutoff.** Trigger behavior is model-dependent and skill-set-dependent;
  a number without these is not interpretable.
- **All state is resumable.** An interrupted batch costs at most its
  in-flight attempt.

### The one attempt, and its stop conditions

An attempt runs with the task's prompt (contract included) and nothing
else — the same shape for a trigger cell and a task cell; what differs
is only whether a stop condition is armed and what the rubric claims.

- **Trigger cells** arm `--stop-on skill-read`: the run is killed the
  moment any loaded skill's SKILL.md is read — that is the trigger
  decision, and reading past it buys nothing (for a should-not query
  the first read is already the false positive to record). The yield
  ends the attempt; the graded evidence is the replayed tool-call
  stream.
- **Task cells** run to completion: no stop condition, the full answer
  gets graded. `--max-tool-calls` stays available as the runaway guard
  for both.
- A stop is recorded, not an error: pi is killed cleanly, the sidecar
  carries the stop reason (`early_exit: "skill-read"`), the answer
  carries an inline marker, and the timeout case keeps its own marker.

### Rubric checks read transcript regions

Every check type reads one region of the captured attempt, and the
regions partition the record:

- the **verdict block** (the last fenced yaml/json block of the answer):
  `field_equals`, `field_regex`, `field_not_regex`, `field_present`;
- the **artifacts**: `evidence_in_artifacts` (grounded quoting);
- the **precheck output** (recorded inside the boundary at run time):
  `precheck_field_equals` — live-cluster ground truth without a live
  cluster at grade time;
- the **tool-call stream** (the replayed session events):
  `skill_triggered`, `triggered_any_of`, `triggered_none` — the trigger
  checks. A `skill_triggered` check claims one skill's read; a
  `triggered_any_of` check claims any member of a skill set (the
  should-trigger class); a `triggered_none` check forbids all reads
  (the should-not class).

The compiler maps queries onto exactly these: an expectation set
becomes one `triggered_any_of` rubric, an empty one a `triggered_none`
rubric — so a trigger campaign and a task campaign share one task
pipeline, one record format and one report.

### Effort: yielded vs full

Effort medians (tokens, cost, tool calls, wall time) are computed from
the records alone. A trigger cell's numbers measure the cost of the
*trigger decision* (the run ended at the read); a task cell's measure
the full attempt. Both are useful, they answer different questions, and
the record's `early_exit` field says which one a number is.

### The boundary-agnostic split

`run` (collect) and `grade` (score) are split so that rubrics and the
grader stay out of the environment an attempt runs in: the runner
cannot load rubrics (its CLI has no such flag), and the grader reads a
bundle and writes a record.

Attempts do not have to run on the host. The grading side (`grade`,
`report`) never runs pi and never touches a live cluster — it consumes
capture bundles — so whoever runs the attempts can do so inside any
boundary the caller cares to draw. Profe is boundary-agnostic: **the
caller provisions the boundary, profe runs inside it, grading happens
outside it** (after which the boundary can be destroyed).

Division of labor:

- **Caller** picks the boundary and makes the toolchain visible inside
  it *at the same absolute paths*: the profe venv and its editable
  source trees (console-script shebangs are absolute; `.pth` files
  point at the sources, so the source trees must ship too), the
  uv-managed python, `skills-ref`, the pinned pi, the skills tree and
  the tasks/rubrics-adjacent artifacts. A sanitized pi config (no
  `packages` key) rides in as input; `run` copies it into its own
  scratch HOME regardless.
- **profe inside** runs the same faces everywhere — `validate` →
  `compile` → `run` — and hands the capture bundles out. Measured
  result: trigger behavior is identical across host, bubblewrap, podman
  and LXD-VM boundaries on smoke queries; the boundary does not move
  trigger decisions.
- **Grader outside**: `grade <bundle>` scores the bundle mechanically
  into the same record format — model included, extracted from the
  session; `report` consumes it unchanged, whether the cell was
  captured on the host or inside a boundary.

Working recipes, one per boundary, live in `profe/scripts/`:

```
profe/scripts/poc-bwrap.sh    # mounts-only bubblewrap sandbox
profe/scripts/poc-podman.sh   # rootless container, no image build
profe/scripts/poc-lxd.sh     # disposable LXD VM (cloud-init + tar push)
```

Each is a complete round trip: provision → validate/compile/run inside →
bundles out → boundary destroyed → `grade <bundle>` on the host. Read
them for the per-boundary specifics: what must be mounted, pushed or
pre-fetched, where outputs land, and which traps were hit and
documented inline (missing editable source trees, uv python symlink
names, image pre-fetching).
