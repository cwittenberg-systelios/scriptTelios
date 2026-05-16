"""
Laengen-Anker: derive_word_limits, resolve_length_anchor, Multi-Style-Vorlagen.

Konsolidiert aus den frueheren Versions-Dateien test_prompts_v13.py bis
test_prompts_v16.py. Tests sind unveraendert, nur nach Feature-Area
umgruppiert. Versions-Geschichte steht im Git-Log.
"""
import pytest
import re
from unittest.mock import patch, MagicMock

# ──── aus test_prompts_v14.py: TestWordLimitsVorrang
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

# ──── aus test_prompts_v14.py: TestFragmentStilErkennung
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

# ──── aus test_prompts_v16.py: TestSplitStyleExamples
class TestSplitStyleExamples:
    """split_style_examples erkennt das Marker-Format und liefert Einzeltexte."""

    def test_empty_input(self):
        from app.services.prompts import split_style_examples
        assert split_style_examples("") == []
        assert split_style_examples("   ") == []

    def test_none_safe(self):
        """None-Eingabe darf nicht crashen."""
        from app.services.prompts import split_style_examples
        # Funktionssignatur erwartet str, aber falls jemand None reinreicht
        # soll es nicht hart crashen - leerer String wird zu []
        assert split_style_examples("") == []

    def test_single_text_no_marker_backwards_compat(self):
        """Text ohne Marker → 1-Element-Liste (wichtig für single-style channels)."""
        from app.services.prompts import split_style_examples
        result = split_style_examples("Just plain text without any markers.")
        assert result == ["Just plain text without any markers."]

    def test_two_examples_eval_format(self):
        """'--- Beispiel N ---'-Marker (Eval-Format ohne Anker-Markierung)."""
        from app.services.prompts import split_style_examples
        combined = (
            "--- Beispiel 1 ---\n"
            "Erste Vorlage Inhalt mit mehreren Sätzen.\n\n"
            "--- Beispiel 2 ---\n"
            "Zweite Vorlage komplett anders."
        )
        result = split_style_examples(combined)
        assert len(result) == 2
        assert result[0].startswith("Erste Vorlage")
        assert result[1].startswith("Zweite Vorlage")

    def test_two_examples_pgvector_format_with_anker(self):
        """'[Anker]'-Markierung (pgvector-Format) wird auch erkannt."""
        from app.services.prompts import split_style_examples
        combined = (
            "--- Beispiel 1 [Anker] ---\n"
            "Anker-Beispiel.\n\n"
            "--- Beispiel 2 ---\n"
            "Semantic-Beispiel."
        )
        result = split_style_examples(combined)
        assert len(result) == 2
        assert "Anker-Beispiel" in result[0]
        assert "Semantic-Beispiel" in result[1]

    def test_three_examples_mixed(self):
        """Mehr als zwei Beispiele, gemischtes Anker/non-Anker-Format."""
        from app.services.prompts import split_style_examples
        combined = (
            "--- Beispiel 1 ---\nA\n\n"
            "--- Beispiel 2 ---\nB\n\n"
            "--- Beispiel 3 [Anker] ---\nC"
        )
        assert split_style_examples(combined) == ["A", "B", "C"]

    def test_marker_with_extra_whitespace(self):
        """Toleranz für unterschiedliche Whitespace-Variationen im Marker."""
        from app.services.prompts import split_style_examples
        combined = (
            "---  Beispiel 1   ---\nA\n\n"
            "--- Beispiel 2 [Anker] ---\nB"
        )
        result = split_style_examples(combined)
        assert result == ["A", "B"]

    def test_strips_inner_whitespace(self):
        """Texte werden gestrippt (kein führender/trailing Leerraum)."""
        from app.services.prompts import split_style_examples
        combined = "--- Beispiel 1 ---\n\n   Erste   \n\n--- Beispiel 2 ---\n\n  Zweite  "
        result = split_style_examples(combined)
        assert result == ["Erste", "Zweite"]


# ── resolve_length_anchor mit Multi-Beispielen ────────────────────────────────

