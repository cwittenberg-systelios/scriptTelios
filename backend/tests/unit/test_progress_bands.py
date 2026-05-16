"""
Tests fuer app/services/progress_bands.py.

Reine Tabellen-/Berechnungslogik: kein Netz, kein LLM. Vor diesem Test
hatte das Modul 0% Coverage obwohl es bei jedem Job aufgerufen wird.
"""
import pytest

from app.services.progress_bands import (
    compute_bands,
    load_durations,
    reload,
    FALLBACK,
)


# ─────────────────────────────────────────────────────────────────────────────
# compute_bands: 6 Workflows × {audio, docs, both, neither}
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_cache_and_isolate(monkeypatch, tmp_path):
    """Vor jedem Test:
      - Modul-Cache leeren
      - load_durations()-Default-Pfad auf eine nicht-existente Datei
        umlenken, damit der echte /workspace/performance.log (falls vorhanden)
        die Tests nicht verschmutzt.
    """
    reload()
    nonexistent = tmp_path / "no-perf-log.log"
    import app.services.progress_bands as pb_mod

    original_load = pb_mod.load_durations

    def _load(log: str = str(nonexistent)) -> dict:
        return original_load(log)

    monkeypatch.setattr(pb_mod, "load_durations", _load)
    yield
    reload()


class TestComputeBandsStructure:

    @pytest.mark.parametrize("workflow", sorted(FALLBACK.keys()))
    def test_alle_workflows_liefern_dict(self, workflow):
        bands = compute_bands(workflow, has_audio=True, has_docs=True)
        assert isinstance(bands, dict)
        # llm-Phase muss IMMER drin sein (jeder Workflow generiert)
        assert "llm" in bands

    def test_audio_workflow_hat_transcription_band(self):
        """dokumentation/anamnese haben transcription > 0 in FALLBACK."""
        bands = compute_bands("dokumentation", has_audio=True, has_docs=False)
        assert "transcription" in bands
        # Range muss aufsteigend sein
        assert bands["transcription"][0] < bands["transcription"][1]

    def test_kein_audio_kein_transcription_band(self):
        bands = compute_bands("dokumentation", has_audio=False, has_docs=False)
        assert "transcription" not in bands

    def test_keine_docs_kein_extraction_band(self):
        bands = compute_bands("dokumentation", has_audio=True, has_docs=False)
        assert "extraction" not in bands

    def test_docs_only_workflow_ohne_audio(self):
        """Verlaengerung/Akutantrag/Folgeverlaengerung haben transcription=0 in FALLBACK,
        also auch mit has_audio=True kommt kein transcription-Band."""
        bands = compute_bands("verlaengerung", has_audio=True, has_docs=True)
        assert "transcription" not in bands  # FALLBACK['verlaengerung']['transcription']=0
        assert "extraction" in bands
        assert "llm" in bands


class TestComputeBandsRange:

    def test_bands_starten_bei_5_oder_hoeher(self):
        """Initialer Cursor ist 5.0 — erste Phase startet bei 5."""
        bands = compute_bands("dokumentation", has_audio=True, has_docs=True)
        starts = [b[0] for b in bands.values()]
        assert min(starts) >= 5

    def test_bands_enden_bei_etwa_95(self):
        """Cursor laeuft auf 5 + 90 = 95. Letztes Band endet dort."""
        bands = compute_bands("dokumentation", has_audio=True, has_docs=True)
        ends = [b[1] for b in bands.values()]
        assert max(ends) <= 95
        assert max(ends) >= 90  # nicht abgeschnitten

    def test_bands_nicht_ueberlappend(self):
        """Sortiert nach Start: jeder Folger startet wo der Vorgaenger endete."""
        bands = compute_bands("dokumentation", has_audio=True, has_docs=True)
        sorted_bands = sorted(bands.values(), key=lambda b: b[0])
        for prev, nxt in zip(sorted_bands, sorted_bands[1:]):
            # Toleranz ±1 wegen Rundung
            assert abs(prev[1] - nxt[0]) <= 1, (
                f"Luecke/Ueberlapp zwischen {prev} und {nxt}"
            )

    def test_unbekannter_workflow_fallback_dokumentation(self):
        """Fuer foo wird auf dokumentation gefallback'd."""
        bands_foo = compute_bands("foo", has_audio=True, has_docs=True)
        bands_doku = compute_bands("dokumentation", has_audio=True, has_docs=True)
        assert bands_foo == bands_doku


