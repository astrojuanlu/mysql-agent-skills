# c3 rig kit — Testflinger boundary layer (job spec + launch/recover)

The Testflinger rig kit for campaign 3 (c3 DESIGN §3): job template, the
on-machine attempt runner, the host-side launcher and recovery scripts, and
the campaign loop. This is the `driver-testflinger.sh`
boundary layer anticipated by `../../research/PROFE-TOOLING-RETRO.md` (B3):
boundary-specific logic lives here as verbatim chokepoint scripts; the
attempt state machine (states, ledger, monitor, settle, resume) lives in
`profe.driver` (the PROFE-DRIVE consolidation, 2026-10-18) and this kit's
`campaign.py` wires it to the boundary via `testflinger_sandbox.py`.

Provenance: the recipe was validated end-to-end on a DL380 Gen11 lab rig
2026-10-16 — the five-probe rehearsal (c3 DESIGN §3.6). The twin mechanics
(coin, twin submits, live caps, ground truth, recovery) were rehearsed on
the Gen10 fleet with this kit's dev-branch twin (`eval/runs-t6/mr-234/
c3-rehearsal/` on the development branch); twin pair 1 (on/off as drawn,
seed 20261031) completed both arms and produced the first
c3-shaped rows. This directory is the c3 port: same validated rig body,
c3 names/paths, no per-machine deltas. (The dev kit's own runbook stays
on the development branch; a verbatim copy of it here was removed
2026-10-16 — operator decision — in favor of this reconciled manual;
see the documentation-fold entry in the delta log below.)

## Layout

- `pi-rig-template.yaml` — the rig job TEMPLATE (`__TWIN_QUEUE__` placeholder;
  persistent deploy; 6 h global lease / 5 h reserve / rig-only test_cmds;
  `~/rig-ready` marker; egress probes; hardware ground truth; `rig-env.sh`
  with pinned PATH, `UV_PYTHON=3.14`, `CAMPAIGN_CONTROLLER`). No secrets,
  no attempt. Pinned at c3 start (P2). Renamed from `pi-mr234-rig.yaml`
  (2026-10-18, pool mode — the template is task-agnostic).
- `launch-payload.sh <ip> <pair> <off|on> [attempt]` — stage payload +
  skills (ON arm only, §3.2.3) + pi config over SSH during reserve; uv
  sync fast-fail; launch the attempt in detached tmux `c3-attempt`.
- `run-attempt.sh` — runs ON the machine (staged to `~/payload/`): guard
  rails (fresh `testing` model, foreign-model sweep, models-before gate),
  profe run (4 h ops wall, budget caps enforced host-side), 25-min
  stream-stall watchdog, grade, record-only forensics (`incidents.py`,
  §3.5/§4.6 — nothing gates) with hygiene-destroy of leftover models.
  Ships the raw bundle + `runner-status` / `leftover-models.txt` /
  `profe.pid` for the host driver.
- `incidents.py` — the record-only forensics recorder (c3 §3.5/§4.6):
  walks the session and writes `incidents.json` counters (model
  switches/destroys/adds/bootstraps, gh commands, git
  clone/fetch/pull/ls-remote segments (`git_sources` — the contamination
  adjudication signal, §6), `~/Projects` references
  for the §4.6.3 stop-and-redesign tripwire, home writes outside the
  workspace, standalone `bug234` mentions). Replaces leak-check v2.8's
  gate; always exit 0 on a readable bundle — infra reruns vs agent
  behavior is the §4.6 two-class rule, adjudication stays with the driver.
- `run-pair.sh <pair#> <queueA> <queueB>` — host-side twin-pair driver
  (rehearsal-grade, c2-era): seeded coin (`seed = 20261030 + pair#`, P7), twin
  submits, rig waits, live §4.4 cap enforcement ($0.30 / 15 M tokens /
  250 calls) via `sample-session.py` polling, 60-min zero-activity infra
  kill, 4 h wall kill, per-machine ground truth + egress dump, recovery,
  host-side sheet rows, off-vs-on comparison, release. SUPERSEDED as the
  campaign driver by `campaign.py` (kept as the single-machine ops
  fallback and the mechanics reference).
- `campaign.py` — the campaign loop (was `driver-v3.py`, retired by the
  PROFE-DRIVE consolidation): B2 manifest pre-registration, P7 coin +
  queue mapping, block boundaries, the pair loop with the §4.3 skip
  decision (exhausted slot → ledger-skip note, never a pause), sheet
  rows and the off-vs-on comparison. All attempt mechanics come from
  `profe.driver` (states, ledger, monitor, settle, resume) via
  `testflinger_sandbox.py`.
- `testflinger_sandbox.py` — the thin adapter: `profe.driver`'s
  AttemptSandbox protocol mapped onto the verbatim boundary verbs (no
  profe import — structural conformance). Capability data:
  `reprovisions_on_resubmit=True`, `recover_releases_lease=True`.
- `test-campaign.py` — the campaign-side tests: the P7 coin pinned
  against the rehearsal pairs, B2 drift refusal, the skills-tree hash,
  the §4.3 skip in the loop, dry-run purity. The attempt machinery's
  negative suite lives in `profe/tests/test_driver.py`.
- `sheet-row.py` — one c3-extended run-sheet row from a recovered bundle
  (csv.writer — never hand-join fields; `incidents` cell derived from
  `incidents.json`, legacy bundles fall back to `legacy-leak:<status>`).
  `--regen <state_dir>` (retro B8) rebuilds the sheet as a VIEW of the
  bundles: latest recovery per (pair, arm, attempt), non-integer pairs
  skipped, cap/wall/infra derived from bundle evidence, driver-state
  columns (slot/machine/job_id) written as "-" — verified byte-identical
  to the accepted twin rows on every bundle-derived column.
- `status.py` — READ-ONLY state viewer (retro A7, campaign-side):
  per-attempt termination class + validity + depth/verdict + usage/cost,
  events heartbeat age, manifest hash — derived from bundles via
  `../lib/bundle.py`, writes nothing.
- `sample-session.py` — runs ON the machine; one summary line per poll
  (calls, tokens, frozen cost, stream age, profe alive). Reads
  `stream.jsonl`, NOT `session.jsonl` — profe materializes the session
  only at run end, so a session-based sampler polls zeros all attempt
  long (live-cap blind spot, found during twin pair 1). Stream rules
  validated against the 901/on ground truth: identical numbers.
- `leak-check.py` — RETIRED from the c3 kit (operator, 2026-10-16): the
  gate never fired on this campaign and §3.5 makes leak rules forensics
  (the git_sources counter in incidents.py). If a c2-comparability run
  ever needs it, stage it by hand from the dev kit or the c2 kit.
- `recover-bundle.sh <ip> <job> [force]` — supplementary logs (juju
  debug-logs, k8s events/pod logs, hardware), bundle rsync back,
  credential scan (auth.json key AND models.json literal apiKey — both
  must be CLEAN), key scrub, early release via `testflinger cancel`.
- `state/` (untracked) — run-sheet.csv, events.jsonl, generated job yamls,
  ground-truth dumps, recovered bundles.

Task inputs live one level up (c2 content, verbatim): `../tasks/
mr-234.jsonl`, `../rubrics/diagnose.yaml`, `../tasks-artifacts/mr-234/`.
They stage onto the machine at the campaign-2 DUT layout
(`~/payload/eval/...`), so every profe invocation is byte-verbatim.

## Twin-pair operation (the c3 shape)

*Superseded 2026-10-18 (pool mode, manifest v2 — see delta log): the
_campaign_ submits both slots to the shared fleet queue
`<fleet-queue>`; machine assignment is whatever the scheduler
gives and is recorded per attempt (hostname event + rig ground truth).
The `run-pair.sh` two-machine path below is the c2-era reference and the
single-machine ops fallback; the campaign runs `campaign.py` (pool
gate, twin slots on the shared queue).*

```bash
cd <repo root>                                    # this worktree
eval/phase2/campaign3/rig/run-pair.sh 1 <machine-a> <machine-b>
```

