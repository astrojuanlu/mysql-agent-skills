"""`profe grade`: score a capture bundle mechanically (offline grader).

One bundle = one attempt: the contract-less trigger cell and the
rubric task cell are the same shape, differing only in what the
rubric addresses. Grading is mechanical and LLM-free, over four
transcript regions:

- the verdict block is the last fenced ```yaml/```json block of the
  answer, parsed with yaml.safe_load — `schema_ok` records format
  compliance separately from correctness (a weak model may fail at
  producing the block rather than at the substance); None when the
  task declares no contract (a compiled trigger cell yields before
  any block could exist);
- `field_*` checks operate on the parsed block (equals / regex / not-
  regex / presence);
- `evidence_in_artifacts` grounds every quoted line against the task's
  artifacts, grep -F semantics — a correct verdict with fabricated
  evidence still fails;
- `precheck_field_equals` compares a verdict field to a field of the
  task precheck's JSON output — recorded at run time inside the
  boundary, never baked into the rubric and never executed by the
  grader;
- the `skill_triggered` family (skill_triggered / triggered_any_of /
  triggered_none) reads the `triggered` map the session replay
  computes — routing cells are graded by the same machinery, and the
  expectations are stamped into the record (`expected_skills`) from
  the rubric so the report's routing shapes stay runs-file-only.

The attempt is not run here: it was collected by `profe run` inside
the caller's boundary and handed over as a capture bundle — pi's own
session file (`session.jsonl`, verbatim), the sidecar (`capture.json`:
task/run identity, wall time, pi exit code and version, the forwarded
skill paths, any stop reason) and the exact prompt (`prompt.txt`),
plus the task precheck's recorded output when the task declares one.
The session replays into an `AttemptCore` through the same pi-format
consumer (`_pi.StreamAggregator`) the live runner feeds; the model is
read off the session's message payloads, so the record carries what
actually ran, not what a caller asked for. Trigger detection matches
the sidecar's forwarded skill paths — the runner's paths, not
caller-side guesses. Grade launches nothing — no pi, no subprocess, no
network.

Resume: a (task, run) pair with a clean attempt is skipped on re-run.
Serial execution, pinned skill loading (`[]` = the skills-off
counterfactual cell) — the same protocol constants as every cell.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import typing as t
from collections.abc import Callable
from pathlib import Path

import yaml
from pi_agent_json import PiSessionParseError, parse_session_entry
from pydantic import BaseModel, ConfigDict, Field

from ..models import (
    AttemptCore,
    CaptureSidecar,
    EarlyExit,
    GradeOptions,
    GradeRecord,
    GradeRunStats,
    Rubric,
    RubricCheck,
    SkillRef,
    Task,
)
from . import _pi
from ._utils import resolve_skills

_VERDICT_FENCE = re.compile(r"```(?:yaml|json)\s*\n(.*?)```", re.DOTALL)


class GradedVerdict(BaseModel):
    """The extraction result: parsed fields plus format compliance."""

    model_config = ConfigDict(protected_namespaces=())

    fields: dict[str, t.Any] = Field(default_factory=dict)
    schema_ok: bool = False


def load_grade_records(path: Path) -> list[GradeRecord]:
    """Read a grade runs JSONL, tolerating torn lines (crash-safe appends).

    Grade files are read as `GradeRecord`s (probe's loader would strip
    the grading fields); a line that does not parse is skipped, not
    fatal — the complete attempts are already on disk.
    """
    records: list[GradeRecord] = []
    if not path.exists():
        return records
    for line in path.read_text().splitlines():
        try:
            records.append(GradeRecord.model_validate_json(line))
        except Exception:  # noqa: BLE001 — torn/partial line, skip it
            continue
    return records


def load_tasks(path: str) -> list[Task]:
    """Read a tasks JSONL; ids must be unique and the file well-formed."""
    tasks: list[Task] = []
    seen: set[str] = set()
    try:
        text = Path(path).read_text()
    except OSError as exc:
        msg = f"{path}: cannot read tasks file ({exc})"
        raise ValueError(msg) from exc
    for n, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            task = Task.model_validate(json.loads(line))
        except Exception as exc:  # noqa: BLE001 — report and fail loudly
            msg = f"{path}:{n}: invalid task ({exc})"
            raise ValueError(msg) from exc
        if task.id in seen:
            msg = f"{path}:{n}: duplicate task id {task.id!r}"
            raise ValueError(msg)
        seen.add(task.id)
        tasks.append(task)
    if not tasks:
        msg = f"{path}: no tasks"
        raise ValueError(msg)
    return tasks


def load_rubrics(dir_path: str, tasks: list[Task]) -> dict[str, Rubric]:
    """Load the rubrics the tasks reference; extras on disk are fine."""
    rubrics: dict[str, Rubric] = {}
    root = Path(dir_path)
    if not root.is_dir():
        msg = f"rubrics directory does not exist: {dir_path}"
        raise ValueError(msg)
    for task in tasks:
        if task.rubric in rubrics:
            continue
        path = root / f"{task.rubric}.yaml"
        if not path.is_file():
            msg = f"missing rubric for task {task.id!r}: {path}"
            raise ValueError(msg)
        try:
            rubrics[task.rubric] = Rubric.model_validate(
                yaml.safe_load(path.read_text()),
            )
        except Exception as exc:  # noqa: BLE001 — report and fail loudly
            msg = f"{path}: invalid rubric ({exc})"
            raise ValueError(msg)
        if rubrics[task.rubric].id != task.rubric:
            msg = f"{path}: rubric id {rubrics[task.rubric].id!r} != file name"
            raise ValueError(msg)
    return rubrics


def _sha256(text: str) -> str:
    """Content hash for the record: contract/rubric identity per attempt."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def extract_verdict(answer: str) -> GradedVerdict:
    """Find and parse the verdict block: the last fenced yaml/json block."""
    blocks = _VERDICT_FENCE.findall(answer)
    if not blocks:
        return GradedVerdict()
    try:
        parsed = yaml.safe_load(blocks[-1])
    except yaml.YAMLError:
        return GradedVerdict()
    return (
        GradedVerdict(fields=parsed, schema_ok=True)
        if isinstance(parsed, dict)
        else GradedVerdict()
    )


