"""
LLM-Generierungs-Service.

Ausschliesslich Ollama (lokales Modell, On-Premise).
Kein externer API-Aufruf – alle Daten bleiben im internen Netz.
"""
import logging
import time

from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# Maximale Zeichen im User-Content (ca. 80k Tokens bei durchschnittlichen Texten)
# Verhindert EOF-Fehler bei sehr langen Transkripten
MAX_USER_CONTENT_CHARS = 60_000

# Modell-spezifische Generierungsparameter.
# Match erfolgt auf Modellname-Prefix (case-insensitive).
# Quantisierungssuffixe (:q6_K, :q4_K_M etc.) werden ignoriert – Prefix reicht.
#
# Empfehlung RTX Pro 4500 (32GB): qwen2.5:32b:q6_K
# Empfehlung RTX 4090 (24GB):     qwen2.5:32b
# Reasoning (beide):               deepseek-r1:32b (langsamerer aber tieferer Analyse)
MODEL_PROFILES: dict[str, dict] = {
    # Reasoning-Modelle: größerer Mindest-Kontext, niedrigere Temperatur
    "deepseek-r1": {"min_ctx": 8192, "temperature": 0.2, "top_p": 0.85},
    "deepseek":    {"min_ctx": 8192, "temperature": 0.2, "top_p": 0.85},
    # Standard-Modelle: dynamischer Kontext, kein Minimum nötig
    "qwen2.5":     {"min_ctx": 2048, "temperature": 0.3, "top_p": 0.9},
    "qwen":        {"min_ctx": 2048, "temperature": 0.3, "top_p": 0.9},
    "llama":       {"min_ctx": 2048, "temperature": 0.3, "top_p": 0.9},
    "gemma":       {"min_ctx": 2048, "temperature": 0.3, "top_p": 0.9},
    "mistral":     {"min_ctx": 2048, "temperature": 0.3, "top_p": 0.9},
    "_default":    {"min_ctx": 2048, "temperature": 0.3, "top_p": 0.9},
}

def _get_model_profile(model_name: str) -> dict:
    """Gibt modellspezifische Generierungsparameter zurück."""
    name_lower = model_name.lower()
    for prefix, profile in MODEL_PROFILES.items():
        if prefix != "_default" and name_lower.startswith(prefix):
            return profile
    return MODEL_PROFILES["_default"]

# Maximale Zeichen für Stilvorlagen im System-Prompt.
# Durch explizite "nur Stil"-Rahmung bei C&P-Texten ist das Looping-Risiko
# deutlich reduziert – 2000 Zeichen reichen für eine vollständige Beispieldoku.
MAX_STYLE_CONTEXT_CHARS = 2_000


def truncate_style_context(text: str) -> str:
    """
    Kürzt Stilvorlagen auf MAX_STYLE_CONTEXT_CHARS.
    Schneidet an Satzgrenze damit der letzte Satz vollständig bleibt.
    """
    if len(text) <= MAX_STYLE_CONTEXT_CHARS:
        return text
    truncated = text[:MAX_STYLE_CONTEXT_CHARS]
    # Sauber am letzten Satzende abschneiden
    last_stop = max(truncated.rfind("."), truncated.rfind("!"), truncated.rfind("?"))
    if last_stop > MAX_STYLE_CONTEXT_CHARS // 2:
        truncated = truncated[:last_stop + 1]
    logger.info(
        "Stilvorlage gekürzt: %d → %d Zeichen (verhindert LLM-Wiederholungsloop)",
        len(text), len(truncated),
    )
    return truncated


def deduplicate_paragraphs(text: str) -> str:
    """
    Entfernt wiederholte Absätze aus dem LLM-Output.
    mistral-nemo neigt bei zu langem Kontext dazu denselben Absatz
    mehrfach zu generieren – dieser Filter erkennt und entfernt Duplikate.
    Überschriften (**Fett** oder Zeile mit nur Grossbuchstaben) bleiben erhalten.
    """
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    seen: set[str] = set()
    result = []
    duplicates = 0
    for p in paragraphs:
        # Normalisierter Vergleichsschlüssel: Leerzeichen zusammenfassen, lowercase
        key = " ".join(p.lower().split())
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        result.append(p)
    if duplicates:
        logger.warning(
            "LLM-Output: %d doppelte Absätze entfernt (Wiederholungsloop erkannt)",
            duplicates,
        )
    return "\n\n".join(result)


async def generate_text(
    system_prompt: str,
    user_content: str,
    max_tokens: int = 2048,
    model: Optional[str] = None,
) -> dict:
    """
    Generiert Text ausschliesslich via lokalem Ollama-Modell.

    num_ctx wird dynamisch berechnet: Input-Tokens * 1.2 + max_tokens,
    auf das nächste Vielfache von 1024 aufgerundet.
    Das vermeidet unnötige KV-Cache-Reallokierungen bei jedem Request.
    """
    if len(user_content) > MAX_USER_CONTENT_CHARS:
        user_content = _sample_uniformly(user_content, MAX_USER_CONTENT_CHARS)

    t0 = time.time()
    result = await _generate_ollama(system_prompt, user_content, max_tokens, model=model)

    # Output-Postprocessing: doppelte Absätze entfernen (LLM-Wiederholungsloop)
    if result.get("text"):
        result["text"] = deduplicate_paragraphs(result["text"])

    result["duration_s"] = round(time.time() - t0, 1)
    logger.info(
        "Generierung: %d Tokens in %.1fs (Modell: %s)",
        result.get("token_count", 0),
        result["duration_s"],
        result["model_used"],
    )
    return result


