"""
llm_chat.py
───────────
Mehrturn-Chat mit Streaming ueber Ollama /api/chat (v19.31, S1).

Warum ein eigenes Modul statt generate_text(): der Dialog-Modus braucht
(a) eine Message-Historie statt system+user, (b) Token-Streaming fuer das
Vorlesen und (c) bei Structured Output nur das erste String-Feld (`sage`)
als Delta - der Rest des JSON kommt am Ende als Ganzes. generate_text()
bleibt unveraendert (Batch, Retry-Schichten, Postprocessing).

Oeffentliche Funktion:

    async for ev in generate_chat_stream(system, messages, model=..., ...):
        ev = ("delta", "text-Stueck")        # nur Inhalt von `sage` bei JSON
           | ("done", {text, structured_data, structured_parse_error,
                       model_used, token_count, duration_s, sage, perf})

`perf` (v19.33): ttft_s (erstes Token), load_s / prompt_s / gen_s und
prompt_tokens / gen_tokens aus Ollamas Abschluss-Objekt, num_ctx.
           | ("error", "Meldung")

Der Aufrufer (api/interview.py) uebersetzt die Events in SSE.
"""
from __future__ import annotations

import json
import logging
import time
from typing import AsyncIterator, Optional

import httpx

from app.core.config import settings
from app.services.llm import (
    _classify_ollama_error, _get_model_profile, _get_ollama_client,
    _normalize_model_id, _repeat_sampling_opts, fixed_num_ctx,
)

logger = logging.getLogger(__name__)


def _ns(v) -> Optional[float]:
    return round(v / 1e9, 2) if isinstance(v, (int, float)) and v else None


def _perf(final: dict, t0: float, t_first: Optional[float], num_ctx: int) -> dict:
    """v19.33: Zeitaufteilung eines Chat-Calls. load_s > ~1 s heisst: Ollama
    hat das Modell (neu) geladen - Kaltstart, Modellwechsel oder anderer
    num_ctx."""
    return {
        "ttft_s": round(t_first - t0, 2) if t_first else None,
        "total_s": round(time.time() - t0, 2),
        "load_s": _ns(final.get("load_duration")),
        "prompt_s": _ns(final.get("prompt_eval_duration")),
        "prompt_tokens": final.get("prompt_eval_count"),
        "gen_s": _ns(final.get("eval_duration")),
        "gen_tokens": final.get("eval_count"),
        "num_ctx": num_ctx,
    }


# ── Inkrementeller Extraktor fuer das erste String-Feld eines JSON-Stroms ─────
#
# Das Turn-Schema ordnet `sage` als erstes Feld an. Wir warten auf
# `"sage"` `:` `"` und geben danach jedes entkodierte Zeichen bis zum
# schliessenden `"` weiter. Escapes (\\n, \\", \\uXXXX) werden korrekt
# aufgeloest, auch wenn sie ueber Chunk-Grenzen zerfallen.

class SageExtractor:
    def __init__(self, field: str = "sage"):
        self._needle = f'"{field}"'
        self._buf = ""              # Rohpuffer bis zum Feldstart
        self._state = "seek"        # seek | colon | quote | text | escape | unicode | done
        self._uni = ""
        self._pending = ""          # Rohtext, der noch nicht als Delta raus ist
        self.text = ""

    def feed(self, chunk: str) -> str:
        """Nimmt ein Roh-Chunk und liefert den daraus entstandenen Klartext."""
        out: list[str] = []
        i = 0
        n = len(chunk)
        while i < n:
            c = chunk[i]
            if self._state == "seek":
                self._buf += c
                if self._buf.endswith(self._needle):
                    self._state = "colon"
                elif len(self._buf) > 4096:
                    self._buf = self._buf[-64:]
                i += 1
                continue
            if self._state == "colon":
                if c == ":":
                    self._state = "quote"
                i += 1
                continue
            if self._state == "quote":
                if c == '"':
                    self._state = "text"
                i += 1
                continue
            if self._state == "text":
                if c == "\\":
                    self._state = "escape"
                elif c == '"':
                    self._state = "done"
                else:
                    out.append(c)
                i += 1
                continue
            if self._state == "escape":
                mapping = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f"}
                if c == "u":
                    self._state = "unicode"
                    self._uni = ""
                else:
                    out.append(mapping.get(c, c))
                    self._state = "text"
                i += 1
                continue
            if self._state == "unicode":
                self._uni += c
                if len(self._uni) == 4:
                    try:
                        out.append(chr(int(self._uni, 16)))
                    except ValueError:
                        out.append("?")
                    self._state = "text"
                i += 1
                continue
            # done: Rest ignorieren
            break
        s = "".join(out)
        self.text += s
        return s

    @property
    def done(self) -> bool:
        return self._state == "done"


