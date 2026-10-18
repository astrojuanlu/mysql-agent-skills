#!/usr/bin/env bash
# One c3 attempt RUN on the Testflinger DUT — adapted from profe/scripts/
# run-cost-campaign-c2.sh (campaign 2's per-attempt driver). Runs ON the
# DUT (profe spawns pi as a subprocess; pi must sit next to the k8s API).
# c3 shape: the campaign DRIVER lives host-side (§4.7) — this script is the
# on-machine attempt-runner only (guard rails + profe + watchdog + grade +
# record-only forensics); the run sheet, caps and adjudication are the host's job.
#
# Deltas from the c2 original (full kit delta log: rig README.md):
#   1. REPO -> /home/ubuntu/payload (payload mirror; the c2 relative layout
#      is preserved so `cd profe && uv run profe ... ../eval/...` is verbatim)
#   2. --timeout 7200 -> 14400 (c3 ops wall limit §4.4; lease is 6 h now)
#   3. no host-tmp sweep step (fresh machine per attempt — no prior debris;
#   pool mode caveat 2026-10-17: reuse WITHOUT reimage is possible — the
#   rig's hard reset step + the namespace sweep below cover the debris)
#   4. no DUT run-sheet row: the machine ships the raw bundle only
#      (runner-status marker file + profe.pid for the host)
#   5. --extra-metadata campaign=c3 pair arm attempt machine (bundle
#      self-describes its DUT hostname — pool mode §3.7: identity is a
#      recorded covariate, per-bundle, not just a sheet cell)
#   6. sources ~/rig-env.sh (nvm node + uv on PATH; rig wrote it — also
#      exports CAMPAIGN_CONTROLLER; leak-check.py is retired from the c3
#      kit — §3.5 gate never fired, residual-contamination check lives in
#      incidents.py as the git_sources counter)
# Everything else is the c2 driver verbatim: overwrite guard, fresh testing
# model + foreign-model sweep, models-before gate, the 25-min stream-stall
# watchdog, grade, record-only forensics (incidents.py).
#
# Usage: run-attempt.sh <pair> <off|on> [attempt] [manifest_sha8]
#   manifest_sha8 — driver v3 (B2, optional): recorded in the sidecar's
#   extra_metadata as manifest=<sha8>; the host driver refuses any bundle
#   whose label differs from its own manifest. Empty = pre-v3 attempt.
#   Env TASK_FILE/TASK_ID — manifest v2 (operator 2026-10-18): which task
#   the attempt runs (smoke.jsonl/smoke-rig-tour for rehearsals,
#   mr-234.jsonl/mr-234 for the campaign); launch-payload.sh forwards
#   them. Defaults = the campaign task.
set -euo pipefail

PAIR="$1"
ARM="$2"
ATTEMPT="${3:-1}"
MANIFEST="${4:-${MANIFEST_SHA8:-}}"
TASK_FILE="${TASK_FILE:-mr-234.jsonl}"
TASK_ID="${TASK_ID:-mr-234}"
case "$ARM" in off|on) ;; *) echo "arm must be off|on"; exit 1;; esac

[ -f "$HOME/rig-env.sh" ] && . "$HOME/rig-env.sh"

REPO="/home/ubuntu/payload"
PI="$HOME/.local/profe-t3/pi-1.2.0/node_modules/.bin/pi"
MODEL="openrouter/z-ai/glm-5.3-flash:nitro"
OUT="$REPO/runs/pair-$PAIR/$ARM"
# guard: never overwrite an existing bundle in place (c2 incident: a driver
# skip-check bug re-ran a bundle in place and destroyed the original
# stream.jsonl). Archive any leftover dir instead.
if [ -e "$OUT" ]; then
  mv "$OUT" "${OUT}-prev-$(date -u +%Y%m%dT%H%M%SZ)"
fi
mkdir -p "$OUT"

# runner-status is the host's attempt marker (§4.7): the host must never
# have to distinguish "still running" from "runner died" — any early exit
# (crashed grade step, killed runner) leaves `incomplete`, the success
# path overwrites with `done` at the end. Rehearsal pair-2 lesson: a
# grade crash left NO marker and the host polled a dead slot for hours.
mark_incomplete() { echo incomplete > "$OUT/runner-status" 2>/dev/null || true; }
trap 'rc=$?; if [ "$rc" -ne 0 ]; then mark_incomplete; fi; exit "$rc"' EXIT
trap 'mark_incomplete; exit 143' TERM
trap 'mark_incomplete; exit 130' INT

cd "$REPO"

echo "== [$PAIR/$ARM] fresh testing model"
juju destroy-model testing --no-prompt --destroy-storage --force --no-wait 2>/dev/null || true
# pool mode (bug 10): a reused machine can carry an ORPHANED `testing`
# namespace (juju model record gone, k8s namespace left) — add-model would
# collide. Belt-and-braces under the rig's hard reset.
sudo k8s kubectl delete namespace testing --ignore-not-found --force \
  --timeout=60s 2>/dev/null || true
