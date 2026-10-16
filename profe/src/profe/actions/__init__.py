"""Profe's actions: the verbs behind the CLI, as importable functions.

Public API (the CLI imports these; everything else is private to the
package):

- `validate_skills(options) -> ValidateReport`
- `compile_trigger_cells(options, progress=None) -> CompileStats`
- `run_capture_bundle(options, progress=None) -> Path`
- `grade_tasks(options, progress=None) -> GradeRunStats`
- `build_grade_report(runs_file) -> GradeReport`
- `compare_grade_runs(a_file, b_file) -> CompareReport`
"""

from .compare import compare_grade_runs
from .compile import compile_trigger_cells
from .grade import grade_tasks
from .report import build_grade_report
from .run import run_capture_bundle
from .validate import validate_skills

__all__ = [
    "build_grade_report",
    "compare_grade_runs",
    "compile_trigger_cells",
    "grade_tasks",
    "run_capture_bundle",
    "validate_skills",
]
