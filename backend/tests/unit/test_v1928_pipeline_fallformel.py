"""v19.28 (S2): Phase _run_fallformel isoliert (Muster test_v1922_suizid_pipeline)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services import generation_pipeline as gp
from app.services.generation_pipeline import PipelineInput, PipelineState, _run_fallformel

FF = "### Auftrag\nA\n\n### Themenkandidaten\n1. **T1** – x (Einzel 01.01.)\n2. **T2** – y\n\n### Wendepunkte je Modalität\n- Einzeltherapie: e\n\n### Symptomveränderung\ns\n\n### Offene Themen\no"


def _job():
    job = MagicMock()
    job.job_id = "j1"
    return job


def _ctx(workflow="entlassbericht", struktur="thematisch", override=None):
    return PipelineInput(workflow=workflow, instructions="I", model="ollama/m",
                         eb_struktur=struktur, fallformel_override=override)


def _state():
    st = PipelineState()
    st.verlaufsdoku_text = "Summary 01.01. Sitzung"
    st.verlaufsdoku_raw_text = "Roh 01.01. Sitzung"
    st.antragsvorlage_text = "Anamnese"
    st.prozessreflexion_text = None
    st._patient_initial_early = "Frau M."
    st.bands = {"extraction": (10, 30), "llm": (30, 100)}
    return st


@pytest.mark.asyncio
async def test_statusquo_laeuft_nicht(monkeypatch):
    called = []
    async def fake(**kw):
        called.append(kw)
        return {}
    monkeypatch.setattr("app.services.fallformel.build_fallformel", fake)
    st = _state()
    await _run_fallformel(_ctx(struktur="modalitaet"), _job(), st)
    assert not called and st.fallformel_text is None and st._fallformel_audit is None


@pytest.mark.asyncio
async def test_anderer_workflow_laeuft_nicht(monkeypatch):
    st = _state()
    await _run_fallformel(_ctx(workflow="verlaengerung"), _job(), st)
    assert st.fallformel_text is None


@pytest.mark.asyncio
async def test_thematisch_llm_pfad(monkeypatch):
    seen = {}
    async def fake(**kw):
        seen.update(kw)
        return {"text": FF, "themen": ["T1", "T2"], "issues": [], "degraded": False,
                "duration_s": 1.0, "word_count": 20, "telemetry": {}, "system_prompt": "S", "user_content": "U"}
    monkeypatch.setattr("app.services.fallformel.build_fallformel", fake)
    logged = []
    monkeypatch.setattr(gp, "_log_prompt", lambda *a: logged.append(("p", a[2])))
    monkeypatch.setattr(gp, "_log_output", lambda *a: logged.append(("o", a[2])))
    st = _state()
    job = _job()
    await _run_fallformel(_ctx(), job, st)
    assert st.fallformel_text == FF
    assert st._fallformel_audit["applied"] and st._fallformel_audit["source"] == "llm"
    assert st._fallformel_audit["themen"] == ["T1", "T2"]
    assert seen["model"] == "ollama/m"
    assert "Roh 01.01." in seen["raw_source_text"] and "Anamnese" in seen["raw_source_text"]
    assert ("p", "stage1b_fallformel") in logged and ("o", "stage1b_fallformel") in logged
    job.set_progress.assert_called()


@pytest.mark.asyncio
async def test_thematisch_override_ohne_llm(monkeypatch):
    async def fake(**kw):
        raise AssertionError("LLM darf bei Override nicht laufen")
    monkeypatch.setattr("app.services.fallformel.build_fallformel", fake)
    st = _state()
    await _run_fallformel(_ctx(override=FF), _job(), st)
    assert st._fallformel_audit["source"] == "therapeut"
    assert st._fallformel_audit["themen"] == ["**T1** – x (Einzel 01.01.)", "**T2** – y"]
    assert st.fallformel_text.startswith("### Auftrag")


@pytest.mark.asyncio
async def test_fehler_kippt_job_nicht(monkeypatch):
    async def fake(**kw):
        raise RuntimeError("Ollama down")
    monkeypatch.setattr("app.services.fallformel.build_fallformel", fake)
    st = _state()
    await _run_fallformel(_ctx(), _job(), st)
    assert st.fallformel_text is None
    assert st._fallformel_audit["applied"] is False
    assert "Ollama down" in st._fallformel_audit["fallback_reason"]


@pytest.mark.asyncio
async def test_ohne_verlauf_kein_call(monkeypatch):
    st = _state()
    st.verlaufsdoku_text = ""
    await _run_fallformel(_ctx(), _job(), st)
    assert st._fallformel_audit["applied"] is False
