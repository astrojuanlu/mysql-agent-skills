"""`profe compare`: paired deltas between two graded cells.

Given two grade runs files from different conditions — skills-off vs
skills-on, weak vs strong model — pair records by (task, run) and emit
per-task deltas plus aggregates: rubric-score and schema-compliance
deltas, per-check flips, and effort deltas (tokens, tool calls, wall
time, cost). Conditions must share the record format; interleaved
collection is preferred over back-to-back cells (the protocol's
decorrelation rule), which compare cannot enforce — it only records
what it was given.
"""

from __future__ import annotations

import typing as t
from pathlib import Path

from pydantic import BaseModel, Field

from .grade import load_grade_records, select_attempt
from .report import _condition, _median, _total_tokens


class TaskPair(BaseModel):
    """One task's paired result across the two cells."""

    task_id: str
    run: int
    a_score: float | None
    b_score: float | None
    score_delta: float | None = Field(description="b - a")
    a_schema_ok: bool | None
    b_schema_ok: bool | None
    a_scores: dict[str, bool] = Field(default_factory=dict)
    b_scores: dict[str, bool] = Field(default_factory=dict)
    checks_gained: list[str] = Field(default_factory=list)
    checks_lost: list[str] = Field(default_factory=list)
    a_total_tokens: int | None = None
    b_total_tokens: int | None = None
    a_cost: float | None = None
    b_cost: float | None = None
    a_tool_calls: int | None = None
    b_tool_calls: int | None = None
    a_dur_s: int | None = None
    b_dur_s: int | None = None


class CompareReport(BaseModel):
    """The paired comparison plus per-cell aggregates."""

    a_file: str
    b_file: str
    a_condition: dict[str, t.Any]
    b_condition: dict[str, t.Any]
    n_pairs: int
    pairs: list[TaskPair]
    improved: int = Field(description="score delta > 0")
    flat: int = Field(description="score delta == 0")
    worse: int = Field(description="score delta < 0")
    median_score_delta: float | None = None
    median_effort_delta: dict[str, float] = Field(default_factory=dict)


def compare_grade_runs(a_file: str, b_file: str) -> CompareReport:
    """Pair the two cells' records by (task, run) and compute deltas."""
    a_records = load_grade_records(Path(a_file))
    b_records = load_grade_records(Path(b_file))
    if not a_records or not b_records:
        msg = "both files must contain graded records"
        raise ValueError(msg)

    def keyed(records: list[t.Any]) -> dict[tuple[str, int], t.Any]:
        """Select the attempt that drives scores, per (task, run)."""
        by_key: dict[tuple[str, int], list[t.Any]] = {}
        for rec in records:
            by_key.setdefault((rec.task_id, rec.run), []).append(rec)
        return {k: select_attempt(v) for k, v in by_key.items()}

    a_map, b_map = keyed(a_records), keyed(b_records)
    pairs: list[TaskPair] = []
    for key in sorted(set(a_map) & set(b_map)):
        ra, rb = a_map[key], b_map[key]
        gained = sorted(
            c for c in rb.scores if rb.scores[c] and not ra.scores.get(c, False)
        )
        lost = sorted(
            c for c in ra.scores if ra.scores[c] and not rb.scores.get(c, False)
        )
        delta = (
            rb.rubric_score - ra.rubric_score
            if ra.rubric_score is not None and rb.rubric_score is not None
            else None
        )
        pairs.append(
            TaskPair(
                task_id=key[0],
                run=key[1],
                a_score=ra.rubric_score,
                b_score=rb.rubric_score,
                score_delta=delta,
                a_schema_ok=ra.schema_ok,
                b_schema_ok=rb.schema_ok,
                a_scores=ra.scores,
                b_scores=rb.scores,
                checks_gained=gained,
                checks_lost=lost,
                a_total_tokens=_total_tokens(ra),
                b_total_tokens=_total_tokens(rb),
                a_cost=ra.usage.cost.total,
                b_cost=rb.usage.cost.total,
                a_tool_calls=ra.n_tool_calls,
                b_tool_calls=rb.n_tool_calls,
                a_dur_s=ra.dur_s,
                b_dur_s=rb.dur_s,
            ),
        )

    deltas = [p.score_delta for p in pairs if p.score_delta is not None]
    # Unpairable files (no shared task ids) leave every median None —
    # keep only computable deltas so an empty pairing degrades to an
    # empty effort table instead of failing validation.
    effort = {
        key: value
        for key, value in {
            "tokens": _median(
                [p.b_total_tokens - p.a_total_tokens for p in pairs],
            ),
            "tool_calls": _median(
                [p.b_tool_calls - p.a_tool_calls for p in pairs],
            ),
            "dur_s": _median([p.b_dur_s - p.a_dur_s for p in pairs]),
            "cost": _median([p.b_cost - p.a_cost for p in pairs]),
        }.items()
        if value is not None
    }
    return CompareReport(
        a_file=a_file,
        b_file=b_file,
        a_condition=_condition(a_records),
        b_condition=_condition(b_records),
        n_pairs=len(pairs),
        pairs=pairs,
        improved=sum(1 for d in deltas if d > 0),
        flat=sum(1 for d in deltas if d == 0),
        worse=sum(1 for d in deltas if d < 0),
        median_score_delta=_median(deltas),
        median_effort_delta=effort,
    )


