# mysql-llm-skills

A collection of Agent Skills (https://agentskills.io/specification) for
debugging and diagnosing problems with Charmed MySQL, based on
https://github.com/canonical/mysql-operators (the `mysql` VM charm and the
`mysql-k8s` Kubernetes charm). The knowledge is distilled from real
debugging case studies.

## Skills

| Skill | Use it for | Needs live access |
|---|---|---|
| `charmed-mysql-log-autopsy` | Static diagnosis from logs, Test Observer bundles, juju crashdumps, `db-dump.yaml`, pasted tracebacks, or issue reports. Fingerprints versions, reconstructs hook timelines, maps evidence to source code, classifies the failure via a taxonomy; also judges whether a reported regression is credible (git archaeology). Includes a script to parse Juju uniter operation lifecycles. | No |
| `charmed-mysql-nightly-triage` | Triaging failed CI: nightly runs, PR integration failures, Test Observer executions. Classifies failures (deterministic bug / flake / infra / test-plan bug), exonerates innocent PRs, produces structured summaries. | No |
| `charmed-mysql-juju-inspect` | Interrogating a live, misbehaving deployment through Juju: reading `juju status` correctly (app vs unit, agent vs workload), cluster truth via actions, containers/ports/pebble topology, credentials, logs, direct SQL. | Yes |
| `charmed-mysql-fault-injection` | Reproducing failures on a live deployment: the member-death / quorum-loss kill taxonomy, Group Replication guardrails, deterministic repro of race-conditional bugs, model and experiment hygiene (works on a scratch model it owns and tears down). | Yes |
| `juju-cli` | Getting the Juju CLI itself right: deploy with trust, `juju ssh/exec` into the right k8s container, charm actions vs shell commands and their client-side wait timeouts, destroy-model with storage, controller/model confusion, leftover background status samplers. | Yes |

Each skill directory contains a `SKILL.md` (workflow + gotchas) and
`references/` with the detailed material, loaded on demand.

The skills are deliberately scoped. The two artifact-driven skills
(log-autopsy, nightly-triage) never touch a live system. The live skills
(juju-inspect, fault-injection, juju-cli) do modify what they are pointed
at — fault-injection on purpose — and carry explicit safety rules: scope
to the named model, prefer a scratch model for destructive experiments,
and verify state after every action.

## Installation

Clone this repository and point your agent at the `skills/` directory
(instructions per harness below), or copy the individual skill directories
into the location your harness discovers.

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

OpenCode discovers skills in `.opencode/skill/` (project) or
`~/.config/opencode/skill/` (global), one directory per skill.

```bash
mkdir -p ~/.config/opencode/skill
cp -r /path/to/mysql-llm-skills/skills/* ~/.config/opencode/skill/
# or, project-local:
mkdir -p .opencode/skill
cp -r /path/to/mysql-llm-skills/skills/* .opencode/skill/
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

- "I have a pile of logs / a crashdump / an issue report and no access to
  the system" -> `charmed-mysql-log-autopsy`
- "A nightly run / PR check failed, what happened and is it real" ->
  `charmed-mysql-nightly-triage`
- "My deployment is blocked/waiting/degraded, help me look at it" ->
  `charmed-mysql-juju-inspect`
- "Reproduce this bug / simulate quorum loss / test the recovery path" ->
  `charmed-mysql-fault-injection`
- "How do I run this juju command correctly / why did juju behave that
  way" -> `juju-cli`

The skills are independent; combining them is common (e.g. triage a CI
failure with `charmed-mysql-nightly-triage`, then go deep with
`charmed-mysql-log-autopsy`).

## Repository layout

- `skills/` — the skill directories (above).
- `profe/` — the evaluation harness: `validate` checks skills against the
  agentskills specification, `probe` measures skill triggering by running
  queries through headless pi sessions, `summarize` aggregates pass rates,
  misroutes and per-query detail. See `profe/README.md`.
- `eval/` — the curated query corpus (per-skill train/validation splits,
  near-misses), the recorded probe runs with their reports, and
  `RESULTS.md` (Phase 1 concluded: all five skills trigger correctly;
  known boundary false positives recorded). `EVAL-PROCESS.md` documents
  the methodology.

## Development

Validate skills with the harness:

```bash
cd profe
uv run profe validate --skills $(find ../skills -mindepth 1 -maxdepth 1 -type d)
```

Checks: frontmatter correctness, name rules (lowercase, hyphens, 64 chars
max, matches the directory), description under 1024 characters.

The body budget is guidance the tool does not enforce: keep SKILL.md
bodies within ~150 lines / ~6k tokens (skill-forge's budget, adopted in
`SKILL_CREATION_PLAN.md` so every skill loads in full even on a small
model) and push detail into `references/`, loaded on demand.

When editing skills, keep in mind:

- Descriptions carry the entire triggering burden — they are the only
  thing the agent sees before deciding to load a skill. Phrase them as
  "use when..." and cover both explicit and implicit phrasings of the
  task.
- After a description edit, re-run the skill's train set through the
  harness (`profe probe`) and only then its validation split;
  `eval/RESULTS.md` records the process and the current numbers.
- The bundled script (`charmed-mysql-log-autopsy/scripts/parse_uniter_ops.py`)
  has no external dependencies and is smoke-tested against synthetic logs;
  run `python3 skills/charmed-mysql-log-autopsy/scripts/parse_uniter_ops.py --help`
  after changing it.
