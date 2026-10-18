"""Tests for the c3 smoke rehearsal task + rubric (kit, manifest v2).

Operator decision 2026-10-18: rehearsal pairs run `smoke-rig-tour` (a
<10-minute rig-inspection task) instead of mr-234, to find machinery
bugs in minutes. These tests pin the REAL shipped kit files
(eval/phase2/campaign3/tasks/smoke.jsonl, rubrics/smoke.yaml):
they must load through the production loaders, reference each other,
and grade mechanically (contract block parses -> schema_ok). The
campaign task (mr-234.jsonl / diagnose.yaml) is untouched by these.
"""

from pathlib import Path

from profe.actions.grade import extract_verdict, load_rubrics, load_tasks

KIT = Path(__file__).resolve().parents[2] / "eval" / "phase2" / "campaign3"
TASKS = KIT / "tasks" / "smoke.jsonl"
RUBRICS = KIT / "rubrics"


def _task():
    return load_tasks(str(TASKS))[0]


def test_smoke_task_loads_and_references_smoke_rubric():
    t = _task()
    assert t.id == "smoke-rig-tour"
    assert t.rubric == "smoke"
    assert t.contract, "smoke must carry a contract (schema_ok is the signal)"
    assert t.artifacts == []
    assert t.precheck is None


def test_smoke_rubric_loads_and_is_mechanically_empty():
    t = _task()
    rubrics = load_rubrics(str(RUBRICS), [t])
    r = rubrics["smoke"]
    assert r.id == "smoke"
    assert r.version == 2
    assert r.mechanical_checks == []
    assert r.dimensions == {}, "rehearsal rubric: no manual depth pass"


def test_smoke_contract_grades_schema_ok():
    answer = (
        "The rig has two Juju models, nothing deployed, one k8s node, "
        "8 cores, 62 GiB RAM.\n\n```yaml\n"
        "models:\n  - controller\n  - testing\ndeployed: empty\n"
        "k8s_nodes: 1\ncpu_cores: 8\nram_gib: 62\n```\n"
    )
    v = extract_verdict(answer)
    assert v.schema_ok is True
    assert v.fields["k8s_nodes"] == 1
    assert v.fields["deployed"] == "empty"


def test_smoke_contract_missing_block_not_ok():
    v = extract_verdict("No yaml block here.")
    assert v.schema_ok is False