def _field_value(verdict: GradedVerdict, field: str) -> t.Any:
    """One verdict field; dotted paths reach into nested blocks."""
    value: t.Any = verdict.fields
    for part in field.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _unquote(text: str) -> str:
    """Unwrap one pair of matching outer quotes — never blind-strip.

    `str.strip("'")` peels apostrophes that are CONTENT (MySQL users like
    `'user'@'%'` end in an apostrophe) and breaks grounding; only a
    symmetric outer pair is decoration.
    """
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    return text


def _quotes(value: t.Any) -> list[str]:
    """Normalize a verdict field to a list of quote strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _norm_token(token: str) -> str:
    """Strip terminal punctuation from a token — never quotes/apostrophes.

    De-wrapped quotes drop sentence-final periods; quotes/apostrophes are
    content (MySQL users like `'user'@'%'`) and must survive.
    """
    return token.strip(".,;:!?…()[]<>")


def _subsequence(needle: list[str], haystack: list[str]) -> bool:
    """In-order subsequence test with gaps (two-pointer)."""
    it = iter(_norm_token(tok) for tok in haystack)
    return all(_norm_token(token) in it for token in needle)


def _check(  # noqa: PLR0911 — one return per check type is the point
    check: RubricCheck,
    verdict: GradedVerdict,
    artifact_texts: list[str],
    precheck_json: dict[str, t.Any] | None,
    triggered: dict[str, bool],
) -> bool:
    """Apply one mechanical check. No LLM anywhere in this function."""
    if check.type == "skill_triggered":
        expected = check.value if isinstance(check.value, bool) else True
        return bool(triggered.get(check.field, False)) == expected
    if check.type == "triggered_any_of":
        return any(triggered.get(name, False) for name in check.patterns)
    if check.type == "triggered_none":
        return not any(triggered.values())
    value = _field_value(verdict, check.field)
    if check.type == "field_present":
        return value is not None and value != ""
    if check.type == "field_equals":
        return value is not None and str(value).strip() == str(check.value)
    if check.type == "field_regex":
        text = json.dumps(value) if not isinstance(value, str) else value
        if not text:
            return False
        return all(re.search(p, text, re.IGNORECASE) for p in check.patterns)
    if check.type == "field_not_regex":
        text = json.dumps(value) if not isinstance(value, str) else value
        return not any(re.search(p, text, re.IGNORECASE) for p in check.patterns)
    if check.type == "evidence_in_artifacts":
        quotes = [_unquote(q.strip()) for q in _quotes(value)]
        if sum(1 for q in quotes if q) < check.min_quotes:
            return False
        # Tolerant-but-strict grounding. Real agents' quotes deviate from
        # literal artifact text in three benign ways: quotes span wrapped
        # artifact lines, skip an intervening caret/comment line, join two
        # lines with " / ", or append an annotation after " # ". Split on
        # the join markers, then ground each fragment as an in-order word
        # subsequence of some artifact (gaps allowed — the artifact may
        # interleave unrelated lines). Paraphrase or fabricated lines
        # still fail: their word sequences do not appear in order.
        collapsed = [re.sub(r"\s+", " ", a) for a in artifact_texts]
        fragments = [
            re.sub(r"\s+", " ", segment).strip()
            for quote in quotes
            for segment in re.split(r" (?:/|#) ", quote)
        ]
        tokenized = [a.split() for a in collapsed]
        grounded = [
            any(
                _subsequence(fragment.split(), artifact_tokens)
                for artifact_tokens in tokenized
            )
            for fragment in fragments
            if fragment
        ]
        return bool(grounded) and all(grounded)
    if check.type == "precheck_field_equals":
        if precheck_json is None:
            return False
        precheck_value = precheck_json.get(check.precheck_field or check.field)
        return (
            precheck_value is not None
            and value is not None
            and str(value).strip() == str(precheck_value).strip()
        )
    return False  # unreachable: CheckType is closed


def apply_rubric(
    rubric: Rubric,
    verdict: GradedVerdict,
    artifact_texts: list[str],
    precheck_json: dict[str, t.Any] | None,
    triggered: dict[str, bool],
) -> dict[str, bool]:
    """Score every rubric check; the dict preserves check order."""
    return {
        check.id: _check(check, verdict, artifact_texts, precheck_json, triggered)
        for check in rubric.checks
    }


def rubric_expected_skills(rubric: Rubric) -> list[str] | None:
    """Return the trigger expectations a rubric claims, None when it makes none.

    Derived from the checks (the compiler emits one decision check per
    expectation set): the any-of set is the union of
    `triggered_any_of` patterns and positively-claimed `skill_triggered`
    fields; a `triggered_none` check is the should-not class (empty
    list). This is what the record's `expected_skills` field is stamped
    from, so the report's routing shapes stay runs-file-only.
    """
    for check in rubric.checks:
        if check.type == "triggered_none":
            return []
    skills: list[str] = []
    for check in rubric.checks:
        if check.type == "triggered_any_of":
            skills.extend(check.patterns)
        elif check.type == "skill_triggered" and check.value is not False:
            skills.append(check.field)
    return skills or None


def select_attempt(records: list[GradeRecord]) -> GradeRecord:
    """Pick the attempt that drives scores for one (task, run).

    Preference: a clean attempt first; else an answer-complete attempt
    (a parseable verdict block — the terminal API error only killed an
    empty epilogue turn, e.g. a rate-limited free endpoint); else the
    first record. Earliest wins within a preference class. `schema_ok`
    is None for contract-less tasks and sorts with False.
    """
    if not records:
        msg = "select_attempt: empty records"
        raise ValueError(msg)
    best = records[0]
    for rec in records[1:]:
        best_key = (best.clean, best.schema_ok is True)
        rec_key = (rec.clean, rec.schema_ok is True)
        if rec_key > best_key:
            best = rec
    return best


class Capture(BaseModel):
    """A loaded capture directory: session, sidecar, exact prompt.

    `precheck_json`/`precheck_output` come from the bundle: the `run`
    verb executes the task precheck inside the boundary (§12) and
    records the output. A task that declares a precheck without a
    bundle carrying one is a hard error in `grade_tasks` — the grader
    is offline and cannot execute prechecks itself.
    """

    root: Path
    sidecar: CaptureSidecar
    session: Path
    stream: Path | None
    prompt: str
    precheck_json: dict[str, t.Any] | None = None
    precheck_output: str | None = None


def _load_capture(capture_dir: str) -> Capture:
    """Read one capture directory (session + sidecar + prompt + precheck?).

    Precheck files are optional at load time (hand-assembled bundles
    have none); whether the graded task may lack them is decided by the
    task, in `grade_tasks`.
    """
    root = Path(capture_dir)
    session = root / "session.jsonl"
    sidecar_path = root / "capture.json"
    prompt_file = root / "prompt.txt"
    missing = [
        str(path) for path in (session, sidecar_path, prompt_file) if not path.is_file()
    ]
    if missing:
        msg = (
            f"capture directory incomplete, missing {', '.join(missing)}: {capture_dir}"
        )
        raise ValueError(msg)
    try:
        sidecar = CaptureSidecar.model_validate_json(sidecar_path.read_text())
    except Exception as exc:  # noqa: BLE001 — fail loudly with the path
        msg = f"{sidecar_path}: invalid capture sidecar ({exc})"
        raise ValueError(msg) from exc
    precheck_json: dict[str, t.Any] | None = None
    precheck_output: str | None = None
    precheck_out_path = root / "precheck-output.txt"
    precheck_json_path = root / "precheck.json"
    if precheck_out_path.is_file():
        precheck_output = precheck_out_path.read_text(errors="replace")
    if precheck_json_path.is_file():
        try:
            parsed = json.loads(precheck_json_path.read_text())
        except json.JSONDecodeError as exc:
            msg = f"{precheck_json_path}: invalid precheck JSON ({exc})"
            raise ValueError(msg) from exc
        precheck_json = parsed if isinstance(parsed, dict) else None
    return Capture(
        root=root,
        sidecar=sidecar,
        session=session,
        stream=root / "stream.jsonl" if (root / "stream.jsonl").is_file() else None,
        prompt=prompt_file.read_text(),
        precheck_json=precheck_json,
        precheck_output=precheck_output,
    )


def _replay_attempt(  # noqa: PLR0913 — one capture bundle + replay knobs
    session_file: str,
    prompt: str,
    skills: list[t.Any],
    *,
    run: int,
    dur_seconds: float,
    exit_code: int | None,
    early_exit: EarlyExit | None = None,
    stream_file: str | None = None,
) -> tuple[AttemptCore, str | None]:
    """Replay a captured attempt into (AttemptCore, model).

    Default capture: pi's own session file, copied verbatim by the
    runner. When the runner killed the attempt (a stop condition fired,
    or any non-zero exit — the watchdog included), pi's session file
    only holds complete messages and may have lost in-flight tool calls
    at the flush boundary; then the runner's own stream tee
    (`stream.jsonl`) is the replay source instead. The consumer is the
    same `StreamAggregator` the live runner feeds — both pi formats
    reduce to the same signal set. `early_exit` comes from the capture's
    sidecar: a runner stop condition fired mid-attempt, so the kill that
    ended the session is the runner's by design, not an error, and the
    answer carries the same `<early-exit:...>` marker the live runner
    writes. `exit_code` is not part of the session format — it comes
    from the sidecar; grading classifies pi failures from
    stopReason/errorMessage as it does live. The model is read off the
    message payloads — what actually ran, pi's own fallbacks included.
    """
    killed = early_exit is not None or exit_code not in (0, None)
    source, mode = session_file, "session"
    if killed and stream_file:
        source, mode = stream_file, "stream"
    stderr_tail: list[str] = []
    agg = _pi.StreamAggregator(
        skills,
        collect_tool_calls=True,
        stderr_tail=stderr_tail,
    )
    model: str | None = None
    n_entries = 0
    with Path(source).open(errors="replace") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                stderr_tail.append(
                    f"torn line skipped: {line[:80]}"[: _pi._STDERR_CHARS]
                )
                continue
            if mode == "stream":
                if agg.feed(line):
                    stderr_tail.append(
                        f"unmodeled stream event: {line[:80]}"[: _pi._STDERR_CHARS],
                    )
                    agg.error_message = (
                        "pi event stream parse error: unmodeled event in stream tee",
                    )
                    break
            else:
                try:
                    agg.feed_session_entry(parse_session_entry(raw))
                except PiSessionParseError as exc:
                    stderr_tail.append(f"session parse: {exc}"[: _pi._STDERR_CHARS])
                    agg.error_message = f"pi session parse error: {exc}"
                    break
            if model is None and isinstance(raw, dict):
                message = raw.get("message")
                if isinstance(message, dict) and message.get("model"):
                    model = str(message["model"])
            n_entries += 1
    fields = agg.core_fields(answer_limit=30000)
    empty = n_entries == 0
    # A runner stop condition is the kill classification, not an error:
    # the session ends mid-attempt by design, and any non-zero exit code
    # is the runner's own group kill.
    errored = not early_exit and (
        agg.stop_reason is not None or agg.error_message is not None or empty
    )
    answer = fields["answer"]
    if early_exit is not None and "<early-exit:" not in answer:
        answer = (answer + f" <early-exit:{early_exit}>").strip()
    core = AttemptCore(
        query=prompt,
        run=run,
        clean=not errored,
        timed_out=False,
        errored=errored,
        early_exit=early_exit,
        exit_code=exit_code,
        error=None
        if early_exit is not None
        else _pi.compose_error(
            agg.stop_reason,
            agg.error_message,
            "empty transcript" if empty else None,
        ),
        dur_s=round(dur_seconds),
        stderr_tail=fields["stderr_tail"][-_pi._STDERR_LINES :],
        **{k: v for k, v in fields.items() if k not in ("stderr_tail", "answer")},
        answer=answer,
    )
    return core, model


def _graded_record(  # noqa: PLR0913 — one bundle per attempt; keyword-only
    task: Task,
    rubric: Rubric,
    core: AttemptCore,
    skills: list[SkillRef],
    options: GradeOptions,
    *,
    run: int,
    model: str | None,
    precheck_output: str | None,
    precheck_json: dict[str, t.Any] | None,
    artifact_texts: list[str],
    pi_version: str | None = None,
    extra_metadata: dict[str, str] | None = None,
) -> tuple[GradeRecord, GradedVerdict, dict[str, bool]]:
    """Grade one attempt core and build its record (the only builder)."""
    verdict = extract_verdict(core.answer)
    scores = apply_rubric(
        rubric, verdict, artifact_texts, precheck_json, core.triggered
    )
    record = GradeRecord(
        **_record_kwargs(core),
        model=model,
        loaded_skills=[ref.name for ref in skills],
        tasks_file=options.tasks,
        rubrics_dir=options.rubrics,
        pi_version=pi_version,
        extra_metadata=extra_metadata,
        ts=time.strftime("%Y-%m-%dT%H:%M:%S"),
        task_id=task.id,
        rubric_id=rubric.id,
        rubric_version=rubric.version,
        contract_sha256=_sha256(task.contract or ""),
        rubric_sha256=_sha256(_rubric_text(options.rubrics, task.rubric)),
        schema_ok=None if task.contract is None else verdict.schema_ok,
        scores=scores,
        rubric_score=(sum(scores.values()) / len(scores) if scores else None),
        verdict=verdict.fields if verdict.schema_ok else {},
        expected_skills=rubric_expected_skills(rubric),
        precheck_output=precheck_output,
    )
    return record, verdict, scores


def _append_record(out: Path, record: GradeRecord) -> None:
    """Crash-safe append: one line per attempt, as it finishes.

    The record declares exactly the graded-attempt fields, so the only
    omission is absent provenance `extra_metadata`; labels, when
    present, ride through verbatim from the bundle's sidecar.
    """
    kwargs: dict[str, t.Any] = {"by_alias": True}
    if record.extra_metadata is None:
        kwargs["exclude"] = {"extra_metadata"}
    with out.open("a") as fh:
        fh.write(record.model_dump_json(**kwargs) + "\n")


def grade_tasks(
    options: GradeOptions,
    progress: Callable[[str], None] | None = None,
) -> GradeRunStats:
    """Grade the one (task, run) the bundle's sidecar names, offline.

    Load bundle + tasks + rubrics, replay the session, apply the rubric,
    append the record. Trigger detection and `loaded_skills` come from
    the sidecar's forwarded skill paths (the runner's — grade-time
    `--skills` is the fallback for hand-assembled bundles only). A task
    that declares a precheck requires the bundle to carry the recorded
    output — the grader cannot execute it.
    """
    say = progress or (lambda _msg: None)
    capture = _load_capture(options.capture)
    tasks = load_tasks(options.tasks)
    rubrics = load_rubrics(options.rubrics, tasks)
    skills = (
        list(capture.sidecar.skills)
        if capture.sidecar.skills
        else resolve_skills(options.skills)
        if options.skills
        else []
    )
    matches = [task for task in tasks if task.id == capture.sidecar.task_id]
    if not matches:
        msg = f"task not found in {options.tasks}: {capture.sidecar.task_id}"
        raise ValueError(msg)
    task, run = matches[0], capture.sidecar.run

    out = Path(options.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    if any(
        rec.clean and rec.task_id == task.id and rec.run == run
        for rec in load_grade_records(out)
    ):
        say(f"{task.id} run {run} already graded clean — skipped")
        return GradeRunStats(
            out_file=str(out),
            tasks_file=options.tasks,
            n_tasks=1,
            n_runs=0,
            n_attempts=0,
            errored_attempts=0,
            dur_s=round(time.monotonic() - t0),
        )

    say(f"[{time.strftime('%H:%M:%S')}] {task.id} run {run}")
    rubric = rubrics[task.rubric]
    artifact_texts = [
        (Path(options.artifacts_dir) / rel).read_text(errors="replace")
        for rel in task.artifacts
    ]
    core, model = _replay_attempt(
        str(capture.session),
        capture.prompt,
        skills,
        run=run,
        dur_seconds=capture.sidecar.dur_seconds,
        exit_code=capture.sidecar.pi_exit_code,
        early_exit=capture.sidecar.early_exit,
        stream_file=str(capture.stream) if capture.stream else None,
    )
    # Prechecks: the `run` verb executes the task precheck inside the
    # boundary (§12) and records the output in the bundle. A precheck-less
    # bundle for a task that declares one is a hard error — the grader is
    # offline and cannot run the precheck itself.
    if task.precheck:
        if capture.precheck_output is None and capture.precheck_json is None:
            msg = (
                f"task {task.id!r} declares a precheck but the bundle carries "
                "none — prechecks execute at run time inside the boundary "
                "(`profe run`); the offline grader cannot run them"
            )
            raise ValueError(msg)
        precheck_output, precheck_json = capture.precheck_output, capture.precheck_json
    else:
        precheck_output, precheck_json = None, None
    record, verdict, scores = _graded_record(
        task,
        rubric,
        core,
        skills,
        options,
        run=run,
        model=model,
        precheck_output=precheck_output,
        precheck_json=precheck_json,
        artifact_texts=artifact_texts,
        pi_version=capture.sidecar.pi_version,
        extra_metadata=capture.sidecar.extra_metadata,
    )
    _append_record(out, record)
    passed = sum(scores.values())
    say(
        f"[{time.strftime('%H:%M:%S')}] {task.id} run {run} done: "
        f"score {passed}/{len(scores)}, schema_ok="
        f"{verdict.schema_ok}, dur={core.dur_s}s",
    )
    return GradeRunStats(
        out_file=str(out),
        tasks_file=options.tasks,
        n_tasks=1,
        n_runs=1,
        n_attempts=1,
        errored_attempts=0 if core.clean else 1,
        dur_s=round(time.monotonic() - t0),
    )


def _rubric_text(rubrics_dir: str, rubric_id: str) -> str:
    """Read the rubric file text, for the content hash in every record."""
    return (Path(rubrics_dir) / f"{rubric_id}.yaml").read_text()


def _record_kwargs(core: AttemptCore) -> dict[str, t.Any]:
    """Map an AttemptCore onto GradeRecord's attempt-fact fields.

    Explicit field selection, not a wholesale dump: the runner-only
    AttemptCore fields (`index`, `expected_skills`, `attempt`) have no
    GradeRecord counterpart (`expected_skills` is stamped from the
    rubric, not echoed from the runner). `schema_version` is dropped so
    the record re-defaults to the current format version; `usage`
    arrives as a dict and revalidates.
    """
    return core.model_dump(
        include={
            "query",
            "run",
            "clean",
            "timed_out",
            "errored",
            "exit_code",
            "error",
            "retries_seen",
            "stderr_tail",
            "answer",
            "n_tool_calls",
            "tool_calls",
            "usage",
            "dur_s",
            "triggered",
            "first_trigger_skill",
            "trigger_call_index",
            "early_exit",
        },
    )
