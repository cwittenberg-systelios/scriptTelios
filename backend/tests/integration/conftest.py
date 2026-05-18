"""
tests/integration/conftest.py
─────────────────────────────
Conftest fuer Integration-Tests (FastAPI TestClient, gemockte LLMs/Whisper).

Im Gegensatz zu tests/unit/conftest.py wird hier eine SQLite-DB pro Test
neu angelegt und am Ende gedropt. Die meisten Endpunkte brauchen den
DB-Layer auch wenn keine echten Daten persistiert werden (FK-Validation,
Status-Updates etc.).
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

# Pfade aus root conftest (Backend-Root ist bereits in sys.path)
from tests.conftest import TXT_SELBST  # noqa: E402


@pytest.fixture(autouse=True)
def init_test_db():
    """
    DB-Tabellen vor jedem Test anlegen, danach bereinigen.

    SQLite-Backend ohne pgvector. Drei Probleme die hier gelöst werden:

    1. SQLite + `database is locked`: jobs.py startet Hintergrund-Tasks per
       `asyncio.ensure_future(self._db_insert_job(...))`. Diese halten
       Connections auf dem alten Event-Loop. Wenn der nächste Test den Loop
       schliesst, sind die Locks noch da. Lösung: nach jedem Test sowohl
       `engine.dispose()` aufrufen (schliesst alle gepoolten Connections)
       als auch eine kurze `asyncio.sleep`-Drain-Phase, damit pending
       Background-Tasks zu Ende laufen können.

    2. Mehrere Tests teilen einen Engine: SQLAlchemy cacht den AsyncEngine
       modulweit. Ein neuer Event-Loop pro Test triggert
       'Future attached to a different loop'. Loesung: jeder Test bekommt
       einen FRESH Engine durch reload des database-Moduls (oder Cleanup).

    3. Loop-Konflikte: pytest-asyncio + new_event_loop kollidieren. Wir
       benutzen pytest-asyncio's eigenen Loop wo verfuegbar.
    """
    from app.core.database import engine, Base

    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def teardown():
        # Pending Background-Tasks (z.B. fire-and-forget job_queue._db_*)
        # 50ms drainen, sonst halten sie Connections und DROP TABLE blockiert.
        await asyncio.sleep(0.05)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        # Pool leeren -> alle gepoolten Connections schliessen.
        # Verhindert "database is locked" beim naechsten Test.
        await engine.dispose()

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(setup())
        yield
        try:
            loop.run_until_complete(teardown())
        except Exception:
            # Falls teardown crasht (z.B. weil bereits gedroppte Tabelle):
            # mindestens dispose nachholen damit der naechste Test sauber
            # startet.
            try:
                loop.run_until_complete(engine.dispose())
            except Exception:
                pass
    finally:
        loop.close()


# ── pgvector-Isolation ────────────────────────────────────────────────────────
# Production benutzt pgvector mit `embedding <=> CAST(? AS vector)`. SQLite
# kennt diesen Operator nicht — jeder Test der einen kompletten Job-Run
# triggert (also durch jobs.py + style_embeddings) wuerde mit
# 'near ">": syntax error' sterben.
#
# Loesung: globaler Stub fuer retrieve_style_examples. Tests die explizit
# das Retrieval-Verhalten testen (siehe TestStyleInfo::test_style_library_*)
# overriden den Stub lokal via `patch(..., new=AsyncMock(return_value=...))`
# in ihrem with-Block. Der hier installierte Stub greift nur als Default.

@pytest.fixture(autouse=True)
def _disable_pgvector_for_sqlite(request, monkeypatch):
    """Verhindert pgvector-SQL gegen SQLite-Tests.

    retrieve_style_examples gibt per Default leeren String zurueck (= kein
    Stil gefunden). Das passt zu allen Tests die NICHT explizit das
    Style-Library-Retrieval pruefen wollen.

    Opt-out: Tests die das echte retrieve_style_examples brauchen (z.B.
    Unit-Tests fuer das Embedding-Retrieval selbst), markieren sich mit::

        @pytest.mark.real_embeddings
        def test_xyz(self): ...

    Dann greift dieser Stub nicht und die Original-Funktion wird verwendet.
    Solche Tests muessen entweder kein pgvector verwenden (siehe
    StyleEmbedding-Tests die direkt mit SQLAlchemy arbeiten) oder die
    pgvector-Query selbst mocken.
    """
    if request.node.get_closest_marker("real_embeddings"):
        # Tests die das echte retrieve_style_examples brauchen:
        # Stub nicht aktivieren.
        yield
        return

    from unittest.mock import AsyncMock

    async def _stub_retrieve(*args, **kwargs):
        return ""

    try:
        monkeypatch.setattr(
            "app.services.embeddings.retrieve_style_examples",
            AsyncMock(side_effect=_stub_retrieve),
            raising=False,
        )
    except (ImportError, AttributeError):
        pass
    try:
        monkeypatch.setattr(
            "app.api.jobs.retrieve_style_examples",
            AsyncMock(side_effect=_stub_retrieve),
            raising=False,
        )
    except (ImportError, AttributeError):
        pass
    yield


@pytest.fixture(autouse=True)
def _reset_ollama_client(monkeypatch):
    """Wie in unit/conftest.py: Singleton-Cache vor jedem Test leeren."""
    try:
        import app.services.llm as _llm
        monkeypatch.setattr(_llm, "_ollama_client", None, raising=False)
    except ImportError:
        pass


# ── LLM-Mocks (opt-in via Argument) ───────────────────────────────────────────
# WICHTIG: Patch-Pfad muss dort sein wo die Funktion VERWENDET wird,
# nicht wo sie definiert ist. Wegen `from app.services.llm import generate_text`
# in mehreren Konsumenten muss jeder Konsumpfad separat gepatcht werden.

_MOCK_LLM_RESPONSE = {
    "text": (
        "VERLAUFSNOTIZ\n\n"
        "Datum: 21.11.2025 | Gespraechsart: Einzeltherapie\n\n"
        "1. HAUPTTHEMEN\n"
        "Im heutigen Gespraech stand die Auseinandersetzung mit dem inneren Kritiker "
        "im Vordergrund. Der Klient berichtete von Fortschritten.\n\n"
        "2. INTERVENTIONEN\n"
        "Hypnosystemische Externalisierung. Ressourcenorientierte Verstaerkung.\n\n"
        "3. VERLAUF\n"
        "Stimmung: 5/10. Schlaf: 6/10. Keine Suizidalitaet.\n\n"
        "4. VEREINBARUNGEN\n"
        "Naechster Termin: 28.11.2025"
    ),
    "model_used": "ollama/qwen3:32b",
    "duration_s": 2.4,
    "token_count": 187,
}


@pytest.fixture
def mock_llm():
    """LLM-Aufruf durch fixen Beispieltext ersetzen (alle Verwendungsorte)."""
    with patch("app.services.llm.generate_text",
               new=AsyncMock(return_value=_MOCK_LLM_RESPONSE)), \
         patch("app.api.jobs.generate_text",
               new=AsyncMock(return_value=_MOCK_LLM_RESPONSE)):
        yield


@pytest.fixture
def mock_llm_anamnese():
    """LLM-Mock mit realistischer Anamnese-Ausgabe."""
    mock_response = {
        "text": (
            "ANAMNESE\n\n"
            "Vorstellungsanlass: Stationaere Aufnahme auf Zuweisung des Hausarztes.\n"
            "Hauptbeschwerde: Erschoepfung, Schlafprobleme, depressive Verstimmung.\n\n"
            "PSYCHOPATHOLOGISCHER BEFUND (AMDP)\n"
            "Bewusstsein: klar | Orientierung: vollstaendig\n"
            "Affektivitaet: subdepressiv | Antrieb: reduziert\n"
            "Suizidalitaet: aktuell verneint\n\n"
            "Diagnosen: F32.1 Mittelgradige depressive Episode, Z73.0 Ausgebranntsein"
        ),
        "model_used": "ollama/qwen3:32b",
        "duration_s": 3.1,
        "token_count": 221,
    }
    with patch("app.services.llm.generate_text",
               new=AsyncMock(return_value=mock_response)), \
         patch("app.api.jobs.generate_text",
               new=AsyncMock(return_value=mock_response)):
        yield


@pytest.fixture
def mock_transcribe():
    """Whisper-Transkription durch fixes Ergebnis ersetzen."""
    mock_result = {
        "transcript": (
            "Therapeut: Wie war die Woche fuer Sie? "
            "Patient: Eigentlich besser als letzte Woche. "
            "Ich habe versucht, die Uebung zu machen. "
            "Das Aufschreiben was gut war hat mir geholfen."
        ),
        "language": "de",
        "duration_s": 8.0,
        "word_count": 37,
    }
    with patch("app.services.transcription.transcribe_audio",
               new=AsyncMock(return_value=mock_result)):
        yield


@pytest.fixture
def mock_extract_text():
    """PDF/DOCX-Extraktion durch realistischen Text ersetzen."""
    text = TXT_SELBST.read_text(encoding="utf-8") if TXT_SELBST.exists() else "Beispieltext."
    with patch("app.services.extraction.extract_text", new=AsyncMock(return_value=text)), \
         patch("app.api.jobs.extract_text",            new=AsyncMock(return_value=text)), \
         patch("app.api.style_embeddings.extract_text", new=AsyncMock(return_value=text)):
        yield


@pytest.fixture
def mock_embedding():
    """Ollama-Embedding durch Zufallsvektor ersetzen."""
    import random
    fake_embedding = [random.uniform(-0.1, 0.1) for _ in range(768)]
    with patch("app.services.embeddings.get_embedding",
               new=AsyncMock(return_value=fake_embedding)), \
         patch("app.api.style_embeddings.get_embedding",
               new=AsyncMock(return_value=fake_embedding)):
        yield


@pytest.fixture
def mock_ollama_unavailable():
    """Ollama als nicht erreichbar simulieren."""
    with patch(
        "app.services.llm._generate_ollama",
        new=AsyncMock(side_effect=RuntimeError(
            "Ollama nicht erreichbar unter http://localhost:11434."
        )),
    ):
        yield