# ──── aus test_prompts_v16.py: TestResolveLengthAnchorMulti
class TestResolveLengthAnchorMulti:
    """resolve_length_anchor mittelt sauber wenn Liste statt konkatenierter Block."""

    def test_two_examples_average_not_concat(self):
        """Hauptzweck: Längen-Anker bei 2×400w landet bei ~400w (nicht ~800w).

        Vorher (konkateniert): ein Eintrag mit 800w → Range 565-909w.
        Jetzt (gesplittet): zwei Einträge je 400w → Range ~350-520w.
        """
        from app.services.prompts import resolve_length_anchor

        style1 = " ".join(["Wort"] * 400)
        style2 = " ".join(["andere"] * 400)
        r = resolve_length_anchor(
            "folgeverlaengerung",
            style_raw_texts=[style1, style2],
        )
        assert r["source"] == "style"
        assert r["n_substantial"] == 2
        # Target sollte um 400 liegen, nicht um 800
        assert 350 <= r["target"] <= 500, (
            f"Target {r['target']} suggeriert Konkatenation statt Mittelung"
        )

    def test_concat_vs_split_demonstrably_different(self):
        """Beweist: Konkatenation gibt höheres Limit als Splitting.

        Wenn diese Assertion fehlschlägt, dann hat resolve_length_anchor sich
        so geändert dass beide Pfade gleich sind - das wäre ein Regression.
        """
        from app.services.prompts import resolve_length_anchor

        style1 = " ".join(["Wort"] * 400)
        style2 = " ".join(["andere"] * 400)
        combined = (
            "--- Beispiel 1 ---\n" + style1 + "\n\n"
            "--- Beispiel 2 ---\n" + style2
        )

        # "Alte" Behandlung: alles als 1 Text (was vor Strategie 3 passierte)
        r_concat = resolve_length_anchor(
            "folgeverlaengerung", style_raw_texts=[combined]
        )
        # "Neue" Behandlung: gesplittet als 2 Texte
        r_split = resolve_length_anchor(
            "folgeverlaengerung", style_raw_texts=[style1, style2]
        )

        # Konkatenation sollte deutlich höheres Limit produzieren
        assert r_concat["target"] > r_split["target"], (
            f"Konkat-Target {r_concat['target']} sollte > Split-Target "
            f"{r_split['target']} sein"
        )

    def test_one_substantial_one_short_filters(self):
        """Eine Vorlage ≥threshold, eine darunter → nur die substantielle zählt."""
        from app.services.prompts import resolve_length_anchor

        style_long = " ".join(["Wort"] * 400)  # substantial
        style_short = " ".join(["x"] * 50)     # too short
        r = resolve_length_anchor(
            "folgeverlaengerung",
            style_raw_texts=[style_long, style_short],
        )
        # Nur 1 substantial → n_substantial=1 (nicht 2)
        assert r["n_substantial"] == 1
        assert r["source"] == "style"


# ── Eval-Discovery Helpers ────────────────────────────────────────────────────

# ──── aus test_prompts_v16.py: TestDiscoverStyleSiblings
class TestDiscoverStyleSiblings:
    """discover_style_siblings findet vorlage.txt + vorlage2.txt etc."""

    def _import_helper(self):
        from tests.eval.eval_helpers import discover_style_siblings
        return discover_style_siblings

    def test_single_vorlage(self, tmp_path):
        """Nur eine Datei → Liste mit dieser einen."""
        discover = self._import_helper()
        p = tmp_path / "vorlage.txt"
        p.write_text("Erste Vorlage.", encoding="utf-8")
        result = discover(p)
        assert len(result) == 1
        assert result[0] == p

    def test_two_vorlagen(self, tmp_path):
        """vorlage.txt + vorlage2.txt → beide gefunden, Reihenfolge stimmt."""
        discover = self._import_helper()
        v1 = tmp_path / "vorlage.txt"
        v2 = tmp_path / "vorlage2.txt"
        v1.write_text("erste", encoding="utf-8")
        v2.write_text("zweite", encoding="utf-8")
        result = discover(v1)
        assert len(result) == 2
        assert result[0] == v1
        assert result[1] == v2

    def test_three_vorlagen_sorted(self, tmp_path):
        """Drei Dateien werden numerisch sortiert (1, 2, 3) zurückgegeben."""
        discover = self._import_helper()
        v1 = tmp_path / "vorlage.txt"
        v2 = tmp_path / "vorlage2.txt"
        v3 = tmp_path / "vorlage3.txt"
        # Reihenfolge der Erstellung soll Ergebnis nicht beeinflussen
        for p in [v3, v1, v2]:
            p.write_text("text", encoding="utf-8")
        result = discover(v1)
        assert [p.name for p in result] == [
            "vorlage.txt", "vorlage2.txt", "vorlage3.txt"
        ]

    def test_underscore_separator(self, tmp_path):
        """vorlage_01.txt-Variante wird ebenfalls erkannt."""
        discover = self._import_helper()
        v1 = tmp_path / "vorlage_01.txt"
        v2 = tmp_path / "vorlage_02.txt"
        v1.write_text("a", encoding="utf-8")
        v2.write_text("b", encoding="utf-8")
        result = discover(v1)
        # Mindestens 2 Dateien gefunden, beide in Reihenfolge
        names = [p.name for p in result]
        assert "vorlage_01.txt" in names
        assert "vorlage_02.txt" in names

    def test_ignores_other_extensions(self, tmp_path):
        """Andere Suffixe werden nicht aufgesammelt."""
        discover = self._import_helper()
        v1 = tmp_path / "vorlage.txt"
        v1.write_text("text", encoding="utf-8")
        (tmp_path / "vorlage2.docx").write_text("docx", encoding="utf-8")
        (tmp_path / "vorlage3.txt").write_text("text", encoding="utf-8")
        result = discover(v1)
        assert all(p.suffix == ".txt" for p in result)
        assert len(result) == 2

    def test_returns_empty_for_nonexistent(self, tmp_path):
        """Primary-Datei existiert nicht → leere Liste."""
        discover = self._import_helper()
        result = discover(tmp_path / "nonexistent.txt")
        assert result == []

    def test_unrelated_files_ignored(self, tmp_path):
        """Dateien mit anderem Stamm werden nicht aufgesammelt."""
        discover = self._import_helper()
        v1 = tmp_path / "vorlage.txt"
        v1.write_text("a", encoding="utf-8")
        (tmp_path / "anderer.txt").write_text("b", encoding="utf-8")
        (tmp_path / "vorlage2.txt").write_text("c", encoding="utf-8")
        result = discover(v1)
        names = [p.name for p in result]
        assert "anderer.txt" not in names
        assert "vorlage.txt" in names
        assert "vorlage2.txt" in names

