"""
LLM-Generierungs-Service.

Ausschliesslich Ollama (lokales Modell, On-Premise).
Kein externer API-Aufruf – alle Daten bleiben im internen Netz.

Hinweis zur Testphase: Auch in der Testphase laeuft Ollama lokal auf dem
Miet-Server (Hetzner/RunPod). Es werden keine Patientendaten an externe
Dienste gesendet.
"""
import logging
import time

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


async def generate_text(
    system_prompt: str,
    user_content: str,
    max_tokens: int = 2048,
) -> dict:
    """
    Generiert Text ausschliesslich via lokalem Ollama-Modell.

    Gibt zurueck:
      text          – generierter Text
      model_used    – verwendetes Modell
      duration_s    – Generierungsdauer in Sekunden
      token_count   – Anzahl Output-Tokens (falls verfuegbar)
    """
    t0 = time.time()
    result = await _generate_ollama(system_prompt, user_content, max_tokens)
    result["duration_s"] = round(time.time() - t0, 1)
    logger.info(
        "Generierung: %d Tokens in %.1fs (Modell: %s)",
        result.get("token_count", 0),
        result["duration_s"],
        result["model_used"],
    )
    return result


async def _generate_ollama(
    system_prompt: str,
    user_content: str,
    max_tokens: int,
) -> dict:
    """Ollama REST API (lokal, kein externer Aufruf)."""
    payload = {
        "model": settings.OLLAMA_MODEL,
        "system": system_prompt,
        "prompt": user_content,
        "stream": False,
        "options": {
            "num_predict": max_tokens,
            "temperature": 0.3,
            "top_p": 0.9,
        },
    }

    async with httpx.AsyncClient(timeout=300.0) as client:
        try:
            r = await client.post(
                f"{settings.OLLAMA_HOST}/api/generate",
                json=payload,
            )
            r.raise_for_status()
        except httpx.ConnectError:
            raise RuntimeError(
                f"Ollama nicht erreichbar unter {settings.OLLAMA_HOST}. "
                "Bitte sicherstellen, dass Ollama laeuft: 'ollama serve' "
                "oder 'docker compose up ollama'."
            )
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"Ollama Fehler {e.response.status_code}: {e.response.text}")

    data = r.json()
    text = data.get("response", "").strip()
    return {
        "text": text,
        "model_used": f"ollama/{settings.OLLAMA_MODEL}",
        "token_count": data.get("eval_count"),
    }
