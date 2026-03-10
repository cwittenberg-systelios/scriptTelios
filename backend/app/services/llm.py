"""
LLM-Generierungs-Service.

Backends:
  ollama    – lokales Modell ueber Ollama (On-Premise, Produktion)
  anthropic – Claude API (Testphase)
"""
import logging
import time
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


async def generate_text(
    system_prompt: str,
    user_content: str,
    max_tokens: int = 2048,
) -> dict:
    """
    Generiert Text per LLM.

    Gibt zurueck:
      text          – generierter Text
      model_used    – tatsaechlich verwendetes Modell
      duration_s    – Generierungsdauer in Sekunden
      token_count   – Anzahl Output-Tokens (falls verfuegbar)
    """
    t0 = time.time()

    if settings.LLM_BACKEND == "anthropic":
        result = await _generate_anthropic(system_prompt, user_content, max_tokens)
    else:
        result = await _generate_ollama(system_prompt, user_content, max_tokens)

    result["duration_s"] = round(time.time() - t0, 1)
    logger.info(
        "Generierung: %d Tokens in %.1fs (Backend: %s, Modell: %s)",
        result.get("token_count", 0),
        result["duration_s"],
        settings.LLM_BACKEND,
        result["model_used"],
    )
    return result


async def _generate_ollama(
    system_prompt: str,
    user_content: str,
    max_tokens: int,
) -> dict:
    """Ollama REST API (lokal)."""
    import httpx

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
                "Bitte sicherstellen, dass Ollama laeuft: 'ollama serve'"
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


async def _generate_anthropic(
    system_prompt: str,
    user_content: str,
    max_tokens: int,
) -> dict:
    """Anthropic Claude API (Testphase)."""
    if not settings.ANTHROPIC_API_KEY:
        raise RuntimeError(
            "ANTHROPIC_API_KEY nicht gesetzt. "
            "Bitte in .env eintragen oder LLM_BACKEND=ollama verwenden."
        )

    import httpx

    headers = {
        "x-api-key": settings.ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": settings.ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_content}],
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=payload,
        )

    if r.status_code != 200:
        raise RuntimeError(f"Anthropic API Fehler {r.status_code}: {r.text}")

    data = r.json()
    text = "".join(
        block.get("text", "")
        for block in data.get("content", [])
        if block.get("type") == "text"
    )
    usage = data.get("usage", {})
    return {
        "text": text.strip(),
        "model_used": f"anthropic/{settings.ANTHROPIC_MODEL}",
        "token_count": usage.get("output_tokens"),
    }
