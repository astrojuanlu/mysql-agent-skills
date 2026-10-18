"""Pydantic models for profe's inputs and serialized outputs.

One lifecycle: `validate` → `compile` → `run` → `grade` → `report`. A
cell — trigger measurement or task grading — is the same shape: one
attempt (collected by `run`, replayed from the capture bundle by
`grade`), scored by a mechanical rubric, recorded self-describing.

Design notes:
- Every serialized record is self-describing: it carries its condition
  (`loaded_skills`, model, and the expectations where the rubric makes
  trigger claims), so a number is interpretable without the caller's
  memory (README "Every reported number records its protocol, model,
  skill count and cutoff").
- Rubrics address transcript regions: the verdict block (`field_*`),
  the artifacts (`evidence_in_artifacts`), the recorded precheck
  (`precheck_field_equals`) and the tool-call stream
  (`skill_triggered` family). Query classes are *derived*, never
  stored: an empty `expected_skills` list IS the should-not class.
- Field names and value shapes follow the record example in
  README/SPEC; `early_exit` uses the `trigger` | `max_tool_calls` |
  null vocabulary (a run stopped by a configured stop condition).
- pi's JSON event stream and session files are modeled in the
  `pi-agent-json` workspace package; the record embeds pi's own
  `PiUsage` verbatim (no profe-side copy of that shape).
- The record/report format version is serialized under the key `schema`
  via a serialization alias; serialize with `by_alias=True` everywhere.
- The schema evolves freely while the project iterates (no
  back-compat): historical runs files are regenerated or converted
  when a new shape needs them, not preserved as format contracts.
"""

from __future__ import annotations

import typing as t

from pi_agent_json import PiUsage
from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Shared vocabularies
# ---------------------------------------------------------------------------

SCHEMA_VERSION: t.Literal[5] = 5
"""Record/report format written by this build (see the module docstring)."""


class _AliasSchema(t.TypedDict):
    """The Field kwargs _alias_schema supplies.

    Keys are pinned so the `Field(**_alias_schema())` spreads
    type-check against Field's own signature.
    """

    alias: str
    serialization_alias: str


def _alias_schema() -> _AliasSchema:
    """Build the Field kwargs for the format-version field.

    The wire key is `schema` (README record example) while the attribute
    name avoids shadowing BaseModel's deprecated `.schema()` method.
    """
    return {"alias": "schema", "serialization_alias": "schema"}


EarlyExit = t.Literal["trigger", "max_tool_calls"]
"""Why a run was stopped by a configured stop condition before completing.

`trigger`: stopped on the first skill read (yield — the caller's policy
decided this cell routes on reads); `max_tool_calls`: the turn cap fired.
Null when the attempt completed on its own."""

RunVerdict = t.Literal["hit", "misroute", "miss"]
"""Per-run verdict against the record's expectations (routing cells).

`hit`: an expected skill triggered. `misroute`: only non-expected skills
triggered — a miss by construction, and the failure worth catching: an
"any skill triggered" fallback would silently pass it. `miss`: nothing
triggered. A run with no clean attempt has no verdict (None) — errors are
not trigger evidence, and the driver/caller decides whether to retry.
"""

Decision = t.Literal["PASS", "FAIL"]


