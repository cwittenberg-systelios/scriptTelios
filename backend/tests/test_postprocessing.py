"""
Tests fuer das v16-Postprocessor-Modul (app/services/postprocessing.py).
"""
import pytest


# ── Klebebug-Fixes ────────────────────────────────────────────────────────────

class TestFixKompositumKlebebugs:

    def test_schweresowie_repariert(self):
        from app.services.postprocessing import fix_kompositum_klebebugs
        text = "Folgende Krankheitssymptomatik macht in der Art und Schweresowie unter Beruecksichtigung..."
        result = fix_kompositum_klebebugs(text)
        assert "Schwere sowie" in result
        assert "Schweresowie" not in result

    def test_aufenthaltszeigte_repariert(self):
        from app.services.postprocessing import fix_kompositum_klebebugs
        text = "Zu Beginn des stationaeren Aufenthaltszeigte sich Frau M. innerlich angespannt."
        result = fix_kompositum_klebebugs(text)
        assert "Aufenthaltes zeigte" in result
        assert "Aufenthaltszeigte" not in result

    def test_verlaengerungsantraghat_repariert(self):
        from app.services.postprocessing import fix_kompositum_klebebugs
        text = "Im weiteren Verlauf seit dem letzten Verlängerungsantraghat sich Herr R. weiterentwickelt."
        result = fix_kompositum_klebebugs(text)
        assert "Verlängerungsantrag hat" in result
        assert "Verlängerungsantraghat" not in result

    def test_mehrere_klebebugs_in_einem_text(self):
        from app.services.postprocessing import fix_kompositum_klebebugs
        text = (
            "Schweresowie unter Beruecksichtigung. "
            "Zu Beginn des Aufenthaltszeigte sich. "
            "Verlängerungsantraghat sich Herr R. entwickelt."
        )
        result = fix_kompositum_klebebugs(text)
        assert "Schwere sowie" in result
        assert "Aufenthaltes zeigte" in result
        assert "Verlängerungsantrag hat" in result

    def test_text_ohne_klebebugs_unveraendert(self):
        from app.services.postprocessing import fix_kompositum_klebebugs
        text = "Normaler Text ohne Klebebugs. Alles ist gut formatiert mit Leerzeichen."
        result = fix_kompositum_klebebugs(text)
        assert result == text

    def test_leerer_input(self):
        from app.services.postprocessing import fix_kompositum_klebebugs
        assert fix_kompositum_klebebugs("") == ""
        assert fix_kompositum_klebebugs(None) is None

    def test_keine_false_positive_bei_legitimer_zusammensetzung(self):
        """Echte deutsche Komposita werden NICHT veraendert."""
        from app.services.postprocessing import fix_kompositum_klebebugs
        text = "Aufenthaltsdauer betraegt zwei Wochen. Verlängerungsantragsstellung war positiv."
        result = fix_kompositum_klebebugs(text)
        # 'Aufenthaltsdauer' und 'Verlängerungsantragsstellung' sind echte Komposita,
        # haben kein Verb-Suffix dahinter -> bleiben unveraendert
        assert "Aufenthaltsdauer" in result
        assert "Verlängerungsantragsstellung" in result


# ── Loop-Repetition-Detector ──────────────────────────────────────────────────

class TestDetectLoopRepetition:

    def test_loop_am_ende_wird_abgeschnitten(self):
        """Output wo der letzte Block ein woertliches Duplikat eines frueheren ist."""
        from app.services.postprocessing import detect_loop_repetition
        # Block ist >200 Zeichen lang
        original_block = (
            "Sie hat Erfahrungen mit Einzelgespraechen und Gruppenangeboten, was sie als hilfreich empfindet. "
            "Sie lebt noch zu Hause bei ihren Eltern, die sich fuer ihre Therapie einsetzen. "
            "Sie hat keine bekannten Suchtprobleme, aber sie berichtet, dass sie sich mit dem Thema ADS beschaeftigt. "
            "Sie hat keine bekannten Allergien oder Vorerkrankungen, die in den Unterlagen genannt werden."
        )
        text = (
            f"Anfangsabschnitt mit ausreichend Text um die Loop-Erkennung zu triggern. "
            f"Zwischenabsatz mit weiteren Inhalten. {original_block} "
            f"Zwischenabschnitt 2 hier mit weiterem unique Inhalt. {original_block}"
        )
        result = detect_loop_repetition(text)
        # Das doppelte Vorkommen wurde am Ende abgeschnitten
        assert result.count(original_block[:100]) == 1

    def test_kein_loop_normaler_text(self):
        """Normaler Text ohne Wiederholung bleibt unveraendert."""
        from app.services.postprocessing import detect_loop_repetition
        text = (
            "Erster Absatz mit eigenem Inhalt. " * 10 + "\n\n"
            + "Zweiter Absatz mit anderem Inhalt. " * 10 + "\n\n"
            + "Dritter Absatz mit drittem Inhalt. " * 10
        )
        result = detect_loop_repetition(text)
        assert result == text

    def test_kurzer_text_unveraendert(self):
        from app.services.postprocessing import detect_loop_repetition
        text = "Zu kurzer Text fuer Loop-Erkennung."
        assert detect_loop_repetition(text) == text


