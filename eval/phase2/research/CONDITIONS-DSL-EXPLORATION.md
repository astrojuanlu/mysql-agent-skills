# Condition expressions for rubrics — special-casing vs a DSL

Date: 2026-10-06. Question (SPEC 2026-10-06 design notes, exploration 2):
absence, any-of and composite conditions beyond flat check lists —
JSON-schema-inspired structure vs deliberate special-casing of trigger
rubrics. When does the first rubric outgrow the flat list? Status:
**explored; adopt nothing now** — the flat list carries every observed
need today; the recommendation is a sectioned design (Design 3)
*triggered by concrete tripwires* (§8), not by aesthetics. Companion to
`PI-RPC-EXPLORATION.md` (that one changes the transport; this one
changes only what a rubric may say).

Evidence base: the five hand-written rubrics (`eval/rubrics/`: diagnose,
ji-live, la-327, la-202-205, la-router-125), the compiled trigger
rubrics (`trig-*`, machine-written), the grader's check evaluator
(`_check` in `actions/grade.py`, 9 check types, all verified this
session), and the pipeline's three separate readers of a rubric (§2).
No new experiment was needed — this exploration is design analysis over
an instrument whose mechanics were just re-verified end to end.

## 1. What the conditions must express

The current inventory is **9 check types in 2 families**, all ANDed by
`apply_rubric`; `rubric_score` is the unweighted mean over the check
booleans:

- **document family** (the verdict block, the artifacts, the precheck):
  `field_present`, `field_equals`, `field_regex` (patterns ANDed),
  `field_not_regex` (patterns ORed, negated), `evidence_in_artifacts`
  (min_quotes + tolerant-but-strict grounding), `precheck_field_equals`;
- **trigger family** (the replayed tool-call stream):
  `skill_triggered` (value `true` = must have fired, `false` = per-skill
  forbid), `triggered_any_of` (any member of a set), `triggered_none`
  (nothing at all fired — a set-level absence over the *loaded* skills).

Requirements, separated by whether they are observed or latent:

**Observed (all expressible today):** per-field equality/regex/presence
on the verdict; grounded quoting from artifacts; precheck comparison;
trigger presence, any-of and absence. The compiler maps an expectation
set onto exactly one rubric shape (`triggered_any_of` or
`triggered_none`), and the hand rubrics use the document family.

**Latent pain 1 — verdict-conditional requirements (real, live).**
`la-router-125`'s contract admits `verdict: need-more-info`, but its
rubric hard-requires `verdict = not-a-regression` plus grounded
evidence. An answer that honestly says need-more-info (with nothing to
quote) scores low *by construction* — the flat list cannot say
"require evidence only when the verdict is a substantive one". The same
shape is latent in la-327/la-202-205 (no cannot-reproduce escape hatch
declared yet, so no conflict yet). This is the one composite need with
a live example.

**Latent pain 2 — richer routing grammar.** The trigger-control thread
(t5) wants to judge *which* skill fired first, not just whether an
any-of member fired: today `triggered_any_of [log-autopsy,
nightly-triage]` passes the misroute the thread is chasing (by design —
the report surfaces it via cross-trigger rows instead). A `first:` or
exclusivity condition is the rubric-level fix candidate.

**Not requirements (no evidence):** weights/partial credit, ordering
beyond first-fire, arithmetic over fields, cross-record conditions.
Designs are judged on the observed + latent set, not the hypothetical
one.

## 2. The constraint that shapes everything: a rubric is read three times

A rubric is not only an evaluator input. The pipeline reads it at three
points, and any condition design must keep all three cheap:

1. **The evaluator** (`apply_rubric`): mechanical, deterministic,
   LLM-free — and auditable. The threat model leans on a small grader
   whose behavior a reviewer can hold in their head.
2. **The claim stamp** (`rubric_expected_skills`): every grade record
   carries `expected_skills` derived *from the rubric* — `[]` for a
   should-not class, the any-of set otherwise. This stamp is what makes
   the report's routing shapes runs-file-only (the 2026-10-06 design's
   central move). The derivation must stay trivial and unambiguous.
