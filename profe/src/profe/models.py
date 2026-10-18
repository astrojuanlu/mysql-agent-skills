"""Deprecated re-export shim: the models live in profe.actions.models.

One-release shim (consolidation step 0, 2026-10-17); import from
`profe.actions.models` directly. Deleted once nothing imports this.
"""

from profe.actions.models import *  # noqa: F401,F403
from profe.actions.models import (  # noqa: F401 (explicit names for IDEs)
    AttemptCore,
    CaptureSidecar,
    EarlyExit,
    RoutingReport,
    Rubric,
    RubricCheck,
    RunOptions,
    RunRouting,
    RunVerdict,
    SkillRate,
    SkillRef,
    SkillValidation,
    Task,
    TaskRouting,
    ValidateOptions,
    ValidateReport,
)
