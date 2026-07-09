"""v19.5.2: Tests fuer die Verdichtungsmodell-Aufloesung (resolve_summary_model).

Sichert ab, dass ein nicht gepulltes/veraltetes SUMMARY_MODEL keinen Job mehr
mit Ollama-404 kippt, sondern zur Laufzeit graceful auf ein vorhandenes Modell
zurueckfaellt (Ursache des anamnese-404: stale OLLAMA_MODEL=qwen3:32b).

Laeuft ohne Ollama/DB/Whisper: _list_available_models wird gemockt.
"""
import pytest


def _avail(*names: str) -> set[str]:
    """Baut die Verfuegbarkeitsmenge wie _list_available_models: jeder Tag
    doppelt (voll + ohne :suffix)."""
    s: set[str] = set()
    for n in names:
        s.add(n)
        s.add(n.split(":")[0])
    return s


def _patch_available(monkeypatch, available: set[str]):
    async def _fake():
        return available
    monkeypatch.setattr("app.services.llm._list_available_models", _fake)


class TestModelIsAvailable:
    def test_voller_tag_matcht(self):
        from app.services.llm import _model_is_available
        av = _avail("mistral-small3.2:latest")
        assert _model_is_available("mistral-small3.2:latest", av) is True

    def test_nackter_name_matcht_vollen_tag(self):
        from app.services.llm import _model_is_available
        av = _avail("mistral-small3.2:latest")
        assert _model_is_available("mistral-small3.2", av) is True

    def test_fehlendes_modell(self):
        from app.services.llm import _model_is_available
        av = _avail("mistral-small3.2:latest", "gemma4:31b")
        assert _model_is_available("qwen3:32b", av) is False

    def test_leere_liste_ist_optimistisch(self):
        from app.services.llm import _model_is_available
        # Kein Check moeglich (Ollama down) -> optimistisch True (Alt-Verhalten)
        assert _model_is_available("irgendwas", set()) is True

    def test_ollama_praefix_wird_gestrippt(self):
        from app.services.llm import _model_is_available
        av = _avail("mistral-small3.2:latest")
        assert _model_is_available("ollama/mistral-small3.2", av) is True


class TestResolveSummaryModel:
    @pytest.mark.asyncio
    async def test_preferred_verfuegbar(self, monkeypatch):
        from app.services import llm
        monkeypatch.setattr("app.services.llm.settings.SUMMARY_MODEL", "mistral-small3.2", raising=False)
        monkeypatch.setattr("app.services.llm.settings.OLLAMA_MODEL", "mistral-small3.2", raising=False)
        _patch_available(monkeypatch, _avail("mistral-small3.2:latest", "gemma4:31b"))
        assert await llm.resolve_summary_model() == "mistral-small3.2"

    @pytest.mark.asyncio
    async def test_preferred_fehlt_fallback_auf_ollama_model(self, monkeypatch):
        from app.services import llm
        # SUMMARY_MODEL retired/nicht gepullt, OLLAMA_MODEL vorhanden
        monkeypatch.setattr("app.services.llm.settings.SUMMARY_MODEL", "qwen3:32b", raising=False)
        monkeypatch.setattr("app.services.llm.settings.OLLAMA_MODEL", "mistral-small3.2", raising=False)
        _patch_available(monkeypatch, _avail("mistral-small3.2:latest", "gemma4:31b"))
        assert await llm.resolve_summary_model() == "mistral-small3.2"

    @pytest.mark.asyncio
    async def test_beide_fehlen_fallback_auf_beliebiges_chat_modell(self, monkeypatch):
        from app.services import llm
        monkeypatch.setattr("app.services.llm.settings.SUMMARY_MODEL", "qwen3:32b", raising=False)
        monkeypatch.setattr("app.services.llm.settings.OLLAMA_MODEL", "qwen2.5:32b", raising=False)
        # Nur gemma + embed geladen -> muss gemma waehlen (embed wird uebersprungen)
        _patch_available(monkeypatch, _avail("gemma4:31b", "nomic-embed-text:latest"))
        assert await llm.resolve_summary_model() == "gemma4:31b"

    @pytest.mark.asyncio
    async def test_nur_embed_modell_kein_chat_fallback_gibt_preferred_zurueck(self, monkeypatch):
        from app.services import llm
        monkeypatch.setattr("app.services.llm.settings.SUMMARY_MODEL", "qwen3:32b", raising=False)
        monkeypatch.setattr("app.services.llm.settings.OLLAMA_MODEL", "qwen2.5:32b", raising=False)
        _patch_available(monkeypatch, _avail("nomic-embed-text:latest"))
        # Kein Chat-Modell -> letzter Ausweg: konfiguriertes preferred (Aufrufer
        # bekommt ggf. aussagekraeftigen Fehler, aber kein stiller Fehlgriff)
        assert await llm.resolve_summary_model() == "qwen3:32b"

    @pytest.mark.asyncio
    async def test_ollama_down_leere_liste_gibt_preferred(self, monkeypatch):
        from app.services import llm
        monkeypatch.setattr("app.services.llm.settings.SUMMARY_MODEL", "mistral-small3.2", raising=False)
        monkeypatch.setattr("app.services.llm.settings.OLLAMA_MODEL", "mistral-small3.2", raising=False)
        _patch_available(monkeypatch, set())  # /api/tags nicht abrufbar
        assert await llm.resolve_summary_model() == "mistral-small3.2"


