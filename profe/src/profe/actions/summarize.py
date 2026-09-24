"""`profe summarize`: aggregate a probe runs file into a report.

Ported from `eval/run_trigger_eval.py::summarize` (and the record selection
is the SAME function probe's resume uses — one definition, not two).
Expectations drive three views over the same runs (SPEC "Summarize"):

- per-skill `expected` rows: over the queries that list the skill, the
  rate of runs where THAT skill triggered (PASS at >= 0.5). An any-of
  list passes the query on any member, but each listed skill's own row
  counts the query by whether that skill triggered — a persistently
  one-sided routing shows up as a 0-rate for the other member.
- `cross-trigger` rows: the rate at which a skill triggered on runs
  where it was not expected (should-not runs included) — the misroute
  table.
- `should-not` aggregate: queries where nothing may fire.

First clean attempt per run drives every rate; a run whose every attempt
errored is preserved (verdict None, counted as a non-hit run — matching
the V6 accounting where errors never contribute trigger evidence).
"""

from __future__ import annotations

from pathlib import Path

from ..models import (
    ClassSummary,
    ConditionMetadata,
    Decision,
    Effort,
    ProbeRecord,
    QuerySummary,
    RunsTaken,
    RunSummary,
    RunVerdict,
    SkillRate,
    SummarizeOptions,
    SummarizeReport,
)
from . import _utils


def summarize_trigger_runs(options: SummarizeOptions) -> SummarizeReport:
    """Aggregate a probe runs file into the report model.

    The JSON report carries the per-query section, so a misroute or a
    near-miss that fired is diagnosable from the report alone. Every
    reported number records the condition it was produced under.
    """
    records = _utils.load_records(options.runs_file)
    if not records:
        msg = f"no parseable records in {options.runs_file}"
        raise ValueError(msg)

    grouped = _utils.group_attempts(records)
    # First clean attempt per run, else the last attempt (an all-errored
    # run stays visible with verdict None and counts as a non-hit).
    selected = {
        key: _utils.first_clean(recs) or recs[-1] for key, recs in grouped.items()
    }

    by_index: dict[int, list[tuple[int, ProbeRecord]]] = {}
    for (index, run), rec in selected.items():
        by_index.setdefault(index, []).append((run, rec))
    for run_recs in by_index.values():
        run_recs.sort()

    intended = _intended_skills(records)
    queries = [
        _query_summary(index, run_recs, grouped)
        for index, run_recs in sorted(by_index.items())
    ]
    skill_rows = _skill_rows(queries, records[0].loaded_skills)

    report = SummarizeReport(
        runs_file=options.runs_file,
        condition=_condition(records),
        intended_skills=intended,
        n_queries=len(by_index),
        n_runs=len(selected),
        skill_rows=skill_rows,
        class_summaries=_class_summaries(queries),
        queries=queries,
        effort=_effort(list(selected.values())),
        effort_by_class={
            qc: _effort(
                [
                    rec
                    for (index, _), rec in selected.items()
                    if (qc == "should-not") == (not records[index].expected_skills)
                ]
            )
            for qc in ("should-trigger", "should-not")
        },
    )
    if options.out:
        out = Path(options.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report.model_dump_json(by_alias=True, indent=2))
    return report


def _condition(records: list[ProbeRecord]) -> ConditionMetadata:
    """Build the condition block from the records.

    Every record in the file must agree (one probe run wrote them); the
    disagreement case is a corrupted file and surfaces as mixed values in
    the first record only.
    """
    return ConditionMetadata(
        model=records[0].model,
        loaded_skills=records[0].loaded_skills,
        queries_file=records[0].queries_file,
        max_tool_calls=records[0].max_tool_calls,
        num_runs=records[0].num_runs,
        retries=records[0].retries,
        ts=max(r.ts for r in records),
    )


def _intended_skills(records: list[ProbeRecord]) -> list[str]:
    """Distinct skills any query names, in first-appearance order."""
    seen: list[str] = []
    for rec in sorted(records, key=lambda r: (r.index, r.run, r.attempt)):
        for name in rec.expected_skills:
            if name not in seen:
                seen.append(name)
    return seen


