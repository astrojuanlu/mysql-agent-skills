"""`profe validate`: structure check over the loaded skills.

Thin wrapper over `skills-ref validate`: frontmatter, name/description
limits, body budget. Validates *structure only* — it cannot assess
trigger behavior (that's `probe`'s job) or answer quality (`grade`'s).
"""

from __future__ import annotations

import shutil
import subprocess

from ..models import SkillValidation, ValidateOptions, ValidateReport


def validate_skills(options: ValidateOptions) -> ValidateReport:
    """Run `skills-ref validate` over every skill path.

    One call per skill (skills-ref takes a single SKILL_PATH); the
    combined report is ok only when every skill is valid. A missing
    skills-ref binary is a hard error, not an empty report. Paths are
    forwarded as given — skills-ref reports its own "does not exist"
    verdict, and glob expansion is the shell's business, not ours.
    """
    exe = shutil.which("skills-ref")
    if exe is None:
        msg = "skills-ref not found on PATH"
        raise ValueError(msg)
    results = []
    for skill in options.skills:
        proc = subprocess.run(
            [exe, "validate", skill],
            capture_output=True,
            text=True,
            check=False,
        )
        output = (proc.stdout + proc.stderr).strip()
        results.append(
            SkillValidation(
                skill=skill,
                ok=proc.returncode == 0,
                output=output,
            )
        )
    return ValidateReport(ok=all(r.ok for r in results), skills=results)
