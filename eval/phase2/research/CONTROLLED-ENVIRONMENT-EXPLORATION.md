# CONTROLLED-ENVIRONMENT-EXPLORATION — parked hardening ideas (c4 / T4 candidates)

*Status: parked 2026-10-16, during c3 design finalization. These ideas were
designed in detail for campaign 3 and then set aside by operator decision —
not because they are wrong, but because the assumption that motivated them
does not hold for the c3 task. This document keeps the designs, their
rationale, their validation status, and their re-entry conditions in one
place so a future campaign can adopt them without redesigning from scratch.
Nothing here is a c3 commitment; c3's open network and simple staging are
deliberate (c3 DESIGN §3.3/§3.4, amendment 2026-10-16).*

**The pivotal fact.** The c3 task bug (`mr-234`) was hand-picked so that
its fix is **not published online**. That single property retired three
layers of hardening the c3 design had originally specified: the egress
jail (§1), need-to-know source staging (§2), and full blinding (§4).
They all matter again the moment we run tasks where that property does
not hold — most obviously the live-bug generalization mode ("T4", c3
DESIGN §10), where the bug comes from a public repo and the surrounding
ecosystem is full of fixes, workarounds, and discussions of *similar*
bugs.

---

## 1. Egress jail (network allow-list + CONNECT proxy)

**What it is.** A per-attempt network policy enforced on the attempt
machine itself (no lab-network changes needed):

- `iptables OUTPUT DROP` on the attempt machine; `ip6tables OUTPUT DROP`
  belt-and-braces.
- The model API allow-list — derived from `~/.pi/agent/models.json` at
  grade time (for the c3 model: `openrouter.ai`, `api.z.ai`) — reachable
  **only** through a whitelist CONNECT proxy run under the stock `proxy`
  user, selected by an iptables owner rule (only the agent's UID is
  forced through the proxy).
- DNS resolution allowed (query-name leak accepted and documented).
- Every CONNECT target logged to the attempt bundle — turning "what did
  the agent *try* to reach" into measurable data even for blocked
  attempts.

**Why c3 dropped it.** Two reasons, in order of weight:

1. The leak channel it closes (an agent finding the upstream fix or an
   equivalent published solution) does not exist for `mr-234` — the bug
   was hand-picked so the fix is not findable online.
2. Its measurement by-products (CONNECT logging, an "egress violation"
   endpoint with single-arm Jeffreys bounds) did not, by themselves,
   justify the build-and-freeze cost of an extra rig component.

**Validation status.** The in-VM variant (iptables inside an LXD VM,
proxy under the stock `proxy` user) was validated on the development
branch (2026-10-04). The lab variant needs no new networking: lab
machines reach the model API directly (verified on a DL380 Gen11 rig,
2026-10-16 — c3 DESIGN §3.6 probe 2), so the jail, not the lab network,
is the enforcement point. Estimated build: ~1 day (script + one
validation run).

**When to revisit.**

- T4 live-bug mode: bugs from public repos, where fixes for *related*
  bugs, technique discussions, and the actual fix (post-merge) are all
  online.
- Any task set without the hand-pick guarantee.
- If "what the agent tried to reach" becomes interesting as a behavior
  endpoint in its own right (it composes with the c3 behavior-rate
  machinery unchanged).
- If an agent is ever observed phoning home or exfiltrating in a way
  that matters — the jail converts that from an incident into a
  recorded, bounded event.

**Interplay with §2.** The jail without staging still lets the agent
fetch what it can name; staging without the jail leaves ordinary
browsing open. They compose; for live-bug work both are wanted.

---

## 2. Need-to-know source staging (bare-mirror git archives + manifests)

**What it is.** Source material for the task never comes from the
network or from the operator's working tree:

- Local **bare mirrors** hold the upstream repos; the attempt workspace
  is materialized via `git archive <pre-fix commit>` — read-only, into
  the attempt staging dir. The agent never needs a git remote.
- The shipped file set is a **denylist-scrubbed, golden-trace-validated
  manifest**: run the reference solution once against the shipped
  corpus and require its touched source paths ⊆ the shipped manifest,
  widening and re-freezing if not. Recorded per attempt: mirror,
  commit, manifest, archive sha256.
- **Charm files pre-cached** on the machine (`~/charms/*.charm` at the
  exact revisions the rig uses), so `juju deploy` need not touch
  charmhub and charm revisions cannot drift mid-campaign.

The pre-fix pin means the fix content is **absent by construction** —
the strongest form of the leak guarantee; transcript greps become
defense-in-depth instead of the primary control.

**Why c3 dropped it.** Same pivotal fact: with the fix not online and
the task identical to campaign 2's, the c2-style staging (working-tree
copy, network charm deploys) is sufficient and already validated in the
2026-10-16 rehearsal. The hardening would have added a pipeline
(mirrors, manifests, golden-trace runs) to freeze before pair 1 for no
c3 benefit.

**Validation status.** Not built. The c2-style staging it would replace
is validated end-to-end (rehearsal bundle). Estimated build: ~1–2 days
(mirror setup + manifest tooling + golden-trace validation run).

**When to revisit.**

- T4 live-bug mode: the task IS an upstream repo at some commit; a
  pre-fix mirror pin makes later upstream history (including the eventual
  fix) absent by construction — this is the single strongest reason to
  build it.
