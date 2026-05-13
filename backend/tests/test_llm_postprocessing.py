"""
Unit-Tests fuer Post-Processing-Funktionen in llm.py.

Testet:
- truncate_style_context(): Schneiden auf MAX_STYLE_CONTEXT_CHARS
- deduplicate_paragraphs(): Wiederholungs-Erkennung
- clean_verlauf_text(): Bereinigung von PDF-Header, Teilnahme-Eintraegen etc.

Diese Tests sind reine Funktion-Tests ohne LLM/DB. Laufen in <1 Sek.
"""
import pytest

from app.services.llm import (
    truncate_style_context,
    deduplicate_paragraphs,
    clean_verlauf_text,
)


# ─────────────────────────────────────────────────────────────────────────────
# truncate_style_context()
# ─────────────────────────────────────────────────────────────────────────────


class TestTruncateStyleContext:
    """Tests fuer das Kuerzen von Stilvorlagen."""

    def test_kurzer_text_bleibt_unveraendert(self):
        text = "Ein kurzer Beispieltext ohne Probleme."
        assert truncate_style_context(text) == text

    def test_langer_text_wird_gekuerzt(self):
        # Sehr langer Text der weit ueber MAX_STYLE_CONTEXT_CHARS ist
        text = "Lorem ipsum dolor sit amet. " * 5000
        result = truncate_style_context(text)
        assert len(result) < len(text)

    def test_schneidet_an_satzgrenze(self):
        # Text der so lang ist dass er gekuerzt wird
        text = ("Dies ist ein Satz. " * 1000)  # ca. 19000 Zeichen
        result = truncate_style_context(text)
        # Sollte auf "." enden (Satzgrenze)
        assert result.endswith("."), f"Result endet mit {result[-10:]!r}"


# ─────────────────────────────────────────────────────────────────────────────
# deduplicate_paragraphs()
# ─────────────────────────────────────────────────────────────────────────────


class TestDeduplicateParagraphs:
    """Tests fuer das Entfernen wiederholter Absaetze."""

    def test_keine_duplikate_unveraendert(self):
        text = "Erster Absatz.\n\nZweiter Absatz.\n\nDritter Absatz."
        result = deduplicate_paragraphs(text)
        assert "Erster Absatz" in result
        assert "Zweiter Absatz" in result
        assert "Dritter Absatz" in result

    def test_duplikate_werden_entfernt(self):
        text = (
            "Identischer Absatz mit etwas Inhalt.\n\n"
            "Identischer Absatz mit etwas Inhalt.\n\n"
            "Identischer Absatz mit etwas Inhalt."
        )
        result = deduplicate_paragraphs(text)
        # Sollte nur einmal vorkommen
        assert result.count("Identischer Absatz") == 1

    def test_normalisierter_vergleich(self):
        # Verschiedene Whitespaces/Casing → trotzdem als Duplikat erkannt
        text = (
            "Text mit vielen Worten und etwas Inhalt zum Pruefen.\n\n"
            "TEXT MIT VIELEN WORTEN UND ETWAS INHALT ZUM PRUEFEN.\n\n"
            "Text   mit   vielen   Worten   und   etwas   Inhalt   zum   Pruefen."
        )
        result = deduplicate_paragraphs(text)
        # Wenn der Normalizer korrekt arbeitet → nur 1 Vorkommen
        # (case + whitespace insensitiv)
        assert len([p for p in result.split("\n\n") if p.strip()]) == 1

    def test_aehnliche_aber_nicht_identische_absaetze_bleiben(self):
        text = (
            "Frau M. zeigt deutliche Erschoepfung mit Schlafstoerungen.\n\n"
            "Frau M. zeigt deutliche Antriebslosigkeit und Konzentrationsprobleme."
        )
        result = deduplicate_paragraphs(text)
        # Beide bleiben weil unterschiedlicher Inhalt
        assert "Schlafstoerungen" in result
        assert "Konzentrationsprobleme" in result

    def test_leerer_text_kein_crash(self):
        result = deduplicate_paragraphs("")
        assert result == ""

    def test_nur_whitespace_kein_crash(self):
        result = deduplicate_paragraphs("\n\n\n   \n\n")
        # Sollte leer oder nur whitespace zurueckgeben
        assert not result.strip()