One pair = two simultaneous jobs on two spec-matched DL380 Gen10 machines
(one job per machine's own queue, §3.7 as originally written). The driver
prints the coin assignment, streams per-slot progress, enforces caps,
recovers both bundles, appends rows to `state/run-sheet.csv` +
`state/events.jsonl`, and cancels both jobs. Machines are reimaged on the
next submit — the release is the cleanup (§3.2.6).

## Campaign operation (the c3 shape — PROFE-DRIVE cutover)

```bash
cd <repo root>                                    # this worktree
python3 eval/phase2/campaign3/rig/campaign.py \
  --state eval/phase2/campaign3/rig/state-campaign  # THE live campaign ledger
#   [--pairs 6] subset; [--dry-run] replay + plan only; [--resume-note …]
#   rehearsal ledgers (inert, read-only): state-v3pool (smoke pairs 5–7),
#   state-v3rehearsal, state/ (v1-era) — never write to them
```

One pair = two attempts (one per slot) on the shared fleet queue; the coin
decides which slot takes the ON arm. The loop prints the coin, streams
per-slot progress from profe's monitor, recovers both bundles, appends rows
to `state/run-sheet.csv` + `state/events.jsonl`, and releases the leases.
Reattach is automatic: a restart replays the event log and continues live
attempts (B1). An exhausted slot is SKIPPED per §4.3 (2026-10-18 operator
decision) with a ledger-skip note — the pair proceeds and ends when both
slots are terminal or exhausted.

## Single-machine ops path (kept for rig validation)

```bash
sed "s/__TWIN_QUEUE__/<queue>/" eval/phase2/campaign3/rig/pi-mr234-rig.yaml > /tmp/job.yaml
testflinger-cli submit --poll /tmp/job.yaml | tee /tmp/rig-build.log
#   wait for === RIG READY ===; the JOB_ID is in the poll output
testflinger-cli results <JOB_ID> | jq .device_info.device_ip
eval/phase2/campaign3/rig/launch-payload.sh <IP> <pair> on
tail -f <stream path printed by the script>   # or: tmux attach -t c3-attempt
eval/phase2/campaign3/rig/recover-bundle.sh <IP> <JOB_ID> [force]
#   if the DUT died before grading, re-grade on the host — the command
#   is printed by recover-bundle.sh
```

## Pre-flight

- `testflinger-cli queue-status <queue>` for every machine in the pair —
  re-check live before submit (the 2026-10-16 snapshot ages).
- OIDC flake (502 "Failed to communicate with OIDC provider"): retry
  `testflinger-cli login` a few times — known issue, see the playground's
  `testflinger-playground/data/MACHINES_NOTES.md`.
- Host-key rotation: every persistent-deploy submit rotates the machine's
  host key; all kit SSH/rsync pins `StrictHostKeyChecking=accept-new`.
- Workstation→lab reachability (the lab subnet) needs the VPN.

## Freeze status (§12 checklist)

Pinned here: the job template (timeouts, rig body, egress probes),
`UV_PYTHON`/`CAMPAIGN_CONTROLLER` env contract, the five skills staged
ON-only, the frozen price vector — now a pinned FILE, `../lib/frozen-prices.json`,
with one implementation (`../lib/cost.py`, retro A4; `sheet-row.py` and
`ANALYSIS-c3.py` both derive from it) — caps ($0.30 / 15 M / 250), coin
seed formula, 4 h ops wall, 25-min stream watchdog.
Landed 2026-10-16: `ANALYSIS-c3.py` (§5 pre-registered analysis, reads
bundles never the sheet), rubric v2 (blind mechanism-depth grading,
`rubrics/diagnose.yaml` v2, now machine-parse-verified), the shared
bundle reader (`../lib/bundle.py` — one reader/validator/termination
classifier), `sheet-row.py --regen` (B8) and `status.py` (A7,
campaign-side). The statistics helper lives in PROFE now:
`profe/src/profe/stats.py` (retro A4/B10's canonical home),
scipy/numpy-backed with numpy+scipy in profe's main dependencies, and
tested against EXTERNAL ground truth only (published tables, analytic
closed forms, definitional brute force, defining equations re-solved
with an independent root-finder — `profe/tests/test_stats.py`). Run
the analysis under the profe env:

    cd <repo>/profe && uv run python \
      ../eval/phase2/campaign3/ANALYSIS-c3.py [--state DIR] [bundles ...]

(sheet-row/status, the cost layer and `profe.bundle` stay stdlib-only
and run anywhere).
Resolved 2026-10-18 (see the launch entry in the delta log): the
driver framework moved into profe.driver (PROFE-DRIVE cutover,
live-proven on pair 7); the §5 sign-off batch is executed (depth
descriptive-only; mutations not recorded; the safety rule reads
Clopper–Pearson); the campaign manifest is re-signed as v3
(mr-234/diagnose, pool queue, 30 pairs); the frozen artifact's
pre-flight submit is the c3-start dry-run recorded there.

## Deltas & decision log

This section is the kit's judgment log — it ABSORBS the former
`../DEVIATIONS.md` (removed 2026-10-16, operator decision: deltas are
recorded in the document they affect; protocol decisions live in
DESIGN, machine-readable driver events in `state/events.jsonl`, retro
dispositions in `../../research/PROFE-TOOLING-RETRO.md` §6). All
entries are 2026-10-16.

### Port from the dev-branch rehearsal kit

Source: development branch `eval/runs-t6/mr-234/c3-rehearsal/`
(five-probe rehearsal + twin-pair rehearsal; probe record DESIGN §3.6).
The rig body is byte-faithful to the validated recipe except:

1. **Names/labels**: `run-rehearsal.sh` → `run-attempt.sh`; tmux
   `rehearsal` → `c3-attempt`; campaign label `tf-rehearsal` → `c3` plus
   an `attempt` field (default 1; rerun bookkeeping moves to driver v3).
2. **Task inputs re-homed** to `eval/phase2/campaign3/{tasks,rubrics,
   tasks-artifacts}/` (this tree's productized `eval/phase2/` has no
   root-level task dirs). The DUT-side staging layout (`~/payload/eval/…`)
   is unchanged, so every profe invocation stays byte-verbatim c2 shape.
3. **Leak-check env mechanism** (superseded by the retirement below):
   `CAMPAIGN_CONTROLLER` read from the environment, rig exports
   `jujuc-tf-36` in `~/rig-env.sh` — retires the dev kit's
   payload-overrides single-line delta; DUT and host ran the same file.
4. **`sample-session.py` promoted** from `payload-overrides/` to a kit
   file (it was never an override — kit-owned staged tooling).
5. **Driver state under `state/`** (untracked): run-sheet, events.jsonl,
   generated yamls, ground truth, recovered bundles — kit code and run
   state separated.
6. **Re-grade fallback paths** in `recover-bundle.sh` re-pointed to the
   campaign3 task-input locations.

Offline validation of the port: `bash -n` / `py_compile` clean; template
+ `sed` job yamls parse and reproduce the live twin jobs byte-for-byte;
`sheet-row.py` and the sampler reproduce the validation-rig rehearsal's accepted
901/on numbers exactly (3526 s / 85 calls / 3,512,162 tokens / $0.0747
frozen); coin verified deterministic (seed 20261031 → slot A on,
matching the live twin event log).

### Bugs found live in the twin rehearsal, fixed in this port

- **`recover()` stdout capture**: the function echoed a human line AND
  the parsed path, so the captured `REC_x` was two lines and every
  `row_for` bundle test failed ("bundle not found" despite valid
  recoveries) — sheet never written, comparison crashed. Bundles were
  safe; rows were written manually. Fix: human line to stderr, stdout is
  exactly the path; the comparison also guards a missing sheet instead
  of crashing.
- **Sampler read `session.jsonl`, which profe materializes only at run
  end** — live cap polling read zeros all attempt long (§4.4 caps armed
  but blind; the driver monitor showed calls=0 while the stream was
  minutes ahead). Fix: the sampler reads `stream.jsonl` — calls =
  `tool_execution_start` count; usage = last `message_update` usage per
  message bracket. Both rules reproduce the 901/on `session.jsonl`
  ground truth exactly (85 calls; 398,085 in / 34,525 out / 3,079,552
  cache; $0.0747), and live values matched the final sheet row. Output
  contract unchanged, so driver `sample()` needed no change.
- **`sheet-row.py` hand-joined CSV** silently shifted every later field
  when `skills_read` listed two skills (c2-inherited; found when the dev
  twin comparison mis-parsed its own sheet). Cell writing moved to
  `csv.writer`; row echo now `key=value` pairs.

### Reconciliation with the dev branch's post-port state

- **Leak-gate retired → record-only forensics (§3.5/§4.6)**: the
  runner's post-run block runs `incidents.py` (model
  switches/destroys/adds/bootstraps, gh commands, `git_sources` —
  verbatim clone/fetch/pull/ls-remote segments, `~/Projects` references
  for the §4.6.3 tripwire, home writes, bug-234 mentions) and
  hygiene-destroys leftover models (`leftover-models.txt`, never
  gating); `launch-payload.sh` stages it. `run-pair.sh` comparison
  prints `incidents=` alongside `leak(c2 gate)=` for the legacy column.
  Post-validation follow-up: despite the staging line, `incidents.py`
  was missing from this kit (the rsync would have failed on next
  launch) — restored from the dev kit and re-validated: re-derivation on
  the pair-1 on-arm bundle byte-identical; clean exit-2 refusal of
  the incomplete 902 smoke bundle.
- **Bundle quirks** (for `--regen`/analysis): the 901/on bundle predates
  `capture.json` (five-probe era) and cannot be re-rowed without one;
  the 902 smoke bundle is incomplete (`leak-status` but no
  `incidents.json` → `legacy-leak:` fallback) and rows as
  `runner-status: incomplete`.
- `README.md` here is the dev kit's README, verbatim (operator
  instruction); this file is the c3 reconciliation. (Superseded late
  the same day — that copy was removed and this file renamed to
  `README.md`; see Documentation fold below.)