3. **The report**: failures are named by check id; a reader of
   `eval/RESULTS.md` or a report table diagnoses from `scores` without
   re-opening the rubric. A condition design that produces anonymous
   boolean mush destroys the description-iteration loop.

Plus the **two authors**: the compiler writes rubrics (canonical,
machine-shaped — needs zero ergonomics) and humans write rubrics (five
files, prose descriptions — needs legibility). A design that serves
only one author is half a design. And the honest observation that
tempers the whole DSL pull: **as compilation spreads, the human-author
constituency shrinks** — machine-written rubrics want canonical forms,
not expressiveness.

## 3. Design 1 — flat AND-list, skills special-cased (the zoo)

Keep everything as is. New semantics arrive as new *check types*;
skills stay a deliberately special-cased family; no boolean operators
are ever added.

- Latent pain 1: unfixable in principle. A `when`-style conditional
  would be a new type carrying sub-conditions — i.e. a DSL leaking in
  through the back door; the honest zoo answer is "two rubric files or
  manual review for conditional contracts".
- Latent pain 2: fixable verbosely — first-fire is a new check type
  (`first_skill: <name>` reading `first_trigger_skill`); exclusivity
  ("exactly A fired") is expressible *today* as `skill_triggered
  {field: A, value: true}` plus per-skill `value: false` forbids on
  every other loaded skill — clunky (must enumerate the loaded set)
  but unambiguous, and the ambiguity-free `expected_skills` stamp
  already handles it ([A], forbids grade-time only).
- Pros: zero evaluator growth beyond the type added; the three-readers
  constraint is maximally served (trivial stamp, check-id report,
  auditability unchanged); no migration; the compiler's output shape is
  already exactly this.
- Cons: the type zoo grows linearly with needs and *combinatorially*
  with their interactions (each new type × each family = docs, tests,
  report footnotes); conditional contracts structurally impossible;
  "exactly" via enumeration is fragile against skill-set churn.

**Verdict: correct as the default, insufficient against pain 1.** If
pain 1 stays latent (no conditional contract ever gets graded), this
design wins on auditability alone.

## 4. Design 2 — generic boolean tree (allOf / anyOf / not)

The JSON-schema-inspired extreme: a rubric's `checks` becomes a
condition tree over atomic leaves (the existing check types minus the
trigger composites).

```yaml
id: la-router-125-v2
version: 2
condition:
  allOf:
    - anyOf:
        - {type: field_equals, field: verdict, value: not-a-regression}
        - allOf:
            - {type: field_equals, field: verdict, value: need-more-info}
            - {type: field_present, field: missing_evidence}
    - anyOf:
        - {type: field_equals, field: verdict, value: need-more-info}
        - {type: evidence_in_artifacts, field: evidence_quotes, min_quotes: 1}
```

- Expressiveness: full — conditional contracts (above), exclusivity
  (`allOf [skillA, {not: anyOf [skillB]}]`), mixed claims.
- **The set-quantification mirage.** `triggered_none` does not become a
  tree expression — "nothing fired" quantifies over the *loaded skill
  set*, not over a fixed list of leaves. The tree keeps a set-level
  leaf (`any_skill_triggered: true`) and absence becomes
  `{not: {any_skill_triggered: true}}`. Genericization does not
  eliminate special-casing; it relocates it into a leaf that pretends
  to be ordinary. The trigger family stays set-level under every
  design.
- **The claim stamp becomes interpretation.** "What does this rubric
  claim?" needs a tree walk with under-approximation rules (positive
  claims = skill leaves reachable without crossing a `not`; absence =
  negated any-skill). Doable, but now a semantic analysis with edge
  cases (`allOf [A, {not: B}]` stamps [A]; the B-forbid is invisible to
  routing) — exactly the derivation the 2026-10-06 design made trivial,
  made subtle again. A wrong walker silently corrupts the routing
  report.