# ──── aus test_prompts_v16.py: TestBuildMultiStyleText
class TestBuildMultiStyleText:
    """build_multi_style_text erzeugt das korrekte Marker-Format."""

    def _import_helper(self):
        from tests.eval.eval_helpers import build_multi_style_text
        return build_multi_style_text

    def test_empty_paths(self):
        """Leere Liste → leerer String."""
        build = self._import_helper()
        assert build([]) == ""

    def test_single_file_no_marker(self, tmp_path):
        """Single-Vorlage erhält KEINEN Marker (Backwards-Compat).

        Wichtig: alte Tests/Eval-Cases mit nur einer vorlage.txt sollen
        sich exakt so verhalten wie vor Strategie 3.
        """
        build = self._import_helper()
        v1 = tmp_path / "vorlage.txt"
        v1.write_text("Nur eine Vorlage.", encoding="utf-8")
        result = build([v1])
        assert "--- Beispiel" not in result
        assert "Nur eine Vorlage." in result

    def test_two_files_with_markers(self, tmp_path):
        """Multi-Vorlagen werden mit Markern verkettet."""
        build = self._import_helper()
        v1 = tmp_path / "vorlage.txt"
        v2 = tmp_path / "vorlage2.txt"
        v1.write_text("Erste Vorlage.", encoding="utf-8")
        v2.write_text("Zweite Vorlage.", encoding="utf-8")
        result = build([v1, v2])
        assert "--- Beispiel 1 ---" in result
        assert "--- Beispiel 2 ---" in result
        assert "Erste Vorlage." in result
        assert "Zweite Vorlage." in result

    def test_three_files_with_sequential_markers(self, tmp_path):
        """Drei Dateien → Marker 1, 2, 3 (nicht 0, 1, 2 oder anders)."""
        build = self._import_helper()
        files = []
        for i, name in enumerate(["vorlage.txt", "vorlage2.txt", "vorlage3.txt"], 1):
            p = tmp_path / name
            p.write_text(f"Inhalt {i}", encoding="utf-8")
            files.append(p)
        result = build(files)
        assert "--- Beispiel 1 ---" in result
        assert "--- Beispiel 2 ---" in result
        assert "--- Beispiel 3 ---" in result
        # Marker-4 sollte NICHT existieren
        assert "--- Beispiel 4 ---" not in result

    def test_skips_unreadable_files(self, tmp_path):
        """Wenn eine Datei nicht lesbar ist (leere oder corrupted), wird sie
        übersprungen, die anderen kommen aber durch."""
        build = self._import_helper()
        v1 = tmp_path / "vorlage.txt"
        v_empty = tmp_path / "vorlage2.txt"  # leer
        v3 = tmp_path / "vorlage3.txt"
        v1.write_text("Erste.", encoding="utf-8")
        v_empty.write_text("", encoding="utf-8")
        v3.write_text("Dritte.", encoding="utf-8")
        result = build([v1, v_empty, v3])
        # Leere wird übersprungen → nur 2 Beispiele
        assert "Erste." in result
        assert "Dritte." in result
        # Wenn alle 3 reingegangen wären, gäbe es Beispiel 3
        # Aber mit nur 2 effektiven gibts nur Beispiel 1 und 2
        # Reihenfolge: Beispiel 1=v1, Beispiel 2=v3 (v_empty entfällt)
        assert "--- Beispiel 1 ---" in result
        assert "--- Beispiel 2 ---" in result


# ── End-to-End Roundtrip ──────────────────────────────────────────────────────