# ─────────────────────────────────────────────────────────────────────────────
# load_durations
# ─────────────────────────────────────────────────────────────────────────────


class TestLoadDurations:

    def test_fehlende_log_datei_default_zu_fallback(self):
        result = load_durations("/nonexistent/path.log")
        # Alle Workflows muessen drin sein, mit Fallback-Werten
        for wf, fb in FALLBACK.items():
            assert wf in result
            for phase, expected in fb.items():
                assert result[wf][phase] == expected

    def test_cache_wird_genutzt(self, tmp_path):
        """Zweiter Aufruf darf nicht erneut lesen."""
        log = tmp_path / "perf.log"
        log.write_text("")  # leere Datei

        first = load_durations(str(log))
        # Datei manipulieren
        log.write_text('{"workflow":"dokumentation","phases":{"llm":99}}\n' * 5)

        # Zweiter Aufruf: muss Cache zurueckgeben (alte Werte), nicht neue Datei lesen
        second = load_durations(str(log))
        assert first == second

    def test_median_aus_drei_samples(self, tmp_path):
        """Mit >= 3 Samples wird Median berechnet (statt Fallback)."""
        log = tmp_path / "perf.log"
        log.write_text(
            '{"workflow":"dokumentation","phases":{"llm":40,"transcription":100}}\n'
            '{"workflow":"dokumentation","phases":{"llm":50,"transcription":120}}\n'
            '{"workflow":"dokumentation","phases":{"llm":60,"transcription":110}}\n'
        )
        reload()
        result = load_durations(str(log))
        # Median von [40,50,60] = 50
        assert result["dokumentation"]["llm"] == 50
        # Median von [100,120,110] = 110
        assert result["dokumentation"]["transcription"] == 110

    def test_weniger_als_drei_samples_fallback(self, tmp_path):
        log = tmp_path / "perf.log"
        log.write_text(
            '{"workflow":"dokumentation","phases":{"llm":999}}\n'
            '{"workflow":"dokumentation","phases":{"llm":999}}\n'
        )
        reload()
        result = load_durations(str(log))
        # Nur 2 samples -> Fallback (35)
        assert result["dokumentation"]["llm"] == FALLBACK["dokumentation"]["llm"]

    def test_malformed_json_zeilen_ignoriert(self, tmp_path):
        log = tmp_path / "perf.log"
        log.write_text(
            'NICHT-JSON\n'
            '{"workflow":"dokumentation"}\n'           # phases fehlt
            '{"workflow":null,"phases":{"llm":50}}\n'   # workflow fehlt
            '{"workflow":"dokumentation","phases":{"llm":50}}\n'
            '{"workflow":"dokumentation","phases":{"llm":50}}\n'
            '{"workflow":"dokumentation","phases":{"llm":50}}\n'
        )
        reload()
        result = load_durations(str(log))
        # 3 gueltige Eintraege -> Median 50
        assert result["dokumentation"]["llm"] == 50

    def test_negative_dauer_ignoriert(self, tmp_path):
        log = tmp_path / "perf.log"
        log.write_text(
            '{"workflow":"dokumentation","phases":{"llm":-5}}\n'
            '{"workflow":"dokumentation","phases":{"llm":-5}}\n'
            '{"workflow":"dokumentation","phases":{"llm":-5}}\n'
            '{"workflow":"dokumentation","phases":{"llm":-5}}\n'
        )
        reload()
        result = load_durations(str(log))
        # Alle ignoriert -> Fallback
        assert result["dokumentation"]["llm"] == FALLBACK["dokumentation"]["llm"]


# ─────────────────────────────────────────────────────────────────────────────
# Konstanten-Sanity
# ─────────────────────────────────────────────────────────────────────────────


class TestFallbackTabelle:

    def test_alle_6_workflows_drin(self):
        expected = {
            "dokumentation", "anamnese", "verlaengerung",
            "folgeverlaengerung", "akutantrag", "entlassbericht",
        }
        assert set(FALLBACK.keys()) == expected

    @pytest.mark.parametrize("wf", sorted([
        "dokumentation", "anamnese", "verlaengerung",
        "folgeverlaengerung", "akutantrag", "entlassbericht",
    ]))
    def test_jeder_workflow_hat_alle_drei_phasen(self, wf):
        assert set(FALLBACK[wf].keys()) == {"transcription", "extraction", "llm"}