- **Score semantics go murky.** `rubric_score` is a mean over check
  booleans; in a tree, which units? Leaves (an anyOf that passed via
  one alternative gives the other leaves no verdict), branches (no
  stable id), or root (binary — the mean's diagnostic value gone)? Any
  choice makes "failed check ids" incoherent for branches: a failed
  `allOf` names child failures, a failed `anyOf` names *all* its
  children though any one could have saved it.
- **Author ergonomics:** verbose for the compiler (trivial — it
  generates), hostile for humans (nesting in YAML; the five hand
  rubrics would all grow a scaffolding layer).
- Pros: one evaluator, every boolean need forever, conditional
  contracts now.
- Cons: stamp interpretation, score/report incoherence, audit surface
  grows from "9 types, one AND" to "9 types + a boolean algebra with
  quantifier-leaf semantics", migration re-derives semantics of all
  existing rubrics.

**Verdict: rejected as the default.** The expressiveness it adds over
Design 3 is entirely hypothetical today, and it breaks the two
pipeline invariants (trivial stamp, per-check-id report) that the
unified lifecycle just bought. Revisit only if a genuinely
cross-domain composition need appears that §5's guards cannot express.

## 5. Design 3 — sections + one-level guards (structured special-casing)

Two named sections with fixed shapes; no recursion, no generic
operators:

```yaml
id: la-router-125-v2
version: 2
routing:
  expect: [charmed-mysql-log-autopsy]   # any-of (absent = no claim)
  forbid: []                            # per-skill forbids
  # first: charmed-mysql-log-autopsy    # candidate for trigger v2
verdict:
  - id: verdict-substantive
    type: field_equals
    field: verdict
    value: not-a-regression
  - id: evidence-grounded
    type: evidence_in_artifacts
    field: evidence_quotes
    min_quotes: 1
    when:
      field: verdict
      not_value: need-more-info         # guard: applies only when verdict differs
```

- `routing:` — the special-cased skill grammar, promoted from the
  check list to a first-class section: `expect` (any-of), `forbid`
  (per-skill), optionally `first` later. The compiler writes exactly
  this; **the claim stamp reads the section, not a tree — stays
  trivial** (expect → stamp it; forbid is grade-time; absent → no
  claim).
- `verdict:` — today's flat document-family checks, plus a **one-level
  `when` guard** (equality/disequality on one verdict field). Guarded
  checks that don't apply are excluded from the score's denominator
  and shown as `n/a` in the report — the applicability rule is one
  sentence, pinned, no nesting allowed (`when` inside `when` is a
  validation error). This kills pain 1 with a bounded grammar.
- Score: mean over *applicable* checks (n/a excluded); report lists
  failed ids as today plus n/a markers; audit surface = 9 types + one
  guard construct + one section.
- Migration: mechanical — flat `checks` → `verdict:` section;
  `triggered_*` types → routing keys; the compiled trig-* rubrics
  regenerate from the compiler (which is the point of having one).
- Cons: two mini-grammars to document; guard scope limited to one
  verdict field (two-field conditionals would need… a second guard
  field — acceptable; nested conditionals do not exist and are not
  missed); slightly richer schema to validate.

**Verdict: the design to adopt, when a tripwire fires (§8).** It
resolves both latent pains with bounded machinery, keeps all three
readers cheap, and treats the compiler as the primary author.

## 6. Design 4 (variant) — kind-tagged rubrics

Design 3 hardened: the rubric declares `kind: trigger | contract` and
the schema validates per kind (trigger rubrics: routing section only,
no verdict section; contract rubrics: the reverse). The compiler's
output is literally a kind; `expected_skills` becomes a function of
kind — zero interpretation.

- Pros: strongest validation; the stamp is structural, not derived;
  hand-written trigger rubrics stay impossible to blur.
- Cons: the unified lifecycle's point was *one* rubric shape; kinds
  re-fence the trigger/task world the compiler just dissolved, and a
  hybrid rubric (routing + verdict in one — plausible for a task cell
  that also asserts its skill was used) needs a third kind. Kind
  proliferation is the zoo again, one level up.

**Verdict: rejected for now; keep as the strict-mode option** if a
mixed claim ever shows up ambiguous in practice (the report would be
the detector).

## 7. Design 5 (variant) — the verdict contract as JSON Schema

The schema-inspired reading taken literally: the contract text becomes
a JSON Schema and grading = validating the verdict document against it
(`additionalProperties: false`, enum on `verdict`, pattern on fields).
`schema_ok` fuses into validation.

- Appeal: one artifact instead of contract-prose + field checks; the
  contract and the rubric cannot drift.
- Fatal frictions: (a) the contract must stay in the **prompt** for the
  model — a schema would have to be rendered to prose (lossy) or
  shipped twice (the drift returns, now between schema and prompt);
  (b) `schema_ok` vs substance is a **deliberate** separation (README:
  a weak model may fail at the block, not the substance) — validation
  fuses them; (c) `evidence_in_artifacts` and the trigger family are
  not document validation at all — they'd live outside the schema, so
  the rubric still needs the second family; (d) tolerant quoting
  (subsequence semantics) is unexpressible in JSON Schema.

**Verdict: rejected.** It optimizes DRYness against three real
separations the pipeline depends on. The "schema-inspired" instinct is
better spent on *validation strictness* of the rubric YAML itself
(Design 3's sections), not on reusing JSON Schema as the condition
language.

## 8. Comparison and recommendation

| | D1 flat+zoo | D2 boolean tree | D3 sections+guards | D4 kinds | D5 JSON Schema |
|---|---|---|---|---|---|
| conditional contracts | no | yes | yes (one-level) | via D3 | partial |
| routing richness | new types | full | expect/forbid/first | per kind | outside |
| claim stamp | trivial | interpretation walk | section read | structural | n/a (no claim) |
| score semantics | clean | murky | clean (applicability) | clean | fused into schema_ok |
| report legibility | check ids | branch mush | check ids + n/a | check ids | validation errors |
| evaluator audit surface | smallest | largest | small | small | medium |
| migration | none | re-derives all | mechanical | mechanical | generative |
| human ergonomics | fine | poor | good | good | good |

**Recommendation.**

1. **Adopt nothing now.** Every observed need is met by the flat list;
   both latent pains have non-rubric mitigations today (pain 2 is
   surfaced by the report's cross-trigger rows; pain 1 is a rubric
   authoring judgment call, and its one live case — la-router-125's
   need-more-info — has not yet produced a disputed grade).
2. **The flat list "outgrows" when a tripwire fires.** Concrete ones:
   (a) a grading dispute turns on a verdict-conditional requirement
   (the need-more-info case produces a false negative worth fixing);
   (b) trigger rubric v2 wants first-fire or exclusivity as *graded*
   semantics rather than report-level observation; (c) a second rubric
   family needs guards and the ad-hoc types start interacting. Any one
   of these → **adopt Design 3** (sections + one-level guards), bump
   the rubric format, regenerate the compiled rubrics, migrate the
   five hand rubrics mechanically.
3. **Never adopt Design 2 as the default.** Keep it as the documented
   fallback only if a cross-domain composition need appears that
   guards cannot express within one release cycle — and if that
   happens, re-examine the claim-stamp invariant first (it is the
   thing a generic language actually breaks).
4. **The compiler is the primary author.** Whatever the condition
   design, machine-written rubrics and a trivial stamp are worth more
   than expressiveness: invest in the writer (compiler) and canonical
   forms, not in a language for a shrinking human constituency.
5. **Record schema untouched in every design** until adoption:
   `scores` stays a per-check-id map, `rubric_score` stays the mean,
   `expected_skills` keeps its semantics. The condition design is a
   rubric-format question, not a record-format one.