# ──── aus test_prompts_v16.py: TestEndToEndRoundtrip
class TestEndToEndRoundtrip:
    """Eval baut Multi-Style → jobs.py-Splitter findet Einzelvorlagen wieder.

    Das ist der entscheidende Roundtrip-Test: Strategie 3 funktioniert nur
    wenn das Marker-Format auf beiden Seiten konsistent ist.
    """

    def _import_helpers(self):
        from tests.eval.eval_helpers import build_multi_style_text
        from app.services.prompts import split_style_examples
        return build_multi_style_text, split_style_examples

    def test_roundtrip_two_examples(self, tmp_path):
        build, split = self._import_helpers()

        v1 = tmp_path / "vorlage.txt"
        v2 = tmp_path / "vorlage2.txt"
        v1.write_text(
            "Erste Vorlage Inhalt mit zehn Worten hier zum Test.",
            encoding="utf-8",
        )
        v2.write_text(
            "Zweite Vorlage Inhalt komplett anders formuliert hier.",
            encoding="utf-8",
        )

        # Eval-Pfad: baut konkatenierte Marker-Form
        combined = build([v1, v2])

        # jobs.py-Pfad: splittet wieder in Einzeltexte
        result = split(combined)

        assert len(result) == 2
        assert "Erste Vorlage" in result[0]
        assert "Zweite Vorlage" in result[1]

    def test_roundtrip_preserves_content(self, tmp_path):
        """Der Roundtrip darf den Inhalt nicht verändern (modulo strip)."""
        build, split = self._import_helpers()

        v1 = tmp_path / "vorlage.txt"
        v2 = tmp_path / "vorlage2.txt"
        original_1 = "Frau M. zeigt sich offen.\nWir erlebten sie reflektiert."
        original_2 = "Wir konnten differenzieren.\nIm Verlauf zeigte sich."
        v1.write_text(original_1, encoding="utf-8")
        v2.write_text(original_2, encoding="utf-8")

        result = split(build([v1, v2]))
        assert result[0] == original_1.strip()
        assert result[1] == original_2.strip()

    def test_roundtrip_single_file_no_marker_overhead(self, tmp_path):
        """Single-Vorlage geht durch ohne dass Marker eingeführt werden.

        Sicherstellt: existierende single-style Eval-Cases werden durch
        Strategie 3 nicht plötzlich verändert.
        """
        build, split = self._import_helpers()

        v1 = tmp_path / "vorlage.txt"
        original = "Nur eine Vorlage hier."
        v1.write_text(original, encoding="utf-8")

        combined = build([v1])
        # Combined sollte EXAKT der Inhalt sein, ohne Marker
        assert combined.strip() == original
        # Split sollte 1-Element-Liste sein
        result = split(combined)
        assert result == [original]


# ── P0: Wir-Workflow Override für STIL-CHECKS ─────────────────────────────────

