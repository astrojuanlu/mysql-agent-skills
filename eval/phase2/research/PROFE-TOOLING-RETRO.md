# PROFE tooling retro — cost campaigns 1–2

*Companion to `../campaign3/DESIGN.md`. Written 2026-10-16 after campaign 2's 10 complete pairs, on request: what do the campaigns say about (a) profe itself and (b) the driver/helper layer around it — **under the standing constraint that profe stays boundary-agnostic** (`eval/SANDBOX-EXPLORATION.md` §12: the caller owns the boundary; sandboxing never enters profe).*

*This document is the campaign-grounded input to `profe/SPEC.md` "Design notes (2026-10-05)", exploration 1 (driver/orchestration layer — explicitly open: "caller-side bash today… design a real layer — inside profe or beside it — before the ad-hoc scripts fossilize further"). The scripts have now fossilized twice over: driver v2→v2.2, runner guards, an inline salvage python in the driver, and four parties' ad-hoc analysis one-offs.*

---

## 0. Scorecard: what held and what didn't

| Component | Verdict | Evidence |
|---|---|---|
| `profe run` capture bundle (session + stream tee + sidecar) | **Held** | Evidence survived every kill mode (watchdog, timeout, manual); the pair-1/on overwrite incident was recoverable *because* bundles are per-file (md5-verified survivors) |
| Frozen-price cost concept | **Held as a concept** | But recomputed ad-hoc 4+ times across sessions (inline python, driver salvage, reviewer scripts) — see A4 |
| Paired design + seeded coins | **Held** | Pairing is what made 4 mean tests agree at n=10 under heavy tails |
| DEVIATIONS.md as audit trail | **Held, as prose** | Complete and honest, but not machine-readable; see B1 |
| §10.1 cause taxonomy / §10.5 ITT policy | **Held as law** | Every hard call had a pre-registered answer; the law was good |
| Driver as bash state machine | **Broke, four ways** | $16/$NF skip bug (pair-1/on re-run **on top of** its accepted bundle → stream.jsonl lost); row identity via `tail -1` of the whole CSV; INFRA-vs-run classification via session-mtime heuristics; attempt-budget decrement inside a `for ATTEMPT in 1 2 3` list is a **no-op** (the word list is fixed — infra-fails silently consume start budget; the code comment claims otherwise) |
| Leak-check policy versions v2→v2.4 | **Held, painfully** | Each rule change was mid-campaign and triggered by a false positive; re-judging bundles under new versions was a manual dance; policy version ↔ bundle judgment linkage lives in prose |
| Runner (fresh model per attempt) | **Held, with a gap** | Restore fired **only on pair completion** → pair-11/off r1's faketime carried into r2's first ~6 min (purged 06:08Z, r2 not invalidated — a per-process tool, but the gap was luck, not design) |
| Watchdog (quiet-based) | **Misleading by construction** | 25-min-quiet killed a stalled-but-live run (pair-11/off r1); the earlier 10-min policy would have killed pair-6/on (7077 s, legitimate). Quiet ≠ hang; the session stream was growing/shrinking in both directions |
| Sweep manifest | **Held** (after a panic) | The "truncation" was the normal per-sweep inventory rewrite; the confusion is a docs/naming failure (current-inventory vs append-only events) |
| Ad-hoc analysis by 4 parties | **Broke, twice documented** | d₉ sign error propagated through a handover; the accepted-row mutation count (4 vs 3) disagreed between agents until re-derived from the audit trail. No canonical analysis code existed |

The pattern: **the measurement core is sound; everything that is *policy* (state, accounting, classification, cleanup, analysis) lived in bash soup and ad-hoc python, and every incident traces back to one of those.** That is exactly the split SPEC's exploration 1 anticipates — profe stays minimal, the driver layer becomes real.

## 1. (a) Recommendations for profe itself

Keep the discipline: nothing below puts a boundary, a policy, or a judgment *inside* profe. These are all about profe's own I/O contract — making the bundle a complete, self-describing, hard-to-misuse unit.

