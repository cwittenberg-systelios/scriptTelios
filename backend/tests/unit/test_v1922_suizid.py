"""
v19.22 / S1 - Pflicht-Hinweis Suizidalitaet: Erkennung + Standardsatz.

Getestet wird ausschliesslich app/services/suizidalitaet.py. Das Modul ist
in S1 noch nirgends verdrahtet - Produktionsverhalten bleibt unveraendert
(siehe test_modul_ist_in_s1_noch_nicht_verdrahtet).
"""
from __future__ import annotations

import pytest

from app.services.suizidalitaet import (
    FALLBACK_RE,
    STATUS_APPENDED,
    STATUS_NO_NAME,
    STATUS_PRESENT,
    STATUS_SOURCE_CONFLICT,
    build_suizid_fallback,
    mentions_nssv,
    mentions_suizidalitaet,
    patient_reference,
    resolve_suizid_note,
)


FRAU_M = {"anrede": "Frau", "vorname": "Maria", "nachname": "Mueller", "initial": "M."}
HERR_S = {"anrede": "Herr", "vorname": "Stefan", "nachname": "Schmitt", "initial": "S."}


# ── Marker-Erkennung ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Frau M. berichtete von Suizidgedanken in der vergangenen Woche.",
    "Im Gespräch zeigte sie sich klar von akuter Suizidalität distanziert.",
    "Herr S. beschrieb sich als lebensmüde.",
    "Er schilderte, zeitweise lebensmued gewesen zu sein.",
    "Themen waren Lebensüberdruss und Rückzug.",
    "Sie äußerte einen Todeswunsch.",
    "Es wurde ein Schutzvertrag vereinbart.",
    "Frau M. ist glaubhaft absprachefähig.",
    "Herr S. ist absprachefaehig.",
    "Sie berichtete, zeitweise nicht mehr leben zu wollen.",
    "Der Gedanke, sich das Leben zu nehmen, sei aufgetaucht.",
    "Der Wunsch, aus dem Leben zu scheiden, wurde benannt.",
    "Sie habe daran gedacht, sich etwas anzutun.",
    "Ein parasuizidales Verhalten wurde nicht berichtet.",
])
def test_marker_treffer(text):
    assert mentions_suizidalitaet(text) is True


@pytest.mark.parametrize("text", [
    "",
    "Frau M. kam mit dem Anliegen, ihren Umgang mit Stress zu verändern.",
    # Trauerkontext - 'Tod'/'Sterben' sind bewusst KEINE Marker
    "Sie sprach über den Tod ihrer Mutter und das Sterben auf der Palliativstation.",
    "Es wurde ein Krisenplan für dissoziative Zustände besprochen.",
    "Herr S. berichtete von Todesangst bei Panikattacken.",
])
def test_marker_fehltreffer(text):
    assert mentions_suizidalitaet(text) is False


def test_none_ist_kein_treffer():
    assert mentions_suizidalitaet(None) is False


# ── D5=A: NSSV zaehlt NICHT ───────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Sie berichtete von selbstverletzendem Verhalten.",
    "Das Ritzen habe im letzten Jahr aufgehört.",
    "NSSV wurde thematisiert.",
])
def test_nssv_erfuellt_die_anforderung_nicht(text):
    assert mentions_nssv(text) is True
    assert mentions_suizidalitaet(text) is False
    _, status = resolve_suizid_note(text, patient_name=FRAU_M)
    assert status == STATUS_APPENDED


# ── Referenzform / Standardsatz ───────────────────────────────────────────────

def test_referenzform_frau_und_herr():
    assert patient_reference(FRAU_M) == "Frau M."
    assert patient_reference(HERR_S) == "Herr S."


def test_referenzform_normalisiert_kuerzel_ohne_punkt():
    assert patient_reference({"anrede": "Frau", "initial": "m"}) == "Frau M."


def test_referenzform_ohne_anrede():
    assert patient_reference({"anrede": "", "initial": "M."}) == "M."


@pytest.mark.parametrize("pn", [
    None,
    {},
    {"anrede": "Frau", "initial": ""},
    {"anrede": "Frau", "initial": None},
    # Muell-Werte, gegen die llm.substitute_patient_placeholders ebenfalls schuetzt
    {"anrede": "", "initial": "die Klientin/der Klient"},
    {"anrede": "Frau", "initial": "Mueller"},
    {"anrede": "Frau", "initial": "7"},
])
def test_referenzform_unplausibel_ist_none(pn):
    assert patient_reference(pn) is None
    assert build_suizid_fallback(pn) is None


