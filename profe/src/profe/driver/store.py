"""Event persistence (PROFE-DRIVE-PROPOSAL §3.2).

The manifest-stamp rule lives HERE, in the store, where it can't be
forgotten: a store refuses to append a line whose manifest differs from
the one it was bound to. Concrete stores are standalone (no ABC); the
`EventStore` Protocol is structural — backends conform without
importing profe.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from profe.driver.models import AttemptEvent

# fixed wire keys (fields are flattened AFTER these)
_FIXED = ("ts", "pair", "event", "slot", "manifest", "schema")
SCHEMA_VERSION = 1


def now() -> str:
    """ISO-8601 UTC timestamp (today's wire format)."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class EventStore(Protocol):
    """Structural interface: no ABC, backends need not import profe."""

    sha8: str  # the manifest hash every event is stamped with

    def append(self, event: AttemptEvent) -> None:
        """Write one event (manifest-stamped)."""
        ...

    def stream(self) -> Iterator[AttemptEvent]:
        """Yield every event ever appended, in write order."""
        ...


def _check_manifest(event: AttemptEvent, bound: str) -> None:
    if event.manifest != bound:
        raise ValueError(
            f"event manifest {event.manifest!r} != store-bound {bound!r} "
            "(the manifest-stamp rule lives in the store)"
        )


class JsonlEventStore:
    """Append-only JSONL file; every line carries the manifest hash.

    Byte-compatible with rig driver-v3's events.jsonl (same key set,
    same order convention); new lines additionally carry `schema: 1`
    (proposal §8.4 — readers tolerate its absence, old readers ignore
    the extra key).
    """

    def __init__(self, path: Path, manifest_sha8: str):
        """Bind the ledger path and the store's manifest stamp."""
        self.path = Path(path)
        self.sha8 = manifest_sha8

    def append(self, event: AttemptEvent) -> None:
        """Append one JSONL line (manifest-checked first)."""
        _check_manifest(event, self.sha8)
        line = {
            "ts": event.ts,
            "pair": event.pair,
            "event": event.kind,
            "slot": event.slot,
            "manifest": event.manifest,
            "schema": SCHEMA_VERSION,
            **event.fields,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(line, sort_keys=False) + "\n")

    def stream(self) -> Iterator[AttemptEvent]:
        """Replay the file (missing file = empty stream)."""
        if not self.path.is_file():
            return
        for line in self.path.read_text().splitlines():
            if not line.strip():
                continue
            yield _parse_line(line)


class ListEventStore:
    """In-memory store (tests); same manifest-stamp contract."""

    def __init__(self, manifest_sha8: str = "testmanifest"):
        """Hold events in a list; sha8 defaults to a test stamp."""
        self.sha8 = manifest_sha8
        self.events: list[AttemptEvent] = []

    def append(self, event: AttemptEvent) -> None:
        """Manifest-check, then keep."""
        _check_manifest(event, self.sha8)
        self.events.append(event)

    def stream(self) -> Iterator[AttemptEvent]:
        """Yield the kept events in order."""
        yield from self.events


def _parse_line(line: str) -> AttemptEvent:
    """One wire line -> AttemptEvent (fields = everything not fixed).

    Tolerates legacy lines without `schema` and legacy field names
    (`ip` rides inside fields untouched — the tracker's fold maps it).
    """
    raw = json.loads(line)
    fields = {k: v for k, v in raw.items() if k not in _FIXED}
    return AttemptEvent(
        ts=raw.get("ts", ""),
        pair=int(raw.get("pair", 0)),
        kind=str(raw.get("event", "note")),
        slot=str(raw.get("slot", "both")),
        manifest=str(raw.get("manifest", "")),
        fields=fields,
    )
