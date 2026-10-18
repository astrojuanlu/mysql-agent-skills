#!/usr/bin/env bash
# RETIRED as a driver (2026-10-18 PROFE-DRIVE cutover): superseded by
# rig/campaign.py over profe.driver — driver-v3.py and this script's role
# as the runnable driver are gone. KEPT as the c2-era two-machine reference
# (per-machine queues, §3.7 as originally written) and single-machine ops
# fallback; its original header is preserved below. Do not run it as the
# c3 driver.
# --- original header ---
# Twin-pair driver, HOST side — rehearsal of the c3 driver-v3 mechanics
# (DESIGN §3.1, §3.7, §4.4, §4.7). One pair = two simultaneous jobs on two
# spec-matched machines; a seeded coin (seed = 20261030 + pair#) assigns
# the skills-ON slot (P7); the §4.4 budget caps are enforced from live
# session reads; recovery scans both bundles; both jobs are cancelled at
# the end so the machines return to the pool.
#
# Usage: run-pair.sh <pair#> <queueA> <queueB>
# State (untracked, under state/): run-sheet.csv + events.jsonl, generated
# job yamls, ground-truth dumps, recovered bundles.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PAIR="$1"; QA="$2"; QB="$3"
RUNID="$PAIR"                      # profe --run needs an int (901 lesson)
SEED=$((20261030 + PAIR))
STATE="$DIR/state"; mkdir -p "$STATE"
SHEET="$STATE/run-sheet.csv"
EVENTS="$STATE/events.jsonl"
SSHOPT="-o StrictHostKeyChecking=accept-new"
UUID_RE='[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'

# §4.4 caps: cost >= $0.30 OR total tokens >= 15M OR calls >= 250
CAP_COST=0.30; CAP_TOKENS=15000000; CAP_CALLS=250
WALL_S=14400        # 4 h ops wall limit
ZACT_S=3600         # 60 min zero session activity => infra
SSHFAIL_MAX=8       # consecutive ssh failures before declaring infra

ev() { # ev <event> <slot> <detail>  — append-only event log (driver-v3 shape)
  printf '{"ts":"%s","pair":%s,"event":"%s","slot":"%s","detail":"%s"}\n' \
    "$(date -u +%FT%TZ)" "$PAIR" "$1" "$2" "$3" >> "$EVENTS"
}

echo "== coin (seed=$SEED)"
COIN=$(python3 -c "import random,sys;print('on' if random.Random(int(sys.argv[1])).random()<0.5 else 'off')" "$SEED")
if [ "$COIN" = "on" ]; then ARM_A=on; ARM_B=off; else ARM_A=off; ARM_B=on; fi
echo "  slot A ($QA) -> $ARM_A ; slot B ($QB) -> $ARM_B"
ev coin both "seed=$SEED A=$QA:$ARM_A B=$QB:$ARM_B"

submit_job() { # $1=queue -> echoes job_id
  local q="$1" jf="$STATE/job-$1.yaml" out id
  sed "s/__TWIN_QUEUE__/$q/" "$DIR/pi-mr234-rig.yaml" > "$jf"
  out=$(testflinger-cli submit "$jf") || { echo "SUBMIT FAILED ($q)"; return 1; }
  id=$(printf '%s' "$out" | grep -oE "$UUID_RE" | head -1)
  echo "$id"
}
echo "== submitting twin jobs (simultaneous)"
JOB_A=$(submit_job "$QA"); ev submit A "job=$JOB_A queue=$QA"
JOB_B=$(submit_job "$QB"); ev submit B "job=$JOB_B queue=$QB"
echo "  A $QA : $JOB_A"
echo "  B $QB : $JOB_B"

