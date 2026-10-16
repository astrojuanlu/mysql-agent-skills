#!/usr/bin/env bash
# PoC 3 — profe inside a disposable LXD VM (SANDBOX-EXPLORATION §12, T4 pattern).
#
# Same contract as PoC 1/2: the caller (this script) decides the boundary;
# profe runs INSIDE it (validate → compile → run), the capture bundle comes
# back to the host, and grading happens OUTSIDE via `profe grade
# `<bundle>`. The VM is destroyed at the end — nothing survives.
#
# VM recipe (T4-validated parts only):
# - cloud-init installs node (nodesource 24.x) and the PINNED pi 0.85.1
#   into /opt/pi via npm — no snaps, so no enterprise-store setup needed;
# - python toolchain is pushed from the host at IDENTICAL absolute paths
#   (the venv's python and skills-ref both resolve into uv-managed
#   cpython-3.14.5; console-script shebangs are absolute) — tar over
#   `lxc exec` handles the spaces in the repo path;
# - pi config rides in as input at /root/.pi/agent with `packages`
#   stripped BEFORE anything runs (run's pi reads it directly);
# - poll a cloud-init marker file, never `cloud-init status --wait`
#   through lxc exec (it hangs — §11 lesson);
# - skills land at their exact host paths so trigger detection and grade
#   metadata match verbatim (T4 invariant), made read-only.
#
# Attempt outputs are pulled BEFORE the VM dies — the capture bundle is
# the only thing that must survive the boundary.

set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"     # profe/scripts -> repo root
PROFE_VENV="$REPO/profe/.venv"
SCRIPTS="$REPO/profe/scripts"
PI_VERSION="0.85.1"
PI_PREFIX="/opt/pi"                            # VM-side pi (npm, pinned)
UV_PY="$HOME/.local/share/uv/python/cpython-3.14.5-linux-x86_64-gnu"
UV_SKILLS_REF="$HOME/.local/share/uv/tools/skills-ref"
SKILLS_REF="$HOME/.local/bin/skills-ref"
PI_CONFIG="$HOME/.pi/agent"
SKILLS="$REPO/skills"
RUBRICS="$REPO/eval/rubrics"
VM=poc-profe
WORK="$REPO/profe/.poc-lxd"
DEADLINE=$(( $(date +%s) + 600 ))

rm -rf "$WORK"
mkdir -p "$WORK/out" "$WORK/pull"

if lxc list --format csv -c n 2>/dev/null | grep -q "^$VM$"; then
  echo "stale $VM found — deleting"; lxc delete --force "$VM"
fi

cat > "$WORK/user-data.yaml" <<'EOF'
#cloud-config
package_update: false
runcmd:
  - |
    LOG=/var/log/poc-provision.log
    : > "$LOG"
    fail() { echo "FAIL $1" >> "$LOG"; echo FAIL > /var/log/poc-provision.done; exit 0; }
    step() { echo "== $1" >> "$LOG"; }
    step node
    apt-get install -y curl ca-certificates >> "$LOG" 2>&1 || fail apt
    curl -fsSL -o /tmp/nodesource.sh https://deb.nodesource.com/setup_24.x \
      >> "$LOG" 2>&1 || fail node-curl
    bash /tmp/nodesource.sh >> "$LOG" 2>&1 || fail node-repo
    apt-get install -y nodejs >> "$LOG" 2>&1 || fail node-install
    step pi
    npm install --prefix /opt/pi --ignore-scripts \
      "@earendil-works/pi-coding-agent@0.85.1" >> "$LOG" 2>&1 || fail pi-install
    /opt/pi/node_modules/.bin/pi --version >> "$LOG" 2>&1 || fail pi-verify
    mkdir -p /root/work /root/staging /root/out
    echo OK > /var/log/poc-provision.done
EOF

echo "== launching VM ($VM) =="
lxc launch ubuntu:24.04 "$VM" --vm \
  -c limits.cpu=2 -c limits.memory=4GiB \
  -c user.user-data="$(cat "$WORK/user-data.yaml")" >/dev/null
trap 'lxc delete --force "'"$VM"'" 2>/dev/null || true' EXIT

echo "== waiting for provisioning (marker poll, loud) =="
STATUS=""
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  STATUS=$(lxc exec "$VM" -- cat /var/log/poc-provision.done 2>/dev/null || true)
  [ -n "$STATUS" ] && break
  sleep 10
done
if [ "$STATUS" != "OK" ]; then
  echo "provisioning did not complete (status: '${STATUS:-timeout}') — log tail:"
  trap - EXIT
  lxc exec "$VM" -- tail -30 /var/log/poc-provision.log 2>/dev/null || true
  exit 1
fi
echo "VM provisioned"