def test_standardsatz_wortlaut():
    satz = build_suizid_fallback(FRAU_M)
    assert satz == (
        "Frau M. ist glaubhaft absprachefähig, "
        "keine Anzeichen von akuter Suizidalität."
    )
    assert FALLBACK_RE.search(satz)


# ── resolve_suizid_note ───────────────────────────────────────────────────────

def test_inhalt_vorhanden_bleibt_unveraendert():
    text = (
        "**Auftragsklärung**\nFrau M. kam mit dem Anliegen ...\n\n"
        "**Relevante Gesprächsinhalte**\nSie berichtete von Suizidgedanken, "
        "von denen sie sich glaubhaft distanzieren konnte."
    )
    out, status = resolve_suizid_note(text, patient_name=FRAU_M)
    assert status == STATUS_PRESENT
    assert out == text


def test_ergaenzung_haengt_satz_als_eigenen_absatz_an():
    text = "**Einladungen**\nEs wurde keine konkrete Einladung oder Aufgabe vereinbart."
    out, status = resolve_suizid_note(text, patient_name=FRAU_M)
    assert status == STATUS_APPENDED
    assert out.startswith(text)
    assert out.endswith(
        "\n\nFrau M. ist glaubhaft absprachefähig, "
        "keine Anzeichen von akuter Suizidalität."
    )
    # D3=A: kein Ueberschriften-Markup
    assert "**Suizidalität**" not in out


def test_ergaenzung_setzt_fehlendes_satzende():
    text = "Frau M. beschrieb ihren Alltag als sehr dicht getaktet"
    out, _ = resolve_suizid_note(text, patient_name=FRAU_M)
    assert "getaktet.\n\nFrau M. ist glaubhaft" in out


def test_ergaenzung_ist_idempotent():
    text = "**Einladungen**\nEs wurde keine konkrete Einladung vereinbart."
    once, s1 = resolve_suizid_note(text, patient_name=FRAU_M)
    twice, s2 = resolve_suizid_note(once, patient_name=FRAU_M)
    assert s1 == STATUS_APPENDED
    assert s2 == STATUS_PRESENT
    assert twice == once
    assert once.count("absprachefähig") == 1


# ── D2=B: Quelle spricht darueber, Output nicht ───────────────────────────────

def test_quellenkonflikt_ergaenzt_nicht():
    text = "**Auftragsklärung**\nIm Mittelpunkt stand der Umgang mit Erschöpfung."
    quelle = "Therapeut: Haben Sie Gedanken daran, sich das Leben zu nehmen?"
    out, status = resolve_suizid_note(text, source_text=quelle, patient_name=FRAU_M)
    assert status == STATUS_SOURCE_CONFLICT
    assert out == text
    assert "absprachefähig" not in out


def test_quelle_ohne_marker_fuehrt_zur_ergaenzung():
    text = "**Auftragsklärung**\nIm Mittelpunkt stand der Umgang mit Erschöpfung."
    quelle = "Therapeut: Wie ging es Ihnen diese Woche mit dem Schlaf?"
    _, status = resolve_suizid_note(text, source_text=quelle, patient_name=FRAU_M)
    assert status == STATUS_APPENDED


def test_output_mit_inhalt_schlaegt_quellenkonflikt():
    text = "Frau M. berichtete von Suizidgedanken und ist glaubhaft distanziert."
    quelle = "Therapeut: Denken Sie daran, sich etwas anzutun?"
    _, status = resolve_suizid_note(text, source_text=quelle, patient_name=FRAU_M)
    assert status == STATUS_PRESENT


# ── D4=C: ohne Kuerzel keine Ergaenzung ───────────────────────────────────────

def test_ohne_kuerzel_keine_ergaenzung():
    text = "**Auftragsklärung**\nIm Mittelpunkt stand der Umgang mit Erschöpfung."
    out, status = resolve_suizid_note(text, patient_name=None)
    assert status == STATUS_NO_NAME
    assert out == text


def test_leerer_output_wird_nicht_zum_reinen_hinweistext():
    out, status = resolve_suizid_note("", patient_name=FRAU_M)
    assert status == STATUS_NO_NAME
    assert out == ""


# ── S1-Abgrenzung: noch nicht verdrahtet ──────────────────────────────────────

def test_modul_ist_in_s1_noch_nicht_verdrahtet():
    """S1 liefert nur das Modul. Der Einbau in die Pipeline ist S2, die
    QC-Regeln sind S3 - bis dahin darf sich am Produktionsverhalten nichts
    aendern."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "app" / "services"
    for name in ("generation_pipeline.py", "quality_check.py"):
        assert "suizidalitaet" not in (root / name).read_text(encoding="utf-8")
