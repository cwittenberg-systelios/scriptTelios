"""
tests/unit/test_v1916_transcript_guard.py
─────────────────────────────────────────
v19.16: Konfabulations-Guard (Sprint G) + Transkript-Vollständigkeit (Sprint T).

Ausloeser: Job 58db7006 (2026-08-04) - P1 generierte ohne Transkript ein
vollstaendig konfabuliertes Dokument; Transkript endete 214 s vor Audio-Ende
(aufnahme-79.webm, Browser-webm ohne Duration-Header).

Kein LLM, keine DB, kein ffmpeg-Subprozess (Parser werden direkt getestet).
"""
from __future__ import annotations

import pytest


# ── Sprint G: Quellen-Gate ────────────────────────────────────────────────────

class TestMissingSourceGate:
    def _gate(self, workflow, **kw):
        from app.api.jobs import _missing_source_error
        return _missing_source_error(workflow, **kw)

    def test_p1_ohne_quellen_blockt(self):
        msg = self._gate("dokumentation", transkript_text="")
        assert msg is not None
        assert "NICHT erstellt" in msg

    def test_p1_mit_transkript_ok(self):
        assert self._gate("dokumentation", transkript_text="[A]: Hallo.") is None

    def test_p1_mit_stichpunkten_ok(self):
        assert self._gate("dokumentation", transkript_text="", bullets="Thema X") is None

    def test_p1_whitespace_zaehlt_als_leer(self):
        assert self._gate("dokumentation", transkript_text="   \n  ") is not None

    def test_p1_failure_reason_in_meldung(self):
        msg = self._gate(
            "dokumentation", transkript_text="",
            transcript_failure_reason="Die Aufnahme war nach 30 Minuten Wartezeit noch nicht fertig transkribiert.",
        )
        assert "30 Minuten" in msg

    def test_p2_alle_quellen_leer_blockt(self):
        msg = self._gate("anamnese", transkript_text="", selbstauskunft_text="", vorbefunde_text="")
        assert msg is not None and "Anamnese" in msg

    def test_p2_eine_quelle_reicht(self):
        assert self._gate("anamnese", selbstauskunft_text="Fragebogen-Inhalt") is None
        assert self._gate("anamnese", vorbefunde_text="Vorbericht") is None
        assert self._gate("anamnese", transkript_text="[A]: ...") is None

    def test_andere_workflows_kein_gate(self):
        # P3/P4 haben eigene Pflichtquellen-Validierung.
        for wf in ("verlaengerung", "folgeverlaengerung", "entlassbericht", "akutantrag"):
            assert self._gate(wf, transkript_text="") is None


# ── Sprint T1: ffmpeg-stderr-Dauer-Parser ─────────────────────────────────────

class TestFfmpegStderrDurationParser:
    def _parse(self, stderr):
        from app.services.transcription import _parse_ffmpeg_stderr_duration
        return _parse_ffmpeg_stderr_duration(stderr)

    def test_header_duration_bevorzugt(self):
        stderr = "  Duration: 00:10:05.50, start: 0.0\nsize=N/A time=00:10:05.50"
        assert self._parse(stderr) == pytest.approx(605.5)

    def test_browser_webm_ohne_header(self):
        # Der Fall aufnahme-79: Header N/A, echte Dauer nur in der Progresszeile.
        stderr = (
            "  Duration: N/A, start: 0.000000, bitrate: N/A\n"
            "size=N/A time=00:30:00.00 bitrate=N/A speed=300x\r"
            "size=N/A time=01:02:38.71 bitrate=N/A speed= 323x\n"
        )
        assert self._parse(stderr) == pytest.approx(3758.71, abs=0.1)

    def test_letzte_time_zeile_gewinnt(self):
        stderr = "Duration: N/A\ntime=00:00:10.00\ntime=00:00:20.00\ntime=00:00:30.50\n"
        assert self._parse(stderr) == pytest.approx(30.5)

    def test_nichts_gefunden(self):
        assert self._parse("kompletter Muell ohne Zeitangaben") == 0.0


# ── Sprint T2: Dateigroessen-Schaetzung nach OBEN gepuffert ───────────────────

class TestSizeEstimate:
    def test_puffer_richtung(self):
        from app.services.transcription import _estimate_duration_from_size
        # aufnahme-79: 11.165.788 B, echte Dauer 3758,7 s.
        # Alte Formel (÷1.05) ergab 3544,7 s → 214 s zu kurz.
        est = _estimate_duration_from_size(11_165_788)
        assert est > 3758.7, "Schaetzung muss ueber der echten Dauer liegen"
        assert est == pytest.approx(11_165_788 / 3000 * 1.05)

    def test_minimum_eins(self):
        from app.services.transcription import _estimate_duration_from_size
        assert _estimate_duration_from_size(0) == 1.0


# ── Sprint T3: letzter Chunk ohne '-to' ───────────────────────────────────────

class TestSplitAudioLastChunkOpen:
    def test_letzter_chunk_liest_bis_eof(self, tmp_path, monkeypatch):
        from app.services import transcription as tr
        captured = []

        def _fake_run(cmd, **kw):
            captured.append(list(cmd))
            class R: returncode = 0
            return R()

        monkeypatch.setattr(tr.subprocess, "run", _fake_run)
        src = tmp_path / "a.webm"
        src.write_bytes(b"x")
        tr._split_audio(src, splits=[900.0, 1800.0], tmp_dir=tmp_path, duration=2700.0)

        assert len(captured) == 3
        # Chunks 1+2 haben -to, der letzte NICHT (liest bis EOF)
        assert "-to" in captured[0] and "-to" in captured[1]
        assert "-to" not in captured[2], (
            "Letzter Chunk darf kein '-to' haben - sonst wird bei "
            "unterschaetzter Dauer das Aufnahme-Ende abgeschnitten"
        )


# ── Sprint T4: QC-Issue TRANSCRIPT_INCOMPLETE ────────────────────────────────

class TestTranscriptCoverageIssue:
    def _run(self, gap, workflow="dokumentation"):
        from app.services.quality_check import (
            ISSUE_CODE_TRANSCRIPT_INCOMPLETE, run_quality_check,
        )
        issues = run_quality_check(
            "Ein hinreichend langer Beispieltext. " * 30, workflow,
            transcript_coverage_gap_s=gap,
        )
        return [i for i in issues if i.code == ISSUE_CODE_TRANSCRIPT_INCOMPLETE]

    def test_critical_bei_luecke(self):
        from app.services.quality_check import SEVERITY_CRITICAL
        hits = self._run(214.0)
        assert len(hits) == 1
        assert hits[0].severity == SEVERITY_CRITICAL
        assert "3.6 Minuten" in hits[0].message
        assert "NICHT durch Neu-Generierung behebbar" in hits[0].repair_hint

    def test_kein_issue_ohne_luecke(self):
        assert self._run(None) == []
        assert self._run(0) == []

    def test_nur_p1_p2(self):
        assert self._run(120.0, workflow="entlassbericht") == []
        assert len(self._run(120.0, workflow="anamnese")) == 1