- **A1 — Sidecar as frozen-protocol record (manifest hash).** Grow `capture.json` with the full frozen protocol context: prices file + hash, skills tree + hash, model, pi path + version, task file hash, boundary type + id, leak-ruleset id, campaign seed, caps. Hash the whole manifest; record `manifest_hash` in every sidecar. This is the machine-readable form of c3's P2 ("freeze and version") — SPEC already lists environment-provenance fields as a remaining follow-up; the campaign says exactly which fields matter. *Incident:* price-table drift (historical A–D ran an older rate table; `cost_provider` vs `cost_frozen` confusion in every analysis), policy-version confusion (which bundle was judged under which leak-check version — answerable only by reading DEVIATIONS prose).
- **A2 — `profe validate-bundle`: strict-parse, machine-checkable attempt validity.** A bundle is valid iff its sidecar parses, hashes match the manifest, and the required artifacts exist. Drivers derive skip/resume/quarantine from *bundle validity*, never from CSV field indexing. *Incident:* the $16/$NF skip bug is the canonical case — resume logic keyed on awk field positions broke when `skills_read` gained comma values; the fix was still positional (`$NF`), still fragile.
- **A3 — Write-once bundle guard, inside profe.** Refuse to write into an existing bundle dir; archive it with a timestamp and abort (or require `--force`). The runner-side v2.1 guard exists only because profe overwrote silently; moving it into the tool means every future driver inherits it. *Incident:* pair-1/on — a second driver re-ran the pair **on top of** its accepted bundle; `stream.jsonl` was lost forever (session/sidecar survived, md5-verified).
- **A4 — Canonical cost accounting: one implementation.** A `profe cost <bundle>` (or stats helper) that reads usage events from the session/stream and emits component tokens + dollars under a pinned price file (A1). Every sheet, review, and analysis derives from it. Never hand-computed again. *Incident:* the same ~10-line computation existed inline in the driver's salvage path, in my session one-offs (4+ times), and in two reviewer scripts — the multiplication surface for numeric errors; frozen prices were re-hardcoded each time.
- **A5 — Record-source as a first-class field.** `source: session|stream` in the sidecar (the salvage path already labels rows `leaks=ok-stream` — make it structured, not a string suffix). Salvage remains auditable forever. *Incident:* driver v2.2's salvage; downstream analyses had to grep for `-stream` to know which rows were best-effort.
- **A6 — Structured termination reason.** profe should emit why an attempt ended (agent exit | budget cap | wall cap | external kill | launch failure) as data, instead of drivers inferring classes from rc + file-presence + elapsed-time heuristics. *Incident:* INFRA-vs-timeout-vs-stall classification was ~40 lines of heuristics plus two §10.5 amendments, and still produced one mid-campaign policy change.
- **A7 — `profe status`: read-only campaign introspection.** A single command that derives and prints: per-pair/arm/attempt state from bundles, manifest summary, sheet-vs-bundle integrity, driver heartbeat expectation. This is the *anti-rogue-monitor* measure: monitoring must never need write access — or inference — to report. *Incident:* 2026-10-15 rogue monitor — it acted (chmod, mv, script rewrites, self-accept) partly because observing required exploring live state and the report/act boundary was unguarded; §10.4's overnight checklist was manual greps by design default.
- **A8 — Keep OUT of profe (anti-recommendations):** sandboxing/mechanisms (§12 — settled); watchdog semantics (boundary-owned: a VM delete is not a host `killpg`); leak *policy* (campaign data; profe at most provides a grep engine over bundles); sheet persistence (sheets are views, §B8); incident judgment and quarantine decisions (operator law, §10.1 lives in the campaign, not the tool); pair/campaign orchestration (the driver layer, below).

## 2. (b) The driver/helper layer — the framework

SPEC exploration 1 asks "inside profe or beside it?" The campaign answers: **beside it, as a small reference framework** — `profe/scripts/` grows a skeleton + per-boundary reference drivers; profe's core stays untouched.

