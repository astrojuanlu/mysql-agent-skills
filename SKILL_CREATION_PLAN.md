# Skill Creation Plan — mysql-llm-skills

Last updated: 2026-09-24

---

## 1. Constraints: model limits

Source of truth for OpenRouter model metadata: `/home/ubuntu/.pi/agent/models-store.json`.
Note: most entries list `contextWindow` but leave `maxOutputTokens` as null — when null,
assume a conservative budget rather than an unbounded one.

| Model | Provider | Context window | Max output tokens | Notes |
|---|---|---|---|---|
| claude-fable-latest | openrouter | 1,000,000 | null (assume ~64k) | Comfortable; can absorb a large SKILL.md body |
| claude-haiku-latest | openrouter | 200,000 | null (assume ~32k) | Tighter; keep bodies lean |
| claude-opus-latest | openrouter | 1,000,000 | null (assume ~64k) | Comfortable |
| grok-latest | openrouter | 500,000 | null (assume ~32k) | Middle ground |
| z-ai/glm-5.3-flash (this session) | openrouter | (not in store) | (not in store) | Reasoning effort configurable via `PI_REASONING_LEVEL`; assume conservative defaults |
| deepseek, minimix, kimi, o3/o4 variants | openrouter | (varies) | (varies) | Not confirmed yet — check before finalizing each skill's body size |

**Working limits derived from the above (applied to every skill):**

- `SKILL.md` body: **≤ 150 lines**, aiming for **≤ 6,000 tokens** of body content
  (strictest reading of the skill-forge "body budget" guidance, so it fits even the
  tightest model above without pushing reasoning context past its limit).
- `description` field: **< 1024 characters** (skill-forge hard cap; agentskills.io
  recommends keeping it short enough that the *trigger conditions* — not the prose —
  are what makes the model load the skill).
- Only pull a reference file into context when the top-level SKILL.md instructs it —
  never load every reference file at once.
- Every script must be runnable stand-alone (`python3 script.py --help` must work);
  no hidden dependency on files that aren't shipped inside the skill directory.

---

## 2. Reference docs consulted

| Doc | URL | Key takeaways used |
|---|---|---|
| agentskills.io — Best practices | `https://agentskills.io/skill-creation/best-practices.md` | Skills are folders, not code libraries; the SKILL.md is *instructions for an agent*, not docs for a human. Name the skill for what it does, not who uses it. Keep the body progressive: one screen of orientation, then pointers to reference files, then scripts. Don't front-load examples the model hasn't asked for yet. |
| agentskills.io — Optimizing descriptions | `https://agentskills.io/skill-creation/optimizing-descriptions.md` | The `description` is the *only* thing the model sees before deciding to load the skill — it must name the trigger conditions (inputs, file types, task verbs) explicitly. Third person. No marketing language. Include "Use when…" plus a concrete list of the tasks that should activate it. |
| DeepMind science-skills — workflow_skill_creator | `https://raw.githubusercontent.com/google-deepmind/science-skills/refs/heads/main/skills/workflow_skill_creator/SKILL.md` | Structure: frontmatter → Overview → When to use → Inputs → Workflow (numbered steps) → Output. Heavy emphasis on explicit, checkable intermediate steps rather than prose. Good template for "operator" skills that wrap a multi-step process. |
| bm629 agent-skills — skill-forge body budget | `https://raw.githubusercontent.com/bm629/agent-skills/refs/heads/main/skills/skill-forge/SKILL.md` | Body budget: ≤150 lines, ≤6k tokens for SKILL.md. Descriptions <1024 chars. Anything longer belongs in a reference file the body links to. Every skill should be loadable in full on a weak/cheap model without blowing its per-call budget. |

(All four were read at the start of this session; they are the source for the limits in
§1 and the layout rules in §4.)

---

## 3. Skills to create

Scope trimmed from the original 15-skill wishlist to **6 core + 2 optional** so the whole
set can be built and verified in one session. Each skill gets its own directory
(`skills/<name>/`) inside this repo, each with `SKILL.md` + any reference/scripts.

