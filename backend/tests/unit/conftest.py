"""
tests/unit/conftest.py
──────────────────────
Conftest fuer pure Unit-Tests:

  - KEIN autouse-DB-Setup. Tests die DB brauchen muessen `db`-Fixture
    explizit anfordern.
  - KEIN ollama_vision_setup. Unit-Tests duerfen nichts ueber Netz tun.
  - _ollama_client wird VOR jedem Test auf None gesetzt, damit der
    Singleton aus llm.py keine Test-Reihenfolge-Abhaengigkeiten erzeugt.
"""
import pytest


@pytest.fixture(autouse=True)
def _reset_ollama_client(monkeypatch):
    """
    Setzt den modulglobalen httpx-Client in llm.py vor jedem Test auf None.

    Hintergrund: app.services.llm._get_ollama_client() cacht einen
    httpx.AsyncClient zwischen Aufrufen. Wenn Test A diesen Client erzeugt
    und Test B dann `patch("httpx.AsyncClient")` versucht, greift der Patch
    nicht — der gecachte Client bleibt aktiv. Folge: Tests passen aus dem
    falschen Grund, manchmal je nach Reihenfolge anders.

    Mit diesem autouse-Reset wird der Cache zwischen jedem Test geleert.
    """
    try:
        import app.services.llm as _llm
        monkeypatch.setattr(_llm, "_ollama_client", None, raising=False)
    except ImportError:
        # app.services.llm noch nicht importiert - nichts zu tun
        pass