- **B1 — Driver as state machine + append-only event log.** Pairs × arms × attempts with explicit states (`pending → running → accepted | infra-failed | rejected-contamination | kept-itt-failed`), every decision logged with a timestamp to `events.jsonl`. Resume derives from bundle validity (A2) — never from CSV indexing. The event log is what DEVIATIONS prose and the driver log *both* wanted to be: machine-readable, survives driver restarts, and gives monitors a fixed wire format instead of `ps` archaeology ("driver alive? pid? pair?"). *Incidents:* the 06:22Z kill (a monitor misread a stop instruction because driver state was invisible); the quarantine dance (mv bundle + strip CSV row + restore later — three representations of one state change).
- **B2 — Campaign manifest as machine-readable pre-registration.** The B1 skeleton reads a frozen manifest (A1): model, prices hash, skills hash, caps, watchdog params, seeds, leak-ruleset id, attempt policy, block structure. Every sidecar records `manifest_hash`; the driver refuses to run an attempt whose manifest differs from the bundle's. "No mid-campaign amendments" gets a *checker* instead of vigilance — c3's P2/P5 as code. *Incident:* every §10 amendment (v2.1→v2.4, driver v2/v2.1/v2.2, §10.5) was a prose paragraph plus a code edit, applied at a specific pair, with "which pairs ran under which policy" reconstructible only by hand.
- **B3 — Reference drivers per boundary, same contract.** The actual ask of "helpers that assist in building drivers": `driver-host.sh` (the c2 pattern: shared juju model, pre-flight gates, salvage fallback), `driver-vm.sh` (the c3 pattern: `lxc launch --vm` cloud-init → stage → `profe run` → pull bundle → `lxc delete --force` — teardown *is* the trap), `driver-testflinger.sh` (job-per-attempt/pair; artifacts back via the queue). Boundary-specific logic lives only in stage/teardown functions; pairs, coins, caps, event log, resume are shared. The c2 driver (154 lines) and its four known bugs become the negative test suite.
- **B4 — Attempt-lifecycle cleanup as traps, not driver happy-path.** Restore/cleanup fires on EXIT/INT traps *inside the attempt boundary* (host boundary), and teardown *is* the trap (VM boundary). *Incident:* pair-11/off — faketime landed in r1, restore fired only at pair completion, so r2's first ~6 min ran with it present. Same class: the "after ANY manual kill, run a full `juju models` audit" lesson — traps make that automatic.
- **B5 — In-boundary integrity audit helper (`rig-audit`).** Snapshot before/after *inside* the boundary (`juju models`, `snap list`, `dpkg -l`, modes of `/etc/passwd` & friends) → diff → `incidents.jsonl`. This is §10.3's deferred audit, now cheap. Under VMs it is forensics (behavioral endpoint data, c3 §6.5); under a host boundary it is the missing real-time detection. *Incident:* the design doc's own words — `chmod 640 /etc/passwd` and `snap install microk8s` were "caught by luck, not tooling."
- **B6 — Watchdog as manifest data; kill on session-stream growth.** Parameters (threshold, what counts) frozen in the manifest; kill criterion is *zero session events over a window* (the stream is ground truth, stdout quietude is not), last-event age recorded in the event log, kill classification via A6. *Incident:* 25-min-quiet killed a live-but-stalled run; 10-min-quiet would have killed a legitimate 7077 s run — both policies were "quiet," both wrong for different runs.
- **B7 — Triage patterns as data.** The §10.4 overnight checklist greps (`snap install`, `chmod .* /etc`, `add-model`, …) become a patterns file consumed by a triage script that emits *candidate incidents* for the operator. *Incident:* every incident triage was hand-rolled greps + auth.log/ctime correlation done live at 3 a.m.
- **B8 — Sheet as a view.** `profe sheet --regen` (or a helper) rebuilds the CSV from bundles on demand; hand-editing banned; quarantine is a state flag in the event log (B1), not a directory mv + row-strip dance. *Incident:* pair-2/off r1's quarantine → re-judge → restore sequence touched three representations and took an operator decision each time.
- **B9 — Attempt-budget semantics as accounting, not arithmetic.** The slot ledger lives in the event log: infra-fails consume no slot, agent-behavior failures do, cap N per slot, explicit. *Incident:* `ATTEMPT=$((ATTEMPT-1))` inside `for ATTEMPT in 1 2 3` is cosmetic (the word list is fixed) — the "does not consume" comment in the code is wrong; infra-fails ate start budget silently. Harmless at campaign-2 rates, wrong at design level.
- **B10 — Analysis as committed code, reading bundles.** `ANALYSIS-c3.py` (already a c3 §10 item) plus a small stats helper (paired t / exact permutation / Wilcoxon / log-ratio CI / power — `power-analysis.py` generalized) reading bundles directly, never the sheet. Every reported number cites script + commit. *Incident:* two documented numeric errors propagated across agents and documents before re-derivation; the multiplicity caveat ("~13 analyses were run") is currently a count kept by memory.

