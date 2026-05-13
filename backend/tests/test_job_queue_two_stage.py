"""
Integrations-Tests fuer die Two-Stage-Pipeline-Integration in jobs.py (v19.2 Schritt 5).

Da die Stage-1-Aufruflogik tief im _run-Closure von jobs.py sitzt, testen
wir hier die Komponenten isoliert + die Aktivierungsregeln:

  1. Workflow-Whitelist: _STAGE1_WORKFLOWS enthaelt die richtigen Keys
  2. Min-Words-Schwelle: _STAGE1_MIN_WORDS sinnvoll gesetzt
  3. Audit-Bundle-Form: Felder, Datentypen
  4. Fallback-Verhalten: bei Exception keine Crashes
"""
import pytest
from unittest.mock import AsyncMock, patch

from app.services.verlauf_summary import summarize_verlauf


# ─────────────────────────────────────────────────────────────────────────────
# Test-Helper: importiere die jobs.py-Konstanten OHNE FastAPI-Boilerplate
# ─────────────────────────────────────────────────────────────────────────────


def test_stage1_workflow_whitelist_korrekt():
    """Whitelist enthaelt die drei Workflows die Stage 1 brauchen."""
    # Direktimport der Konstante aus dem Modul ohne FastAPI-App-Start
    import importlib.util
    from pathlib import Path

    # Wir parsen jobs.py auf Konstanten-Definitionen
    src = (Path(__file__).parent / "jobs.py").read_text()

    assert '_STAGE1_WORKFLOWS = {"verlaengerung", "folgeverlaengerung", "entlassbericht"}' in src

    # Sanity: andere Workflows sind NICHT in der Whitelist
    assert '"anamnese"' not in src.split("_STAGE1_WORKFLOWS")[1].split("}")[0]
    assert '"akutantrag"' not in src.split("_STAGE1_WORKFLOWS")[1].split("}")[0]
    assert '"dokumentation"' not in src.split("_STAGE1_WORKFLOWS")[1].split("}")[0]


def test_stage1_min_words_schwelle_sinnvoll():
    """Min-Words-Schwelle ist auf einem realistischen Wert (1000–3000)."""
    from pathlib import Path
    src = (Path(__file__).parent / "jobs.py").read_text()

    import re
    m = re.search(r"_STAGE1_MIN_WORDS\s*=\s*(\d+)", src)
    assert m, "_STAGE1_MIN_WORDS nicht gefunden"
    val = int(m.group(1))
    assert 500 <= val <= 5000, f"Min-Words-Schwelle implausibel: {val}"


def test_stage1_aktivierungs_bedingungen_kommen_im_code_vor():
    """Pipeline-Block prueft auf alle drei Bedingungen explizit."""
    from pathlib import Path
    src = (Path(__file__).parent / "jobs.py").read_text()

    # Die drei Aktivierungs-Bedingungen sind als && verkettet
    assert "_stage1_enabled" in src
    assert "workflow in _STAGE1_WORKFLOWS" in src
    assert "_STAGE1_MIN_WORDS" in src


def test_stage1_audit_bundle_felder_im_code():
    """Audit-Bundle hat alle dokumentierten Felder."""
    from pathlib import Path
    src = (Path(__file__).parent / "jobs.py").read_text()

    expected_fields = [
        "applied", "raw_word_count", "summary_word_count",
        "compression_ratio", "duration_s", "telemetry",
        "retry_used", "retry_telemetry", "degraded",
        "issues", "target_words", "fallback_reason",
    ]
    for f in expected_fields:
        # Im Code als String-Key vor :
        assert f'"{f}":' in src, f"Audit-Feld '{f}' fehlt im Bundle"


def test_stage1_audit_landet_im_result_dict():
    """Stage-1-Audit wird im finalen Return-Dict des Jobs durchgereicht."""
    from pathlib import Path
    src = (Path(__file__).parent / "jobs.py").read_text()
    # Im Return-Dict (am Ende von _run) muss verlauf_summary_audit drin sein
    assert '"verlauf_summary_audit": _stage1_audit' in src


# ─────────────────────────────────────────────────────────────────────────────
# Verhaltens-Tests fuer summarize_verlauf im Pipeline-Kontext
# Diese testen das gleiche Verhalten wie test_verlauf_summary_service.py,
# aber mit dem Pipeline-typischen Workflow-Mix (verlaengerung/folge/entlass).
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stage1_call_form_fuer_verlaengerung():
    """Stage-1-Aufruf fuer verlaengerung erzeugt erwarteten Audit-Aufbau."""
    fake_result = {
        "text": "### Sitzungsübersicht\nDrei Gespraeche.\n\n"
                "### Bearbeitete Themen\nSelbstwert.\n\n"
                "### Therapeutische Interventionen\nIFS.\n\n"
                "### Beobachtete Entwicklung\nStabilisierend." + (" wort" * 2000),
        "model_used": "ollama/qwen3:32b",
        "token_count": 2000,
        "telemetry": {"think_ratio": 0.0, "tokens_hit_cap": False},
        "duration_s": 30.0,
    }

    with patch(
        "app.services.llm.generate_text",
        new=AsyncMock(return_value=fake_result),
    ):
        result = await summarize_verlauf(
            verlauf_text=" ".join(["wort"] * 8000),
            workflow="verlaengerung",
            patient_initial="M.",
            target_words=2000,
        )

    # Alle Audit-Felder vorhanden
    for f in ["summary", "raw_word_count", "summary_word_count",
              "compression_ratio", "duration_s", "telemetry",
              "issues", "retry_used", "degraded"]:
        assert f in result, f"Feld {f} fehlt im Stage-1-Result"

    # Sanity
    assert result["raw_word_count"] == 8000
    assert result["summary_word_count"] >= 100
    assert result["retry_used"] is False
    assert result["degraded"] is False


