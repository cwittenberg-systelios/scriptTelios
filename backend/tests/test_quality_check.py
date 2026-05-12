"""
Unit-Tests fuer app/services/quality_check.py

Geprueft werden alle Einzel-Checks und das Zusammenspiel.
Laeuft ohne Backend-Server, ohne LLM, ohne DB - pure Logik.

Aufruf:
    pytest tests/test_quality_check.py -v
"""
import json
import pytest

from app.services.quality_check import (
    Issue,
    QualityCheckResult,
    StyleAnalyzer,
    run_quality_check,
)
from app.services.quality_specs import WORKFLOW_SPECS, get_spec


# ── Hilfs-Texte ──────────────────────────────────────────────────────────────

def _good_entlassbericht_text() -> str:
    """Genuegend Wortzahl + alle erwarteten Sektionen + saubere Sprache."""
    base = (
        "Frau M. stellt sich vor mit einer mittelgradigen depressiven Episode. "
        "Sie berichtet von Schlafstörungen und Antriebslosigkeit seit mehreren Monaten. "
        "Im Verlauf der Behandlung zeigten sich biographische Belastungen und "
        "konflikthafte Beziehungsdynamiken. Wir erlebten Frau M. als reflexionsfähig "
        "und veränderungsmotiviert. Der Behandlungsverlauf war von kontinuierlicher "
        "Auseinandersetzung mit den eigenen Anteilen geprägt. "
        "Als Empfehlung halten wir eine ambulante Weiterbehandlung für indiziert, "
        "um die erarbeiteten Erkenntnisse zu vertiefen und die Affektregulation "
        "weiter zu stabilisieren. "
    )
    return base * 12  # ca. 700-800 Wörter


def _good_anamnese_text() -> str:
    base = (
        "Frau M. stellt sich vor mit einer depressiven Episode. Der "
        "Vorstellungsanlass liegt in einer Belastungssituation am Arbeitsplatz "
        "kombiniert mit familiären Konflikten. Sie berichtet von Schlafstörungen, "
        "Konzentrationsproblemen und sozialem Rückzug. Wir erlebten Frau M. als "
        "reflektiert und motiviert. Als Ressourcen sind ihre tragfähige Partnerschaft "
        "und ihre berufliche Selbstwirksamkeit zu nennen. "
    )
    return base * 12  # ca. 400 Wörter, sicher zwischen 300 und 700


def _short_text() -> str:
    return "Frau M. war hier. Kurzer Text."


# ── StyleAnalyzer ────────────────────────────────────────────────────────────

class TestStyleAnalyzer:
    def test_handles_empty_text(self):
        a = StyleAnalyzer("")
        assert a.avg_sentence_length == 0.0
        assert a.avg_paragraph_length == 0.0
        assert a.fachbegriff_density == 0.0
        assert a.wir_perspektive_ratio == 0.0

    def test_wir_perspektive_detection(self):
        text = ("Wir haben heute viel besprochen. Unser Eindruck war positiv. "
                "Sie kam pünktlich. Frau M. war engagiert.")
        a = StyleAnalyzer(text)
        # 2 von 4 Sätzen enthalten 'wir'/'unser'
        assert 0.4 <= a.wir_perspektive_ratio <= 0.6

    def test_fachbegriffe_counted(self):
        # "Ressourcen", "Anteile", "Stabilisierung" sind in FACHBEGRIFFE
        text = ("Wir arbeiteten mit den Ressourcen und Anteilen der Klientin. "
                "Die Stabilisierung war ein zentrales Thema im Verlauf der Therapie.")
        a = StyleAnalyzer(text)
        assert a.fachbegriff_density > 0

    def test_to_dict_keys(self):
        a = StyleAnalyzer("Ein kurzer Satz hier. Noch einer dazu.")
        d = a.to_dict()
        assert set(d.keys()) >= {
            "word_count", "sentence_count", "paragraph_count",
            "avg_sentence_length", "avg_paragraph_length",
            "fachbegriff_density", "wir_perspektive_ratio", "direkte_zitate",
        }


# ── Einzel-Checks ueber run_quality_check ────────────────────────────────────

