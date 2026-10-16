"""The attempt runner: one headless pi invocation -> an AttemptCore.

Ported from `eval/run_trigger_eval.py::run_once` (validated over six
protocol generations); the mechanics below are the parts that must not
be reinvented:

- invocation shape: `pi --mode json --no-extensions -nc --no-skills
  --skill <path>... <query>` from the neutral cwd /tmp; the query is
  the last positional argument. The runner (`run`, §12) always runs
  with pi's own session persistence on (the scratch HOME comes in via
  `env`), because the session file is the canonical capture a bundle
  needs — yielded attempts included.
- trigger detection matches the *exact forwarded path strings*, not
  `<frontmatter-name>/SKILL.md` — name == dir name is not an invariant.
  `n_tool_calls` counts ALL `tool_execution_start` events; a trigger is
  never inferred from the answer text.
- kill mechanics: `start_new_session=True` + SIGTERM to the process
  group (descendants die with the decision; by-name cleanup is
  unreliable), best-effort SIGKILL escalation after a grace period,
  bounded wait, unbounded final reaping. Early exit and the watchdog use
  the same helper.
- stderr drain thread: prevents a full-pipe deadlock and keeps the
  Case-E diagnostic tail; drain-side appends from timer threads are safe.
- error taxonomy and exit-code classification, in order.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
import typing as t
from collections import deque
from collections.abc import Callable
from pathlib import Path

from pi_agent_json import (
    AutoRetryEndEvent,
    AutoRetryStartEvent,
    MessageEndEvent,
    MessageEntry,
    PiEventParseError,
    PiMessage,
    PiTextBlock,
    PiToolCallBlock,
    SessionEntry,
    ToolExecutionStartEvent,
    UsageAccumulator,
    parse_pi_event,
)

from ..models import AttemptCore, EarlyExit, SkillRef, ToolCall

RUN_CWD = "/tmp"  # neutral: the skills repo's own files bias the agent

_ANSWER_LIMIT = 2000
_TOOL_ARG_CHARS = 400  # per-arg trim: a single `write` must not blow the record
_TOOL_CALL_CAP = 500  # transcript cap: pathological loops stay bounded
_STDERR_LINES = 10
_STDERR_CHARS = 300
_REAP_GRACE_S = 15.0
_KILL_GRACE_S = 5.0


def _terminate_group(
    proc: subprocess.Popen, stderr_tail: deque[str], grace_s: float = _KILL_GRACE_S
) -> None:
    """Kill pi and everything it spawned.

    SIGTERM to the process group so descendants die with the decision
    (by-name cleanup is unreliable), best-effort SIGKILL escalation after
    a grace period.
    """
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError) as exc:
        stderr_tail.append(f"killpg(SIGTERM): {exc!r}"[:_STDERR_CHARS])
        return

    def _escalate() -> None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError) as exc:
            stderr_tail.append(f"killpg(SIGKILL, best-effort): {exc!r}"[:_STDERR_CHARS])

    timer = threading.Timer(grace_s, _escalate)
    timer.daemon = True
    timer.start()


def _trim_arg(value: t.Any) -> t.Any:  # noqa: ANN401 — passthrough by design
    """Trim one tool-call argument for the recorded transcript.

    Large blobs (file contents through `write`, pasted logs) are capped:
    the transcript is for action-sequence checks, not for replaying the
    whole session.
    """
    if isinstance(value, str) and len(value) > _TOOL_ARG_CHARS:
        return value[:_TOOL_ARG_CHARS] + f"…<{len(value) - _TOOL_ARG_CHARS} more>"
    return value


def compose_error(
    stop_reason: str | None,
    error_message: str | None,
    extra: str | None = None,
) -> str | None:
    """Join the error signals into the record's one error string."""
    return (
        "; ".join(str(part) for part in (stop_reason, error_message, extra) if part)
        or None
    )


