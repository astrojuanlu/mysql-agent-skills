# mysql-llm-skills

A collection of Agent Skills (https://agentskills.io/specification) for
debugging and diagnosing problems with Charmed MySQL, based on
https://github.com/canonical/mysql-operators (the `mysql` VM charm and the
`mysql-k8s` Kubernetes charm) and
https://github.com/canonical/mysql-router-operators (the `mysql-router`
and `mysql-router-k8s` charms). The knowledge is distilled from real
debugging case studies.

## Skills

| Skill | Use it for | Needs live access |
|---|---|---|
| `juju-cli` | Getting the Juju CLI itself right: deploy with trust, `juju ssh/exec` into the right k8s container, charm actions vs shell commands and their client-side wait timeouts, config-key discovery, destroy-model with storage, controller/model confusion, leftover background status samplers, sick-controller diagnosis with pod-restart heal, controller destruction. | Yes |
| `mysql-charms-source` | Navigating the mysql charm-family source (canonical/mysql-operators and canonical/mysql-router-operators share one layout): charm sources (`mysql`/`mysql-k8s`, `mysql-router`/`mysql-router-k8s`), fetching the source for a deployed revision, grep cookbook for tracing errors to call sites and tests, charm config vs mysqld configuration. | No |
| `charmed-mysql-juju-inspect` | Diagnosing Charmed MySQL failures both ways. Live: interrogating a misbehaving deployment through Juju — reading `juju status` correctly (app vs unit, agent vs workload), cluster truth via actions, containers/ports/pebble topology, credentials, logs, direct SQL. Offline: static diagnosis from logs, Test Observer bundles, juju crashdumps, `db-dump.yaml`, pasted tracebacks, or issue reports — fingerprints versions, reconstructs hook timelines, maps evidence to source code, classifies the failure via a taxonomy, judges whether a reported regression is credible (git archaeology). Includes a script to parse Juju uniter operation lifecycles. | Both (live or offline) |
| `charmed-mysql-nightly-triage` | Triaging failed CI: nightly runs, PR integration failures, Test Observer executions. Classifies failures (deterministic bug / flake / infra / test-plan bug), exonerates innocent PRs, produces structured summaries. | No |
| `charmed-mysql-fault-injection` | Reproducing failures on a live deployment: the member-death / quorum-loss kill taxonomy, Group Replication guardrails, deterministic repro of race-conditional bugs, model and experiment hygiene (works on a scratch model it owns and tears down). | Yes |

Each skill directory contains a `SKILL.md` (workflow + gotchas) and
`references/` with the detailed material, loaded on demand.

The skills are deliberately scoped. `charmed-mysql-nightly-triage` is
fully artifact-driven and never touches a live system.
`charmed-mysql-juju-inspect` spans both worlds — read-only live
interrogation and offline analysis of captured artifacts. The live skills
(fault-injection, juju-cli) do modify what they are pointed
at — fault-injection on purpose — and carry explicit safety rules: scope
to the named model, prefer a scratch model for destructive experiments,
and verify state after every action.

## Installation

Clone this repository and point your agent at the `skills/` directory
(instructions per harness below), or copy the individual skill directories
into the location your harness discovers. Prefer symlinks where the
harness supports them: they track `git pull` automatically, while copied
directories must be re-copied after every update.

### Using the `skills` CLI (all agents)