### Operator decision batch (protocol text in DESIGN §6; analysis in §5)

- **No staged-layout guidance in any skill** (overriding DESIGN §6's
  original proposal, retained there as provenance). Verified: neither
  skills tree ever carried such text. The residual contamination surface
  (live issue thread, revisions newer than the deployed one) is
  recorded, not blocked: `incidents.py` gained the `git_sources`
  counter. Rehearsal evidence (twin pair 1): the ON agent ran exactly
  one clone — `--depth 1 --branch mysql-router-k8s/rev916`, the deployed
  revision, pinned; no `gh` usage. The skill's revision-pinning
  demonstrably produced the anti-contamination behavior.
- **No "stop at verdict-sufficient evidence" norm** (both arms keep the
  unchanged prompt): with the rubric extended to mechanism depth, an
  early-stop norm could suppress exactly the work the depth rubric
  grades.
- **Mutation recording**: operator indifferent; KEPT because §5.1.4(a)
  (behavior-safety endpoint) consumes the mut counters — retirement is
  a §5 amendment, deferred to the §5 sign-off. The runner's
  hygiene-destroy of leftover models is unaffected (machine
  cleanliness, not measurement).
- **Rubric margin / endpoint mapping**: deferred to the §5
  pre-registration sign-off.
- **Rubric v2 landed**: anchored mechanism-depth scale (0–4; top anchor
  = the c2 pair-9/off `Poll: true` stratum, reached by BOTH arms of
  rehearsal pair 1), evidence_strength (0–3, counterfactual top), blind
  grading protocol (skills-tree scrub; known-leak note),
  `grade-depth.json` schema. The runner's mechanical grade is
  unchanged; the depth pass is host-side and blind. (Syntax fix in the
  same pass: two plain scalars contained `": "`, which YAML rejects —
  the file had never been machine-parsed; now parses clean, text
  unchanged except one em dash → hyphen.)

### Consolidation pass (evening)

Second pass over the kit before the pre-flight submit; campaign-side
tooling only — runner, task, skills, rubric text, protocol untouched.

- **`leak-check.py` removed from the kit** (zero code references
  remain): the gate never fired and §3.5 had retired it to record-only
  forensics; a dead staged script invites drift between kit and DUT
  payload. Stage it by hand from the dev or c2 kit if a
  c2-comparability run ever needs it. Reversible at zero notice.
- **`../lib/bundle.py` — the one bundle reader** (retro A2/A6/A7): a
  single reader + validator + termination classifier instead of
  per-consumer walks of session.jsonl/incidents.json (the multiplicity
  surface behind retro incident A2). `ANALYSIS-c3.py` refactored onto
  it — output byte-identical on the twin pair.
- **`sheet-row.py --regen`** (retro B8): rebuilds rows from a state
  dir's bundles — latest recovery per (pair, arm, attempt), non-integer
  pairs skipped, cap/wall/infra from bundle evidence, driver-state
  columns (slot/machine/job_id) as `-` (not bundle facts). Verified
  byte-identical to the accepted twin rows on every bundle-derived
  column.
- **`status.py`** (retro A7): read-only per-attempt viewer, bundle-
  derived, writes nothing (anti-rogue-monitor). Profe-core `profe
  status` stays deferred.

### Statistics moved into profe (night) — supersedes a same-evening stdlib-only note

- `lib/stats.py` → **`profe/src/profe/stats.py`** (canonical home, retro
  A4/B10); moved, not copied. `pyproject.toml` gains `numpy>=2.3` and
  `scipy>=1.16` as MAIN dependencies (resolved numpy 2.5.4 / scipy
  1.18.1 under py3.14); the pre-existing `analysis` group is redundant
  and left. Mapping: betainc → scipy.special; t p/quantile →
  scipy.stats.t; Jeffreys/CP bounds → beta.ppf (replacing bisection);
  paired t → ttest_1samp; McNemar → binom.cdf, same doubled-one-sided
  convention. Two pieces stay bespoke, each for a stated reason: the
  sign-flip permutation keeps meet-in-the-middle enumeration (scipy's
  exact test enumerates all 2^n vectors — infeasible at n ≤ 40), and
  the MN CI keeps its score root-finder (scipy ships no MN interval;
  statsmodels was NOT added).
- **Convention fix found by the migration**: the retired hand-rolled
  Wilcoxon dropped zero differences BEFORE ranking (Wilcoxon-original)
  while its docstring and the c3 pre-registered text both say PRATT.
  The shipped version now implements Pratt via scipy
  `zero_method="pratt"` (ranks all differences, then drops the zero
  block; first-principles verified d=[0,1,2] → S=5, Var=Σr²=13).
  Zero-free data (all c3 pair data so far) is unaffected; the
  pre-registration wording is unchanged because the code now matches it.
- **Kit rewiring**: `ANALYSIS-c3.py` imports `profe.stats` and runs
  under the profe env (command in Freeze status above).
  `launch-payload.sh` mirrors `profe/src` wholesale, so the module ships
  automatically; the DUT's `uv sync` grows (numpy+scipy into its venv);
  no runner behavior change — no profe CLI path imports stats.
- **Package shape**: `profe/src/profe/__init__.py` is a docstring only
  (`import profe` pulls nothing); entry point `profe = "profe.cli:main"`
  (was `profe:main`) — exit-code semantics unchanged (`profe --help` →
  0). Pre-existing observation, unchanged: `profe validate --skills
  /nonexistent` exits 1 though cli.py's docstring advertises 2 —
  flagged for whoever revisits the validate verb's exit-code contract.

### Oracle removal; external ground truth found two real MN bugs (night, iii)

Operator challenge on the parity tests: "how do I know those are
correct?" — correct: the parity was circular (the oracles were
validated by the same checks as the module, and one — the Wilcoxon —
carried a wrong convention). `profe/tests/_oracles.py` deleted; the
suite is anchored to EXTERNAL ground truth only (published tables,
analytic closed forms/identities, definitional brute force, defining
equations re-solved by an independent root-finder — scipy brentq on
the MN score — and invariance properties; numpy.testing assertions,
NaN-safe). References added to every statistical docstring (Student
1908; Wilcoxon 1945; Pratt 1959; McNemar 1947; Clopper & Pearson 1934;
Jeffreys 1946; Brown/Cai/DasGupta 2001; Miettinen & Nurminen 1985;
Pitman 1937; Ernst 2004; Lehmann 1975; Agresti 2002; Casella & Berger
2002; DLMF §8.17).

The external tests immediately caught **two real Miettinen–Nurminen
bugs**, both inherited from the c2-era validated implementation and
invisible to oracle parity because the oracle shared them:

1. **Constrained-MLE bracket violated the δ<0 constraint**: with
   p̃1 = p̃2 + δ, the constraint p̃1 > 0 means p̃2 > −δ, but the search
   used a plain (0, 1−δ) bracket, admitting p̃1 ≤ 0 (and, for δ < 0,
   p̃2 > 1) where the score degenerates and bisection converged to
   false roots.
2. **Boundary-MLE chosen by score magnitude, not derivative sign**: for
   zero-event arms the constrained MLE sits at a bracket endpoint; the
   old code picked by |score| comparison, but the constrained
   log-likelihood is concave in p̃2, so its derivative is monotonically
   decreasing and the endpoint is identified by the derivative's SIGN.

Both fixed in `profe/src/profe/stats.py`; `tests/test_stats.py` asserts
the defining equation |Z_MN(δ*)| = z_0.975 on 60 seeded random grids
including zero-cell arms, via brentq with a likelihood-argmax boundary
fallback selected independently of the module's derivative-sign rule
(21 checks green).