def _verdict(rec: ProbeRecord) -> RunVerdict | None:
    expected = set(rec.expected_skills)
    hit = any(state for name, state in rec.triggered.items() if name in expected)
    if not rec.clean:
        return None  # every attempt errored: no trigger evidence
    if hit:
        return "hit"
    if any(rec.triggered.values()):
        return "misroute"  # only non-expected skills fired: a miss
    return "miss"


def _query_summary(
    index: int,
    run_recs: list[tuple[int, ProbeRecord]],
    grouped: dict[tuple[int, int], list[ProbeRecord]],
) -> QuerySummary:
    first = run_recs[0][1]
    runs = [
        RunSummary(
            run=run,
            attempt=rec.attempt,
            clean=rec.clean,
            verdict=_verdict(rec),
            triggered=rec.triggered,
        )
        for run, rec in run_recs
    ]
    n = len(runs)
    hit_runs = sum(1 for r in runs if r.verdict == "hit")
    rate = hit_runs / n
    if first.expected_skills:
        decision: Decision = "PASS" if rate >= 0.5 else "FAIL"
    else:
        # should-not: nothing may fire; any-trigger rate above 0.5 fails
        decision = "PASS" if rate <= 0.5 else "FAIL"
    return QuerySummary(
        index=index,
        query=first.query,
        expected_skills=first.expected_skills,
        query_class="should-not" if not first.expected_skills else "should-trigger",
        runs=runs,
        decision=decision,
        timeouts=sum(1 for _, rec in run_recs if rec.timed_out),
        errors=sum(1 for _, rec in run_recs if rec.errored),
        retries_used=sum(len(grouped[(index, run)]) - 1 for run, _ in run_recs),
    )


def _skill_rows(queries: list[QuerySummary], loaded: list[str]) -> list[SkillRate]:
    rows: list[SkillRate] = []
    for skill in loaded:
        expected_runs = [
            r for q in queries if skill in q.expected_skills for r in q.runs
        ]
        if expected_runs:
            # A skill no query names gets no expected row — a 0-run row
            # would read as a FAIL when it is simply out of scope here.
            hits = sum(1 for r in expected_runs if r.triggered.get(skill))
            rate = hits / len(expected_runs)
            rows.append(
                SkillRate(
                    skill=skill,
                    view="expected",
                    runs=len(expected_runs),
                    hits=hits,
                    rate=round(rate, 3),
                    decision="PASS" if rate >= 0.5 else "FAIL",
                )
            )
        other_runs = [
            r for q in queries if skill not in q.expected_skills for r in q.runs
        ]
        other_hits = sum(1 for r in other_runs if r.triggered.get(skill))
        rows.append(
            SkillRate(
                skill=skill,
                view="cross-trigger",
                runs=len(other_runs),
                hits=other_hits,
                rate=round(other_hits / len(other_runs), 3) if other_runs else 0.0,
                decision=None,  # informational: the misroute table
            )
        )
    sn_queries = [q for q in queries if q.query_class == "should-not"]
    sn_runs = [r for q in sn_queries for r in q.runs]
    sn_trig = sum(1 for r in sn_runs if any(r.triggered.values()))
    sn_rate = sn_trig / len(sn_runs) if sn_runs else 0.0
    rows.append(
        SkillRate(
            skill="(should-not queries)",
            view="should-not",
            runs=len(sn_runs),
            hits=sn_trig,
            rate=round(sn_rate, 3),
            decision="PASS" if sn_rate <= 0.5 else "FAIL",
        )
    )
    return rows