juju add-model testing
# pre-run foreign-model sweep (c2 DEVIATIONS 2026-10-15: a killed attempt
# left a stray model that contaminated the next attempts). Single-tenant
# rig: anything that is not the controller or testing is wreckage.
for m in $(juju models 2>/dev/null | awk 'NR>1 && $1!="Model" && $1!="controller" && $1!="testing" {print $1}'); do
  echo "sweep: destroying foreign model $m"
  juju destroy-model "$m" --no-prompt --destroy-storage --force --no-wait 2>/dev/null || true
done
juju models > "$OUT/juju-models-before.txt"
cat "$OUT/juju-models-before.txt"
grep -q 'testing\*' "$OUT/juju-models-before.txt" \
  || { echo "FATAL: testing is not the current model"; exit 1; }

echo "== [$PAIR/$ARM] profe run (pi 1.2.0, timeout 14400 (4 h ops wall), uncapped — budget caps enforced host-side)"
SKILLS_ARGS=()
if [ "$ARM" = "on" ]; then
  SKILLS_ARGS=(
    --skills
    "$REPO/skills/charmed-mysql-fault-injection"
    "$REPO/skills/charmed-mysql-juju-inspect"
    "$REPO/skills/charmed-mysql-nightly-triage"
    "$REPO/skills/juju-cli"
    "$REPO/skills/mysql-charms-source"
  )
fi
MANIFEST_ARGS=()
[ -n "$MANIFEST" ] && MANIFEST_ARGS=("manifest=$MANIFEST")
( cd profe && exec setsid uv run profe run \
  --tasks "../eval/tasks/$TASK_FILE" --task-id "$TASK_ID" --run "$PAIR" \
  --out "$OUT" --artifacts-dir "$REPO/eval/tasks-artifacts" \
  --model "$MODEL" --pi "$PI" --timeout 14400 \
  --extra-metadata campaign=c3 pair="$PAIR" arm="$ARM" attempt="$ATTEMPT" \
    machine="$(hostname)" \
  "${MANIFEST_ARGS[@]}" \
  "${SKILLS_ARGS[@]}" ) &
PROFE_PID=$!
echo "$PROFE_PID" > "$OUT/profe.pid"   # host-side driver kills this group on cap/wall
# Watchdog (runner guard v3, arm-symmetric, verbatim from c2): a tool call
# that never returns stalls pi silently until the wall cap. Kill the run if
# the stream shows no progress for 25 minutes; true provider hangs bounded.
(
  while kill -0 "$PROFE_PID" 2>/dev/null; do
    sleep 60
    if [ -f "$OUT/stream.jsonl" ] && \
       [ -n "$(find "$OUT/stream.jsonl" -mmin +25 2>/dev/null)" ]; then
      echo "WATCHDOG: no stream progress for 25m — killing stalled run"
      kill -TERM -- "-$PROFE_PID" 2>/dev/null || \
        kill "$PROFE_PID" 2>/dev/null || true
      exit 0
    fi
  done
) &
wait "$PROFE_PID" || true

if [ ! -f "$OUT/session.jsonl" ]; then
  echo incomplete > "$OUT/runner-status"
  echo "RUN INCOMPLETE (stall-killed or profe failure) — skipping grade; " \
    && echo "quarantine the bundle and rerun the arm" && exit 3
fi

echo "== [$PAIR/$ARM] grade"
(cd profe && uv run profe grade "$OUT" \
  --tasks "../eval/tasks/$TASK_FILE" \
  --artifacts-dir ../eval/tasks-artifacts \
  --rubrics ../eval/rubrics \
  --out "$OUT/graded.jsonl")

echo "== [$PAIR/$ARM] post-run forensics (c3 §3.5/§4.6: record-only — nothing gates)"
juju models > "$OUT/juju-models-after.txt"
python3 profe/scripts/incidents.py "$OUT" || echo "  (incidents recorder failed — bundle kept)"
# hygiene: destroy leftover models so the machine is clean for teardown.
# Recorded, never gating (§4.6: agent behavior stays, ITT).
EXTRA_MODELS=$(awk 'NR>2 && NF {print $1}' "$OUT/juju-models-after.txt" \
  | grep -vE '^(Model|controller|testing)' || true)
if [ -n "$EXTRA_MODELS" ]; then
  echo "leftover models (record + hygiene-destroy): $EXTRA_MODELS"
  printf 'leftover_models: %s\n' "$EXTRA_MODELS" > "$OUT/leftover-models.txt"
  for m in $EXTRA_MODELS; do
    m="${m%\*}"
    juju destroy-model "$m" --no-prompt --destroy-storage --force \
      >/dev/null 2>&1 || true
  done
fi

echo "== [$PAIR/$ARM] run-sheet row (host-side; this machine ships the raw bundle only)"
echo done > "$OUT/runner-status"
echo "== [$PAIR/$ARM] DONE"