echo "== pushing inputs (path-preserving tar; spaces in paths handled) =="
tar -C / -czf - \
  "home/ubuntu/.local/share/uv/python/cpython-3.14-linux-x86_64-gnu" \
  "home/ubuntu/.local/share/uv/python/cpython-3.14.5-linux-x86_64-gnu" \
  "home/ubuntu/.local/share/uv/tools/skills-ref" \
  "home/ubuntu/.local/bin/skills-ref" \
  "home/ubuntu/Projects/Canonical/Data Platform/Charmed MySQL/mysql-llm-skills/profe/.venv" \
  "home/ubuntu/Projects/Canonical/Data Platform/Charmed MySQL/mysql-llm-skills/profe/src" \
  "home/ubuntu/Projects/Canonical/Data Platform/Charmed MySQL/mysql-llm-skills/profe/packages/pi-agent-json/src" \
  "home/ubuntu/Projects/Canonical/Data Platform/Charmed MySQL/mysql-llm-skills/profe/scripts" \
  "home/ubuntu/Projects/Canonical/Data Platform/Charmed MySQL/mysql-llm-skills/skills" \
  "home/ubuntu/Projects/Canonical/Data Platform/Charmed MySQL/mysql-llm-skills/eval/rubrics" \
  | lxc exec "$VM" -- tar -C / -xzf -
lxc exec "$VM" -- chmod -R a-w "$SKILLS" "$RUBRICS"

# pi config: pushed with `packages` stripped BEFORE anything runs.
for f in auth.json models.json models-store.json settings.json; do
  [ -f "$PI_CONFIG/$f" ] && lxc file push "$PI_CONFIG/$f" "$VM/root/.pi/agent/$f" --create-dirs
done
lxc file pull "$VM/root/.pi/agent/settings.json" "$WORK/settings.json"
WORK="$WORK" python3 - <<'PY'
import json, os
p = os.path.join(os.environ["WORK"], "settings.json")
d = json.load(open(p))
d.pop("packages", None)
json.dump(d, open(p, "w"), indent=1)
PY
lxc file push "$WORK/settings.json" "$VM/root/.pi/agent/settings.json"

echo "== profe inside the VM: validate → compile → run =="
VM_PROFE="$PROFE_VENV/bin/profe"
VM_PY="$PROFE_VENV/bin/python"
VM_SCRIPTS="$SCRIPTS"
VM_SKILLS="$SKILLS"
INNER_ENV=(env HOME=/root PATH="$PI_PREFIX/node_modules/.bin:$HOME/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")

lxc exec "$VM" -- "${INNER_ENV[@]}" "$VM_PROFE" validate --skills "$VM_SKILLS"/*/
lxc exec "$VM" -- "${INNER_ENV[@]}" "$VM_PROFE" compile \
  --skills "$VM_SKILLS"/*/ \
  --queries "$VM_SCRIPTS/poc-queries.json" \
  --tasks /root/out/tasks-trigger.jsonl \
  --rubrics-dir /root/out/rubrics
TRIG_TASK=$(lxc exec "$VM" -- "${INNER_ENV[@]}" "$VM_PY" -c "
import json
rows = [json.loads(line) for line in open('/root/out/tasks-trigger.jsonl')]
print(next(r['id'] for r in rows if r['prompt'].startswith('Test Observer')))")
lxc exec "$VM" -- "${INNER_ENV[@]}" "$VM_PROFE" run \
  --task-id "$TRIG_TASK" \
  --tasks /root/out/tasks-trigger.jsonl \
  --skills "$VM_SKILLS"/*/ \
  --stop-on skill-read --max-tool-calls 12 \
  --out /root/out/bundle-trig \
  --timeout 300 \
  --pi "$PI_PREFIX/node_modules/.bin/pi" \
  --extra-metadata env=lxd-vm:ubuntu-24.04:trigger-cell
lxc exec "$VM" -- "${INNER_ENV[@]}" "$VM_PROFE" run \
  --task-id poc-smoke \
  --tasks "$VM_SCRIPTS/poc-smoke-tasks.jsonl" \
  --artifacts-dir "$VM_SCRIPTS/poc-artifacts" \
  --out /root/out/bundle \
  --timeout 300 \
  --pi "$PI_PREFIX/node_modules/.bin/pi" \
  --extra-metadata env=lxd-vm:ubuntu-24.04

echo "== pulling the evidence out (before the boundary dies) =="
lxc file pull -r "$VM/root/out/bundle" "$WORK/pull/"
lxc file pull -r "$VM/root/out/bundle-trig" "$WORK/pull/"
lxc file pull "$VM/root/out/tasks-trigger.jsonl" "$WORK/pull/"
lxc file pull -r "$VM/root/out/rubrics" "$WORK/pull/"

echo "== destroying the boundary =="
trap - EXIT
lxc delete --force "$VM"

echo
echo "== host side: grade the bundles that came out of the VM =="
"$PROFE_VENV/bin/profe" grade "$WORK/pull/bundle-trig" \
  --tasks "$WORK/pull/tasks-trigger.jsonl" \
  --rubrics "$WORK/pull/rubrics" \
  --out "$WORK/out/graded-trig.jsonl"
"$PROFE_VENV/bin/profe" grade "$WORK/pull/bundle" \
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
echo "PoC 3 (LXD VM) OK — artifacts in $WORK/out and $WORK/pull"
