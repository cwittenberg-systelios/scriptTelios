"""v19.28 (D8): Primer-Guard - gemma setzt den Assistant-Prefill nicht fort.

S0-Lauf 2026-09-22: 2 von 4 Entlassberichten begannen mit
"Zu Beginn des stationären AufenthaltsWir erlebten ..." bzw.
"Zu Beginn des stationären AufenthaltsZu Beginn seines ...".
"""
from app.services.llm import _postprocess_text

PRIMER = "Zu Beginn des stationären Aufenthalts"


def _run(text, primer=PRIMER):
    return _postprocess_text(text, primer, "entlassbericht", None, None)


def test_neuer_satz_grossbuchstabe_kein_primer():
    out = _run("Wir erlebten Frau M. zu Beginn des stationären Aufenthaltes erschöpft. Sie kam mit dem Wunsch nach Ruhe.")
    assert out.startswith("Wir erlebten Frau M.")
    assert "AufenthaltsWir" not in out


def test_primer_variante_kein_doppel():
    out = _run("Zu Beginn seines stationären Aufenthaltes formulierte Herr R. als zentrales Anliegen, Ruhe zu finden.")
    assert out.startswith("Zu Beginn seines stationären Aufenthaltes")
    assert out.count("Zu Beginn") == 1


def test_echte_fortsetzung_bekommt_primer():
    out = _run(" formulierte Frau M. als zentrales Anliegen, wieder Halt zu finden.")
    assert out.startswith("Zu Beginn des stationären Aufenthalts formulierte Frau M.")


def test_echo_weiterhin_erkannt():
    out = _run("Zu Beginn des stationären Aufenthalts formulierte Frau M. ihr Anliegen klar.")
    assert out.count("Zu Beginn des stationären Aufenthalts") == 1


def test_primer_mit_whitespace_ende_unveraendert():
    # "Auftragsklärung\n\n" endet mit Whitespace -> Grossbuchstabe danach ist normal
    out = _postprocess_text("Frau M. kam mit dem Anliegen, ihre Angst zu verstehen.", "Auftragsklärung\n\n", "dokumentation", None, None)
    assert out.startswith("Auftragsklärung")
    assert "Frau M. kam" in out
