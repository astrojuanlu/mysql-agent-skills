#!/usr/bin/env bash
# PoC 1 — profe inside bubblewrap (SANDBOX-EXPLORATION §12).
#
# The caller (this script) decides the boundary; profe runs INSIDE it and
# emits capture bundles; grading happens OUTSIDE via `profe grade
# `<bundle>`. Boundary = mount view only (filesystem isolation for simple
# evals); the network stays open (the T3 §4-Stage-2 netns+whitelist rig
# composes on top and is intentionally out of scope here).
#
# Mount-recipe facts all validated in T3 (SANDBOX-EXPLORATION §2):
# - merged-usr root: --ro-bind /usr + the usr/{bin,lib,sbin,lib64} symlinks
#   (without them every exec dies: kernel resolves /lib64/ld-linux);
# - /sys NOT needed (that was the snap-pi era); /dev,/proc,/tmp are fresh;
# - python venvs and npm prefixes only work when bound at their EXACT
#   host paths (console-script shebangs are absolute);
# - the scratch HOME is writable; pi config is pre-staged into it with
#   the `packages` key stripped (profe run strips again defensively).
#
# Everything the attempt needs is bound read-only at identical paths;
# the only writable spots are the scratch HOME and the output dir.

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
WORK="$REPO/profe/.poc-bwrap"

rm -rf "$WORK"
mkdir -p "$WORK/home" "$WORK/out"

# Scratch HOME: pi config rides in as input (never via the ambient HOME),
# with `packages` stripped — pi auto-installs that list at startup, a live
# npm call that breaks restricted boundaries and drifts between attempts.
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

# The runner's inner script: profe's three faces inside the boundary.
cat > "$WORK/inner.sh" <<'INNER'
#!/bin/bash
set -euo pipefail
PROFE="$PROFE_VENV/bin/profe"
SKILLS="$REPO/skills"
SCRIPTS="$REPO/profe/scripts"
OUT=/poc/out

echo "== profe validate (structure check inside the boundary) =="
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
  --pi "$PI_BIN" --extra-metadata env=bwrap:trigger-cell

echo "== profe run (task attempt -> capture bundle) =="
"$PROFE" run --task-id poc-smoke --tasks "$SCRIPTS/poc-smoke-tasks.jsonl" \
  --artifacts-dir "$SCRIPTS/poc-artifacts" --out "$OUT/bundle" --timeout 300 \
  --pi "$PI_BIN" --extra-metadata env=bwrap
echo "== bundle contents =="
ls -la "$OUT" "$OUT/bundle" "$OUT/bundle-trig"
INNER
chmod +x "$WORK/inner.sh"

bwrap \
  --ro-bind /usr /usr \
  --symlink usr/bin /bin \
  --symlink usr/lib /lib \
  --symlink usr/sbin /sbin \
  --symlink usr/lib64 /lib64 \
  --ro-bind /etc /etc \
  --ro-bind /run /run \
  --dev /dev \
  --proc /proc \
  --tmpfs /tmp \
  --ro-bind "$PROFE_VENV" "$PROFE_VENV" \
  --ro-bind "$REPO/profe/src" "$REPO/profe/src" \
  --ro-bind "$REPO/profe/packages/pi-agent-json/src" \
    "$REPO/profe/packages/pi-agent-json/src" \
  --ro-bind "$UV_ROOT" "$UV_ROOT" \
  --ro-bind "$SKILLS_REF" "$SKILLS_REF" \
  --ro-bind "$PI_PREFIX" "$PI_PREFIX" \
  --ro-bind "$NVM_BIN" "$NVM_BIN" \
  --ro-bind "$PI_CONFIG" "$PI_CONFIG" \
  --ro-bind "$SKILLS" "$SKILLS" \
  --ro-bind "$SCRIPTS" "$SCRIPTS" \
  --ro-bind "$RUBRICS" "$RUBRICS" \
  --bind "$WORK/home" /home/poc \
  --bind "$WORK" /poc \
  --clearenv \
  --setenv HOME /home/poc \
  --setenv PATH "$PI_PREFIX/node_modules/.bin:$NVM_BIN:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin" \
  --setenv TERM "${TERM:-xterm}" \
  --setenv PROFE_VENV "$PROFE_VENV" \
  --setenv REPO "$REPO" \
  --setenv PI_BIN "$PI_BIN" \
  bash /poc/inner.sh

echo
echo "== host side: grade the bundles that came out of the boundary =="
"$PROFE_VENV/bin/profe" grade "$WORK/out/bundle-trig" \
  --tasks "$WORK/out/tasks-trigger.jsonl" \
  --rubrics "$WORK/out/rubrics" \
  --out "$WORK/out/graded-trig.jsonl"
"$PROFE_VENV/bin/profe" grade "$WORK/out/bundle" \
  --tasks "$SCRIPTS/poc-smoke-tasks.jsonl" \
  --rubrics "$RUBRICS" \
  --artifacts-dir "$SCRIPTS/poc-artifacts" \
  --out "$WORK/out/graded.jsonl"
echo
echo "== graded trigger record (from the bundle, never from inside) =="
python3 -c "
import json, sys
r = json.loads(open(sys.argv[1]).readlines()[-1])
print('task:', r['task_id'], '| clean:', r['clean'], '| early_exit:', r.get('early_exit'),
      '| expected:', r.get('expected_skills'), '| triggered:', r['triggered'],
      '| score:', r['rubric_score'], '| pi:', r['pi_version'], '| dur:', r['dur_s'], 's')
" "$WORK/out/graded-trig.jsonl"
echo
echo "== graded task record (from the bundle, never from inside) =="
python3 -c "
import json, sys
r = json.loads(open(sys.argv[1]).readlines()[-1])
print('task:', r['task_id'], '| clean:', r['clean'], '| schema_ok:', r['schema_ok'],
      '| verdict:', r['verdict'], '| pi:', r['pi_version'], '| dur:', r['dur_s'], 's')
" "$WORK/out/graded.jsonl"
echo
echo "PoC 1 (bubblewrap) OK — artifacts in $WORK/out"
