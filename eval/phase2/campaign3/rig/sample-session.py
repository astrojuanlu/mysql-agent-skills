#!/usr/bin/env python3
"""Live per-attempt usage sampler for the host-side pair driver.

Runs ON the attempt machine; the driver ssh-invokes it with the run's OUT
dir and reads one summary line per poll:

    calls in_tok out_tok cache_tok total_tok cost_frozen_usd stream_age_s profe_alive

Source = stream.jsonl (pi writes it incrementally during the run;
session.jsonl only materializes at run end, so a session-based sampler
reads zeros all attempt long — the live-cap blind spot found during the
twin-pair rehearsal). Validated against the recovered 901/on bundle:
`tool_execution_start` count = calls exactly (85), and the last
`message_update` usage within each message bracket, summed over brackets,
reproduces the session.jsonl token totals exactly.

Cost uses c3's frozen price vector (c2 snapshot, DESIGN §4.2):
P_IN=0.0261 P_CACHE=0.0108 P_OUT=0.90 $/Mtok — the same constants the
run-sheet row uses, so live and final numbers agree.
"""

import json
import os
import pathlib
import sys
import time

P_IN, P_CACHE, P_OUT = 0.0261e-6, 0.0108e-6, 0.90e-6  # frozen §7 snapshot

out = pathlib.Path(sys.argv[1]).expanduser()
stream, pidf = out / "stream.jsonl", out / "profe.pid"

calls = in_t = out_t = cache_t = 0
last = None
if stream.exists():
    with stream.open() as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            t = r.get("type")
            if t == "tool_execution_start":
                calls += 1
            elif t == "message_update":
                u = r.get("usage")
                if u:
                    last = u
            elif t == "message_end":
                if last:
                    in_t += last.get("input") or 0
                    out_t += last.get("output") or 0
                    cache_t += last.get("cacheRead") or 0
                last = None

cost = in_t * P_IN + cache_t * P_CACHE + out_t * P_OUT


def age(p):
    try:
        return time.time() - p.stat().st_mtime
    except OSError:
        return -1.0


alive = 0
if pidf.exists():
    try:
        os.kill(int(pidf.read_text().strip()), 0)
        alive = 1
    except (OSError, ValueError):
        alive = 0

total = in_t + out_t + cache_t
print(f"{calls} {in_t} {out_t} {cache_t} {total} {cost:.4f} {age(stream):.0f} {alive}")
