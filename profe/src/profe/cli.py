"""The `profe` command-line interface.

Plain argparse over the action functions — no business logic lives
here: parsing, defaults from the README, printing and exit codes only.
One lifecycle: `validate` → `compile` → `run` → `grade` → `report`.
"""

from __future__ import annotations

import argparse
import sys
import typing as t
from collections.abc import Callable
from pathlib import Path

from .actions import (
    build_grade_report,
    compare_grade_runs,
    compile_trigger_cells,
    grade_tasks,
    run_capture_bundle,
    validate_skills,
)
from .actions.compare import render_compare_table
from .actions.models import (
    CompileOptions,
    GradeOptions,
    RunOptions,
    ValidateOptions,
)
from .actions.report import render_report_markdown


def _heartbeat(message: str) -> None:
    """Print one progress line, unbuffered (long runs, piped logs)."""
    print(message, flush=True)


def build_parser() -> argparse.ArgumentParser:  # noqa: PLR0915 — many
    # similar subparsers; verbatim clarity over abstraction
    """Build the argument parser: one subparser per verb."""
    parser = argparse.ArgumentParser(
        prog="profe",
        description="Evaluate skills following a structured methodology "
        "(validate → compile → run → grade → report).",
    )
    sub = parser.add_subparsers(dest="verb", required=True)

    validate = sub.add_parser(
        "validate", help="structure check (thin skills-ref wrapper)"
    )
    validate.add_argument(
        "--skills",
        nargs="+",
        required=True,
        metavar="PATH",
        help="skill directories or SKILL.md files, one per skills-ref call",
    )
    validate.set_defaults(func=cmd_validate)

    compile_ = sub.add_parser(
        "compile",
        help="turn a queries file into trigger-cell tasks + rubrics",
        description="Offline: one task per query (id is a content hash of "
        "the query text), one rubric per distinct expectation set "
        "(triggered_any_of / triggered_none). The tasks file feeds "
        "`profe run`, the rubrics dir feeds `profe grade`.",
    )
    compile_.add_argument(
        "--skills",
        nargs="+",
        required=True,
        metavar="PATH",
        help="skill dirs or SKILL.md files to pin into every run; loading "
        "everything is a shell glob away: --skills 'skills/*/'",
    )
    compile_.add_argument(
        "--queries",
        required=True,
        metavar="FILE",
        help="queries file (schema-declared; expectations are any-of "
        "lists, [] = should-not)",
    )
    compile_.add_argument(
        "--tasks",
        default="eval/tasks-trigger.jsonl",
        metavar="FILE",
        help="tasks JSONL to write, overwritten (default: eval/tasks-trigger.jsonl)",
    )
    compile_.add_argument(
        "--rubrics-dir",
        default="eval/rubrics",
        metavar="DIR",
        help="directory to write the compiled <rubric-id>.yaml files into "
        "(default: eval/rubrics)",
    )
    compile_.set_defaults(func=cmd_compile)

    grade = sub.add_parser(
        "grade",
        help="score a capture bundle mechanically (offline grader)",
        description="Score one capture bundle (as emitted by `profe run`) "
        "against its task's rubric — no pi launch, no subprocess, no "
        "network. The bundle holds pi's own session file (session.jsonl, "
        "verbatim), the sidecar (capture.json: task/run identity, wall "
        "time, pi exit code and version, the forwarded skill paths, any "
        "stop reason) and the exact prompt (prompt.txt), plus the "
        "recorded precheck output when the task declares a precheck.",
    )
    grade.add_argument(
        "capture",
        metavar="BUNDLE",
        help="capture bundle directory to grade",
    )
    grade.add_argument(
        "--tasks",
        default="eval/tasks.jsonl",
        metavar="FILE",
        help="tasks JSONL (default: eval/tasks.jsonl)",
    )
    grade.add_argument(
        "--rubrics",
        default="eval/rubrics",
        metavar="DIR",
        help="rubrics directory, one <id>.yaml per task (default: eval/rubrics)",
    )
    grade.add_argument(
        "--artifacts-dir",
        default="eval/tasks-artifacts",
        metavar="DIR",
        help="artifact files referenced by tasks (default: eval/tasks-artifacts)",
    )
    grade.add_argument(
        "--skills",
        nargs="+",
        default=None,
        metavar="PATH|none",
        help="fallback skill paths for hand-assembled bundles without "
        "sidecar skills (metadata only — trigger detection and "
        "loaded_skills come from the bundle's sidecar when it carries "
        "them); the literal 'none' records the skills-off condition",
    )
    grade.add_argument(
        "--out",
        default=None,
        metavar="FILE",
        help="runs JSONL to append to, resumable "
        "(default: eval/runs/<tasks stem>.jsonl)",
    )
    grade.set_defaults(func=cmd_grade)

    run = sub.add_parser(
        "run",
        help="one attempt inside the caller's boundary; emit a capture "
        "bundle (SANDBOX-EXPLORATION §12)",
    )
    run.add_argument(
        "--tasks",
        default="eval/tasks.jsonl",
        metavar="FILE",
        help="tasks JSONL (default: eval/tasks.jsonl)",
    )
    run.add_argument(
        "--task-id",
        required=True,
        help="the one task to attempt (a boundary hosts one cell)",
    )
    run.add_argument(
        "--run",
        type=int,
        default=0,
        help="run index recorded in the bundle (default: 0)",
    )
    run.add_argument(
        "--out",
        required=True,
        metavar="DIR",
        help="capture bundle directory to write (prompt.txt, "
        "session.jsonl, capture.json, precheck files when prechecked)",
    )
    run.add_argument(
        "--skills",
        nargs="+",
        default=None,
        metavar="PATH",
        help="skill dirs to pin into the attempt (validated; forwarded "
        "verbatim to pi and recorded in the sidecar)",
    )
    run.add_argument("--model", default=None, help="override pi's default model")
    run.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="single global hang-guard in seconds (default: 600)",
    )
    run.add_argument(
        "--stop-on",
        nargs="+",
        choices=["skill-read"],
        default=[],
        metavar="CONDITION",
        help="yield on the first SKILL.md read of any loaded skill "
        "(the trigger-cell stop condition; a misroute stops too)",
    )
    run.add_argument(
        "--max-tool-calls",
        type=int,
        default=None,
        metavar="N",
        help="tool-call cap, decoupled from the skill-read yield "
        "(the should-not bound and the task-cell runaway guard)",
    )
    run.add_argument(
        "--artifacts-dir",
        default="eval/tasks-artifacts",
        metavar="DIR",
        help="artifact files referenced by tasks (default: eval/tasks-artifacts)",
    )
    run.add_argument(
        "--pi-config",
        default="~/.pi/agent",
        metavar="DIR",
        help="pi config dir copied into the attempt's scratch HOME "
        "(default: ~/.pi/agent); the `packages` settings key is "
        "stripped (the auto-install call breaks restricted boundaries)",
    )
    run.add_argument(
        "--pi",
        default=None,
        metavar="PATH",
        help="pi binary to launch, pinned explicitly (entirely optional; "
        "default: resolve `pi` on PATH, first entry wins); validated "
        "and recorded in the sidecar either way",
    )
    run.add_argument(
        "--extra-metadata",
        nargs="+",
        default=None,
        metavar="KEY=VALUE",
        help="free-form provenance labels recorded in the bundle's sidecar "
        "and copied into the grade record, e.g. --extra-metadata "
        "env=lxd-vm:ubuntu-24.04 provision=<hash>",
    )
    run.set_defaults(func=cmd_run)

    report = sub.add_parser(
        "report", help="render graded runs into the durable deliverable"
    )
    report.add_argument(
        "runs_files",
        nargs="+",
        metavar="RUNS_FILE",
        help="one graded runs file (single-cell report), or exactly two "
        "(paired comparison: A vs B per task/run, with per-check flips)",
    )
    report.add_argument(
        "--out",
        default=None,
        metavar="FILE",
        help="also write the report here (markdown for one file, JSON for "
        "a two-file comparison)",
    )
    report.add_argument(
        "--format",
        choices=["md", "json"],
        default="md",
        help="one file — md: the shareable deliverable, json: the "
        "structured report; two files — md: the comparison table, json: "
        "the full comparison report",
    )
    report.set_defaults(func=cmd_report)
    return parser


