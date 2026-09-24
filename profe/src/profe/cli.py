"""The `profe` command-line interface.

Plain argparse over the action functions (`validate_skills`,
`probe_trigger_runs`, `summarize_trigger_runs`) — no business logic
lives here: parsing, defaults from the README, printing and exit codes
only.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from .actions import (
    probe_trigger_runs,
    summarize_trigger_runs,
    validate_skills,
)
from .actions.summarize import render_summarize_table
from .models import ProbeOptions, ProbeRunStats, SummarizeOptions, ValidateOptions


def _run_count(value: str) -> int | str:
    """Parse --num-runs: the adaptive policy sentinel or a fixed N >= 1."""
    if value == ":auto:":
        return value
    try:
        n = int(value)
    except ValueError as exc:
        msg = f"expected :auto: or an integer >= 1, got {value!r}"
        raise argparse.ArgumentTypeError(msg) from exc
    if n < 1:
        msg = f"expected an integer >= 1, got {n}"
        raise argparse.ArgumentTypeError(msg)
    return n


def _heartbeat(message: str) -> None:
    """Print one probe progress line, unbuffered (long runs, piped logs)."""
    print(message, flush=True)


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser: one subparser per verb."""
    parser = argparse.ArgumentParser(
        prog="profe",
        description="Evaluate skills following a structured methodology "
        "(validate → probe → summarize).",
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

    probe = sub.add_parser("probe", help="execute trigger queries, record attempts")
    probe.add_argument(
        "--skills",
        nargs="+",
        required=True,
        metavar="PATH",
        help="skill dirs or SKILL.md files to pin into every run; loading "
        "everything is a shell glob away: --skills 'skills/*/'",
    )
    probe.add_argument(
        "--queries",
        default="eval/queries.json",
        metavar="FILE",
        help="queries file (default: eval/queries.json)",
    )
    probe.add_argument(
        "--out",
        default=None,
        metavar="FILE",
        help="runs JSONL to append to, resumable "
        "(default: eval/runs/<queries stem>.jsonl)",
    )
    probe.add_argument("--model", default=None, help="override pi's default model")
    probe.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="single global hang-guard in seconds, not a task budget",
    )
    probe.add_argument(
        "--retries",
        type=int,
        default=1,
        help="re-attempts for errored/timed-out runs within a run",
    )
    probe.add_argument(
        "--max-tool-calls",
        type=int,
        default=12,
        help="kill a run with no skill read after this many tool calls "
        "(calibrated constant; see the eval calibration note)",
    )
    probe.add_argument(
        "--num-runs",
        type=_run_count,
        default=":auto:",
        metavar="N|:auto:",
        help="adaptive runs (default :auto:, cap 3) or a fixed N for "
        "instrument validation and agreement checks",
    )
    probe.set_defaults(func=cmd_probe)

    summarize = sub.add_parser(
        "summarize", help="aggregate a probe runs file into a report"
    )
    summarize.add_argument("runs_file", metavar="RUNS_FILE")
    summarize.add_argument(
        "--out",
        default=None,
        metavar="FILE",
        help="also write the JSON report here, whatever --format prints",
    )
    summarize.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="table: skill rows plus condition/effort footer; json: the "
        "full report with the per-query section",
    )
    summarize.set_defaults(func=cmd_summarize)
    return parser


def cmd_validate(args: argparse.Namespace) -> int:
    """Run `skills-ref validate` once per skill; fail if any skill fails."""
    report = validate_skills(ValidateOptions(skills=args.skills))
    for skill in report.skills:
        print(skill.output)
    n_ok = sum(1 for skill in report.skills if skill.ok)
    print(f"validate: {n_ok}/{len(report.skills)} valid")
    return 0 if report.ok else 1


def cmd_probe(args: argparse.Namespace) -> int:
    """Run the probe action with heartbeat lines on stdout."""
    out = args.out or str(Path("eval/runs") / f"{Path(args.queries).stem}.jsonl")
    options = ProbeOptions(
        skills=args.skills,
        queries=args.queries,
        out=out,
        model=args.model,
        timeout=args.timeout,
        retries=args.retries,
        max_tool_calls=args.max_tool_calls,
        num_runs=args.num_runs,
    )
    stats = probe_trigger_runs(options, progress=_heartbeat)
    _print_stats(stats)
    return 0


def _print_stats(stats: ProbeRunStats) -> None:
    """Print the one-line bookkeeping summary of a probe run."""
    print(
        f"probe: {stats.n_queries} queries, {stats.n_runs} runs, "
        f"{stats.n_attempts} attempts ({stats.errored_attempts} errored) "
        f"in {stats.dur_s}s -> {stats.out_file}",
    )


def cmd_summarize(args: argparse.Namespace) -> int:
    """Run the summarize action and print the requested format."""
    options = SummarizeOptions(
        runs_file=args.runs_file, out=args.out, format=args.format
    )
    report = summarize_trigger_runs(options)
    if args.format == "table":
        print(render_summarize_table(report))
    else:
        print(report.model_dump_json(by_alias=True, indent=2))
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
    func: Callable[[argparse.Namespace], int] = args.func
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
