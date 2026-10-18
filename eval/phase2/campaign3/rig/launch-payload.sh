#!/usr/bin/env bash
# Host-side launcher: stage the payload + pi config on the DUT, then start
# the attempt in a detached tmux session. Run AFTER the rig build completes
# (~/rig-ready marker visible over SSH) while the job is in its reserve
# phase. The provider key rides ONLY this SSH channel (as
# ~/.pi/agent/auth.json) — never the job yaml, never the job log.
#
# Usage: launch-payload.sh <DEVICE_IP> <pair> <off|on> [attempt] [manifest_sha8]
#   [task_file] [task_id]
#   manifest_sha8 — driver v3 (B2): rides into the sidecar via
#   run-attempt.sh's --extra-metadata manifest=... so every attempt
#   records which pre-registration it ran under.
#   task_file/task_id — manifest v2: which task the attempt runs
#   (smoke.jsonl / smoke-rig-tour for rehearsals; mr-234 for the
#   campaign). Defaults keep the script usable standalone.
set -euo pipefail

IP="$1"; PAIR="$2"; ARM="$3"; ATTEMPT="${4:-1}"; MANIFEST="${5:-}"
TASK_FILE="${6:-mr-234.jsonl}"; TASK_ID="${7:-mr-234}"
# This kit lives in the campaign tree: eval/phase2/campaign3/rig/
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$(cd "$DIR/../../../.." && pwd)"   # repo root (source of truth for payload + skills)

# Persistent leases reimage the DUT -> its host key rotates every attempt.
SSHOPT="-o StrictHostKeyChecking=accept-new"
SSH_RSYNC="ssh -o StrictHostKeyChecking=accept-new"   # for rsync -e

echo "== [1/6] payload mirror -> DUT (c2 relative layout preserved)"
ssh $SSHOPT "ubuntu@$IP" 'mkdir -p ~/payload/profe ~/payload/profe/scripts ~/payload/eval/tasks ~/payload/eval/rubrics ~/payload/eval/tasks-artifacts ~/payload/skills ~/payload/runs'
rsync -a -e "$SSH_RSYNC" "$SRC/profe/src/"      "ubuntu@$IP:~/payload/profe/src/"
rsync -a -e "$SSH_RSYNC" "$SRC/profe/packages/" "ubuntu@$IP:~/payload/profe/packages/"
rsync -a -e "$SSH_RSYNC" "$SRC/profe/pyproject.toml" "$SRC/profe/uv.lock" "$SRC/profe/README.md" "ubuntu@$IP:~/payload/profe/"
# leak-check.py is retired from the c3 kit (operator, 2026-10-16): the gate
# never fired on this campaign and §3.5's residual-contamination check is now
# the git_sources counter inside incidents.py (record-only). If a c2-
# comparability run ever needs it, stage it by hand from the dev kit or the
# c2 kit — the c2 runner keeps its own copy.
# record-only forensics recorder (c3 §3.5/§4.6) — the runner's post-run step
rsync -a -e "$SSH_RSYNC" "$DIR/incidents.py" "ubuntu@$IP:~/payload/profe/scripts/incidents.py"
rsync -a -e "$SSH_RSYNC" "$SRC/profe/scripts/" "ubuntu@$IP:~/payload/profe/scripts/"
# stage ALL tasks + rubrics (small files; the attempt picks by task id —
# manifest v2: smoke for rehearsals, mr-234 for the campaign)
rsync -a -e "$SSH_RSYNC" "$DIR/../tasks/" "ubuntu@$IP:~/payload/eval/tasks/"
rsync -a -e "$SSH_RSYNC" "$DIR/../rubrics/" "ubuntu@$IP:~/payload/eval/rubrics/"
rsync -a -e "$SSH_RSYNC" "$DIR/../tasks-artifacts/" "ubuntu@$IP:~/payload/eval/tasks-artifacts/"
# §3.2.3: the skills tree stages on the ON arm ONLY — an OFF machine carries
# no skills in its workspace at all.
if [ "$ARM" = "on" ]; then
  for s in charmed-mysql-fault-injection charmed-mysql-juju-inspect \
           charmed-mysql-nightly-triage juju-cli mysql-charms-source; do
    rsync -a -e "$SSH_RSYNC" "$SRC/skills/$s/" "ubuntu@$IP:~/payload/skills/$s/"
  done
else
  echo "   (OFF arm: no skills tree staged)"
fi
# live-usage sampler the host-side driver polls for the §4.4 budget caps
rsync -a -e "$SSH_RSYNC" "$DIR/sample-session.py" "ubuntu@$IP:~/payload/"
rsync -a -e "$SSH_RSYNC" "$DIR/run-attempt.sh" "ubuntu@$IP:~/payload/"

echo "== [2/6] pi config (verbatim: auth.json models.json settings.json)"
ssh $SSHOPT "ubuntu@$IP" 'mkdir -p ~/.pi/agent'
rsync -a -e "$SSH_RSYNC" "$HOME/.pi/agent/auth.json" "$HOME/.pi/agent/models.json" \
       "$HOME/.pi/agent/settings.json" "ubuntu@$IP:.pi/agent/"

echo "== [3/6] uv sync --frozen (pypi egress fast-fail before the attempt)"
ssh $SSHOPT "ubuntu@$IP" '. ~/rig-env.sh && cd ~/payload/profe && uv sync --frozen && uv run profe --help >/dev/null && echo profe-ok'

echo "== [4/6] launch attempt in detached tmux (survives SSH drops)"
MANIFEST_ENV=""
[ -n "$MANIFEST" ] && MANIFEST_ENV="MANIFEST_SHA8=$MANIFEST"
ssh $SSHOPT "ubuntu@$IP" "tmux new-session -d -s c3-attempt \
  '$MANIFEST_ENV TASK_FILE=$TASK_FILE TASK_ID=$TASK_ID \
  bash ~/payload/run-attempt.sh $PAIR $ARM $ATTEMPT 2>&1 | \
  tee ~/payload/runs/attempt-$PAIR-$ARM.log'"

echo "== [5/6] verify launch"
sleep 3
ssh $SSHOPT "ubuntu@$IP" 'tmux ls && ls -la ~/payload/runs/'

echo "== [6/6] watch points"
cat <<EOF
  live stream : ssh $SSHOPT ubuntu@$IP 'tail -f ~/payload/runs/pair-$PAIR/$ARM/stream.jsonl'
  driver log  : ssh $SSHOPT ubuntu@$IP 'tail -f ~/payload/runs/attempt-$PAIR-$ARM.log'
  tmux attach : ssh -t $SSHOPT ubuntu@$IP tmux attach -t c3-attempt
  juju        : ssh $SSHOPT ubuntu@$IP 'juju status -m testing'
EOF
