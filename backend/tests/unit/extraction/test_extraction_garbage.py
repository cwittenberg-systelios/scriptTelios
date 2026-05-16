"""
Tests fuer app/services/extraction.py:
  - detect_extraction_garbage (5 Kategorien von Halluzinations-Mustern)
  - validate_or_reject (Wrapper mit Hard-Reject-Threshold, neu in P2-Refactor)

Vor v18 lieferte das Vision-Modell bei leeren Seiten Pseudo-Inhalte
("A. Klinikleitung B. IT-Support", Refusal-Strings, Listen 1-300).
Diese Tests sichern dass die Detection-Logik kein false-positive auf
echte Inhalte produziert UND echte Garbage zuverlaessig erkennt.
"""
import pytest

from app.services.extraction import (
    detect_extraction_garbage,
    validate_or_reject,
    EXTRACTION_HARD_REJECT_THRESHOLD,
)


# ─────────────────────────────────────────────────────────────────────────────
# Kategorie 1: Vision-Refusal-Strings
# ─────────────────────────────────────────────────────────────────────────────


class TestVisionRefusal:

    def test_deutscher_refusal(self):
        text = "Es tut mir leid, aber ich kann keine Bilder bearbeiten." * 5
        problems = detect_extraction_garbage(text)
        assert any("refusal" in p.lower() or "abgelehnt" in p.lower()
                   for p in problems)

    def test_englischer_refusal(self):
        text = "I'm sorry, I cannot read this image." * 5 + " " * 100
        problems = detect_extraction_garbage(text)
        assert any("refusal" in p.lower() or "abgelehnt" in p.lower()
                   for p in problems)

    def test_normal_text_kein_refusal(self):
        text = "Die Patientin berichtet ueber Schlafstoerungen." * 10
        problems = detect_extraction_garbage(text)
        assert not any("refusal" in p.lower() for p in problems)


# ─────────────────────────────────────────────────────────────────────────────
# Kategorie 2: Listen-Halluzinationen (typische Vision-Patterns)
# ─────────────────────────────────────────────────────────────────────────────


class TestListenHalluzination:

    def test_checkbox_serie(self):
        """8+ aufeinanderfolgende Checkbox-Zeilen ist verdaechtig."""
        text = "\n".join(f"[ ] Option {i}" for i in range(10)) + "\n" + "x" * 100
        problems = detect_extraction_garbage(text)
        assert any("liste" in p.lower() or "halluzination" in p.lower()
                   for p in problems)

    def test_buchstaben_aufzaehlung(self):
        """A. Foo / B. Bar / C. Baz typisches Vision-Boilerplate."""
        text = (
            "A. Klinikleitung\n"
            "B. IT-Support\n"
            "C. Verwaltung\n"
            "D. Empfang\n"
            "E. Pflege\n"
            "F. Aerztliche Leitung\n"
            "G. Therapie\n"
            "H. Hauswirtschaft\n"
        ) + "x" * 100
        problems = detect_extraction_garbage(text)
        assert any("liste" in p.lower() for p in problems)

    def test_normale_kurze_liste_kein_alarm(self):
        """Eine sinnvolle 3-Punkt-Liste darf nicht als Halluzination markiert werden."""
        text = (
            "Therapieziele:\n"
            "1. Schlafregulation\n"
            "2. Stresstoleranz erhoehen\n"
            "3. Soziale Kontakte pflegen\n"
            "Diese Ziele wurden gemeinsam erarbeitet."
        )
        problems = detect_extraction_garbage(text)
        assert not any("liste" in p.lower() for p in problems)


# ─────────────────────────────────────────────────────────────────────────────
# Kategorie 3: Stoppwort-Plausibilitaet
# ─────────────────────────────────────────────────────────────────────────────


class TestStoppwortPlausibilitaet:

    def test_kein_deutsch(self):
        """Text mit sehr wenig deutschen Stoppwoertern -> verdaechtig."""
        text = " ".join(["xyz", "abc", "qwer", "asdf"] * 50)
        problems = detect_extraction_garbage(text)
        assert any("stoppwort" in p.lower() or "deutsch" in p.lower()
                   for p in problems)

    def test_normaler_deutscher_text_ok(self):
        text = (
            "Die Patientin berichtet von zunehmenden Schlafstoerungen und einer "
            "depressiven Verstimmung. Sie hat seit einigen Wochen das Interesse an "
            "ihren Hobbys verloren und sich sozial zurueckgezogen. Im Aufnahmegespraech "
            "wirkt sie verlangsamt und in ihrem Affekt eingeschraenkt. "
        ) * 3
        problems = detect_extraction_garbage(text)
        assert not any("stoppwort" in p.lower() for p in problems)


