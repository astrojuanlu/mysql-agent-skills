"""Tests for profe.bundle — anchored to the bundle format's DEFINING
semantics, with all expected values computed by hand in the fixtures
(synthetic bundles built inline; no external files). The acceptance
regression against real recorded bundles (identical records and rows
from the campaign's former reader) was run at migration time; these
tests pin the semantics so the format contract can't drift silently.

Reading is strict about FORMAT (BundleFormatError on non-current-format
directories — era-compatibility for five-probe-era shapes was removed)
and tolerant about VALUE (runner-status=incomplete reads, quarantined).
"""

import json

import pytest

from profe.bundle import BundleFormatError, read_bundle, session_usage, validate_bundle


def _write_session(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _assistant(usage=None, tool_calls=()):
    content = [{"type": "toolCall", "arguments": a} for a in tool_calls]
    m = {"role": "assistant", "content": content}
    if usage is not None:
        m["usage"] = usage
    return {"message": m}


# ---------------------------------------------------------------- usage walk


def test_session_usage_defining(tmp_path):
    s = tmp_path / "session.jsonl"
    # a line that is not JSON at all is skipped (a line that parses to a
    # JSON *scalar* would crash the walk — same as the implementation it
    # mirrors; the tolerance contract covers malformed lines only)
    s.write_text("{not json}\n")
    with s.open("a") as f:
        for row in [
            {"message": {"role": "user", "content": "hi"}},  # skipped: not assistant
            _assistant(
                usage={"input": 10, "output": 2, "cacheRead": 100, "totalTokens": 112},
                tool_calls=[
                    {"path": "/x/skills/juju-cli/SKILL.md"},
                    {"command": "cat /p/skills/foo-bar/SKILL.md"},
                ],
            ),
            {"message": {"role": "assistant", "content": ["plain string"]}},  # no usage
            _assistant(
                usage={"input": 1, "output": 1, "cacheRead": 1, "totalTokens": 999},
                tool_calls=[{"path": "/x/skills/juju-cli/SKILL.md", "extra": 1}],
            ),
        ]:
            f.write(json.dumps(row) + "\n")
    u = session_usage(s)
    assert u["components"] == {
        "input": 11,
        "output": 3,
        "cacheRead": 101,
        "totalTokens": 1111,
    }  # 112 + 999, as reported
    assert u["calls"] == 3
    assert u["skill_reads"] == ["foo-bar", "juju-cli"]  # deduplicated, sorted


def test_session_usage_empty_and_degenerate(tmp_path):
    s = tmp_path / "session.jsonl"
    s.write_text("")
    u = session_usage(s)
    assert u["components"] == {
        "input": 0,
        "output": 0,
        "cacheRead": 0,
        "totalTokens": 0,
    }
    assert u["calls"] == 0 and u["skill_reads"] == []
    # toolCall without path/command, and SKILL.md absent → no skill read
    s.write_text(json.dumps(_assistant(tool_calls=[{}, {"path": "/etc/rc"}])) + "\n")
    u = session_usage(s)
    assert u["calls"] == 2 and u["skill_reads"] == []


# ---------------------------------------------------------------- read_bundle


def _make_bundle(  # noqa: PLR0913, PLR0917 (fixture knobs, kwargs-only use)
    tmp_path,
    capture=None,
    session_rows=None,
    graded=None,
    runner_status=None,
    raw_capture=None,
):
    b = tmp_path / "bundle"
    b.mkdir(parents=True)
    if capture is not None or raw_capture is not None:
        (b / "capture.json").write_text(
            raw_capture if raw_capture is not None else json.dumps(capture)
        )
    if session_rows is not None:
        _write_session(b / "session.jsonl", session_rows)
    if graded is not None:
        (b / "graded.jsonl").write_text("".join(json.dumps(r) + "\n" for r in graded))
    if runner_status is not None:
        (b / "runner-status").write_text(runner_status)
    return b


CAPTURE = {
    "task_id": "mr-234",
    "run": 901,
    "dur_seconds": 3526.0,
    "pi_exit_code": 0,
    "skills": [
        {"name": "juju-cli", "path": "/s/juju-cli"},
        {"name": "mysql-charms-source", "path": "/s/mcs"},
    ],
    "early_exit": None,
    "extra_metadata": {"pair": "901", "arm": "on", "attempt": "2", "machine": "rig-a"},
}
SESSION_ROWS = [
    _assistant(
        usage={
            "input": 398085,
            "output": 34525,
            "cacheRead": 3079552,
            "totalTokens": 3512162,
        },
        tool_calls=[{"path": "/x/skills/juju-cli/SKILL.md"}],
    ),
]
GRADED = [
    {"schema_ok": True, "verdict": {"verdict": "old", "root_cause": "x"}},
    {"schema_ok": True, "verdict": {"verdict": "charm-bug", "root_cause": "rev916"}},
]


def test_read_bundle_full(tmp_path):
    b = _make_bundle(
        tmp_path,
        capture=CAPTURE,
        session_rows=SESSION_ROWS,
        graded=GRADED,
        runner_status="done\n",
    )
    r = read_bundle(b)
    assert r.duration_seconds == 3526.0
    assert r.pi_exit_code == 0
    assert r.pair == "901" and r.arm == "on"
    assert r.attempt == 2  # extra_metadata attempt wins, int-cast
    assert r.machine == "rig-a"
    assert r.skills_staged == ["juju-cli", "mysql-charms-source"]
    assert r.early_exit is None
    assert r.calls == 1
    assert r.total_tokens == 3512162
    assert r.skill_reads == ["juju-cli"]
    assert (r.verdict, r.root_cause, r.schema_ok) == ("charm-bug", "rev916", True)
    assert r.runner_status == "done"


def test_read_bundle_refuses_incomplete_bundles(tmp_path):
    # every missing required artifact is a format refusal — the era
    # compatibility tolerance (pre-sidecar / five-probe-era shapes) is
    # gone; the message carries the validate_bundle reason strings
    bare = _make_bundle(tmp_path)
    with pytest.raises(BundleFormatError) as ei:
        read_bundle(bare)
    msg = str(ei.value)
    assert "no capture.json (sidecar)" in msg
    assert "no session.jsonl (primary record)" in msg
    assert "no graded.jsonl (mechanical grade)" in msg
    assert "no runner-status (attempt marker)" in msg
    # missing runner-status alone is a refusal — the five-probe-era
    # signature (pre-c3 runners shipped no marker)
    no_marker = _make_bundle(
        tmp_path / "no-marker",
        capture=CAPTURE,
        session_rows=SESSION_ROWS,
        graded=GRADED,
    )
    with pytest.raises(BundleFormatError, match="no runner-status"):
        read_bundle(no_marker)
    # unparseable sidecar → refusal too (not a flag on a record)
    bad = _make_bundle(
        tmp_path / "bad",
        raw_capture="{oops",
        session_rows=SESSION_ROWS,
        graded=GRADED,
        runner_status="done",
    )
    with pytest.raises(BundleFormatError, match="capture.json unparseable"):
        read_bundle(bad)


def test_read_bundle_value_tolerance_quarantine(tmp_path):
    # runner-status is a VALUE, not a format check: an `incomplete`
    # bundle reads fine (quarantine adjudication needs the value)
    b = _make_bundle(
        tmp_path,
        capture=CAPTURE,
        session_rows=SESSION_ROWS,
        graded=GRADED,
        runner_status="incomplete\n",
    )
    r = read_bundle(b)
    assert r.runner_status == "incomplete"
    assert r.calls == 1 and r.pair == "901"
    assert validate_bundle(b) == ["runner-status=incomplete"]


def test_read_bundle_last_grade_wins_and_schema_ok_bool(tmp_path):
    graded = [{"verdict": {"verdict": "v1"}}]  # no schema_ok key
    r = read_bundle(
        _make_bundle(
            tmp_path,
            capture=CAPTURE,
            session_rows=SESSION_ROWS,
            graded=graded,
            runner_status="done",
        )
    )
    assert r.verdict == "v1" and r.root_cause == ""
    assert r.schema_ok is False  # bool(None) — absent means not-ok


# ---------------------------------------------------------------- validate


def test_validate_bundle_valid_and_reasons(tmp_path):
    b = _make_bundle(
        tmp_path,
        capture=CAPTURE,
        session_rows=SESSION_ROWS,
        graded=GRADED,
        runner_status="done",
    )
    assert validate_bundle(b) == []
    # each missing artifact has its exact reason
    bare = _make_bundle(tmp_path / "bare")
    assert validate_bundle(bare) == [
        "no capture.json (sidecar)",
        "no session.jsonl (primary record)",
        "no graded.jsonl (mechanical grade)",
        "no runner-status (attempt marker)",
    ]
    # unparseable sidecar / session
    bad = _make_bundle(
        tmp_path / "bad", raw_capture="{oops", session_rows=SESSION_ROWS, graded=GRADED
    )
    (bad / "session.jsonl").write_text("")
    reasons = validate_bundle(bad)
    assert "capture.json unparseable" in reasons
    assert "session.jsonl unparseable" in reasons
    # runner-status != done is a reason (quarantined bundle)
    q = _make_bundle(
        tmp_path / "q",
        capture=CAPTURE,
        session_rows=SESSION_ROWS,
        graded=GRADED,
        runner_status="incomplete\n",
    )
    assert validate_bundle(q) == ["runner-status=incomplete"]