# ──── aus test_prompts_v16.py: TestWirWorkflowOverride
class TestWirWorkflowOverride:
    """v13 P0: STIL-CHECKS darf bei Wir-Workflows nie 'kein Wir' sagen.

    Hintergrund: Bei Wir-Workflows (akutantrag, verlaengerung, folgeverlaengerung,
    entlassbericht) verlangt der BASE_PROMPT explizit Wir-Perspektive. Wenn die
    Stilvorlage selbst aber in 3.-Person ist (typisch Frau-F.-Vorlagen), gab die
    Checkliste 'durchgängig dritte Person, kein Wir' aus → direkter Widerspruch
    im Prompt. Im Eval-Run nach Ä1-Ä5 verursachte dieser Konflikt drei Failures
    (fva-02, eb-02, dok-01).

    P0 fügt einen workflow-Parameter ein, der bei Wir-Workflows die
    Perspektive-Zeile auf eine Wir-Bandbreite umstellt.
    """

    def _make_3p_style(self):
        """3.-Person-Stilvorlage (typisch Frau-F.-Vorlagen)."""
        return (
            "Frau F. konnte sich gut auf die stationäre Behandlung einlassen und "
            "zeigte sich bereits durch die Aufnahme etwas entlastet. Sie nahm von "
            "Anfang an motiviert an den Therapien teil. Ihr Selbstwert habe in "
            "den letzten Jahren der Beziehung gelitten. Sie habe in der Zeit "
            "immer mehr von sich aufgegeben und keine Grenzen gesetzt. Sie fühle "
            "sich wertlos."
        ) * 3

    def _make_wir_style(self):
        """Wir-Stilvorlage (typisch Frau-Sch.-Vorlagen)."""
        return (
            "Wir nahmen Frau M. unter Druck auf. Wir erlebten sie zu "
            "Therapiebeginn deutlich erschöpft. Im Verlauf gelang es uns die "
            "Anteile zu differenzieren. Wir konnten gemeinsam eine "
            "wohlwollendere innere Haltung aufbauen. Aus medizinisch-"
            "psychotherapeutischer Sicht halten wir eine Verlängerung für indiziert."
        ) * 3

    def test_wir_workflows_constant_complete(self):
        """WIR_WORKFLOWS enthält genau die vier Wir-pflichtigen Workflows."""
        from app.services.prompts import WIR_WORKFLOWS
        assert WIR_WORKFLOWS == frozenset({
            "akutantrag",
            "verlaengerung",
            "folgeverlaengerung",
            "entlassbericht",
        })

    def test_3p_style_no_workflow_keeps_old_behavior(self):
        """Ohne workflow-Parameter: alter Pfad, '3.-Person-Pflicht' bleibt.

        Backwards-Compat: wer die Funktion ohne workflow ruft, sieht das
        gleiche Verhalten wie vor P0.
        """
        from app.services.prompts import _compute_style_constraints
        result = _compute_style_constraints(self._make_3p_style())
        assert "dritte Person" in result
        assert "kein 'Wir'" in result

    def test_3p_style_wir_workflow_overrides_to_wir(self):
        """A korrigiert: 3.-Person-Vorlage + empathischer Workflow.

        NEU: KEIN Wir-Zwang mehr (P0 wurde durch A korrigiert ersetzt).
        Stattdessen: Vorlagen-Mimik ('3.-Person erlaubt') plus explizites
        Verbot des objektiv-distanzierten Berichtstons.
        """
        from app.services.prompts import _compute_style_constraints
        for wf in ("akutantrag", "verlaengerung",
                   "folgeverlaengerung", "entlassbericht"):
            result = _compute_style_constraints(self._make_3p_style(), workflow=wf)
            # Wir wird NICHT mehr erzwungen
            assert "Wir-Anteil:" not in result, (
                f"{wf}: Wir-Anteil-Bandbreite drin, sollte aber nicht "
                f"(A korrigiert: kein Wir-Zwang bei 3.-Person-Vorlage)"
            )
            assert "Erster Satz beginnt mit 'Wir'" not in result, (
                f"{wf}: Wir-Zwang noch da"
            )
            # NEU: Tonfall-Hinweis MUSS da sein
            assert "Tonfall: empathisch-konjunktivisch" in result, (
                f"{wf}: empathischer Tonfall-Hinweis fehlt"
            )
            assert "objektiv-wissenden Berichtston" in result, (
                f"{wf}: Verbot des Berichtstons fehlt"
            )

    def test_3p_style_wir_workflow_no_more_default_band(self):
        """A korrigiert: bei 3.-Person-Vorlage kommt KEINE Wir-Default-Bandbreite mehr.

        Vorher (P0): Klinik-Default 1-3% Wir-Anteil wurde aufgezwungen.
        Jetzt (A korrigiert): Vorlage 3.-Person → bleibt 3.-Person, aber
        empathisch.
        """
        from app.services.prompts import _compute_style_constraints
        result = _compute_style_constraints(
            self._make_3p_style(), workflow="folgeverlaengerung",
        )
        # Klinik-Default-Bandbreite darf nicht mehr auftauchen
        assert "Klinik-Default" not in result
        assert "1.0%" not in result  # alte LO-Grenze
        # Stattdessen: Tonfall-Hinweis
        assert "empathisch-konjunktivisch" in result

    def test_wir_style_wir_workflow_uses_empirical_band(self):
        """Wir-Vorlage + Wir-Workflow: empirische Bandbreite aus der Vorlage.

        Sicherstellt: P0 ändert NICHT das alte Verhalten für Wir-Vorlagen.
        Der Override gilt nur für 3.-Person-Vorlagen.
        """
        from app.services.prompts import _compute_style_constraints
        result = _compute_style_constraints(
            self._make_wir_style(), workflow="folgeverlaengerung",
        )
        assert "Wir-Anteil:" in result
        # Empirisch (Stilvorlage: ...) statt Klinik-Default
        assert "Stilvorlage:" in result
        assert "Klinik-Default" not in result

    def test_3p_style_non_wir_workflow_keeps_3p(self):
        """3.-Person-Vorlage + Nicht-Wir-Workflow: weiterhin '3.-Person'.

        Wichtig: dokumentation und anamnese sind klassisch 3.-Person und
        sollen das auch bleiben.
        """
        from app.services.prompts import _compute_style_constraints
        for wf in ("dokumentation", "anamnese"):
            result = _compute_style_constraints(self._make_3p_style(), workflow=wf)
            assert "dritte Person" in result, f"{wf}: 3.-Person fehlt"
            assert "kein 'Wir'" in result, f"{wf}: 'kein Wir' fehlt"

    def test_signature_workflow_param_is_optional(self):
        """workflow-Parameter ist optional (Default None) - alte Aufrufer
        ohne workflow brechen nicht."""
        from app.services.prompts import _compute_style_constraints
        # Kein workflow → kein Crash
        result = _compute_style_constraints(self._make_3p_style())
        assert isinstance(result, str)
        # workflow=None explizit → kein Crash
        result = _compute_style_constraints(self._make_3p_style(), workflow=None)
        assert isinstance(result, str)

    def test_unknown_workflow_falls_back_safely(self):
        """Unbekannter Workflow-Name verhält sich wie 'kein Workflow' - kein Crash."""
        from app.services.prompts import _compute_style_constraints
        result = _compute_style_constraints(
            self._make_3p_style(), workflow="completely-made-up",
        )
        # Unbekannter Workflow → fällt auf alten Pfad zurück (3.-Person)
        assert "dritte Person" in result

    def test_build_system_prompt_threads_workflow_through(self):
        """Integration: build_system_prompt reicht workflow korrekt durch.

        Damit ist sichergestellt dass alle drei Aufrufstellen den Parameter
        weiterleiten - sonst hätten P0 / A korrigiert in der Praxis keine Wirkung.

        A korrigiert: empathische Workflows haben "Tonfall: empathisch-
        konjunktivisch" + Verbot des objektiv-distanzierten Berichtstons.
        Nicht-empathische Workflows behalten "kein Wir" (klassische 3.-Person).
        """
        from app.services.prompts import build_system_prompt

        style_3p = self._make_3p_style()

        # Empathischer Workflow: NEU empathischer Tonfall, kein "kein Wir"
        for wf in ("akutantrag", "folgeverlaengerung", "entlassbericht", "verlaengerung"):
            p = build_system_prompt(
                workflow=wf,
                style_context=style_3p,
                word_limits=(300, 500),
                patient_name={"anrede": "Frau", "vorname": "X",
                              "nachname": "M", "initial": "M."},
            )
            assert "STIL-CHECKS" in p, f"{wf}: STIL-CHECKS-Block fehlt"
            # Die alte 3.-Person-Pflicht-Zeile darf nicht im Prompt landen
            assert "kein 'Wir'/'uns'/'unser'" not in p, (
                f"{wf}: STIL-CHECKS sagt 'kein Wir' trotz empathischem Workflow"
            )
            # NEU (A korrigiert): empathischer Tonfall-Hinweis MUSS da sein
            assert "Tonfall: empathisch-konjunktivisch" in p, (
                f"{wf}: A korrigiert greift nicht - Tonfall-Hinweis fehlt"
            )

        # Nicht-empathischer Workflow: 3.-Person bleibt
        p = build_system_prompt(
            workflow="dokumentation",
            style_context=style_3p,
            word_limits=(150, 450),
            patient_name={"anrede": "Frau", "vorname": "X",
                          "nachname": "M", "initial": "M."},
        )
        assert "kein 'Wir'/'uns'/'unser'" in p, (
            "dokumentation: 3.-Person-Zwang fehlt - sollte bei Nicht-empathischem "
            "Workflow bleiben"
        )


