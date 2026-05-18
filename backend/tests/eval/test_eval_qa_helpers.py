"""
tests/unit/test_eval_qa_helpers.py
──────────────────────────────────
Unit-Tests fuer die QA-Mode-Helper im Eval-Framework (v19 Phase C).

Was hier getestet wird (ohne LLM, ohne Backend, ohne DB):
  - _filter_issues_by_mode  : Filterung nach Severity
  - _delta_qc               : Delta-Vergleich zweier QC-Bundles

Die Helper sind in tests/eval/test_eval.py definiert; wir importieren sie
ueber sys.path, da das eval-Modul nicht als regulaeres Package strukturiert
ist.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


# Eval-Modul als Pfad importieren (kein Package)
_EVAL_DIR = Path(__file__).parents[1] / "eval"
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))


@pytest.fixture(scope="module")
def helpers():
    """Lazy-Import damit ImportErrors im Eval-Modul nicht alle Tests killen."""
    try:
        from test_eval import _delta_qc, _filter_issues_by_mode  # type: ignore
        return _filter_issues_by_mode, _delta_qc
    except ImportError as e:
        pytest.skip(f"Eval-Helper nicht importierbar: {e}")


# ── _filter_issues_by_mode ────────────────────────────────────────────────────

class TestFilterIssuesByMode:

    def test_auto_keeps_all(self, helpers):
        filter_fn, _ = helpers
        issues = [
            {"code": "A", "severity": "critical"},
            {"code": "B", "severity": "warning"},
            {"code": "C", "severity": "info"},
        ]
        assert len(filter_fn(issues, "auto")) == 3

    def test_all_issues_keeps_all(self, helpers):
        # 'all_issues' ist Alias von 'auto'
        filter_fn, _ = helpers
        issues = [
            {"code": "A", "severity": "critical"},
            {"code": "B", "severity": "warning"},
        ]
        assert len(filter_fn(issues, "all_issues")) == 2

    def test_critical_only_keeps_critical(self, helpers):
        filter_fn, _ = helpers
        issues = [
            {"code": "CRIT_A", "severity": "critical"},
            {"code": "WARN_A", "severity": "warning"},
            {"code": "INFO_A", "severity": "info"},
        ]
        out = filter_fn(issues, "critical_only")
        assert len(out) == 1
        assert out[0]["code"] == "CRIT_A"

    def test_empty_input(self, helpers):
        filter_fn, _ = helpers
        assert filter_fn([], "critical_only") == []
        assert filter_fn([], "auto") == []

    def test_returns_copy_not_reference(self, helpers):
        # Filter darf den Eingabe-Container nicht modifizieren
        filter_fn, _ = helpers
        issues = [{"code": "A", "severity": "warning"}]
        out = filter_fn(issues, "auto")
        out.append({"code": "B", "severity": "info"})
        assert len(issues) == 1  # Original unveraendert


# ── _delta_qc ─────────────────────────────────────────────────────────────────

class TestDeltaQc:

    def test_basic_delta(self, helpers):
        _, delta_fn = helpers
        original = {
            "summary": {"critical": 1, "warning": 2, "info": 0, "total": 3},
            "issues": [
                {"code": "CRIT_A", "severity": "critical"},
                {"code": "WARN_A", "severity": "warning"},
                {"code": "WARN_B", "severity": "warning"},
            ],
        }
        repair = {
            "summary": {"critical": 0, "warning": 1, "info": 1, "total": 2},
            "issues": [
                {"code": "WARN_A", "severity": "warning"},
                {"code": "INFO_C", "severity": "info"},
            ],
        }
        d = delta_fn(original, repair)
        assert d["original_count"] == 3
        assert d["repair_count"] == 2
        assert d["delta_total"] == 1
        assert d["resolved"] == ["CRIT_A", "WARN_B"]
        assert d["introduced"] == ["INFO_C"]
        assert d["persisting"] == ["WARN_A"]
        assert d["severity_delta"]["critical"] == 1
        assert d["severity_delta"]["warning"] == 1
        assert d["severity_delta"]["info"] == -1

    def test_repair_improves_to_zero(self, helpers):
        _, delta_fn = helpers
        original = {
            "summary": {"critical": 1, "warning": 0, "info": 0, "total": 1},
            "issues": [{"code": "X", "severity": "critical"}],
        }
        repair = {
            "summary": {"critical": 0, "warning": 0, "info": 0, "total": 0},
            "issues": [],
        }
        d = delta_fn(original, repair)
        assert d["delta_total"] == 1
        assert d["resolved"] == ["X"]
        assert d["introduced"] == []
        assert d["repair_count"] == 0

    def test_repair_makes_worse(self, helpers):
        # Hypothetischer Fall: Repair fuehrt neue Issues ein (negative delta)
        _, delta_fn = helpers
        original = {
            "summary": {"critical": 0, "warning": 1, "info": 0, "total": 1},
            "issues": [{"code": "W1", "severity": "warning"}],
        }
        repair = {
            "summary": {"critical": 1, "warning": 1, "info": 1, "total": 3},
            "issues": [
                {"code": "W1", "severity": "warning"},
                {"code": "C1", "severity": "critical"},
                {"code": "I1", "severity": "info"},
            ],
        }
        d = delta_fn(original, repair)
        assert d["delta_total"] == -2  # schlechter geworden
        assert d["resolved"] == []
        assert sorted(d["introduced"]) == ["C1", "I1"]
        assert d["persisting"] == ["W1"]
        assert d["severity_delta"]["critical"] == -1

    def test_null_inputs(self, helpers):
        _, delta_fn = helpers
        d = delta_fn(None, None)
        assert d["original_count"] == 0
        assert d["repair_count"] == 0
        assert d["delta_total"] == 0
        assert d["resolved"] == []
        assert d["introduced"] == []

    def test_null_original_with_repair(self, helpers):
        # Edge case: kein Original-QC (Pre-v19) aber Repair-Output mit QC
        _, delta_fn = helpers
        repair = {
            "summary": {"critical": 0, "warning": 1, "info": 0, "total": 1},
            "issues": [{"code": "W", "severity": "warning"}],
        }
        d = delta_fn(None, repair)
        assert d["delta_total"] == -1
        assert d["introduced"] == ["W"]

    def test_missing_summary(self, helpers):
        # Defensive: QC ohne summary-Feld
        _, delta_fn = helpers
        original = {"issues": [{"code": "A", "severity": "warning"}]}
        repair = {"issues": []}
        d = delta_fn(original, repair)
        # Summary-Counts faller auf 0 zurueck, aber Codes-Vergleich klappt
        assert d["resolved"] == ["A"]
        assert d["introduced"] == []

    def test_missing_issues_list(self, helpers):
        _, delta_fn = helpers
        d = delta_fn({"summary": {"total": 5}}, {"summary": {"total": 0}})
        assert d["original_count"] == 5
        assert d["repair_count"] == 0
        # Ohne issues-Listen: keine Code-Aussagen moeglich
        assert d["resolved"] == []
        assert d["introduced"] == []
