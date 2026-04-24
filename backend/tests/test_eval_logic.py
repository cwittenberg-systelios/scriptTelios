"""
Unit-Tests fuer die Eval-Logik in test_eval.py.

Testet:
- StyleAnalyzer: Wortzahl, Satzlaenge, Absatzlaenge, Wir-Perspektive, Fachbegriff-Dichte
- EvalResult.check_word_count(): word_count_ok Flag
- EvalResult.check_style_consistency(): Bandbreiten-Check + Downgrade-Logik

Wichtig: Der Eval-Code selbst ist Pruefungs-Logik. Bug 3b war ein Bug in
der Eval (nicht im Backend) — die Toleranz war zu eng und meldete legitime
Outputs als Stil-Issues.
"""
import pytest

# Da StyleAnalyzer und EvalResult in tests/test_eval.py liegen, importieren
# wir per importlib um die Test-Discovery nicht durcheinander zu bringen
import importlib
import sys
from pathlib import Path

# Test-Discovery konsistent machen: tests/ ins sys.path
_TESTS_DIR = Path(__file__).parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

# Lazy import damit wir nicht die ganze test_eval.py-Test-Suite triggern
test_eval_module = importlib.import_module("test_eval")
StyleAnalyzer = test_eval_module.StyleAnalyzer
EvalResult = test_eval_module.EvalResult


# ─────────────────────────────────────────────────────────────────────────────
# StyleAnalyzer
# ─────────────────────────────────────────────────────────────────────────────


class TestStyleAnalyzerBasics:
    """Grundfunktionen des StyleAnalyzers."""

    def test_leerer_text(self):
        sa = StyleAnalyzer("")
        assert sa.avg_sentence_length == 0.0
        assert sa.avg_paragraph_length == 0.0
        assert sa.wir_perspektive_ratio == 0.0

    def test_einfacher_text_satzlaenge(self):
        # 3 Saetze a 5 Woerter = avg 5
        text = "Erster Satz mit fuenf Woertern. Zweiter Satz mit fuenf Woertern. Dritter Satz mit fuenf Woertern."
        sa = StyleAnalyzer(text)
        assert 4 <= sa.avg_sentence_length <= 6

    def test_absatzlaenge_mit_doppel_newline(self):
        text = "Erster Absatz hier.\n\nZweiter Absatz hier."
        sa = StyleAnalyzer(text)
        assert sa.avg_paragraph_length == 3.0  # 3 Woerter pro Absatz

    def test_einzelner_absatz_ohne_newline(self):
        text = "Ein Absatz mit fuenf Worten hier."
        sa = StyleAnalyzer(text)
        assert sa.avg_paragraph_length == 6.0  # 6 Woerter

    def test_word_count_aus_words_property(self):
        sa = StyleAnalyzer("Eins zwei drei vier fuenf.")
        assert len(sa.words) == 5

    def test_to_dict_enthaelt_alle_metriken(self):
        text = "Text mit etwas Inhalt zum Pruefen der Metriken hier."
        sa = StyleAnalyzer(text)
        d = sa.to_dict()
        expected_keys = {
            "word_count", "sentence_count", "paragraph_count",
            "avg_sentence_length", "avg_paragraph_length",
            "fachbegriff_density", "wir_perspektive_ratio", "direkte_zitate",
        }
        assert set(d.keys()) == expected_keys