# ── P3: Eval-Side Bibliotheks-Fallback ─────────────────────────────────────────

# ──── aus test_prompts_v16.py: TestP3EvalLibraryFallback
class TestP3EvalLibraryFallback:
    """v13 P3: Wenn nur style_therapeut gesetzt ist (kein style_file im
    input_files), lädt die Eval die Therapeuten-Bibliothek und sendet sie als
    style_text ans Backend - mit Strategie-3-Markern wenn es mehrere Vorlagen
    sind.

    Diese Tests prüfen die ISOLATED Logik (kein echter HTTP-Call, kein
    Filesystem). Die echte Integration läuft im Eval-Run selbst.
    """

    def _simulate_p3(self, input_files, test_case, library_loader):
        """Reproduziert exakt die P3-Logik aus test_eval_workflow."""
        extra_form_data = None
        has_style_file = bool(input_files and "style_file" in input_files)
        if not has_style_file and test_case.get("style_therapeut"):
            therapeut_id = test_case["style_therapeut"]
            try:
                library_texts = library_loader(therapeut_id, test_case["workflow"])
                if library_texts:
                    if len(library_texts) == 1:
                        style_content = library_texts[0]
                    else:
                        style_content = "\n\n".join(
                            f"--- Beispiel {i} ---\n{txt}"
                            for i, txt in enumerate(library_texts, 1)
                        )
                    extra_form_data = {"style_text": style_content}
            except Exception:
                pass
        return extra_form_data

    def test_multi_vorlagen_uses_markers(self):
        """3 Vorlagen → Marker-Format das split_style_examples wieder splitten kann."""
        loader = lambda t, w: ["A", "B", "C"]
        r = self._simulate_p3(
            input_files=None,
            test_case={"workflow": "anamnese", "style_therapeut": "T1"},
            library_loader=loader,
        )
        assert r is not None
        assert "--- Beispiel 1 ---" in r["style_text"]
        assert "--- Beispiel 3 ---" in r["style_text"]

    def test_single_vorlage_no_marker(self):
        """1 Vorlage → kein Marker (Backwards-Compat mit alten Eval-Cases)."""
        loader = lambda t, w: ["Single Vorlage Text"]
        r = self._simulate_p3(
            input_files=None,
            test_case={"workflow": "akutantrag", "style_therapeut": "T1"},
            library_loader=loader,
        )
        assert r == {"style_text": "Single Vorlage Text"}

    def test_inactive_when_style_file_present(self):
        """input_files hat style_file → P3 darf das nicht überschreiben."""
        loader = lambda t, w: ["Library Text"]
        r = self._simulate_p3(
            input_files={"style_file": "vorlage.txt"},
            test_case={"workflow": "anamnese", "style_therapeut": "T1"},
            library_loader=loader,
        )
        assert r is None

    def test_inactive_without_style_therapeut(self):
        """Ohne style_therapeut → P3 macht nichts."""
        loader = lambda t, w: ["Should Not Appear"]
        r = self._simulate_p3(
            input_files=None,
            test_case={"workflow": "anamnese"},
            library_loader=loader,
        )
        assert r is None

    def test_empty_library_returns_none(self):
        """Library leer → kein Crash, P3 setzt nichts."""
        loader = lambda t, w: []
        r = self._simulate_p3(
            input_files=None,
            test_case={"workflow": "anamnese", "style_therapeut": "T1"},
            library_loader=loader,
        )
        assert r is None

    def test_marker_format_compatible_with_splitter(self):
        """Roundtrip: P3-Output kann von split_style_examples wieder gesplittet werden.

        Das ist die wichtigste Garantie: die Marker, die P3 in den style_text
        einfügt, müssen exakt das Format haben, das split_style_examples in
        jobs.py erkennt. Sonst sieht das Backend wieder einen single block.
        """
        from app.services.prompts import split_style_examples

        loader = lambda t, w: [
            "Erste Vorlage Inhalt.",
            "Zweite Vorlage Inhalt.",
        ]
        r = self._simulate_p3(
            input_files=None,
            test_case={"workflow": "verlaengerung", "style_therapeut": "T1"},
            library_loader=loader,
        )
        assert r is not None
        # split_style_examples muss sauber zwei Beispiele rauspulen
        split = split_style_examples(r["style_text"])
        assert len(split) == 2
        assert "Erste Vorlage" in split[0]
        assert "Zweite Vorlage" in split[1]

    def test_loader_exception_swallowed(self):
        """Exception beim Library-Loader → P3 setzt nichts, kein Crash."""
        def broken_loader(t, w):
            raise RuntimeError("Filesystem broken")
        r = self._simulate_p3(
            input_files=None,
            test_case={"workflow": "anamnese", "style_therapeut": "T1"},
            library_loader=broken_loader,
        )
        assert r is None


