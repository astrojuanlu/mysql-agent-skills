"""Tests for the rubric schema (profe.actions.models.Rubric).

All fixtures are inline YAML mirroring the defining shapes: mechanical
checks under `mechanical_checks`, and the blind-depth-pass metadata
(task, blind_protocol, dimensions, grade_depth_json_schema, notes)
carried and hashed for the host-side manual grader. The real shipped
rubric (eval/phase2/campaign3/rubrics/diagnose.yaml, diagnose@2) was
validated against this schema at the 2026-10-17 rehearsal fix; these
tests pin the shapes so a rubric-field drift fails loudly here instead
of crashing the DUT's grade step. There is no legacy shape: a rubric
carrying anything else fails validation loudly.
"""

import pytest
import yaml
from pydantic import ValidationError

from profe.actions.models import Rubric

RUBRIC = """
id: diagnose
version: 2
description: >
  T4 mode + mechanism-depth instrument; mechanical contract unchanged.
task: >
  mr-234 (mysql-router-k8s rev 916 — log rotation makes promtail CPU
  grow with pod age).
blind_protocol:
  input: >
    A SCRUBBED transcript copy: skills-tree references redacted.
  known_leak: >
    Blinding is imperfect by construction; record suspected inference
    in grader_notes.
  procedure:
    - Read the final contract block first.
    - Assign mechanism_depth using the anchored scale below.
dimensions:
  verdict_confirmation:
    type: enum
    values: [confirmed, overstated, wrong, unresolved]
    description: Does the evidence support the verdict?
  mechanism_depth:
    type: ordinal
    scale: [0, 1, 2, 3, 4]
    description: How far down the causal chain the run got.
    anchors:
      0: Verdict only, or mechanism wrong/guessed.
      4: Implementation-level causal chain, consumer internals, quantified.
  evidence_strength:
    type: ordinal
    scale: [0, 1, 2, 3]
    anchors:
      0: asserted
      3: counterfactual
grade_depth_json_schema:
  path: <bundle>/grade-depth.json
  fields:
    run_id: opaque id from the scrubbed copy (string)
    mechanism_depth: 0-4 (integer)
mechanical_checks: []
notes: >
  Task-specific anchors by design.
"""


def _parse(text):
    return Rubric.model_validate(yaml.safe_load(text))


def test_rubric_defining_shapes():
    r = _parse(RUBRIC)
    assert r.version == 2
    assert r.mechanical_checks == []
    # the blind-depth-pass metadata is carried, fully typed; the model
    # fields are optional (None when absent from the yaml) — narrow first
    assert r.task is not None
    assert r.task.startswith("mr-234")
    assert r.blind_protocol is not None
    assert r.blind_protocol.input.startswith("A SCRUBBED")
    assert len(r.blind_protocol.procedure) == 2
    vc = r.dimensions["verdict_confirmation"]
    assert vc.type == "enum" and vc.values[0] == "confirmed" and vc.scale == []
    md = r.dimensions["mechanism_depth"]
    assert md.scale == [0, 1, 2, 3, 4]
    assert md.anchors == {
        0: "Verdict only, or mechanism wrong/guessed.",
        4: "Implementation-level causal chain, consumer internals, quantified.",
    }  # YAML int keys stay ints
    ds = r.grade_depth_json_schema
    assert ds is not None
    assert ds.path == "<bundle>/grade-depth.json"
    assert ds.fields["mechanism_depth"] == "0-4 (integer)"
    assert r.notes is not None
    assert r.notes.startswith("Task-specific")


def test_mechanical_checks_execute_from_their_field():
    with_checks = RUBRIC.replace(
        "mechanical_checks: []",
        "mechanical_checks:\n"
        "  - id: verdict-present\n"
        "    type: field_present\n"
        "    field: verdict",
    )
    r = _parse(with_checks)
    assert [c.id for c in r.mechanical_checks] == ["verdict-present"]


def test_checks_field_is_not_a_rubric_field():
    # mechanical checks live ONLY under mechanical_checks; the old
    # `checks:` spelling is an unmodeled field and fails loudly
    text = RUBRIC.replace(
        "mechanical_checks: []",
        "mechanical_checks: []\nchecks:\n  - id: x\n    type: field_present",
    )
    with pytest.raises(ValidationError, match="checks"):
        _parse(text)


def test_unmodeled_rubric_field_fails_loudly():
    with pytest.raises(ValidationError, match="new_instrument"):
        _parse(RUBRIC + "\nnew_instrument: oops\n")


def test_unmodeled_dimension_field_fails_loudly():
    text = RUBRIC.replace(
        "  mechanism_depth:\n    type: ordinal",
        "  mechanism_depth:\n    type: ordinal\n    mood: generous",
    )
    with pytest.raises(ValidationError, match="mood"):
        _parse(text)