# ─────────────────────────────────────────────────────────────────────────────
# clean_verlauf_text() — die meisten Bug-relevanten Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestCleanVerlaufTextHeaders:
    """PDF-Seitenheader-Erkennung."""

    def test_seite_x_von_y_wird_entfernt(self):
        text = (
            "Wichtiger therapeutischer Inhalt der erhalten bleiben soll.\n"
            "Seite 5 von 12\n"
            "Weitere wichtige Information."
        )
        result = clean_verlauf_text(text)
        assert "Seite 5 von 12" not in result
        assert "Wichtiger therapeutischer Inhalt" in result

    def test_verlaufsdokumentation_stand_header_wird_entfernt(self):
        text = (
            "Therapeutische Notiz mit Inhalt.\n"
            "Verlaufsdokumentation - Stand: 24.04.2026\n"
            "Weitere Notiz."
        )
        result = clean_verlauf_text(text)
        assert "Stand:" not in result

    def test_a_nummer_zimmer_header_wird_entfernt(self):
        text = (
            "Wichtige Notiz.\n"
            "(A12345) Zi. 234\n"
            "Weitere Information."
        )
        result = clean_verlauf_text(text)
        assert "Zi. 234" not in result


class TestCleanVerlaufTextParticipation:
    """Inhaltsleere Teilnahme-Eintraege werden entfernt."""

    def test_hat_teilgenommen_alleine_wird_entfernt(self):
        text = "Therapeutische Sitzung beschrieben.\nhat teilgenommen\nNaechste Notiz."
        result = clean_verlauf_text(text)
        assert "hat teilgenommen" not in result
        assert "Therapeutische Sitzung" in result

    def test_entschuldigt_alleine_wird_entfernt(self):
        text = "Vor-Notiz.\nentschuldigt\nNach-Notiz."
        result = clean_verlauf_text(text)
        # "entschuldigt" als Solo-Zeile sollte raus
        assert "Vor-Notiz" in result
        assert "Nach-Notiz" in result

    def test_inhaltliche_zeile_mit_teilgenommen_bleibt(self):
        # "hat teilgenommen mit produktiver Mitarbeit" → bleibt erhalten
        text = "Frau M. hat teilgenommen mit produktiver Mitarbeit und konkretem Beitrag."
        result = clean_verlauf_text(text)
        assert "produktiver Mitarbeit" in result


class TestCleanVerlaufTextAdmin:
    """Administrative Zeilen werden entfernt."""

    def test_termin_planung_wird_entfernt(self):
        text = (
            "Therapeutischer Hauptinhalt der Sitzung.\n"
            "Termin am 25.04.2026 um 10:00\n"
            "Fortsetzung der Notiz."
        )
        result = clean_verlauf_text(text)
        assert "Termin am 25.04" not in result

    def test_raum_eintrag_wird_entfernt(self):
        text = "Inhalt.\nRaum 234\nWeiterer Inhalt."
        result = clean_verlauf_text(text)
        assert "Raum 234" not in result
        assert "Inhalt." in result

    def test_au_bescheinigung_wird_entfernt(self):
        text = "Notiz.\nAU-Bescheinigung bis 30.04.2026\nWeiter."
        result = clean_verlauf_text(text)
        assert "AU-Bescheinigung" not in result


class TestCleanVerlaufTextLeerzeilen:
    """Komprimierung wiederholter Leerzeilen."""

    def test_mehrfache_leerzeilen_werden_komprimiert(self):
        text = "Erster Block.\n\n\n\n\nZweiter Block."
        result = clean_verlauf_text(text)
        # Sollte nicht mehr als 2 Newlines hintereinander haben
        assert "\n\n\n" not in result


class TestCleanVerlaufTextEdgeCases:
    """Edge Cases."""

    def test_leerer_text_ergibt_leerstring(self):
        assert clean_verlauf_text("") == ""

    def test_nur_whitespace(self):
        result = clean_verlauf_text("   \n\n   ")
        assert not result.strip()

    def test_text_ohne_zu_filternde_inhalte_bleibt_erhalten(self):
        text = (
            "Im Einzelgespraech mit der Patientin wurde die Anteilearbeit "
            "fortgesetzt. Der Manager-Anteil zeigte sich kooperativ und "
            "konnte erste Aufgaben abgeben. Die Klientin reflektierte "
            "ausfuehrlich ueber ihre Beziehungsmuster."
        )
        result = clean_verlauf_text(text)
        # Substanzieller Inhalt bleibt erhalten
        assert "Anteilearbeit" in result
        assert "Manager-Anteil" in result
        assert "Beziehungsmuster" in result


