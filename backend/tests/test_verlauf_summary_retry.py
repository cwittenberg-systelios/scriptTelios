"""
Unit-Tests fuer die Retry-Logik bei critical Halluzinations-Signalen
(v19.2 Schritt 4).
"""
import pytest
from unittest.mock import AsyncMock, patch

from app.services.verlauf_summary import summarize_verlauf


def _result(text: str, words: int | None = None) -> dict:
    if words is None:
        words = len(text.split())
    return {
        "text": text,
        "model_used": "ollama/qwen3:32b",
        "token_count": words,
        "telemetry": {"think_ratio": 0.0, "tokens_hit_cap": False},
        "duration_s": 5.0,
    }


def _faithful(words: int = 2000) -> str:
    """Saubere Summary ohne Halluzinations-Signale."""
    base = (
        "### Sitzungsübersicht\n"
        "Mehrere Einzelgespraeche.\n\n"
        "### Bearbeitete Themen\n"
        "Selbstwert und Beziehungsmuster.\n\n"
        "### Therapeutische Interventionen\n"
        "Klinische Gespraeche.\n\n"
        "### Beobachtete Entwicklung\n"
        "Stabilisierend."
    )
    current = len(base.split())
    if current < words:
        base += "\n\n" + " ".join(["fuelltext"] * (words - current))
    return base


def _with_erfundene_icds(words: int = 2000) -> str:
    """Summary mit erfundenen ICD-Codes → critical Issue."""
    base = (
        "### Sitzungsübersicht\n"
        "Drei Sitzungen.\n\n"
        "### Bearbeitete Themen\n"
        "Diagnostisch wurde F33.2 und F41.1 eingeordnet (im Source nicht "
        "explizit so genannt).\n\n"
        "### Therapeutische Interventionen\n"
        "Klinische Gespraeche.\n\n"
        "### Beobachtete Entwicklung\n"
        "Stabilisierend."
    )
    current = len(base.split())
    if current < words:
        base += "\n\n" + " ".join(["fuelltext"] * (words - current))
    return base


class TestRetryTriggering:
    @pytest.mark.asyncio
    async def test_retry_triggered_on_critical_issue(self):
        """Erster Pass hat erfundene ICDs → Retry wird gestartet."""
        verlauf = "Patient kommt zur Aufnahme. Keine ICD-Codes hier."
        bad = _result(_with_erfundene_icds(2000), words=2000)
        good = _result(_faithful(2000), words=2000)

        # Erster Aufruf: bad. Zweiter Aufruf (Retry): good.
        mock = AsyncMock(side_effect=[bad, good])

        with patch("app.services.llm.generate_text", new=mock):
            result = await summarize_verlauf(
                verlauf_text=verlauf,
                workflow="verlaengerung",
                target_words=2000,
            )

        assert mock.call_count == 2
        assert result["retry_used"] is True
        # Sauberes Retry-Result → nicht degraded
        assert result["degraded"] is False
        # Die finale Summary darf keine erfundenen ICDs mehr enthalten
        assert "F33.2" not in result["summary"]

    @pytest.mark.asyncio
    async def test_retry_not_triggered_on_medium_issue(self):
        """Medium-Issues (Zitat-Wendung, implausible Anzahl) → KEIN Retry."""
        verlauf = "Patient hat 2 Sitzungen besucht."
        # Summary mit Direkt-Zitat-Wendung die im Source nicht steht (medium)
        text = _faithful(2000)
        # Direktes Zitat-Pattern einbauen
        bad_medium = text + '\n\nPatient sagte: "Ich bin am Ende."'
        bad = _result(bad_medium, words=2000)

        mock = AsyncMock(return_value=bad)

        with patch("app.services.llm.generate_text", new=mock):
            result = await summarize_verlauf(
                verlauf_text=verlauf,
                workflow="verlaengerung",
                target_words=2000,
            )

        # Nur 1 Aufruf — kein Retry bei medium
        assert mock.call_count == 1
        assert result["retry_used"] is False
        # Medium-Issue muss aber in result["issues"] auftauchen
        issue_types = [i["type"] for i in result["issues"]]
        assert "wortlaut_halluzination" in issue_types

    @pytest.mark.asyncio
    async def test_retry_not_triggered_on_clean_first_pass(self):
        """Erster Pass sauber → kein Retry, keine issues."""
        verlauf = "Klinischer Verlauf ohne Auffaelligkeiten."
        good = _result(_faithful(2000), words=2000)
        mock = AsyncMock(return_value=good)

        with patch("app.services.llm.generate_text", new=mock):
            result = await summarize_verlauf(
                verlauf_text=verlauf,
                workflow="verlaengerung",
                target_words=2000,
            )

        assert mock.call_count == 1
        assert result["retry_used"] is False
        assert result["degraded"] is False
        # Keine critical/high Issues
        criticals = [i for i in result["issues"] if i["severity"] == "critical"]
        highs = [i for i in result["issues"] if i["severity"] == "high"]
        assert criticals == []
        assert highs == []


