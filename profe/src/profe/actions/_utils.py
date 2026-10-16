"""Shared helpers: skill pinning, runner mechanics, task prechecks.

Skill resolution is one authoritative copy (imported by `compile` for
the startup checks and by `run` for the forwards): names come from the
frontmatter, paths are the exact strings pi receives — trigger
detection and grade metadata match those verbatim.

The runner mechanics (staging, prompt composition, prechecks) live
here too, one authoritative copy imported by `run`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import typing as t
from pathlib import Path

from ..models import SkillRef, Task
from . import _pi

_PROBE_TIMEOUT_S = 120
_PROBE_TAIL_CHARS = 4000


def resolve_skills(paths: list[str]) -> list[SkillRef]:
    """Resolve `--skills` entries to pinned skill refs.

    Accepts skill directories (SKILL.md inside) or SKILL.md files (the
    forwarded path is the parent directory). Names come from the
    frontmatter `name:` field, falling back to the directory name.
    Invalid paths are a hard error: a silently missing competitor
    corrupts trigger accounting.
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


def stage_artifacts(artifacts: list[str], artifacts_dir: str) -> Path:
    """Copy the task's artifacts into a fresh staging dir for the attempt.

    A fresh copy per attempt: an agent must not be able to edit the
    artifacts a later cell (or a retry) is graded against. The grader
    always grounds against the originals under `artifacts_dir`.
    """
    staging = Path(tempfile.mkdtemp(prefix="profe-t2-", dir=_pi.RUN_CWD))
    for rel in artifacts:
        src = Path(artifacts_dir) / rel
        if not src.is_file():
            msg = f"artifact does not exist: {src}"
            raise ValueError(msg)
        dest = staging / src.name
        shutil.copyfile(src, dest)
        dest.chmod(0o444)  # read-only: discourage in-run tampering
    return staging


def attempt_prompt(task: Task, staging: Path) -> str:
    """Build the prompt one attempt receives: task + artifacts + contract.

    The runner always knows the staging dir it just created — there is
    no unknown-staging phrasing: callers outside the runner never
    compose prompts (§12: the boundary gets inputs, not prompt logic).
    A contract-less task (a compiled trigger cell) gets no contract
    block — the attempt yields before any verdict block could exist.
    """
    prompt = task.prompt
    if task.artifacts:
        names = ", ".join(Path(rel).name for rel in task.artifacts)
        prompt += f"\n\nThe artifacts for this task are in {staging}: {names}."
    if task.contract:
        prompt += f"\n\n{task.contract}"
    return prompt


def run_precheck(command: str) -> tuple[str | None, dict[str, t.Any] | None]:
    """Run the task's precheck command; capture output tail and parsed JSON.

    Executes in whatever context calls it — for the `run` verb (§12),
    inside the attempt boundary, where live-cluster ground truth is
    reachable; the result is embedded in the capture bundle.
    """
    try:
        proc = subprocess.run(  # noqa: PLW1510 — exit code is checked below
            ["bash", "-c", command],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_S,
            cwd=_pi.RUN_CWD,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"precheck failed: {exc!r}", None
    output = proc.stdout.strip()
    tail = (output + f"\n[exit={proc.returncode}]")[-_PROBE_TAIL_CHARS:]
    if proc.returncode != 0:
        return tail + f"\n{proc.stderr[-400:]}", None
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return tail, None
    return tail, parsed if isinstance(parsed, dict) else None
