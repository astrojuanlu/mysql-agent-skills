"""Pydantic models for profe's inputs and serialized outputs.

Scope (Phase 1): the `validate` → `probe` → `summarize` pipeline. The
`grade`/`compare`/`report` verbs (Phase 2/3, WIP) will extend the same
attempt core rather than replace it, so `AttemptCore` is the single
definition of "one headless pi invocation" that both phases serialize.

Design notes:
- Every serialized record is self-describing: it carries its expectations
  (`expected_skills`) and its condition (`loaded_skills`, model, cutoff,
  run policy), so the gap between them is exactly what the run measured
  and a number is interpretable without the caller's memory
  (README "Every reported number records its protocol, model, skill
  count and cutoff").
- Query classes are *derived*, never stored: an empty `expected_skills`
  list IS the should-not class (SPEC "Summarize").
- Field names and value shapes follow the record example in
  README/SPEC (`schema: 1`, `index`, `n_tool_calls`, `clean`, ...);
  `early_exit` uses the `trigger` | `max_tool_calls` | null vocabulary.
- pi's JSON event stream is modeled in the `pi-agent-json` workspace
  package (`CostBreakdown` is re-exported here: schema-2 usage embeds
  it verbatim); the event vocabulary itself is profe's parsing input,
  not part of the record format.
- The record/report format version is serialized under the key `schema`
  (README record example) via a serialization alias; serialize with
  `by_alias=True` everywhere. Version history: schema 1 recorded usage
  cost as a flat total (float); schema 2 records pi's nested cost
  breakdown verbatim (`UsageV2`). The `AttemptCore` validator enforces
  the pairing — a record's `schema` value and its usage shape may not
  disagree.
"""

from __future__ import annotations

import typing as t

from pi_agent_json import CostBreakdown
from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Shared vocabularies
# ---------------------------------------------------------------------------

SCHEMA_VERSION: t.Literal[2] = 2
"""Record/report format written by this build (see the module docstring)."""

SCHEMA_VERSION_V1: t.Literal[1] = 1
"""Legacy record/report format (flat usage cost); still readable."""


def _alias_schema() -> dict:
    """Build the Field kwargs for the format-version field.

    The wire key is `schema` (README record example) while the attribute
    name avoids shadowing BaseModel's deprecated `.schema()` method.
    """
    return {"alias": "schema", "serialization_alias": "schema"}


QueryClass = t.Literal["should-trigger", "should-not"]
"""The two query classes, derived from `expected_skills` emptiness."""

EarlyExit = t.Literal["trigger", "max_tool_calls"]
"""Why a Phase-1 run was killed before completing. Null when it completed."""

RunVerdict = t.Literal["hit", "misroute", "miss", "error"]
"""Per-run verdict against the query's expectations.

`hit`: an expected skill triggered. `misroute`: only non-expected skills
triggered — a miss by construction, and the failure worth catching (SPEC:
an "any skill triggered" fallback would silently pass it). `miss`: nothing
triggered. `error`: the run was not clean — errors are not trigger
evidence, they escalate, never conclude.
"""

Decision = t.Literal["PASS", "FAIL"]
RunCount = int | t.Literal[":auto:"]
"""Adaptive run policy (`:auto:`, cap 3) or a fixed N (validation runs)."""


class Usage(BaseModel):
    """Schema-1 usage: token/cost accounting with cost as a flat total.

    pi reports cost as a nested dict; schema-1 records (written before
    the breakdown was modeled) extracted only its `total`. Kept for
    reading legacy runs files; new records use `UsageV2`.
    """

    model_config = ConfigDict(extra="allow")

    input: int = 0
    output: int = 0
    cost: float = 0.0

    @property
    def cost_total(self) -> float:
        """The billed total, uniform across schema versions."""
        return self.cost


