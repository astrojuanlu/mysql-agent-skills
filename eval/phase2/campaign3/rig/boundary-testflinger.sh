#!/usr/bin/env bash
# boundary-testflinger.sh — the c3 TESTFLINGER BOUNDARY for driver v3
# (retro B3: boundary-specific logic lives ONLY here; pairs, coins, caps,
# event log, resume are the python core's job). Every op is one callable
# command wrapping the twin-rehearsal-validated mechanics verbatim —
# run-pair.sh (2026-10-16/17 rehearsals) is the frozen reference this
# file was extracted from; do not "simplify" the ssh/rsync handling.
#
# Ops:
#   submit QUEUE            -> job uuid on stdout (non-zero rc on failure)
#   device-ip JOB           -> device_ip or "" (ONE poll; the driver loops)
#   keygen-r IP             -> clear a stale host key (persistent-deploy
#                              reimages rotate keys every lease; §3.2)
#   rig-ready IP            -> rc 0 if ~/rig-ready visible over ssh
#   cancel JOB              -> idempotent release
#   launch IP RUNID ARM ATTEMPT [MANIFEST]
#                           -> stage payload + start attempt (launch-payload.sh)
#   sample IP RUNID ARM     -> "calls in out cache total cost stream_age alive"
#                              or the literal SSHFAIL
#   kill-run IP RUNID ARM   -> TERM the profe process group
#   runner-status IP RUNID ARM -> the marker file's contents ("" if absent)
#   dump-gt QUEUE IP        -> ground-truth/egress dump into $STATE_DIR
#   recover IP JOB [force]  -> recovered dir path on stdout (last line),
#                              human log on stderr; releases the machine
#   agent-state QUEUE       -> the queue's agent state ("waiting"/"busy"/…)
#   job-failed JOB          -> rc 0 iff the job terminally FAILED at the
#                              lab (results: job_state=complete AND a
#                              nonzero provision/test/cleanup status);
#                              rc 1 otherwise (running/reserve/unknown).
#                              Added 2026-10-18 (manifest v4 amendment,
#                              operator GO): a failed MAAS deploy still
#                              reports device_info.device_ip, so the
#                              driver granted an ip on a released node
#                              and burned the full 45-min rig-wait —
#                              the terminal signal was in the same
#                              results payload all along (pair 1/A).
#
# STATE_DIR (for dump-gt) comes from the environment; driver v3 exports it.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSHOPT="-o StrictHostKeyChecking=accept-new"
UUID_RE='[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'

op="${1:?op required}"; shift
case "$op" in
  submit)
    q="$1"
    jf="$STATE_DIR/job-$q.yaml"
    sed "s/__TWIN_QUEUE__/$q/" "$DIR/pi-rig-template.yaml" > "$jf"
    out=$(testflinger-cli submit "$jf") || { echo "SUBMIT FAILED ($q)" >&2; exit 1; }
    id=$(printf '%s\n' "$out" | grep -oE "$UUID_RE" | head -1)
    # the CLI exits 0 even on client-side errors (e.g. snap /tmp), so the
    # UUID is the only trustworthy success signal
    [ -n "$id" ] || { echo "SUBMIT NO-UUID ($q): $out" >&2; exit 1; }
    echo "$id"
    ;;
  device-ip)
    timeout 30 testflinger-cli results "$1" 2>/dev/null | python3 -c "
import json, sys
try: print((json.load(sys.stdin).get('device_info') or {}).get('device_ip', ''))
except Exception: print('')" || echo ""
    ;;
  job-failed)
    timeout 30 testflinger-cli results "$1" 2>/dev/null | python3 -c "
import json, sys
try:
    r = json.load(sys.stdin)
    failed = r.get('job_state') == 'complete' and any(
        int(r.get(k) or 0) != 0
        for k in ('provision_status', 'test_status', 'cleanup_status')
    )
except Exception:
    failed = False  # unreadable results = unknown = NOT failed
