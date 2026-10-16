"""`profe compile`: turn a queries file into trigger-cell tasks + rubrics.

The compiler dissolves the old probe/summarize collection path: a
trigger cell is an ordinary cell — one task per query, one rubric per
distinct expectation set — so `run`/`grade`/`report` need no special
cases for it. Offline generation (no pi anywhere near it); deterministic
(ids are content hashes, so re-compiles are stable and idempotent):

- task id `q-<hash8(query)>` — stable across description edits that do
  not touch the query text, so records stay attributable;
- rubric id `trig-<hash8(expectations)>` — one rubric shared by every
  query with the same expectation set; a should-trigger set becomes a
  `triggered_any_of` check, an empty set a `triggered_none` check.

The startup checks a routing cell has always enforced move here:
the schema declaration is mandatory, every `expected_skills` name
must be among the loaded skills (a query expecting a skill that is
not loaded would silently never pass), and no field may hold a bare
string where a list is required.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import yaml
from pydantic import ValidationError

from ..models import (
    CompileOptions,
    CompileStats,
    QueriesFile,
    Query,
    Rubric,
    RubricCheck,
    Task,
)
from ._utils import resolve_skills


def compile_trigger_cells(
    options: CompileOptions,
    progress: Callable[[str], None] | None = None,
) -> CompileStats:
    """Compile the queries file into tasks + rubrics; returns what it wrote."""
    say = progress or (lambda _msg: None)
    skills = resolve_skills(options.skills)
    queries = _load_queries(options.queries)
    _check_queries(queries, skills)

    groups: dict[tuple[str, ...], list[Query]] = {}
    for query in queries:
        groups.setdefault(tuple(sorted(query.expected_skills)), []).append(query)

    rubric_ids: dict[tuple[str, ...], str] = {}
    rubrics_dir = Path(options.rubrics_dir)
    rubrics_dir.mkdir(parents=True, exist_ok=True)
    for expected in sorted(groups):
        if expected:
            checks = [
                RubricCheck(
                    id="expected-triggered",
                    type="triggered_any_of",
                    patterns=list(expected),
                ),
            ]
            description = f"compiled trigger rubric: any-of {list(expected)}"
        else:
            checks = [RubricCheck(id="nothing-triggered", type="triggered_none")]
            description = "compiled trigger rubric: nothing should trigger (should-not)"
        rubric_id = (
            "trig-"
            + hashlib.sha256(json.dumps(list(expected)).encode()).hexdigest()[:8]
        )
        rubric = Rubric(id=rubric_id, description=description, checks=checks)
        path = rubrics_dir / f"{rubric_id}.yaml"
        path.write_text(
            yaml.safe_dump(
                rubric.model_dump(mode="json", exclude_defaults=True), sort_keys=False
            ),
        )
        rubric_ids[expected] = rubric_id
        say(f"rubric {rubric_id} -> {path}")

    tasks: list[Task] = []
    seen: dict[str, tuple[str, ...]] = {}
    for query in queries:
        key = tuple(sorted(query.expected_skills))
        task_id = "q-" + hashlib.sha256(query.query.encode()).hexdigest()[:8]
        if task_id in seen:
            if seen[task_id] != key:
                msg = (
                    f"{options.queries}: task id collision between different "
                    f"expectation sets ({task_id!r} for {seen[task_id]} and {key}) — "
                    "differentiate the query texts"
                )
                raise ValueError(msg)
            continue  # identical (query, expectations): one task
        seen[task_id] = key
        tasks.append(Task(id=task_id, prompt=query.query, rubric=rubric_ids[key]))

    tasks_path = Path(options.tasks)
    tasks_path.parent.mkdir(parents=True, exist_ok=True)
    with tasks_path.open("w") as fh:
        for task in tasks:
            fh.write(task.model_dump_json(exclude_none=True) + "\n")
    say(f"{len(tasks)} tasks -> {tasks_path}")
    return CompileStats(
        queries_file=options.queries,
        tasks_file=str(tasks_path),
        rubrics_dir=str(rubrics_dir),
        n_queries=len(queries),
        n_tasks=len(tasks),
        n_rubrics=len(rubric_ids),
    )


def _check_queries(queries: list[Query], skills: list) -> None:
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


def _load_queries(path: str) -> list[Query]:
    """Parse a queries file; the schema declaration is mandatory.

    A missing file is a clean `exit 2` (boundary PoCs hit this first:
    a forgot-to-push input must not surface as a raw traceback).
    """
    try:
        text = Path(path).read_text()
    except OSError as exc:
        msg = f"{path}: cannot read queries file ({exc})"
        raise ValueError(msg) from exc
    try:
        root = json.loads(text)
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
