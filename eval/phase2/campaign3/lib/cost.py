#!/usr/bin/env python3
"""Canonical frozen-price cost accounting for c3 (retro A4/A1, DESIGN §4.2).

ONE implementation of the ~10-line computation that campaign 2 recomputed
ad-hoc 4+ times across sessions. Every sheet, review, and analysis derives
from here; nobody hardcodes the price vector again.

    lib/frozen-prices.json  — the pinned vector (edit ONLY this to re-snapshot)
    cost.session_usage(p)   — read a bundle's session.jsonl usage events
                              (implemented by profe.bundle, the schema
                              owner — re-exported here for API stability)
    cost.cost(components)   — dollars under the pinned vector

Usage (the `profe cost <bundle>` shape):
    python3 lib/cost.py <bundle-dir>
      → input=... output=... cacheRead=... totalTokens=...
        calls=... skill_reads=... cost_usd=...

The PRICING stays campaign-side on purpose: the price vector is campaign
protocol (frozen, hashed into the manifest) — profe takes prices as an
argument, never owns a vector. The USAGE WALK is profe-domain (session
schema + skill-read concept) and lives in `profe.bundle` (stdlib-only, so
this module still runs on a bare host).
"""

import json
import sys
from pathlib import Path

# profe.bundle is stdlib-only; make it importable on a bare host even when
# only lib/ is on sys.path (sheet-row.py inserts just lib/).
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "profe" / "src"))

from profe.bundle import session_usage  # noqa: E402  (schema owner, above)

PRICES_PATH = Path(__file__).resolve().parent / "frozen-prices.json"


def load_prices(path=None):
    """Pinned vector as per-token multipliers (USD/Mtok → USD/token)."""
    p = json.loads((Path(path) if path else PRICES_PATH).read_text())
    v = p["vector_usd_per_mtok"]
    return {
        "input": v["input"] * 1e-6,
        "cache_read": v["cache_read"] * 1e-6,
        "output": v["output"] * 1e-6,
    }


def cost(components, prices=None):
    """Dollars for one attempt under the pinned vector (frozen, §4.2)."""
    pr = prices or load_prices()
    return (
        components["input"] * pr["input"]
        + components["cacheRead"] * pr["cache_read"]
        + components["output"] * pr["output"]
    )


def bundle_cost(bundle_dir, prices=None):
    """Return usage + cost for one recovered bundle directory."""
    u = session_usage(Path(bundle_dir) / "session.jsonl")
    u["cost_usd"] = cost(u["components"], prices)
    return u


def main():
    """CLI: print the canonical usage + cost line for one bundle."""
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    u = bundle_cost(sys.argv[1])
    c = u["components"]
    print(
        f"input={c['input']} output={c['output']} "
        f"cacheRead={c['cacheRead']} totalTokens={c['totalTokens']} "
        f"calls={u['calls']} skill_reads={','.join(u['skill_reads']) or '-'} "
        f"cost_usd={u['cost_usd']:.6f}"
    )


if __name__ == "__main__":
    main()