**Twin-output impact** (analysis re-run after the fix): exactly one
substantive field —
`family4_safety.rate_diff_env_mutation_MN_CI[0]`: −9.99e-10 → −0.5869
(the old lower endpoint was a false root of the malformed bracket);
upper endpoint and every Jeffreys bound unchanged (≤1e-15 relative
drift). No c3 decision consumed the affected number: the twin pair was
rig validation, and the §5.1.4 safety rule reads the ON-arm Jeffreys
bound alone (0.9985, unchanged). The §5.3 futility grids run on the
corrected implementation from pair 1 onward.

### Documentation fold (late)

The dev kit's runbook — copied here verbatim as `rig/README.md` at
port time — was removed by operator decision, and this file
(previously `README-campaign3-rig.md`) renamed to `README.md` as the
kit's only runbook. The copy described the dev kit (old names,
`payload-overrides/`, leak-check staged, 5400 s cap, validation rig/901
decisions, dev-tree paths), so a reader following it in this tree
would be misled. Salvaged before deletion: the probe-5 wall-time
caveat (now DESIGN §3.6) and the watch/re-grade step detail in the
single-machine path above. The canonical rehearsal record remains on
the development branch (`eval/runs-t6/mr-234/c3-rehearsal/`, DESIGN
§11).

### Stats' siblings move into profe (pre-pair-1)

Operator decision (supersedes the retro's "don't couple profe-core
changes to c3" for these two library functions — the profe VERBS stay
deferred): the session-usage walk and the bundle reader/validator move
into profe before pair 1, so schema knowledge sits next to the code
that writes the formats. New module `profe/src/profe/bundle.py`
(stdlib-only): `session_usage` (the session.jsonl usage walk),
`read_bundle` → `BundleRecord` (capture/session/graded/runner-status
facts), `validate_bundle` (same checks and reason strings as before).
`profe.models.CaptureSidecar`/`GradeRecord` remain the strict typed
twins (pydantic); `profe.bundle` is the tolerant dependency-free twin
so bare-host consumers keep running.

Campaign side keeps, per retro A8 (no campaign judgment in profe):
**pricing** (`lib/cost.py` — frozen-prices.json + dollars; profe takes
prices as an argument, never owns a vector), **incidents.json +
grade-depth.json reading**, and **`classify_termination`** (the §4.6
two-class law). `lib/bundle.py` is now the campaign merge layer over
`profe.bundle` — same record dict, same reason strings, zero consumer
changes; `sample-session.py`'s DUT-local walk stays (documented A4
exception).

Validation battery, all green: profe tests 28/28 (7 new bundle tests,
hand-computed fixtures); reader output identical on the 5 real bundles
(incl. the pair-R no-sidecar and 902 incomplete tolerance paths); regen
rows and the status table identical (the regen `ts` column is a
write-time stamp, not a bundle fact); ANALYSIS twin output byte-
identical; selftest 12/12; bare-host python3.12 runs sheet-row/status
with no pydantic/scipy in the import chain; `profe --help` → 0; `import
profe` loads no cli.

Pre-existing quirks surfaced by the migration, recorded not fixed: the
usage walk skips only NON-JSON lines (a line parsing to a JSON scalar
would crash it — true of the old walk too, never hit in real bundles);
`sheet-row.py --regen` on the incomplete 902 smoke bundle crashes in
`row_for` (capture.json assumed present — incidents.py's clean exit-2
refusal is the only guaranteed path; the earlier "sheet-row refuses it
cleanly" note was optimistic); `--regen`'s sheet flag is `--sheet=PATH`
(usage string said `--sheet PATH` — fixed).

### Rehearsal pair 2 (2026-10-16/17 night) — three fixes it forced

First pair run from THIS kit tree (off/on as drawn, seed
20261032; coin flipped the arms vs pair 1). The on arm completed
completely clean (graded.jsonl, incidents, runner-status=done,
skills_read=juju-cli,mysql-charms-source); the off arm's RUN completed
(pi_exit=0, dur=2887 s, valid contract block: charm-bug, schema_ok) but
its DUT grade step crashed. Three real kit bugs surfaced and are fixed:

1. **Rotated host keys + hashed known_hosts**: persistent deploy
   reimages on every submit (§3.2), so each lease's host key is stale
   by the next one — and StrictHostKeyChecking=accept-new covers
   UNKNOWN hosts only. Both slots hung in rig-wait until the stale
   entries were cleared by hand. Fix: the driver now runs
   `ssh-keygen -R <ip>` the moment each device IP is assigned (run-pair
   .sh); correct by construction, no security regression (the machine
   is fresh).
2. **Rubric v2 vs profe.models.Rubric**: `profe grade` crashed on the
   shipped diagnose@2 rubric — the v2 fields (task, blind_protocol,
   dimensions, grade_depth_json_schema, mechanical_checks, notes) were
   unmodeled and the model forbids extras. "Machine-parse-verified" had
   never meant profe.models. Fix (schema owner): Rubric extended with
   typed submodels (RubricBlindProtocol/RubricDimension/
   RubricDepthSchema; int-keyed anchors — pydantic does not coerce map
   keys); hash is over raw text, unaffected. 5 new tests
   (test_rubric.py); the real diagnose.yaml
   parses; a host re-grade of the recovered off bundle produced
   schema_ok=True, verdict=charm-bug — the exact DUT failure, green
   after the fix.
3. **Runner-status not crash-safe**: run-attempt.sh wrote
   runner-status only on the stall path and at the end — a crashed
   GRADE step left NO marker, and the host driver cannot distinguish
   "still running" from "runner died" (it polled the dead slot until
   the giveup bound). Fix: EXIT/TERM/INT traps guarantee an
   `incomplete` marker on any early exit; the success path still
   writes `done`.

Rehearsal-only interventions (documented, never in the campaign flow):
the off slot's runner-status was hand-written as `incomplete` (the
truthful value a trapped runner would have written) to terminate the
host loop, and the on slot's payload was live-patched with the rubric
fix mid-run (the grade step re-imports from disk, so the fix took
effect without touching the running attempt — which is why the on arm
is complete). The recovered off bundle is kept AS the runner produced
it (no graded.jsonl, no incidents, runner-status=incomplete); the
host re-grade that validated fix 2 ran on a /tmp copy, not the bundle.
The sheet's off-row verdict/schema_ok come from the transcript's
contract block (sheet-row's mechanical fallback — the sheet is the
operational layer; ANALYSIS reads bundles, never the sheet).

ANALYSIS smoke on the pair-2 state: attempts_total=3 (off, on, the
902 smoke stray), off + 902 excluded as unreadable with the exact
strict-reader reasons (no graded.jsonl / no sidecar), complete_pairs=0
(off missing) — the refusal-and-exclude path worked on real data.
Ground-truth dumps for both Gen10 machines in state/ground-truth-*.txt.

### Rehearsal pair 3 (2026-10-17 early morning) — all fixes validated

Re-run with the three pair-2 fixes aboard (pair seed 20261033; arms
again off/on as drawn). Clean end-to-end, zero interventions:

- **key-rotation fix self-validated**: IP → rig-wait → launch with no
  manual known_hosts clearing.
- **rubric fix validated in production**: the off DUT grade now
  completes — graded.jsonl written with schema_ok=False, a MECHANICAL
  RESULT (the off agent ended without a valid contract block; agent
  behavior, ITT data — exactly what schema_ok exists to catch), not a
  crash. The on arm graded clean (charm-bug, schema_ok=True,
  skills_read=juju-cli,mysql-charms-source — same reads as pair 2).
- **runner-status trap validated**: done written on success on both
  slots; recovery complete on both bundles.
- ANALYSIS on the full state: attempts_total=5 (pair-2 off/on, pair-3
  off/on, 902 stray); pair-2 off excluded (no graded.jsonl — the
  crashed DUT grade, kept as produced), 902 refused (strict reader);
  **complete_pairs=1** — pair 3 forms the first complete analysis pair
  from this kit tree; the schema_ok=False off attempt is correctly
  ITT-kept (calls/tokens/cost endpoints don't need the verdict).

Agent-behavior note for the operator: the OFF arm emitted no valid
contract block in pair 3 while it did in pair 2 (and in pair 1) —
schema_ok variance in the off arm is now observable; if it recurs, the
contract-block instruction may need a look in §5 (analysis-side, not a
runner fix). Fleet fact from the ground-truth dumps: the "spec-matched
twins" differ in RAM (125 GiB vs 62 GiB; same Xeon Gold
5118 / 24 cores) — a §3.7 pairing question, recorded here, not acted
on. Machines released; both queues idle again.

