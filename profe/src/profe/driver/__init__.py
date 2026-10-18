"""The attempt machinery (consolidation of rig/driver-v3.py, PROFE-DRIVE).

Layering: `profe.driver` is an L1 sibling of `profe.actions`; the two
never import each other — they meet at the process boundary (a sandbox
backend stages the instrument and invokes `profe run`; the drive loop
reads the bundle back through `profe.bundle`, the L0 seam). The
campaign consumes this package as a library and keeps all judgment
(A8): pairs, queues, coin epoch, dispositions, stats.

Operator constraints (PROFE-DRIVE-PROPOSAL, 2026-10-17):
- No ABCs: interface conformance is `typing.Protocol` (structural).
- Composition over inheritance: concrete classes inherit from nothing.
- Backend differences are separate standalone classes, capability
  protocols, or capability data — never a base-class hierarchy.
"""

from profe.driver.drive import (
    Adjudication,
    KillFlags,
    adjudicate,
    classify_termination,
    drive_attempt,
    monitor_attempt,
    scan_text,
)
from profe.driver.models import (
    TERMINAL,
    AttemptEvent,
    AttemptState,
    CapPolicy,
    LedgerPolicy,
    Sample,
    SlotKey,
    SlotRecord,
    parse_sample,
)
from profe.driver.sandbox import (
    AttemptSandbox,
    AttemptSpec,
    LocalOneShotSandbox,
    SandboxUnreachable,
)
from profe.driver.store import EventStore, JsonlEventStore, ListEventStore
from profe.driver.tracker import AttemptTracker, coin_drift, fair_coin

__all__ = [
    "TERMINAL",
    "AttemptEvent",
    "AttemptSandbox",
    "AttemptSpec",
    "AttemptState",
    "AttemptTracker",
    "CapPolicy",
    "EventStore",
    "JsonlEventStore",
    "Adjudication",
    "KillFlags",
    "LedgerPolicy",
    "ListEventStore",
    "LocalOneShotSandbox",
    "Sample",
    "SandboxUnreachable",
    "SlotKey",
    "SlotRecord",
    "adjudicate",
    "classify_termination",
    "coin_drift",
    "drive_attempt",
    "fair_coin",
    "monitor_attempt",
    "parse_sample",
    "scan_text",
]
