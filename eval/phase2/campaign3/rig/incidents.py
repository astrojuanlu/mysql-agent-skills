#!/usr/bin/env python3
"""Record-only forensics for a c3 attempt bundle (DESIGN §3.5 / §4.6).

Replaces leak-check v2.8's GATE. Under the disposable-machine rig every
campaign-2 INVALIDATING criterion is obsolete:

  - the attempt's world is a fresh machine plus the public internet (§3.2);
    nothing campaign-internal is reachable from inside it (§3.3);
  - the machine — including its juju models and controllers — is wiped at
    teardown (§3.2.6), so a model teardown or stray model cannot damage
    the campaign (the c2 rule defended a shared host channel);
  - agent behavior is never invalidating: it is either infra (rerun, no
    slot) or data (§4.6 two-class rule, incidents.jsonl in driver v3).

This recorder walks session.jsonl and writes counters/lists ONLY; it never
gates and always exits 0 on a readable bundle. The §4.6.3 stop-and-redesign
tripwire stays with the driver: a reference to campaign-host paths in a
bundle is a rig bug, not a data point — the projects_paths list here is the
raw material for that check.

Counters (all recorded, none gating):
  model_switch      juju switch targets (verbatim)
  (2026-10-18 operator decision: in-machine environment MUTATIONS are no
  longer recorded — the destroy-model / add-model / bootstrap capture and
  its self_created_models derivative are retired; under pool mode every
  machine is freshly minted (§3.7 + the rig's hard reset), and the
  §5.1.4 behavior-safety endpoint is contract anomalies only.)
  gh_commands       assistant `gh` invocations (count)
  git_sources       git clone/fetch/pull/ls-remote command segments
                    (verbatim) — source-acquisition provenance: the
                    contamination adjudication signal (DESIGN §6: did
                    this attempt read the deployed revision, the live
                    issue thread, or something newer?)
  projects_paths    references to ~/Projects (impossible-by-construction
                    on the rig; recorded for the §4.6.3 tripwire)
  home_writes       unique /home/ubuntu paths outside the workspace,
                    the pinned pi install, and the pi config
  bug234_mentions   standalone-token `bug234` occurrences (v2.8 compound
                    rule kept: task-derived compounds are not campaign
                    knowledge; recorded for the behavior-safety endpoint)

Usage: incidents.py <bundle-dir>  →  writes <bundle-dir>/incidents.json
"""

import json
import re
import sys
from pathlib import Path

WORKSPACE = "/home/ubuntu/payload"  # the staged workspace (§3.4)
PI_INSTALL = "/profe-t3/"  # pinned pi install dir (rig phase)
PI_CONFIG = "/home/ubuntu/.pi/"  # pi config dir (key injection)
BUG234_RX = re.compile(r"(?<![a-zA-Z0-9-])bug234(?![a-zA-Z0-9-])")


def _uniq(seq):
    out, seen = [], set()
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def main() -> None:
    bundle = Path(sys.argv[1])
    session = bundle / "session.jsonl"
    if not session.is_file():
        print(f"incidents: no session.jsonl in {bundle} — nothing to record")
        sys.exit(2)

    rec = {
        "policy": "c3-record-only-1.0 (DESIGN §3.5/§4.6 — sandboxed attempt,"
        " machine disposable; nothing here gates)",
        "model_switch": [],
        "gh_commands": 0,
        "git_sources": [],
        "projects_paths": [],
        "home_writes": 0,
        "bug234_mentions": 0,
    }

    for line in open(session):
        try:
            raw = json.loads(line)
        except Exception:
            continue
        m = raw.get("message") or {}
        for c in m.get("content") or []:
            if not isinstance(c, dict):
                continue
            blob = json.dumps(c)
            if BUG234_RX.search(blob):
                rec["bug234_mentions"] += 1
            if c.get("type") != "toolCall":
                continue
            cmd = str((c.get("arguments") or {}).get("command", ""))
            for sw in re.finditer(r"juju switch\s+(\S+)", cmd):
                rec["model_switch"].append(sw.group(1))
            if re.search(r"(^|[;&|]\s*|\beval\s+)gh\b", cmd):
                rec["gh_commands"] += 1
            for gs in re.findall(r"git\s+(?:clone|fetch|pull|ls-remote)\b[^;&|]*", cmd):
                rec["git_sources"].append(gs.strip())
            for seg in re.findall(r"(?:/home/ubuntu|~)/Projects[^;&|]*", cmd):
                rec["projects_paths"].append(seg)

    rec["model_switch"] = _uniq(rec["model_switch"])
    rec["git_sources"] = _uniq(rec["git_sources"])
    rec["projects_paths"] = _uniq(rec["projects_paths"])

    # /home/ubuntu writes — hygiene record; unique paths, excluding the
    # workspace the task staged, the pinned pi install, and the pi config
    seen: set[str] = set()
    for line in open(session):
        for raw_path in re.findall(r"/home/ubuntu/[^\s\"')\\]+", line):
            path = raw_path.rstrip(".,;)")
            if (
                path.startswith(WORKSPACE)
                or PI_INSTALL in path
                or path.startswith(PI_CONFIG)
            ):
                continue
            if path not in seen:
                seen.add(path)
    rec["home_writes"] = len(seen)

    (bundle / "incidents.json").write_text(json.dumps(rec, indent=1))
    print(
        f"incidents (record-only): gh={rec['gh_commands']} "
        f"git={len(rec['git_sources'])} "
        f"projects={len(rec['projects_paths'])} home_writes={rec['home_writes']}"
    )


if __name__ == "__main__":
    main()
