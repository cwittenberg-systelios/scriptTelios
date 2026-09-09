"""
tests/unit/test_v1919_sprint_k.py — v19.19 Sprint K: Kontextbudget & Trunkierungs-Transparenz.

K1: harte Kontextdecke 32768 statt 20480
K2: INPUT_TRUNCATED-QC-Issue aus der Telemetrie
K3: Head/Tail-bewahrender Sampler statt rein uniform
"""
from __future__ import annotations

import pytest


class TestK1ContextCap:
    def test_harte_decke_ist_32k(self):
        import inspect
        from app.services import llm
        src = inspect.getsource(llm)
        assert "MAX_SAFE_CTX = min(32768," in src
        assert "MAX_SAFE_CTX = min(20480," not in src


class TestK3HeadTailSampler:
    def _text(self, n_lines=2000):
        return "".join(f"Zeile {i:05d}: Inhalt der Sitzung Nummer {i}.\n" for i in range(n_lines))

    def test_unveraendert_wenn_passt(self):
        from app.services.llm import _sample_head_tail
        t = self._text(10)
        assert _sample_head_tail(t, len(t) + 100) == t

    def test_anfang_und_ende_bewahrt(self):
        from app.services.llm import _sample_head_tail
        t = self._text()
        out = _sample_head_tail(t, 20_000)
        assert len(out) <= 20_000 + 300  # Marker-Toleranz
        assert out.startswith("Zeile 00000:")
        assert out.rstrip().endswith("Nummer 1999.")
        # Aufnahme-/Entlassphase (erste/letzte ~5 % Zeilen) sind komplett da
        for i in (0, 1, 2, 50, 1950, 1998, 1999):
            assert f"Zeile {i:05d}:" in out

    def test_marker_nennt_ausgelassene_menge(self):
        from app.services.llm import _sample_head_tail
        out = _sample_head_tail(self._text(), 20_000)
        assert "Mittelteil wurde aus Platzgruenden gekuerzt" in out
        assert "Erfinde keine Inhalte" in out

    def test_mittelteil_gesampelt_nicht_leer(self):
        from app.services.llm import _sample_head_tail
        out = _sample_head_tail(self._text(), 20_000)
        # Irgendetwas aus der Mitte (Zeilen 800-1200) muss ueberleben
        assert any(f"Zeile {i:05d}:" in out for i in range(800, 1200))

    def test_winziges_budget_nur_kopf_und_schwanz(self):
        from app.services.llm import _sample_head_tail
        out = _sample_head_tail(self._text(), 600)
        assert out.startswith("Zeile 00000:")
        assert "Nummer 1999." in out


class TestK2InputTruncatedIssue:
    def _run(self, chars):
        from app.services.quality_check import (
            ISSUE_CODE_INPUT_TRUNCATED, run_quality_check,
        )
        issues = run_quality_check(
            "Ein hinreichend langer Beispieltext. " * 30, "entlassbericht",
            input_truncated_chars=chars,
        )
        return [i for i in issues if i.code == ISSUE_CODE_INPUT_TRUNCATED]

    def test_warning_mit_prozent(self):
        from app.services.quality_check import SEVERITY_WARNING
        hits = self._run((125_000, 50_000))
        assert len(hits) == 1
        assert hits[0].severity == SEVERITY_WARNING
        assert "60 %" in hits[0].message
        assert hits[0].code_detail["pct_removed"] == 60

    def test_liste_aus_json_akzeptiert(self):
        # generation_telemetry kommt nach DB-Roundtrip als Liste, nicht Tuple
        assert len(self._run([80_000, 56_000])) == 1

    def test_kein_issue_ohne_kuerzung(self):
        assert self._run(None) == []
        assert self._run((1000, 1000)) == []
        assert self._run((1000, 1200)) == []