@pytest.mark.asyncio
async def test_stage1_call_form_fuer_entlassbericht():
    """Entlassbericht-Pfad funktioniert genauso wie Verlaengerung."""
    fake_result = {
        "text": "### Sitzungsübersicht\nAcht Sitzungen.\n\n"
                "### Bearbeitete Themen\nTrauma.\n\n"
                "### Therapeutische Interventionen\nGespraeche.\n\n"
                "### Beobachtete Entwicklung\nGesamtbogen vom Schock zur "
                "Stabilisierung." + (" wort" * 2000),
        "model_used": "ollama/qwen3:32b",
        "token_count": 2000,
        "telemetry": {"think_ratio": 0.0},
        "duration_s": 25.0,
    }

    calls = []

    async def _cap(**kwargs):
        calls.append(kwargs)
        return fake_result

    with patch("app.services.llm.generate_text", new=_cap):
        result = await summarize_verlauf(
            verlauf_text=" ".join(["wort"] * 8000),
            workflow="entlassbericht",
            target_words=2000,
        )

    # Entlassbericht-Hint im system_prompt
    assert "Entlassbericht" in calls[0]["system_prompt"]
    assert "Wendepunkte" in calls[0]["system_prompt"]
    assert result["summary_word_count"] >= 100


@pytest.mark.asyncio
async def test_stage1_call_form_fuer_folgeverlaengerung():
    """Folgeverlaengerung-Pfad hat eigenen Hint."""
    fake_result = {
        "text": "### Sitzungsübersicht\nFortsetzung.\n\n"
                "### Bearbeitete Themen\nNeue Themen seit letztem Antrag.\n\n"
                "### Therapeutische Interventionen\nGespraeche.\n\n"
                "### Beobachtete Entwicklung\nFestigung." + (" wort" * 2000),
        "model_used": "ollama/qwen3:32b",
        "token_count": 2000,
        "telemetry": {"think_ratio": 0.0},
        "duration_s": 25.0,
    }

    calls = []

    async def _cap(**kwargs):
        calls.append(kwargs)
        return fake_result

    with patch("app.services.llm.generate_text", new=_cap):
        result = await summarize_verlauf(
            verlauf_text=" ".join(["wort"] * 8000),
            workflow="folgeverlaengerung",
            target_words=2000,
        )

    # Folgeverlaengerung-Hint im system_prompt
    assert "Folgeverlängerung" in calls[0]["system_prompt"]
    assert result["summary_word_count"] >= 100


# ─────────────────────────────────────────────────────────────────────────────
# Fallback-Verhalten: wenn Stage 1 fehlschlaegt darf der Workflow nicht crashen
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stage1_exception_wird_vom_aufrufer_gefangen_simulation():
    """summarize_verlauf wirft RuntimeError - der Pipeline-Code muss das fangen.
    Wir simulieren das hier: Aufrufer faengt den Error ab und kann den
    Original-Verlauf weiterverwenden."""
    short_summary = {
        "text": "zu kurz",  # 2 Woerter, unter 40% Threshold
        "model_used": "ollama/qwen3:32b",
        "token_count": 2,
        "telemetry": {},
        "duration_s": 5.0,
    }

    with patch(
        "app.services.llm.generate_text",
        new=AsyncMock(return_value=short_summary),
    ):
        # Aufrufer muss RuntimeError fangen
        fallback_used = False
        try:
            await summarize_verlauf(
                verlauf_text=" ".join(["wort"] * 5000),
                workflow="verlaengerung",
                target_words=2000,
            )
        except RuntimeError:
            # Genau das passiert in jobs.py: Fallback auf Roh-Verlauf
            fallback_used = True

        assert fallback_used


@pytest.mark.asyncio
async def test_stage1_audit_struktur_im_happy_path():
    """Audit-Bundle hat im Happy-Path alle Felder mit korrekten Typen."""
    fake_result = {
        "text": "### Sitzungsübersicht\nText.\n\n" + (" wort" * 2000),
        "model_used": "ollama/qwen3:32b",
        "token_count": 2000,
        "telemetry": {"think_ratio": 0.05, "tokens_hit_cap": False},
        "duration_s": 15.0,
    }

    with patch(
        "app.services.llm.generate_text",
        new=AsyncMock(return_value=fake_result),
    ):
        result = await summarize_verlauf(
            verlauf_text=" ".join(["wort"] * 5000),
            workflow="verlaengerung",
            target_words=2000,
        )

    # Typ-Pruefungen analog zum Audit-Bundle in jobs.py
    assert isinstance(result["raw_word_count"], int)
    assert isinstance(result["summary_word_count"], int)
    assert isinstance(result["compression_ratio"], float)
    assert isinstance(result["duration_s"], float)
    assert isinstance(result["telemetry"], dict)
    assert isinstance(result["issues"], list)
    assert isinstance(result["retry_used"], bool)
    assert isinstance(result["degraded"], bool)