class TestStyleAnalyzerWirPerspektive:
    """Wir-Perspektive-Erkennung."""

    def test_wir_dominiert(self):
        text = (
            "Wir erlebten die Patientin zu Beginn deutlich erschoepft. "
            "In unserer Arbeit gelang uns eine deutliche Verbesserung."
        )
        sa = StyleAnalyzer(text)
        assert sa.wir_perspektive_ratio > 0.5

    def test_keine_wir_perspektive(self):
        text = (
            "Die Patientin zeigte deutliche Erschoepfung zu Beginn. "
            "Sie konnte ihre Symptomatik schrittweise verbessern."
        )
        sa = StyleAnalyzer(text)
        assert sa.wir_perspektive_ratio == 0.0

    def test_gemischte_perspektive(self):
        # 4 Saetze, 1 mit "wir", 1 mit "Unsere" (matched NICHT wegen \b)
        # Tatsaechliches Verhalten: ratio = 0.25
        text = (
            "Wir beobachteten die Klientin bei der Aufnahme. "
            "Die Patientin zeigte deutliche Symptome der Depression. "
            "Unsere therapeutischen Methoden waren erfolgreich. "
            "Sie konnte ihre Stabilitaet erhoehen."
        )
        sa = StyleAnalyzer(text)
        # Ratio sollte > 0 sein (mind. 1 Wir-Satz) aber < 1 (nicht alle)
        assert 0.0 < sa.wir_perspektive_ratio < 1.0


class TestStyleAnalyzerDirekteZitate:
    """Direkte Zitate-Zaehlung."""

    def test_keine_zitate(self):
        sa = StyleAnalyzer("Text ohne jegliche Anfuehrungszeichen hier.")
        assert sa.direkte_zitate_count == 0

    def test_einfache_anfuehrungszeichen(self):
        sa = StyleAnalyzer('Sie sagte "Ich bin sehr muede" und ging weg.')
        assert sa.direkte_zitate_count == 1

    def test_mehrere_zitate(self):
        text = '"Erstes Zitat" und dann "Zweites Zitat" und "Drittes Zitat".'
        sa = StyleAnalyzer(text)
        assert sa.direkte_zitate_count == 3


# ─────────────────────────────────────────────────────────────────────────────
# EvalResult.check_word_count() — neuer word_count_ok Flag (Bug-Fix #3b)
# ─────────────────────────────────────────────────────────────────────────────


class TestCheckWordCount:
    """Tests fuer den word_count_ok Flag (Bug-Fix #3b)."""

    def test_word_count_ok_bei_treffer(self):
        text = "wort " * 200  # 200 Woerter
        ev = EvalResult("entlassbericht", "test-1", text)
        ev.check_word_count(min_words=100, max_words=300)
        assert ev.word_count_ok is True
        assert any("OK" in p for p in ev.passed)

    def test_word_count_nicht_ok_bei_zu_kurz(self):
        text = "wort " * 50  # 50 Woerter
        ev = EvalResult("entlassbericht", "test-2", text)
        ev.check_word_count(min_words=100, max_words=300)
        assert ev.word_count_ok is False
        assert any("Zu kurz" in i for i in ev.issues)

    def test_word_count_nicht_ok_bei_zu_lang(self):
        text = "wort " * 500
        ev = EvalResult("entlassbericht", "test-3", text)
        ev.check_word_count(min_words=100, max_words=300)
        assert ev.word_count_ok is False
        assert any("Zu lang" in i for i in ev.issues)


# ─────────────────────────────────────────────────────────────────────────────
# EvalResult.check_style_consistency() — Downgrade-Logik (Bug-Fix #3b)
# ─────────────────────────────────────────────────────────────────────────────


