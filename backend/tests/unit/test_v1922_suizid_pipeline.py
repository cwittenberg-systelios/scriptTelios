"""
v19.22 / S2 - Einbau des Pflicht-Hinweises Suizidalitaet in _finalize.

Testet die Phase isoliert (Muster aus test_v1921_s6_pipeline.py): kein
LLM, kein HTTP, nur PipelineInput/PipelineState + _finalize.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.generation_pipeline import PipelineInput, PipelineState, _finalize

FRAU_M = {"anrede": "Frau", "vorname": "Maria", "nachname": "Mueller", "initial": "M."}

DOKU = (
    "**Auftragsklärung**\n"
    "Frau M. kam mit dem Anliegen, ihren Umgang mit Erschöpfung zu verändern.\n\n"
    "**Einladungen**\n"
    "Es wurde keine konkrete Einladung oder Aufgabe vereinbart."
)


def _job():
    job = MagicMock()
    job.job_id = "test-job"
    return job


def _state(text: str, *, transkript: str = "", patient_name=FRAU_M):
    st = PipelineState()
    st.result = {"text": text, "model_used": "qwen3"}
    st.patient_name = patient_name
    st._transkript_raw_for_result = transkript or None
    st.transkript_text = transkript
    return st


def _ctx(workflow: str = "dokumentation", *, bullets: str = ""):
    return PipelineInput(
        workflow=workflow, instructions="I", model=None, bullets=bullets,
    )


# ── Ergaenzung ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_doku_ohne_inhalt_bekommt_standardsatz():
    st = _state(DOKU, transkript="Therapeutin: Wie war die Woche mit dem Schlaf?")
    res = await _finalize(_ctx(), _job(), st)
    assert res["suizid_note_status"] == "appended"
    assert res["text"].endswith(
        "Frau M. ist glaubhaft absprachefähig, "
        "keine Anzeichen von akuter Suizidalität."
    )
    assert res["text"].startswith(DOKU)


@pytest.mark.asyncio
async def test_doku_mit_inhalt_bleibt_unveraendert():
    text = DOKU + "\n\nFrau M. berichtete von Suizidgedanken, von denen sie sich glaubhaft distanzieren konnte."
    st = _state(text)
    res = await _finalize(_ctx(), _job(), st)
    assert res["suizid_note_status"] == "present"
    assert res["text"] == text


@pytest.mark.asyncio
async def test_platzhalter_substitution_laeuft_vor_der_ergaenzung():
    """Der Standardsatz nennt die Referenzform - der Hook muss deshalb NACH
    substitute_patient_placeholders laufen."""
    st = _state("[Patient/in] kam mit dem Anliegen, besser zu schlafen.")
    res = await _finalize(_ctx(), _job(), st)
    assert "[Patient/in]" not in res["text"]
    assert res["text"].count("Frau M.") == 2


# ── D2=B: Quellenkonflikt ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_transkript_thematisiert_suizidalitaet_output_nicht():
    st = _state(DOKU, transkript="Therapeutin: Haben Sie Gedanken, sich das Leben zu nehmen?")
    res = await _finalize(_ctx(), _job(), st)
    assert res["suizid_note_status"] == "source_conflict"
    assert res["text"] == DOKU
    assert "absprachefähig" not in res["text"]


@pytest.mark.asyncio
async def test_stichpunkte_zaehlen_als_quelle():
    st = _state(DOKU)
    res = await _finalize(_ctx(bullets="Suizidalität angesprochen"), _job(), st)
    assert res["suizid_note_status"] == "source_conflict"


# ── D4=C: ohne Kuerzel keine Ergaenzung ───────────────────────────────────────

@pytest.mark.asyncio
async def test_ohne_patientenname_keine_ergaenzung():
    st = _state(DOKU, patient_name=None)
    res = await _finalize(_ctx(), _job(), st)
    assert res["suizid_note_status"] == "no_name"
    assert res["text"] == DOKU


# ── Abgrenzung anderer Workflows ──────────────────────────────────────────────

@pytest.mark.parametrize("workflow", [
    "anamnese", "verlaengerung", "folgeverlaengerung", "akutantrag",
    "entlassbericht", "ism_fragebogen",
])
@pytest.mark.asyncio
async def test_andere_workflows_unveraendert(workflow):
    st = _state(DOKU)
    if workflow == "anamnese":
        st.result["befund_text"] = "Im Gespräch offen, wach, bewusstseinsklar."
    res = await _finalize(_ctx(workflow), _job(), st)
    assert res["suizid_note_status"] is None
    assert res["text"] == DOKU