class TestThinkBlocks:
    def test_no_think_block_passes(self):
        r = run_quality_check("dokumentation", _good_anamnese_text())
        assert any("Think-Block" in p for p in r.passed)
        assert not any(i.code == "THINK_BLOCK" for i in r.issues)

    def test_think_block_open_tag_is_critical(self):
        r = run_quality_check("dokumentation", "<think>...</think> Text danach")
        issues = [i for i in r.issues if i.code == "THINK_BLOCK"]
        assert len(issues) == 1
        assert issues[0].severity == "critical"
        assert r.has_critical

    def test_think_block_closes_only_is_critical(self):
        # Nur </think> ohne <think> sollte trotzdem auffallen
        r = run_quality_check("dokumentation", "Text </think> Rest")
        assert any(i.code == "THINK_BLOCK" for i in r.issues)


class TestWordCount:
    def test_too_short_anamnese_yields_warning(self):
        r = run_quality_check("anamnese", _short_text())
        wc_issues = [i for i in r.issues if i.code == "WORD_COUNT_LOW"]
        assert len(wc_issues) == 1
        assert wc_issues[0].severity == "warning"
        assert "300" in wc_issues[0].message

    def test_excessive_text_yields_high_warning(self):
        # Anamnese-Limit: 700w; wir bauen ~1400w
        text = "Frau M. stellt sich vor und berichtet ausführlich. " * 200
        r = run_quality_check("anamnese", text)
        assert any(i.code == "WORD_COUNT_HIGH" for i in r.issues)

    def test_in_range_passes(self):
        r = run_quality_check("anamnese", _good_anamnese_text())
        assert any("Wortanzahl OK" in p for p in r.passed)
        assert not any(i.code.startswith("WORD_COUNT") for i in r.issues)

    def test_repair_hint_contains_target_number(self):
        r = run_quality_check("anamnese", _short_text())
        wc_issue = next(i for i in r.issues if i.code == "WORD_COUNT_LOW")
        assert "300" in wc_issue.repair_hint
        # Soll explizit verbieten zu erfinden
        assert "erfinden" in wc_issue.repair_hint.lower()


class TestForbiddenPatterns:
    def test_markdown_stars_warning(self):
        r = run_quality_check("anamnese", _good_anamnese_text() + " **fett**")
        issues = [i for i in r.issues
                  if i.code == "FORBIDDEN_PATTERN" and "**" in i.message]
        assert len(issues) == 1
        assert issues[0].severity == "warning"

    def test_placeholder_remnant_critical(self):
        # 'die Klientin/der Klient' ist Platzhalter-Rest -> critical
        text = _good_anamnese_text() + " die Klientin/der Klient zeigte ..."
        r = run_quality_check("anamnese", text)
        critical = [i for i in r.issues
                    if i.code == "FORBIDDEN_PATTERN" and i.severity == "critical"]
        assert len(critical) >= 1
        assert r.has_critical

    def test_befund_marker_critical(self):
        r = run_quality_check("anamnese", _good_anamnese_text() + " ###BEFUND### Rest")
        assert r.has_critical
        assert any(i.code == "FORBIDDEN_PATTERN" and "###BEFUND###" in i.message
                   for i in r.issues)


class TestRequiredKeywords:
    def test_synonym_match_passes(self):
        # "behandlungsverlauf" als Keyword - 'im verlauf' ist Synonym
        text = ("Im Verlauf der Therapie zeigten sich positive Entwicklungen. "
                "Wir empfehlen eine ambulante Weiterbehandlung. " * 30)
        r = run_quality_check("entlassbericht", text)
        # behandlungsverlauf + empfehlung sollten beide ueber Synonyme matchen
        assert not any(i.code == "MISSING_KEYWORD" and "Behandlungsverlauf" in i.message
                       for i in r.issues)

    def test_missing_keyword_yields_warning(self):
        # Anamnese erwartet 'Vorstellungsanlass' - lassen wir komplett weg
        text = "Eine Patientin war in der Klinik. " * 100
        r = run_quality_check("anamnese", text)
        missing = [i for i in r.issues if i.code == "MISSING_KEYWORD"]
        assert len(missing) >= 1
        for i in missing:
            assert i.severity == "warning"
            assert i.repair_hint  # nicht leer


class TestForbiddenNames:
    def test_clean_text_passes(self):
        r = run_quality_check("entlassbericht", _good_entlassbericht_text(),
                              forbidden_names=["Schmidt", "Müller"])
        assert not any(i.code == "FORBIDDEN_NAME" for i in r.issues)

    def test_name_in_text_is_critical(self):
        text = "Frau Schmidt war in Behandlung. " + _good_entlassbericht_text()
        r = run_quality_check("entlassbericht", text,
                              forbidden_names=["Schmidt"])
        crits = [i for i in r.issues if i.code == "FORBIDDEN_NAME"]
        assert len(crits) == 1
        assert crits[0].severity == "critical"
        assert r.has_critical
        # Hint warnt explizit vor Datenschutz
        assert "Datenschutz" in crits[0].repair_hint or "datenschutz" in crits[0].repair_hint.lower()


