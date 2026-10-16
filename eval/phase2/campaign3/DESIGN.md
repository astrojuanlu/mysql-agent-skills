# DESIGN — cost campaign 3 (c3): confirmatory skills A/B on an isolated rig

*Status: **proposed** 2026-10-16. Becomes the c3 pre-registration when
committed at c3 start (§12); until then the operator may amend it.
Provenance and review trail: §11.*

---

## 0. Summary

Same task (`mr-234`), same model and price vector, same five skills — but a
rebuilt rig and a rebuilt analysis plan:

- **Per-attempt disposable rig** (validated): every attempt runs on a
  fresh, fully-rebuilt environment that nothing survives and nothing leaks
  out of — a **Testflinger lab machine**, provisioned fresh per job and
  released after artifact recovery. The recipe (persistent deploy + the
  juju-on-k8s-snap reference stack) was validated end-to-end on
  2026-10-16 with a full `mr-234` attempt (probe record in §3.6).
  Isolation is a *measurement-validity* fix first (§3.5), a host-safety
  fix second.
- **30 pairs in 3 blocks of 10**, futility look at block boundaries, hard cap
  50. No efficacy-driven early stop.
- **Price-invariant endpoints first**: tool calls and total token volume are
  the pre-registered primaries; dollars enter only as a frozen-price
  descriptive layer plus one pre-frozen exceedance threshold.
- **Budget cap, not a wall-clock cap** (P4, §4.4): cost is never censored.
- **No mid-campaign amendments** (P2). No leak-check re-judging dance (§3.5).
- Campaign 2's data is **exploratory**; c3 starts at n = 0. No pooling
  across rigs — the friend-review suggestion to promote a campaign-2 pair
  to "first confirmatory pair" is **declined**: it would confirm on a rig
  c3 no longer uses.

## 1. Design inputs: what campaigns 1–2 established, and the verdicts

The operator's five campaign-2 wrap-up takeaways, adjudicated. All
campaign-2 numbers live in `../campaign2/RESULTS.md` (single source;
campaign 1: `../campaign1/RESULTS.md`; provenance in §11) — only the
verdicts and the qualitative lessons are imported here.

