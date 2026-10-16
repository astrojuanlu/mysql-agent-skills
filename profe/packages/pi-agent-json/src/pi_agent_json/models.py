"""Models of pi's JSON event stream (`pi --mode json`).

pi emits one JSON object per line. This package mirrors that vocabulary so
consumers fold typed events instead of digging through raw dicts. The
models are parsing types (they describe pi's output, not any serialized
format of their own): unmodeled event types are instrument debt and must
be fixed, not ignored — `parse_pi_event` raises on them.
"""

from __future__ import annotations

import typing as t

from pydantic import BaseModel, ConfigDict, Field


class CostBreakdown(BaseModel):
    """pi's per-message cost object, verbatim (`usage.cost`).

    The split matters for Phase-2 overhead accounting: cache reads are
    billed at a fraction of uncached input, so `input`-cost vs
    `cacheRead`-cost is the fixed-vs-marginal overhead signal.
    """

    model_config = ConfigDict(extra="allow")

    input: float = 0.0
    output: float = 0.0
    cacheRead: float = 0.0
    cacheWrite: float = 0.0
    total: float = 0.0


# ---------------------------------------------------------------------------
# Pi's JSON event stream (parsed, not serialized)
# ---------------------------------------------------------------------------
# pi --mode json emits one JSON object per line. These models mirror that
# vocabulary so _pi.py folds typed events instead of digging through raw
# dicts. They are internal parsing types (never serialized into records),
# so they carry no schema version. Unmodeled event types are instrument
# debt and must be fixed, not ignored: parse_pi_event raises on them.


class PiEventBase(BaseModel):
    """Shared shape of every pi stream event: discriminated by `type`."""

    model_config = ConfigDict(extra="allow")


class PiTextBlock(PiEventBase):
    """A text content block (`{type: "text", text}`)."""

    type: t.Literal["text"]
    text: str = ""


class PiThinkingBlock(PiEventBase):
    """A reasoning content block (`{type: "thinking", thinking, ...})."""

    type: t.Literal["thinking"]
    thinking: str = ""
    thinkingSignature: str | None = None


class PiToolCallBlock(PiEventBase):
    """A tool-call content block inside an assistant message."""

    type: t.Literal["toolCall"]
    id: str | None = None
    name: str | None = None
    arguments: dict[str, t.Any] = Field(default_factory=dict)


class PiImageBlock(PiEventBase):
    """An image content block (`{type: "image", data, mimeType}`)."""

    type: t.Literal["image"]
    data: str = ""
    mimeType: str | None = None


PiContentBlock = t.Annotated[
    PiTextBlock | PiThinkingBlock | PiToolCallBlock | PiImageBlock,
    Field(discriminator="type"),
]


class PiUsage(PiEventBase):
    """pi's per-message usage object, verbatim.

    `input` is the uncached token count (cached context is reported
    under `cacheRead`); `cost` is pi's nested breakdown. Zeros during
    streaming (`message_update`); the real values ride `message_end`.
    """

    input: int = 0
    output: int = 0
    cacheRead: int = 0
    cacheWrite: int = 0
    reasoning: int = 0
    totalTokens: int = 0
    cost: CostBreakdown = Field(default_factory=CostBreakdown)


class PiMessage(PiEventBase):
    """A message as pi emits it (user, assistant, or tool result).

    Tool-result messages additionally carry `toolCallId`, `toolName`,
    `isError` and `details` (modeled from session files); `role` is
    deliberately a plain string — pi also emits `bashExecution`.
    """

    id: str | None = None
    role: str
    # pi 1.2.0 streams the system message with `content` as a string
    # (the prompt text rides in `sections`, allowed via extra=allow);
    # 0.85.x and all assistant/user/tool messages use block lists.
    content: list[PiContentBlock] | str = Field(default_factory=list)
    timestamp: int | None = None
    usage: PiUsage | None = None
    stopReason: str | None = None
    errorMessage: str | None = None
    api: str | None = None
    provider: str | None = None
    model: str | None = None
    toolCallId: str | None = None
    toolName: str | None = None
    isError: bool | None = None
    details: dict[str, t.Any] = Field(default_factory=dict)


class PiToolResult(PiEventBase):
    """The result payload of a finished tool execution."""

    content: list[PiContentBlock] = Field(default_factory=list)
    isError: bool | None = None


class SessionEvent(PiEventBase):
    """Session header (first event of every run)."""

    type: t.Literal["session"]
    version: int | None = None
    id: str | None = None
    timestamp: str | None = None
    cwd: str | None = None


class AgentStartEvent(PiEventBase):
    """The agent loop begins."""

    type: t.Literal["agent_start"]


class AgentEndEvent(PiEventBase):
    """Final event of a completed agent loop: the whole message list."""

    type: t.Literal["agent_end"]
    messages: list[PiMessage] = Field(default_factory=list)


class AgentSettledEvent(PiEventBase):
    """Post-run bookkeeping: the agent loop fully settled."""

    type: t.Literal["agent_settled"]


class TurnStartEvent(PiEventBase):
    """One agentic turn begins (a full model round-trip)."""

    type: t.Literal["turn_start"]


class TurnEndEvent(PiEventBase):
    """End of one agentic turn; carries the final assistant message."""

    type: t.Literal["turn_end"]
    message: PiMessage | None = None