sys.exit(0 if failed else 1)
" || exit 1
    ;;
  keygen-r)
    ssh-keygen -R "$1" >/dev/null 2>&1 || true
    ;;
  rig-ready)
    ssh -o ConnectTimeout=10 $SSHOPT "ubuntu@$1" 'test -f ~/rig-ready' 2>/dev/null
    ;;
  cancel)
    testflinger-cli cancel "$1" >/dev/null 2>&1 || true
    ;;
  launch)
    ip="$1" runid="$2" arm="$3" attempt="$4" manifest="${5:-}"
    task_file="${6:-}" task_id="${7:-}"
    (cd "$DIR" && bash launch-payload.sh "$ip" "$runid" "$arm" \
      "$attempt" "$manifest" "$task_file" "$task_id")
    ;;
  hostname)
    # the DUT's hostname == canonical machine name (MAAS names hosts after
    # the machine) — pool mode records it per attempt (§3.7 v2)
    ssh -o ConnectTimeout=15 $SSHOPT "ubuntu@$1" 'hostname' 2>/dev/null || true
    ;;
  sample)
    ssh -o ConnectTimeout=15 $SSHOPT "ubuntu@$1" \
      "python3 ~/payload/sample-session.py ~/payload/runs/pair-$2/$3" 2>/dev/null \
      || echo "SSHFAIL"
    ;;
  kill-run)
    ssh -o ConnectTimeout=15 $SSHOPT "ubuntu@$1" \
      "kill -TERM -- -\$(cat ~/payload/runs/pair-$2/$3/profe.pid) 2>/dev/null" || true
    ;;
  runner-status)
    ssh -o ConnectTimeout=15 $SSHOPT "ubuntu@$1" \
      "cat ~/payload/runs/pair-$2/$3/runner-status 2>/dev/null" || true
    ;;
  dump-gt)
    STATE_DIR="${STATE_DIR:-$DIR/state}"
    ssh -o ConnectTimeout=15 $SSHOPT "ubuntu@$2" \
      'hostname; nproc; free -h | head -2; lscpu | grep -E "^(Model name|Architecture)";
       curl -s -o /dev/null -w "egress openrouter.ai: %{http_code}\n" https://openrouter.ai/api/v1/models;
       curl -s -o /dev/null -w "egress api.charmhub.io: %{http_code}\n" https://api.charmhub.io;
       curl -s -o /dev/null -w "egress pypi.org: %{http_code}\n" https://pypi.org/simple/' \
      > "$STATE_DIR/ground-truth-$1.txt" 2>/dev/null || true
    ;;
  recover)
    ip="$1" job="$2" force="${3:-}"
    out=$( (cd "$DIR" && bash recover-bundle.sh "$ip" "$job" $force 2>&1) || true )
    # human lines to stderr, the path on stdout (twin-pair-1 lesson: the
    # captured stdout must be exactly the return value)
    path=$(printf '%s\n' "$out" | sed -n 's/.*recovered to: //p' | head -1)
    printf '%s\n' "$out" | grep -E "credential-scan|CREDENTIAL MATCH" >&2 || true
    [ -n "$path" ] || { echo "RECOVER FAILED (job $job)" >&2; exit 1; }
    echo "$path"
    ;;
  agent-state)
    # §3.7 gate: the machine's own queue must show the agent waiting
    # before the driver submits. Parse the queue-status JSON or table;
    # anything unparseable prints "" (the driver decides policy).
    out=$(timeout 30 testflinger-cli queue-status "$1" 2>/dev/null || true)
    printf '%s' "$out" | python3 -c "
import json, re, sys
raw = sys.stdin.read().strip()
if not raw: print(''); raise SystemExit
try:
    d = json.loads(raw)
    agents = d if isinstance(d, list) else d.get('agents', [d])
    states = [a.get('state', '') for a in agents if isinstance(a, dict)]
    print(states[0] if len(states) == 1 else ','.join(s or 'unknown' for s in states) or 'unknown')
except Exception:
    m = re.search(r'^Available:\s*(\d+)', raw, re.M)
    if m:
        print('waiting' if int(m.group(1)) > 0 else 'busy')
    elif re.search(r'^Busy:\s*[1-9]', raw, re.M):
        print('busy')
    else:
        print('unknown')
"
    ;;
  *)
    echo "unknown op: $op" >&2; exit 2;;
esac