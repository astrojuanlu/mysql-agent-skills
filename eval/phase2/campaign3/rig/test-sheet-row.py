"""Tests for sheet-row.py's contract extraction (pool-mode bug fix).

The 2026-10-17 pool rehearsal caught it live (pair 5/A, a reused node): the
smoke run graded schema_ok True, but the sheet row said False —
sheet-row re-parsed the contract block with the mr-234 fields hardcoded.
Now graded.jsonl is the authoritative parse and the mr-234 re-parse is
the legacy fallback (bundles with no graded.jsonl).
"""

import importlib.util
import json
from pathlib import Path

RIG = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location("sheet_row", RIG / "sheet-row.py")
sr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sr)

FENCE = "`" * 3


def _bundle(tmp_path, graded=None, text="done", incidents=False):
    """One minimal bundle dir: capture + session (+ optional grade/inc)."""
    out = tmp_path / "b"
    out.mkdir()
    (out / "capture.json").write_text(
        json.dumps({"model": "m", "ts": "t", "pi_exit_code": 0})
    )
    msg = {
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}
    }
    (out / "session.jsonl").write_text(json.dumps(msg) + "\n")
    if graded is not None:
        (out / "graded.jsonl").write_text(json.dumps(graded) + "\n")
    if incidents:
        (out / "incidents.json").write_text(
            json.dumps(
                {
                    "model_destroy": [],
                    "add_model": [],
                    "bootstrap": [],
                    "gh_commands": 0,
                    "projects_paths": [],
                }
            )
        )
    return out


def _row(out):
    """One row from a minimal bundle (positional cells per the header)."""
    return sr.row_for(out, 1, "A", "m", "on", 1, "j")


def test_smoke_contract_from_graded_jsonl(tmp_path):
    """A smoke bundle: graded.jsonl says schema_ok True, no verdict field."""
    out = _bundle(
        tmp_path,
        graded={
            "schema_ok": True,
            "verdict": {"models": ["controller"], "deployed": "empty"},
        },
        incidents=True,
    )
    row = _row(out)
    assert row[14] is True
    assert row[15] == ""  # no verdict field in the smoke contract
    assert "contract=0" in row[18]


def test_legacy_mr234_contract_fallback(tmp_path):
    """No graded.jsonl: the mr-234 re-parse still works (legacy bundles)."""
    text = (
        "done\n"
        f"{FENCE}yaml\nverdict: charm-bug\nroot_cause: x\nreproduction: y\n"
        f"evidence_quotes: [a]\naffected_component: z\n{FENCE}"
    )
    out = _bundle(tmp_path, text=text)
    row = _row(out)
    assert row[14] is True
    assert row[15] == "charm-bug"


def test_no_contract_anywhere_is_schema_ok_false(tmp_path):
    """Grade says not-ok: row schema_ok False and the incident is flagged."""
    out = _bundle(tmp_path, graded={"schema_ok": False, "verdict": {}}, incidents=True)
    row = _row(out)
    assert row[14] is False
    assert "contract=1" in row[18]