wait_ip() { # $1=job_id $2=slot -> echoes device_ip (poll results, 40 min)
  local job="$1" slot="$2" ip i
  for i in $(seq 160); do
    ip=$(timeout 30 testflinger-cli results "$job" 2>/dev/null \
         | python3 -c "import json,sys
try: print((json.load(sys.stdin).get('device_info') or {}).get('device_ip',''))
except Exception: print('')" || true)
    if [ -n "$ip" ]; then echo "$ip"; return 0; fi
    sleep 15
  done
  ev infra "$slot" "no device_ip after 40 min"
  return 1
}
echo "== waiting for device IPs"
IPA=$(wait_ip "$JOB_A" A) && echo "  A ip: $IPA" || IPA=""
IPB=$(wait_ip "$JOB_B" B) && echo "  B ip: $IPB" || IPB=""
# persistent deploy rotates host keys on every reimage (§3.2) — any key
# an IP had under a previous lease is stale by definition, and
# StrictHostKeyChecking=accept-new covers UNKNOWN hosts only (a hashed
# stale entry fails every kit ssh). Rehearsal pair-2 lesson: both slots
# hung in rig-wait until the entries were cleared by hand.
[ -n "$IPA" ] && ssh-keygen -R "$IPA" >/dev/null 2>&1 || true
[ -n "$IPB" ] && ssh-keygen -R "$IPB" >/dev/null 2>&1 || true

wait_rig() { # $1=ip $2=slot — poll ~/rig-ready over ssh (45 min budget)
  local ip="$1" slot="$2" i
  for i in $(seq 90); do
    if ssh -o ConnectTimeout=10 $SSHOPT "ubuntu@$ip" 'test -f ~/rig-ready' 2>/dev/null; then
      ev rig-ready "$slot" "ip=$ip"
      return 0
    fi
    sleep 30
  done
  ev infra "$slot" "rig not ready after 45 min"
  return 1
}
rig_wait() { # background wrapper: $1=ip $2=slot $3=marker-file
  if wait_rig "$1" "$2"; then echo ok > "$3"; else echo fail > "$3"; fi
}
echo "== waiting for rigs (parallel)"
if [ -n "$IPA" ]; then rig_wait "$IPA" A "$STATE/.rigA" & fi
if [ -n "$IPB" ]; then rig_wait "$IPB" B "$STATE/.rigB" & fi
RA=""; RB=""
for i in $(seq 110); do
  [ -f "$STATE/.rigA" ] && RA=$(cat "$STATE/.rigA")
  [ -f "$STATE/.rigB" ] && RB=$(cat "$STATE/.rigB")
  [ -n "$RA" ] && [ -n "$RB" ] && break
  sleep 30
done
rm -f "$STATE/.rigA" "$STATE/.rigB"
echo "  A rig: ${RA:-TIMEOUT} ; B rig: ${RB:-TIMEOUT}"

launch() { # $1=ip $2=slot $3=queue $4=arm — returns 0 on success
  local ip="$1" slot="$2" q="$3" arm="$4"
  echo "== [$slot/$q] launching $arm payload"
  if ! (cd "$DIR" && bash launch-payload.sh "$ip" "$RUNID" "$arm" >/dev/null); then
    ev infra "$slot" "launch failed"
    return 1
  fi
  ev launch "$slot" "arm=$arm ip=$ip"
}

T0A=0; T0B=0; HIT_A=0; HIT_B=0; WALL_A=0; WALL_B=0; INFRA_A=0; INFRA_B=0
FA=0; FB=0
if [ "$RA" = ok ]; then
  if launch "$IPA" A "$QA" "$ARM_A"; then T0A=$(date +%s); else RA=fail; fi
fi
if [ "$RB" = ok ]; then
  if launch "$IPB" B "$QB" "$ARM_B"; then T0B=$(date +%s); else RB=fail; fi
fi

sample() { # $1=ip $2=arm -> "calls in out cache total cost stream_age alive"
  ssh -o ConnectTimeout=15 $SSHOPT "ubuntu@$1" \
    "python3 ~/payload/sample-session.py ~/payload/runs/pair-$RUNID/$2" 2>/dev/null \
    || echo "SSHFAIL"
}
kill_run() { # $1=ip $2=arm
  ssh $SSHOPT "ubuntu@$1" \
    "kill -TERM -- -\$(cat ~/payload/runs/pair-$RUNID/$2/profe.pid) 2>/dev/null" || true
}
mark_done() { # $1=slot — final runner status into $ST_<slot>
  local slot="$1" ip arm
  if [ "$slot" = A ]; then ip="$IPA"; arm="$ARM_A"; else ip="$IPB"; arm="$ARM_B"; fi
  ssh -o ConnectTimeout=15 $SSHOPT "ubuntu@$ip" \
    "cat ~/payload/runs/pair-$RUNID/$arm/runner-status 2>/dev/null" || true
}

cap_hit() { # $1=cost $2=tokens $3=calls -> 0/1
  python3 -c "import sys
c,t,k=float(sys.argv[1] or 0),int(sys.argv[2] or 0),int(sys.argv[3] or 0)
sys.exit(0 if (c>=0.30 or t>=15000000 or k>=250) else 1)" "$1" "$2" "$3"
}

echo "== monitoring (caps: \$${CAP_COST} / ${CAP_TOKENS} tok / ${CAP_CALLS} calls; wall ${WALL_S}s; zact ${ZACT_S}s)"
ST_A=""; ST_B=""
while :; do
  NOW=$(date +%s); ALLDONE=1
  if [ "$T0A" -gt 0 ] && [ -z "$ST_A" ]; then
    ALLDONE=0
    S=$(sample "$IPA" "$ARM_A")
    if [ "$S" = SSHFAIL ]; then
      FA=$((FA+1)); echo "  [A $(date -u +%H:%M:%SZ)] ssh fail $FA/$SSHFAIL_MAX"
      [ "$FA" -ge "$SSHFAIL_MAX" ] && { INFRA_A=1; ST_A=ssh-dead; ev infra A "ssh unreachable x$FA"; }
    else
      FA=0
      read -r CALLS IN OUT CK TOTAL COST SAGE ALIVE <<< "$S"
      echo "  [A $(date -u +%H:%M:%SZ)] calls=$CALLS tok=$TOTAL \$$COST age=${SAGE}s alive=$ALIVE"
      if [ "$ALIVE" = 1 ] && cap_hit "$COST" "$TOTAL" "$CALLS"; then
        kill_run "$IPA" "$ARM_A"; HIT_A=1; ev cap-kill A "cost=$COST tokens=$TOTAL calls=$CALLS"
      fi
      if [ "$ALIVE" = 1 ] && [ -n "$SAGE" ] && [ "$SAGE" -gt "$ZACT_S" ] 2>/dev/null; then
        kill_run "$IPA" "$ARM_A"; INFRA_A=1; ev zact-kill A "stream_age=${SAGE}s"
      fi
      if [ "$ALIVE" = 1 ] && [ $((NOW - T0A)) -gt "$WALL_S" ]; then
        kill_run "$IPA" "$ARM_A"; WALL_A=1; ev wall-kill A "elapsed=$((NOW - T0A))s"
      fi
      if [ "$ALIVE" = 0 ]; then
        ST_A=$(mark_done A); [ -n "$ST_A" ] && ev done A "status=$ST_A"
      fi
    fi
  fi
  if [ "$T0B" -gt 0 ] && [ -z "$ST_B" ]; then
    ALLDONE=0
    S=$(sample "$IPB" "$ARM_B")
    if [ "$S" = SSHFAIL ]; then
      FB=$((FB+1)); echo "  [B $(date -u +%H:%M:%SZ)] ssh fail $FB/$SSHFAIL_MAX"
      [ "$FB" -ge "$SSHFAIL_MAX" ] && { INFRA_B=1; ST_B=ssh-dead; ev infra B "ssh unreachable x$FB"; }
    else
      FB=0
      read -r CALLS IN OUT CK TOTAL COST SAGE ALIVE <<< "$S"
      echo "  [B $(date -u +%H:%M:%SZ)] calls=$CALLS tok=$TOTAL \$$COST age=${SAGE}s alive=$ALIVE"
      if [ "$ALIVE" = 1 ] && cap_hit "$COST" "$TOTAL" "$CALLS"; then
        kill_run "$IPB" "$ARM_B"; HIT_B=1; ev cap-kill B "cost=$COST tokens=$TOTAL calls=$CALLS"
      fi
      if [ "$ALIVE" = 1 ] && [ -n "$SAGE" ] && [ "$SAGE" -gt "$ZACT_S" ] 2>/dev/null; then
        kill_run "$IPB" "$ARM_B"; INFRA_B=1; ev zact-kill B "stream_age=${SAGE}s"
      fi
      if [ "$ALIVE" = 1 ] && [ $((NOW - T0B)) -gt "$WALL_S" ]; then
        kill_run "$IPB" "$ARM_B"; WALL_B=1; ev wall-kill B "elapsed=$((NOW - T0B))s"
      fi
      if [ "$ALIVE" = 0 ]; then
        ST_B=$(mark_done B); [ -n "$ST_B" ] && ev done B "status=$ST_B"
      fi
    fi
  fi
  [ "$ALLDONE" = 1 ] && break
  if [ $((NOW - (T0A > T0B ? T0A : T0B))) -gt 19800 ]; then
    ev giveup both "monitor bound exceeded"; break
  fi
  sleep 120
done

echo "== ground truth + egress dump (Gen10 first validation, §9)"
dump_gt() { # $1=queue $2=ip
  ssh -o ConnectTimeout=15 $SSHOPT "ubuntu@$2" \
    'nproc; free -h | head -2; lscpu | grep -E "^(Model name|Architecture)";
     curl -s -o /dev/null -w "egress openrouter.ai: %{http_code}\n" https://openrouter.ai/api/v1/models;
     curl -s -o /dev/null -w "egress api.charmhub.io: %{http_code}\n" https://api.charmhub.io;
     curl -s -o /dev/null -w "egress pypi.org: %{http_code}\n" https://pypi.org/simple/' \
    > "$STATE/ground-truth-$1.txt" 2>/dev/null || true
}
[ -n "$IPA" ] && dump_gt "$QA" "$IPA"
[ -n "$IPB" ] && dump_gt "$QB" "$IPB"

echo "== recovery (scan + scrub + release) — well inside the lease"
REC_A=""; REC_B=""
recover() { # $1=slot $2=queue $3=ip $4=job -> echoes recovered dir path
  local slot="$1" q="$2" ip="$3" job="$4" out
  if [ -z "$ip" ]; then echo "  [$slot] no machine — skipped"; return 0; fi
  out=$( (cd "$DIR" && bash recover-bundle.sh "$ip" "$job" 2>&1) || true )
  local path
  path=$(printf '%s' "$out" | sed -n 's/.*recovered to: //p' | head -1)
  # human line to STDERR: the function's stdout is the return value (a
  # twin-pair-1 lesson — the captured echo made REC_x two lines, which made
  # every row_for dir test fail and crashed the comparison on a missing sheet)
  echo "  [$slot] recovered: ${path:-NOT FOUND}" >&2
  ev recover "$slot" "job=$job dir=$path"
  echo "$path"
}
REC_A=$(recover A "$QA" "$IPA" "$JOB_A")
REC_B=$(recover B "$QB" "$IPB" "$JOB_B")

echo "== rows (host-side sheet)"
row_for() { # $1=slot $2=rec $3=arm $4=queue $5=job $6=hit $7=wall $8=infra
  local slot="$1" rec="$2" arm="$3" q="$4" job="$5" hit="$6" wall="$7" infra="$8"
  if [ -z "$rec" ] || [ ! -d "$rec/runs/pair-$RUNID/$arm" ]; then
    echo "  [$slot] bundle not found for arm=$arm — no row"; return 0
  fi
  python3 "$DIR/sheet-row.py" "$rec/runs/pair-$RUNID/$arm" \
    "$PAIR" "$slot" "$q" "$arm" 1 "$job" "$hit" "$wall" "$infra" --sheet="$SHEET"
  ev row "$slot" "arm=$arm bundle=$rec"
}
row_for A "$REC_A" "$ARM_A" "$QA" "$JOB_A" "$HIT_A" "$WALL_A" "$INFRA_A"
row_for B "$REC_B" "$ARM_B" "$QB" "$JOB_B" "$HIT_B" "$WALL_B" "$INFRA_B"

echo "== comparison (pair $PAIR: off vs on)"
if [ ! -f "$SHEET" ]; then
  echo "  (no sheet — no rows were written; check recovery)"
else
python3 - "$SHEET" "$PAIR" <<'PYEOF'
import csv, math, sys
rows = [r for r in csv.DictReader(open(sys.argv[1])) if r["pair"] == sys.argv[2]]
off = next((r for r in rows if r["arm"] == "off"), None)
on = next((r for r in rows if r["arm"] == "on"), None)
if not (off and on):
    print("  (incomplete pair — see run-sheet)"); sys.exit(0)
print(f"  {'metric':<16} {'skills-off':>16} {'skills-on':>16} {'on/off':>8}")
for k, lab, fmt in [("calls", "tool calls", ".0f"), ("total_tok", "total tokens", ".0f"),
                    ("cost_frozen_usd", "cost $ (frozen)", ".4f")]:
    o, n = float(off[k]), float(on[k])
    ratio = f"{n/o:.3f}" if o else "-"
    print(f"  {lab:<16} {o:>16{fmt}} {n:>16{fmt}} {ratio:>8}")
do, dn = float(off["dur_s"] or 0), float(on["dur_s"] or 0)
print(f"  {'duration s':<16} {do:>16.0f} {dn:>16.0f} {f'{dn/do:.3f}' if do else '-':>8}")
if float(off["calls"]) and float(on["calls"]):
    d = math.log(float(off["calls"]) / float(on["calls"]))
    print(f"  paired calls log-ratio d = ln(off)-ln(on) = {d:+.3f}")
for r, lab in [(off, "off"), (on, "on")]:
    print(f"  [{lab}] verdict={r['verdict'] or '-'} schema_ok={r['schema_ok']} "
          f"skills_read={r['skills_read']} incidents={r['incidents']} "
          f"leak(c2 gate)={r['leak_check']} "
          f"hit_cap={r['hit_cap']} wall={r['wall_killed']} infra={r['infra']} "
          f"machine={r['machine']}")
PYEOF
fi

echo "== final release check"
testflinger-cli cancel "$JOB_A" >/dev/null 2>&1 || true
testflinger-cli cancel "$JOB_B" >/dev/null 2>&1 || true
echo "  cancelled $JOB_A / $JOB_B (idempotent with recover-bundle.sh step 5)"
ev release both "jobs=$JOB_A,$JOB_B cancelled"
echo "== PAIR DONE (sheet: $SHEET; events: $EVENTS)"