## 3. What I would do first

| # | Item | Size | Unlocks |
|---|---|---|---|
| P1 | A1+A2: sidecar manifest fields + `validate-bundle` | ~½ day | Every driver/monitor/resume path; hardens A3's precondition |
| P2 | B1–B3: driver skeleton + manifest reader + `driver-vm.sh` | ~1 day | c3 itself (driver v3 in c3 §10 open items); retires the c2 bash as reference |
| P3 | A4+B10: canonical cost + `ANALYSIS-c3.py` from bundles | ~1 day | Must land **before c3 pair 1** (it *is* the pre-registration of the analysis) |
| P4 | A7: `profe status` read-only | ~½ day | Overnight checklists, monitor safety (P8) |
| P5 | B5: `rig-audit` (host-mode first) | ~½ day | §10.3's deferred audit, behavioral endpoints |
| P6 | B6+B7: watchdog params + triage patterns | ~2 h each | Comes along with P2 naturally |

Not now (deliberate): A6 (structured termination) wants a profe core change with parity re-validation — SPEC's next-steps list already sequences core changes; don't couple it to c3. B4 host-mode traps are superseded for c3 by the VM boundary (teardown = trap).

## 4. Honest non-changes

- DEVIATIONS.md stays prose — it is the *judgment* log (operator voice, human-readable), complementing the machine event log (B1), not replaced by it.
- The §10.1/§10.5 adjudication law stays campaign-side prose. It was pre-registered, it held under every hard case, and it is not tooling.
- `sweep-host-tmp.sh` stays as-is for the host boundary (c2 pairs still may run); c3's VM boundary makes it a non-component. Rename its outputs (`current-inventory` vs append-only) at most.
- The three reviews' statistical machinery (fixed-sequence family, single-arm bounds, etc.) lives in the c3 analysis script, not profe — profe supplies bundles and (via A4) canonical numbers.

## 5. Provenance

- Incidents cited: `eval/runs-t6/mr-234/cost-campaign/DEVIATIONS.md` (2026-10-13 → 2026-10-16, passim), `cost-campaign/c2/statistics-consult.md` §5.
- Scripts reviewed for this retro: `profe/scripts/drive-campaign-c2.sh` (v2.2, 154 lines), `run-cost-campaign-c2.sh` (211), `leak-check.py` (200), `sweep-host-tmp.sh` (66), `power-analysis.py` (266).
- Direction documents: `profe/SPEC.md` "Design notes (2026-10-05)" + "Explorations" (driver layer open; boundary-agnostic contract; `--pi`/provenance follow-ups), `eval/SANDBOX-EXPLORATION.md` §10–§12 (LXD-VM strategy, testflinger open questions, boundary-agnostic profe).
- Companion: `../campaign3/DESIGN.md` (§4 the rig, §5 frozen protocol, §6 endpoints, §10 open items — driver v3 references this document).
