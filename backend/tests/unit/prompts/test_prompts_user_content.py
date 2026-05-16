"""
Aufbau des User-Content: Quelltexte, Wir-Perspektive, Standardformulierung, Deduplikation.

Konsolidiert aus den frueheren Versions-Dateien test_prompts_v13.py bis
test_prompts_v16.py. Tests sind unveraendert, nur nach Feature-Area
umgruppiert. Versions-Geschichte steht im Git-Log.
"""
import pytest
import re
from unittest.mock import patch, MagicMock

# ──── aus test_prompts_v14.py: TestStandardformulierungAlsBlock
class TestStandardformulierungAlsBlock:
    """Standardformulierungen werden als woertlich-zu-uebernehmender Block markiert."""

    def test_akutantrag_standardformulierung_als_block(self):
        from app.services.prompts import BASE_PROMPT_AKUTANTRAG
        # Block-Marker >>> ... <<< um die Standardformulierung
        assert ">>>" in BASE_PROMPT_AKUTANTRAG
        assert "<<<" in BASE_PROMPT_AKUTANTRAG
        # Standardformulierung muss noch enthalten sein
        assert "Folgende Krankheitssymptomatik" in BASE_PROMPT_AKUTANTRAG
        # Hinweis "wörtlich" / "WOERTLICH" / "Kopie"
        assert any(w in BASE_PROMPT_AKUTANTRAG.upper() for w in ("WOERTLICH", "WÖRTLICH", "KOPIE"))

    def test_role_preamble_warnt_vor_klebebugs(self):
        from app.services.prompts import ROLE_PREAMBLE
        # Explizite Erwaehnung der Klebebug-Beispiele
        assert "Aufenthaltszeigte" in ROLE_PREAMBLE or "Aufenthaltes zeigte" in ROLE_PREAMBLE
        # Hinweis auf Leerzeichen
        assert "Leerzeichen" in ROLE_PREAMBLE


# ── Bug 6: Wir-Perspektive-Anker im ersten Satz ──────────────────────────────

# ──── aus test_prompts_v14.py: TestWirPerspektiveErsterSatz
class TestWirPerspektiveErsterSatz:
    """BASE_PROMPTs fordern explizit dass der erste Satz mit 'Wir' beginnt."""

    def test_akutantrag_fordert_wir_anfang(self):
        from app.services.prompts import BASE_PROMPT_AKUTANTRAG
        # Hinweis auf Wir-Anfang nach Standardformulierung
        text = BASE_PROMPT_AKUTANTRAG.lower()
        assert "wir" in text
        # Konkreter Hinweis dass Wir-Konstruktion am Satzanfang stehen muss
        assert "wir nehmen" in text or "muss mit 'wir'" in text

    def test_folgeverlaengerung_fordert_wir_erstsatz(self):
        from app.services.prompts import BASE_PROMPTS
        text = BASE_PROMPTS["folgeverlaengerung"]
        # Expliziter Hinweis dass erster Satz mit Wir beginnt
        assert "erste Satz" in text or "Erster Satz" in text or "erste Wort" in text
        # Negativbeispiel "Im weiteren Verlauf" wird ausgeschlossen
        assert "Im weiteren Verlauf" in text or "nicht mit" in text.lower()


# ── Bug 7: Word-Limits-Vorrang ueber BASE_PROMPT-Mindestlaengen ──────────────

# ──── aus test_prompts_v14.py: TestPatientenspezifischeKeywords
class TestPatientenspezifischeKeywords:
    """STRUKTURELLE SCHABLONE-Block fordert explizit dass aktuelle Themen vorkommen."""

    def test_strukturelle_schablone_warnt_vor_keyword_verlust(self):
        from app.services.prompts import build_system_prompt
        p = build_system_prompt(
            workflow="entlassbericht",
            style_context="Wir nahmen Frau M. auf. Sie zeigte sich erschoepft.\n" * 50,
        )
        # Block muss patientenspezifische Themen explizit nennen
        assert "PATIENTENSPEZIFISCHE BEGRIFFE" in p or "BEGRIFFE BEIBEHALTEN" in p
        # Konkrete Beispiele wie "Trennung", "Mobbing"
        assert "Trennung" in p or "Mobbing" in p


# ── Smoke-Test: Alle Workflows bauen valide Prompts ───────────────────────────

# ──── aus test_prompts_v15.py: TestTranscriptDeduplikation
class TestTranscriptDeduplikation:
    """Quelltexte werden vor dem Bau des User-Contents dedupliziert."""

    def test_transcript_doppelter_absatz_entfernt(self):
        from app.services.prompts import build_user_content
        # Whisper-Hallucination: derselbe Satz mehrfach
        transcript = (
            "Der Patient berichtet von Schlafstoerungen.\n\n"
            "Der Patient berichtet von Schlafstoerungen.\n\n"
            "Er hat seit Monaten Albtraeume.\n\n"
            "Der Patient berichtet von Schlafstoerungen."
        )
        u = build_user_content(workflow="dokumentation", transcript=transcript)
        # Der Satz darf nur einmal vorkommen
        count = u.count("Der Patient berichtet von Schlafstoerungen.")
        assert count == 1, f"Duplikat nicht entfernt: {count}x in:\n{u}"
        # Andere Inhalte bleiben erhalten
        assert "Er hat seit Monaten Albtraeume." in u

    def test_verlaufsdoku_dedupliziert(self):
        from app.services.prompts import build_user_content
        verlauf = (
            "Sitzung 1: Aufnahmegespraech.\n\n"
            "Sitzung 1: Aufnahmegespraech.\n\n"  # PDF-Header-Duplikat
            "Sitzung 2: Vertiefung."
        )
        u = build_user_content(
            workflow="entlassbericht",
            verlaufsdoku_text=verlauf,
        )
        assert u.count("Sitzung 1: Aufnahmegespraech.") == 1

    def test_dedup_bricht_bei_fehler_nicht_alles(self):
        """Wenn deduplicate_paragraphs einen Fehler wirft, faellt der
        User-Content trotzdem zusammen (Original-Text wird verwendet)."""
        from app.services.prompts import build_user_content

        with patch("app.services.llm.deduplicate_paragraphs",
                   side_effect=RuntimeError("simuliert")):
            u = build_user_content(
                workflow="dokumentation",
                transcript="Original-Transcript ohne Duplikate.",
            )
            assert "Original-Transcript ohne Duplikate." in u

    def test_dedup_bei_leeren_quelltexten_kein_crash(self):
        from app.services.prompts import build_user_content
        u = build_user_content(
            workflow="dokumentation",
            transcript="",
            selbstauskunft_text=None,
            verlaufsdoku_text="   ",
        )
        # Prompt wurde gebaut ohne TypeError oder AttributeError
        assert isinstance(u, str)


# ── /api/testrun ──────────────────────────────────────────────────────────────

