"""Paired statistics for cost campaigns (the B10 math, standalone member).

Workspace member on the pi-agent-json pattern: own pyproject, own deps
(numpy+scipy), imports nothing from profe. Reached by the campaign
analysis via the `analysis` dependency group; profe's own runtime never
imports it (only its tests, which travel with the package).
"""

from pairedstats.stats import (
    betainc,
    cp_upper,
    jeffreys_upper,
    mcnemar_exact,
    mn_ci_diff,
    norm_ppf_975,
    paired_t,
    selftest,
    signflip_exact,
    t_quantile_975,
    t_two_sided_p,
    wilcoxon_pratt,
)

__all__ = [
    "betainc",
    "cp_upper",
    "jeffreys_upper",
    "mcnemar_exact",
    "mn_ci_diff",
    "norm_ppf_975",
    "paired_t",
    "selftest",
    "signflip_exact",
    "t_quantile_975",
    "t_two_sided_p",
    "wilcoxon_pratt",
]
