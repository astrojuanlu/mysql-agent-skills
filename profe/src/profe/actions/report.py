"""`profe report`: render graded runs into the durable deliverable.

Per-task scores with the failed checks named, condition metadata,
verdict-class tallies and effort medians — everything a reader needs
to interpret the numbers without re-opening the runs file. A trigger
cell (records whose rubric made trigger claims — `expected_skills` is
stamped, the `triggered` map replayed) additionally gets the routing
shapes: per-skill rows, the cross-trigger (misroute) table and the
per-task per-run verdicts. Every number derives from the records
alone — no caller memory involved.
"""

from __future__ import annotations

import json
import typing as t
from pathlib import Path

from pydantic import BaseModel, Field

from ..models import RoutingReport, RunRouting, SkillRate, TaskRouting
from .grade import load_grade_records, select_attempt


class ReportEntry(BaseModel):
    """One task's selected (first clean) attempt, graded."""

    task_id: str
    rubric_id: str
    rubric_version: int
    run: int
    clean: bool
    schema_ok: bool | None
    rubric_score: float | None
    scores: dict[str, bool]
    verdict: dict[str, t.Any] = Field(default_factory=dict)
    early_exit: str | None = None
    usage_split: dict[str, int] = Field(
        default_factory=dict,
        description="token counts: input / output / cacheRead / totalTokens",
    )
    total_tokens: int = 0
    cost: float = 0.0
    n_tool_calls: int = 0
    dur_s: int = 0
    error: str | None = None


class GradeReport(BaseModel):
    """The rendered deliverable over one graded runs file."""

    runs_file: str
    condition: dict[str, t.Any]
    n_tasks: int
    entries: list[ReportEntry]
    median_score: float | None
    schema_ok_rate: float | None = Field(
        description="over the entries with a contract; None for cells of "
        "contract-less tasks (a routing attempt has no verdict block)",
    )
    routing: RoutingReport | None = Field(
        default=None,
        description="the trigger-cell routing shapes, when the records are "
        "one (a mixed file is rejected)",
    )
    effort: dict[str, float] = Field(default_factory=dict)


def _total_tokens(rec: t.Any) -> int:
    """Cache-independent token count (input + cacheRead + output)."""
    usage = rec.usage
    return (usage.input or 0) + (usage.cacheRead or 0) + (usage.output or 0)


def _usage_split(rec: t.Any) -> dict[str, int]:
    """Return the token accounting a reader needs: the in/out split."""
    usage = rec.usage
    return {
        "input": (usage.input or 0) + (usage.cacheRead or 0),
        "output": usage.output or 0,
        "cacheRead": usage.cacheRead or 0,
    }