| Operator takeaway | c2 lesson | Verdict → consequence in c3 |
|---|---|---|
| **1. Last round of skill edits worked** (both core skills read in every ON run; unused skills free) | adoption ≠ adherence: the mined pair-10/on run worked from system-prompt descriptions alone and hit friction points already documented in `juju-cli` SKILL.md | **Agree — and demote**: adoption = QA gate + covariate (§4.1); adherence is the gap c3 measures (§6); staged-layout skill edit before freeze |
| **2. Tool calls significantly reduced; reportable as success** | the calls ratio persists late-campaign where the cost effect fades, and it is the mechanism (log cost ≈ log calls + log per-call); the c2 result is post-hoc on that data | **Agree descriptively; not yet established** → calls = primary #1, confirmatory on new data (§5.1) |
| **3. Time drift not explained by caching/learning** | the paired difference attenuates over pair index — consistent with rig/protocol churn (c2's own mid-campaign amendments), not provider caching (pair-symmetric) or agent learning (none); within-pair tests stay exact under drift | **Agree, sharpened** → freeze + version (P2); blocks of 10 expose drift as a reported contrast; amendments never mid-block |
| **4. Censoring wall time but not cost complicated analysis; cost structure complex; want simpler** | cost was never censored — the pain was cap-adjacent ITT policy (timeout-keep) and 3-component price dependence (any non-negative price vector favors ON, but effect size moves with prices); the cost effect was borderline and outlier-sensitive; "tail eliminated" is not certified distribution-free | **Agree** → budget cap only (§4.4); price-invariant primaries, dollars descriptive + sensitivity table (§5.1); dispersion described, never certified (§5.4); per-component capture stays (free) |
| **5. Need isolation; testflinger-class remote infra** | every landed host mutation and baseline-drift event was off-arm in origin; the failure modes were isolation failures (leftover state, inherited hazards, watchdog-killed installs); Fisher with a zero cell cannot accumulate safety evidence | **Agree** → isolation by construction (§3); behavior rates + single-arm bounds (§5.1.4); rig form settled by probe evidence + operator choice (§3.6) |

## 2. Principles

- **P1 — Isolation by construction.** Nothing an attempt can touch survives
  it; the attempt's world is a fresh machine plus the public internet —
  nothing campaign-internal is reachable from inside it. Leak rules
  become forensics, not gates.
- **P2 — Freeze and version.** Pinned at c3 start: model, pi binary,
  harness, prompt, skills commit, staged sources, cloud-init/rig kit, price
  vector, analysis script. Any change to any of these ⇒ new block,
  versioned, disclosed in the campaign deviations log.
- **P3 — Few, simple, price-invariant endpoints**, pre-registered as a
  fixed-sequence family (§5.1).
- **P4 — ITT with budget caps, not wall caps.** Failed, gave-up, and
  budget-capped runs stay in as cost samples; caps bound worst-case spend
  and create a clean "hit the cap" tail endpoint.
- **P5 — Fixed design, pre-declared stopping.** Futility at block
  boundaries; no efficacy stopping; cap 50 with a declared continuation
  condition.
- **P6 — Attempts are attempts.** Rejected/rerun attempts are counted in
  per-attempt endpoints and in spend-per-accepted-run; no free reruns in
  the economics.
- **P7 — Randomize what is cheap, skip the rest.**
  The arm↔twin-machine-slot assignment is randomized across pairs
  (seeded coin, §3.1) so no machine is systematically the "skills" one.
  No opaque pair/arm codes and no outside-repo condition map: transcripts
  reveal the arm via skill-file reads anyway, so full blinding was never
  available, and the residual machinery was judged not worth its
  complexity.
- **P8 — Monitoring is read-only.** Standing lesson from the 2026-10-15
  rogue-monitor incident: destructive judgment calls (accept/quarantine/
  rerun/kill) belong to the operator only.

## 3. The rig

### 3.1 Topology and pair definition

- **Pair** = one task instance (same `mr-234` prompt, same artifacts, same
  source staging) run **twice**: skills-off and skills-on, each in **its
  own fresh machine**. Pairing absorbs: task-instance quirks, provider
  load at that hour, mirror/template versions, provisioning variance.
- **Block** = 10 pairs, run contiguously (nominally one block per day).
  Blocks are the drift checkpoints: per-block estimates are *reported*,
  then pooled with block as stratum.
- Concurrency: **twin concurrent** — both arms of a pair submit
  simultaneously as two jobs on two **spec-matched** machines (same
  hardware model; pool and slot mechanics in §3.7). One mode for the
  whole campaign. Hardware model is recorded per attempt and reported as
  a stratum; twin arms always land on the same model (§4.5, §5.4).
- The seeded coin (`seed = 20261030 + pair#`) decides, per pair, which
  twin slot (machine A or B) takes the skills-on arm (P7). There is no
  "first" arm under twin concurrency; the coin neutralizes any
  per-machine quirk (a marginally faster sibling) across the campaign.

### 3.2 Per-attempt environment (validated recipe)

1. **Submit** one job per attempt, targeting one of the pair's two
   machines (§3.7). Job yaml pinned and committed: `ephemeral: false` (rig
   lesson: ephemeral MAAS deploys root on overlayfs, which cannot host
   containerd overlay snapshots — the cluster never converges);
   `global_timeout` = 6 h (the Testflinger lease maximum);
   `output_timeout` sized to cover rig build + all rig waits counted
   from *agent pickup*, not test start. Persistent deploy ≈ 13 min, and
   it rotates the machine's host key on every submit — all SSH/rsync in
   the kit pins `StrictHostKeyChecking=accept-new`.
2. **Rig phase** (job test_cmds, ≈ 7.5 min validated): install the
   reference stack — node via nvm, k8s snap `1.32-classic` +
   `hostpath-storage`, `juju 3.6` bootstrap — then dump hardware ground
   truth (nproc/free/lscpu → bundle; replaces CSV spec guesses) and
   probe egress to the model API and the fetch targets the task may use
   (charmhub, github, pypi). Write `~/rig-env.sh` (pinned
   PATH, `UV_PYTHON` pinned to the reference interpreter).
3. **Stage** on the machine (campaign-2 layout, §3.4): task artifacts,
   frozen source trees, skills tree (ON arm only), scratch home with the
   pi config; the provider key rides the SSH channel during reserve,
   never the job yaml (§3.6 probe 3). Charm deploys may use the network
   (§3.3), as in campaign 2.
4. Pre-run snapshot: `juju models` / controller state recorded (must be:
   one controller, one empty `testing` model — and nothing else, by
   construction).
5. Attempt: headless `pi` in a detached tmux on the machine (survives
   SSH drops), session written incrementally on the machine; stdout
   redirected to files **on the machine**.
6. Teardown: pull supplementary logs + the bundle (§4.5) → credential
   scan (both the auth.json key and the models.json literal apiKey) →
   scrub the key on the machine → release it. **The release is the kill,
   the restore, and the sweep in one operation**; the next submit
   reimages the machine from scratch.

### 3.3 Network policy: open, as in campaign 2

- **No egress jail.** The attempt machine keeps normal lab egress
  (model API, charmhub, github, pypi — all verified reachable, §3.6
  probe 2); both arms get it identically, exactly as in campaign 2.
- Rationale: the task bug was hand-picked so that
  its fix is not findable online — the leak channel a jail would close
  does not exist for this task — and the jail's measurement by-products
  (CONNECT logging, an egress-violation endpoint) do not justify their
  build and maintenance cost.
- What the agent reaches remains visible after the fact in the session
  transcript (curl/git/charmhub calls); it is recorded, not enforced.

### 3.4 Source staging: campaign-2 style

- Task artifacts and source trees are staged onto the machine exactly as
  in campaign 2 (same task `mr-234`, same layout — the rehearsed
  launcher already does this). No manifest hardening, no pre-fix
  git-archive pipeline, no pre-cached charms: the open-network rationale
  covers this too (§3.3).
- Recorded per attempt (unchanged): which tree the workspace carried
  (commit/revision in `capture.json`), so any comparison knows exactly
  what the agent saw.

### 3.5 Retired campaign-2 machinery (and parked hardening designs)

| Campaign-2 machinery / considered hardening | c3 decision | Parked? |
|---|---|---|
| `chmod 640 /etc/passwd`, snap/apt installs on the host | machine blast radius; on-machine behavior is recorded (§5.1.4), harmless by construction; the operator host untouched | — |
| Contamination chains (leftover `bug234` model, /tmp canaries, `/tmp` debris) | fresh machine per attempt: nothing to inherit; canaries unnecessary | — |
| Real-tree reads incl. alias paths (`~/Projects/...`) | no `~/Projects` on the machine; workspace-only staging | — |
| Leak-check hard-pattern gates v2.1–v2.8, carve-outs, re-judging dance | record-only forensics from the transcript (what the agent read, wrote, fetched); nothing left to adjudicate | — |
| Campaign-2's 3-class cause taxonomy | two classes: infra vs agent behavior (§4.6); channel destruction is impossible — the channel *is* the machine, and it is disposable | — |
| Campaign-2's timeout-keep salvage path | budget cap (§4.4); a capped run is a failed run with cost = cap | — |
| Restore procedures, /etc/passwd checks, snap drift audits, watchdog orphan risk | machine release at teardown (§3.2.6); the driver runs host-side (§4.7) | — |
| bwrap sandboxing (explored pre-c3) | isolated machines only: snap binaries cannot run inside user namespaces; per-process mounts cannot host a stateful cluster | parked |
| Egress jail | not built: open network, hand-picked bug (§3.3) | parked |
| Need-to-know staging hardening | not built: staging stays campaign-2 style (§3.4) | parked |
| Blinded pair/arm codes + outside-repo condition map | not used: seeded arm↔slot coin only (P7, §3.1) | parked |
| Tooling-only golden image | moot: machines are MAAS-provisioned fresh per job (§3.2) | parked |

"Parked" rows: full discarded designs and their revisit conditions live in
`../research/CONTROLLED-ENVIRONMENT-EXPLORATION.md`.

### 3.6 Rig decision and probe record

- **The five rig probes were resolved 2026-10-16** with two rig-build
  attempts and one full mr-234 rehearsal (attempt pair 901/on) on the
  amd64 DL380 Gen11 lab rig (hardware ground truth in the
  rehearsal bundle):

  | Probe | Result |
  |---|---|
  | Queue availability/latency | pickup 10–20 s from submit on both attempts; persistent deploy ≈ 13 min; rig ≈ 7.5 min; machine spec vastly exceeds the ≥10 GiB bar |
  | Lab egress | model API reachable directly from the lab machine (openrouter.ai 200); charmhub/github/pypi 200; all four OCI registries reachable (401 = auth wall, not firewall) |
  | Key injection | provider key rides the **SSH channel during the reserve phase** (post-provision, pre-release); never in the job yaml, job log, or returned bundle — credential scan covers both the auth.json key and the models.json literal apiKey (both CLEAN) |
  | Artifact plumbing incl. killed sessions | full c2-style bundle recovered (stream, session, capture, grading, leak-report, juju models before/after, juju debug-logs, k8s events/pod logs); the killed-session path was exercised for real (driver argument error → RUN INCOMPLETE quarantine, no silent loss) |
  | Timing fairness | hardware ground truth recorded per attempt (wall-clock policy: §4.4) |
- **Decision: c3 runs on Testflinger for the whole campaign.** Basis:
  the probes passed (above); the rig is validated end-to-end rather
  than a scale-out maybe; a single rig class removes the
  mixed-hardware question entirely; and the operator host stays free
  for everything else. Fallback if the lab becomes unusable
  mid-campaign: the local-LXD variant (a rig change ⇒ new block per
  P2).
- Prior art and lab data: the testflinger playground at
  `~/Projects/Canonical/Data Platform/Charmed MySQL/testflinger-playground/`
  — a plain directory outside both trees (sibling of the worktrees, not a
  git repo); its `data/` holds the 2026-10-16 lab inventory and
  machine-health snapshot used for twin-pool selection (§3.7).

### 3.7 Machine pool and twin slots (spec-matched pairing)

- The rehearsal rig (a DL380 **Gen11** machine) is a singleton in the
  lab pool (2026-10-16 inventory: no second healthy Gen11 exists). It
  stays on rig-validation/ops duty, not c3 pairs.
- **Twin pairs come from the ProLiant DL380 Gen10 fleet** (2× Xeon
  Scalable, Cascade Lake; 56 threads / 3 TB per CSV guesses — ample:
  campaign 2 ran the entire stack on a 19 GiB workstation). Eight
  machines assessed healthy on 2026-10-16 (pass streaks 1–7, all idle) → up to
  4 twin slots. Identical model ⇒ "more or less the same specs" holds
  within every pair; the rig dumps each machine's ground truth into its
  bundle anyway.
- Twin jobs pin machines by targeting each machine's **own queue**
  (named by canonical_id), so both arms provably land on the intended
  twin pair. The driver checks `agent_state == waiting` before
  submitting; a machine grabbed by another queue mid-pair is an infra
  event (rerun, no slot consumed).
- Pool hygiene: health re-checked at each block boundary; a machine
  entering a fail-streak is swapped for a healthy fleet sibling.
  Hardware *model* stays constant within the campaign, and any swap is
  disclosed in the deviations log.

## 4. Frozen protocol

### 4.1 Arms, skills, adoption gate

- Arms: **OFF** = no skills tree in the workspace; **ON** = the frozen
  five (`charmed-mysql-fault-injection`, `charmed-mysql-juju-inspect`,
  `charmed-mysql-nightly-triage`, `juju-cli` 0.12.x,
  `mysql-charms-source` 0.6.x) pinned at the c3-start commit. No per-skill
  cherry-picking; `capture.json` records the skills list.
- **Adoption gate (QA, not endpoint):** an ON attempt is valid only if the
  session shows `juju-cli` and `mysql-charms-source` SKILL.md reads
  (c2: 13/13). A miss ⇒ investigate before accepting
  (likely a staging bug), not silently rerun.

### 4.2 Task, model, prices

- Task `eval/tasks/mr-234.jsonl` unchanged; contract YAML skeleton fix
  already in place. Model `openrouter/z-ai/glm-5.3-flash:nitro`; pi pin
  recorded in the sidecar (currently 1.2.0; a change is a P2 trigger).
- **Price vector: campaign 2's frozen prices** (in 0.0261 / cache 0.0108 /
  out 0.90 $/Mtok) kept for cross-campaign descriptive comparability;
  re-snapshot at c3 start **only** to detect provider price changes — if
  changed, c3 freezes *its own* snapshot and reports both vectors in the
  sensitivity table. Costs are always recomputed from token counts, never
  from provider billing.

