"""
v19.22 / S3+S5 - QualityCheck-Regeln zum Pflicht-Hinweis Suizidalitaet
und Absicherung des Repair-Pfads.

Entscheidungen: D1=A (Normalfall still), D2=B (Quellenkonflikt -> critical,
kein Standardsatz), D4=C (ohne Kuerzel -> warning).
"""
from __future__ import annotations

import pytest

from app.services import quality_check as QC
from app.services.suizidalitaet import (
    STATUS_APPENDED, STATUS_NO_NAME, STATUS_PRESENT, STATUS_SOURCE_CONFLICT,
)

DOKU = (
    "**Auftragsklärung**\n"
    "Frau M. kam mit dem Anliegen, ihren Umgang mit Erschöpfung zu verändern.\n\n"
    "**Einladungen**\n"
    "Es wurde keine konkrete Einladung oder Aufgabe vereinbart."
)


def _codes(**kw):
    issues = QC.run_quality_check(DOKU, "dokumentation", **kw)
    return [i.code for i in issues]


# ── D1=A: Normalfall ist still ────────────────────────────────────────────────

@pytest.mark.parametrize("status", [None, STATUS_PRESENT, STATUS_APPENDED])
def test_unauffaellige_status_erzeugen_kein_issue(status):
    assert QC.ISSUE_CODE_SUIZIDALITAET_QUELLE_NICHT_UEBERNOMMEN not in _codes(
        suizid_note_status=status)
    assert QC.ISSUE_CODE_SUIZIDHINWEIS_FEHLT not in _codes(suizid_note_status=status)


# ── D2=B: Quellenkonflikt ─────────────────────────────────────────────────────

def test_quellenkonflikt_ist_critical():
    issues = QC.run_quality_check(
        DOKU, "dokumentation", suizid_note_status=STATUS_SOURCE_CONFLICT)
    hit = [i for i in issues
           if i.code == QC.ISSUE_CODE_SUIZIDALITAET_QUELLE_NICHT_UEBERNOMMEN]
    assert len(hit) == 1
    assert hit[0].severity == QC.SEVERITY_CRITICAL
    assert hit[0].repair_hint.strip()
    assert hit[0].code_detail["status"] == STATUS_SOURCE_CONFLICT


# ── D4=C: kein Namenskuerzel ──────────────────────────────────────────────────

def test_fehlendes_kuerzel_ist_warning():
    issues = QC.run_quality_check(
        DOKU, "dokumentation", suizid_note_status=STATUS_NO_NAME)
    hit = [i for i in issues if i.code == QC.ISSUE_CODE_SUIZIDHINWEIS_FEHLT]
    assert len(hit) == 1
    assert hit[0].severity == QC.SEVERITY_WARNING


# ── Abgrenzung ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("workflow", ["anamnese", "entlassbericht", "akutantrag"])
def test_andere_workflows_ohne_suizid_issue(workflow):
    issues = QC.run_quality_check(
        "Ein hinreichend langer Text zum Behandlungsverlauf.", workflow,
        suizid_note_status=STATUS_SOURCE_CONFLICT)
    assert QC.ISSUE_CODE_SUIZIDALITAET_QUELLE_NICHT_UEBERNOMMEN not in [i.code for i in issues]


def test_registry_kennt_die_regel():
    reg = {c.name: c for c in QC.CHECK_REGISTRY}
    assert "suizid_note" in reg
    assert set(reg["suizid_note"].codes) == {
        QC.ISSUE_CODE_SUIZIDALITAET_QUELLE_NICHT_UEBERNOMMEN,
        QC.ISSUE_CODE_SUIZIDHINWEIS_FEHLT,
    }


