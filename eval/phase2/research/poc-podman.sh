#!/usr/bin/env bash
# PoC 2 — profe inside a rootless podman container (SANDBOX-EXPLORATION §12).
#
# Same contract as PoC 1: the caller (this script) decides the boundary;
# profe runs INSIDE it (validate → compile → run), the capture bundle comes
# out, and grading happens OUTSIDE via `profe grade <bundle>`.
#
# Container recipe (validated here for the first time):
# - NO image build: the cached `ubuntu:24.04` base plus the host's
#   python-3.14 venv, uv-managed interpreters, node and the npm-installed
#   pi bound (read-only) at their EXACT host paths — console-script
#   shebangs are absolute, editable .pth files point at the source trees,
#   so path-preserving mounts make the host venv work unmodified;
# - the scratch HOME is a mounted writable dir (profe run copies pi
#   config into its own attempt scratch; nothing ambient leaks);
# - rootless networking (slirp4netns) gives outbound egress — open by
#   design for simple evals; a whitelist rig would compose inside.
#
# Reuses the PoC-1 facts: mounts with spaces in paths are fine as long
# as no commas appear in the values.

set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"     # profe/scripts -> repo root
PROFE_VENV="$REPO/profe/.venv"
SCRIPTS="$REPO/profe/scripts"
PI_PREFIX="$HOME/.local/profe-t3/pi-0.85.1"     # pinned instrument (pi 0.85.1)
PI_BIN="$PI_PREFIX/node_modules/.bin/pi"        # explicit --pi for the run verb
NVM_BIN="$HOME/.local/share/nvm/v24.21.0/bin"   # node for the npm-installed pi
UV_ROOT="$HOME/.local/share/uv"                 # python 3.14 + skills-ref tool venv
SKILLS_REF="$HOME/.local/bin/skills-ref"
PI_CONFIG="$HOME/.pi/agent"
SKILLS="$REPO/skills"
RUBRICS="$REPO/eval/rubrics"
WORK="$REPO/profe/.poc-podman"

rm -rf "$WORK"
mkdir -p "$WORK/home" "$WORK/out"

# Scratch HOME: pi config rides in as input, `packages` stripped
# (pi auto-installs that list at startup — a live npm call that breaks
# restricted boundaries and drifts between attempts).
mkdir -p "$WORK/home/.pi/agent"
for f in auth.json models.json models-store.json settings.json; do
  [ -f "$PI_CONFIG/$f" ] && cp "$PI_CONFIG/$f" "$WORK/home/.pi/agent/$f"
done
WORK="$WORK" python3 - <<'PY'
import json, os
p = os.path.join(os.environ["WORK"], "home/.pi/agent/settings.json")
if os.path.exists(p):
    d = json.load(open(p))
    d.pop("packages", None)
    json.dump(d, open(p, "w"), indent=1)
PY

# The runner's inner script (identical to PoC 1's: same profe face inside).
cat > "$WORK/inner.sh" <<'INNER'
#!/bin/bash
set -euo pipefail
PROFE="$PROFE_VENV/bin/profe"
SKILLS="$REPO/skills"
SCRIPTS="$REPO/profe/scripts"
OUT=/poc/out

echo "== profe validate (structure check inside the container) =="
"$PROFE" validate --skills "$SKILLS"/*/

echo "== profe compile (queries -> trigger tasks + rubrics; offline) =="
"$PROFE" compile --skills "$SKILLS"/*/ --queries "$SCRIPTS/poc-queries.json" \
  --tasks "$OUT/tasks-trigger.jsonl" --rubrics-dir "$OUT/rubrics"

