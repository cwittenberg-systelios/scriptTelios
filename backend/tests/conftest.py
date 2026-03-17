"""
conftest.py – Geteilte Fixtures und Konfiguration fuer alle Tests.

Umgebung wird hier einmalig konfiguriert bevor irgendein Modul importiert wird.
"""
import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

# ── Testumgebung konfigurieren ────────────────────────────────────────────────
os.environ.update({
    "OLLAMA_HOST":                      "http://localhost:11434",
    "OLLAMA_MODEL":                     "mistral-nemo",
    "WHISPER_MODEL":                    "medium",
    "WHISPER_DEVICE":                   "cpu",
    "WHISPER_COMPUTE_TYPE":             "int8",
    "DATABASE_URL":                     "sqlite+aiosqlite:///./test_systelios.db",
    "SECRET_KEY":                       "test-secret-key-fuer-tests",
    "DELETE_AUDIO_AFTER_TRANSCRIPTION": "false",
    "UPLOAD_DIR":                       "/tmp/systelios_test_uploads",
    "OUTPUT_DIR":                       "/tmp/systelios_test_outputs",
    "LOG_LEVEL":                        "WARNING",
    "LOG_FILE":                         "/tmp/systelios_test.log",
})

# ── Fixture-Pfade ─────────────────────────────────────────────────────────────
FIXTURES = Path(__file__).parent / "fixtures"

# Dummy-Dateien (immer vorhanden)
AUDIO_KURZ       = FIXTURES / "audio" / "gespraech_kurz.wav"
AUDIO_LANG       = FIXTURES / "audio" / "gespraech_lang.wav"
PDF_VERLAUF      = FIXTURES / "pdf" / "verlaufsbericht.pdf"
PDF_SELBST_DIG   = FIXTURES / "pdf" / "selbstauskunft_digital.pdf"
PDF_SELBST_LEER  = FIXTURES / "pdf" / "selbstauskunft_leer.pdf"
DOCX_ENTLASS_V   = FIXTURES / "docx" / "entlassbericht_vorlage.docx"
DOCX_ENTLASS_B   = FIXTURES / "docx" / "entlassbericht_beispiel.docx"
DOCX_VERL_V      = FIXTURES / "docx" / "verlaengerungsantrag_vorlage.docx"
DOCX_STILPROFIL  = FIXTURES / "docx" / "stilprofil_verlaufsnotiz.docx"
TXT_TRANSKRIPT   = FIXTURES / "txt" / "transkript_einzelgespraech.txt"
TXT_STICHPUNKTE  = FIXTURES / "txt" / "stichpunkte_verlauf.txt"
TXT_SELBST       = FIXTURES / "txt" / "selbstauskunft_text.txt"
TXT_VERLAUF      = FIXTURES / "txt" / "verlaufsdokumentation.txt"

# Echte Dateien (optional – werden uebersprungen wenn nicht vorhanden)
# Legt anonymisierte echte Dateien hier ab:
REAL_FILES = {
    "audio":         FIXTURES / "audio" / "gespraech_real.mp3",
    "selbstauskunft_handschrift": FIXTURES / "pdf" / "selbstauskunft_handschrift.pdf",
    "entlassbericht_real": FIXTURES / "docx" / "entlassbericht_real.docx",
    "verlauf_real":  FIXTURES / "pdf" / "verlauf_real.pdf",
}


def real_file(key: str):
    """Gibt pytest.mark.skipif zurueck wenn echte Datei nicht vorhanden."""
    path = REAL_FILES.get(key)
    if path is None or not path.exists():
        return pytest.mark.skip(reason=f"Echte Testdatei nicht vorhanden: {key}")
    return pytest.mark.skipif(False, reason="")


# ── Verzeichnisse anlegen ─────────────────────────────────────────────────────
os.makedirs("/tmp/systelios_test_uploads", exist_ok=True)
os.makedirs("/tmp/systelios_test_outputs", exist_ok=True)


# ── Gemeinsame Mocks ──────────────────────────────────────────────────────────