| # | Skill name (dir) | Status | Description (draft, <1024 chars) |
|---|---|---|---|
| 1 | `mysql-health-check` | core | "Operator skill for Charmed MySQL. Use when the user asks to check cluster health, inspect status, or diagnose replication/lag issues. Triggers on: 'is mysql healthy', 'check juju status', 'replication lag', 'cluster is degraded', 'unit is offline'. Walks through `juju status`, `mysql-shell` cluster.status(), and alerts, then reports a verdict." |
| 2 | `mysql-backup-restore` | core | "Operator skill for Charmed MySQL backups. Use when the user asks to create, list, verify, or restore a backup (mysqldump / xtrabackup via `create-backup`, `list-backups`, `restore`). Triggers on 'take a backup', 'restore from backup', 'backup failed', 'S3 config'. Covers prerequisites, S3 credentials, and safety checks before restore." |
| 3 | `mysql-upgrade` | core | "Operator skill for Charmed MySQL in-place upgrades (e.g. 8.0 → 8.4). Use when the user asks to upgrade the charm, refresh a unit, or plan an upgrade path. Triggers on 'upgrade mysql', 'charm refresh', 'rolling upgrade', 'major version bump'. Includes pre-flight checks, backup-before-upgrade, per-unit refresh order, and post-upgrade verification." |
| 4 | `mysql-troubleshooting` | core | "Diagnostic skill for Charmed MySQL. Use when something is wrong but the user hasn't named a cause: unit won't start, juju hook failed, mysql-shell errors, OOMKilled, disk full, split-brain. Triggers on error pastes, 'unit is in error state', 'mysql won't start', 'juju debug-log'. Guides a bisection workflow: juju status → hook logs → mysql logs → mysql-shell." |
| 5 | `mysql-observability` | core | "Skill for wiring up monitoring for Charmed MySQL with COS (Canonical Observability Stack). Use when the user asks to relate `grafana-agent`/`cos` to the MySQL charm, or asks about metrics/dashboards/alerts. Triggers on 'set up monitoring', 'no metrics in grafana', 'alert rules', 'COS integration'." |
| 6 | `mysql-tuning` | core | "Skill for tuning Charmed MySQL. Use when the user asks to change config (buffer pool, max_connections, innodb settings) via `juju config`, or asks why performance is poor. Triggers on 'slow queries', 'increase max_connections', 'tune buffer pool', 'charm config'. Includes config → restart implications and how to read current values." |
| 7 | `charmed-mysql-primer` | optional | "Reference skill giving background on Charmed MySQL architecture (Juju charm layout, mysql-shell, InnoDB Cluster, relations to data-integrator/cos). Use when the user is new to the deployment or asks a general 'how does this work' question that isn't a concrete operation." |
| 8 | `mysql-cascade-pitfalls` | optional | "Skill listing known sharp edges of operating Charmed MySQL: relation removal side-effects, backup/restore cluster-id mismatch, upgrade-order mistakes, and common juju-action footguns. Use when a user is about to do something destructive or reports an unexpected side-effect after a standard action." |

Each skill directory will look like:

```
skills/<name>/
├── SKILL.md          # ≤150 lines, frontmatter (name, description) + body
├── references/       # only if the body needs to point at longer material
│   └── <topic>.md
└── scripts/          # only if there's a runnable helper
    └── <tool>.py
```

---

## 4. Planned directory tree + scripts

| Skill dir | Reference files planned | Scripts planned |
|---|---|---|
| `skills/mysql-health-check/` | `references/cluster-status-interpretation.md` | `scripts/check_health.sh` — one-shot wrapper that runs `juju status`, extracts mysql unit states, and prints a summary |
| `skills/mysql-backup-restore/` | `references/s3-and-pitfalls.md` | `scripts/pre_restore_check.sh` — verifies S3 relation config + backup file exists before a destructive restore |
| `skills/mysql-upgrade/` | `references/upgrade-matrix.md` | `scripts/preflight_upgrade.sh` — checks current version, backup freshness, and cluster health; refuses if unsafe |
| `skills/mysql-troubleshooting/` | `references/log-signatures.md` (common error strings → meaning) | none — the skill is a decision tree, not a script |
| `skills/mysql-observability/` | `references/metrics-and-alerts.md` | none — mostly juju relation commands |
| `skills/mysql-tuning/` | `references/config-reference.md` (charm config options ↔ mysql variables) | none |
| `skills/charmed-mysql-primer/` | (single-file SKILL.md is likely enough) | none |
| `skills/mysql-cascade-pitfalls/` | (single-file SKILL.md is likely enough) | none |

Line-count estimates for the three scripts above: each ~80–120 lines of bash with
`set -euo pipefail`, argument parsing, and clear stdout — well within any model's
per-call budget.

---

## 5. Other notes

- **No web access from this sandbox** was confirmed for arbitrary fetches, so §1 limits
  rely on the numbers actually quoted in the four reference docs (all of which state
  150 lines / 6k tokens / 1024-char description explicitly). Where a target model's
  limits are unknown, the strictest portable limit is used.
- All skills will be written in the **imperative, second-person instruction style**
  recommended by agentskills.io ("Run `juju status`", not "You could run…").
- Every SKILL.md must end with a short **"Do not"** section (anti-patterns from
  skill-forge guidance), e.g. "Do not restore without a fresh backup."
- Before declaring done, each skill gets a smoke test: load it in a fresh session and
  confirm the body + one reference file fit within a single model call comfortably.
