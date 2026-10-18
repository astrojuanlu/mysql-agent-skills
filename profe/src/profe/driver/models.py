"""Frozen types of the attempt machine (PROFE-DRIVE-PROPOSAL §3.1).

Plain frozen dataclasses — harness state, never user-facing artifacts
(the pydantic schema models stay in `profe.actions.models`). Wire
format is byte-compatible with rig/driver-v3's events.jsonl: the JSON
key `event` carries `kind`, `manifest` stamps provenance on every line,
and the legacy memory names (`ip`, state string `rig-ready`) are only
renamed here, never in the log (proposal §8.5).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class AttemptState(StrEnum):
    """Attempt lifecycle states, wire-faithful to the rehearsal log."""

    PENDING = "pending"
    SUBMITTED = "submitted"
    ENDPOINT_READY = "endpoint-ready"  # was "rig-ready" (rehearsal logs keep
    # the legacy string; AttemptState.from_wire accepts both)
    RUNNING = "running"
    ACCEPTED = "accepted"
    KEPT_ITT_FAILED = "kept-itt-failed"
    REJECTED_CONTAMINATION = "rejected-contamination"
    INFRA_FAILED = "infra-failed"

    @classmethod
    def from_wire(cls, value: str) -> AttemptState:
        """Parse one log state string.

        Legacy `rig-ready` maps to ENDPOINT_READY (byte-compatible
        reading of the rehearsal record, proposal §8.5).
        """
        if value == "rig-ready":  # noqa: PLR2004 (legacy wire literal)
            return cls.ENDPOINT_READY
        return cls(value)


# INFRA_FAILED is deliberately NOT terminal: it stays in the loop until
# the ledger cap. That omission *is* the §4.3 retry policy.
TERMINAL = frozenset(
    {
        AttemptState.ACCEPTED,
        AttemptState.KEPT_ITT_FAILED,
        AttemptState.REJECTED_CONTAMINATION,
    }
)


@dataclass(frozen=True)
class SlotKey:
    """Which campaign slot a record belongs to."""

    pair: int
    slot: str  # "A"/"B" today; labels are campaign's choice


@dataclass
class SlotRecord:
    """The state vector per (pair, slot).

    Field-for-field rig driver-v3's _fresh_slot(); `ip` is renamed
    `endpoint` (it need not be an IP).
    """

    state: AttemptState = AttemptState.PENDING
    job: str | None = None  # opaque sandbox handle (testflinger uuid today)
    endpoint: str | None = None  # where the attempt is reachable (ip today)
    arm: str | None = None
    t0: int | None = None  # attempt-envelope start (§4.4 wall base)
    attempt: int = 0
    coin: dict[str, Any] | None = None  # the recorded P7 draw for this pair
    n_infra: int = 0
    bundle: str | None = None  # recovered dir, when settle wrote one
    machine: str | None = None  # DUT identity (pool mode: recorded, not pinned)


# fields a `state` event may carry; the replay fold absorbs them
# (last wins) — `ip` is the legacy wire name of `endpoint`
STATE_FIELDS = ("state", "job", "endpoint", "ip", "arm", "t0", "bundle", "machine")


@dataclass(frozen=True)
class AttemptEvent:
    """One log line."""

    ts: str
    pair: int
    kind: str  # serialized as the JSON key "event"
    slot: str  # "A"/"B"/"both"
    manifest: str
    fields: dict[str, Any]  # open provenance payload (flattened on wire)


@dataclass(frozen=True)
class LedgerPolicy:
    """The §4.3 infra ledger; the value lives in the manifest."""

    max_infra_per_slot: int = 3


@dataclass(frozen=True)
class CapPolicy:
    """The §4.4 caps and monitor windows.

    Values live in the campaign manifest; the poll/patience fields
    extend the proposal sketch to carry today's full watchdog block
    (the mechanism needs the windows; the DATA stays manifest-side).
    """

    cost_usd: float
    total_tokens: int
    calls: int
    wall_s: int
    zact_s: int
    sshfail_max: int
    monitor_bound_s: int
    poll_s: int = 120  # live-session poll interval
    ip_wait_s: int = 2400  # resolve() patience window
    rig_wait_s: int = 2700  # endpoint_ready() patience window
    endpoint_poll_s: int = 15
    ready_poll_s: int = 30


@dataclass(frozen=True)
class Sample:
    """Parsed live-session line (today: sample-session.py's 8 fields)."""

    calls: int
    tokens: int
    cost_usd: float
    stream_age_s: int | None  # None = not a number (zact check skipped)
    alive: bool


def parse_sample(text: str) -> Sample:
    """Parse one sample wire line.

    Wire: `<calls> <in> <out> <cache> <total> <cost> <age> <alive>`
    (sample-session.py's space-separated line; missing fields default
    to 0 — today's `(raw.split() + ["0"] * 8)[:8]`).
    """
    raw = (text.split() + ["0"] * 8)[:8]
    age = raw[6]
    return Sample(
        calls=int(raw[0] or 0),
        tokens=int(raw[4] or 0),
        cost_usd=float(raw[5] or 0),
        stream_age_s=int(age) if age.isdigit() else None,
        alive=raw[7] == "1",
    )


def fresh_record() -> SlotRecord:
    """Return a new SlotRecord in its pending initial state."""
    return SlotRecord()