echo "== profe run (trigger query, yield on first skill read) =="
TRIG_TASK=$("$PROFE_VENV/bin/python" - <<'PY'
import json
rows = [json.loads(line) for line in open("/poc/out/tasks-trigger.jsonl")]
print(next(r["id"] for r in rows if r["prompt"].startswith("Test Observer")))
PY
)
"$PROFE" run --task-id "$TRIG_TASK" --tasks "$OUT/tasks-trigger.jsonl" \
  --skills "$SKILLS"/*/ --stop-on skill-read --max-tool-calls 12 \
  --out "$OUT/bundle-trig" --timeout 300 \
  --pi "$PI_BIN" --extra-metadata env=podman:ubuntu-24.04:trigger-cell

echo "== profe run (task attempt -> capture bundle) =="
"$PROFE" run --task-id poc-smoke --tasks "$SCRIPTS/poc-smoke-tasks.jsonl" \
  --artifacts-dir "$SCRIPTS/poc-artifacts" --out "$OUT/bundle" --timeout 300 \
  --pi "$PI_BIN" --extra-metadata env=podman:ubuntu-24.04
echo "== bundle contents =="
ls -la "$OUT" "$OUT/bundle" "$OUT/bundle-trig"
INNER
chmod +x "$WORK/inner.sh"

mnt() { printf -- "--mount=type=bind,src=%s,dst=%s%s" "$1" "$2" "${3:-}"; }

podman run --rm \
  "$(mnt "$PROFE_VENV" "$PROFE_VENV" ",ro")" \
  "$(mnt "$REPO/profe/src" "$REPO/profe/src" ",ro")" \
  "$(mnt "$REPO/profe/packages/pi-agent-json/src" "$REPO/profe/packages/pi-agent-json/src" ",ro")" \
  "$(mnt "$UV_ROOT" "$UV_ROOT" ",ro")" \
  "$(mnt "$SKILLS_REF" "$SKILLS_REF" ",ro")" \
  "$(mnt "$PI_PREFIX" "$PI_PREFIX" ",ro")" \
  "$(mnt "$NVM_BIN" "$NVM_BIN" ",ro")" \
  "$(mnt "$PI_CONFIG" "$PI_CONFIG" ",ro")" \
  "$(mnt "$SKILLS" "$SKILLS" ",ro")" \
  "$(mnt "$SCRIPTS" "$SCRIPTS" ",ro")" \
  "$(mnt "$RUBRICS" "$RUBRICS" ",ro")" \
  "$(mnt "$WORK" /poc)" \
  "$(mnt "$WORK/home" /home/poc)" \
  --user root \
  --workdir /tmp \
  --env HOME=/home/poc \
  --env PATH="$PI_PREFIX/node_modules/.bin:$NVM_BIN:$HOME/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
  --env TERM="${TERM:-xterm}" \
  --env PROFE_VENV="$PROFE_VENV" \
  --env REPO="$REPO" \
  --env PI_BIN="$PI_BIN" \
  ubuntu:24.04 \
  bash /poc/inner.sh

echo
echo "== host side: grade the bundles that came out of the container =="
"$PROFE_VENV/bin/profe" grade "$WORK/out/bundle-trig" \
  --tasks "$WORK/out/tasks-trigger.jsonl" \
  --rubrics "$WORK/out/rubrics" \
  --out "$WORK/out/graded-trig.jsonl"
"$PROFE_VENV/bin/profe" grade "$WORK/out/bundle" \
  --tasks "$SCRIPTS/poc-smoke-tasks.jsonl" \
  --rubrics "$RUBRICS" \
  --artifacts-dir "$SCRIPTS/poc-artifacts" \
  --skills "$SKILLS"/*/ \
  --out "$WORK/out/graded.jsonl"
echo
python3 -c "
import json, sys
r = json.loads(open(sys.argv[1]).readlines()[-1])
print('task:', r['task_id'], '| clean:', r['clean'], '| early_exit:', r.get('early_exit'),
      '| expected:', r.get('expected_skills'), '| triggered:', r['triggered'],
      '| score:', r['rubric_score'], '| pi:', r['pi_version'], '| dur:', r['dur_s'], 's')
" "$WORK/out/graded-trig.jsonl"
echo
python3 -c "
import json, sys
r = json.loads(open(sys.argv[1]).readlines()[-1])
print('task:', r['task_id'], '| clean:', r['clean'], '| schema_ok:', r['schema_ok'],
      '| verdict:', r['verdict'], '| pi:', r['pi_version'], '| dur:', r['dur_s'], 's')
" "$WORK/out/graded.jsonl"
echo
echo "PoC 2 (podman) OK — artifacts in $WORK/out"
