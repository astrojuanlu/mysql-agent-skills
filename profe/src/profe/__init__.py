"""Profe: evaluate skills following a structured methodology."""

from __future__ import annotations

import sys

from .cli import main as _cli_main

__all__ = ["main"]


def main() -> None:
    """Run the CLI and exit with its status code (console-script entry)."""
    sys.exit(_cli_main())