def _estimate_num_ctx(system_prompt: str, user_content: str, max_tokens: int) -> int:
    """
    Schätzt den benötigten Kontext basierend auf der tatsächlichen Input-Länge.

    Faustregel: 1 Token ≈ 3.5 Zeichen (Deutsch, klinischer Text).
    Puffer: 20% Sicherheitsmarge + max_tokens für den Output.
    Rundet auf das nächste Vielfache von 512 für Ollama-Effizienz.

    Warum nicht immer 32768:
    Ein Therapiegespräch (System ~2k + Transkript ~6k Zeichen) braucht
    ~2500 Tokens Kontext. num_ctx=32768 erzwingt einen 13x größeren KV-Cache
    der bei jedem Request neu alloziert wird → ~20s Overhead.
    """
    total_chars = len(system_prompt) + len(user_content)
    estimated_tokens = int(total_chars / 3.5)
    needed = int(estimated_tokens * 1.2) + max_tokens
    # Auf nächstes Vielfaches von 512 aufrunden, Minimum 2048
    rounded = max(2048, ((needed + 511) // 512) * 512)
    # Niemals über das Modell-Limit
    return min(rounded, 32768)


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


def _is_vram_error(err: Exception) -> bool:
    msg = str(err).lower()
    return any(k in msg for k in (
        "cuda out of memory", "out of memory", "cublas",
        "illegal memory access", "vram", "oom",
    ))


def _classify_ollama_error(status_code: int, body: str) -> RuntimeError:
    """Wandelt Ollama-HTTP-Fehler in sprechende RuntimeErrors um."""
    if "EOF" in body or "completion" in body:
        return RuntimeError(
            f"Ollama Kontext-Fehler (Eingabe zu lang). Details: {body[:200]}"
        )
    if any(k in body.lower() for k in ("out of memory", "cuda", "cublas", "oom")):
        return RuntimeError(f"cuda out of memory: {body[:200]}")
    return RuntimeError(f"Ollama Fehler {status_code}: {body}")


async def _ollama_unload(model: Optional[str] = None) -> None:
    """Entlädt das angegebene Ollama-Modell aus dem VRAM (keep_alive=0)."""
    target = model or settings.OLLAMA_MODEL
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            await client.post(
                f"{settings.OLLAMA_HOST}/api/generate",
                json={"model": target, "keep_alive": 0, "prompt": ""},
            )
        logger.info("Ollama-Modell '%s' aus VRAM entladen (OOM-Recovery)", target)
    except Exception as e:
        logger.debug("Ollama-Entladen nicht moeglich: %s", e)


async def _generate_ollama(
    system_prompt: str,
    user_content: str,
    max_tokens: int,
    model: Optional[str] = None,
) -> dict:
    """
    Ollama REST API (lokal, kein externer Aufruf).

    model: Optionales Modell-Override. Wenn None, wird settings.OLLAMA_MODEL verwendet.
    VRAM-OOM-Strategie (3 Stufen):
    1. Normalaufruf mit num_ctx=32768
    2. Bei OOM: Modell entladen + neu laden, num_ctx auf 8192 reduzieren
    3. Bei erneutem OOM: Klare Fehlermeldung an den Therapeuten
    """
    effective_model = model or settings.OLLAMA_MODEL

    profile = _get_model_profile(effective_model)
    num_ctx = _estimate_num_ctx(system_prompt, user_content, max_tokens)
    # Für Reasoning-Modelle: mindestens deren Profil-Minimum respektieren
    num_ctx = max(num_ctx, profile.get("min_ctx", 2048))
    logger.debug(
        "Modell '%s': num_ctx=%d (dynamisch berechnet, Profil-Max: %d)",
        effective_model, num_ctx, profile["num_ctx"]
    )

    async def _call(num_ctx: int) -> httpx.Response:
        payload = {
            "model":      effective_model,
            "system":     system_prompt,
            "prompt":     user_content,
            "stream":     False,
            "keep_alive": -1,   # Modell nach Generierung im VRAM behalten
            "options": {
                "num_predict": max_tokens,
                "num_ctx":     num_ctx,
                "temperature": profile["temperature"],
                "top_p":       profile["top_p"],
            },
        }
        async with httpx.AsyncClient(timeout=300.0) as client:
            try:
                r = await client.post(
                    f"{settings.OLLAMA_HOST}/api/generate",
                    json=payload,
                )
                r.raise_for_status()
                return r
            except httpx.ConnectError:
                raise RuntimeError(
                    f"Ollama nicht erreichbar unter {settings.OLLAMA_HOST}. "
                    "Bitte sicherstellen, dass Ollama laeuft."
                )
            except httpx.HTTPStatusError as e:
                body = e.response.text
                raise _classify_ollama_error(e.response.status_code, body)

    # Versuch 1: Normal mit dynamisch berechnetem Kontext
    try:
        r = await _call(num_ctx=num_ctx)
    except Exception as e:
        if not _is_vram_error(e):
            raise
        logger.warning(
            "Ollama VRAM-OOM – entlade Modell und versuche erneut "
            "mit reduziertem Kontext (8192 tokens). Fehler: %s", e
        )
        await _ollama_unload(effective_model)
        # Versuch 2: reduzierter Kontext
        try:
            r = await _call(num_ctx=8192)
            logger.info("Ollama-Generierung erfolgreich mit reduziertem Kontext (8192)")
        except Exception as e2:
            if not _is_vram_error(e2):
                raise
            raise RuntimeError(
                "Ollama: Nicht genug VRAM auch mit reduziertem Kontext (8192 tokens). "
                "Bitte WHISPER_FREE_OLLAMA_VRAM=true in .env setzen "
                "oder das Gespraech kuerzen."
            ) from e2

    data = r.json()
    text = data.get("response", "").strip()
    return {
        "text": text,
        "model_used": f"ollama/{effective_model}",
        "token_count": data.get("eval_count"),
    }
