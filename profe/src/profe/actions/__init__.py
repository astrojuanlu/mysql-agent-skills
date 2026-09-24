"""Profe's actions: the verbs behind the CLI, as importable functions.

Public API (the CLI imports these; everything else is private to the
package):

- `probe_trigger_runs(options, progress=None) -> ProbeRunStats`
- `summarize_trigger_runs(options) -> SummarizeReport`
- `validate_skills(options) -> ValidateReport`
"""

from .probe import probe_trigger_runs
from .summarize import summarize_trigger_runs
from .validate import validate_skills

__all__ = ["probe_trigger_runs", "summarize_trigger_runs", "validate_skills"]
