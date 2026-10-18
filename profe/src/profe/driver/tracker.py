"""The attempt tracker: single writer, pure fold (§3.3).

This is rig driver-v3's slot table + emit_state + replay, made
explicit: every mutation goes through the store first, then the table
(the same motion, in the same order — `state` events absorb their
fields last-wins, the coin fans out to both slots, INFRA_FAILED
increments the ledger counter, everything else is provenance only).

The P7 coin: profe ships only the primitive (`fair_coin`) and the
drift check; the campaign owns the seed epoch, the slot/queue
application, and the once-only emission (A8).
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from typing import Any

from profe.driver.models import (
    STATE_FIELDS,
    TERMINAL,
    AttemptEvent,
    AttemptState,
    LedgerPolicy,
    SlotKey,
    SlotRecord,
    fresh_record,
)
from profe.driver.store import EventStore, now


class AttemptTracker:
    """In-memory machine over (pair, slot).

    Every mutation is written through the store.
    """

    def __init__(self, store: EventStore, policy: LedgerPolicy):
        """Bind the store (single writer) and the §4.3 ledger policy."""
        self.store = store
        self.policy = policy
        self.st: dict[SlotKey, SlotRecord] = {}

    # -- the transition writer ----------------------------------------
    def record(self, key: SlotKey, state: AttemptState, **f: Any) -> None:
        """Write one state transition.

        One state event through the store, then the fold updates the
        table (today's emit_state motion, log-first — a crash between
        the two loses nothing that replay can't rebuild).
        """
        event = AttemptEvent(
            ts=now(),
            pair=key.pair,
            kind="state",
            slot=key.slot,
            manifest=self.store.sha8,
            fields={"state": str(state), **f},
        )
        self.store.append(event)
        self.apply(event)

    def note(self, key: SlotKey | None, kind: str, **f: Any) -> None:
        """Provenance events (submit/ip/cap-kill/notes) — never state.

        key=None is a pair-level note: pair 0, slot "both" (today's
        resume-note shape). The fold runs here too (log-first, same as
        record): the coin note is what puts the recorded draw into the
        table — without it a note-append-only note would re-emit the
        coin every pass (2026-10-18: the dry-run check caught it).
        """
        pair = key.pair if key else 0
        slot = key.slot if key else "both"
        event = AttemptEvent(
            ts=now(),
            pair=pair,
            kind=kind,
            slot=slot,
            manifest=self.store.sha8,
            fields=f,
        )
        self.store.append(event)
        self.apply(event)

    # -- the replay fold ----------------------------------------------
    def apply(self, event: AttemptEvent) -> None:
        """One fold step (today's replay() body, verbatim semantics)."""
        f = event.fields
        if event.kind == "coin" and "arms" in f:
            # pair-level event (slot "both"): the recorded coin belongs
            # to BOTH slots — coin reads it from each slot's record. The
            # full arms dict is stored (queues included) so a drift check
            # can compare the whole draw, exactly as rig replay does.
            arms = f["arms"]
            for s in ("A", "B"):
                rec = self.st.setdefault(SlotKey(event.pair, s), fresh_record())
                rec.coin = arms if isinstance(arms, dict) else None
            return
        if event.kind != "state":
            return  # provenance only; state comes from state events
        key = SlotKey(event.pair, event.slot)
        rec = self.st.setdefault(key, fresh_record())
        for k in STATE_FIELDS:
            if f.get(k) is not None:
                if k == "state":
                    rec.state = AttemptState.from_wire(str(f[k]))
                elif k == "ip":  # legacy wire name -> endpoint
                    rec.endpoint = str(f[k])
                else:
                    setattr(rec, k, f[k])
        if f.get("attempt") is not None:
            rec.attempt = int(f["attempt"])
        if rec.state == AttemptState.INFRA_FAILED:
            rec.n_infra += 1

    @classmethod
    def replay(cls, store: EventStore, policy: LedgerPolicy) -> AttemptTracker:
        """Fold the whole event stream into a fresh tracker."""
        tracker = cls(store, policy)
        for event in store.stream():
            tracker.apply(event)
        return tracker

    # -- reads ---------------------------------------------------------
    def record_of(self, key: SlotKey) -> SlotRecord:
        """Return the slot's record, creating it pending on first touch."""
        return self.st.setdefault(key, fresh_record())

    def is_terminal(self, key: SlotKey) -> bool:
        """Report whether the slot has reached a terminal state."""
        return self.record_of(key).state in TERMINAL

    def in_loop(self, keys: Iterable[SlotKey]) -> list[SlotKey]:
        """Slots still needing work (not terminal)."""
        return [k for k in keys if not self.is_terminal(k)]

    def infra_exhausted(self, key: SlotKey) -> bool:
        """§4.3: the ledger cap — policy decision belongs to the caller."""
        return self.record_of(key).n_infra >= self.policy.max_infra_per_slot


def fair_coin(seed: int) -> bool:
    """P7 primitive: the seeded draw (arms-to-queues stays campaign-side)."""
    return random.Random(seed).random() < 0.5


def coin_drift(recorded: dict[str, Any], seed: int) -> bool:
    """Check a recorded coin draw against the seed.

    True when the recorded arms disagree with the seed's draw. Compares
    ARM ASSIGNMENTS only (queues are the campaign's judgment and may
    legitimately differ across manifests).
    """
    on_a = fair_coin(seed)
    expected = {"A": "on" if on_a else "off", "B": "off" if on_a else "on"}
    return any((recorded.get(s) or {}).get("arm") != expected[s] for s in ("A", "B"))