# ─────────────────────────────────────────────────────────────────────────────
# v19.2 Schritt 0: Erweiterte clean_verlauf_text-Tests
# Neue PDF-Layouts (Klebebugs, ---Seite N---, Sitzungs-Header-Typen).
# Die alten Tests oben bleiben unveraendert gruen — diese Tests pruefen
# zusaetzliche Patterns.
# ─────────────────────────────────────────────────────────────────────────────


class TestCleanVerlaufV192Klebebug:
    """v19.2: OCR-Klebebugs in Headern werden global repariert."""

    def test_klebebug_im_sitzungsheader_wird_repariert(self):
        """Sitzungstyp + Zeit ohne Leerzeichen → mit Leerzeichen + Inhalt → Header bleibt."""
        text = (
            "Aufwecken, Anregen09:30 - 11:10\n"
            "Patientin berichtet stabile Stimmung.\n"
        )
        result = clean_verlauf_text(text)
        # Klebebug repariert
        assert "Anregen 09:30" in result
        assert "Anregen09:30" not in result

    def test_klebebug_mit_therapeut_klammer(self):
        """Klebebug nach schliessender Klammer: 'Wolf)11:45' → 'Wolf) 11:45'."""
        text = (
            "Einzelgespräch (J.Wolf)11:00 - 11:50\n"
            "Inhaltlicher Text der Sitzung.\n"
        )
        result = clean_verlauf_text(text)
        assert "Wolf) 11:00" in result
        assert "Wolf)11:00" not in result


class TestCleanVerlaufV192SeitenMarker:
    """v19.2: ---Seite N--- und [Pseudonymisiertes ...] werden entfernt."""

    def test_seite_marker_entfernt(self):
        text = (
            "--- Seite 1 ---\n"
            "10.03.2026\n"
            "Echter Sitzungsinhalt der bleiben muss.\n"
            "--- Seite 2 ---\n"
        )
        result = clean_verlauf_text(text)
        assert "--- Seite" not in result
        assert "Echter Sitzungsinhalt" in result

    def test_pseudonymisiert_marker_entfernt(self):
        text = (
            "[Pseudonymisiertes Dokument – Original-Layout nicht erhalten]\n"
            "Inhalt der bleibt.\n"
        )
        result = clean_verlauf_text(text)
        assert "Pseudonymisiertes Dokument" not in result
        assert "Inhalt der bleibt" in result


class TestCleanVerlaufV192LeereHeader:
    """v19.2: Sitzungs-Header ohne folgenden Inhalt werden entfernt."""

    def test_leerer_header_vor_naechstem_header_entfernt(self):
        """Aufwecken hat KEINEN Inhalt darunter (direkt naechster Header) → weg."""
        text = (
            "10.03.2026\n"
            "Aufwecken, Anregen 09:30 - 11:10\n"
            "Bahnen, Verankern 11:50 - 12:50\n"
            "Patientin reflektiert ueber Therapieprozess.\n"
        )
        result = clean_verlauf_text(text)
        assert "Aufwecken, Anregen" not in result
        # Bahnen hat Inhalt → bleibt
        assert "Bahnen, Verankern" in result
        assert "Therapieprozess" in result

    def test_leerer_header_vor_naechstem_datum_entfernt(self):
        """Beobachten hat KEINEN Inhalt darunter (Datum folgt) → weg."""
        text = (
            "10.03.2026\n"
            "Einzelgespräch (J.Wolf) 11:00 - 11:50\n"
            "Sitzungsinhalt am 10.03.\n"
            "Beobachten, Integrieren 14:00 - 14:50\n"
            "11.03.2026\n"
            "Bahnen, Verankern 09:00 - 10:00\n"
            "Sitzungsinhalt am 11.03.\n"
        )
        result = clean_verlauf_text(text)
        assert "Beobachten, Integrieren" not in result
        # Beide Tage mit Inhalt bleiben
        assert "Sitzungsinhalt am 10.03" in result
        assert "Sitzungsinhalt am 11.03" in result

    def test_header_mit_inhalt_bleibt(self):
        """Header gefolgt von echtem Inhalt bleibt erhalten."""
        text = (
            "Aufwecken, Anregen 09:30 - 11:10\n"
            "Patientin berichtet stabile Stimmung, gute Schlafqualität.\n"
            "Beobachten, Integrieren 14:00 - 14:50\n"
            "Weitere therapeutische Arbeit.\n"
        )
        result = clean_verlauf_text(text)
        # Beide Header haben Inhalt → bleiben beide drin
        assert "Aufwecken, Anregen" in result
        assert "Beobachten, Integrieren" in result
        assert "Schlafqualität" in result