class ToolCall(BaseModel):
    """One recorded tool invocation (the Phase-2 action transcript).

    Collected only when the caller asks (`grade`); `args` values are
    trimmed so a single `write` cannot blow the record. The transcript is
    what action-sequence rubric checks assert on — prose is never graded.
    """

    tool: str
    args: dict[str, t.Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


class Query(BaseModel):
    """One item of a queries file (SPEC "How to run a typical session").

    `expected_skills` is always a list, any-of semantics; a bare string is
    rejected (a hard error, per the startup checks). An empty list is a
    should-not query: every skill must stay away from it. There is no way
    to say "should trigger" without naming who — the shape makes the
    "no free-floating should-trigger queries" rule structural.

    Unrecognized fields are forbidden: an input the format did not
    design for is rejected, not guessed at — with `expected_skills`
    defaulting to empty, a silently dropped field would reclass a query
    as should-not (the "silently corrupts accounting" class the README
    warns about for skills).
    """

    model_config = ConfigDict(extra="forbid")

    query: str
    expected_skills: list[str] = Field(default_factory=list)


class QueriesFile(BaseModel):
    """A queries file: schema-declared, queries in fixed file order.

    The schema declaration is mandatory and must be 1 — a file without
    one is rejected rather than guessed at, because a silent default
    would reclass every query it cannot interpret as should-not.
    """

    model_config = ConfigDict(protected_namespaces=())

    schema_version: t.Literal[1] = Field(**_alias_schema())
    queries: list[Query] = Field(min_length=1)


class SkillRef(BaseModel):
    """A skill profe pinned into the runs.

    `name` comes from the frontmatter (`name:` field) with the directory
    name as fallback; `path` is the exact path forwarded to pi via
    `--skill`, so records can match triggers by forwarded path string
    rather than by name (name and dir name coincide today, but that is
    not an invariant).
    """

    name: str
    path: str


class ValidateOptions(BaseModel):
    """`profe validate` inputs: a thin wrapper over `skills-ref validate`."""

    skills: list[str] = Field(min_length=1)


# ---------------------------------------------------------------------------
# Outputs: the attempt core
# ---------------------------------------------------------------------------


class AttemptCore(BaseModel):
    """The self-describing core of one attempt (one headless pi invocation).

    Built live by the runner and by replaying a captured session file in
    `grade` — the same fields either way, so a cell's records do not
    depend on how the attempt was collected. Trigger accounting is
    computed always and serialized where the record shape wants it.
    """

    model_config = ConfigDict(protected_namespaces=())

    schema_version: int = Field(default=SCHEMA_VERSION, **_alias_schema())
    index: int = Field(
        default=0,
        description="query index, fixed by file order",
    )
    query: str
    expected_skills: list[str] = Field(
        default_factory=list,
        description="verbatim echo of the query's expectations; [] = should-not",
    )
    run: int = Field(description="run label from the caller's retry loop")
    attempt: int = Field(
        default=0,
        description="retry index within the run (the runner's, not a flag)",
    )
    clean: bool = Field(
        description="not errored and not timed out; errors are not trigger "
        "evidence even when a trigger was preserved",
    )
    triggered: dict[str, bool] = Field(
        description="per loaded skill, whether its SKILL.md was read "
        "(mechanical detection from the event stream, never inferred "
        "from the answer)",
    )
    first_trigger_skill: str | None = None
    trigger_call_index: int | None = Field(
        default=None,
        description="1-based index into ALL tool calls at which the first "
        "SKILL.md read happened (not just skill reads)",
    )
    early_exit: EarlyExit | None = Field(
        default=None,
        description="a run stopped by a configured stop condition (yield on "
        "the first skill read, or the tool-call cap) — the attempt is "
        "routing evidence only: no verdict block exists, competence "
        "rubrics do not apply to it",
    )
    timed_out: bool = False
    errored: bool = False
    exit_code: int | None = None
    error: str | None = None
    retries_seen: int = 0
    stderr_tail: list[str] = Field(
        default_factory=list,
        description="drained pi stderr (~10 lines x 300 chars), the Case-E "
        "diagnostic record",
    )
    answer: str = Field(
        default="",
        description="truncated to 2000 chars by the runner, with an inline "
        "<timeout> / <early-exit:...> marker when the run was killed",
    )
    n_tool_calls: int = Field(
        default=0,
        description="count of ALL tool_execution_start events",
    )
    tool_calls: list[ToolCall] = Field(
        default_factory=list,
        description="recorded action transcript, when the caller asked for "
        "it (yielded runs may hold the calls up to the stop point)",
    )
    usage: PiUsage = Field(default_factory=PiUsage)
    dur_s: int = Field(default=0, description="wall seconds for the attempt")


# ---------------------------------------------------------------------------
# Phase 2: grading (tasks, rubrics, graded records)
# ---------------------------------------------------------------------------


CheckType = t.Literal[
    "field_equals",
    "field_regex",
    "field_not_regex",
    "field_present",
    "evidence_in_artifacts",
    "precheck_field_equals",
    "skill_triggered",
    "triggered_any_of",
    "triggered_none",
]
"""The mechanical check vocabulary (no LLM anywhere in grading), typed by
transcript region.

`field_*` checks operate on the agent's parsed verdict block;
`evidence_in_artifacts` grounds quoted lines against the task's artifacts
(grep -F semantics); `precheck_field_equals` compares a verdict field to a
field of the task precheck's JSON output — the live-cluster ground truth;
the `skill_triggered` family operates on the tool-call stream — the
`triggered` map every replay computes. Routing cells ("uses skill X" /
"no skill fires") are graded with the same machinery as task cells.
"""


class Task(BaseModel):
    """One task: a prompt, an optional verdict contract and a rubric id.

    A trigger cell's task is compiled from a queries file: the prompt is
    the query text, the rubric is the expectation set, and there is no
    contract (a routing attempt yields before any verdict block could
    exist; `schema_ok` is then None). `contract` is appended verbatim to
    the prompt in every cell where present — it is part of the task, not
    of the skills. `artifacts` are paths relative to the artifacts dir,
    staged read-only for the agent and used by the grader for evidence
    grounding. `precheck` is a shell command whose JSON output is
    recorded after the attempt (live-cluster ground truth); profe records
    the condition, it does not deploy clusters.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    prompt: str
    contract: str | None = None
    rubric: str
    artifacts: list[str] = Field(default_factory=list)
    precheck: str | None = None


class RubricCheck(BaseModel):
    """One mechanical check over a transcript region.

    Regions: the verdict block, the artifacts, the precheck output and
    the tool-call stream.
    Trigger checks (`skill_triggered` family) read the record's
    `triggered` map, which the replay computes from the session's
    tool-call events — grade-time `--skills` cannot influence them.
    `field` carries a skill name for `skill_triggered`, a dotted verdict
    path for `field_*`; `patterns` carries the any-of skill set for
    `triggered_any_of` and the regex set for `field_regex`.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    type: CheckType
    field: str = ""
    value: str | int | float | bool | None = None
    patterns: list[str] = Field(default_factory=list)
    min_quotes: int = 1
    precheck_field: str = ""
    description: str | None = None


class RubricBlindProtocol(BaseModel):
    """How the host-side depth grader is kept blind to the arm.

    Prose contract for a human pass; profe never executes it. Carried
    and hashed with the rubric — modeled (not dropped) because an
    unmodeled rubric field must fail loudly, never vanish into `allow`.
    """

    model_config = ConfigDict(extra="forbid")

    input: str = ""
    known_leak: str = ""
    procedure: list[str] = Field(default_factory=list)


class RubricDimension(BaseModel):
    """One graded dimension of the manual depth pass.

    `values` for enums; `scale` + `anchors` for ordinals — the YAML
    anchor keys are the scale's levels (ints) and stay ints here.
    """

    model_config = ConfigDict(extra="forbid")

    type: str = ""
    values: list[str] = Field(default_factory=list)
    scale: list[int] = Field(default_factory=list)
    description: str = ""
    anchors: dict[int, str] = Field(default_factory=dict)


class RubricDepthSchema(BaseModel):
    """The grade-depth.json record the blind pass writes."""

    model_config = ConfigDict(extra="forbid")

    path: str = ""
    fields: dict[str, str] = Field(default_factory=dict)


class Rubric(BaseModel):
    """A versioned set of checks; lives in `eval/rubrics/<id>.yaml`.

    Engineered and versioned independently of tasks and skills: the
    rubric is derived before the run from ground truth that exists
    independently of the agent (issue resolutions, case studies,
    live-cluster probes).

    Two kinds of content, one hash: `mechanical_checks` are what the
    grade step executes (an empty list is the rubric-free T4 mode —
    live, unsolved bugs: no scores are produced, `rubric_score` is
    None in the record, but the verdict contract is still enforced
    mechanically, `schema_ok`, and the rubric hash still pins the
    contract identity); the blind-depth-pass metadata (task,
    blind_protocol, dimensions, grade_depth_json_schema, notes) is
    carried and hashed for the host-side manual grader — profe never
    executes it.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    version: int = 1
    description: str | None = None
    mechanical_checks: list[RubricCheck] = Field(default_factory=list)
    task: str | None = None
    blind_protocol: RubricBlindProtocol | None = None
    dimensions: dict[str, RubricDimension] = Field(default_factory=dict)
    grade_depth_json_schema: RubricDepthSchema | None = None
    notes: str | None = None


class GradeCondition(BaseModel):
    """The condition a graded record was produced under.

    Same role as `ConditionMetadata` for probe records: model, skill set,
    input files and the task/rubric identities whose pairing produced the
    number.
    """

    model_config = ConfigDict(protected_namespaces=())

    model: str | None = Field(
        default=None,
        description="pi's configured default model when None",
    )
    loaded_skills: list[str] = Field(
        description="exactly what ran: [] is the skills-off counterfactual cell",
    )
    tasks_file: str
    rubrics_dir: str
    extra_metadata: dict[str, str] | None = Field(
        default=None,
        description="free-form provenance labels from the bundle's sidecar "
        "(--extra-metadata KEY=VALUE at run time); None when the runner had none",
    )
    pi_version: str | None = Field(
        default=None,
        description="pi version that executed the attempt (from the bundle's sidecar)",
    )
    ts: str = Field(description="attempt finish timestamp, local time")


class CaptureSidecar(BaseModel):
    """One capture directory's sidecar (`capture.json`).

    The pi session file is the canonical capture and is never touched;
    the sidecar carries exactly what the session format does not
    retain: wall time, pi's exit code and version, the identity of
    the attempt (task, run), the forwarded skill paths (trigger
    detection matches exact forwarded strings — grade-time `--skills`
    paths in another boundary would silently mismatch), whether the
    runner stopped the attempt (a stop condition fired), and where the
    session was copied from. Written by the runner next to
    `session.jsonl` and `prompt.txt`; `profe grade <bundle>` reads all
    three.
    """

    task_id: str
    run: int = 0
    dur_seconds: float = 0.0
    pi_exit_code: int | None = None
    pi_version: str | None = None
    session_source: str | None = None
    pi_path: str | None = Field(
        default=None,
        description="resolved pi binary that ran (explicit --pi or PATH)",
    )
    skills: list[SkillRef] = Field(
        default_factory=list,
        description="the exact skill paths forwarded to pi (name + path), "
        "the identity trigger detection and loaded_skills are graded from",
    )
    early_exit: EarlyExit | None = Field(
        default=None,
        description="stop condition that fired, if any — the replay must "
        "not classify the runner's own kill as an error",
    )
    extra_metadata: dict[str, str] | None = Field(
        default=None,
        description="free-form provenance labels (--extra-metadata KEY=VALUE)",
    )


class GradeRecord(GradeCondition):
    """One graded attempt: the attempt facts, the condition, the scores.

    A graded record is exactly one capture bundle's outcome plus its
    condition; the attempt core is the replayed session's, so yielded
    attempts (a stop condition fired — routing evidence only, no verdict
    block) and full attempts share the shape.
    `schema_ok` separates format compliance from
    correctness (a weak model may fail at producing the verdict block
    rather than at the substance — the two failure modes stay
    distinguishable); None when the task declares no contract.
    `scores` maps every rubric check id to its
    outcome; `rubric_score` is the fraction passed. `verdict` echoes
    the parsed block for audit.
    """

    schema_version: int = Field(default=SCHEMA_VERSION, **_alias_schema())
    query: str = Field(
        description="the exact prompt pi received (contract included)",
    )
    run: int = Field(description="run label from the caller's retry loop")
    clean: bool = Field(
        description="not errored and not timed out — the gate for "
        "report/compare attempt selection",
    )
    timed_out: bool = False
    errored: bool = False
    exit_code: int | None = None
    early_exit: EarlyExit | None = Field(
        default=None,
        description="runner stop condition that ended the attempt "
        "(yield/cap), None for a natural completion",
    )
    error: str | None = None
    retries_seen: int = Field(
        default=0,
        description="retry events observed in the attempt transcript — real "
        "signal (hook retries the boundary surfaced)",
    )
    stderr_tail: list[str] = Field(
        default_factory=list,
        description="drained pi stderr (~10 lines x 300 chars), the Case-E "
        "diagnostic record",
    )
    answer: str = Field(
        default="",
        description="the attempt's final answer as trimmed by the runner's "
        "answer_limit; the verdict block is parsed from here",
    )
    n_tool_calls: int = Field(
        default=0,
        description="count of ALL tool_execution_start events",
    )
    tool_calls: list[ToolCall] = Field(
        default_factory=list,
        description="recorded action transcript",
    )
    usage: PiUsage = Field(default_factory=PiUsage)
    dur_s: int = Field(default=0, description="wall seconds for the attempt")
    triggered: dict[str, bool] = Field(
        default_factory=dict,
        description="per loaded skill, whether its SKILL.md was read "
        "(mechanical detection from the session's tool calls, never "
        "inferred from the answer) — the routing evidence and the input "
        "to the skill_triggered family of checks",
    )
    first_trigger_skill: str | None = None
    trigger_call_index: int | None = Field(
        default=None,
        description="1-based index into ALL tool calls at which the first "
        "SKILL.md read happened (not just skill reads) — the datum the "
        "turn-cap calibration is computed from",
    )
    expected_skills: list[str] | None = Field(
        default=None,
        description="the trigger expectations the rubric claims, stamped by "
        "grade (any-of list; [] = should-not); None = the rubric makes no "
        "trigger claims (an ordinary task cell)",
    )

    task_id: str
    rubric_id: str
    rubric_version: int
    contract_sha256: str
    rubric_sha256: str
    schema_ok: bool | None
    scores: dict[str, bool] = Field(default_factory=dict)
    rubric_score: float | None = None
    verdict: dict[str, t.Any] = Field(default_factory=dict)
    precheck_output: str | None = Field(
        default=None,
        description="the recorded precheck output, when the task declares "
        "a precheck (executed at run time inside the boundary)",
    )


class GradeOptions(BaseModel):
    """`profe grade` inputs: one capture bundle, scored offline.

    The attempt already ran (inside the caller's boundary, collected by
    `profe run`); grade never launches pi, never touches the network,
    and has zero subprocess calls. `skills` is the fallback for
    hand-assembled bundles whose sidecar predates the forwarded-skill
    record; bundles written by `run` carry the forwarded skill paths in
    the sidecar, and those are what the replay (and every trigger
    check) uses. The model is not an input either: it is extracted from
    the bundle's session file, so the record carries what actually ran,
    not what the caller asked for.
    """

    capture: str = Field(description="capture bundle directory to grade")
    tasks: str
    rubrics: str
    out: str
    skills: list[str] = Field(default_factory=list)
    artifacts_dir: str = "eval/tasks-artifacts"


class GradeRunStats(BaseModel):
    """What one `grade` execution did (heartbeat bookkeeping)."""

    out_file: str
    tasks_file: str
    n_tasks: int
    n_runs: int = Field(description="runs executed, including resumed ones")
    n_attempts: int = Field(description="pi invocations, incl. retries")
    errored_attempts: int = 0
    dur_s: int = 0


class RunOptions(BaseModel):
    """`profe run` inputs (§12: one attempt inside the caller's boundary).

    Deliberately narrower than GradeOptions: no rubrics (must never
    enter the boundary), no retries/num_runs (one invocation is one
    attempt — a retry is a fresh boundary, the caller's loop), one task
    id (a boundary hosts one cell). `pi_config` is the config dir the
    runner copies into the scratch HOME (pi reads config from HOME,
    which inside the attempt is a fresh scratch dir). `pi` pins the
    instrument explicitly (entirely optional: when None, `pi` resolves
    on PATH, first entry wins — the resolved binary is recorded in the
    sidecar either way). `extra_metadata` carries free-form provenance
    labels (KEY=VALUE via `--extra-metadata`), recorded in the sidecar and
    copied into the grade record. The stop conditions
    (`stop_on_skill_read`, `max_tool_calls`) are the yield surface: a
    trigger cell yields on the first SKILL.md read of any loaded skill;
    a task cell can cap the attempt (`max_tool_calls`) without
    yielding on reads.
    """

    tasks: str
    task_id: str
    run: int = 0
    out: str = Field(description="capture bundle directory to write")
    skills: list[str] = Field(default_factory=list)
    model: str | None = None
    timeout: int = 600
    stop_on_skill_read: bool = Field(
        default=False,
        description="yield on the first SKILL.md read of any loaded skill "
        "(the trigger-cell stop condition)",
    )
    max_tool_calls: int | None = Field(
        default=None,
        description="tool-call cap, decoupled from the skill-read yield; "
        "None = the wall-clock deadline is the only bound",
    )
    artifacts_dir: str = "eval/tasks-artifacts"
    pi_config: str = "~/.pi/agent"
    pi: str | None = Field(
        default=None,
        description="pi binary to launch; None resolves `pi` on PATH",
    )
    extra_metadata: dict[str, str] | None = Field(
        default=None,
        description="free-form provenance labels (sidecar + grade record)",
    )


class CompileOptions(BaseModel):
    """`profe compile` inputs: a queries file → trigger-cell tasks + rubrics.

    Offline generation (no pi anywhere near it): one task per query, one
    rubric per distinct expectation set. The same startup checks a
    routing cell has always enforced move here: the schema declaration
    is mandatory, every `expected_skills` name must be among the loaded
    skills, and no field may hold a bare string where a list is
    required.
    """

    skills: list[str] = Field(min_length=1)
    queries: str
    tasks: str = Field(description="tasks JSONL to write (overwritten)")
    rubrics_dir: str = Field(description="directory to write <rubric-id>.yaml into")


class CompileStats(BaseModel):
    """What one `profe compile` execution wrote."""

    queries_file: str
    tasks_file: str
    rubrics_dir: str
    n_queries: int
    n_tasks: int
    n_rubrics: int


# ---------------------------------------------------------------------------
# Outputs: validate report
# ---------------------------------------------------------------------------


class SkillValidation(BaseModel):
    """`skills-ref validate` result for one skill."""

    skill: str
    ok: bool
    output: str = Field(description="raw skills-ref output, kept verbatim")


class ValidateReport(BaseModel):
    """Aggregate `profe validate` output.

    Structure only: this cannot assess trigger behavior (that is a
    routing cell's rubric) or answer quality (a task cell's rubric).
    """

    ok: bool
    skills: list[SkillValidation]


# ---------------------------------------------------------------------------
# Outputs: routing report (the trigger-cell shapes of `profe report`)
# ---------------------------------------------------------------------------


class RunRouting(BaseModel):
    """One run's trigger state inside a per-task routing entry.

    `verdict` is None only for a run that never got a clean attempt
    (every attempt errored) — preserved for diagnosis, excluded from
    rates by the first-clean-attempt rule.
    """

    run: int
    clean: bool
    verdict: RunVerdict | None = None
    triggered: dict[str, bool] = Field(default_factory=dict)


class TaskRouting(BaseModel):
    """Per-task routing entry (JSON report only).

    The view description iteration actually works from — a misroute or a
    near-miss that fired is diagnosable from the report alone, without
    re-reading the runs file.
    """

    task_id: str
    query: str
    expected_skills: list[str]
    runs: list[RunRouting]
    decision: Decision


class SkillRate(BaseModel):
    """One rate row over a skill.

    Two views over the same runs: `expected` — over the tasks that list
    the skill, the rate of runs where THAT skill triggered (a
    persistently one-sided routing shows up as a 0-rate for the other
    any-of member: the data a dual-trigger policy decision needs);
    `cross-trigger` — the rate at which the skill triggered on runs
    where it was not expected (the misroute table, should-not runs
    included).
    """

    skill: str
    view: t.Literal["expected", "cross-trigger", "should-not"]
    runs: int = 0
    hits: int = 0
    rate: float = 0.0
    decision: Decision | None = Field(
        default=None,
        description="None for informational rows (cross-trigger, "
        "should-not aggregate); expected rows PASS at rate >= 0.5",
    )


class RoutingReport(BaseModel):
    """The routing shapes `profe report` renders for a trigger cell.

    Derived entirely from the records (expectations stamped by grade
    from the rubric, trigger state replayed from the session) — no
    caller memory involved.
    """

    n_tasks: int
    n_runs: int = Field(description="runs with a clean attempt")
    skill_rows: list[SkillRate] = Field(default_factory=list)
    tasks: list[TaskRouting] = Field(default_factory=list)
