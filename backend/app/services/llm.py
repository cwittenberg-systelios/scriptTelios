"""
LLM-Generierungs-Service.

Ausschliesslich Ollama (lokales Modell, On-Premise).
Kein externer API-Aufruf – alle Daten bleiben im internen Netz.
"""
import logging
import time

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# Maximale Zeichen im User-Content (ca. 80k Tokens bei durchschnittlichen Texten)
# Verhindert EOF-Fehler bei sehr langen Transkripten
MAX_USER_CONTENT_CHARS = 60_000


async def generate_text(
    system_prompt: str,
    user_content: str,
    max_tokens: int = 2048,
) -> dict:
    """
    Generiert Text ausschliesslich via lokalem Ollama-Modell.

    Fallback-Kuerzung bei extremen Laengen: gleichmaessiges Sampling
    ueber das gesamte Transkript – jeder Gespraechsabschnitt bleibt
    anteilig vertreten. Greift normalerweise nicht, da num_ctx=32768
    auch lange Therapiegespraeche (ca. 15-20k Tokens) vollstaendig abdeckt.
    """
    if len(user_content) > MAX_USER_CONTENT_CHARS:
        user_content = _sample_uniformly(user_content, MAX_USER_CONTENT_CHARS)

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


def _sample_uniformly(text: str, max_chars: int, n_windows: int = 10) -> str:
    """
    Kuerzt einen langen Text durch gleichmaessiges Sampling.

    Das Transkript wird in n_windows gleich grosse Abschnitte aufgeteilt.
    Aus jedem Abschnitt wird ein proportionaler Teil an Zeilengrenzen
    entnommen. So bleibt jeder Gespraechsabschnitt anteilig erhalten –
    kein Teil des Gespraechs wird komplett uebersprungen.

    Schnitte erfolgen immer an Zeilengrenzen ([A]:/[B]: Sprecher-Marker)
    damit keine Saetze mitten im Wort abgebrochen werden.
    """
    lines = text.splitlines(keepends=True)
    total = len(lines)
    window_size = total // n_windows
    chars_per_window = max_chars // n_windows

    sampled = []
    sampled.append(
        f"[Hinweis: Transkript war zu lang – gleichmaessig auf {n_windows} "
        f"Abschnitte reduziert, jeder Abschnitt anteilig vertreten]\n\n"
    )

    for i in range(n_windows):
        start_line = i * window_size
        end_line   = (i + 1) * window_size if i < n_windows - 1 else total
        window_lines = lines[start_line:end_line]
        window_text  = "".join(window_lines)

        if len(window_text) <= chars_per_window:
            sampled.append(window_text)
        else:
            # An naechster Zeilengrenze kuerzen
            truncated = window_text[:chars_per_window]
            last_newline = truncated.rfind("\n")
            if last_newline > chars_per_window // 2:
                truncated = truncated[:last_newline + 1]
            sampled.append(truncated)

        if i < n_windows - 1:
            sampled.append(f"\n[— Abschnitt {i+1}/{n_windows} —]\n")

    result = "".join(sampled)
    logger.warning(
        "Transkript gekuerzt: %d → %d Zeichen (%d Abschnitte gleichmaessig gesampelt)",
        len(text), len(result), n_windows,
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
            "num_ctx":     32768,   # Explizit setzen – verhindert EOF bei langen Prompts
            "temperature": 0.3,
            "top_p":       0.9,
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
                "Bitte sicherstellen, dass Ollama laeuft."
            )
        except httpx.HTTPStatusError as e:
            body = e.response.text
            if "EOF" in body or "completion" in body:
                raise RuntimeError(
                    f"Ollama Kontext-Fehler (Eingabe zu lang). "
                    f"Details: {body[:200]}"
                )
            raise RuntimeError(f"Ollama Fehler {e.response.status_code}: {body}")

    data = r.json()
    text = data.get("response", "").strip()
    return {
        "text": text,
        "model_used": f"ollama/{settings.OLLAMA_MODEL}",
        "token_count": data.get("eval_count"),
    }
