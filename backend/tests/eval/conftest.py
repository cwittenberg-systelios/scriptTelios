"""
tests/eval/conftest.py
──────────────────────
Conftest fuer das Evaluations-Framework.

Diese Tests sprechen mit dem laufenden Backend + Ollama. Sie brauchen:
  - Erreichbares Ollama (sonst session-Abbruch oder Soft-Skip)
  - Optional: llava fuer Vision-OCR
  - Optional: Wechselbares Whisper-Modell

Im Gegensatz zu unit/ und integration/ haben wir hier echte Netz-Aufrufe.
"""
import os
import subprocess
import urllib.request

import pytest


@pytest.fixture(scope="session", autouse=True)
def ollama_vision_setup():
    """
    Zieht 'llava' einmalig pro Test-Session wenn Ollama erreichbar ist.
    Schlaegt fehl wenn Ollama nicht erreichbar — Tests werden dann via
    httpx-Exception abgebrochen, was korrekt ist (eval braucht LLM).
    """
    ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

    try:
        urllib.request.urlopen(f"{ollama_host}/api/tags", timeout=3)
    except Exception:
        return

    try:
        result = subprocess.run(
            ["ollama", "pull", "llava"],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            print(f"\n[WARN] ollama pull llava fehlgeschlagen: {result.stderr.strip()}")
    except FileNotFoundError:
        print("\n[WARN] ollama nicht im PATH - llava wird nicht geladen")
    except subprocess.TimeoutExpired:
        print("\n[WARN] ollama pull llava Timeout (>5 Min) - wird uebersprungen")


@pytest.fixture(scope="session", autouse=True)
def _configure_whisper_model_for_session(request):
    """
    Wenn --whisper-model gesetzt ist, wechselt das Backend-Modell vor den Tests
    und stellt es nach Abschluss wieder her. Backend muss laufen.
    """
    override_model = request.config.getoption("--whisper-model", default=None)
    if not override_model:
        yield
        return

    import httpx

    backend_url = os.environ.get("EVAL_BACKEND_URL", "http://localhost:8000")
    shared_secret = os.environ.get("CONFLUENCE_SHARED_SECRET", "")
    headers = {"X-Admin-Token": shared_secret} if shared_secret else {}
    previous = None

    try:
        with httpx.Client(base_url=backend_url, timeout=10.0) as client:
            r = client.get("/api/admin/whisper-model")
            if r.status_code == 200:
                previous = r.json().get("whisper_model")
                print(f"\n[WHISPER] Aktuelles Modell: {previous}", flush=True)

            r = client.post(
                "/api/admin/whisper-model",
                params={"model": override_model},
                headers=headers,
            )
            if r.status_code != 200:
                print(
                    f"\n[WHISPER] WARNUNG: Modellwechsel fehlgeschlagen "
                    f"({r.status_code} {r.text}) - Tests laufen mit {previous}",
                    flush=True,
                )
                previous = None
            else:
                print(
                    f"[WHISPER] Gewechselt auf {override_model} fuer diesen Testlauf",
                    flush=True,
                )
    except Exception as e:
        print(f"\n[WHISPER] WARNUNG: Admin-Endpoint nicht erreichbar ({e})", flush=True)
        previous = None

    yield

    if previous and previous != override_model:
        try:
            with httpx.Client(base_url=backend_url, timeout=10.0) as client:
                client.post(
                    "/api/admin/whisper-model",
                    params={"model": previous},
                    headers=headers,
                )
            print(f"\n[WHISPER] Zurueck auf {previous}", flush=True)
        except Exception as e:
            print(f"\n[WHISPER] Zurueckstellen fehlgeschlagen: {e}", flush=True)