# ── Streaming-Chat ────────────────────────────────────────────────────────────

async def generate_chat_stream(
    system_prompt: str,
    messages: list[dict],
    *,
    model: Optional[str] = None,
    max_tokens: int = 400,
    temperature: float = 0.3,
    response_format: Optional[dict] = None,
    stream_field: str = "sage",
    num_ctx: Optional[int] = None,
) -> AsyncIterator[tuple[str, object]]:
    """Streamt eine Chat-Antwort. messages: [{role: user|assistant|system, content}]."""
    effective_model = _normalize_model_id(model) or settings.OLLAMA_MODEL
    profile = _get_model_profile(effective_model)
    if num_ctx is None:
        num_ctx = fixed_num_ctx()
    if num_ctx is None:
        total_chars = len(system_prompt) + sum(len(m.get("content", "")) for m in messages)
        est = int(total_chars / 3.2 * 1.2) + max_tokens
        num_ctx = max(profile.get("min_ctx", 2048), ((est + 1023) // 1024) * 1024)

    payload = {
        "model": effective_model,
        "stream": True,
        "think": False,
        "keep_alive": -1,
        "options": {
            "num_predict": max_tokens,
            "num_ctx": num_ctx,
            "temperature": temperature,
            "top_p": profile["top_p"],
            **_repeat_sampling_opts(profile),
        },
        "messages": [{"role": "system", "content": system_prompt}, *messages],
    }
    if response_format is not None:
        payload["format"] = response_format

    extractor = SageExtractor(stream_field) if response_format is not None else None
    raw_parts: list[str] = []
    token_count = 0
    t0 = time.time()
    t_first: Optional[float] = None
    final: dict = {}
    client = _get_ollama_client()

    try:
        async with client.stream("POST", "/api/chat", json=payload) as r:
            if r.status_code >= 400:
                body = (await r.aread()).decode("utf-8", "replace")
                raise _classify_ollama_error(r.status_code, body)
            async for line in r.aiter_lines():
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("error"):
                    raise RuntimeError(f"Ollama: {obj['error']}")
                piece = (obj.get("message") or {}).get("content", "") or ""
                if piece:
                    if t_first is None:
                        t_first = time.time()
                    raw_parts.append(piece)
                    token_count += 1
                    if extractor is not None:
                        delta = extractor.feed(piece)
                        if delta:
                            yield ("delta", delta)
                    else:
                        yield ("delta", piece)
                if obj.get("done"):
                    final = obj
                    token_count = obj.get("eval_count") or token_count
                    break
    except httpx.ConnectError:
        yield ("error", f"Ollama nicht erreichbar unter {settings.OLLAMA_HOST}.")
        return
    except Exception as e:  # noqa: BLE001
        logger.warning("Chat-Stream abgebrochen: %s", e)
        yield ("error", str(e))
        return

    raw = "".join(raw_parts).strip()
    result: dict = {
        "text": raw,
        "model_used": effective_model,
        "token_count": token_count,
        "duration_s": round(time.time() - t0, 1),
        "structured_data": None,
        "structured_parse_error": False,
        "sage": extractor.text if extractor else raw,
        "perf": _perf(final, t0, t_first, num_ctx),
    }
    if response_format is not None:
        try:
            result["structured_data"] = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            result["structured_parse_error"] = True
            logger.error("STRUCTURED_OUTPUT_PARSE_ERROR (chat-stream, model=%s, %d Zeichen)",
                         effective_model, len(raw))
    yield ("done", result)