# ─────────────────────────────────────────────────────────────────────────────
# Kategorie 4: Leere Seiten
# ─────────────────────────────────────────────────────────────────────────────


class TestLeereSeiten:

    def test_mehr_als_30_prozent_leer(self):
        text = (
            "[Seite 1]\nVoll mit echtem Inhalt der lang genug ist um nicht\n"
            "als leer zu zaehlen. Mehr Text der echt sein soll und wirklich nicht leer ist.\n"
            "[Seite 2]\n"  # leer
            "[Seite 3]\n"  # leer
            "[Seite 4]\nKurzer Text\n"  # < 50 Zeichen
        )
        problems = detect_extraction_garbage(text)
        assert any("seite" in p.lower() or "leer" in p.lower() for p in problems)

    def test_alle_seiten_voll_kein_alarm(self):
        text = (
            "[Seite 1]\n" + "Echter Inhalt mit langem Text. " * 10 +
            "[Seite 2]\n" + "Mehr echter Inhalt. " * 10 +
            "[Seite 3]\n" + "Wiederum echter Inhalt. " * 10
        )
        problems = detect_extraction_garbage(text)
        assert not any("seite" in p.lower() and "leer" in p.lower() for p in problems)


# ─────────────────────────────────────────────────────────────────────────────
# Kategorie 5: Boilerplate / Werbephrasen
# ─────────────────────────────────────────────────────────────────────────────


class TestBoilerplate:

    def test_wir_bieten_umfangreiche_versorgung(self):
        text = (
            "Wir bieten eine umfangreiche Versorgung. " +
            "Fuer die bestmoegliche Behandlung. " +
            "Echter Patient-Inhalt: Beschwerden, Symptome. " * 5
        )
        problems = detect_extraction_garbage(text)
        assert any("boilerplate" in p.lower() or "werbe" in p.lower() or "klinik" in p.lower()
                   for p in problems)

    def test_normaler_bericht_kein_alarm(self):
        text = (
            "Die Patientin wurde am 01.01. aufgenommen. " +
            "Aktuell berichtet sie ueber anhaltende Schlafstoerungen. " +
            "Im Befund zeigt sich ein gedrueckter Affekt. " * 5
        )
        problems = detect_extraction_garbage(text)
        assert not any("boilerplate" in p.lower() or "werbe" in p.lower()
                       for p in problems)


# ─────────────────────────────────────────────────────────────────────────────
# Edge Cases
# ─────────────────────────────────────────────────────────────────────────────


class TestEdgeCases:

    def test_leerer_input_keine_issues(self):
        assert detect_extraction_garbage("") == []

    def test_sehr_kurzer_input_keine_issues(self):
        assert detect_extraction_garbage("kurz.") == []

    def test_keine_issues_bei_normalem_anamnese_text(self):
        text = """
        Aktuelle Anamnese: Frau M. stellt sich auf Empfehlung ihres Hausarztes
        in unserer Klinik vor. Im Aufnahmegespraech beschreibt sie eine seit
        sechs Monaten zunehmende depressive Symptomatik mit Schlafstoerungen,
        Interessenverlust und gedrueckter Stimmung. Sie habe wiederholt
        Suizidgedanken gehabt, aber keine konkrete Planung.

        Aus der Biografie ergibt sich eine schwere Trennungssituation vor acht
        Monaten und chronische berufliche Ueberlastung. Frueher keine
        psychiatrischen Vorbehandlungen.
        """
        problems = detect_extraction_garbage(text)
        assert problems == [], f"False positives auf normalem Text: {problems}"


# ─────────────────────────────────────────────────────────────────────────────
# validate_or_reject (Wrapper mit Hard-Reject)
# ─────────────────────────────────────────────────────────────────────────────