class TestRetryFailureKeepsOriginalAsDegraded:
    @pytest.mark.asyncio
    async def test_retry_auch_kaputt_degraded_true(self):
        """Beide Versuche haben critical ICD-Halluzinationen → degraded=True."""
        verlauf = "Patient kommt zur Aufnahme. Keine ICDs."
        bad1 = _result(_with_erfundene_icds(2000), words=2000)
        bad2 = _result(_with_erfundene_icds(2000), words=2000)  # auch kaputt

        mock = AsyncMock(side_effect=[bad1, bad2])

        with patch("app.services.llm.generate_text", new=mock):
            result = await summarize_verlauf(
                verlauf_text=verlauf,
                workflow="verlaengerung",
                target_words=2000,
            )

        assert mock.call_count == 2
        assert result["retry_used"] is True
        assert result["degraded"] is True
        # Original-Summary wird zurueckgegeben (Aufrufer entscheidet ob Fallback)
        assert result["summary_word_count"] > 0

    @pytest.mark.asyncio
    async def test_retry_leerer_output_degraded(self):
        """Retry liefert leeren Text → degraded=True."""
        verlauf = "Patient kommt zur Aufnahme. Keine ICDs."
        bad1 = _result(_with_erfundene_icds(2000), words=2000)
        bad2 = _result("", words=0)  # leerer Retry

        mock = AsyncMock(side_effect=[bad1, bad2])

        with patch("app.services.llm.generate_text", new=mock):
            result = await summarize_verlauf(
                verlauf_text=verlauf,
                workflow="verlaengerung",
                target_words=2000,
            )

        assert mock.call_count == 2
        assert result["retry_used"] is True
        assert result["degraded"] is True

    @pytest.mark.asyncio
    async def test_retry_wirft_exception_degraded(self):
        """Retry-Call wirft Exception → Original behalten, degraded=True."""
        verlauf = "Patient kommt zur Aufnahme. Keine ICDs."
        bad1 = _result(_with_erfundene_icds(2000), words=2000)

        mock = AsyncMock(side_effect=[bad1, RuntimeError("Ollama down")])

        with patch("app.services.llm.generate_text", new=mock):
            result = await summarize_verlauf(
                verlauf_text=verlauf,
                workflow="verlaengerung",
                target_words=2000,
            )

        assert mock.call_count == 2
        assert result["retry_used"] is True
        assert result["degraded"] is True


class TestRetryUsesLowerTemperature:
    @pytest.mark.asyncio
    async def test_retry_call_hat_niedrigere_temperatur(self):
        """Retry-Call muss temperature_override=0.1 setzen (noch niedriger)."""
        verlauf = "Patient kommt zur Aufnahme."
        bad = _result(_with_erfundene_icds(2000), words=2000)
        good = _result(_faithful(2000), words=2000)

        call_args_list = []

        async def _capture(**kwargs):
            call_args_list.append(kwargs)
            return bad if len(call_args_list) == 1 else good

        with patch("app.services.llm.generate_text", new=_capture):
            await summarize_verlauf(
                verlauf_text=verlauf,
                workflow="verlaengerung",
                target_words=2000,
            )

        assert len(call_args_list) == 2
        # Erster Call: 0.2
        assert call_args_list[0]["temperature_override"] == 0.2
        # Retry-Call: 0.1 (noch niedriger)
        assert call_args_list[1]["temperature_override"] == 0.1

    @pytest.mark.asyncio
    async def test_retry_system_prompt_erwaehnt_issues(self):
        """Retry-System-Prompt muss die gefundenen Issues namentlich erwaehnen."""
        verlauf = "Patient kommt zur Aufnahme."
        bad = _result(_with_erfundene_icds(2000), words=2000)
        good = _result(_faithful(2000), words=2000)

        call_args_list = []

        async def _capture(**kwargs):
            call_args_list.append(kwargs)
            return bad if len(call_args_list) == 1 else good

        with patch("app.services.llm.generate_text", new=_capture):
            await summarize_verlauf(
                verlauf_text=verlauf,
                workflow="verlaengerung",
                target_words=2000,
            )

        retry_system = call_args_list[1]["system_prompt"]
        # Retry-Prompt erwaehnt die gefundenen Probleme
        assert "icd_halluzination" in retry_system
        # Und mahnt explizit zur Vermeidung
        assert "Vermeide" in retry_system or "vermeide" in retry_system