- Whenever "what source did the agent see" must be *provable* per
  attempt rather than recorded.
- When charm/network revision drift could confound a long campaign
  (pre-caching freezes the environment).

---

## 3. Attempt density: several LXD VMs inside one lab machine

**What it is.** Instead of one attempt per lab machine, run k attempts
concurrently as LXD VMs on a single large DUT (a DL380-Gen10 with 3 TB
RAM could host ~8–16 attempt-VMs comfortably). This is essentially the
*original* local c3 rig design (per-attempt LXD VMs, cloud-init recipe,
`lxc delete --force` teardown) transplanted onto a lab machine: the job
provisions the machine, installs LXD, and launches attempt-VMs.

**Important non-objection first:** this is *not* nested virtualization —
the DUT is bare metal, so LXD VMs run on it directly. The recipe's
feasibility is backed by the dev-branch validation (2026-10-04:
juju-on-microk8s bootstraps healthy inside an LXD VM). What is *not*
yet validated: the k8s-snap reference stack inside an LXD VM (microk8s
was the validated variant; snap-in-LXD-VM should behave the same but
"should" is not validated).

**Why c3 stays with one attempt per machine (operator + assistant
concur, 2026-10-16).**

1. **No need.** With 8 healthy Gen10 machines and twin concurrency,
   a 10-pair block is ≈ 5–8 h of machine time (c3 DESIGN §8). Density
   buys nothing this campaign.
2. **One more layer to freeze and validate.** LXD + VM image +
   cloud-init recipe + snap-in-LXD stack — each a c3 freeze-surface
   item (P2) with its own failure modes, for a benefit c3 cannot use.
3. **Shared failure domain.** Co-tenant attempts share kernel, disk,
   and network. Campaign 2's incident taxonomy was full of exactly this
   class of interference (a watchdog-killed install poisoning later
   baselines, /tmp debris, resource contention). Machine-per-attempt
   makes that structurally impossible; VM-per-attempt merely makes it
   unlikely — and turns any interference into an adjudication question
   (which attempt was it? does the pair survive?) that c3's two-class
   taxonomy handles poorly.
4. **Timing contention.** Co-tenants contend for CPU/IO; wall time is
   descriptive-only in c3, but co-tenancy would put noise into every
   descriptive timing claim and complicate the twin-pairing story
   (twins on the *same* machine would share the noise; twins on
   different machines wouldn't — an asymmetry needing its own disclosure).

**When to revisit.** If the healthy pool shrinks below ~4 machines, or
queue contention from other teams makes machine-per-attempt throughput
the binding constraint, or a future campaign (T4 with more tasks)
justifies the freeze cost. Then: validate k8s-snap-in-LXD-VM once,
decide co-tenancy policy (e.g., never co-schedule the two arms of a
pair on one machine — keep twin pairing machine-pure), and reuse the
validated dev-branch recipe.

**Composes with §5:** golden images are mostly worthless for
MAAS-provisioned machines but natural for LXD VMs — a tooling-only VM
image (snaps preinstalled, no cluster state) would cut per-VM cold
start from ~13 min to minutes.

---

## 4. Blinded pair/arm codes (full P7)

**What it is.** Every pair and arm carries an opaque code (e.g.,
`pair_07/arm_B`); the condition↔code map lives outside the repo until
analysis, so transcripts can be read cold without knowing which arm is
which.

**Why c3 dropped it.** Three converging reasons, operator decision
2026-10-16:

1. Transcripts leak the condition via skill-file reads — full blinding
   was never actually available (this was already acknowledged when P7
   was written; the machinery bought only *partial* blinding).
2. Single-operator campaign: the person reading transcripts is the
   person who ran the campaign; a map in a private note adds no real
   separation.
3. The apparatus (code generator, map custody, per-artifact code
   plumbing) is exactly the kind of complexity c3 is trying to shed.

**What c3 keeps instead:** the seeded coin randomizing which twin slot
takes skills-on, per pair (c3 DESIGN §3.1) — the one randomization that
still earns its keep.

**When to revisit.** Delegated or third-party analysis (graders,
reviewers who should not know arms); A/B skill variants where the
difference is subtle and unlabeled; any publication-facing blinding
requirement.

---

## 5. Golden VM images (rejected pre-c3; recorded for completeness)

**The lesson (validated 2026-10-04, dev branch):** publishing a golden
image *with in-flight k8s control-plane state* does not work — restored
cluster state does not resume; it re-converges slowly and unreliably.
Hence provision-fresh-per-attempt everywhere.

**The permitted variant:** a *tooling-only* golden image (snaps
preinstalled, **no cluster state**). Under the adopted c3 rig it is
moot (MAAS provisions fresh per job; there is no image to cache). It
becomes relevant again inside the LXD-VM density idea (§3), where VM
images are locally cacheable and the ~13-min cold start is the thing to
beat.

---

## Provenance

- Designs §1–§2, §5: c3 DESIGN as originally drafted 2026-10-16
  (§3.3, §3.4, §3.5 golden-image row) — full text preserved above.
- §3: operator question and joint assessment, 2026-10-16.
- §4: P7 as drafted 2026-10-16, reduced by operator decision same day.
- Decisions and rationale: c3 DESIGN amendment block 2026-10-16;
  rehearsal evidence in c3 DESIGN §3.6.
