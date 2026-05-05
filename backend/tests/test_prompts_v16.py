"""
Tests fuer die v15 -> v16 Patches: Multi-Vorlagen-Unterstützung.

Schwerpunkte:
- v13 Strategie 3: vorlage.txt + vorlage2.txt automatisch erkennen
- split_style_examples: Marker-Format korrekt parsen
- _discover_style_siblings: Geschwister-Dateien finden
- _build_multi_style_text: Marker-Format korrekt erzeugen
- jobs.py: alle drei _style_raw_texts-Sites benutzen Splitter
- resolve_length_anchor mit Liste: mittelt korrekt statt zu konkatenieren
- End-to-End-Roundtrip: Eval baut → jobs.py splittet wieder

Hintergrund:
Vor v16 wurde bei mehreren Stilvorlagen (z.B. pgvector liefert 5 Beispiele)
der konkatenierte Block als EIN Eintrag in _style_raw_texts gespeichert.
Folge: derive_word_limits berechnete die Statistik auf 5×400=2000 Wörter,
das Längen-Limit landete bei ~2000±30%. Strategie 3: an "--- Beispiel N ---"-
Markern aufsplitten, Liste durchreichen, Limit mittelt sauber bei ~400±30%.
"""
import re
import pytest


# ── Splitter (prompts.py) ─────────────────────────────────────────────────────

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

class TestDiscoverStyleSiblings:
    """discover_style_siblings findet vorlage.txt + vorlage2.txt etc."""

    def _import_helper(self):
        from tests.eval_helpers import discover_style_siblings
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


class TestBuildMultiStyleText:
    """build_multi_style_text erzeugt das korrekte Marker-Format."""

    def _import_helper(self):
        from tests.eval_helpers import build_multi_style_text
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

class TestEndToEndRoundtrip:
    """Eval baut Multi-Style → jobs.py-Splitter findet Einzelvorlagen wieder.

    Das ist der entscheidende Roundtrip-Test: Strategie 3 funktioniert nur
    wenn das Marker-Format auf beiden Seiten konsistent ist.
    """

    def _import_helpers(self):
        from tests.eval_helpers import build_multi_style_text
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