### v1 rubric compatibility removed (operator decision, 2026-10-17)

"Remove compatibility for v1 rubrics — we don't care." The Rubric
model now carries mechanical checks ONLY under `mechanical_checks`
(the grade step, compile's emitted rubrics and the shipped diagnose@2
all use it); there is no dual-name reading and no version-history
narrative in profe's docstrings. A rubric using the old `checks:`
spelling (or any unmodeled field) fails validation loudly — pinned by
test. compile roundtrips through the new model (trigger rubric emitted
and re-parsed); 33/33 tests; ANALYSIS twin byte-identical; selftest
12/12.

### Reader strictness: five-probe-era bundles refused (pre-pair-1)

Operator decision (kills the era-compatibility tolerance the bundle
move had carried): `profe.bundle.read_bundle` now raises
`BundleFormatError` on any directory that is not a current-format
attempt bundle — the format checks of `validate_bundle` minus the
runner-status VALUE check. `validate_bundle` gains the reason
"no runner-status (attempt marker)", making the five-probe-era
signature (pre-c3 runners shipped no marker) a refusal: the 901
rehearsal bundle no longer reads. Its ground-truth anchors (85 calls,
$0.0747) remain as recorded numbers in this log and in the test
fixtures; the bundle itself is no longer a supported input. Strict
about format, tolerant about value: `runner-status=incomplete` still
reads (quarantine adjudication needs the value), and a truncated
session tail is skipped (kill robustness, not era compatibility).
`sidecar_error` left `BundleRecord` (unreachable behind the gate); the
campaign sentinel "sidecar-unparseable" left `lib/bundle.py`.
Consumers handle refusals explicitly: `status.py` displays a refused
attempt row with the validate reasons; the ANALYSIS collect path
records non-current-format bundles as excluded with the refusal
reasons (ITT bookkeeping — never a silent drop, never a crash).

Validation battery, all green: profe tests 28/28 (tolerance tests
rewritten as refusal tests + a value-tolerance pin for quarantined
bundles); complete twins (pair-1 on/off) read + validate byte-
identical to the migration snapshots; regen rows identical; ANALYSIS
twin output byte-identical; selftest 12/12; bare-host python3.12
unchanged; `profe --help` → 0. Pre-existing kit lint debt outside the
touched files (sheet-row, incidents, cost, sample-session) remains
recorded, not fixed. The sheet-row `--regen` 902 crash quirk above is
now moot for profe-side tooling (the reader refuses what `row_for`
crashed on) but `row_for` itself still reads capture.json directly —
unmodified.

### Driver v3 first live rehearsal — five real bugs caught and fixed
(2026-10-17)

The operator's polish loop: driver v3 driven pair 4 (twin slots
the rehearsal pair) against fresh state (`state-v3rehearsal/`). The state
machine held through every failure (no crash escalation, ledger
counts, reattach from events). Five porting/latent bugs surfaced:

1. **submit via snap + /tmp**: the boundary built the job yaml with
   `mktemp /tmp/...` — the testflinger snap cannot read /tmp (snap
   private tmp), and the CLI exits 0 on that client-side error, so the
   empty output looked like a success with no UUID. Fix: the yaml goes
   to `$STATE_DIR/job-$q.yaml` (as run-pair.sh did) and the op hard-
   fails unless a UUID is extracted. c2's run-pair.sh had been safe by
   accident of path choice; v3 now pins the requirement.
2. **submit failure crashed the driver**: `RuntimeError` from the
   boundary submit escaped `run_attempt` uncaught. Per §4.3 it is an
   infra failure: now recorded (ledger), the slot retries through the
   normal loop, the cap still gates at 3.
3. **`s["ip"]` not persisted after `wait_ip`**: the fresh-attempt path
   re-read `ip = s["ip"]` without writing the granted IP back, so the
   rig-ready poll got `None` (TypeError in subprocess args). Fix:
   persist `s["ip"] = ip` immediately after the grant (the rig-ready
   state event alone lands too late). The reattach path is unaffected
   (it rebuilds from events).
4. **`SSH_AUTH_SOCK` dropped by the restricted env**: the rig
   authorizes the host key via ssh-agent (`lp:astrojuanlu` in
   reserve_data); the driver's tight env whitelist omitted the socket,
   so EVERY ssh op failed instantly and silently — wait_rig would have
   burned its full 45-min window and infra-failed a healthy attempt.
   Fix: `_run` forwards `SSH_AUTH_SOCK` from the driver's own
   environment. (Test lesson: the env -i reproduction failed even with
   the socket — the real cause was a released machine by then; the
   original break was diagnosed from the silent-failure pattern.)
5. **alive=0 premature done (c2 `mark_done` gate lost in port)**:
   monitor treated `alive=0` as done; the first sample after launch is
   alive=0 while pi boots, so the driver recovered a half-built bundle,
   invalid-bundle → infra-failed → recover RELEASED the lease under a
   running attempt (attempt 1, ~26 min of machine time wasted).
   c2's gate restored: done only when the runner's terminal marker
   (`runner-status`) is non-empty; empty marker logs "runner-status
   pending" and keeps polling. Pinned by two regression tests (fake
   boundary; monitor now 20 tests in the suite, 53 with the profe
   battery).

Bookkeeping from the same run: slot A carries n_infra=2 of the §4.3
cap (invalid-bundle + rig-gone) — the next infra failure pauses for
the operator BY DESIGN. The premature-done bundle note "sidecar
carries no manifest label (pre-v3 attempt)" is the §4.6 tolerance
note, expected on a half-built bundle; attempt 2 must show the label
(settle will scan it). run-pair.sh's sequential-slot lesson stands:
the driver runs slots one after the other (≈2× wall clock vs the c2
parallel twins) — accepted for v3, revisit only if campaign wall time
becomes a binding constraint.

### Driver v3 rehearsal continued — slot B accepted end-to-end; three
more bugs caught (2026-10-17)

Pair 4/B (off) ran the FULL v3 pipeline unattended: submit →
rig (23 min) → launch → monitor (2.3 h, healthy throughout, the
runner-status gate firing correctly in production) → runner-done →
recover → settle → sheet row. Result: 190 calls, 10.65M tok, $0.1808,
schema_ok=True, verdict charm-bug, incidents clean — accepted. The
off-arm row shows skills_read "-" as designed (no skill reads on the
control arm). First complete v3-driven attempt.

Three more bugs surfaced while A retried:

6. **§4.3 cap dead in-process** (B9): `emit_state` never incremented
   `n_infra` — only `replay()` did, so the cap fired solely on
   restart. With A stale-reattaching, the loop hot-spun "rig gone on
   reattach" ~1/sec for minutes: 506 duplicate events appended, 255
   empty `recovered-*` dirs created. Fix: `emit_state` counts
   infra-failed exactly like replay. Pinned by a test (3 fails →
   SystemExit "OPERATOR PAUSE").
7. **Stale-job reattach** (B1): `reattach = bool(s["job"])` trusted a
   job id from an infra-failed attempt forever — the runaway's fuel.
   Fix: reattach only for submitted/rig-ready/running states; an
   infra-failed slot cancels its stale lease and submits fresh (the
   §4.3 retry). Pinned by a test (stale job → cancel + new submit,
   attempt incremented).
8. **recover-bundle.sh ignored STATE_DIR**: `DEST` hardcoded
   `$DIR/state/…`, so B's accepted bundle landed in the default state
   dir while the driver's resume_scan looked in `state-v3rehearsal/`
   — a restart would have RESUBMITTED B over a valid bundle (the c2
   bug-1 class via path split). Fix: `DEST="${STATE_DIR:-$DIR/state}/…"`.

State surgery (documented, kept minimal): the 506 runaway events
(`rig gone on reattach` after B's 10:55 acceptance) were removed from
`state-v3rehearsal/events.jsonl` — machine artifacts of bug 6/7, no
machine resources consumed; the two REAL A infra events (08:14
invalid-bundle, 08:18 rig-gone) are kept, so A's ledger stays n_infra=2.
B's bundle was moved into `state-v3rehearsal/recovered-20261017T105522Z`
where the driver's resume finds it; the 255 empty junk dirs were
purged from `rig/state/` (the four real pair-2/3 recoveries and the
0814Z premature-done partial remain as evidence). Driver tests now 22
(55 with the profe battery), ruff clean.

Fleet note: a released reserve job leaves the DUT agent in `reserve`
until the reserve timeout (~5 h from pickup) — the stuck machine showed
`agent busy` for ~1 h after the lease was already gone. The §3.7 gate
refuses to submit into a busy queue (correct); the driver defers every
5 min and self-resumes when the agent frees. Campaign-side this means
a released machine is NOT immediately reusable: twin slots must
account for the reserve tail when scheduling back-to-back attempts.

### Fleet contention is real and can be 24 h (2026-10-17, §3.7 note)

While the driver deferred for A's retry, the machine's busy state turned
out to be ANOTHER TEAM's reserve job (`fbce5a19`, submitted by
`sqa_testing`/oil-ci-bot, picked up 09:49 while our attempt-1 lease
was still nominally open) with `reserve_data.timeout: 86400` — a
24-hour reservation. The §3.7 gate did exactly its job (defer, never
submit into a busy queue, never drift the rig). Operational
consequences for the campaign:

