#!/usr/bin/env python3
"""Migrate a legacy queries corpus file to the current schema-1 format.

The legacy corpus predates profe and carries no per-query skill
attribution; the current format requires it (expected_skills). This
script does the mechanical migration; it never guesses silently — every
should-trigger query must get its intended skill from somewhere:

- per-skill set files (a bare list of {"query", "should_trigger"}): the
  skill comes from the file name (charmed-mysql-<skill>-train.json /
  -validation.json); --skill is the fallback for names that carry none
  (smoke or ad-hoc files) — the file name always wins, so a uniform loop
  over mixed files cannot mis-attribute;
- the master corpus ({"<skill>": {"train": [...], "validation": [...]}}):
  the skill comes from the keys (--skill is not needed); the train/validation
  split is carried by the per-skill files, the flat order here is skill
  order then train then validation.

should_trigger=true maps to expected_skills=[<skill>]; false maps to [].
A query that legitimately sits between two skills still gets a
one-element list — widening it to an any-of list is a curation decision
made by editing the migrated file, not by this script.

Idempotent: a file already in the current format is validated and
skipped, so a loop over a directory is safe to re-run. One input file
per invocation.

Usage:
    uv run scripts/migrate_queries.py eval/charmed-mysql-juju-inspect-train.json
    uv run scripts/migrate_queries.py eval/smoke.json --skill charmed-mysql-juju-inspect
    uv run scripts/migrate_queries.py eval/queries.json --dry-run
    uv run scripts/migrate_queries.py in.json --out in.new.json
"""

from __future__ import annotations

import argparse
import json
import sys
import typing as t
from pathlib import Path

from pydantic import BaseModel, ConfigDict, RootModel, ValidationError

from profe.models import QueriesFile, Query

_SPLIT_SUFFIXES = ("-train", "-validation")


class LegacyQuery(BaseModel):
    """One legacy entry: a query plus its bare should-trigger flag."""

    model_config = ConfigDict(extra="forbid")

    query: str
    should_trigger: bool


class LegacySetFile(RootModel[list[LegacyQuery]]):
    """A legacy per-skill set: a bare list of legacy entries."""


class LegacyCorpusFile(
    RootModel[dict[str, dict[t.Literal["train", "validation"], list[LegacyQuery]]]],
):
    """The legacy master corpus: skill -> split -> entries."""


def skill_from_stem(stem: str) -> str | None:
    """Infer the intended skill from a set-file name.

    Only the unambiguous suffixes are trusted (`<skill>-train`,
    `<skill>-validation`); a stem without one carries no attribution and
    needs --skill (e.g. smoke or ad-hoc files).
    """
    for suffix in _SPLIT_SUFFIXES:
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return None


def build_queries(
    pairs: list[tuple[str | None, LegacyQuery]],
) -> list[Query]:
    """Map (skill, legacy entry) pairs to current-format queries."""
    return [
        Query(
            query=entry.query,
            expected_skills=[skill] if entry.should_trigger else [],
        )
        for skill, entry in pairs
    ]


def migrate_file(  # noqa: PLR0911 — the fail-fast validation ladder is the point
    path: Path,
    skill: str | None,
    out: Path | None,
    dry_run: bool,
) -> int:
    """Migrate one file; returns the process exit code.

    Each validation step returns as soon as it fails — shape detection,
    skill attribution, entry validation — so the ladder reads top to
    bottom as the list of things that can be wrong with one file.
    """
    try:
        root = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return _fail(f"{path}: not valid JSON ({exc.msg} at line {exc.lineno})")
    if isinstance(root, dict) and "schema" in root:
        try:
            QueriesFile.model_validate(root)
        except ValidationError as exc:
            return _fail(f"{path}: schema-declared but invalid ({errors(exc)})")
        print(f"{path}: already in the current format, skipped")
        return 0

    if isinstance(root, list):
        try:
            legacy = LegacySetFile.model_validate(root)
        except ValidationError as exc:
            return _fail(f"{path}: not a legacy set file ({errors(exc)})")
        # The file name wins over --skill: in a loop that passes --skill for
        # the files that need it, an override would silently re-attribute
        # every set file that already carries its skill in its name.
        chosen = skill_from_stem(path.stem) or skill
        if chosen is None and any(q.should_trigger for q in legacy.root):
            return _fail(
                f"{path}: the file name carries no skill and it has "
                "should-trigger queries — pass --skill",
            )
        pairs = [(chosen, entry) for entry in legacy.root]
    elif isinstance(root, dict):
        try:
            corpus = LegacyCorpusFile.model_validate(root)
        except ValidationError as exc:
            return _fail(f"{path}: not the legacy master corpus ({errors(exc)})")
        # Attribution comes from the corpus keys; --skill is not needed
        # (and not an error, so a uniform loop over mixed files works).
        pairs = [
            (name, entry)
            for name, splits in corpus.root.items()
            for part in ("train", "validation")
            for entry in splits[part]
        ]
    else:
        return _fail(
            f"{path}: unrecognized shape (expected a legacy set list or "
            "the master corpus object)",
        )

    queries = build_queries(pairs)
    try:
        # Construct through the wire-format alias: the schema field is
        # aliased to `schema`, so a field-name kwarg would be ignored as
        # extra input and the required declaration would be missing.
        migrated = QueriesFile.model_validate({"schema": 1, "queries": queries})
    except ValidationError as exc:
        return _fail(f"{path}: migrated file would be invalid ({errors(exc)})")

    target = out or path
    n_true = sum(1 for q in queries if q.expected_skills)
    skills = sorted({q.expected_skills[0] for q in queries if q.expected_skills})
    summary = (
        f"{path}: {len(queries)} queries "
        f"({n_true} should-trigger -> {skills or '[]'}, "
        f"{len(queries) - n_true} should-not) -> {target}"
    )
    if dry_run:
        print(f"{summary} [dry run, not written]")
        return 0
    target.write_text(migrated.model_dump_json(by_alias=True, indent=2) + "\n")
    print(summary)
    return 0


def errors(exc: ValidationError) -> str:
    """Render a pydantic error compactly, one line per error."""
    return "; ".join(
        f"{'.'.join(str(loc) for loc in err['loc']) or '<file>'}: {err['msg']}"
        for err in exc.errors()
    )


def _fail(message: str) -> int:
    """Report an error and return the failure exit code."""
    print(f"error: {message}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and migrate one file (loop the script per file)."""
    parser = argparse.ArgumentParser(
        prog="migrate_queries",
        description="Migrate a legacy queries file to the current "
        "schema-1 format (idempotent; one file per run).",
    )
    parser.add_argument("file", type=Path, help="the queries file to migrate")
    parser.add_argument(
        "--skill",
        default=None,
        metavar="NAME",
        help="intended skill for should-trigger queries when the file "
        "name carries none (e.g. smoke or ad-hoc files); the file "
        "name always wins over this",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        metavar="FILE",
        help="write the migrated file here instead of in place",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be written, write nothing",
    )
    args = parser.parse_args(argv)
    return migrate_file(args.file, args.skill, args.out, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
