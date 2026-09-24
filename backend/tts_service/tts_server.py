#!/usr/bin/env python3
"""
tts_server.py - lokaler Vorlese-Dienst fuer scriptTelios (v19.35).

Laeuft in einem EIGENEN venv (/workspace/venv-tts), damit piper-tts
(onnxruntime) und chatterbox-tts (torch==2.6.0) das Backend-venv nicht
beruehren. Hoert nur auf 127.0.0.1; das Backend reicht die Anfragen durch
(Authentifizierung, Protokoll). Nur Standardbibliothek plus die Engines.

    python tts_server.py [--port 8011]

Endpoints:
    GET  /engines     -> {"engines": [{"key","label","available","reason"}]}
    POST /synthesize  {"text": "...", "engine": "piper|chatterbox"} -> audio/wav
                      Header X-TTS-Synth-S (Rechenzeit), X-TTS-Audio-S (Laenge)

Umgebung:
    TTS_PIPER_VOICE          Pfad zur .onnx-Stimme
                             (Default /workspace/tts/de_DE-thorsten-high.onnx)
    TTS_PIPER_THREADS        CPU-Threads fuer Piper (Default 2)
    TTS_CHATTERBOX_ENABLED   true = Chatterbox Multilingual anbieten (Default false)
    TTS_CHATTERBOX_THREADS   CPU-Threads fuer Chatterbox (Default 8)
    TTS_CACHE_SIZE           Anzahl gecachter Saetze (Default 256)

Datenschutz: Texte werden weder geloggt noch gespeichert (nur im RAM-Cache).
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import os
import threading
import time
import wave
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional

logger = logging.getLogger("tts_server")

MAX_CHARS = 600


def _env_bool(name: str, default: bool = False) -> bool:
    return (os.environ.get(name, str(default)) or "").strip().lower() in ("1", "true", "yes", "on")


def pcm16_to_wav(pcm: bytes, sample_rate: int, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def wav_duration_s(wav_bytes: bytes) -> float:
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        return round(w.getnframes() / float(w.getframerate() or 1), 2)


# ── Engines ──────────────────────────────────────────────────────────────────

class Engine:
    key = ""
    label = ""

    def __init__(self):
        self._lock = threading.Lock()
        self._loaded = False

    def check(self) -> tuple[bool, str]:          # (verfuegbar, Grund)
        raise NotImplementedError

    def _load(self) -> None:
        raise NotImplementedError

    def _synth(self, text: str) -> bytes:           # -> WAV
        raise NotImplementedError

    def synthesize(self, text: str) -> bytes:
        # Ein Satz nach dem anderen je Engine: begrenzt die CPU-Last und
        # vermeidet Thread-Konflikte in onnxruntime/torch.
        with self._lock:
            if not self._loaded:
                t0 = time.time()
                self._load()
                self._loaded = True
                logger.info("Engine %s geladen (%.1fs)", self.key, time.time() - t0)
            return self._synth(text)


class PiperEngine(Engine):
    key = "piper"
    label = "Piper (Thorsten)"

    def __init__(self):
        super().__init__()
        self.voice_path = os.environ.get("TTS_PIPER_VOICE", "/workspace/tts/de_DE-thorsten-high.onnx")
        self.voice = None

    def check(self) -> tuple[bool, str]:
        try:
            import piper  # noqa: F401
        except ImportError:
            return False, "piper-tts nicht installiert (setup_tts.sh)"
        if not os.path.exists(self.voice_path):
            return False, f"Stimme fehlt: {self.voice_path}"
        return True, ""

    def _load(self) -> None:
        os.environ.setdefault("OMP_NUM_THREADS", os.environ.get("TTS_PIPER_THREADS", "2"))
        from piper import PiperVoice
        self.voice = PiperVoice.load(self.voice_path)

    def _synth(self, text: str) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            self.voice.synthesize_wav(text, w)
        return buf.getvalue()


class ChatterboxEngine(Engine):
    key = "chatterbox"
    label = "Chatterbox (CPU, Test)"

    def __init__(self):
        super().__init__()
        self.model = None

    def check(self) -> tuple[bool, str]:
        if not _env_bool("TTS_CHATTERBOX_ENABLED"):
            return False, "abgeschaltet (TTS_CHATTERBOX_ENABLED)"
        try:
            import chatterbox  # noqa: F401
        except ImportError:
            return False, "chatterbox-tts nicht installiert (setup_tts.sh --chatterbox)"
        return True, ""

    def _load(self) -> None:
        import torch
        torch.set_num_threads(int(os.environ.get("TTS_CHATTERBOX_THREADS", "8")))
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        try:
            self.model = ChatterboxMultilingualTTS.from_pretrained(device="cpu", t3_model="v3")
        except TypeError:                           # aeltere Versionen ohne t3_model
            self.model = ChatterboxMultilingualTTS.from_pretrained(device="cpu")

    def _synth(self, text: str) -> bytes:
        import numpy as np
        import torch
        with torch.inference_mode():
            wav = self.model.generate(text, language_id="de")
        arr = wav.squeeze().detach().cpu().numpy() if hasattr(wav, "detach") else np.asarray(wav).squeeze()
        pcm = (np.clip(arr, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        return pcm16_to_wav(pcm, int(self.model.sr))


# ── Dienst ───────────────────────────────────────────────────────────────────

class TTSService:
    def __init__(self, engines: Optional[dict[str, Engine]] = None, cache_size: int = 256):
        self.engines = engines if engines is not None else {e.key: e for e in (PiperEngine(), ChatterboxEngine())}
        self.cache_size = cache_size
        self._cache: OrderedDict[tuple[str, str], bytes] = OrderedDict()
        self._cache_lock = threading.Lock()

    def list_engines(self) -> list[dict]:
        out = []
        for e in self.engines.values():
            ok, reason = e.check()
            out.append({"key": e.key, "label": e.label, "available": ok, "reason": reason})
        return out

    def synthesize(self, text: str, engine: str) -> tuple[bytes, float, bool]:
        """-> (wav, rechenzeit_s, aus_cache). ValueError bei ungueltiger Anfrage."""
        text = (text or "").strip()
        if not text:
            raise ValueError("leerer Text")
        if len(text) > MAX_CHARS:
            raise ValueError(f"Text zu lang (max. {MAX_CHARS} Zeichen)")
        eng = self.engines.get(engine)
        if eng is None:
            raise ValueError(f"unbekannte Engine: {engine}")
        ok, reason = eng.check()
        if not ok:
            raise LookupError(reason)
        key = (engine, text)
        with self._cache_lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key], 0.0, True
        t0 = time.time()
        wav = eng.synthesize(text)
        dt = round(time.time() - t0, 2)
        with self._cache_lock:
            self._cache[key] = wav
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)
        return wav, dt, False


def make_handler(service: TTSService) -> Callable:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):     # keine Request-Zeilen (keine Texte im Log)
            return

        def _json(self, code: int, obj: dict) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/engines":
                return self._json(200, {"engines": service.list_engines()})
            if self.path == "/health":
                return self._json(200, {"ok": True})
            return self._json(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/synthesize":
                return self._json(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(n) or b"{}")
                wav, dt, cached = service.synthesize(str(data.get("text") or ""), str(data.get("engine") or ""))
            except LookupError as e:
                return self._json(503, {"error": str(e)})
            except (ValueError, json.JSONDecodeError) as e:
                return self._json(422, {"error": str(e)})
            except Exception as e:  # noqa: BLE001
                logger.exception("Synthese fehlgeschlagen")
                return self._json(500, {"error": f"Synthese fehlgeschlagen: {type(e).__name__}"})
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(wav)))
            self.send_header("X-TTS-Synth-S", str(dt))
            self.send_header("X-TTS-Audio-S", str(wav_duration_s(wav)))
            self.send_header("X-TTS-Cached", "1" if cached else "0")
            self.end_headers()
            self.wfile.write(wav)
    return Handler


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("TTS_PORT", "8011")))
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    service = TTSService(cache_size=int(os.environ.get("TTS_CACHE_SIZE", "256")))
    for e in service.list_engines():
        logger.info("Engine %-10s verfuegbar=%s %s", e["key"], e["available"], e["reason"])
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(service))
    logger.info("TTS-Dienst auf %s:%d", a.host, a.port)
    srv.serve_forever()


if __name__ == "__main__":
    main()