# ── Score-Berechnung & is_passing ────────────────────────────────────────────

class TestScoreAndPassing:
    def test_score_zero_to_one_range(self):
        r = run_quality_check("anamnese", _good_anamnese_text())
        assert 0.0 <= r.score <= 1.0

    def test_clean_text_is_passing(self):
        r = run_quality_check("entlassbericht", _good_entlassbericht_text())
        assert r.is_passing
        assert not r.has_critical

    def test_critical_blocks_passing_even_with_high_score(self):
        # Sehr guter Text aber mit einem Critical-Pattern
        text = _good_anamnese_text() + " die Klientin/der Klient zeigte..."
        r = run_quality_check("anamnese", text)
        assert r.has_critical
        # Auch wenn Score evtl. >0.7 ist: is_passing muss False sein
        assert not r.is_passing

    def test_low_score_blocks_passing(self):
        r = run_quality_check("anamnese", _short_text())
        # Kurz + verfehlte Keywords + Sektionen -> Score wird unter 0.7 fallen
        assert not r.is_passing


# ── Serialisierung ───────────────────────────────────────────────────────────

class TestSerialization:
    def test_to_dict_is_json_serializable(self):
        r = run_quality_check("anamnese", _good_anamnese_text())
        d = r.to_dict()
        # darf nicht werfen
        s = json.dumps(d)
        assert isinstance(s, str)

    def test_to_dict_has_expected_keys(self):
        r = run_quality_check("entlassbericht", _good_entlassbericht_text())
        d = r.to_dict()
        assert set(d.keys()) >= {
            "workflow", "word_count", "score", "is_passing",
            "has_critical", "passed_count", "passed", "issues",
        }

    def test_issue_to_dict_has_all_fields(self):
        r = run_quality_check("anamnese", _short_text())
        for issue_dict in r.to_dict()["issues"]:
            assert set(issue_dict.keys()) == {
                "severity", "code", "message", "repair_hint"
            }
            assert issue_dict["severity"] in ("critical", "warning", "info")


# ── Workflow-Specs ───────────────────────────────────────────────────────────

class TestWorkflowSpecs:
    def test_all_known_workflows_have_specs(self):
        # Konsistenz: Workflows aus quality_specs.py decken die UI-Workflows ab
        expected = {"dokumentation", "anamnese", "befund", "verlaengerung",
                    "folgeverlaengerung", "akutantrag", "entlassbericht"}
        assert expected.issubset(set(WORKFLOW_SPECS.keys()))

    def test_unknown_workflow_returns_empty_spec(self):
        assert get_spec("nicht_existent") == {}

    def test_unknown_workflow_does_not_crash(self):
        # Mit unbekanntem Workflow: nur Think-Block-Check, sonst leer
        r = run_quality_check("nicht_existent", "Beliebiger Text ohne think.")
        assert r.workflow == "nicht_existent"
        # Es darf trotzdem ein Result entstehen
        assert isinstance(r.score, float)


# ── Edge Cases ───────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_text_handled(self):
        r = run_quality_check("anamnese", "")
        assert r.word_count == 0
        # Word-Count-Check sollte trotzdem feuern
        assert any(i.code == "WORD_COUNT_LOW" for i in r.issues)

    def test_none_text_treated_as_empty(self):
        # Falls jobs.py mal None durchschiebt - darf nicht crashen
        r = run_quality_check("anamnese", None)  # type: ignore[arg-type]
        assert r.word_count == 0

    def test_custom_spec_overrides_workflow_default(self):
        # custom_spec ueberschreibt: hier 50-100 statt 300-700
        from app.services.quality_specs import WorkflowSpec
        custom: WorkflowSpec = {"min_words": 50, "max_words": 100}
        text = " ".join(["wort"] * 75)
        r = run_quality_check("anamnese", text, custom_spec=custom)
        assert not any(i.code == "WORD_COUNT_LOW" for i in r.issues)
        assert not any(i.code == "WORD_COUNT_HIGH" for i in r.issues)

    def test_style_metrics_optional(self):
        r = run_quality_check("anamnese", _good_anamnese_text(),
                              include_style_metrics=False)
        assert r.style_metrics is None