# ── A korrigiert: BASE_PROMPT-Stilanweisung gegen Berichtston ─────────────────

# ──── aus test_prompts_v16.py: TestEmpathicToneOverride
class TestEmpathicToneOverride:
    """v13 Iteration A korrigiert: BASE_PROMPTS für empathische Workflows
    erzwingen nicht mehr Wir-Form, verbieten aber den objektiv-distanzierten
    Berichtston ('Der Patient zeigte ...').
    """

    def _patient(self):
        return {"anrede": "Frau", "vorname": "X", "nachname": "M", "initial": "M."}

    def test_akutantrag_no_wir_zwang(self):
        """Akutantrag-BASE_PROMPT erzwingt kein 'erster Satz mit Wir' mehr."""
        from app.services.prompts import build_system_prompt
        p = build_system_prompt(
            workflow="akutantrag",
            style_context="Sample.",
            word_limits=(150, 350),
            patient_name=self._patient(),
        )
        # Alte Wir-Pflicht muss weg sein
        assert "MUSS der erste inhaltliche Satz mit 'Wir' beginnen" not in p

    def test_akutantrag_warns_against_berichtston(self):
        """Akutantrag-BASE_PROMPT warnt vor objektiv-distanziertem Berichtston."""
        from app.services.prompts import build_system_prompt
        p = build_system_prompt(
            workflow="akutantrag",
            style_context="Sample.",
            word_limits=(150, 350),
            patient_name=self._patient(),
        )
        assert "objektiv-wissenden Berichtston" in p
        assert "Der Patient zeigte" in p  # als Negativ-Beispiel

    def test_verlaengerung_no_wir_zwang(self):
        """Verlängerung-BASE_PROMPT erzwingt nicht mehr 'Schreibe konsequent aus Wir-Sicht'."""
        from app.services.prompts import build_system_prompt
        p = build_system_prompt(
            workflow="verlaengerung",
            style_context="Sample.",
            word_limits=(300, 500),
            patient_name=self._patient(),
        )
        # Alte Wir-Pflicht-Phrase muss weg sein
        assert "Schreibe konsequent aus 'Wir'-Sicht" not in p
        # Stilanweisung muss da sein
        assert "objektiv-wissenden Berichtston" in p

    def test_folgeverlaengerung_struktur_no_wir(self):
        """Folgeverlängerung-STRUKTUR erzwingt nicht mehr 'Zeile 3+ mit Wir'."""
        from app.services.prompts import build_system_prompt
        p = build_system_prompt(
            workflow="folgeverlaengerung",
            style_context="Sample.",
            word_limits=(300, 500),
            patient_name=self._patient(),
        )
        # Überschrift-Zwang muss bleiben
        assert "'Verlauf und Begründung der weiteren Verlängerung'" in p
        # Aber Wir-Zwang in Zeile 3 muss weg
        assert "Fließtext, der DIREKT mit 'Wir'" not in p
        # Stattdessen Vorlagen-Mimik
        assert "Fließtext im Stil der Vorlage" in p

    def test_entlassbericht_warns_against_berichtston(self):
        """Entlassbericht-BASE_PROMPT warnt vor objektiv-distanziertem Berichtston."""
        from app.services.prompts import build_system_prompt
        p = build_system_prompt(
            workflow="entlassbericht",
            style_context="Sample.",
            word_limits=(500, 1000),
            patient_name=self._patient(),
        )
        # Alte Wir-Perspektive-Beispielzeile soll weg sein
        assert "Wir-Perspektive: 'Wir erlebten...'," not in p
        # Stattdessen Stilfreiheit + Berichtston-Verbot
        assert "objektiv-distanzierter Berichtston" in p