def cmd_validate(args: argparse.Namespace) -> int:
    """Run `skills-ref validate` once per skill; fail if any skill fails."""
    report = validate_skills(ValidateOptions(skills=args.skills))
    for skill in report.skills:
        print(skill.output)
    n_ok = sum(1 for skill in report.skills if skill.ok)
    print(f"validate: {n_ok}/{len(report.skills)} valid")
    return 0 if report.ok else 1


def cmd_compile(args: argparse.Namespace) -> int:
    """Compile the queries file into trigger-cell tasks + rubrics."""
    options = CompileOptions(
        skills=args.skills,
        queries=args.queries,
        tasks=args.tasks,
        rubrics_dir=args.rubrics_dir,
    )
    stats = compile_trigger_cells(options, progress=_heartbeat)
    print(
        f"compile: {stats.n_queries} queries -> {stats.n_tasks} tasks, "
        f"{stats.n_rubrics} rubrics -> {stats.tasks_file}",
    )
    return 0


def cmd_grade(args: argparse.Namespace) -> int:
    """Run the grade action with heartbeat lines on stdout."""
    out = args.out or str(Path("eval/runs") / f"{Path(args.tasks).stem}.jsonl")
    skills = [] if args.skills is None or args.skills == ["none"] else args.skills
    options = GradeOptions(
        capture=args.capture,
        tasks=args.tasks,
        rubrics=args.rubrics,
        out=out,
        skills=skills,
        artifacts_dir=args.artifacts_dir,
    )
    stats = grade_tasks(options, progress=_heartbeat)
    print(
        f"grade: {stats.n_tasks} tasks, {stats.n_runs} runs, "
        f"{stats.n_attempts} attempts ({stats.errored_attempts} errored) "
        f"in {stats.dur_s}s -> {stats.out_file}",
    )
    return 0