class UsageV2(BaseModel):
    """Schema-2 usage: token/cost accounting with pi's nested cost.

    Summing every message (assistant carries the real values, others ~0)
    survives timeout and early-exit kills. `input` is the *uncached*
    token count — the provider serves repeated context from cache and
    reports it under `cacheRead`, so the real context size is input +
    cacheRead (or just `totalTokens`).
    """

    model_config = ConfigDict(extra="allow")

    input: int = 0
    output: int = 0
    cost: CostBreakdown = Field(default_factory=CostBreakdown)

    @property
    def cost_total(self) -> float:
        """The billed total, uniform across schema versions."""
        return self.cost.total


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

    @property
    def query_class(self) -> QueryClass:
        """Derive the class; never store it (SPEC "Summarize")."""
        return "should-not" if not self.expected_skills else "should-trigger"


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


class ProbeOptions(BaseModel):
    """`profe probe` inputs.

    Serial execution, neutral cwd (/tmp), no hint, no session, no
    extensions are protocol constants baked into the implementation, not
    options. `max_tool_calls` is the calibrated Phase-1 cutoff constant
    (default 12; see eval/P1-CALIBRATION.md): a run with no skill read
    within the cutoff is non-trigger evidence modulo the documented
    cutoff approximation.
    """

    skills: list[str] = Field(min_length=1)
    queries: str
    out: str
    model: str | None = None
    timeout: int = 300
    retries: int = 1
    max_tool_calls: int = 12
    num_runs: RunCount = ":auto:"


class SummarizeOptions(BaseModel):
    """`profe summarize` inputs."""

    runs_file: str
    out: str | None = None
    format: t.Literal["table", "json"] = "table"


# ---------------------------------------------------------------------------
# Outputs: the attempt core
# ---------------------------------------------------------------------------


class AttemptCore(BaseModel):
    """The shared, self-describing core of one attempt.

    One record per attempt (a headless pi invocation), appended to the
    runs JSONL as it finishes — crash-safe, resumable. Phase 2 (`grade`)
    will subclass this with scores and rubric hashes instead of forking
    the format.
    """

    model_config = ConfigDict(protected_namespaces=())

    schema_version: int = Field(default=SCHEMA_VERSION, **_alias_schema())
    index: int = Field(description="query index, fixed by file order")
    query: str
    expected_skills: list[str] = Field(
        description="verbatim echo of the query's expectations; [] = should-not",
    )
    run: int = Field(description="adaptive escalation attempt index")
    attempt: int = Field(description="retry index within the run")
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
        description="Phase-1 runs are killed once the trigger question is "
        "answered, whatever the class: a should-not trigger is a false "
        "positive, a should-trigger cutoff run is non-trigger evidence",
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
        description="truncated to 2000 chars, with an inline <timeout> / "
        "<early-exit:...> marker; Phase-1 answers are truncated by design "
        "and never graded",
    )
    n_tool_calls: int = Field(
        default=0,
        description="count of ALL tool_execution_start events",
    )
    usage: Usage | UsageV2 = Field(default_factory=UsageV2)
    dur_s: int = Field(default=0, description="wall seconds for the attempt")

    @model_validator(mode="after")
    def _usage_matches_schema(self) -> t.Self:
        """Require the schema version and the usage shape to agree.

        Schema 1 = flat cost (float), schema 2 = nested cost breakdown.
        The union would dispatch by shape anyway; the check keeps the
        serialized `schema` key truthful about which model produced the
        record.
        """
        if self.schema_version == SCHEMA_VERSION_V1 and not isinstance(
            self.usage, Usage
        ):
            raise ValueError(
                "schema 1 records use the flat Usage model (cost: float); "
                "got " + type(self.usage).__name__
            )
        if self.schema_version == SCHEMA_VERSION and not isinstance(
            self.usage, UsageV2
        ):
            raise ValueError(
                "schema 2 records use the CostBreakdown usage model; "
                "got " + type(self.usage).__name__
            )
        return self


class ConditionMetadata(BaseModel):
    """The condition a record or report was produced under.

    Trigger behavior is model-dependent and skill-set-dependent; this is
    the block that keeps every reported number interpretable.
    """

    model_config = ConfigDict(protected_namespaces=())

    model: str | None = Field(
        default=None,
        description="pi's configured default model when None",
    )
    loaded_skills: list[str] = Field(
        description="exactly what ran: the pinned --skills forwards",
    )
    queries_file: str
    max_tool_calls: int
    num_runs: RunCount
    retries: int
    ts: str = Field(description="attempt finish timestamp, local time")


