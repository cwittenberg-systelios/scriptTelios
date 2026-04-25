"""
Tests fuer die v13 -> v14 Patches in prompts.py.

Schwerpunkte:
5. Standardformulierungen werden als woertlich-zu-uebernehmender Block markiert
   (Verhindert Klebebugs wie "Schweresowie", "Aufenthaltszeigte")
6. Wir-Perspektive im ersten Satz wird explizit gefordert (Akutantrag, Folgeverlaengerung)
7. Word-Limits-Block enthaelt klare Vorrang-Regel ueber BASE_PROMPT-Mindestlaengen
8. Fragment-Stil wird in _compute_style_constraints erkannt
9. Patientenspezifische Keywords werden im strukturellen Modus explizit eingefordert
"""
import pytest


# ── Bug 5: Klebebug-Praevention bei Standardformulierungen ────────────────────

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

class TestWordLimitsVorrang:
    """VERBINDLICHES TEXTLIMIT muss klar Vorrang ueber BASE_PROMPT-Mindestangaben haben."""

    def test_word_limits_block_erwaehnt_vorrang(self):
        from app.services.prompts import build_system_prompt
        p = build_system_prompt(
            workflow="anamnese",
            word_limits=(200, 400),
        )
        assert "VERBINDLICHES TEXTLIMIT" in p
        # Klare Anweisung dass das BASE_PROMPT-Minimum ungueltig wird
        assert "UNGUELTIG" in p or "überschreibt" in p or "Vorrang" in p
        assert "harte Obergrenze" in p or "maximal 400" in p

    def test_anamnese_basis_keine_harten_mindestens(self):
        """Anamnese-BASE_PROMPT darf kein 'Mindestens 450' mehr stehen
        ohne Vorrang-Hinweis auf VERBINDLICHES TEXTLIMIT."""
        from app.services.prompts import BASE_PROMPTS
        text = BASE_PROMPTS["anamnese"]
        if "Mindestens 450" in text or "mindestens 450" in text:
            # Wenn 450 noch drinsteht, MUSS auch der Vorrang-Hinweis da sein
            assert "VERBINDLICHES TEXTLIMIT" in text or "absolute Vorrang" in text or "Richtwert" in text

    def test_entlassbericht_basis_keine_harten_teil_mindestens(self):
        """Entlassbericht: 'mindestens 300', 'mindestens 100', 'mindestens 80'
        sollten weich formuliert sein."""
        from app.services.prompts import BASE_PROMPTS
        text = BASE_PROMPTS["entlassbericht"]
        # Entweder weich formuliert (Richtwert/ca.) oder mit Vorrang-Hinweis
        if "mindestens 300" in text:
            assert "Richtwert" in text or "VERBINDLICHES TEXTLIMIT" in text


# ── Bug 8: Fragment-Stil-Erkennung ───────────────────────────────────────────

class TestFragmentStilErkennung:
    """_compute_style_constraints erkennt Stichwort-/Notiz-Stil."""

    def test_fragment_stil_wird_erkannt(self):
        from app.services.prompts import _compute_style_constraints
        # Fragment-artiger Text wie dok-02-Stilvorlage
        fragment_text = (
            "thematisiert, dass hier für sie ein guter Ort sei.\n"
            "signalisiere ihr, dass es okay ist.\n"
            "bringt Bilder mit, die sie gezeichnet habe.\n"
            "Wunsch, Tod ungeschehen zu machen.\n"
            "Schuldgefühle, Kontaktaufnahme zur Tochter.\n"
            "Bilder nicht angucken können.\n"
            "Gegen Ende des Gesprächs Entlastung.\n"
            "Kein Hinweis auf akute Suizidalität."
        )
        result = _compute_style_constraints(fragment_text)
        assert "Stichwort" in result or "Notiz-Stil" in result or "STILTYP" in result

    def test_normaler_stil_wird_nicht_als_fragment_erkannt(self):
        from app.services.prompts import _compute_style_constraints
        # Normaler Fliesstext (Wir-Form, lange Saetze)
        normal_text = (
            "Wir nahmen Frau M. im bisherigen Verlauf des stationären Aufenthaltes "
            "unter anhaltendem innerem Druck auf, mit ausgeprägter Anspannung und "
            "emotionaler Ambivalenz. Gleichzeitig erkannten wir eine zunehmende "
            "Bereitschaft, sich auf den therapeutischen Prozess einzulassen und "
            "auch sehr vulnerable innere Themen zu explorieren. Im hypnosystemischen "
            "Einzelprozess konnten wir mithilfe der Anteilearbeit insbesondere einen "
            "dominanten Kontrollanteil differenzieren."
        )
        result = _compute_style_constraints(normal_text)
        assert "Stichwort" not in result and "Notiz-Stil" not in result


# ── Bug 9: Patientenspezifische Keywords beibehalten ──────────────────────────

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

class TestSmokeAllWorkflows:
    """Alle Workflows muessen weiterhin valide System-Prompts erzeugen."""

    @pytest.mark.parametrize("wf", [
        "dokumentation", "anamnese", "verlaengerung",
        "folgeverlaengerung", "akutantrag", "entlassbericht",
    ])
    def test_workflow_baut_validen_prompt(self, wf):
        from app.services.prompts import build_system_prompt
        p = build_system_prompt(
            workflow=wf,
            patient_name={"anrede": "Frau", "vorname": "Maria",
                          "nachname": "Schmidt", "initial": "S."},
            diagnosen=["F33.1 Rezidivierende depressive Störung"],
        )
        assert len(p) > 500
        assert "[Patient/in]" not in p or "Frau S." in p  # Replace funktionierte

    @pytest.mark.parametrize("wf", [
        "dokumentation", "anamnese", "verlaengerung",
        "folgeverlaengerung", "akutantrag", "entlassbericht",
    ])
    def test_workflow_baut_validen_user_content(self, wf):
        from app.services.prompts import build_user_content
        u = build_user_content(
            workflow=wf,
            transcript="Patient berichtet von erschoepfender Arbeitsphase.",
            patient_name={"anrede": "Frau", "vorname": "Maria",
                          "nachname": "Schmidt", "initial": "S."},
        )
        assert len(u) > 50
        assert "die Klientin/der Klient" not in u