def _condition(records: list[t.Any]) -> dict[str, t.Any]:
    """Echo the cell's condition metadata (first record)."""
    if not records:
        return {}
    rec = records[0]
    return {
        "model": rec.model,
        "loaded_skills": rec.loaded_skills,
        "tasks_file": rec.tasks_file,
        "pi_version": rec.pi_version,
    }


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    if n % 2:
        return ordered[n // 2]
    return (ordered[n // 2 - 1] + ordered[n // 2]) / 2


def _routing_report(
    runs_file: str,
    records: list[t.Any],
    chosen: dict[tuple[str, int], t.Any],
) -> RoutingReport:
    """Derive the routing shapes from trigger-cell records.

    Expectations are stamped on every record (`expected_skills`, from
    the rubric); the trigger state is the replayed `triggered` map.
    Rates use the first-clean attempt per (task, run); a run with no
    clean attempt keeps verdict None and counts as a non-hit. The
    decision rule: a should-trigger task passes at hit-rate >= 0.5; a
    should-not task passes while its any-trigger rate stays <= 0.5.
    """
    by_task: dict[str, list[t.Any]] = {}
    for rec in chosen.values():
        by_task.setdefault(rec.task_id, []).append(rec)
    for recs in by_task.values():
        recs.sort(key=lambda r: r.run)
    loaded: list[str] = []
    for name in records[0].loaded_skills:
        if name not in loaded:
            loaded.append(name)

    def verdict(rec: t.Any) -> str | None:
        if not rec.clean:
            return None
        expected = set(rec.expected_skills or [])
        if any(state for name, state in rec.triggered.items() if name in expected):
            return "hit"
        if any(rec.triggered.values()):
            return "misroute"
        return "miss"

    task_rows: list[TaskRouting] = []
    for task_id, recs in sorted(by_task.items()):
        runs = [
            RunRouting(
                run=rec.run,
                clean=rec.clean,
                verdict=verdict(rec),
                triggered=rec.triggered,
            )
            for rec in recs
        ]
        n = len(runs)
        if recs[0].expected_skills:
            # hit-rate over runs (verdict None = not clean = non-hit)
            hit_rate = sum(1 for r in runs if r.verdict == "hit") / n
            decision: str = "PASS" if hit_rate >= 0.5 else "FAIL"
        else:
            # should-not: nothing may fire; the any-trigger rate decides.
            trig_rate = sum(1 for r in runs if any(r.triggered.values())) / n
            decision = "PASS" if trig_rate <= 0.5 else "FAIL"
        task_rows.append(
            TaskRouting(
                task_id=task_id,
                query=recs[0].query,
                expected_skills=recs[0].expected_skills or [],
                runs=runs,
                decision=decision,  # type: ignore[arg-type]
            ),
        )

    skill_rows: list[SkillRate] = []
    for skill in loaded:
        expected_recs = [
            rec
            for recs in by_task.values()
            for rec in recs
            if skill in (rec.expected_skills or [])
        ]
        if expected_recs:
            # A skill no task names gets no expected row — a 0-run row
            # would read as a FAIL when it is simply out of scope here.
            hits = sum(1 for rec in expected_recs if rec.triggered.get(skill))
            rate = hits / len(expected_recs)
            skill_rows.append(
                SkillRate(
                    skill=skill,
                    view="expected",
                    runs=len(expected_recs),
                    hits=hits,
                    rate=round(rate, 3),
                    decision="PASS" if rate >= 0.5 else "FAIL",
                ),
            )
        other_recs = [
            rec
            for recs in by_task.values()
            for rec in recs
            if skill not in (rec.expected_skills or [])
        ]
        other_hits = sum(1 for rec in other_recs if rec.triggered.get(skill))
        skill_rows.append(
            SkillRate(
                skill=skill,
                view="cross-trigger",
                runs=len(other_recs),
                hits=other_hits,
                rate=round(other_hits / len(other_recs), 3) if other_recs else 0.0,
                decision=None,  # informational: the misroute table
            ),
        )
    sn_recs = [
        rec
        for recs in by_task.values()
        for rec in recs
        if not (rec.expected_skills or [])
    ]
    sn_trig = sum(1 for rec in sn_recs if any(rec.triggered.values()))
    skill_rows.append(
        SkillRate(
            skill="(should-not tasks)",
            view="should-not",
            runs=len(sn_recs),
            hits=sn_trig,
            rate=round(sn_trig / len(sn_recs), 3) if sn_recs else 0.0,
            decision="PASS" if not sn_recs or sn_trig / len(sn_recs) <= 0.5 else "FAIL",
        ),
    )
    return RoutingReport(
        n_tasks=len(by_task),
        n_runs=sum(1 for rec in chosen.values() if rec.clean),
        skill_rows=skill_rows,
        tasks=task_rows,
    )


def build_grade_report(runs_file: str) -> GradeReport:
    """Select the first clean attempt per (task, run) and aggregate."""
    records = load_grade_records(Path(runs_file))
    if not records:
        msg = f"no parseable graded records in {runs_file}"
        raise ValueError(msg)

    by_key: dict[tuple[str, int], list[t.Any]] = {}
    for rec in records:
        by_key.setdefault((rec.task_id, rec.run), []).append(rec)
    chosen = {k: select_attempt(v) for k, v in by_key.items()}

    entries = [
        ReportEntry(
            task_id=rec.task_id,
            rubric_id=rec.rubric_id,
            rubric_version=rec.rubric_version,
            run=rec.run,
            clean=rec.clean,
            schema_ok=rec.schema_ok,
            rubric_score=rec.rubric_score,
            scores=rec.scores,
            verdict=rec.verdict,
            early_exit=rec.early_exit,
            usage_split=_usage_split(rec),
            total_tokens=_total_tokens(rec),
            cost=rec.usage.cost.total,
            n_tool_calls=rec.n_tool_calls,
            dur_s=rec.dur_s,
            error=rec.error,
        )
        for _, rec in sorted(chosen.items())
    ]
    scores = [e.rubric_score for e in entries if e.rubric_score is not None]
    contracted = [e for e in entries if e.schema_ok is not None]
    routing = _routing_report(runs_file, records, chosen)
    return GradeReport(
        runs_file=runs_file,
        condition=_condition(records),
        n_tasks=len({e.task_id for e in entries}),
        entries=entries,
        median_score=_median(scores),
        schema_ok_rate=(
            sum(1 for e in contracted if e.schema_ok) / len(contracted)
            if contracted
            else None
        ),
        routing=routing,
        effort={
            "tokens_median": _median([e.total_tokens for e in entries]) or 0.0,
            "cost_median": _median([e.cost for e in entries]) or 0.0,
            "tool_calls_median": _median(
                [float(e.n_tool_calls) for e in entries],
            )
            or 0.0,
            "dur_s_median": _median([float(e.dur_s) for e in entries]) or 0.0,
        },
    )


def render_report_markdown(report: GradeReport) -> str:
    """Markdown deliverable: headline table plus per-task detail."""
    cond = report.condition
    model = cond.get("model") or "default"
    skills = cond.get("loaded_skills") or []
    lines = [
        "# Graded runs report",
        "",
        f"- runs file: `{report.runs_file}`",
        f"- model: `{model}` | skills: {len(skills)}"
        + (f" ({', '.join(skills)})" if skills else " (none — counterfactual cell)"),
        f"- tasks file: `{cond.get('tasks_file')}`",
        f"- tasks: {report.n_tasks} | median rubric score: "
        f"{_fmt(report.median_score)} | schema-ok rate: "
        + (
            "n/a (no contracts)"
            if report.schema_ok_rate is None
            else f"{report.schema_ok_rate:.2f}"
        ),
        "- effort medians: "
        + ", ".join(
            # cost is dollars: 1-decimal formatting would render every
            # real-world median as 0.0
            f"{k.replace('_median', '')}="
            + (f"${v:.4f}" if k == "cost_median" else f"{v:.1f}")
            for k, v in report.effort.items()
        ),
        "- score semantics: the fraction of a rubric's mechanical checks "
        "passed (no LLM in grading); `x/y (z.zz)` = checks passed / total "
        "with the aggregate. Verdict = the answer's final fenced yaml/json "
        "block; `evidence` checks are grounded verbatim against the "
        "task's artifacts.",
        "",
        # header split for lint; the pieces concatenate to one line
        "| Task | Run | Rubric | Score | Schema | Failed | In/out tok "
        "| Cost | Calls | Wall |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for e in report.entries:
        failed = [c for c, ok in e.scores.items() if not ok]
        usage = e.usage_split
        lines.append(
            f"| `{e.task_id}` | {e.run} | `{e.rubric_id}` v{e.rubric_version} | "
            f"{sum(e.scores.values())}/{len(e.scores)} ({_fmt(e.rubric_score)}) | "
            f"{_schema_cell(e.schema_ok)} | "
            + (", ".join(f"`{c}`" for c in failed) if failed else "—")
            + f" | {usage['input']:,} / {usage['output']:,} | "
            f"${e.cost:.4f} | {e.n_tool_calls} | {e.dur_s}s |",
        )
    lines.append("")
    if report.routing is not None:
        lines += _render_routing(report.routing)
    for e in report.entries:
        lines += [f"## `{e.task_id}` (run {e.run})", ""]
        if not e.clean:
            note = e.error or ""
            if len(note) > 180:
                note = note[:180] + "…"
            lines.append(f"attempt not clean: `{note}`")
        if e.verdict:
            lines += ["```yaml", str(_yaml_block(e.verdict)), "```"]
        for check, ok in e.scores.items():
            lines.append(f"- {'✅' if ok else '❌'} `{check}`")
        u = e.usage_split
        lines += [
            "- usage: "
            f"{u['input']:,} in / {u['output']:,} out"
            + (f" / {u['cacheRead']:,} cache-read" if u["cacheRead"] else "")
            + f" — ${e.cost:.4f} | {e.n_tool_calls} tool calls | {e.dur_s}s wall",
            "",
        ]
    return "\n".join(lines)


def _schema_cell(value: bool | None) -> str:
    """Render the schema-ok table cell; None = no contract declared."""
    return "✅" if value else ("❌" if value is False else "—")


def _render_routing(routing: RoutingReport) -> list[str]:
    """Render the trigger-cell routing shapes.

    Per-skill rows plus the misroute table; the per-task/per-run
    verdicts live in the JSON report.
    """
    lines = ["## Routing", ""]
    lines += [
        "| Skill | View | Runs | Trigger rate | Decision |",
        "|---|---|---|---|---|",
    ]
    for row in routing.skill_rows:
        view = {
            "expected": "should-trigger",
            "cross-trigger": "cross-trigger",
            "should-not": "—",
        }[row.view]
        lines.append(
            f"| `{row.skill}` | {view} | {row.runs} | {row.rate:.2f} | "
            f"{row.decision or '—'} |",
        )
    lines.append("")
    return lines


def _yaml_block(verdict: dict[str, t.Any]) -> str:
    """Render the parsed verdict for audit (compact, stable order)."""
    return json.dumps(verdict, indent=2, ensure_ascii=False)


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"