def test_issues_sind_serialisierbar():
    issues = QC.run_quality_check(
        DOKU, "dokumentation", suizid_note_status=STATUS_SOURCE_CONFLICT)
    bundle = QC.serialize_issues(issues, workflow="dokumentation")
    codes = [i["code"] for i in bundle["issues"]]
    assert QC.ISSUE_CODE_SUIZIDALITAET_QUELLE_NICHT_UEBERNOMMEN in codes
    assert bundle["summary"]["critical"] >= 1
    # Round-Trip: Frontend/Repair lesen die Issues wieder ein
    back = QC.deserialize_issues(bundle)
    assert QC.ISSUE_CODE_SUIZIDALITAET_QUELLE_NICHT_UEBERNOMMEN in [i.code for i in back]


# ── S5: Repair-Pfad ───────────────────────────────────────────────────────────

class _RepairJob:
    """Minimaler JobState-Ersatz fuer _run_repair_coroutine."""
    def __init__(self, *, source="", patient_name=None):
        self.job_id = "repair-0001-abcd"
        self.qc_source_text = source
        self.patient_name = patient_name

    def set_progress(self, *a, **kw):
        pass


FRAU_M = {"anrede": "Frau", "vorname": "Maria", "nachname": "Mueller", "initial": "M."}


@pytest.fixture
def _stub_generate(monkeypatch):
    """generate_text durch einen Stub ersetzen, der einen festen Text liefert."""
    def _install(text):
        async def _fake(*a, **kw):
            return {"text": text, "model_used": "stub", "telemetry": {}}
        monkeypatch.setattr("app.services.repair.generate_text", _fake)
    return _install


@pytest.mark.asyncio
async def test_repair_bekommt_weggeschriebenen_hinweis_zurueck(_stub_generate):
    from app.services.repair import _run_repair_coroutine
    _stub_generate(DOKU)   # Repair-Output ohne jeden Suizidalitaets-Bezug
    res = await _run_repair_coroutine(
        _RepairJob(patient_name=FRAU_M), "dokumentation", "PROMPT",
        original_text="etwas anderes", user_hint="kürzer bitte",
    )
    assert res["suizid_note_status"] == STATUS_APPENDED
    assert res["text"].endswith(
        "Frau M. ist glaubhaft absprachefähig, "
        "keine Anzeichen von akuter Suizidalität."
    )


@pytest.mark.asyncio
async def test_repair_mit_inhalt_bleibt_unveraendert(_stub_generate):
    from app.services.repair import _run_repair_coroutine
    text = DOKU + "\n\nFrau M. verneinte ausdrücklich Suizidgedanken."
    _stub_generate(text)
    res = await _run_repair_coroutine(
        _RepairJob(patient_name=FRAU_M), "dokumentation", "PROMPT",
        original_text="etwas anderes", user_hint="kürzer bitte",
    )
    assert res["suizid_note_status"] == STATUS_PRESENT
    assert res["text"] == text


@pytest.mark.asyncio
async def test_repair_quellenkonflikt_ergaenzt_nicht(_stub_generate):
    from app.services.repair import _run_repair_coroutine
    _stub_generate(DOKU)
    res = await _run_repair_coroutine(
        _RepairJob(source="Therapeutin: Denken Sie daran, sich das Leben zu nehmen?",
                   patient_name=FRAU_M),
        "dokumentation", "PROMPT",
        original_text="etwas anderes", user_hint="kürzer bitte",
    )
    assert res["suizid_note_status"] == STATUS_SOURCE_CONFLICT
    assert "absprachefähig" not in res["text"]


@pytest.mark.asyncio
async def test_repair_anderer_workflow_unberuehrt(_stub_generate):
    from app.services.repair import _run_repair_coroutine
    _stub_generate("Ein überarbeiteter Entlassbericht.")
    res = await _run_repair_coroutine(
        _RepairJob(patient_name=FRAU_M), "entlassbericht", "PROMPT",
        original_text="alt", user_hint="kürzer bitte",
    )
    assert res["suizid_note_status"] is None
    assert res["text"] == "Ein überarbeiteter Entlassbericht."