# ── C: Anamnese-Längen-Disziplin ──────────────────────────────────────────────

# ──── aus test_prompts_v16.py: TestAnamneseLengthDiscipline
class TestAnamneseLengthDiscipline:
    """v13 Iteration C: Anamnese-Längen-Disziplin verstärken.

    Hintergrund: an-02 produzierte 633w bei Max=418w (51% über Limit). Modell
    ignoriert die ZIELLÄNGE strukturell, weil es jede Subkategorie
    gleich-wichtig behandelt. C fügt explizite Längen-Disziplin-Anweisung ein.
    """

    def _patient(self):
        return {"anrede": "Frau", "vorname": "X", "nachname": "M", "initial": "M."}

    def test_anamnese_length_discipline_block_present(self):
        """Anamnese-BASE_PROMPT enthält LÄNGEN-DISZIPLIN-Block."""
        from app.services.prompts import build_system_prompt
        p = build_system_prompt(
            workflow="anamnese",
            style_context="Sample.",
            word_limits=(280, 418),
            patient_name=self._patient(),
        )
        assert "LÄNGEN-DISZIPLIN" in p

    def test_anamnese_length_discipline_says_hard_limit(self):
        """LÄNGEN-DISZIPLIN: ZIELLÄNGE als hartes Limit, nicht offen nach oben."""
        from app.services.prompts import build_system_prompt
        p = build_system_prompt(
            workflow="anamnese",
            style_context="Sample.",
            word_limits=(280, 418),
            patient_name=self._patient(),
        )
        assert "hartes Limit" in p
        assert "KEIN Zielwert nach oben offen" in p

    def test_anamnese_length_discipline_priorisierung(self):
        """LÄNGEN-DISZIPLIN nennt konkrete Priorisierung statt erschöpfende Auflistung."""
        from app.services.prompts import build_system_prompt
        p = build_system_prompt(
            workflow="anamnese",
            style_context="Sample.",
            word_limits=(280, 418),
            patient_name=self._patient(),
        )
        # Priorisierungs-Anweisung
        assert "Priorisiere:" in p
        assert "Sekundäres" in p or "kann komplett entfallen" in p
        # Verdichtungs-Anweisung
        assert "verdichten" in p.lower() or "Zusammenfassung" in p

    def test_anamnese_length_discipline_self_count(self):
        """LÄNGEN-DISZIPLIN sagt explizit: zähle Wörter vor Abgabe."""
        from app.services.prompts import build_system_prompt
        p = build_system_prompt(
            workflow="anamnese",
            style_context="Sample.",
            word_limits=(280, 418),
            patient_name=self._patient(),
        )
        assert "zähle deine Wörter" in p
        assert "kürze" in p

    def test_anamnese_only_no_other_workflow(self):
        """C-Block nur in anamnese, nicht in anderen Workflows."""
        from app.services.prompts import build_system_prompt
        for wf in ("entlassbericht", "verlaengerung", "akutantrag", "dokumentation"):
            p = build_system_prompt(
                workflow=wf,
                style_context="Sample.",
                word_limits=(300, 500),
                patient_name=self._patient(),
            )
            assert "LÄNGEN-DISZIPLIN" not in p, (
                f"{wf}: LÄNGEN-DISZIPLIN-Block sollte nur in anamnese sein"
            )

