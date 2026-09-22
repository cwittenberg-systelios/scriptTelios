"""v19.28 (S1): Stage-1-Erweiterungen fuer den thematischen Entlassbericht.

- Hypothesen-Block nur im EB-Struktur-Prompt (D6)
- Abdeckungs-Guard: fehlender Behandlungsmonat wird erkannt und loest den
  Retry aus (S0-Befund 2026-09-22, EB-HerrR: Dezember komplett weg)
"""
import pytest

from app.services import verlauf_summary as vs
from app.services.verlauf_summary import (
    _build_focus_hint,
    _structure_for,
    _system_prompt,
    detect_coverage_gap,
)


class TestStrukturEB:
    def test_eb_hat_hypothesen_block(self):
        st = _structure_for("entlassbericht")
        assert "### Dokumentierte Hypothesen und Muster" in st
        assert "VIER Abschnitten" in st
        assert "Laut Protokoll" in st

    @pytest.mark.parametrize("wf", ["verlaengerung", "folgeverlaengerung", None])
    def test_andere_workflows_unveraendert(self, wf):
        st = _structure_for(wf)
        assert "Hypothesen und Muster" not in st
        assert "DREI Abschnitten" in st

    def test_system_prompt_nutzt_workflow(self):
        assert "Hypothesen und Muster" in _system_prompt("", workflow="entlassbericht")
        assert "Hypothesen und Muster" not in _system_prompt("", workflow="verlaengerung")

    def test_fokus_hint_eb_fordert_zeitspanne_und_hypothesen(self):
        h = _build_focus_hint("entlassbericht")
        assert "GESAMTE" in h and "ZEITSPANNE" in h
        assert "HYPOTHESEN" in h


_SRC = "\n".join(
    [f"{d:02d}.12.2025 Einzelgespräch: Thema X" for d in (9, 12, 15, 19, 23, 30)]
    + [f"{d:02d}.01.2026 Bezugsgruppe: Thema Y" for d in (2, 6, 8, 12, 14, 20, 29)]
)


class TestAbdeckungsGuard:
    def test_fehlender_dezember_wird_erkannt(self):
        summary = "Übersicht\n69 Sitzungen vom 02.01. bis 29.01.\n\nVerlauf\n02.01. (Bezugsgruppe): ...\n14.01. ...\n29.01. ..."
        gap = detect_coverage_gap(summary, _SRC)
        assert gap is not None
        assert gap["type"] == "abdeckung_luecke"
        assert gap["severity"] == "high"
        assert gap["missing_months"] == [12]

    def test_vollstaendige_abdeckung_kein_alarm(self):
        summary = "Übersicht\n13 Sitzungen vom 09.12. bis 29.01.\n\nVerlauf\n09.12. ...\n15.12. ...\n02.01. ...\n29.01. ..."
        assert detect_coverage_gap(summary, _SRC) is None

    def test_randmonat_mit_wenigen_markern_ignoriert(self):
        # Ein einzelner November-Eintrag (Voraufnahme) darf keinen Alarm ausloesen
        src = "28.11.2025 Vorgespräch\n" + _SRC
        summary = "Verlauf\n09.12. ...\n02.01. ...\n29.01. ..."
        assert detect_coverage_gap(summary, src) is None

    def test_zu_wenig_daten_kein_alarm(self):
        assert detect_coverage_gap("12.01. etwas", "05.12.2025 Sitzung") is None
        assert detect_coverage_gap("", _SRC) is None
        assert detect_coverage_gap("x", "") is None

    def test_datum_mit_und_ohne_jahr(self):
        summary = "Verlauf\n09.12.2025 ...\n02.01.2026 ..."
        assert detect_coverage_gap(summary, _SRC) is None


class TestRetryBeiLuecke:
    @pytest.mark.asyncio
    async def test_luecke_loest_retry_aus_und_retry_wird_uebernommen(self, monkeypatch):
        calls = []
        lueckig = "### Übersicht\n" + " ".join(["wort"] * 60) + "\n02.01. Sitzung A\n14.01. Sitzung B\n29.01. Sitzung C"
        voll = "### Übersicht\n" + " ".join(["wort"] * 60) + "\n09.12. Sitzung 0\n02.01. Sitzung A\n29.01. Sitzung C"

        async def fake_generate(system_prompt, user_content, **kw):
            calls.append(system_prompt)
            return {"text": lueckig if len(calls) == 1 else voll, "telemetry": {}}

        monkeypatch.setattr(vs, "stage1_generate", fake_generate)
        monkeypatch.setattr("app.services.staging.stage1_chunk_chars", lambda: 10**9)
        monkeypatch.setattr("app.services.staging.compute_verlauf_min_acceptable", lambda t, raw_words=0: 10)
        res = await vs.summarize_verlauf(_SRC, "entlassbericht", target_words=60)
        assert len(calls) == 2, "Retry muss durch die Abdeckungsluecke ausgeloest werden"
        assert "abdeckung_luecke" in calls[1]
        assert res["retry_used"] is True
        assert res["degraded"] is False
        assert "09.12." in res["summary"]
        assert not any(i["type"] == "abdeckung_luecke" for i in res["issues"])

    @pytest.mark.asyncio
    async def test_luecke_bleibt_nach_retry_degraded(self, monkeypatch):
        lueckig = "### Übersicht\n" + " ".join(["wort"] * 60) + "\n02.01. Sitzung A\n29.01. Sitzung C"

        async def fake_generate(system_prompt, user_content, **kw):
            return {"text": lueckig, "telemetry": {}}

        monkeypatch.setattr(vs, "stage1_generate", fake_generate)
        monkeypatch.setattr("app.services.staging.stage1_chunk_chars", lambda: 10**9)
        monkeypatch.setattr("app.services.staging.compute_verlauf_min_acceptable", lambda t, raw_words=0: 10)
        res = await vs.summarize_verlauf(_SRC, "entlassbericht", target_words=60)
        assert res["retry_used"] is True
        assert res["degraded"] is True
        assert any(i["type"] == "abdeckung_luecke" for i in res["issues"])
