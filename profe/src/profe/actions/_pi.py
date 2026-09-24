"""The attempt runner: one headless pi invocation -> an AttemptCore.

Ported from `eval/run_trigger_eval.py::run_once` (validated over six
protocol generations); the mechanics below are the parts that must not
be reinvented:

- invocation shape: `pi --mode json --no-session --no-extensions -nc
  --no-skills --skill <path>... <query>` from the neutral cwd /tmp; the
  query is the last positional argument.
- trigger detection matches the *exact forwarded path strings*, not
  `<frontmatter-name>/SKILL.md` — name == dir name is not an invariant.
  `n_tool_calls` counts ALL `tool_execution_start` events; a trigger is
  never inferred from the answer text.
- kill mechanics: `start_new_session=True` + SIGTERM to the process
  group (descendants die with the decision; by-name cleanup is
  unreliable), best-effort SIGKILL escalation after a grace period
  (AppArmor blocks SIGKILL from this sandbox — logged, never relied on),
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
from collections import deque

from pi_agent_json import (
    AutoRetryEndEvent,
    AutoRetryStartEvent,
    CostBreakdown,
    MessageEndEvent,
    PiEventParseError,
    PiTextBlock,
    PiUsage,
    ToolExecutionStartEvent,
    parse_pi_event,
)

from ..models import AttemptCore, EarlyExit, SkillRef, UsageV2

RUN_CWD = "/tmp"  # neutral: the skills repo's own files bias the agent

_ANSWER_LIMIT = 2000
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
    a grace period (AppArmor blocks SIGKILL from this sandbox — the
    failure is logged, never relied on).
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


def _extra_usage(usage_acc: dict[str, float]) -> dict[str, float | int]:
    """Fold pi's extra usage keys into the Usage extras.

    cacheRead, cacheWrite, totalTokens, ... are normalized the same way as
    the core fields.
    """
    return {
        k: (round(v, 4) if k == "cost" else int(v))
        for k, v in usage_acc.items()
        if k not in ("input", "output", "cost")
    }


def _accumulate_usage(
    mu: PiUsage | None, usage_acc: dict[str, float], cost_acc: dict[str, float]
) -> None:
    """Fold one message's usage into the attempt accumulators.

    Schema-2 records keep pi's nested cost breakdown verbatim (the
    input-vs-cacheRead split is the Phase-2 overhead signal); token keys
    accumulate flat.
    """
    if mu is None:
        return
    for key, value in (
        ("input", mu.input),
        ("output", mu.output),
        ("cacheRead", mu.cacheRead),
        ("cacheWrite", mu.cacheWrite),
        ("reasoning", mu.reasoning),
        ("totalTokens", mu.totalTokens),
    ):
        if value:
            usage_acc[key] = usage_acc.get(key, 0) + value
    for key, value in (
        ("input", mu.cost.input),
        ("output", mu.cost.output),
        ("cacheRead", mu.cost.cacheRead),
        ("cacheWrite", mu.cost.cacheWrite),
        ("total", mu.cost.total),
    ):
        cost_acc[key] = cost_acc.get(key, 0.0) + value


def run_attempt(  # noqa: PLR0913, PLR0912, PLR0915 — see docstring
    *,
    index: int,
    query: str,
    expected_skills: list[str],
    run: int,
    attempt: int,
    skills: list[SkillRef],
    model: str | None,
    timeout: int,
    max_tool_calls: int | None,
    early_exit: bool = True,
) -> AttemptCore:
    """One headless pi invocation with the given skills pinned.

    Streams stdout line by line (evidence survives every kill — the V3
    lesson) and enforces the wall-clock deadline with a watchdog group
    kill. With `early_exit` (the Phase-1 lifecycle) the run is killed the
    moment its trigger question is answered: on the first skill read, or,
    with no skill read, after `max_tool_calls` tool calls — the cutoff
    applies to both query classes (a should-trigger run with no read
    within the cutoff is non-trigger evidence modulo the documented
    cutoff approximation).

    Ported verbatim from the validated V6 loop; the pylint complexity
    thresholds are deliberately suppressed (see the def line) rather than
    splitting the streaming loop — a refactor here risks exactly the
    evidence-loss class of regressions the V3 incident taught us to fear.
    """
    cmd = [
        "pi",
        "--mode",
        "json",
        "--no-session",
        "--no-extensions",
        "-nc",
        "--no-skills",
    ]
    if model:
        cmd += ["--model", model]
    for skill in skills:
        cmd += ["--skill", skill.path]
    cmd.append(query)

    triggered = {skill.name: False for skill in skills}
    answer = ""
    stop_reason: str | None = None
    error_message: str | None = None
    retries_seen = 0
    n_tool_calls = 0
    first_trigger_index: int | None = None
    first_trigger_skill: str | None = None
    early_exit_reason: EarlyExit | None = None
    usage_acc: dict[str, float] = {}
    cost_acc: dict[str, float] = {}
    t0 = time.monotonic()

    # Own process group: our kills take descendants too (§3).
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=RUN_CWD,
        start_new_session=True,
    )
    stderr_tail: deque[str] = deque(maxlen=_STDERR_LINES)

    def _drain_stderr() -> None:
        """Drain the child's stderr in a daemon thread (§4)."""
        for errline in proc.stderr:
            stderr_tail.append(errline.rstrip()[:_STDERR_CHARS])

    threading.Thread(target=_drain_stderr, daemon=True).start()

    watchdog_fired = threading.Event()

    def _kill_at_deadline() -> None:
        """Watchdog fire: same group-kill helper as the early exit (§3)."""
        watchdog_fired.set()
        _terminate_group(proc, stderr_tail)

    watchdog = threading.Timer(timeout, _kill_at_deadline)
    watchdog.start()

    def _early_exit() -> bool:
        """Decide whether the run's trigger question is already answered.

        Checked after every event: a Phase-1 run is killed once its
        trigger question is answered, whatever the class — a misroute is
        just as decided as a clean hit, and post-trigger behavior carries
        no Phase-1 information.
        """
        nonlocal early_exit_reason
        if not early_exit:
            return False
        if first_trigger_index is not None:
            early_exit_reason = "trigger"
            return True
        cutoff_hit = max_tool_calls and n_tool_calls >= max_tool_calls
        if cutoff_hit and first_trigger_index is None:
            early_exit_reason = "max_tool_calls"
            return True
        return False

    exit_code: int | None = None
    try:
        for raw_line in proc.stdout:
            line = raw_line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                # A torn line (interrupted append at a kill boundary) is
                # not stream data — skip it; the complete attempts are
                # already recorded.
                continue
            try:
                ev = parse_pi_event(raw)
            except PiEventParseError as exc:
                # An unmodeled pi event is instrument debt, not evidence:
                # fail the attempt loudly (errored → retried → escalated)
                # instead of silently ignoring stream data.
                stderr_tail.append(f"event parse: {exc}"[:_STDERR_CHARS])
                error_message = f"pi event stream parse error: {exc}"
                _terminate_group(proc, stderr_tail)
                break
            if isinstance(ev, ToolExecutionStartEvent):
                n_tool_calls += 1
                if ev.toolName in ("read", "bash"):
                    blob = json.dumps(ev.args)
                    for skill in skills:
                        if triggered[skill.name]:
                            continue
                        # Match the exact forwarded path (§2), not the name.
                        if f"{skill.path}/SKILL.md" in blob:
                            triggered[skill.name] = True
                            if first_trigger_index is None:
                                first_trigger_index = n_tool_calls
                                first_trigger_skill = skill.name
                if _early_exit():
                    _terminate_group(proc, stderr_tail)
                    break
            elif isinstance(ev, AutoRetryStartEvent):
                retries_seen += 1
                error_message = ev.errorMessage or error_message
            elif isinstance(ev, AutoRetryEndEvent):
                if ev.success is False:
                    error_message = ev.finalError or error_message
            elif isinstance(ev, MessageEndEvent):
                msg = ev.message
                _accumulate_usage(msg.usage if msg else None, usage_acc, cost_acc)
                if msg is None or msg.role != "assistant":
                    continue
                # pi marks failed/aborted runs on the final assistant
                # message (stopReason "error"/"aborted", e.g. API 429/5xx).
                if msg.stopReason in ("error", "aborted"):
                    stop_reason = msg.stopReason
                    error_message = msg.errorMessage or error_message
                for content in msg.content:
                    if isinstance(content, PiTextBlock) and content.text.strip():
                        answer = content.text
            # Every other modeled event type (lifecycle, streaming
            # deltas, tool results, …) carries no Phase-1 signal and is
            # ignored; unmodeled shapes fail the attempt above.
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
        proc.stdout.close()
        time.sleep(0.05)  # let the stderr thread drain the last lines
        proc.stderr.close()

    timed_out = watchdog_fired.is_set() and early_exit_reason is None
    # Our own group kills surface as exit -15/-9: by design (early exit or
    # watchdog), not an error. §5 classification, in order.
    errored = stop_reason is not None or (
        exit_code not in (0, None) and not timed_out and early_exit_reason is None
    )
    answer_out = answer[:_ANSWER_LIMIT]
    if timed_out:
        answer_out = (answer_out + " <timeout>").strip()
    elif early_exit_reason is not None:
        answer_out = (answer_out + f" <early-exit:{early_exit_reason}>").strip()

    usage = UsageV2(
        input=int(usage_acc.get("input", 0)),
        output=int(usage_acc.get("output", 0)),
        cost=CostBreakdown(
            input=round(cost_acc.get("input", 0.0), 6),
            output=round(cost_acc.get("output", 0.0), 6),
            cacheRead=round(cost_acc.get("cacheRead", 0.0), 6),
            cacheWrite=round(cost_acc.get("cacheWrite", 0.0), 6),
            total=round(cost_acc.get("total", 0.0), 6),
        ),
        **_extra_usage(usage_acc),
    )
    return AttemptCore(
        index=index,
        query=query,
        expected_skills=expected_skills,
        run=run,
        attempt=attempt,
        clean=not (errored or timed_out),
        triggered=triggered,
        first_trigger_skill=first_trigger_skill,
        trigger_call_index=first_trigger_index,
        early_exit=early_exit_reason,
        timed_out=timed_out,
        errored=errored,
        exit_code=exit_code,
        error=None
        if early_exit_reason is not None
        else "; ".join(
            part
            for part in (
                stop_reason,
                error_message,
                None if timed_out or exit_code in (0, None) else f"exit={exit_code}",
            )
            if part
        )
        or None,
        retries_seen=retries_seen,
        stderr_tail=list(stderr_tail),
        answer=answer_out,
        n_tool_calls=n_tool_calls,
        usage=usage,
        dur_s=round(time.monotonic() - t0),
    )