class StreamAggregator:
    """The one pi-format consumer: live streaming and captured replay.

    Owns everything that must not drift between executing an attempt and
    replaying its captured session: trigger detection on the exact
    forwarded SKILL.md path, tool-call counting, usage/cost
    accumulation, stopReason-based error classification, and the last
    non-empty assistant text as the answer. Both pi formats reduce to
    the same signal set — `feed` takes `--mode json` stream lines, `feed_session_entry`
    takes parsed session-file entries — so live and replay share the
    identical state machine. Callers add the mechanics: the live runner
    wraps `feed` with kill/watchdog and exit-code observation, the
    replay feeds a captured session file and adds nothing.
    """

    def __init__(
        self,
        skills: list[SkillRef],
        *,
        collect_tool_calls: bool,
        stderr_tail: t.MutableSequence[str],
        stop_fn: Callable[[], bool] | None = None,
    ) -> None:
        self.skills = skills
        self.collect_tool_calls = collect_tool_calls
        self.stderr_tail = stderr_tail
        self._stop_fn = stop_fn
        self.triggered: dict[str, bool] = {skill.name: False for skill in skills}
        self.answer = ""
        self.stop_reason: str | None = None
        self.error_message: str | None = None
        self.retries_seen = 0
        self.n_tool_calls = 0
        self.n_events = 0
        self.first_trigger_index: int | None = None
        self.first_trigger_skill: str | None = None
        self.usage_acc = UsageAccumulator()
        self.tool_calls: list[ToolCall] = []

    # -- format-independent signals -------------------------------------
    # One signal set serves both pi formats: the live event stream calls
    # on_tool_call from tool-execution events, the session-file frontend
    # from the assistant message's toolCall content items — same data,
    # different envelope.

    def on_tool_call(self, tool_name: str | None, args: dict[str, t.Any]) -> None:
        """Signal: a tool call was issued."""
        self.n_tool_calls += 1
        if self.collect_tool_calls and len(self.tool_calls) < _TOOL_CALL_CAP:
            self.tool_calls.append(
                ToolCall(
                    tool=tool_name or "",
                    args={key: _trim_arg(value) for key, value in args.items()},
                ),
            )
        if tool_name in ("read", "bash"):
            blob = json.dumps(args)
            for skill in self.skills:
                if self.triggered[skill.name]:
                    continue
                # Match the exact forwarded path (§2), not the name.
                if f"{skill.path}/SKILL.md" in blob:
                    self.triggered[skill.name] = True
                    if self.first_trigger_index is None:
                        self.first_trigger_index = self.n_tool_calls
                        self.first_trigger_skill = skill.name

    def on_assistant_message(self, message: PiMessage | None) -> None:
        """Signal: an assistant message finalized.

        The identical payload in both formats: usage (incl. nested cost),
        stopReason/errorMessage, and the text blocks — of which the last
        non-empty one is the answer.
        """
        if message is None:
            return
        self.usage_acc.add(message.usage)
        # pi marks failed/aborted runs on the final assistant message
        # (stopReason "error"/"aborted", e.g. API 429/5xx).
        if message.stopReason in ("error", "aborted"):
            self.stop_reason = message.stopReason
            self.error_message = message.errorMessage or self.error_message
        for content in message.content:
            if isinstance(content, PiTextBlock) and content.text.strip():
                self.answer = content.text

    def on_retry_error(self, message: str | None) -> None:
        """Signal: an auto-retry surfaced an error (live stream only)."""
        self.error_message = message or self.error_message

    def feed(self, line: str) -> bool:
        """Consume one stripped JSONL line; True = stop consuming.

        Stops on an unmodeled event (instrument debt — the attempt fails
        loudly rather than silently ignoring stream data) or on a stop
        condition (live runner only; checked after tool events, where
        the cap/trigger state can change). Torn lines (interrupted
        appends at a kill boundary) are noted and skipped.
        """
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            self.stderr_tail.append(f"torn line skipped: {line[:80]}"[:_STDERR_CHARS])
            return False
        try:
            ev = parse_pi_event(raw)
        except PiEventParseError as exc:
            self.stderr_tail.append(f"event parse: {exc}"[:_STDERR_CHARS])
            self.error_message = f"pi event stream parse error: {exc}"
            return True
        self.n_events += 1
        tool_event = self._dispatch(ev)
        return bool(tool_event and self._stop_fn and self._stop_fn())

    def feed_session_entry(self, entry: SessionEntry) -> None:
        """Consume one parsed pi session-file entry (replay frontend).

        Same signals as the event stream, sourced from the session
        vocabulary: tool calls ride the assistant message's `toolCall`
        content items (session files have no tool-execution events), and
        auto-retries have no entry representation, so `retries_seen`
        stays 0 on replay — that evidence lives in the ops log.
        """
        if not isinstance(entry, MessageEntry):
            return
        message = entry.message
        if message is None or message.role != "assistant":
            return
        for content in message.content:
            if isinstance(content, PiToolCallBlock):
                self.on_tool_call(content.name, content.arguments)
        self.on_assistant_message(message)

    def _dispatch(  # noqa: PLR0912 — one branch per event type is the point
        self,
        ev: t.Any,
    ) -> bool:  # noqa: ANN401 — event union
        """Fold one event into the signals; True for tool events."""
        if isinstance(ev, ToolExecutionStartEvent):
            self.on_tool_call(ev.toolName, ev.args)
            return True
        if isinstance(ev, AutoRetryStartEvent):
            self.retries_seen += 1
            self.on_retry_error(ev.errorMessage)
            return False
        if isinstance(ev, AutoRetryEndEvent):
            if ev.success is False:
                self.on_retry_error(ev.finalError)
            return False
        if isinstance(ev, MessageEndEvent):
            if ev.message is not None and ev.message.role == "assistant":
                self.on_assistant_message(ev.message)
            return False
        # Every other modeled event type (lifecycle, streaming deltas,
        # tool results, …) carries no Phase-1 signal and is ignored.
        return False

    def core_fields(self, *, answer_limit: int) -> dict[str, t.Any]:
        """Return the AttemptCore kwargs both builders share.

        `clean`/`errored`/`error` are deliberately excluded: the live
        runner decides them from exit codes and kills, the replay from
        the stream alone.
        """
        return {
            "triggered": self.triggered,
            "first_trigger_skill": self.first_trigger_skill,
            "trigger_call_index": self.first_trigger_index,
            "retries_seen": self.retries_seen,
            "stderr_tail": list(self.stderr_tail),
            "answer": self.answer[:answer_limit],
            "n_tool_calls": self.n_tool_calls,
            "tool_calls": self.tool_calls,
            "usage": self.usage_acc.usage,
        }