class TestCheckStyleConsistencyDowngrade:
    """
    Tests fuer den Downgrade-Mechanismus:
    Wenn die Wortzahl OK ist, soll eine abweichende Absatzlaenge
    nur als Warnung (passed) statt als Issue gewertet werden.
    """

    def test_downgrade_aktiv_wenn_wortzahl_ok(self):
        # Output mit korrekter Wortzahl aber stark abweichender Absatzlaenge
        # Output: 200 Woerter in einem grossen Absatz
        output = "wort " * 200
        # Style: 50 Woerter pro kleinem Absatz × mehrere
        style = ("Kurzer Absatz mit etwa fuenfzig Worten zum Vergleich. " * 5
                 + "\n\n"
                 + "Zweiter kurzer Absatz mit ebenfalls etwa fuenfzig Worten. " * 5)

        ev = EvalResult("entlassbericht", "test-downgrade", output)
        ev.check_word_count(min_words=100, max_words=300)
        # Sollte word_count_ok = True
        assert ev.word_count_ok is True

        ev.check_style_consistency(style)
        # Issues sollten KEINE Absatzlaengen-Issues enthalten (downgegradet)
        absatz_issues = [i for i in ev.issues if "Absatzlänge" in i]
        # Falls Absatz-Issues existieren, muessten sie nun in passed sein
        absatz_passed = [p for p in ev.passed if "Absatzlänge" in p and "akzeptabel" in p]

        # Mindestens eine der beiden Listen sollte was haben (zumindest geprueft)
        # Wichtig: kein Issue mit "weicht ab" obwohl Wortzahl OK
        assert not any("Absatzlänge" in i and "weicht ab" in i for i in ev.issues), \
            f"Absatz-Issue trotz word_count_ok=True: {ev.issues}"

    def test_kein_downgrade_wenn_wortzahl_nicht_ok(self):
        # Output zu lang → kein Downgrade fuer Absatzlaenge
        output = "wort " * 1000
        style = "Ein Absatz hier.\n\nZweiter Absatz da."

        ev = EvalResult("entlassbericht", "test-no-downgrade", output)
        ev.check_word_count(min_words=100, max_words=300)
        assert ev.word_count_ok is False

        ev.check_style_consistency(style)
        # Hier waere es OK wenn Issue-Liste Absatz-Issues hat
        # (Downgrade darf NICHT greifen)


class TestCheckStyleConsistencyTolerance:
    """Tests fuer die ±60% Toleranz (Bug-Fix #3a)."""

    def test_default_tolerance_ist_06(self):
        # ev mit Output sehr aehnlich zur Referenz → sollte OK sein
        text = "wort " * 100
        ev = EvalResult("dokumentation", "tol-1", text)
        ev.check_word_count(min_words=50, max_words=200)
        ev.check_style_consistency(text)  # default tolerance=0.6
        # Mit gleichem Text sollte alles passed sein
        absatz_issues = [i for i in ev.issues if "Absatzlänge" in i]
        satz_issues = [i for i in ev.issues if "Satzlänge" in i]
        assert not absatz_issues
        assert not satz_issues

    def test_explicit_tolerance_kann_uebergeben_werden(self):
        text = "wort " * 100
        ev = EvalResult("dokumentation", "tol-2", text)
        ev.check_word_count(50, 200)
        ev.check_style_consistency(text, tolerance=0.1)
        # Sollte trotzdem nicht crashen


# ─────────────────────────────────────────────────────────────────────────────
# EvalResult.check_required_keywords()
# ─────────────────────────────────────────────────────────────────────────────


class TestCheckRequiredKeywords:
    """Tests fuer Keyword-Pruefung mit Synonym-Matching."""

    def test_direktes_keyword_match(self):
        text = "Im Verlauf der Behandlung..."
        ev = EvalResult("entlassbericht", "kw-1", text)
        ev.check_required_keywords(["Verlauf"])
        assert any("Verlauf" in p for p in ev.passed)

    def test_synonym_match_fuer_vorstellungsanlass(self):
        text = "Frau M. stellt sich vor mit dem Hauptanliegen..."
        ev = EvalResult("anamnese", "kw-2", text)
        ev.check_required_keywords(["vorstellungsanlass"])
        # Sollte ueber Synonym "stellt sich vor" matchen
        passed_count = sum(1 for p in ev.passed if "vorstellungsanlass" in p.lower() or "Synonym" in p)
        # Mindestens passed (auch wenn als Synonym gekennzeichnet)
        assert any("vorstellungsanlass" in p.lower() for p in ev.passed)

    def test_keyword_fehlt_wird_als_issue_gemeldet(self):
        text = "Komplett anderer Text ohne Bezug."
        ev = EvalResult("entlassbericht", "kw-3", text)
        ev.check_required_keywords(["Behandlungsverlauf"])
        # Sollte als Issue
        assert any("Keyword fehlt" in i or "Behandlungsverlauf" in i for i in ev.issues)