- The twin queues are SHARED with other teams; a slot can be blocked
  for many hours by foreign jobs. The defer loop is unbounded by
  design (the operator watches; the ledger cap only counts OUR infra
  failures, not foreign contention).
- The coin binds arms to queues — a blocked machine CANNOT be swapped
  for its twin (that would drift the frozen design). The pair waits.
- Campaign wall-clock planning must assume possible 24 h stalls per
  slot; pairs 2+3 this morning saw no contention, pair 4 saw a
  24 h-class hold. Scheduling should prefer submitting early in each
  machine's quiet windows and tolerate inter-pair gaps.
- the machine's agent also restarted mid-incident (reimage on release),
  which reset its reserve timer — the "1 h busy tail" note above was
  partly this agent restart, partly the foreign pickup. Both tails are
  handled by the same defer loop.

### Pool mode + smoke-task rehearsals (operator decisions, 2026-10-18)

Two operator decisions before the campaign launch, both aimed at the
same diagnosis: with mr-234 rehearsals running ~6 h wall per pair and
pair 4/A stuck behind a 24 h foreign reserve on a named machine, machinery
bugs would surface too slowly to ever launch.

1. **Pool mode (DESIGN §3.7 amended, manifest v2).** No machine pinning:
   both twin slots submit to the shared fleet queue
   `<fleet-queue>`; the scheduler assigns whatever is free
   (10+ waiting Gen10s at decision time). Consequences accepted and
   recorded: arms of a pair may land on different machines (even Gen10
   Plus siblings — the queue advertises them); machine identity is a
   RECORDED covariate, not a controlled one — the driver fetches the DUT
   hostname at rig-ready (new boundary op `hostname`), carries it in the
   state events and the sheet's machine column, and the rig's
   ground-truth dump gains a `hostname` first line (dump files are now
   labeled pair-slot, not queue — both slots share one queue name).
   Contention semantics change: a busy POOL (no waiting agent at all) is
   a 5-min defer, not an infra event; the "coin binds arms to queues —
   never swap" lesson above is SUPERSEDED (there is nothing to swap).
   Implementation: `manifest-c3.yaml` v2 (twin_slots both = pool queue,
   version bump, re-signed); template renamed `pi-rig-template.yaml`;
   driver: `dut_hostname()`, `machine` slot field (replay/emit), sheet
   row machine cell = DUT hostname (fallback queue), dump-gt label
   pair-slot.

2. **Smoke task `smoke-rig-tour` (manifest v2 frozen_inputs.task).**
   Rehearsals stop consuming mr-234 runs (~2.3 h of attempt per arm).
   New `tasks/smoke.jsonl` + `rubrics/smoke.yaml` (mechanical_checks
   empty; schema_ok via the contract block is the mechanics signal):
   inspect the DUT rig with the juju/k8s CLIs, emit the contract yaml
   block — arm-symmetric, representative tool calls, <10 min attempt.
   Task selection is manifest-driven: `frozen_inputs.task` (file + id,
   now HASH-VERIFIED by load_manifest) → driver `launch` op →
   `launch-payload.sh` args → `TASK_FILE`/`TASK_ID` env → run-attempt.sh
   (run + grade steps; defaults = mr-234 for standalone use).
   launch-payload stages the WHOLE tasks/ and rubrics/ dirs (the attempt
   picks by id). The campaign manifest still runs mr-234/diagnose — the
   freeze is per-manifest.

Validation at the change: driver suite 22/22, profe suite 37/37 (4 new
`test_smoke_task.py` pins on the real kit files), ruff clean on touched
files under both configs, `bash -n` on the three shell scripts. First
pool-mode smoke rehearsal launched in a fresh state dir
(`state-v3pool/`) — manifest v1 and v2 events never share a ledger.

Rehearsal-economics note (for §12): smoke pair wall ≈ deploy 13 min +
rig 7.5 min + smoke attempt <10 min + recovery ≈ 2 min, per arm,
sequential ⇒ ≈ 40-65 min per pair vs ≈ 6 h for mr-234 pairs. The
machinery under test (submit → rig → launch → monitor → recover →
settle → sheet) is unchanged.

### First pool-mode smoke pair — one bug, one fleet lesson (2026-10-17,
### same day)

Pair 5/A ran end-to-end in ~20 min wall (submit 14:35Z → row 15:04Z) on
**a CSV-unverified fleet node** (the pool handed it out and the
ground-truth dump + hostname event now record it): smoke attempt 5
calls / 22.1k tok / $0.00094, contract block perfect, machine column
live. Two findings:

- **Bug 9 (sheet-row): the contract parse hardcoded the mr-234 fields.**
  The run graded schema_ok True; the row said False (and `contract=1`
  incident) because `row_for` re-parsed the yaml block and required
  verdict/root_cause/… — fields the smoke contract intentionally lacks.
  Fix: graded.jsonl is the authoritative parse (sheet-row reads
  schema_ok/verdict from it; the mr-234 re-parse demoted to
  `_legacy_contract`, used only for pre-graded bundles). Pinned by
  `test-sheet-row.py` (3 tests: smoke-via-graded, legacy mr-234,
  not-ok). `--regen` also enriched: it now rebuilds slot/machine/job
  identity from the ledger (`_ledger_identity`: coin → slot↔arm,
  rig-ready → machine, submit → job) — pool-mode rows regenerate with
  their DUT identity intact. Driver restart not needed: sheet-row
  spawns per row. A's stale row repaired via `--regen --sheet=…`.