# ── Keyword-Check ─────────────────────────────────────────────────────────────

class TestDetectMissingKeywords:

    def test_alle_keywords_vorhanden(self):
        from app.services.postprocessing import detect_missing_keywords
        text = "Trennung im Mai. Methylphenidat wird verschrieben. Diagnose F33.1."
        missing = detect_missing_keywords(text, ["Trennung", "Methylphenidat", "F33.1"])
        assert missing == []

    def test_einige_fehlen(self):
        from app.services.postprocessing import detect_missing_keywords
        text = "Trennung im Mai. Diagnose F33.1."
        missing = detect_missing_keywords(text, ["Trennung", "Methylphenidat", "F33.1"])
        assert missing == ["Methylphenidat"]

    def test_case_insensitive_default(self):
        from app.services.postprocessing import detect_missing_keywords
        text = "trennung im Mai."
        missing = detect_missing_keywords(text, ["Trennung"])
        assert missing == []

    def test_case_sensitive_optional(self):
        from app.services.postprocessing import detect_missing_keywords
        text = "trennung im Mai."
        missing = detect_missing_keywords(text, ["Trennung"], case_insensitive=False)
        assert missing == ["Trennung"]


class TestExtractLikelyKeywords:

    def test_findet_semantische_keywords(self):
        from app.services.postprocessing import extract_likely_keywords
        source = "Patient erlebte Trennung von der Ehefrau im Mai. Diagnose F33.1 mit ADS."
        kw = extract_likely_keywords(source)
        assert "Trennung" in kw
        assert "ADS" in kw
        assert "F33.1" in kw

    def test_findet_keine_keywords_in_unrelevantem_text(self):
        from app.services.postprocessing import extract_likely_keywords
        source = "Heute ist schoenes Wetter und der Hund spielt im Garten."
        kw = extract_likely_keywords(source)
        assert kw == []

    def test_leerer_input(self):
        from app.services.postprocessing import extract_likely_keywords
        assert extract_likely_keywords("") == []
        assert extract_likely_keywords(None) == []


# ── Hard-Cap ──────────────────────────────────────────────────────────────────

class TestHardCapWordCount:

    def test_text_unter_limit_unveraendert(self):
        from app.services.postprocessing import hard_cap_word_count
        text = "Kurzer Text mit zehn Worten genau hier zu sehen drin."
        assert hard_cap_word_count(text, max_words=20) == text

    def test_text_ueber_limit_wird_an_satzgrenze_gekuerzt(self):
        from app.services.postprocessing import hard_cap_word_count
        text = (
            "Erster Satz mit fuenf Worten. "
            "Zweiter Satz auch fuenf Worten. "
            "Dritter Satz hat fuenf Worten. "
            "Vierter Satz hat fuenf Worten."
        )
        result = hard_cap_word_count(text, max_words=10)
        # Erste 2 Saetze sind 10 Worte zusammen, sollte da abgeschnitten werden
        assert result.endswith(".") or result.endswith("!") or result.endswith("?")
        assert len(result.split()) <= 12  # mit Toleranz

    def test_5prozent_toleranz(self):
        """Bei 105% des Limits wird NICHT abgeschnitten."""
        from app.services.postprocessing import hard_cap_word_count
        # Genau 21 Woerter, Limit 20 -> 105% -> nicht abschneiden
        text = " ".join([f"wort{i}" for i in range(21)])
        result = hard_cap_word_count(text, max_words=20)
        assert result == text  # 105% Toleranz greift

    def test_leerer_input(self):
        from app.services.postprocessing import hard_cap_word_count
        assert hard_cap_word_count("", max_words=100) == ""


# ── Master-Postprocessor ──────────────────────────────────────────────────────

class TestPostprocessOutput:

    def test_komplette_pipeline(self):
        """Klebebug + Loop + Cap in Reihenfolge."""
        from app.services.postprocessing import postprocess_output
        text = (
            "Folgende Krankheitssymptomatik macht in der Art und Schweresowie unter "
            "Beruecksichtigung der Beurteilung des Einweisers ein stationaeres "
            "Krankenhaussetting akut notwendig. Wir nehmen Frau M. schwer belastet auf. "
            "Sie zeigt sich mit ausgepraegter depressiver Symptomatik."
        )
        result = postprocess_output(text)
        # 1. Klebebug repariert
        assert "Schwere sowie" in result
        assert "Schweresowie" not in result

    def test_mit_max_words_cap(self):
        from app.services.postprocessing import postprocess_output
        text = ". ".join([f"Satz {i} hat einige Wörter drin" for i in range(20)])
        result = postprocess_output(text, max_words=30)
        assert len(result.split()) <= 35  # mit Toleranz

    def test_keyword_check_loggt_warning(self, caplog):
        from app.services.postprocessing import postprocess_output
        import logging
        text = "Output ohne wichtige Keywords."
        with caplog.at_level(logging.WARNING):
            postprocess_output(text, expected_keywords=["Trennung", "ADS"])
        # Warning wurde geloggt
        assert any("erwartete Keywords" in r.message for r in caplog.records)

    def test_leerer_input(self):
        from app.services.postprocessing import postprocess_output
        assert postprocess_output("") == ""
        assert postprocess_output(None) is None