class TestCheckSummaryModelAvailable:
    @pytest.mark.asyncio
    async def test_verfuegbar_true(self, monkeypatch):
        from app.services import llm
        monkeypatch.setattr("app.services.llm.settings.SUMMARY_MODEL", "mistral-small3.2", raising=False)
        monkeypatch.setattr("app.services.llm.settings.OLLAMA_MODEL", "mistral-small3.2", raising=False)
        _patch_available(monkeypatch, _avail("mistral-small3.2:latest"))
        assert await llm.check_summary_model_available() is True

    @pytest.mark.asyncio
    async def test_nicht_geladen_false(self, monkeypatch):
        from app.services import llm
        monkeypatch.setattr("app.services.llm.settings.SUMMARY_MODEL", "qwen3:32b", raising=False)
        monkeypatch.setattr("app.services.llm.settings.OLLAMA_MODEL", "mistral-small3.2", raising=False)
        _patch_available(monkeypatch, _avail("mistral-small3.2:latest"))
        # konfiguriertes fehlt -> Resolver liefert Fallback -> Check meldet False
        assert await llm.check_summary_model_available() is False


class TestEnsureGenerationModel:
    """v19.5.3: Client-gewaehltes Generierungsmodell gegen geladene Ollama-
    Modelle validieren. Schuetzt vor stale localStorage-Modellen
    ('systelios_model=qwen3:32b') -> kein Ollama-404 mehr im Haupt-Call."""

    @pytest.mark.asyncio
    async def test_client_modell_verfuegbar_wird_genommen(self, monkeypatch):
        from app.services import llm
        _patch_available(monkeypatch, _avail("mistral-small3.2:latest", "gemma4:31b"))
        got = await llm.ensure_generation_model("mistral-small3.2:latest", "anamnese")
        assert got == "mistral-small3.2:latest"

    @pytest.mark.asyncio
    async def test_stale_client_modell_faellt_auf_workflow_default(self, monkeypatch):
        from app.services import llm
        # anamnese -> WORKFLOW_MODEL = mistral-small3.2 (vorhanden); qwen NICHT.
        _patch_available(monkeypatch, _avail("mistral-small3.2:latest", "gemma4:31b"))
        got = await llm.ensure_generation_model("qwen3:32b", "anamnese")
        assert got == "mistral-small3.2"

    @pytest.mark.asyncio
    async def test_kein_client_modell_nimmt_workflow_default(self, monkeypatch):
        from app.services import llm
        _patch_available(monkeypatch, _avail("mistral-small3.2:latest"))
        got = await llm.ensure_generation_model(None, "anamnese")
        assert got == "mistral-small3.2"
        got_empty = await llm.ensure_generation_model("   ", "anamnese")
        assert got_empty == "mistral-small3.2"

    @pytest.mark.asyncio
    async def test_client_und_workflow_default_fehlen_letzter_fallback(self, monkeypatch):
        from app.services import llm
        # Nur gemma geladen; anamnese-Default (mistral) + qwen fehlen ->
        # resolve_summary_model -> erstes geladenes Chat-Modell = gemma.
        _patch_available(monkeypatch, _avail("gemma4:31b"))
        got = await llm.ensure_generation_model("qwen3:32b", "anamnese")
        assert got == "gemma4:31b"

    @pytest.mark.asyncio
    async def test_ollama_down_kein_override(self, monkeypatch):
        from app.services import llm
        _patch_available(monkeypatch, set())  # /api/tags nicht abrufbar
        # Optimistisch: das angeforderte Modell bleibt (Verhalten wie vor v19.5.3)
        got = await llm.ensure_generation_model("qwen3:32b", "anamnese")
        assert got == "qwen3:32b"

    @pytest.mark.asyncio
    async def test_ollama_praefix_wird_gestrippt(self, monkeypatch):
        from app.services import llm
        _patch_available(monkeypatch, _avail("mistral-small3.2:latest"))
        got = await llm.ensure_generation_model("ollama/mistral-small3.2", "anamnese")
        assert got == "mistral-small3.2"
