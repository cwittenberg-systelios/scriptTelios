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
    POST /synthesize  {"text": "...", "engine": "piper|chatterbox|chatterbox:<stimme>",
                       "params": {...}  (optional, Hoertest)} -> audio/wav
                      Header X-TTS-Synth-S (Rechenzeit), X-TTS-Audio-S (Laenge)

Umgebung:
    TTS_PIPER_VOICE          Pfad zur .onnx-Stimme
                             (Default /workspace/tts/de_DE-thorsten-high.onnx)
    TTS_PIPER_THREADS        CPU-Threads fuer Piper (Default 2)
    TTS_CHATTERBOX_ENABLED   true = Chatterbox Multilingual anbieten (Default false)
    TTS_CHATTERBOX_THREADS   CPU-Threads fuer Chatterbox (Default 8)
    TTS_CACHE_SIZE           Anzahl gecachter Saetze (Default 256)
    TTS_VOICES_DIR           Referenzstimmen fuer Chatterbox (Default /workspace/tts/voices):
                             <key>.wav (+ <key>.json: label, cfg_weight, exaggeration,
                             temperature) -> Engine "chatterbox:<key>" (v19.38)
    TTS_CHATTERBOX_CFG / _EXAGGERATION / _TEMPERATURE   Defaults 0.3 / 0.5 / 0.8
    TTS_PIPER_LENGTH_SCALE   Sprechtempo Piper, >1 = langsamer (Default 1.1)
    TTS_CHATTERBOX_DEVICE    cpu (Default) | cuda - GPU nur mit >= TTS_GPU_MIN_FREE_GB
                             (Default 5) freiem Grafikspeicher, bei Speicherfehler
                             Rueckfall auf CPU (v19.40). Braucht torch mit CUDA
                             (setup_tts.sh --cuda).
    TTS_WARMUP               true (Default) = beim Start alle Engines laden und
                             Referenzstimmen vorverarbeiten (v19.38.1)

Datenschutz: Texte werden weder geloggt noch gespeichert (nur im RAM-Cache).
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import os
import re
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

    def _synth(self, text: str, params: dict) -> bytes:   # -> WAV
        raise NotImplementedError

    def ensure_loaded(self) -> None:
        if not self._loaded:
            t0 = time.time()
            self._load()
            self._loaded = True
            logger.info("Engine %s geladen (%.1fs)", self.key, time.time() - t0)

    def synthesize(self, text: str, params: Optional[dict] = None) -> bytes:
        # Ein Satz nach dem anderen je Engine: begrenzt die CPU-Last und
        # vermeidet Thread-Konflikte in onnxruntime/torch.
        with self._lock:
            self.ensure_loaded()
            return self._synth(text, params or {})


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

    def _synth(self, text: str, params: dict) -> bytes:
        # v19.38: etwas langsamer (Default 1.1) - Thorsten klingt sonst gehetzt
        ls = float(params.get("length_scale") or os.environ.get("TTS_PIPER_LENGTH_SCALE", "1.1"))
        cfg = None
        try:
            from piper import SynthesisConfig
            cfg = SynthesisConfig(length_scale=ls)
        except Exception:  # noqa: BLE001 - aeltere piper-Versionen
            cfg = None
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            if cfg is not None:
                self.voice.synthesize_wav(text, w, syn_config=cfg)
            else:
                self.voice.synthesize_wav(text, w)
        return buf.getvalue()


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _is_oom(e: BaseException) -> bool:
    m = str(e).lower()
    return "out of memory" in m or "cuda error" in m or "cublas" in m


def choose_device(wanted: str, *, mem_get_info=None, cuda_available=None) -> str:
    """v19.40: TTS_CHATTERBOX_DEVICE = cpu (Default) | cuda.
    cuda nur, wenn eine GPU da ist und mindestens TTS_GPU_MIN_FREE_GB frei sind
    (Default 5 GB: Modell ~3 GB + Luft, damit gemma nicht auf die CPU
    ausweichen muss) - sonst CPU mit Log-Hinweis."""
    wanted = (wanted or "cpu").strip().lower()
    if wanted != "cuda":
        return "cpu"
    try:
        if cuda_available is None or mem_get_info is None:
            import torch
            cuda_available = torch.cuda.is_available() if cuda_available is None else cuda_available
            mem_get_info = mem_get_info or torch.cuda.mem_get_info
        if not cuda_available:
            logger.warning("TTS_CHATTERBOX_DEVICE=cuda, aber keine GPU fuer torch - CPU")
            return "cpu"
        free, _total = mem_get_info()
    except Exception as e:  # noqa: BLE001
        logger.warning("GPU-Pruefung fehlgeschlagen (%s) - CPU", e)
        return "cpu"
    need = _env_float("TTS_GPU_MIN_FREE_GB", 5.0)
    if free / 1024 ** 3 < need:
        logger.warning("Nur %.1f GB Grafikspeicher frei (< %.1f GB) - Chatterbox auf CPU", free / 1024 ** 3, need)
        return "cpu"
    return "cuda"


