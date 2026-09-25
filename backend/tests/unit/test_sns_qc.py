"""Tests QC-Regelkatalog SNS-Verlauf (v19.41, S4): jede Regel positiv/negativ + Dispatch."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import sns_llm as sl
from app.services import sns_qc as sq
from app.services import sns_verlauf as sv
from app.services.quality_check import run_quality_check, serialize_issues

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "sns_verlauf"


@pytest.fixture(scope="module")
def basis():
    t = {n: (FIX / f).read_text(encoding="utf-8") for n, f in
         (("hsf", "hsf.csv"), ("ind", "individuell.csv"), ("xml", "individuell.xml"))}
    a = sv.analyse_userexport((FIX / "userexport.xlsx").read_bytes(), t["xml"])
    entries, namen = sl.pseudonymisiere(sv.diary_entries(a))
    stage_a = [{"datum": "2026-03-15", "tenor": "gemischt", "verworfen": 0, "ereignisse": [
        {"datum": "2026-03-15", "kategorie": "autonomie_erfahrung", "ism_faktor": 0, "kurz": "Nein gesagt",
         "zitat": "ich habe zum ersten Mal Nein gesagt"}]}]
    flags = sl.flags_nach_stage_a(a, stage_a, entries)
    fb = sl.build_faktenblock(a.fakten, stage_a, flags, "Klientin", "K.")
    k = a.fakten["hsf"]["komposit"]
    text = "\n\n".join([
        "1. Zusammenfassung\nDie Klientin (K.) zeigt am 19.03.2026 einen Ordnungsübergang; der Komposit stieg von "
        f"{str(k['anfang']).replace('.', ',')} auf {str(k['ende']).replace('.', ',')}.",
        "2. Fragebögen und Faktorstruktur\nDer Bogen bildet fünf Faktoren ab, Faktor VI ist unbesetzt.",
        "3. Verlauf der Kernfaktoren\nText.",
        "4. Individueller Fragebogen: Zielerleben und ISM-Faktoren\nText.",
        "5. Dynamische Komplexität und kritische Instabilität\nText.",
        "6. Rekurrenzmuster\nText.",
        "7. Phasen\nP1 – Ankommen: erste Tage.",
        "8. Anfang und Ende\nText.",
        "9. Einordnung und Hinweise für Entlassung und Nachsorge\nSie sagte: „ich habe zum ersten Mal Nein gesagt“.",
    ])
    return {"text": text, "faktenblock": fb, "fakten": a.fakten, "stage_a": stage_a, "flags": flags["flags"],
            "namen": ["Jonas", "Lea", "Mira"], "quelle": entries}


def _codes(res):
    return [i.code for i in sq.sns_issues_for_result(res)]


def test_sauberer_text_ohne_critical(basis):
    issues = sq.sns_issues_for_result(basis)
    assert not [i for i in issues if i.severity == "critical"], [i.message for i in issues]


def test_zahl_nicht_in_fakten(basis):
    res = {**basis, "text": basis["text"].replace("Text.", "Der Wert lag bei 77,7 Punkten.", 1)}
    assert "SNS_ZAHL_NICHT_IN_FAKTEN" in _codes(res)
    res = {**basis, "text": basis["text"].replace("Text.", "Am 01.01.2026 passierte etwas.", 1)}
    assert "SNS_ZAHL_NICHT_IN_FAKTEN" in _codes(res)


def test_zitat_und_name(basis):
    res = {**basis, "text": basis["text"] + ' Sie schrieb: „das habe ich nie so gesagt“.'}
    assert "SNS_ZITAT_NICHT_IN_QUELLE" in _codes(res)
    res = {**basis, "text": basis["text"] + " Gespräch mit Jonas."}
    assert "SNS_NAME_LEAK" in _codes(res)


def test_uebergang_fehlt(basis):
    res = {**basis, "text": basis["text"].replace("19.03.2026", "an diesem Tag")}
    assert "SNS_UEBERGANG_FEHLT" in _codes(res)


def test_ism_faktor_falsch(basis):
    res = {**basis, "text": basis["text"] + " Das Item „Heute konnte ich meine Ruhe in der Natur als Kraftquelle nutzen“ "
                                            "gehört zu Faktor IV und stieg."}
    assert "SNS_ISM_FAKTOR_FALSCH" in _codes(res)
    res = {**basis, "text": basis["text"] + " Das Item „Heute konnte ich meine Ruhe in der Natur als Kraftquelle nutzen“ "
                                            "gehört zu Faktor II und stieg."}
    assert "SNS_ISM_FAKTOR_FALSCH" not in _codes(res)


def test_flag_regeln(basis):
    res = {**basis, "text": basis["text"].replace("Faktor VI ist unbesetzt", "alles gut"),
           "flags": basis["flags"] + ["MEDIKATION_IM_UEBERGANGSFENSTER", "SOMATIK_NEU", "DECKENEFFEKT_ENDE",
                                      "SUIZIDALITAET_IN_QUELLE"]}
    c = _codes(res)
    for code in ("SNS_ISM_UNBESETZT_FEHLT", "SNS_MEDIKATION_FEHLT", "SNS_SOMATIK_FEHLT",
                 "SNS_DECKENEFFEKT_FEHLT", "SNS_SUIZIDALITAET_FEHLT"):
        assert code in c
    res["text"] += " Die Medikation wurde angepasst, Kopfschmerzen sind abzuklären, Werte am Skalenende; " \
                   "das Tagebuch erwähnt Suizidgedanken, unbesetzt bleibt VI."
    c = _codes(res)
    assert not any(x in c for x in ("SNS_ISM_UNBESETZT_FEHLT", "SNS_MEDIKATION_FEHLT", "SNS_SOMATIK_FEHLT",
                                    "SNS_DECKENEFFEKT_FEHLT", "SNS_SUIZIDALITAET_FEHLT"))


def test_abschnitt_aufzaehlung_hypno(basis):
    res = {**basis, "text": basis["text"].replace("6. Rekurrenzmuster\nText.", "") + "\n- ein Punkt\n- noch einer\n"
                                                                                       "Das war hypnosystemisch."}
    c = _codes(res)
    assert "SNS_ABSCHNITT_FEHLT" in c and "SNS_AUFZAEHLUNG" in c and "HYPNOSYSTEMISCH" in c


def test_polung_fraglich_und_korrektur(basis):
    fakten = json.loads(json.dumps(basis["fakten"]))
    fakten["polung_check"][0]["fraglich"] = True
    c = _codes({**basis, "fakten": fakten, "flags": basis["flags"] + ["POLUNG_KORRIGIERT"]})
    assert "SNS_POLUNG_FRAGLICH" in c and "SNS_POLUNG_KORREKTUR_FEHLT" in c
    res = {**basis, "flags": basis["flags"] + ["POLUNG_KORRIGIERT"],
           "text": basis["text"] + " Ein Item wurde automatisch umgepolt."}
    assert "SNS_POLUNG_KORREKTUR_FEHLT" not in _codes(res)


def test_stage_a_regeln(basis):
    assert "SNS_STAGE_A_LEER" in _codes({**basis, "stage_a": []})
    sa = [{**basis["stage_a"][0], "verworfen": 2}]
    assert "SNS_STAGE_A_VERWORFEN" in _codes({**basis, "stage_a": sa})


def test_dispatch_und_serialize(basis):
    issues = run_quality_check(json.dumps(basis, ensure_ascii=False), "sns_verlauf")
    ser = serialize_issues(issues, workflow="sns_verlauf")
    assert ser["summary"]["checks_run"] == sq.SNS_CHECKS_RUN == 16
    assert ser["summary"]["critical"] == 0
    bad = run_quality_check("kein json", "sns_verlauf")
    assert bad[0].code == "SNS_ERGEBNIS_UNLESBAR"
