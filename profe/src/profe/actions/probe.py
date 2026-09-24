"""`profe probe`: execute trigger queries, record attempts.

Ported from `eval/run_trigger_eval.py::collect` plus the escalation and
resume logic validated there (the fine print):

- escalation: one run per query, escalate (cap 3) only on non-decisive
  outcomes — a miss while a pass is still reachable, a should-not run
  that triggered (corroborate the false positive), an errored/timed-out
  run (errors are not trigger evidence), or a boundary rate (exactly
  0.5). Stop even when the naive rule says escalate: should-trigger 0/2
  at cap 3 (best case 1/3 < 0.5) and should-not 2/2 (2/3 and 3/3 fail
  too — the verdict is already fixed). Always run at least once.
- resume keyed on "query has a decisive conclusion"; a run that exists
  in the file but has no clean attempt is redone; recorded outcomes are
  replayed through the same escalation function used for fresh runs.
- fixed-N mode (`--num-runs N`) is the same loop with escalation checks
  bypassed — for instrument validation and per-(query, run) agreement
  checks.

Startup checks: invalid skill paths are a hard error (a silently missing
competitor corrupts cross-trigger accounting), every `expected_skills`
name must be among the loaded skills, and names must be unique.
"""

from __future__ import annotations

import json
import re
import time
import typing as t
from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError

from ..models import (
    ProbeOptions,
    ProbeRecord,
    ProbeRunStats,
    QueriesFile,
    Query,
    SkillRef,
)
from . import _pi, _utils

Outcome = bool | t.Literal["error"]  # hit/miss per run, or not trigger evidence

RUN_CAP = 3  # adaptive escalation cap


def _resolve_skills(paths: list[str]) -> list[SkillRef]:
    """Resolve `--skills` entries to pinned skill refs.

    Accepts skill directories (SKILL.md inside) or SKILL.md files (the
    forwarded path is the parent directory). Names come from the
    frontmatter `name:` field, falling back to the directory name.
    Invalid paths are a hard error: a silently missing competitor
    corrupts cross-trigger accounting.
    """
    refs: list[SkillRef] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            md = path / "SKILL.md"
            if not md.is_file():
                msg = f"no SKILL.md in skill directory: {raw}"
                raise ValueError(msg)
            name, forward = _frontmatter_name(md) or path.name, path.resolve()
        elif path.is_file():
            name = _frontmatter_name(path) or path.parent.name
            forward = path.parent.resolve()
        else:
            msg = f"skill path does not exist: {raw}"
            raise ValueError(msg)
        refs.append(SkillRef(name=name, path=str(forward)))
    names = [ref.name for ref in refs]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        msg = f"duplicate skill names across --skills paths: {duplicates}"
        raise ValueError(msg)
    return refs


def _frontmatter_name(md: Path) -> str | None:
    """Read the frontmatter `name:` field, None when absent."""
    match = re.search(r"^name:\s*(\S+)", md.read_text(errors="replace"), re.M)
    return match.group(1) if match else None


def _check_queries(queries: list[Query], skills: list[SkillRef]) -> None:
    """Check that every expected skill is loaded.

    A query expecting a skill that is not loaded would silently never
    pass.
    """
    known = {ref.name for ref in skills}
    unknown = sorted(
        {name for q in queries for name in q.expected_skills} - known,
    )
    if unknown:
        msg = f"queries expect skills that are not loaded: {unknown}"
        raise ValueError(msg)


def _outcome(rec: ProbeRecord) -> Outcome:
    """Derive one run's outcome against its query's expectations.

    For a should-trigger query: True iff an expected skill triggered (a
    misroute — only non-expected skills fired — is a miss). For a
    should-not query: True iff ANYTHING triggered (the false-positive
    signal the escalation must corroborate). Errors are not trigger
    evidence even when a trigger was preserved (§7).
    """
    if not rec.clean:
        return "error"
    expected = set(rec.expected_skills)
    if not expected:
        return any(rec.triggered.values())
    return any(state for name, state in rec.triggered.items() if name in expected)


def _escalate(should: bool, outcomes: list[Outcome], cap: int) -> str:  # noqa: PLR0911
    """Decide whether the adaptive loop escalates.

    §7 rules, edge cases included; `should` is "the query has non-empty
    expectations". The explicit enumeration of cases is the point —
    complexity thresholds are suppressed, not refactored away.
    """
    taken = len(outcomes)
    if taken >= cap:
        return "stop"
    if taken == 0:
        return "escalate"  # always run at least once
    if any(o == "error" for o in outcomes):
        return "escalate"
    hits = sum(1 for o in outcomes if o is True)
    if not should:
        # pass = no trigger, rate <= 0.5; a clean non-trigger is decided at
        # once, any observed trigger needs corroboration while a pass is
        # still reachable within the cap (2t <= cap: e.g. 2/2 at cap 3 is
        # already a decided fail — 2/3 and 3/3 fail too)
        return "escalate" if (hits and 2 * hits <= cap) else "stop"
    rate = hits / taken
    if rate > 0.5:
        return "stop"  # clean pass, cannot fall to a fail within the cap
    if (hits + (cap - taken)) / cap < 0.5:
        return "stop"  # pass unreachable even if every remaining run hits
    return "escalate"  # miss that could still pass, or boundary rate