class ChatterboxEngine(Engine):
    """Chatterbox Multilingual auf der CPU. Die Standard-Referenz des Modells
    ist englisch -> deutlicher Akzent; deshalb v19.38 eigene deutsche
    Referenzstimmen (ChatterboxVoice). Die Conditionals je Stimme werden
    einmal berechnet und gecacht."""
    key = "chatterbox"
    label = "Chatterbox (Standardstimme, englische Referenz)"

    def __init__(self):
        super().__init__()
        self.model = None
        self._default_conds = None
        self._conds: dict[str, object] = {}

    def check(self) -> tuple[bool, str]:
        if not _env_bool("TTS_CHATTERBOX_ENABLED"):
            return False, "abgeschaltet (TTS_CHATTERBOX_ENABLED)"
        try:
            import chatterbox  # noqa: F401
        except ImportError:
            return False, "chatterbox-tts nicht installiert (setup_tts.sh --chatterbox)"
        return True, ""

    device = "cpu"

    def _load(self) -> None:
        import torch
        torch.set_num_threads(int(os.environ.get("TTS_CHATTERBOX_THREADS", "8")))
        self._load_on(choose_device(os.environ.get("TTS_CHATTERBOX_DEVICE", "cpu")))

    def _load_on(self, device: str) -> None:
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        try:
            self.model = ChatterboxMultilingualTTS.from_pretrained(device=device, t3_model="v3")
        except TypeError:                           # aeltere Versionen ohne t3_model
            self.model = ChatterboxMultilingualTTS.from_pretrained(device=device)
        self.device = device
        self._default_conds = getattr(self.model, "conds", None)
        self._conds = {}                            # Conditionals liegen auf dem Geraet
        logger.info("Chatterbox auf %s geladen", device)

    def fall_back_to_cpu(self, reason: str) -> None:
        """v19.40: Grafikspeicher-Fehler auf der GPU -> Modell auf der CPU neu
        laden (langsamer, aber gemma behaelt die GPU). Gilt bis zum Neustart."""
        logger.warning("Chatterbox: %s - Rueckfall auf CPU", reason)
        self.model = None
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001
            pass
        self._load_on("cpu")

    def _gen_kwargs(self, params: dict, voice: Optional[dict]) -> dict:
        v = voice or {}
        return {
            # v19.38: cfg_weight 0.3 statt 0.5 -> ruhigeres Tempo (Doku-Empfehlung)
            "cfg_weight": float(params.get("cfg_weight", v.get("cfg_weight", _env_float("TTS_CHATTERBOX_CFG", 0.3)))),
            "exaggeration": float(params.get("exaggeration", v.get("exaggeration", _env_float("TTS_CHATTERBOX_EXAGGERATION", 0.5)))),
            "temperature": float(params.get("temperature", v.get("temperature", _env_float("TTS_CHATTERBOX_TEMPERATURE", 0.8)))),
        }

    def prepare_voice(self, voice: dict, exaggeration: Optional[float] = None) -> None:
        """Conditionals einer Referenzstimme berechnen und cachen (Aufrufer
        haelt den Lock). v19.38.1: auch fuer das Vorwaermen beim Start."""
        if voice["key"] in self._conds:
            return
        ex = exaggeration if exaggeration is not None else self._gen_kwargs({}, voice)["exaggeration"]
        self.model.prepare_conditionals(voice["wav"], exaggeration=ex)
        self._conds[voice["key"]] = self.model.conds

    def synth_voice(self, text: str, params: dict, voice: Optional[dict]) -> bytes:
        import contextlib

        import numpy as np
        try:
            import torch
            no_grad = torch.inference_mode
        except ImportError:                         # nur in Tests ohne torch
            no_grad = contextlib.nullcontext
        kw = self._gen_kwargs(params, voice)
        if voice:
            self.prepare_voice(voice, kw["exaggeration"])
            self.model.conds = self._conds[voice["key"]]
        elif self._default_conds is not None:
            self.model.conds = self._default_conds
        try:
            with no_grad():
                wav = self.model.generate(text, language_id="de", **kw)
        except RuntimeError as e:
            if self.device == "cpu" or not _is_oom(e):
                raise
            self.fall_back_to_cpu(f"kein Grafikspeicher ({str(e)[:80]})")
            return self.synth_voice(text, params, voice)
        arr = wav.squeeze().detach().cpu().numpy() if hasattr(wav, "detach") else np.asarray(wav).squeeze()
        sr = int(self.model.sr)
        if not params.get("no_trim"):
            arr = trim_tail(arr, sr)
        pcm = (np.clip(arr, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        return pcm16_to_wav(pcm, sr)

    def _synth(self, text: str, params: dict) -> bytes:
        return self.synth_voice(text, params, None)


class ChatterboxVoice(Engine):
    """Eigene Referenzstimme fuer Chatterbox: <TTS_VOICES_DIR>/<key>.wav
    (+ optional <key>.json mit label, cfg_weight, exaggeration, temperature).
    Nutzt Modell und Lock der Chatterbox-Engine."""

    def __init__(self, base: ChatterboxEngine, voice: dict):
        super().__init__()
        self.base = base
        self.voice = voice
        self.key = f"chatterbox:{voice['key']}"
        self.label = f"Chatterbox – {voice.get('label') or voice['key']}"
        self._lock = base._lock

    def check(self) -> tuple[bool, str]:
        ok, reason = self.base.check()
        if not ok:
            return ok, reason
        if not os.path.exists(self.voice["wav"]):
            return False, f"Referenz fehlt: {self.voice['wav']}"
        return True, ""

    def synthesize(self, text: str, params: Optional[dict] = None) -> bytes:
        with self._lock:
            self.base.ensure_loaded()
            return self.base.synth_voice(text, params or {}, self.voice)


VOICE_KEY_RE = re.compile(r"^[a-z0-9_-]{1,32}$")


def discover_voices(voices_dir: str) -> list[dict]:
    """Referenzstimmen im Ordner: <key>.wav, optional <key>.json."""
    out: list[dict] = []
    if not voices_dir or not os.path.isdir(voices_dir):
        return out
    for fn in sorted(os.listdir(voices_dir)):
        key, ext = os.path.splitext(fn)
        if ext.lower() != ".wav" or not VOICE_KEY_RE.match(key):
            continue
        meta: dict = {}
        jpath = os.path.join(voices_dir, key + ".json")
        if os.path.exists(jpath):
            try:
                with open(jpath, encoding="utf-8") as fh:
                    meta = json.load(fh) or {}
            except (OSError, ValueError):
                logger.warning("Stimme %s: %s nicht lesbar", key, jpath)
        v = {"key": key, "wav": os.path.join(voices_dir, fn), "label": meta.get("label") or key}
        for k in ("cfg_weight", "exaggeration", "temperature"):
            if isinstance(meta.get(k), (int, float)):
                v[k] = float(meta[k])
        out.append(v)
    return out


# ── Textbereinigung und Nachlauf-Schnitt (v19.38) ────────────────────────────

_ABK = [
    (r"\bz\.\s?B\.", "zum Beispiel"), (r"\bd\.\s?h\.", "das heißt"), (r"\bu\.\s?a\.", "unter anderem"),
    (r"\bbzw\.", "beziehungsweise"), (r"\bggf\.", "gegebenenfalls"), (r"\bca\.", "circa"),
    (r"\busw\.", "und so weiter"), (r"\bNr\.", "Nummer"), (r"\bDr\.", "Doktor"), (r"\bevtl\.", "eventuell"),
]


# v19.38.2: Einzelbuchstaben-Kuerzel ("Frau K.") ausgesprochen - Chatterbox
# erfand bei "K." am Satzende Silben ("Frau KaKa", "Frau Kakamas").
BUCHSTABEN = {
    "A": "A", "B": "Be", "C": "Ze", "D": "De", "E": "E", "F": "Eff", "G": "Ge", "H": "Ha",
    "I": "I", "J": "Jott", "K": "Ka", "L": "Ell", "M": "Emm", "N": "Enn", "O": "O", "P": "Pe",
    "Q": "Ku", "R": "Err", "S": "Ess", "T": "Te", "U": "U", "V": "Fau", "W": "We", "X": "Ix",
    "Y": "Üpsilon", "Z": "Zett", "Ä": "Ä", "Ö": "Ö", "Ü": "Ü",
}
_KUERZEL_RE = re.compile(r"\b(Frau|Herrn|Herr|Fr\.|Hr\.)\s+([A-ZÄÖÜ])\.(?=\s|$|[.,;:!?])(\s*)(\S?)")


def _kuerzel(m: "re.Match") -> str:
    anrede = {"Fr.": "Frau", "Hr.": "Herr"}.get(m.group(1), m.group(1))
    folgt, naechstes = m.group(3), m.group(4)
    # Grossbuchstabe danach -> der Punkt war zugleich Satzende
    ende = "." if (naechstes and naechstes[0].isupper()) or not naechstes else ""
    return f"{anrede} {BUCHSTABEN.get(m.group(2), m.group(2))}{ende}{folgt}{naechstes}"


def normalize_for_tts(text: str) -> str:
    """Macht Text sprechbar: Anfuehrungszeichen weg (loesen bei Chatterbox
    Nachlaute aus), gaengige Abkuerzungen ausschreiben, Satzzeichen am Ende."""
    t = text or ""
    t = re.sub(r"[„“”\"«»‚‘’]", "", t)
    t = re.sub(r"(?<=\w)'(?=\w)", "", t)
    t = _KUERZEL_RE.sub(_kuerzel, t)
    for pat, rep in _ABK:
        t = re.sub(pat, rep, t)
    t = re.sub(r"[\u2013\u2014]", ", ", t)          # Gedankenstrich -> Pause
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\s+([,.!?;:])", r"\1", t)
    t = re.sub(r"\.{2,}", ".", t)                     # "K.." nach Anfuehrungszeichen
    if t and t[-1] not in ".!?":
        t += "."
    return t


def trim_tail(arr, sr: int, frame_ms: int = 20, gap_s: float = 0.25, blip_s: float = 0.6, pad_s: float = 0.12):
    """Schneidet Nachlaute ab: ein kurzer Laut (< blip_s) nach einer Pause
    (>= gap_s) am Ende wird entfernt (bis zu zwei Mal), danach Stille bis auf
    pad_s gekuerzt. Wirkt nur auf das Ende - der Satz selbst bleibt unangetastet."""
    import numpy as np
    x = np.asarray(arr, dtype="float32").reshape(-1)
    n = max(1, int(sr * frame_ms / 1000))
    if x.size < n * 5:
        return x
    frames = x[: (x.size // n) * n].reshape(-1, n)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    thr = max(1e-4, float(rms.max()) * 0.06)
    voiced = rms > thr
    end = len(voiced)
    for _ in range(2):
        idx = np.nonzero(voiced[:end])[0]
        if idx.size == 0:
            break
        last = idx[-1] + 1
        start = last
        while start > 0 and voiced[start - 1]:
            start -= 1
        gap_end = start
        gap_start = gap_end
        while gap_start > 0 and not voiced[gap_start - 1]:
            gap_start -= 1
        blip = (last - start) * frame_ms / 1000
        gap = (gap_end - gap_start) * frame_ms / 1000
        if gap_start > 0 and gap >= gap_s and blip < blip_s:
            end = gap_start
            continue
        end = last
        break
    cut = min(x.size, end * n + int(pad_s * sr))
    return x[:cut]


# ── Dienst ───────────────────────────────────────────────────────────────────

class TTSService:
    def __init__(self, engines: Optional[dict[str, Engine]] = None, cache_size: int = 256,
                 voices_dir: Optional[str] = None):
        if engines is None:
            self._chatterbox = ChatterboxEngine()
            engines = {e.key: e for e in (PiperEngine(), self._chatterbox)}
        else:
            self._chatterbox = next((e for e in engines.values() if isinstance(e, ChatterboxEngine)), None)
        self.engines = engines
        self.voices_dir = voices_dir if voices_dir is not None else os.environ.get("TTS_VOICES_DIR", "/workspace/tts/voices")
        self.cache_size = cache_size
        self._cache: OrderedDict[tuple, bytes] = OrderedDict()
        self._cache_lock = threading.Lock()
        self.refresh_voices()

    def refresh_voices(self) -> None:
        """Stimmen-Ordner neu einlesen (neue .wav ohne Neustart nutzbar)."""
        if self._chatterbox is None:
            return
        found = {f"chatterbox:{v['key']}": v for v in discover_voices(self.voices_dir)}
        for k in [k for k in self.engines if k.startswith("chatterbox:") and k not in found]:
            del self.engines[k]
        for k, v in found.items():
            cur = self.engines.get(k)
            if not isinstance(cur, ChatterboxVoice) or cur.voice != v:
                if isinstance(cur, ChatterboxVoice):
                    self._chatterbox._conds.pop(v["key"], None)   # Referenz geaendert
                self.engines[k] = ChatterboxVoice(self._chatterbox, v)

    def warmup(self) -> dict:
        """v19.38.1: Nach dem Start alle verfuegbaren Engines laden und die
        Referenzstimmen vorverarbeiten - sonst wartet der erste Satz nach
        jedem Pod-Start auf Modell-Laden (Chatterbox ~20-60 s CPU) und
        Referenz (~1-5 s je Stimme). Laeuft im Hintergrund; Anfragen werden
        waehrenddessen angenommen (sie warten ggf. am Lock der Engine)."""
        self.refresh_voices()
        result: dict = {}
        for key, eng in list(self.engines.items()):
            ok, reason = eng.check()
            if not ok:
                result[key] = f"uebersprungen: {reason}"
                continue
            t0 = time.time()
            try:
                if isinstance(eng, ChatterboxVoice):
                    with eng._lock:
                        eng.base.ensure_loaded()
                        eng.base.prepare_voice(eng.voice)
                else:
                    with eng._lock:
                        eng.ensure_loaded()
                result[key] = f"bereit ({time.time() - t0:.1f}s)"
            except Exception as e:  # noqa: BLE001 - Vorwaermen darf den Dienst nie stoppen
                logger.warning("Vorwaermen %s fehlgeschlagen: %s", key, e)
                result[key] = f"Fehler: {type(e).__name__}"
            logger.info("Vorwaermen %-24s %s", key, result[key])
        return result

    def list_engines(self) -> list[dict]:
        self.refresh_voices()
        out = []
        for e in self.engines.values():
            ok, reason = e.check()
            out.append({"key": e.key, "label": e.label, "available": ok, "reason": reason})
        return out

    def synthesize(self, text: str, engine: str, params: Optional[dict] = None) -> tuple[bytes, float, bool]:
        """-> (wav, rechenzeit_s, aus_cache). ValueError bei ungueltiger Anfrage.
        params (nur fuer das Hoertest-Skript): cfg_weight, exaggeration,
        temperature, length_scale, no_trim."""
        text = (text or "").strip()
        if not text:
            raise ValueError("leerer Text")
        if len(text) > MAX_CHARS:
            raise ValueError(f"Text zu lang (max. {MAX_CHARS} Zeichen)")
        text = normalize_for_tts(text)
        if engine not in self.engines and engine.startswith("chatterbox:"):
            self.refresh_voices()
        eng = self.engines.get(engine)
        if eng is None:
            raise ValueError(f"unbekannte Engine: {engine}")
        ok, reason = eng.check()
        if not ok:
            raise LookupError(reason)
        p = {k: v for k, v in (params or {}).items()
             if k in ("cfg_weight", "exaggeration", "temperature", "length_scale", "no_trim")}
        key = (engine, text, json.dumps(p, sort_keys=True))
        with self._cache_lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key], 0.0, True
        t0 = time.time()
        wav = eng.synthesize(text, p)
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
                cb = getattr(service, "_chatterbox", None)          # v19.40.1: Geraet mitliefern
                dev = getattr(cb, "device", None) if cb is not None and getattr(cb, "_loaded", False) else None
                return self._json(200, {"engines": service.list_engines(), "chatterbox_device": dev})
            if self.path == "/health":
                cb = getattr(service, "_chatterbox", None)
                return self._json(200, {"ok": True, "chatterbox_device": getattr(cb, "device", None),
                                        "chatterbox_geladen": bool(cb and cb._loaded)})
            return self._json(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/synthesize":
                return self._json(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(n) or b"{}")
                params = data.get("params") if isinstance(data.get("params"), dict) else None
                wav, dt, cached = service.synthesize(str(data.get("text") or ""), str(data.get("engine") or ""), params)
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
    if _env_bool("TTS_WARMUP", True):
        threading.Thread(target=service.warmup, name="tts-warmup", daemon=True).start()
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(service))
    logger.info("TTS-Dienst auf %s:%d", a.host, a.port)
    srv.serve_forever()


if __name__ == "__main__":
    main()
