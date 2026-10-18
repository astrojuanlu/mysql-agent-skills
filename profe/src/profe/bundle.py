"""Read and validate attempt bundles — the artifacts profe itself writes.

An attempt bundle is a recovered capture directory: `session.jsonl` (the
primary record), `capture.json` (the sidecar profe's runner writes next
to it), `graded.jsonl` (`profe grade` output) and `runner-status`. This
module is the ONE reader for those artifacts — schema knowledge lives
here, next to the code that produces the formats, so a format change
updates writer and reader in the same commit (the campaign-side shadow
parsers this retires were exactly the drift surface retro A2/A4
describe).

Scope boundary (deliberate): only profe's OWN artifacts are read here.
Campaign forensics (`incidents.json`), blind depth grades
(`grade-depth.json`), price vectors and any adjudication semantics
(cap/wall classification, infra-vs-agent two-class rules) stay
campaign-side — profe supplies facts and canonical numbers, never
campaign judgment.

Import weight: stdlib only (json/pathlib/dataclasses), so lightweight
consumers — recovery drivers, sheet tooling, host-side monitors — can
parse bundles without profe's third-party dependencies. Strict typed
parsing of the sidecar lives in `profe.models.CaptureSidecar` (pydantic);
this module is the dependency-free twin.

Strictness split: reading is strict about FORMAT — a directory that is
not a current-format attempt bundle (missing or unparseable required
artifacts) raises BundleFormatError. Era-compatibility tolerance for
older profe writers (five-probe-era shapes) was deliberately removed.
Tolerance remains for VALUE: `runner-status=incomplete` reads fine
(quarantine adjudication needs the value), and a truncated session tail
(a killed run's partial last line) is skipped, not fatal.

    session_usage(path)      → usage walk of one session.jsonl
    read_bundle(dir)         → BundleRecord (raises BundleFormatError on
                               non-current-format directories)
    validate_bundle(dir)     → list[str] reasons; empty means valid
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict


class BundleFormatError(ValueError):
    """Not a current-format attempt bundle.

    Raised by read_bundle when a directory lacks a required artifact
    (capture.json, session.jsonl, graded.jsonl, runner-status) or its
    sidecar/session head is unparseable. The message carries the
    reasons (same strings validate_bundle produces).
    """


class UsageComponents(TypedDict):
    """Per-component token totals (the wire contract, see session_usage)."""

    input: int
    output: int
    cacheRead: int
    totalTokens: int


class SessionUsage(TypedDict):
    """session_usage's return shape.

    The docstring there is the contract (the DUT-side live sampler
    mirrors it); typed here so the compiler enforces what the prose
    pins.
    """

    components: UsageComponents
    calls: int
    skill_reads: list[str]


def session_usage(session_path: str | Path) -> SessionUsage:
    """Sum usage events from one session.jsonl (the primary record).

    One pass: assistant messages only; toolCall count = calls;
    SKILL.md-read paths → skill names; usage blocks summed per
    component. totalTokens is carried as the provider reported it (it is
    NOT input+output+cacheRead). The returned dict shape
    ({"components": ..., "calls": ..., "skill_reads": [...]}) is a
    cross-implementation contract — the DUT-side live sampler mirrors
    these rules for §4.4 cap enforcement, so the shape must not change
    without updating that mirror.

    Returns {"components": {"input": int, "output": int,
    "cacheRead": int, "totalTokens": int}, "calls": int,
    "skill_reads": sorted[str]}.
    """
    tot: UsageComponents = {"input": 0, "output": 0, "cacheRead": 0, "totalTokens": 0}
    calls = 0
    skill_reads = set()
    with open(session_path) as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            m = r.get("message") or {}
            if m.get("role") != "assistant":
                continue
            for c in m.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "toolCall":
                    calls += 1
                    rd = c.get("arguments", {}) or {}
                    p = str(rd.get("path") or rd.get("command") or "")
                    if "SKILL.md" in p:
                        skill_reads.add(
                            p.rsplit("/skills/", maxsplit=1)[-1].split("/", maxsplit=1)[
                                0
                            ]
                        )
            u = m.get("usage")
            if u:
                # literal keys: TypedDict forbids str-keyed subscripts
                for k in ("input", "output", "cacheRead", "totalTokens"):
                    tot[k] += u.get(k) or 0
    return {"components": tot, "calls": calls, "skill_reads": sorted(skill_reads)}


@dataclass
class BundleRecord:
    """Profe facts for one attempt bundle (current format guaranteed).

    The format gate (read_bundle) guarantees capture.json, a parseable
    session head, graded.jsonl and runner-status exist — this record is
    tolerant to VALUES only: a truncated session tail skips that line,
    and `runner-status` may read `incomplete` (quarantine). `calls=None`
    means the session walk failed on a malformed line that parses as
    JSON but is not an entry object (e.g. a scalar) — no readable usage
    totals; token fields stay 0.
    """

    bundle: str
    # capture.json sidecar
    duration_seconds: float | None = None
    pi_exit_code: int | None = None
    pair: Any = None  # extra_metadata free-form labels
    arm: str | None = None
    attempt: int = 1
    machine: str = ""
    skills_staged: list[str] = field(default_factory=list)
    early_exit: Any = None  # raw stop-condition value, if any
    # session.jsonl usage (via session_usage)
    calls: int | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    total_tokens: int = 0
    skill_reads: list[str] = field(default_factory=list)
    # graded.jsonl (profe grade output) — last record wins
    verdict: str = ""
    root_cause: str = ""
    schema_ok: bool | None = None
    # runner-status marker
    runner_status: str = ""


def read_bundle(bundle):
    """All profe facts for one attempt bundle.

    Raises BundleFormatError unless the directory is a current-format
    attempt bundle: the format checks of validate_bundle, minus the
    runner-status VALUE check (any value reads). See the module
    docstring for the strictness split (format vs value).
    """
    b = Path(bundle)
    bad = [r for r in validate_bundle(b) if not r.startswith("runner-status=")]
    if bad:
        raise BundleFormatError(f"{b}: {'; '.join(bad)}")
    rec = BundleRecord(bundle=str(b))
    c = json.loads((b / "capture.json").read_text())
    rec.duration_seconds = c.get("dur_seconds")
    rec.pi_exit_code = c.get("pi_exit_code")
    md = c.get("extra_metadata") or {}
    rec.pair = md.get("pair")
    rec.arm = md.get("arm")
    rec.attempt = int(md.get("attempt") or 1)
    rec.machine = md.get("machine", "")
    rec.skills_staged = [s.get("name") for s in c.get("skills") or []]
    rec.early_exit = c.get("early_exit")
    try:
        u = session_usage(b / "session.jsonl")
        rec.calls = u["calls"]
        rec.input_tokens = u["components"]["input"]
        rec.output_tokens = u["components"]["output"]
        rec.cache_read_tokens = u["components"]["cacheRead"]
        rec.total_tokens = u["components"]["totalTokens"]
        rec.skill_reads = u["skill_reads"]
    except Exception:
        rec.calls = None  # session walk failed on a malformed entry
    try:
        rows = [
            json.loads(ln)
            for ln in (b / "graded.jsonl").read_text().splitlines()
            if ln.strip()
        ]
        if rows:
            v = rows[-1].get("verdict") or {}
            rec.verdict = v.get("verdict", "")
            rec.root_cause = v.get("root_cause", "")
            rec.schema_ok = bool(rows[-1].get("schema_ok"))
    except Exception:
        pass  # graded content is value, not format — a garbage tail → zeros
    rec.runner_status = (b / "runner-status").read_text().strip()
    return rec


def validate_bundle(bundle):
    """Check validity for skip/resume/quarantine decisions.

    This is also the format definition: read_bundle refuses exactly what
    this flags, minus the runner-status VALUE check (an `incomplete`
    bundle reads, quarantined).

    Returns the list of reasons; EMPTY means the bundle is valid.
    """
    b = Path(bundle)
    reasons = []
    if not (b / "capture.json").is_file():
        reasons.append("no capture.json (sidecar)")
    else:
        try:
            json.loads((b / "capture.json").read_text())
        except Exception:
            reasons.append("capture.json unparseable")
    if not (b / "session.jsonl").is_file():
        reasons.append("no session.jsonl (primary record)")
    else:
        try:
            with open(b / "session.jsonl") as f:
                first = f.readline()
            json.loads(first)
        except Exception:
            reasons.append("session.jsonl unparseable")
    if not (b / "graded.jsonl").is_file():
        reasons.append("no graded.jsonl (mechanical grade)")
    rs = b / "runner-status"
    if not rs.is_file():
        reasons.append("no runner-status (attempt marker)")
    elif rs.read_text().strip() != "done":
        reasons.append(f"runner-status={rs.read_text().strip()}")
    return reasons