@pytest.fixture
def mock_llm():
    """LLM-Aufruf durch fixen Beispieltext ersetzen."""
    with patch(
        "app.services.llm.generate_text",
        new=AsyncMock(return_value={
            "text": (
                "VERLAUFSNOTIZ\n\n"
                "Datum: 21.11.2025 | Gespraechsart: Einzeltherapie\n\n"
                "1. HAUPTTHEMEN\n"
                "Im heutigen Gespraech stand die Auseinandersetzung mit dem inneren Kritiker "
                "im Vordergrund. Der Klient berichtete von einer konkreten Situation am "
                "Arbeitsplatz, in der er sich trotz guter Leistung als unzulaenglich erlebt hatte.\n\n"
                "2. INTERVENTIONEN\n"
                "Hypnosystemische Externalisierung des inneren Kritikers. "
                "Ressourcenorientierte Verstaerkung.\n\n"
                "3. VERLAUF\n"
                "Stimmung: 5/10. Schlaf: 6/10. Keine Suizidalitaet.\n\n"
                "4. VEREINBARUNGEN\n"
                "Abendliches Notieren positiver Selbstbeobachtungen. Naechster Termin: 28.11.2025"
            ),
            "model_used": "ollama/mistral-nemo",
            "duration_s": 2.4,
            "token_count": 187,
        }),
    ):
        yield


@pytest.fixture
def mock_llm_anamnese():
    """LLM-Mock mit realistischer Anamnese-Ausgabe."""
    with patch(
        "app.services.llm.generate_text",
        new=AsyncMock(return_value={
            "text": (
                "ANAMNESE\n\n"
                "Vorstellungsanlass: Stationaere Aufnahme auf Zuweisung des Hausarztes.\n"
                "Hauptbeschwerde: Anhaltende Erschoepfung, Schlafprobleme, depressive Verstimmung.\n\n"
                "Aktuelle Erkrankung: Beginn vor ca. 8 Monaten nach beruflicher Belastungsphase.\n\n"
                "PSYCHOPATHOLOGISCHER BEFUND (AMDP)\n"
                "Bewusstsein: klar\n"
                "Orientierung: vollstaendig\n"
                "Affektivitaet: subdepressiv, Schwingungsfaehigkeit erhalten\n"
                "Antrieb: reduziert\n"
                "Suizidalitaet: aktuell verneint\n\n"
                "Diagnosen: F32.1 Mittelgradige depressive Episode, Z73.0 Ausgebranntsein"
            ),
            "model_used": "ollama/mistral-nemo",
            "duration_s": 3.1,
            "token_count": 221,
        }),
    ):
        yield


@pytest.fixture
def mock_transcribe():
    """Whisper-Transkription durch fixes Ergebnis ersetzen."""
    with patch(
        "app.services.transcription.transcribe_audio",
        new=AsyncMock(return_value={
            "transcript": (
                "Therapeut: Wie war die Woche fuer Sie? "
                "Patient: Eigentlich besser als letzte Woche. "
                "Ich habe versucht, die Uebung zu machen. "
                "Das Aufschreiben was gut war hat mir geholfen."
            ),
            "language": "de",
            "duration_s": 8.0,
            "word_count": 37,
        }),
    ):
        yield


@pytest.fixture
def mock_extract_text():
    """PDF/DOCX-Extraktion durch realistischen Text ersetzen."""
    with patch(
        "app.services.extraction.extract_text",
        new=AsyncMock(return_value=TXT_SELBST.read_text(encoding="utf-8")),
    ):
        yield


@pytest.fixture
def mock_embedding():
    """Ollama-Embedding durch Zufallsvektor ersetzen."""
    import random
    fake_embedding = [random.uniform(-0.1, 0.1) for _ in range(768)]
    with patch(
        "app.services.embeddings.get_embedding",
        new=AsyncMock(return_value=fake_embedding),
    ):
        yield


@pytest.fixture
def mock_ollama_unavailable():
    """Ollama als nicht erreichbar simulieren."""
    import httpx
    with patch(
        "app.services.llm._generate_ollama",
        new=AsyncMock(side_effect=RuntimeError(
            "Ollama nicht erreichbar unter http://localhost:11434."
        )),
    ):
        yield