def _parse_extra_metadata(items: list[str] | None) -> dict[str, str] | None:
    """Parse `--extra-metadata KEY=VALUE` items; duplicates fail loudly."""
    if not items:
        return None
    metadata: dict[str, str] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key or not value:
            msg = f"--extra-metadata: expected KEY=VALUE, got {item!r}"
            raise ValueError(msg)
        if key in metadata:
            msg = f"--extra-metadata: duplicate key {key!r}"
            raise ValueError(msg)
        metadata[key] = value
    return metadata


def cmd_run(args: argparse.Namespace) -> int:
    """Run one attempt inside the boundary; emit the capture bundle."""
    options = RunOptions(
        tasks=args.tasks,
        task_id=args.task_id,
        run=args.run,
        out=args.out,
        skills=args.skills or [],
        model=args.model,
        timeout=args.timeout,
        stop_on_skill_read="skill-read" in args.stop_on,
        max_tool_calls=args.max_tool_calls,
        artifacts_dir=args.artifacts_dir,
        pi_config=args.pi_config,
        pi=args.pi,
        extra_metadata=_parse_extra_metadata(args.extra_metadata),
    )
    bundle = run_capture_bundle(options, progress=_heartbeat)
    print(f"bundle: {bundle}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Run the report action; one file = deliverable, two = comparison."""
    if len(args.runs_files) == 1:
        report = build_grade_report(args.runs_files[0])
        markdown = render_report_markdown(report)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(markdown + "\n")
        if args.format == "md":
            print(markdown)
        else:
            print(report.model_dump_json(by_alias=True, indent=2))
        return 0
    if len(args.runs_files) != 2:
        print(
            "error: report takes one runs file, or exactly two to compare",
            file=sys.stderr,
        )
        return 2
    comparison = compare_grade_runs(args.runs_files[0], args.runs_files[1])
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            comparison.model_dump_json(by_alias=True, indent=2) + "\n"
        )
    if args.format == "md":
        print(render_compare_table(comparison))
    else:
        print(comparison.model_dump_json(by_alias=True, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, dispatch to a verb, and return the exit code.

    Returns, never exits — callers must `sys.exit` it (the console script
    and `python -m profe` do). Exit codes: 0 success · 1 validate found
    invalid skills · 2 usage or semantic error (bad skill path, invalid
    queries file, unmet expectations, empty runs file) ·
    130 Ctrl-C (the runs file stays resumable).
    """
    args = build_parser().parse_args(argv)
    # argparse attaches .func via set_defaults — untypeable at the parser,
    # so the cast documents the wire contract every subcommand obeys.
    func = t.cast(Callable[[argparse.Namespace], int], args.func)
    try:
        return func(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(
            "interrupted — the runs file is resumable; re-run to continue",
            file=sys.stderr,
        )
        return 130


if __name__ == "__main__":
    sys.exit(main())
