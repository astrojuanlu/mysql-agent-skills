"""Shared helpers: runs-file access and attempt selection.

One definition of "which attempt drives the accounting" (implementation-
notes §7: first-clean-attempt-per-run semantics are shared by probe and
summarize — same selection function, one definition).
"""

from __future__ import annotations

from pathlib import Path

from ..models import ProbeRecord

AttemptKey = tuple[int, int]  # (index, run)


def load_records(path: str | Path) -> list[ProbeRecord]:
    """Read a runs JSONL, tolerating torn lines (crash-safe appends).

    A line that does not parse (an interrupted batch can leave one) is
    skipped, not fatal — the complete attempts are already on disk.
    """
    records: list[ProbeRecord] = []
    for line in Path(path).read_text().splitlines():
        try:
            records.append(ProbeRecord.model_validate_json(line))
        except Exception:  # noqa: BLE001 — torn/partial line, skip it
            continue
    return records


def group_attempts(records: list[ProbeRecord]) -> dict[AttemptKey, list[ProbeRecord]]:
    """Group records per (index, run), sorted by attempt order."""
    grouped: dict[AttemptKey, list[ProbeRecord]] = {}
    for rec in records:
        grouped.setdefault((rec.index, rec.run), []).append(rec)
    for attempts in grouped.values():
        attempts.sort(key=lambda r: r.attempt)
    return grouped


def first_clean(attempts: list[ProbeRecord]) -> ProbeRecord | None:
    """Select the attempt that drives the accounting for one run.

    The first clean one, else None: a run whose every attempt errored is
    not trigger evidence (probe redoes it, summarize preserves it with no
    verdict).
    """
    return next((r for r in attempts if r.clean), None)