def run_attempt(  # noqa: PLR0913, PLR0912, PLR0915 — see docstring
    *,
    query: str,
    skills: list[SkillRef],
    model: str | None,
    timeout: int,
    run: int = 0,
    stop_on_skill_read: bool = False,
    max_tool_calls: int | None = None,
    answer_limit: int = _ANSWER_LIMIT,
    env: dict[str, str] | None = None,
    pi: str | None = None,
    stream_out: Path | None = None,
) -> AttemptCore:
    """One headless pi invocation with the given skills pinned.

    Streams stdout line by line (evidence survives every kill — the V3
    lesson) and enforces the wall-clock deadline with a watchdog group
    kill. The stop conditions are composable, set by the caller (the
    cell's policy, not this function's):

    - `stop_on_skill_read`: yield on the first SKILL.md read of ANY
      loaded skill — a misroute is just as decided as a clean hit, and
      post-trigger behavior carries no trigger information. The
      trigger-cell condition.
    - `max_tool_calls`: the turn cap, decoupled from the yield — the
      bound for should-not cells (no skill may fire) and the runaway
      guard for task cells alike.

    With no stop condition configured the attempt runs to its natural
    end (the wall-clock deadline or pi's own completion) — the task-cell
    default, where the whole attempt is what gets graded.

    `stream_out` (the runner passes the bundle's `stream.jsonl`): pi's
    stdout event stream is teed there line by line, flushed as it is
    consumed. pi's own session file only appends complete messages, so
    a killed attempt (yield, cap, watchdog) loses its in-flight tool
    calls from the session file — the stream tee is the replay source
    that fills that flush gap (grade prefers it for killed attempts).

    Ported verbatim from the validated V6 loop; the pylint complexity
    thresholds are deliberately suppressed (see the def line) rather
    than splitting the streaming loop — a refactor here risks exactly
    the evidence-loss class of regressions the V3 incident taught us to
    fear.

    The action transcript is always recorded (trimmed) and the answer
    kept up to `answer_limit` chars; pi's session persistence is always
    on — the capture bundle's canonical artifact.
    """
    # `pi` (explicit --pi, validated by the caller) or PATH resolution.
    cmd = [
        pi or "pi",
        "--mode",
        "json",
        "--no-extensions",
        "-nc",
        "--no-skills",
    ]
    if model:
        cmd += ["--model", model]
    for skill in skills:
        cmd += ["--skill", skill.path]
    cmd.append(query)

    stop_reason: EarlyExit | None = None
    t0 = time.monotonic()

    # Own process group: our kills take descendants too (§3).
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=RUN_CWD,
        start_new_session=True,
        env=env,
    )
    stderr_tail: deque[str] = deque(maxlen=_STDERR_LINES)

    def _drain_stderr() -> None:
        """Drain the child's stderr in a daemon thread (§4)."""
        for errline in proc.stderr:
            stderr_tail.append(errline.rstrip()[:_STDERR_CHARS])

    threading.Thread(target=_drain_stderr, daemon=True).start()

    def _stop_condition() -> bool:
        """Decide whether a configured stop condition has fired.

        Checked after every tool event: a yield cell stops once its
        trigger question is answered (the first skill read, whatever
        skill it was); a cap stops the attempt when it exhausts its
        turn budget, with or without a read. The skill-read yield is
        evaluated first, so a trigger-cell run stops at the read event
        and never observes the cap.
        """
        nonlocal stop_reason
        if stop_on_skill_read and agg.first_trigger_index is not None:
            stop_reason = "trigger"
            return True
        if max_tool_calls is not None and agg.n_tool_calls >= max_tool_calls:
            stop_reason = "max_tool_calls"
            return True
        return False

    agg = StreamAggregator(
        skills,
        collect_tool_calls=True,
        stderr_tail=stderr_tail,
        stop_fn=_stop_condition,
    )

    watchdog_fired = threading.Event()

    def _kill_at_deadline() -> None:
        """Watchdog fire: same group-kill helper as the early exit (§3)."""
        watchdog_fired.set()
        _terminate_group(proc, stderr_tail)

    watchdog = threading.Timer(timeout, _kill_at_deadline)
    watchdog.start()

    exit_code: int | None = None
    tee = None if stream_out is None else stream_out.open("w")
    try:
        for raw_line in proc.stdout:
            line = raw_line.strip()
            if not line:
                continue
            if tee is not None:
                tee.write(line + "\n")
                tee.flush()
            if agg.feed(line):
                # Unmodeled event (fail loudly) or a stop condition: same
                # group kill as the watchdog (§3).
                _terminate_group(proc, stderr_tail)
                break
        # EOF (or post-kill): reap the child (§3: bounded wait + SIGKILL
        # escalation for a child that ignores SIGTERM; unbounded last resort).
        try:
            exit_code = proc.wait(timeout=_REAP_GRACE_S)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError) as exc:
                stderr_tail.append(f"killpg(SIGKILL) reap: {exc!r}"[:_STDERR_CHARS])
            exit_code = proc.wait()
    finally:
        watchdog.cancel()
        if tee is not None:
            tee.close()
        proc.stdout.close()
        time.sleep(0.05)  # let the stderr thread drain the last lines
        proc.stderr.close()

    timed_out = watchdog_fired.is_set() and stop_reason is None
    # Our own group kills surface as exit -15/-9: by design (a stop
    # condition or the watchdog), not an error. §5 classification, in order.
    errored = agg.stop_reason is not None or (
        exit_code not in (0, None) and not timed_out and stop_reason is None
    )
    answer_out = agg.answer[:answer_limit]
    if timed_out:
        answer_out = (answer_out + " <timeout>").strip()
    elif stop_reason is not None:
        answer_out = (answer_out + f" <early-exit:{stop_reason}>").strip()

    return AttemptCore(
        query=query,
        run=run,
        clean=not (errored or timed_out),
        early_exit=stop_reason,
        timed_out=timed_out,
        errored=errored,
        exit_code=exit_code,
        error=None
        if stop_reason is not None
        else compose_error(
            agg.stop_reason,
            agg.error_message,
            None if timed_out or exit_code in (0, None) else f"exit={exit_code}",
        ),
        dur_s=round(time.monotonic() - t0),
        **agg.core_fields(answer_limit=answer_limit),
    )