- **Fleet lesson: a rig build can fail on a pool machine — and the
  pool absorbs it.** Slot B's attempt 1 landed on **hind** (another
  CSV-unverified machine): k8s bootstrap fine, rig-env.sh written, but
  the k8s-convergence wait ran ~21 min (slow sibling) and
  `juju bootstrap k8s` then exited without registering a controller →
  no `~/rig-ready`. The driver held the window (45 min), canceled the
  lease, recorded infra-failed (n_infra 1/3) and resubmitted — attempt
  2 drew the pool again within 2 s. No stall, no operator action: this
  is precisely the contention semantics §3.7-v2 bought. (hind's
  failure mode is recorded here; health triage is the provider's.)

Rehearsal economics, measured: pair 5 wall ≈ 2×30 min sequential arms
attempt 1 (A: 29 min submit→row; B: rig build only) — consistent with
the §12 estimate of ~40-65 min/pair, vs ≈ 6 h for mr-234 pairs.

### Bug 10: pool reuse without reimage kills the runner (2026-10-17 night)

Pair 6 exposed the pool mode's sharpest edge, in two parts:

- **Fleet dud cluster (§4.3 pause).** Pair 6/A burned its 3 infra
  attempts on "rig not ready in window": three consecutive pool draws
  (.31, .22, .31 again) sat the full 45-min window without finishing the
  rig — wedged provisioning or a hung snap/bootstrap on those machines;
  both are unreachable over ssh after release now (provider-side
  diagnosis). The driver paused per §4.3 and exited cleanly — that path
  worked exactly as designed. Noted for the campaign: under a shared
  pool, per-slot infra counters punish bad luck, not our rig (see the
  open §4.3 amendment question for the operator).
- **Bug 10 (kit): a reused machine keeps the previous attempt's stack.**
  Pair 6/B attempt 1 drew the machine pair 5/A had run on and
  released ~2.5 h earlier. It was NOT reimaged: the k8s cluster and
  `testing` namespace from pair 5/A were still alive; the rig body
  de-facto passed (fast, near-idempotent) but the runner's
  `juju add-model testing` collided with the surviving namespace and
  died at its first guard (runner-status=incomplete, empty sidecar →
  "pre-v3 attempt" note → invalid → infra-failed). Fix, two layers:
  the job template gains a **[rig 0/7] hard reset** (purge k8s/juju
  snaps + their data dirs — every job now converges to a fresh stack
  whether or not the reimage ran), and the runner gains a
  `kubectl delete namespace testing` sweep before add-model
  (belt-and-braces). Live re-verification: next rehearsal pair.

### PROFE-DRIVE rehearsal continued — the driver drew a relative-path bug,
the machinery recovered it, and the pair completed (2026-10-18, same
night)

The cutover's first live pair (7, smoke task, pool queue) found exactly
what rehearsals are for:

- **Relative `--state` doubled the recovered-stamp path.** The campaign
  ran with `--state rig/state-v3pool` (cwd = campaign3); the boundary's
  recover verb `cd`s into the rig dir before running
  recover-bundle.sh, whose `STATE_DIR` is resolved against ITS cwd —
  the stamp landed at `rig/rig/state-v3pool/recovered-…` while the
  campaign adjudicated `rig/state-v3pool/…` (empty) → the attempt
  settled `infra-failed` with a COMPLETE bundle unrecovered. Fix
  (testflinger_sandbox.py): `state_dir` is resolved to ABSOLUTE at
  construction — the boundary's cwd games then cannot break it. The
  lost bundle was moved into the real state dir (state surgery,
  provenance note in the log), and the restart's resume scan
  adjudicated it: **slot A resumed accepted from bundle evidence
  without resubmitting** (B1 live).
- **Slot B reattached mid-flow** (state=submitted, lease live), re-
  resolved its ip, and — after two full rig-wait windows expired on
  dud machines (two fleet nodes; the §4.3 retries + ledger worked as
  designed) — attempt 3 reached rig-ready in ~4 min, ran the smoke
  attempt, recovered, and settled `accepted` through the FIXED path
  (sheet row 7/B, calls 7, $0.0009).
- **Bug 10's hard reset live-verified**: pair 7/A drew the
  machine pair 6/B's attempt 1 reused when the bug fired — and the
  `[rig 0/7] hard reset` + runner sweep converged it: rig-ready in ~7
  min, attempt completed, bundle clean.
- **Pair 7: accepted / accepted.** The consolidation (§7 step 4) is
  complete: profe.driver + campaign.py are the driver; driver-v3.py is
  retired. One v3-behavior note: a resumed slot writes no sheet row
  (v3 parity — the sheet is regenerable from bundles via
  `sheet-row.py --regen`).

PROFE-DRIVE consolidation: driver-v3 retired into profe.driver (2026-10-18 night)

Battery-standard correction, found at the launch gate: the earlier "ty
clean" claims were scoped (`ty check src packages/pairedstats/src`) and
missed tests + scripts. The bare whole-project `ty check` (run from
profe/) found 11 diagnostics — all type-honesty fixes in test
fixtures/scripts, none in the library: the fakes' method params were
named `spc` where the Protocol says `spec` (structural conformance
requires keyword-name compat), a fake shadowed its class-level
`prior_bundles` method with a None instance attr (now a plain instance
attr: bound method or None — same runtime semantics), one test read
`.alive` off the SampleOrMiss union without narrowing, and
migrate_queries.py needed an explicit `pairs` annotation plus a guard
for the None-skill case (unreachable at runtime by the fail-fast
ladder). Standard going forward: bare `ty check` + `ruff check .` +
`ruff format --check .` from profe/, and the same ruff pair from rig/ —
56 + 21 + 9 green after.

Same night, the ty config went strict (profe/pyproject.toml [tool.ty]):
analysis.return-type-inference = "always" +
analysis.respect-type-ignore-comments = false (ty: ignore only), and
the rules unsound-assignment / unsound-return-statement / unsound-yield /
possibly-unresolved-reference / possibly-missing-attribute /
possibly-missing-import / missing-type-argument / unused-ignore-comment /
unused-type-ignore-comment / blanket-ignore-comment all at error.
First strict run surfaced 34 diagnostics — all fixed at the source, no
ignores added: session_usage got a TypedDict contract (the dict's
homogeneous-value inference was the real bug the union errors pointed
at), report.py's helpers typed as GradeRecord (killing the rec: t.Any
and the mypy-dialect `# type: ignore[arg-type]` with it),
pi_agent_json._parse made properly generic (PEP 695), Popen/Field/
TypeAdapter type arguments filled in, four unsound Any-flows pinned with
isinstance/str guards that match the runtime contracts, and the test
fixtures narrowed (asserts) instead of silenced. The one pre-existing
`# type: ignore` in report.py is gone; the codebase now uses zero
ignore comments. Battery after: ty strict clean, 77 + 9 green.

The proposal (`../../research/PROFE-DRIVE-PROPOSAL.md`) is implemented.
The attempt state machine (states, ledger, monitor, settle, resume
adjudication) moved into `profe/driver/` as the L1 library; this kit
keeps the campaign judgment (A8). `driver-v3.py` and its 22-test suite
are retired; their ports live in `profe/tests/test_driver.py` (40
tests) and the campaign-side remainder in `test-campaign.py` (5).

- **Layering:** `profe.driver` is sandbox-agnostic — the campaign
  imports it and wires `testflinger_sandbox.py` (the thin adapter over
  the VERBATIM boundary verbs, B3 unchanged; no profe import, no ABC —
  `typing.Protocol` structural conformance; capability data:
  `reprovisions_on_resubmit=True`, `recover_releases_lease=True`).
- **Behavior pins kept:** the fresh+reattach flow is the ported verbatim
  loop (one flow for both); harvest never releases (the split that
  makes the premature-done bug unrepresentable); done is ONLY the
  runtime's own completion marker; infra never consumes a slot.
- **§4.3 changed (operator decision, 2026-10-18):** an exhausted slot
  no longer pauses the campaign — the loop emits a `ledger-skip` note
  and skips the slot; the pair proceeds with the other slot and ends
  when both are terminal or exhausted. `test-campaign.py::
  test_s43_exhaustion_skips_the_slot_and_ends_the_pair` pins it.
- **Validated before retiring driver-v3:** profe battery 56 passed,
  pairedstats 21 passed, `ty` + `ruff` clean; a fake-boundary
  integration run (campaign → drive_attempt → adapter → boundary →
  harvest → settle → real `sheet-row.py` row → compare) completes pair 1
  both arms accepted; and the REAL `state-v3pool` rehearsal log replays
  field-for-field identical through profe's tracker (rig's own replay
  vs profe's: state/job/ip/t0/attempt/n_infra/coin/machine/bundle all
  equal on pairs 5/6).
- **Wire format unchanged** (events.jsonl lines byte-compatible with
  the rehearsal log); new lines carry an additive `schema: 1` field
  (readers tolerate its absence — proposal §8.4).
- **One diagnostics-only divergence:** an invalid bundle's
  infra-failed record names the attempt's ARM DIR inside the recover
  stamp (driver-v3 named the stamp root) — the adapter resolves the rig
  layout so adjudication can read the bundle; the terminal path and
  the sheet are unchanged.

### Operator decision batch executed; campaign launched (2026-10-18)

The operator's A–E decisions, executed on the post-cutover kit and
verified before launch:

- **A1 — mechanism depth is DESCRIPTIVE-ONLY** (already in DESIGN §12's
  resolution): the blind depth grade is reported per arm/pair but
  carries no confirmatory weight and no margin. ANALYSIS-c3 reports
  depth descriptively.
- **A2 — mutations are NOT recorded**: incidents.py's
  destroy-model/add-model/bootstrap capture (and self_created_models)
  is retired; §5.1.4's endpoint (a) is dropped — behavior safety is
  CONTRACT ANOMALIES only (DESIGN §5.1.4 amendment); ANALYSIS-c3's
  family 4 re-aimed accordingly (MN/Boschloo CI now on contract
  anomalies). The sheet's `mut=` cell stays as a legacy zero.
- **A3 — the safety rule reads CLOPPER–PEARSON** (conservative;
  annotated 2026-10-17; ANALYSIS reports Jeffreys alongside).
- **A4 — §5 machinery confirmed unchanged** under pool mode: fixed
  sequence (calls → tokens → McNemar cost → safety), futility ±13% at
  blocks 10/20/30, hard cap 50; pairing is by pair index.
- **B — campaign manifest re-signed as v3**: task mr-234
  (sha 15fbff5a…), rubric diagnose@2 (sha a5a77653…), pool queue in
  both twin slots, total_pairs 30, caps $0.30/15M/250, seed formula
  unchanged. v2 archived as `manifest-c3-v2-rehearsal.yaml`.
