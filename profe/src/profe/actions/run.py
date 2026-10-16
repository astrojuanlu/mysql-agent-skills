"""`profe run`: one attempt inside the caller's boundary (§12).

The runner half of the boundary-agnostic split (SANDBOX-EXPLORATION
§12): the caller decides how and where profe runs — bare host,
bubblewrap, podman, an LXD VM, a Testflinger machine — and provisions
the boundary; run does the rest:

- stage the task's artifacts into a fresh read-only staging dir;
- launch ONE headless pi attempt with pi's own session persistence ON
  (the session file is the canonical capture, copied out verbatim);
- execute the task's precheck, if any, at run time — inside the
  boundary, where live-cluster ground truth is reachable — and embed
  it in the bundle, so the grader never needs the boundary again;
- emit the capture bundle: `prompt.txt` (the exact prompt),
  `session.jsonl` (pi's own file, verbatim), `capture.json` (sidecar:
  task/run identity, wall time, pi exit code and version, the
  forwarded skill paths, any stop reason, session source),
  `stream.jsonl` (profe's own tee of pi's event stream — the replay
  source for killed attempts, whose in-flight tool calls pi's session
  file never flushes), plus `precheck-output.txt`/`precheck.json` when
  prechecked.

Rubric-free by construction: run cannot load rubrics — they must never
enter the boundary for rubric tasks (the threat model). The stop
conditions are the yield surface: `--stop-on skill-read` kills the
attempt at the first SKILL.md read of any loaded skill (the trigger-
cell condition — the caller regains control and this run ends there;
a future in-process driver could instead log and continue),
`--max-tool-calls N` caps the attempt without yielding on reads. No
retries, no num-runs: one invocation is one attempt; a retry is a
fresh boundary, which is the caller's loop.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from ..models import CaptureSidecar, RunOptions
from . import _pi
from ._utils import attempt_prompt, resolve_skills, run_precheck, stage_artifacts
from .grade import load_tasks

_PI_CONFIG_FILES = ("auth.json", "models.json", "models-store.json", "settings.json")


def _stage_pi_config(source: str, scratch_home: Path) -> None:
    """Populate the scratch HOME's .pi/agent from the source config dir.

    The boundary gets inputs, not host state: pi config rides in as
    input (the caller pushes/mounts it), never via the ambient HOME.
    """
    agent = scratch_home / ".pi" / "agent"
    agent.mkdir(parents=True)
    src = Path(source).expanduser()
    for name in _PI_CONFIG_FILES:
        path = src / name
        if path.is_file():
            shutil.copyfile(path, agent / name)
    settings = agent / "settings.json"
    if settings.is_file():
        data = json.loads(settings.read_text())
        # pi auto-installs the `packages` list at startup — a live npm
        # call that a restricted boundary would block and thereby break
        # startup (and it adds extension drift between attempts).
        # Stripped unconditionally.
        data.pop("packages", None)
        settings.write_text(json.dumps(data, indent=1))


def _resolve_pi(pi_flag: str | None) -> str:
    """Resolve the pi binary: explicit `--pi` wins, else PATH.

    An explicit `--pi` is validated (exists, is a file, executable) —
    the resolved path is what runs and what the sidecar records;
    without the flag, `pi` resolves on PATH, first entry wins.
    """
    if pi_flag is not None:
        path = Path(pi_flag).expanduser().resolve()
        if not path.is_file() or not os.access(path, os.X_OK):
            msg = f"--pi: not an executable file: {pi_flag}"
            raise ValueError(msg)
        return str(path)
    which = shutil.which("pi")
    if which is None:
        msg = "pi not found on PATH (pass --pi to pin the instrument)"
        raise ValueError(msg)
    return which


def _pi_version(pi_bin: str) -> str:
    """Probe the resolved binary once; a broken pi fails the run loudly."""
    try:
        proc = subprocess.run(  # noqa: PLW1510 — exit code is checked below
            [pi_bin, "--version"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=_pi.RUN_CWD,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        msg = f"pi is not usable ({pi_bin}): {exc!r}"
        raise ValueError(msg) from exc
    if proc.returncode != 0:
        msg = f"pi --version failed: {proc.stderr.strip()[:200]}"
        raise ValueError(msg)
    return proc.stdout.strip()


def _newest_session(scratch_home: Path) -> Path | None:
    """Newest pi session file under the scratch HOME (T4 glob semantics)."""
    root = scratch_home / ".pi" / "agent" / "sessions"
    if not root.is_dir():
        return None
    candidates = sorted(root.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime)
    return candidates[-1] if candidates else None


def run_capture_bundle(
    options: RunOptions,
    progress: Callable[[str], None] | None = None,
) -> Path:
    """Run one attempt and emit its capture bundle; returns the bundle dir.

    The instrument is the explicit `--pi` binary (validated) or `pi`
    resolved on PATH (first entry wins — boundaries pin the instrument
    by PATH order); the resolved path and version are recorded in the
    sidecar either way. The attempt runs with the scratch HOME the
    runner created (pi config copied in from `--pi-config`), so no
    boundary-internal state leaks between attempts beyond what the
    caller's provisioning already guarantees. `--extra-metadata` labels ride
    into the sidecar and from there into the grade record.
    """
    say = progress or (lambda _msg: None)
    tasks = load_tasks(options.tasks)
    matches = [task for task in tasks if task.id == options.task_id]
    if not matches:
        msg = f"task not found in {options.tasks}: {options.task_id}"
        raise ValueError(msg)
    task = matches[0]
    skills = resolve_skills(options.skills)
    staging = stage_artifacts(task.artifacts, options.artifacts_dir)
    prompt = attempt_prompt(task, staging)

    out = Path(options.out)
    out.mkdir(parents=True, exist_ok=True)
    pi_bin = _resolve_pi(options.pi)
    pi_version = _pi_version(pi_bin)

    scratch_home = Path(tempfile.mkdtemp(prefix="profe-run-home-", dir=_pi.RUN_CWD))
    _stage_pi_config(options.pi_config, scratch_home)
    env = dict(os.environ, HOME=str(scratch_home))

    say(f"run: task {task.id} run {options.run} -> {out}")
    core = _pi.run_attempt(
        query=prompt,
        skills=skills,
        model=options.model,
        timeout=options.timeout,
        run=options.run,
        stop_on_skill_read=options.stop_on_skill_read,
        max_tool_calls=options.max_tool_calls,
        answer_limit=30000,
        env=env,
        pi=pi_bin,
        stream_out=out / "stream.jsonl",
    )

    session = _newest_session(scratch_home)
    if session is None:
        # pi never persisted a session (an early death): without it
        # there is nothing to grade. Fail loudly with the diagnostics.
        tail = " | ".join(core.stderr_tail[-3:])
        msg = (
            f"no session file under {scratch_home}/.pi/agent/sessions — "
            f"pi died before persisting (exit={core.exit_code}; stderr: {tail})"
        )
        raise ValueError(msg)
    shutil.copyfile(session, out / "session.jsonl")
    (out / "prompt.txt").write_text(prompt)
    sidecar = CaptureSidecar(
        task_id=task.id,
        run=options.run,
        dur_seconds=core.dur_s,
        pi_exit_code=core.exit_code,
        pi_version=pi_version,
        session_source=str(session),
        pi_path=pi_bin,
        skills=skills,
        early_exit=core.early_exit,
        extra_metadata=options.extra_metadata,
    )
    (out / "capture.json").write_text(json.dumps(sidecar.model_dump(), indent=1))

    if task.precheck:
        precheck_output, precheck_json = run_precheck(task.precheck)
        (out / "precheck-output.txt").write_text(precheck_output or "")
        if precheck_json is not None:
            (out / "precheck.json").write_text(json.dumps(precheck_json, indent=1))
        say(f"run: precheck captured ({'json' if precheck_json else 'output only'})")

    say(
        f"run: bundle {out} — task {task.id} run {options.run}, "
        f"pi {pi_version}, clean={core.clean}, dur={core.dur_s}s, "
        f"{core.n_tool_calls} tool calls",
    )
    return out