def _class_summaries(queries: list[QuerySummary]) -> list[ClassSummary]:
    summaries: list[ClassSummary] = []
    for qc in ("should-trigger", "should-not"):
        entries = [q for q in queries if q.query_class == qc]
        taken = [len(q.runs) for q in entries]
        passed = sum(1 for q in entries if q.decision == "PASS")
        summaries.append(
            ClassSummary(
                query_class=qc,
                passed=passed,
                total=len(entries),
                pass_rate=round(passed / len(entries), 3) if entries else 0.0,
                runs_taken=RunsTaken(
                    min=min(taken),
                    median=_median(taken),
                    max=max(taken),
                )
                if taken
                else None,
            )
        )
    return summaries


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    if not n:
        return 0.0
    if n % 2:
        return float(ordered[n // 2])
    return (ordered[n // 2 - 1] + ordered[n // 2]) / 2


def _effort(records: list[ProbeRecord]) -> Effort:
    if not records:
        return Effort()
    return Effort(
        # totalTokens is cache-independent: input alone swings with provider
        # cache luck (uncached remainder vs cacheRead), and its median is
        # therefore not a stable measure.
        total_tokens=_median(
            [
                (r.usage.input or 0)
                + (getattr(r.usage, "cacheRead", 0) or 0)
                + (r.usage.output or 0)
                for r in records
            ]
        ),
        output_tokens=_median([r.usage.output for r in records]),
        # Cost is kept in the report for verification; it is 0.0 for runs
        # recorded before the nested-cost fix in _pi.py.
        cost=round(_median([r.usage.cost_total for r in records]), 4),
        tool_calls=_median([r.n_tool_calls for r in records]),
        wall_s=_median([r.dur_s for r in records]),
    )


def render_summarize_table(report: SummarizeReport) -> str:
    """Render the report in the README's table format.

    One row per skill and query class, then a footer carrying the
    condition and effort medians.
    """
    widths = (
        max([len("skill")] + [len(row.skill) for row in report.skill_rows]),
        13,  # "cross-trigger" is the longest class label
    )
    header = (
        f"{'skill':<{widths[0]}}  {'class':<{widths[1]}}"
        f"  {'runs':>5}  {'pass-rate':>9}  decision"
    )
    lines = [header]
    cross = [r for r in report.skill_rows if r.view == "cross-trigger"]
    for row in report.skill_rows:
        if row.view == "cross-trigger":
            continue  # aggregated below: the table's misroute row
        label = "should-trigger" if row.view == "expected" else "—"
        decision = row.decision if row.decision is not None else "—"
        lines.append(
            f"{row.skill:<{widths[0]}}  {label:<{widths[1]}}"
            f"  {row.runs:>5}  {row.rate:>9.2f}  {decision}"
        )
    if cross:
        # The table's misroute row: every loaded skill on the runs where it
        # was not expected (should-not runs included). Per-skill detail
        # stays in the JSON report's skill_rows.
        runs = sum(r.runs for r in cross)
        hits = sum(r.hits for r in cross)
        lines.append(
            f"{'(all others)':<{widths[0]}}  {'cross-trigger':<{widths[1]}}"
            f"  {runs:>5}  {hits / runs:>9.2f}  —"
        )
    condition = report.condition
    model = condition.model or "default"
    runs = "auto" if condition.num_runs == ":auto:" else str(condition.num_runs)
    lines.append("")
    lines.append(
        f"protocol: serial, neutral cwd, no hint | model: {model} "
        f"| skills: {len(condition.loaded_skills)}"
    )
    lines.append(
        f"early exit: on first skill read / "
        f"max_tool_calls={condition.max_tool_calls} | runs: {runs} "
        f"| retries: {condition.retries}"
    )
    lines.append(f"effort (median/attempt): {_fmt_effort(report.effort)}")
    return "\n".join(lines)


def _fmt_effort(effort: Effort) -> str:
    def tok(value: float | None, suffix: str) -> str:
        if value is None:
            return f"– tok {suffix}"
        if value >= 1000:
            return f"{value / 1000:.1f}k tok {suffix}"
        return f"{value:.0f} tok {suffix}"

    # 2 decimals, but a nonzero cost that rounds away is not zero — the
    # tilde keeps sub-cent medians visible without extra digits.
    cost = "~$0.00" if round(effort.cost or 0.0, 2) == 0.0 else f"${effort.cost:.2f}"
    return (
        f"{tok(effort.total_tokens, 'total')}, {tok(effort.output_tokens, 'out')}, "
        f"{cost}, {effort.tool_calls:.0f} tool calls, "
        f"{effort.wall_s:.0f}s"
    )