class TestCleanVerlaufV192Datum:
    """v19.2: Datums-Zeilen werden zu Tagestrennern normalisiert."""

    def test_datum_wird_zu_tagestrenner(self):
        text = (
            "10.03.2026\n"
            "Einzelgespräch (J.Wolf) 11:00 - 11:50\n"
            "Inhalt der Sitzung.\n"
        )
        result = clean_verlauf_text(text)
        assert "### 10.03.2026" in result

    def test_doppel_datum_an_seitengrenze_dedupliziert(self):
        """Datum das durch Seitenumbruch unmittelbar wiederholt wird: nur einmal behalten.

        Realistischer Fall: PDF zeigt am Seiten-Ende das Datum als Footer und
        am Seiten-Anfang derselben Folgeseite nochmal als Header — ohne dass
        dazwischen inhaltliche Sätze liegen.
        """
        text = (
            "10.03.2026\n"
            "Einzelgespräch (J.Wolf) 11:00 - 11:50\n"
            "Inhalt der Sitzung.\n"
            "--- Seite 2 ---\n"
            "10.03.2026\n"
            "Bahnen, Verankern 13:00 - 14:00\n"
            "Fortsetzung am gleichen Tag.\n"
        )
        result = clean_verlauf_text(text)
        # 10.03 als Trenner nur einmal (zweites Vorkommen ist redundant — selber Tag)
        assert result.count("### 10.03.2026") == 1, result
        # Echter Inhalt beider Sitzungen bleibt
        assert "Inhalt der Sitzung" in result
        assert "Fortsetzung am gleichen Tag" in result

    def test_zwei_verschiedene_daten_beide_drin(self):
        text = (
            "10.03.2026\n"
            "Inhalt Tag 1.\n"
            "11.03.2026\n"
            "Inhalt Tag 2.\n"
        )
        result = clean_verlauf_text(text)
        assert "### 10.03.2026" in result
        assert "### 11.03.2026" in result


class TestCleanVerlaufV192RealesFormat:
    """v19.2: Smoke-Test mit realem PDF-Format (so wie es von der Extraktion kommt)."""

    def test_typisches_realistisches_verlauf_fragment(self):
        text = (
            "[Pseudonymisiertes Dokument – Original-Layout nicht erhalten]\n"
            "--- Seite 1 ---\n"
            "10.03.2026\n"
            "Aufwecken, Anregen09:30 - 11:10\n"
            "Abschlusskontakt (J.Wolf)11:45 - 11:55\n"
            "Frau v.M. zeigt sich stabil, aufgehellt, schwingungsfähig im Kontakt.\n"
            "Bahnen, Verankern11:50 - 12:50\n"
            "Beobachten, Integrieren14:00 - 14:50\n"
            "09.03.2026\n"
            "Aufwecken, Anregen08:50 - 10:30\n"
            "Bilanz-Trance zum Aufenthalt.\n"
        )
        orig_words = len(text.split())
        result = clean_verlauf_text(text)
        new_words = len(result.split())

        # Marker raus
        assert "--- Seite" not in result
        assert "Pseudonymisiertes" not in result
        # Klebebugs repariert
        assert "Anregen09:30" not in result
        assert "Wolf)11:45" not in result
        # Datums-Trenner
        assert "### 10.03.2026" in result
        assert "### 09.03.2026" in result
        # Header MIT Inhalt bleiben drin
        assert "Abschlusskontakt" in result
        assert "Aufwecken, Anregen 08:50" in result  # zweiter Tag - hat Inhalt
        # Header OHNE Inhalt sind raus
        assert "Bahnen, Verankern" not in result
        assert "Beobachten, Integrieren" not in result
        # Echte Sitzungsinhalte bleiben unveraendert
        assert "schwingungsfähig" in result
        assert "Bilanz-Trance" in result
        # Substanzielle Reduktion
        reduction = 1 - new_words / orig_words
        assert reduction > 0.15, f"Erwartet >15%, bekommen: {reduction:.1%}"