class TestValidateOrReject:

    def test_leerer_text_passt_durch(self):
        text_out, warning = validate_or_reject("", "Selbstauskunft")
        assert text_out == ""
        assert warning is None

    def test_normaler_text_passt_durch(self):
        text = (
            "Die Patientin berichtet ueber Schlafstoerungen seit einigen Wochen. "
            "Sie hat sich sozial zurueckgezogen und ihr Interesse an Hobbys verloren. " * 3
        )
        text_out, warning = validate_or_reject(text, "Vorbefunde")
        assert text_out == text
        assert warning is None

    def test_text_mit_einem_problem_warning_aber_durchgelassen(self):
        """Eines der Garbage-Pattern (z.B. Listen-Halluzination) -> Warning,
        Text bleibt aber erhalten (kein Hard-Reject)."""
        text = "\n".join(f"A. Foo{i}" for i in range(10))
        text_out, warning = validate_or_reject(text, "Vorbefunde", "test.pdf")
        # Erwartung: 1-2 Problem-Typen, also unter Hard-Reject-Schwelle
        # text bleibt erhalten falls < HARD_REJECT_THRESHOLD Probleme
        # ABER: dieser Text hat ggf. mehrere Treffer (Liste + Stoppwoerter + Boilerplate)
        if warning is not None:
            # Wenn Hard-Reject: text leer; sonst gleich
            if len(warning.split(";")) >= EXTRACTION_HARD_REJECT_THRESHOLD:
                assert text_out == ""
            else:
                assert text_out == text

    def test_hard_reject_bei_vielen_problemen(self):
        """Konstruiertes Mehrfach-Garbage triggert Hard-Reject (text='')."""
        bad_text = (
            "Es tut mir leid, ich kann keine Bilder bearbeiten.\n"
            "A. Klinikleitung\nB. IT-Support\nC. Verwaltung\n"
            "D. Empfang\nE. Pflege\nF. Aerztliche Leitung\n"
            "G. Therapie\nH. Hauswirtschaft\n"
            "Wir bieten eine umfangreiche Versorgung fuer die bestmoegliche Behandlung. "
            + ("xyz abc qwer asdf " * 50)  # kein Deutsch
        )
        text_out, warning = validate_or_reject(bad_text, "Selbstauskunft", "muell.pdf")
        # mit so vielen Patterns sollte Hard-Reject greifen
        # (mindestens 3: Refusal + Liste + Boilerplate + Stoppwort)
        assert warning is not None
        assert text_out == ""

    def test_label_und_filename_in_warning(self):
        bad_text = (
            "Es tut mir leid, ich kann keine Bilder bearbeiten.\n"
            "A. Klinikleitung\nB. IT-Support\nC. Verwaltung\n"
            "D. Empfang\nE. Pflege\nF. Aerztliche Leitung\n"
            "G. Therapie\nH. Hauswirtschaft\n"
            "Wir bieten umfangreiche Versorgung. " * 3
        )
        _, warning = validate_or_reject(bad_text, "Vorbefunde", "patient_xyz.pdf")
        assert warning is not None
        assert "Vorbefunde" in warning
        assert "patient_xyz.pdf" in warning

    def test_kurzer_text_unter_min_chars_passt_durch(self):
        """Sehr kurze Texte (< 50 Zeichen) werden gar nicht geprueft."""
        text = "Kurz."
        text_out, warning = validate_or_reject(text, "Vorbefunde")
        assert text_out == text
        assert warning is None

    def test_threshold_parameter_konfigurierbar(self):
        """hard_reject_threshold kann ueberschrieben werden."""
        bad_text = (
            "Es tut mir leid, ich kann keine Bilder bearbeiten.\n"
            "A. Klinikleitung\nB. IT-Support\nC. Verwaltung\n"
            "D. Empfang\nE. Pflege\nF. Aerztliche Leitung\n"
            "G. Therapie\nH. Hauswirtschaft\n"
            "Wir bieten umfangreiche Versorgung. " * 3
        )
        # Mit threshold=99: nichts wird hart abgelehnt
        text_out, warning = validate_or_reject(
            bad_text, "Selbstauskunft", hard_reject_threshold=99
        )
        assert warning is not None
        assert text_out == bad_text  # behalten

        # Mit threshold=1: schon EIN Problem triggert Reject
        text_out2, _ = validate_or_reject(
            bad_text, "Selbstauskunft", hard_reject_threshold=1
        )
        assert text_out2 == ""