The `skills` CLI (https://github.com/vercel-labs/skills) installs into any
supported agent — including Pi, OpenCode, Claude Code, Codex, and Cursor —
as symlinks to a canonical copy, and can update them later:

```bash
npx skills add /path/to/mysql-llm-skills/skills -g   # from a local checkout
npx skills add astrojuanlu/mysql-agent-skills -g     # or from GitHub
npx skills add astrojuanlu/mysql-agent-skills -a pi -g --skill '*' -y
npx skills update                                    # update installed skills
```

`-g` installs globally (for Pi: `~/.agents/skills/`, which Pi reads
natively); without `-g` it installs project-locally (`.agents/skills/`).
Inspect without installing: append `--list`.

### Pi

Pi discovers skills in `~/.pi/agent/skills/` and `~/.agents/skills/`
(global) or `.pi/skills/` and `.agents/skills/` (project, after trusting
the project). Directories containing a `SKILL.md` are discovered
recursively.

Project-local (recommended for this repository):

```jsonc
// .pi/settings.json in a project that sits next to this checkout
{
  "skills": ["/path/to/mysql-llm-skills/skills"]
}
```

Or symlink the skills into your global directory:

```bash
ln -s /path/to/mysql-llm-skills/skills/* ~/.pi/agent/skills/
```

Use in a session: skills trigger automatically when the task matches their
description, or force one with `/skill:charmed-mysql-juju-inspect`.

### OpenCode

OpenCode discovers skills in `.opencode/skills/` (project) or
`~/.config/opencode/skills/` (global), one directory per skill.

```bash
mkdir -p ~/.config/opencode/skills
cp -r /path/to/mysql-llm-skills/skills/* ~/.config/opencode/skills/
# or, project-local:
mkdir -p .opencode/skills
cp -r /path/to/mysql-llm-skills/skills/* .opencode/skills/
```

### Claude Code

Claude Code discovers skills in `.claude/skills/` (project) or
`~/.claude/skills/` (global).

```bash
mkdir -p ~/.claude/skills
cp -r /path/to/mysql-llm-skills/skills/* ~/.claude/skills/
# or, project-local:
mkdir -p .claude/skills
cp -r /path/to/mysql-llm-skills/skills/* .claude/skills/
```

## Which skill to reach for

- "My deployment is blocked/waiting/degraded — or I have a pile of logs /
  a crashdump / an issue report and no access to the system" ->
  `charmed-mysql-juju-inspect`
- "A nightly run / PR check failed, what happened and is it real" ->
  `charmed-mysql-nightly-triage`
- "Reproduce this bug / simulate quorum loss / test the recovery path" ->
  `charmed-mysql-fault-injection`
- "How do I run this juju command correctly / why did juju behave that
  way" -> `juju-cli`
- "Where is this in the charm source / what does this config option
  actually mean" -> `mysql-charms-source`

The skills are independent; combining them is common (e.g. triage a CI
failure with `charmed-mysql-nightly-triage`, then go deep with
`charmed-mysql-juju-inspect`).

## Repository layout

- `skills/` — the skill directories (above).
- `profe/` — the evaluation harness: `validate` checks skills against the
  agentskills specification, `compile` turns query corpora into
  trigger-cell tasks and rubrics, `run` executes tasks through headless
  pi sessions, `grade`/`report` produce pass rates, trigger maps and
  per-run detail. See `profe/README.md`.
- `eval/` — `phase1/` holds the curated query corpus (per-skill
  train/validation splits, near-misses), the recorded runs with their
  reports, and the phase-1 results; `phase2/` holds the trigger
  measurement and the cost campaigns (`campaign1/` and `campaign2/`
  wrapped; `campaign3/` the proposed confirmatory design), plus `research/`
  notes. `EVAL-PROCESS.md`
  documents the methodology.

## Development

Validate skills with the harness:

```bash
cd profe
uv run profe validate --skills $(find ../skills -mindepth 1 -maxdepth 1 -type d)
```

Checks: frontmatter correctness, name rules (lowercase, hyphens, 64 chars
max, matches the directory), description under 1024 characters.

The body budget is guidance the tool does not enforce: keep SKILL.md
bodies lean and push detail into `references/`, loaded on demand, so every
skill loads in full even on a small model. The mature skills have grown
(165–443 lines) — keep additions minimal.

When editing skills, keep in mind:

- Descriptions carry the entire triggering burden — they are the only
  thing the agent sees before deciding to load a skill. Phrase them as
  "use when..." and cover both explicit and implicit phrasings of the
  task.
- After a description edit, re-compile the skill's train queries and
  re-run their trigger cells through the harness (`profe compile`, then
  `profe run --stop-on skill-read`), and only then its validation split.
- The bundled script (`charmed-mysql-juju-inspect/scripts/parse_uniter_ops.py`)
  has no external dependencies and is smoke-tested against synthetic logs;
  run `python3 skills/charmed-mysql-juju-inspect/scripts/parse_uniter_ops.py --help`
  after changing it.