class MessageStartEvent(PiEventBase):
    """A message begins streaming."""

    type: t.Literal["message_start"]
    message: PiMessage | None = None


class MessageEndEvent(PiEventBase):
    """A message is final: usage, stopReason and full content ride here."""

    type: t.Literal["message_end"]
    message: PiMessage | None = None


class MessageUpdateEvent(PiEventBase):
    """Streaming progress; `assistantMessageEvent` is opaque deltas."""

    type: t.Literal["message_update"]
    usage: PiUsage | None = None
    assistantMessageEvent: dict[str, t.Any] = Field(default_factory=dict)


class ToolExecutionStartEvent(PiEventBase):
    """A tool call begins; the trigger signal lives in `args` (skill reads)."""

    type: t.Literal["tool_execution_start"]
    toolCallId: str | None = None
    toolName: str | None = None
    args: dict[str, t.Any] = Field(default_factory=dict)


class ToolExecutionUpdateEvent(PiEventBase):
    """Streaming progress of a running tool call (e.g. bash output lines).

    `partialResult` is pi's live snapshot of the result being built; the
    authoritative final result rides `tool_execution_end`. Modeled after
    the observed stream (echo/sleep probe, 2026-09-29): content blocks
    accumulate, `details` may be absent on the first update.
    """

    type: t.Literal["tool_execution_update"]
    toolCallId: str | None = None
    toolName: str | None = None
    args: dict[str, t.Any] = Field(default_factory=dict)
    partialResult: dict[str, t.Any] = Field(default_factory=dict)


class ToolExecutionEndEvent(PiEventBase):
    """A tool call finished; the result transcript Phase-2 grading reads."""

    type: t.Literal["tool_execution_end"]
    toolCallId: str | None = None
    toolName: str | None = None
    result: PiToolResult | None = None


class AutoRetryStartEvent(PiEventBase):
    """pi retrying after a transient provider error."""

    type: t.Literal["auto_retry_start"]
    errorMessage: str | None = None


class AutoRetryEndEvent(PiEventBase):
    """Retry outcome; `success: false` carries the final error."""

    type: t.Literal["auto_retry_end"]
    success: bool | None = None
    finalError: str | None = None


# ---------------------------------------------------------------------------
# pi session files (the durable capture: `~/.pi/agent/sessions/**.jsonl`)
# ---------------------------------------------------------------------------
# pi session files carry the same message payloads as the event stream,
# parent-linked with ids and timestamps — but only the durable entries:
# no streaming deltas, no tool-execution events. A graded attempt's
# canonical capture is its session file; the event stream is this
# vocabulary plus a live firehose envelope.


class ModelChangeEntry(PiEventBase):
    """The session's model was set (`provider`, `modelId`)."""

    type: t.Literal["model_change"]
    id: str | None = None
    parentId: str | None = None
    timestamp: str | None = None
    provider: str | None = None
    modelId: str | None = None


class ThinkingLevelChangeEntry(PiEventBase):
    """The session's thinking level was set."""

    type: t.Literal["thinking_level_change"]
    id: str | None = None
    parentId: str | None = None
    timestamp: str | None = None
    thinkingLevel: str | None = None


class MessageEntry(PiEventBase):
    """One durable message; `message` is the same payload as the stream."""

    type: t.Literal["message"]
    id: str | None = None
    parentId: str | None = None
    timestamp: str | None = None
    message: PiMessage | None = None


class CompactionEntry(PiEventBase):
    """Context compaction marker; `summary` is the folded-away history."""

    type: t.Literal["compaction"]
    id: str | None = None
    parentId: str | None = None
    timestamp: str | None = None
    summary: str = ""


class LabelEntry(PiEventBase):
    """A user-assigned label on another entry (`targetId`)."""

    type: t.Literal["label"]
    id: str | None = None
    parentId: str | None = None
    timestamp: str | None = None
    targetId: str | None = None
    label: str | None = None


class CustomEntry(PiEventBase):
    """Extension-injected entry (`customType` + opaque `data`)."""

    type: t.Literal["custom"]
    id: str | None = None
    parentId: str | None = None
    timestamp: str | None = None
    customType: str | None = None
    data: dict[str, t.Any] = Field(default_factory=dict)


SessionEntry = t.Annotated[
    SessionEvent
    | ModelChangeEntry
    | ThinkingLevelChangeEntry
    | MessageEntry
    | CompactionEntry
    | LabelEntry
    | CustomEntry,
    Field(discriminator="type"),
]
"""Every entry type a pi session file is known to contain.

Same policy as the event vocabulary: an unmodeled entry type is
instrument debt and fails parsing, never silent data loss.
"""


PiEvent = t.Annotated[
    SessionEvent
    | AgentStartEvent
    | AgentEndEvent
    | AgentSettledEvent
    | TurnStartEvent
    | TurnEndEvent
    | MessageStartEvent
    | MessageEndEvent
    | MessageUpdateEvent
    | ToolExecutionStartEvent
    | ToolExecutionUpdateEvent
    | ToolExecutionEndEvent
    | AutoRetryStartEvent
    | AutoRetryEndEvent,
    Field(discriminator="type"),
]
"""Every event type pi --mode json is known to emit.

Anything else fails validation — a pi release that adds event types (or
content shapes) is an unmodeled instrument input and must surface as an
error, not silently-skipped stream data.
"""