def render_compare_table(report: CompareReport) -> str:
    """Render the headline: per-task deltas and aggregate counts.

    Score cells are `checks/total`; the `checks` column names the checks
    that flipped (B gained `+`, B lost `-`) — the score delta without
    the flip names is unreadable.
    """
    lines = [
        f"compare: {report.a_file} (A) vs {report.b_file} (B)",
        f"A: model={report.a_condition.get('model') or 'default'} "
        f"skills={len(report.a_condition.get('loaded_skills', []))} | "
        f"B: model={report.b_condition.get('model') or 'default'} "
        f"skills={len(report.b_condition.get('loaded_skills', []))}",
        "",
        f"{'task':24s} {'run':>3s} {'A score':>10s} {'B score':>10s} {'Δ':>6s}"
        f"  {'A tok':>7s} {'B tok':>7s} {'A cost':>7s} {'B cost':>7s}"
        f" {'A dur':>5s} {'B dur':>5s}  {'checks (B vs A)':<28s}",
    ]
    for p in report.pairs:
        flips = (
            ", ".join(f"+{c}" for c in p.checks_gained)
            + ", ".join(f"−{c}" for c in p.checks_lost)
        ).strip(", ")
        lines.append(
            f"{p.task_id:24s} {p.run:>3d} "
            f"{_fmt_cell(p.a_scores, p.a_score):>10s} "
            f"{_fmt_cell(p.b_scores, p.b_score):>10s} "
            f"{_fmt_score(p.score_delta):>6s}  "
            f"{p.a_total_tokens or 0:>7d} {p.b_total_tokens or 0:>7d} "
            f"{p.a_cost or 0:>7.4f} {p.b_cost or 0:>7.4f} "
            f"{p.a_dur_s or 0:>5d} {p.b_dur_s or 0:>5d}  "
            f"{(flips or '—')[:28]:<28s}",
        )
    lines += [
        "",
        "score cells are passed/total mechanical rubric checks; "
        "Δ = B − A in aggregate points",
        f"score: {report.improved} improved / {report.flat} flat / "
        f"{report.worse} worse (of {report.n_pairs} pairs), "
        f"median Δ = {_fmt_score(report.median_score_delta)}",
        "effort (median Δ, B−A): "
        + ", ".join(
            (f"{k} {v:+.4f}" if k == "cost" else f"{k} {v:+.1f}")
            if v is not None
            else f"{k} n/a"
            for k, v in report.median_effort_delta.items()
        ),
    ]
    return "\n".join(lines)


def _fmt_score(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.2f}" if value < 0 or "+" in f"{value}" else f"{value:.2f}"


def _fmt_cell(scores: dict[str, bool], value: float | None) -> str:
    """One score cell: `x/y (z.zz)` — the aggregate with its components."""
    if value is None:
        return "n/a"
    return f"{sum(scores.values())}/{len(scores)} ({value:.2f})"
