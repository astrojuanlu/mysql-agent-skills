#!/usr/bin/env bash
# Host-side recovery: pull the attempt bundle + supplementary logs back from
# the DUT, scan the returned bundle for BOTH credentials that ride the cell
# (the openrouter key from auth.json AND the literal apiKey inside the
# verbatim models.json — must be CLEAN), scrub the key files on the DUT,
# then release the machine early via testflinger cancel.
#
# Usage: recover-bundle.sh <DEVICE_IP> <JOB_ID> [force]
#   force — kill the tmux session first (attempt still running near lease
#           end); the stream tee means killed sessions keep their evidence.
set -euo pipefail

IP="$1"; JOB="$2"; FORCE="${3:-}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # .../eval/phase2/campaign3/rig
SRC="$(cd "$DIR/../../../.." && pwd)"                    # repo root (for the re-grade fallback note)
# DEST honors STATE_DIR (the driver points it at its own state dir);
# without it (bare ops) the rig default state dir is used.
DEST="${STATE_DIR:-$DIR/state}/recovered-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$DEST"

if [ -n "$FORCE" ]; then
  echo "== force: killing the tmux session (stream.jsonl keeps the evidence)"
  ssh "ubuntu@$IP" 'tmux kill-session -t c3-attempt 2>/dev/null || true'
  sleep 2
fi

echo "== [1/5] supplementary logs on the DUT (before release)"
ssh "ubuntu@$IP" 'bash -s' <<'LOGS'
set -x
cd ~/payload/runs
juju debug-log --replay > controller-debug-log.txt 2>&1 || true
juju debug-log -m testing --replay > testing-debug-log.txt 2>&1 || true
sudo k8s kubectl get events -A --sort-by=.lastTimestamp > k8s-events.txt 2>&1 || true
sudo k8s kubectl get pods -A -o wide > k8s-pods.txt 2>&1 || true
: > pod-logs.txt
for p in $(sudo k8s kubectl -n testing get pods -o name 2>/dev/null); do
  echo "===== $p" >> pod-logs.txt
  sudo k8s kubectl -n testing logs "$p" --tail=200 >> pod-logs.txt 2>&1 || true
done
{ free -h; nproc; lscpu | grep "Model name"; } > hardware.txt 2>&1
LOGS

echo "== [2/5] rsync bundle + run-sheet + logs back"
rsync -a "ubuntu@$IP:~/payload/runs/" "$DEST/runs/"

echo "== [3/5] credential scan of the returned bundle (expect CLEAN)"
python3 - "$DEST" <<'PY'
import json, sys
from pathlib import Path

dest = Path(sys.argv[1])
secrets = []  # (label, string)

def collect_from_auth(v):
    # auth.json holds only the provider key: take every long string
    if isinstance(v, str) and len(v) >= 20:
        secrets.append(("auth.json", v))
    elif isinstance(v, dict):
        for x in v.values(): collect_from_auth(x)
    elif isinstance(v, list):
        for x in v: collect_from_auth(x)

def collect_from_models(v, keyname=""):
    # models.json (verbatim copy): only values under credential-ish keys
    if isinstance(v, dict):
        for k, x in v.items():
            if isinstance(x, str) and len(x) >= 20 and any(
                    t in k.lower() for t in ("key", "token", "secret", "password")):
                secrets.append(("models.json:" + k, x))
            else:
                collect_from_models(x, k)
    elif isinstance(v, list):
        for x in v: collect_from_models(x, keyname)

collect_from_auth(json.load(open(Path.home() / ".pi/agent/auth.json")))
collect_from_models(json.load(open(Path.home() / ".pi/agent/models.json")))

hits = 0
for f in sorted(dest.rglob("*")):
    if f.is_file() and f.stat().st_size < 50_000_000:
        data = f.read_bytes()
        for label, s in secrets:
            if s.encode() in data:
                hits += 1
                print(f"CREDENTIAL MATCH ({label}) in: {f}")
print("credential-scan:", "CLEAN" if hits == 0 else f"{hits} MATCH(ES) — scrub before any sharing")
PY

echo "== [4/5] scrub credentials on the DUT"
ssh "ubuntu@$IP" 'rm -f ~/.pi/agent/auth.json ~/.pi/agent/models.json'

echo "== [5/5] release the machine early (ephemeral: wiped on release)"
testflinger-cli cancel "$JOB"
echo "recovered to: $DEST"
echo "note: if the DUT died before grading, re-grade on the host from the recovered bundle:"
echo "  (cd \"$SRC/profe\" && uv run profe grade \"$DEST/runs/<pair>/<arm>\" \\"
echo "    --tasks ../eval/phase2/campaign3/tasks/mr-234.jsonl --artifacts-dir ../eval/phase2/campaign3/tasks-artifacts \\"
echo "    --rubrics ../eval/phase2/campaign3/rubrics --out \"$DEST/runs/<pair>/<arm>/graded.jsonl\")"