### 4.3 Pairs, order, concurrency

- 30 pairs planned; both arms of a pair run concurrently on twin
  machines (§3.1, §3.7).
- Provisioning failures are **infra** (rerun, no slot consumed). Agent
  behavior — anything inside the machine — stays (§3.5).
- Max 3 attempts per arm slot on genuine infra failures; beyond that the
  operator decides (pause the campaign, never drift the rig).

### 4.4 Caps

- **Budget cap**: cost ≥ $0.30 *or* total tokens ≥ 15M *or* calls ≥ 250 ⇒
  kill, count as a failed run **with cost = min(observed, cap)** and flag
  `hit_cap=True`. (Caps ≈ 1.3× the campaign-2 maxima — insurance against
  unbounded burn, expected to bind ~never. If it binds twice in one arm,
  that is a finding.)
- **Wall clock**: recorded, never load-bearing. Ops watchdog: kill the
  attempt after 60 min with zero session activity (hang) ⇒ infra class,
  rerun.
  Generous ops wall limit 4 h; a wall-killed run keeps its tokens as a
  slot sample (campaign-2's timeout-keep ITT rationale carried over),
  verdict discarded,
  `schema_ok=False` unless a final yaml block exists.

### 4.5 Capture

- Per attempt: `session.jsonl` (pi's own, pulled from the machine) +
  sidecar `capture.json` = {task_id, pair, arm, attempt, pi_version,
  pi_path, model, prices_hash, skills_hash, hardware_model,
  provision_dur_s, dur_s, calls, in/cache/out tokens, cost_frozen_usd,
  schema_ok, verdict, hit_cap, infra_flag} + `juju models` before/after.
- **Per-call tags** computed offline from the session (pattern rules,
  versioned with the analysis script): productive / polling / retry /
  exploration; plus sleep-seconds per run (idle share).
- Adherence fields per ON attempt: skills listed, skills read, and (from
  the campaign-2 friction taxonomy) whether the documented guidance was
  later followed on the known traps.

### 4.6 Adjudication (pre-registered, two classes)

1. **Infra**: provisioning failure, host/LXD failure, hang-with-zero-
   activity, operator error → rerun; consumes no slot; logged.
2. **Agent behavior**: everything the attempt did inside its machine → keep
   (ITT); incidents logged to `incidents.jsonl` (machine-readable,
   now trivial under isolation) with class = {mutation-attempt,
   contract-anomaly, other}.
3. Residual contamination rule: if an attempt reaches campaign internals
   by any unforeseen channel, exclude + rerun + document + **stop and
   redesign** (under c3's rig this should be structurally impossible; any
   occurrence is a rig bug, not a data point).

### 4.7 Monitoring discipline

Driver runs on the operator host, **outside** the attempt machines
(state: run sheet, sheet integrity, twin-slot discipline, lab-pool
health). Monitor turns are **read-only** (P8): observe, report, never
mutate campaign state, scripts, or rig. The overnight checklist is now
~4 lines: driver alive, sheet growing, twin jobs paired and progressing,
lab-pool health, operator-host disk headroom.

## 5. Endpoints, sizing, stopping (pre-registered)

### 5.1 Primary family — fixed sequence, α = 0.05 at each step

Each test runs only if the previous passed (fixed-sequence family: no
alpha splitting, family-wise error controlled by the sequence — the
pattern both external reviews recommended):

1. **Tool calls per run**: d = ln(calls_off) − ln(calls_on) per pair;
   exact within-pair sign-flip permutation, **two-sided**. (Paired t and
   Wilcoxon reported as agreement checks.) Rationale: the strongest
   campaign-2 effect, price-invariant, persists late-campaign; expected
   confirmable in ~15–20 pairs.
2. **Total token volume**: same test on ln(total tokens) — the
   price-invariant cost proxy.
3. **Cost exceedance**: P(cost_frozen > **$0.10**) per pair, exact
   McNemar on discordant pairs, **evaluated on c3 pairs only** (c frozen
   from campaigns 1–2: 4-vs-1 discordant at n=13). Declared likely
   underpowered at 30 (needs ~40): a null here does not refute 1–2.
4. **Behavior safety**: per-attempt rates of (a) in-machine
   environment-mutation attempts, (b) contract anomalies — with **exact
   single-arm Jeffreys 95% upper bounds**
   (0 events: 26% @ n=10, 14% @ n=20, 9.5% @ n=30; ≤10% needs ≈19–30
   clean ON attempts); between-arm comparison reported as an
   unconditional exact CI on the rate difference (Boschloo/Miettinen–
   Nurminen), **never** as Fisher-conditioned accumulation. Stopping for
   safety uses the ON-arm bound alone — more OFF disasters are never
   rewarded. Expected to reproduce c2's OFF-vs-ON asymmetry (OFF arms
   self-build decoy environments; ON arms have not) — now a behavioral
   endpoint with no operational risk.

Descriptive/secondary (no claims): dollars at frozen prices + the
price-sensitivity vector table (cache×3, cache=in, out×0.5, out×2,
token-volume-only); decomposition log(cost ratio) = log(calls ratio) +
log(per-call ratio) + per-component token deltas; output tokens per call;
wall time, idle share, pace (s/call), $/s; schema_ok; blind-graded verdict
correctness (§6); spend per accepted run (includes reruns' spend);
skills listed/read/adhered.

### 5.2 Sizing

- **30 pairs** (3 blocks × 10). Power at campaign-2's realised
  parameters (σ_d = $0.0601 raw / 0.68 log), two-sided α = 0.05: at the
  observed cost effect (−$0.033 raw / 0.41 log) n = 30 gives 83% (raw)
  to 89% (log); at a shrunk effect ($0.020 raw / 0.30 log) only 42–65% —
  hence the futility looks and the hard cap 50. The calls endpoint
  (t ≈ 5 at n = 13) carries the family at 30; the cost endpoint is the
  one at risk of landing borderline again.
- Hard cap **50**, continuation beyond 30 only if (a) futility was not
  triggered and (b) family member 1 or 2 is still unresolved (CI spans
  "no effect" and "meaningful"). Continuation is a **new block** with a
  one-paragraph justification in the campaign deviations log, never
  silent.

### 5.3 Stopping

- **Futility (block boundaries only, n = 10, 20, 30):** if the calls-ratio
  95% CI lies entirely within ratio [0.87, 1.15] (±13%), stop and
  report "no meaningful calls effect on the
  isolated rig" as the deliverable (a clean null is a useful result).
- **No efficacy stopping.** Fixed design; the three looks are disclosed
  and the p-trajectory per pair count is reported (the campaign-1
  amended-interim convention: `../campaign1/RESULTS.md`).
- Safety stopping is independent (§5.1.4): any rig-level integrity
  failure — an attempt reaching campaign internals by an unforeseen
  channel (§4.6.3) — ⇒ pause and redesign (that is a rig bug).

### 5.4 Robustness and sensitivities (pre-committed, reported either way)

- Leave-one-out stability for every family member (establishment
  criterion: no single pair flips a conclusion).
- Per-block estimates + pooled-with-block-stratum; between-block contrast
  reported as the drift measure.
- Price-vector sensitivity table (above); per-component decomposition.
- Dispersion *described* (MAD ratio, ES₅₀, trimmed variance) — never
  claimed as certified tail elimination (campaign-2 lesson: parametric
  variance tests are invalid under these heavy tails).

## 6. Skills measurement and policy

- **Allowed pre-freeze edits** (before c3 pair 1, then frozen):
  1. `mysql-charms-source`: teach the staged layout — "charm source
     is provided in the workspace at `<path>` (pre-fix revision); do not
     clone — use the provided tree; never read outside your workspace" —
     replacing the clone-from-scratch-dir guidance that plausibly
     produced both campaign-2 contamination attempts (5/on, 10/on alias
     path). The guidance is about using what is provided, not about
     what is reachable (§3.3).
  2. The known adherence traps (the four pair-10/on friction points, all
     already covered by current SKILL.md text) stay as-is — the gap is
     adherence, and c3 measures it.
- **Decide before freeze (open item §9):** whether the ON prompt gets a
  "stop at verdict-sufficient evidence" norm (rationale: the pair-10/on
  lesson, §1). Any such norm applies to **both arms** (it is a prompt
  change, not a skill), or not at all.
- **Adherence metrics** (from §4.5 fields): per ON run, fraction of known
  documented traps avoided; reported as the adoption→adherence story.
- **Skill versioning**: skills are a release; a post-freeze edit is a P2
  trigger, and pre/post estimates are never pooled silently.

## 7. Explicitly not doing (analysis and process)

- No mid-campaign amendments (P2).
- No post-hoc endpoint shopping: the family in §5.1 is the analysis;
  exploratory plots are labeled and counted in interpretation.
- No dropping of failed/gave-up/capped runs (ITT, §4.4).
- No Fisher-exact safety accumulation; single-arm bounds only (§5.1.4).
- No pooling across rigs: campaigns 1, 2 and 3 stay separate;
  cross-campaign comparisons are descriptive only (§0).
- No parametric variance/tail significance claims (§5.4).
- No waiting for p < 0.05 to keep using skills internally — c2 already
  supports the deploy decision; c3 exists to *certify and mechanize*,
  not to re-decide (`../campaign2/RESULTS.md`).

## 8. Budget and schedule

- API spend: 60 attempts × ≈ $0.07 ≈ **$4–5** expected; worst case
  bounded by caps: 50 pairs × 2 arms × $0.30 = **$30**.
- Wall time (twin concurrent on the Gen10 fleet, §3.7): per pair-slot ≈
  deploy 13 min + rig 7.5 min + run (budget-capped; ops wall limit 4 h)
  + recovery ≈ 2 min → typically ≈ 55–85 min, worst case bounded by the
  4 h ops wall limit. Four healthy fleet machines ⇒ 2 pairs in flight ⇒
  a 10-pair block ≈ 5–8 h of machine time, queue permitting.
  Provisioning compute is remote and free.
- Storage: bundles ≈ 60 × few-MB on the operator host; machines hold
  nothing after release.

## 9. Open items before pair 1

Resolved 2026-10-16, no action: Testflinger probes (§3.6); charm
pre-cache list (moot — the network stays open, §3.3); egress allow-list derivation (§3.3); pair/arm code randomization
map (P7, §3.1). Each remaining item is cheap; none blocks the design.

- [ ] Verdict correctness: extend the mr-234 rubric to grade mechanism
      depth, grade **all** c3 runs blind, pre-declare the non-inferiority
      margin (campaign-2 mining found an OFF arm reaching the deeper
      mechanism — decide explicitly what depth the rubric pays for).
- [ ] The §6 stop-at-sufficient-evidence norm: yes (both arms) or no.
- [ ] `ANALYSIS-c3.py` committed **before pair 1** — per
      `../research/PROFE-TOOLING-RETRO.md` A4/B10: the analysis script is
      frozen with the design (reads bundles + the canonical cost helper,
      not the sheet).
- [ ] Driver v3 per `../research/PROFE-TOOLING-RETRO.md` B1–B3 (state
      machine + append-only event log + manifest reader + per-boundary
      reference drivers): c2 shape (skip-if-valid, infra/run distinction,
      ≤3 attempts) minus salvage paths (§3.5), plus the §4.4 caps;
      host-side state/run sheet; twin submits per §3.7.
- [ ] Model-API reachability probe on the DL380-Gen10 class (first
      rig-validation run) — verified on a DL380 Gen11 rig only (§3.6).

## 10. Generalization follow-on (after c3, not part of it)

c3 confirms on the vehicle task; the remaining scientific question is
**generalization** — do the skills pay on bugs they were not tuned on?
That is the live-bug mode ("T4", development branch; designed there):
live unsolved bugs, per-bug strata, held-out set, bug interleaved over
time, per-bug estimates shrunk toward the pooled value. The c3 rig is
the T4 rig base; effective n is closer to the number of bugs than the
number of runs.

## 11. Provenance

- Campaign 1: `../campaign1/{DESIGN,RESULTS}.md`; deeper artifacts
  (transcript-mining learnings, phase model, invalid-run bundles)
  archived on the development branch.
- Campaign 2: `../campaign2/{DESIGN,RESULTS}.md` + `run-sheet.csv`
  (26 accepted rows); incident audit trail, consultation brief, and both
  external statistical reviews archived on the development branch.
  Review recommendations folded into §5 — review 1: blocks of 10,
  fixed-sequence family, budget caps, cost-per-verdict, zero-event
  single-arm bounds; review 2: parametric-F invalidity under heavy
  tails, ES₅₀/MAD leads, mediation-by-calls, drift regression,
  censoring analysis, Jeffreys bounds, Bayesian decision view. Where
  they differ (n = 25 vs 30) this design takes the more conservative
  reading (§0).
- Isolation engineering and boundary-agnostic profe: validated on the
  development branch, summarized in §3; `profe/` in this tree is the
  profe-side contract.
- Testflinger probe validation and rig rehearsal (2026-10-16,
  development branch): rig kit, rig-build logs, and the recovered 901/on
  bundle under `eval/runs-t6/mr-234/c3-rehearsal/`; lab inventory and
  machine-health snapshot from the testflinger playground
  (`~/Projects/Canonical/Data Platform/Charmed MySQL/testflinger-playground/data/`,
  §3.7).
- Standing lessons carried in: read-only monitoring (§4.7), ITT for
  capped runs (§4.4).

## 12. Freezing

This document is *proposed*. It becomes the c3 pre-registration when:
(1) the operator signs off (or amends) §5 endpoints and sizing (§5.2),
§4.4 caps, and §6 skill edits; (2) `ANALYSIS-c3.py` + driver v3 + the Testflinger rig
kit (job yaml, rig/payload/recovery scripts) are committed;
(3) the c3-start skills commit is tagged. From that
commit, P2 applies: no amendments, only new blocks.