class ProbeRecord(AttemptCore, ConditionMetadata):
    """One probe attempt: the attempt core plus the condition metadata."""


class ProbeRunStats(BaseModel):
    """What one `probe_trigger_runs` execution did.

    The detailed per-attempt evidence lives in the runs JSONL; this is the
    bookkeeping the caller needs (heartbeat lines carry it too, via the
    progress callback).
    """

    out_file: str
    queries_file: str
    n_queries: int
    concluded: int = Field(
        description="queries whose escalation loop reached a decisive "
        "conclusion (all of them unless the run was interrupted)",
    )
    n_runs: int = Field(description="runs executed, including resumed ones")
    n_attempts: int = Field(
        description="pi invocations, including within-run retries",
    )
    errored_attempts: int = 0
    dur_s: int = 0


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

    Structure only: this cannot assess trigger behavior (probe's job) or
    answer quality (grade's).
    """

    ok: bool
    skills: list[SkillValidation]


# ---------------------------------------------------------------------------
# Outputs: summarize report
# ---------------------------------------------------------------------------


class RunSummary(BaseModel):
    """One run's trigger state inside a per-query entry.

    `verdict` is None only for a run that never got a clean attempt
    (every attempt errored) — preserved for diagnosis, excluded from
    rates by the first-clean-attempt rule.
    """

    run: int
    attempt: int | None = None
    clean: bool
    verdict: RunVerdict | None = None
    triggered: dict[str, bool] = Field(default_factory=dict)


class QuerySummary(BaseModel):
    """Per-query entry (JSON report only).

    The view description iteration actually works from — a misroute or a
    near-miss that fired is diagnosable from the report alone, without
    re-reading the runs file.
    """

    index: int
    query: str
    expected_skills: list[str]
    query_class: QueryClass
    runs: list[RunSummary]
    decision: Decision
    timeouts: int = 0
    errors: int = 0
    retries_used: int = 0


class SkillRate(BaseModel):
    """One rate row over a skill.

    Three views over the same runs (SPEC "Summarize"): `expected` — over
    the queries that list the skill, the rate of runs where THAT skill
    triggered (a persistently one-sided routing shows up as a 0-rate for
    the other any-of member: the data a dual-trigger policy decision
    needs); `cross-trigger` — the rate at which the skill triggered on
    runs where it was not expected (the misroute table, should-not runs
    included); `should-not` — the aggregate over should-not queries.
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


class Effort(BaseModel):
    """Effort medians over the selected attempts.

    Under early exit these measure the cost of the trigger *decision*,
    not of the task (Phase-1 answers are truncated by design): how
    expensive it is to get this model to route, and what the measurement
    itself cost. Phase-2 effort accounting reads full attempts instead.
    """

    total_tokens: float | None = None
    output_tokens: float | None = None
    cost: float | None = None
    tool_calls: float | None = None
    wall_s: float | None = None


class RunsTaken(BaseModel):
    """Run-count distribution over queries.

    Adaptive policy bookkeeping: most clean queries conclude on the first
    run, boundary cases at the cap reproduce fixed-N=3 semantics.
    """

    min: int
    median: float
    max: int


class ClassSummary(BaseModel):
    """Pass-rate aggregate for one query class."""

    query_class: QueryClass
    passed: int
    total: int
    pass_rate: float
    runs_taken: RunsTaken | None = None


class SummarizeReport(BaseModel):
    """`probe summarize` output (JSON format).

    The table format renders the skill rows plus a footer carrying the
    condition and effort.
    """

    model_config = ConfigDict(protected_namespaces=())

    schema_version: int = Field(default=SCHEMA_VERSION, **_alias_schema())
    runs_file: str
    condition: ConditionMetadata
    intended_skills: list[str] = Field(
        description="the distinct skills any query names, in load order",
    )
    n_queries: int
    n_runs: int = Field(description="completed runs (first-clean per run)")
    skill_rows: list[SkillRate] = Field(default_factory=list)
    class_summaries: list[ClassSummary] = Field(default_factory=list)
    queries: list[QuerySummary] = Field(default_factory=list)
    effort: Effort = Field(default_factory=Effort)
    effort_by_class: dict[str, Effort] = Field(default_factory=dict)