- **C — pool ops**: rig_wait_s stays 2700 (45 min); §4.3 exhaustion =
  skip (landed with the cutover, pinned in test-campaign.py). NEW
  corollary executed: the §3.1 block-boundary check is
  OBSERVABILITY-ONLY — a busy/degraded pool is recorded as
  `boundary-pool-busy` and the loop continues; the per-attempt
  lease_ready gate owns capacity (a fleet stall is not our rig's
  failure, and the campaign must not pause for one). Pinned by
  `test_block_boundary_is_observability_only`.
- **D — rehearsals end**: bug 10's hard reset was live-verified on
  pair 7 (a reused-dirty node → converged in ~7 min); pair 7 completed
  accepted/accepted. No further rehearsal pairs.
- **E — machine covariate completed**: the sheet's machine cell and
  SlotRecord.machine landed with the cutover; this session adds the
  bundle sidecar label (`run-attempt.sh --extra-metadata ... machine=
  $(hostname)`) so every bundle self-describes its DUT.
- **Version-skew fix found at the launch gate**: the cutover ran under
  the profe env (Python 3.14, PEP 649 deferred annotations) but the
  runbook's bare `python3` is 3.12, where the in-class forward
  references (`AttemptState.from_wire -> AttemptState`,
  `AttemptTracker.replay -> AttemptTracker`) evaluate eagerly and
  NameError at import. Fix: `from __future__ import annotations` in
  all five `profe/src/profe/driver/` modules (no semantic change on
  3.14); battery re-run green on both interpreters (56 + 21 + 10,
  ty strict, ruff, format).

Launch record: `python3 rig/campaign.py --state rig/state-campaign`
(all 30 pairs), dry-run pre-flight in `rig/state-preflight` (removed
after) — manifest v3 hash `b853d2ce`, frozen inputs verified against
the tree, 30-pair coin plan clean. Tag `c3-start` on the campaign
worktree (kit files are untracked — the tag timestamps the tree, the
ledgers carry the provenance). Fresh ledger: `rig/state-campaign/`.

### Handover reconciliation pass (2026-10-18, pre-handover)

The incoming agent's doc review flagged stale text; all fixes are
mechanical (no §5/§3 decision content touched — that is pre-registration):

- **campaign3/README.md** (the new one-page brief, entry point): block
  boundaries restated as "after every 10 completed pairs (10/20/30)" —
  the boundary *event* fires as pair 11/21 starts; 31 exists only under
  a cap-50 extension. status.py command now carries `--state`.
  Cardinal rule 5 reworded: a tag cannot content-address untracked
  files — provenance = tag message + ledgers.
- **DESIGN.md**: header status proposed → committed (c3-start, v3
  `b853d2ce`); §4.3 "concurrently" → sequential arms (matches §3.1 and
  the cutover, with the amendment note); §9 checklist boxes marked done
  with dates (driver-v3 box notes the PROFE-DRIVE supersession); §3.7
  "manifest v2" → "rehearsal manifests v2, campaign manifest v3".
- **status.py**: banner + docstring no longer claim the driver writes
  state/manifest.json — the manifest hash rides every event line.
- **campaign.py** docstring + **rig/README** campaign-operation block:
  examples now point at `state-campaign` (rehearsal ledgers listed as
  inert/read-only).

State-dir inventory (all inert except the live one): `state/` (v1-era
mr-234 rehearsal + 901/902 strays), `state-v3rehearsal/` (pre-pool v3
rehearsal), `state-v3pool/` (smoke pairs 5–7, bug-10 evidence),
`state-campaign/` (LIVE). Never write to a rehearsal ledger.

### Boundary fast-fail amendment — manifest v4 (2026-10-18, operator GO)

**Incident (pair 1/A, pre-amendment):** the attempt's job `40444dd5`
was picked up by a fleet agent 18 s after submit (healthy), but MAAS
reported **Failed Deployment** at 08:30:41Z ("unexpected or deployment
failure status signal") and the agent released the node at 08:32:18Z.
The job's `results` still carried `device_info.device_ip`
(a rig-class address — read by the connector pre-release), so the driver
granted `ip` at 08:31:21Z on an already-dead node and burned the full
45-min rig-wait window ssh-polling it (each poll fails in ≤10 s via
`ConnectTimeout=10`, silently; `sshfail_max` is a monitor-phase
counter and never applies to rig-wait). Prognosis was ~09:16:21Z
INFRA_FAILED "endpoint not ready in window" — 45 min of wall clock for
a signal that sat in the same `results` payload the driver had already
parsed (`provision_status: 1`, `job_state: complete`), 40 s after the
failure. First occurrence of this shape: pair 6/A's failures were
ssh-reachable machines that never finished the rig; this one never
came up at all.

**Operator decision (2026-10-18, chat GO):** detect terminal job
failures before entering long-poll mode. Executed as a rule-3
amendment (edit + manifest_version bump + this sign-off entry):

1. **New boundary verb** `job-failed JOB` (the B3 chokepoint): rc 0
   iff `results` shows `job_state=complete` AND a nonzero
   provision/test/cleanup status; rc 1 otherwise; unreadable results
   = unknown = NOT failed (the windows stay the backstop). Dumbest
   shape, same style as `device-ip`.
2. **Adapter** `TestflingerSandbox.job_failed(job)` — optional
   capability, read by the drive loop via `getattr` (the
   `prior_bundles` precedent); fakes without it keep the old shape.
3. **Driver** (`profe.driver.drive`): the `wait_ip` and rig-wait
   loops check `job_failed` each cycle alongside the existing poll;
   a failure cuts the window short with
   `INFRA_FAILED / "job terminally failed (fast-check)"` — lease
   released, in-process n_infra bumped, resubmit follows immediately.
   Healthy flow unchanged (pinned).
4. **Tests:** 3 driver pins (fast-fail in wait_ip; in rig-wait;
   absent-or-false capability changes nothing) + 4 verb pins against
   canned results payloads (the observed real failure is the template),
   stubbed `testflinger-cli` on PATH — no lab contact.
5. **Manifest v3 → v4**: frozen inputs, caps, watchdog windows, seed
   formula, blocks UNCHANGED — only the version bump so every
   pre/post-amendment event is distinguishable by stamp (the store
   binds new appends only; replay folds all events, so mid-campaign
   resume is unaffected — verified in `store.py`/`tracker.py`).

**Battery after:** profe+pairedstats 80/80 (was 77), rig 14/14 (was
10), `ty check` strict clean, ruff clean (both configs), `bash -n`
clean.

**Driver restart (resume-safe per Cardinal rule 2):** the pre-amendment
driver was SIGTERM'd at 08:54:31Z (all ledger events b853d2ce; no
in-flight mutation — no DUT attempt process existed, the lab job was
already terminal). Resumed under v4 with a resume-note; pair 1/A
reattaches (state=submitted, job `40444dd5`, persisted rig-class ip), the fast-check fires within seconds → attempt 1 recorded
INFRA_FAILED (n_infra 1/3, truthful detail) → attempt 2 resubmits to
the pool immediately. Net: attempt 2 launches ~25 min earlier than the
old shape, and every future failed deploy costs seconds instead of
45 min (both early windows covered, including the no-`device_info`
acquire-failure variant).

### Publish scrub — genericized lab identifiers (2026-10-18, operator)

**Order (operator surgery, 2026-10-18):** remove node names (four
fleet/rig machines), real lab addresses (the 10.x lab subnet), and the
fleet queue name from everything that ships.

**Executed:** 27 verified replacements across 10 branch-delta files
plus one main-era research doc (`CONTROLLED-ENVIRONMENT-EXPLORATION.md`,
1 hit). Replacements: manifests/fixtures carry `__TWIN_QUEUE__` — the
rig template's own substitution token, so the kit stays coherent
(manifest `twin_slots` = the token; deployment substitutes the real
name) — and generic phrasing in prose ("a fleet agent", "a
CSV-unverified fleet node", "a DL380 Gen11 lab rig", "a rig-class
address", "the lab subnet"). Hardware classes (DL380 Gen10/Gen11), pair
numbers, and ledger stamps (historical facts, no identifying value)
kept by design.

**Consequences, on the record:** (1) frozen §3 content scrubbed by the
same order — this entry is the sign-off; (2) the coin draw is seeded
`20261030 + pair` only — the queue name was never a seed input, only
the coin's printed label, so protocol behavior is unchanged; (3) the
campaign manifest sha8 is now **`dab48f0f`** (v4 post-scrub) — the
kit-README hash references updated; no code pins existed (the
`b35f5c36` fixture label in test_driver is synthetic); (4) the full
unscrubbed provenance (this entry's original wording, the captures,
the driver/handover logs) stays in the on-disk parks
(`rig/state-campaign/`, `.pi/` — gitignored, not shipped).