def probe_trigger_runs(
    options: ProbeOptions,
    progress: Callable[[str], None] | None = None,
) -> ProbeRunStats:
    """Run every query serially against the pinned skills.

    Appends one record per attempt to `options.out` and returns the run
    statistics. Crash-safe and resumable: every finished attempt is on
    disk immediately, and a query with a decisive conclusion is skipped
    on re-run. Serial by protocol — every relaxed-parallelism generation
    produced degraded provider completions that look exactly like skill
    failures. `progress` receives one heartbeat line per attempt (the
    CLI decides how to show it; the action never prints).
    """

    def say(message: str) -> None:
        if progress is not None:
            progress(message)

    skills = _resolve_skills(options.skills)
    queries = _load_queries(options.queries)
    _check_queries(queries, skills)

    out = Path(options.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    grouped = _utils.group_attempts(_utils.load_records(out)) if out.exists() else {}

    adaptive = options.num_runs == ":auto:"
    cap = RUN_CAP if adaptive else int(options.num_runs)
    t0 = time.monotonic()
    n_attempts = n_runs = errored_attempts = concluded = 0

    def execute_run(index: int, query: Query, run: int) -> ProbeRecord:
        """Run all attempts for one (index, run).

        Each attempt is appended to the JSONL as it finishes; returns the
        attempt that concluded the run (first clean, else the last one).
        """
        final: ProbeRecord | None = None
        for attempt in range(options.retries + 1):
            started = time.monotonic()
            core = _pi.run_attempt(
                index=index,
                query=query.query,
                expected_skills=query.expected_skills,
                run=run,
                attempt=attempt,
                skills=skills,
                model=options.model,
                timeout=options.timeout,
                max_tool_calls=options.max_tool_calls,
            )
            record = ProbeRecord(
                **core.model_dump(),
                model=options.model,
                loaded_skills=[ref.name for ref in skills],
                queries_file=options.queries,
                max_tool_calls=options.max_tool_calls,
                num_runs=options.num_runs,
                retries=options.retries,
                ts=time.strftime("%Y-%m-%d %H:%M:%S"),
            )
            with out.open("a") as f:
                f.write(record.model_dump_json(by_alias=True) + "\n")
            state = (
                "OK" if record.clean else ("TIMEOUT" if record.timed_out else "ERRORED")
            )
            if record.early_exit is not None:
                state += f"/{record.early_exit}"
            err_note = "" if not record.error else f" err={str(record.error)[:120]}"
            at_note = (
                ""
                if record.trigger_call_index is None
                else f" at#{record.trigger_call_index}"
            )
            say(
                f"[run {record.ts[11:]}] q{index + 1} run {run + 1}/{cap} "
                f"attempt {attempt} -> {state}{err_note} "
                f"trig={sum(record.triggered.values())}{at_note} "
                f"dur={round(time.monotonic() - started)}s"
            )
            final = record
            if record.clean:
                break
        assert final is not None  # the loop runs at least once
        return final

    for index, query in enumerate(queries):
        outcomes: list[Outcome] = []
        for run in range(cap):
            attempts = grouped.get((index, run))
            if attempts is not None:
                clean = _utils.first_clean(attempts)
                if clean is not None:
                    outcomes.append(_outcome(clean))
                    continue
                # ran before but never clean -> redo this run
            elif (
                adaptive
                and _escalate(bool(query.expected_skills), outcomes, cap) == "stop"
            ):
                break  # the loop concluded: resume replays the same decision
            record = execute_run(index, query, run)
            n_runs += 1
            n_attempts += record.attempt + 1
            errored_attempts += bool(record.errored) + record.retries_seen
            outcomes.append(_outcome(record))
        concluded += 1
        say(
            f"q{index + 1}/{len(queries)} concluded after {len(outcomes)} "
            f"run(s): {outcomes}"
        )

    return ProbeRunStats(
        out_file=str(out),
        queries_file=options.queries,
        n_queries=len(queries),
        concluded=concluded,
        n_runs=n_runs,
        n_attempts=n_attempts,
        errored_attempts=errored_attempts,
        dur_s=round(time.monotonic() - t0),
    )


def _load_queries(path: str) -> list[Query]:
    """Parse a queries file; the schema declaration is mandatory."""
    try:
        root = json.loads(Path(path).read_text())
    except json.JSONDecodeError as exc:
        msg = f"{path}: not valid JSON ({exc.msg} at line {exc.lineno})"
        raise ValueError(msg) from exc
    if not isinstance(root, dict) or "schema" not in root.keys():
        msg = (
            f"{path}: queries file does not declare its schema — "
            'rejected (expected {"schema": 1, "queries": [...]}; '
            "see profe/README.md)"
        )
        raise ValueError(msg)
    try:
        return QueriesFile.model_validate(root).queries
    except ValidationError as exc:
        msg = f"{path}: invalid queries file ({_validation_errors(exc)})"
        raise ValueError(msg) from exc


def _validation_errors(exc: ValidationError) -> str:
    """Render a pydantic error as one line per error, field-located."""
    parts = [
        f"{'.'.join(str(loc) for loc in err['loc']) or '<file>'}: {err['msg']}"
        for err in exc.errors()
    ]
    return "; ".join(parts)
