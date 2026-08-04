"""
Transkriptions-Service.

Ausschliesslich faster-whisper (lokal, On-Premise).
Kein externer API-Aufruf – Audiodateien verlassen nie den Server.
Nach der Transkription wird die Audiodatei geloescht (Datenschutz).

Lange Aufnahmen (>10 Min) werden automatisch in Chunks aufgeteilt
um CUDA-OOM-Fehler bei langen Therapiegesprächen zu vermeiden.
"""
import logging
import subprocess
import tempfile
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)


# Hintergrund-Warmup-Tasks (Referenzhaltung gegen GC, RUF006)
_BG_WARMUP_TASKS: set = set()

# Maximale Chunk-Laenge in Sekunden (15 Minuten)
# Groessere Chunks reduzieren Setup-Overhead (VAD-Initialisierung, Modell-Transfer)
# Bei 71-Min-Aufnahme: 5 Chunks statt 8.
CHUNK_MAX_SECONDS = 900

# Klinischer Initial-Prompt fuer Whisper.
# Verbessert Erkennung von Fachbegriffen und Eigennamen die Whisper ohne
# Kontext oft verschluckt oder falsch schreibt. Whisper nutzt diesen Text als
# "Kontext vor der Aufnahme" – kein echter Prompt, sondern ein Hint der die
# Wahrscheinlichkeitsverteilung des Tokenizers beeinflusst.
#
# WICHTIG: Hier KEINE schulenspezifischen Verfahrensbegriffe (IFS,
# Manager-Anteil, Self-Energy, Exile, Feuerwehr-Anteil etc.) auflisten.
# Whisper schreibt sonst neutrale Aussagen wie "ein Anteil von mir" zu
# "Manager-Anteil" um, was das Transkript verfaelscht und dem nachgelagerten
# LLM falsche Evidenz fuer IFS-Sprache liefert. Die Erkennung dieser Begriffe
# leidet dadurch nicht spuerbar – sie sind nicht so selten, dass Whisper
# sie ohne Hint nicht koennte; sie waeren aber so haeufig im Hint, dass
# Whisper sie auch dort sieht wo sie nicht gesagt werden.
WHISPER_INITIAL_PROMPT = (
    "Psychotherapeutisches Gespräch. Fachbegriffe: "
    "innere Anteile, Schutzmechanismus, Schutzmuster, "
    "hypnosystemisch, systemische Therapie, Ressourcenorientierung, "
    "Auftragsklärung, Verlaufsnotiz, Anamnese, Epikrise, Entlassbericht, "
    "Verlängerungsantrag, Kostenübernahme, Krankenkasse, "
    "AMDP, Psychopathologie, Affektregulation, Dissoziation, "
    "Bindungsmuster, Traumatisierung, "
    "Therapeut, Klient, Klientin, Sitzung, Intervention. "
    # 2026-07-03 (Live-Fund Herr W.): institutionelle/organisatorische
    # Klinikbegriffe ergaenzt. Whisper kannte z.B. "Familiengespraech" nicht
    # gut genug und halluzinierte "Bruder-Treibens-Gespraech". Diese Begriffe
    # sind KORREKTE, aber im Alltagssprachgebrauch seltene Institutions-
    # vokabeln - genau der Fall, fuer den ein Initial-Prompt gedacht ist.
    "Klinikorganisation: Familiengespräch, Transfergespräch, "
    "Angehörigengespräch, Bezugsgruppe, Bezugstherapeut, Ü-Gruppe, "
    "Konzeptgruppe, Sprechstundenzeit, ambulante Therapeutin, "
    "stationärer Aufenthalt."
)

# Modell-Cache – einmalig laden, dann wiederverwenden
_model_cache: dict = {}
_diarization_pipeline = None   # pyannote Pipeline-Cache


# ── Watchdog-Konstanten (v18) ────────────────────────────────────────────────
#
# Variante 3: Statt eines fixen Timeouts ueberwachen wir den Fortschritt der
# Transkription. Faster-whisper liefert Segmente als Generator - wir
# konsumieren ihn segment-fuer-segment und protokollieren bei jedem Segment
# einen Zeitstempel. Zwei Trip-Wires:
#
#   STALL_TIMEOUT_SEC: kommt 60s lang kein neues Segment -> der Process haengt.
#                      Typische Ursache: CUDA-Operation blockiert, Whisper-
#                      interne Schleife haengt, GPU-Lock-Konflikt.
#
#   MAX_REALTIME_FACTOR: Wallclock-Zeit darf maximal X-mal so lang sein wie
#                        die Audio-Dauer (plus OVERHEAD-Sockel). Faengt den
#                        Fall "Whisper macht Mini-Fortschritt aber unrealistisch
#                        langsam" - typisch fuer Halluzinationsschleifen bei
#                        degeneriertem Audio (Stille, Rauschen, Loops).
#
# Diese Werte sind ueber Config justierbar:
STALL_TIMEOUT_SEC    = getattr(settings, "WHISPER_STALL_TIMEOUT_SEC",    60)
MAX_REALTIME_FACTOR  = getattr(settings, "WHISPER_MAX_REALTIME_FACTOR",  2.0)
TRANSCRIBE_OVERHEAD  = getattr(settings, "WHISPER_TRANSCRIBE_OVERHEAD",  60)

# Eigene Exception fuer differenzierte Fehlerbehandlung im Chunk-Loop
class TranscriptionStalledError(RuntimeError):
    """Whisper hat 60s lang kein neues Segment geliefert."""

class TranscriptionTooSlowError(RuntimeError):
    """Wallclock > Audio-Dauer * MAX_REALTIME_FACTOR + OVERHEAD."""


def _transcribe_audio_segment(
    model,
    audio_path: str,
    timeout: int = 300,   # Legacy-Parameter, wird nicht mehr direkt genutzt
                          # (Watchdog ist jetzt verantwortlich), bleibt fuer
                          # Backwards-Compat in der Signatur
) -> tuple:
    """
    Transkribiert ein Audio-Segment mit beam_size=1 (Greedy) und
    Watchdog-Ueberwachung.

    Watchdog-Logik (Variante 3):
    - Konsumiert den faster-whisper Generator segment-fuer-segment.
    - Jedes Segment aktualisiert einen Heartbeat-Zeitstempel.
    - Watchdog-Thread prueft alle 5s zwei Bedingungen:
        a) Heartbeat-Alter > STALL_TIMEOUT_SEC (60s default) -> Stall
        b) Gesamt-Wallclock > Audio-Dauer * MAX_REALTIME_FACTOR + OVERHEAD
           -> zu langsam (Halluzinationsschleife)
    - Bei Trip wird ein stop-Event gesetzt; der Consumer-Thread bricht beim
      naechsten Generator-Yield ab. Faster-whisper-interne Berechnungen
      koennen nicht hart abgebrochen werden, aber der Thread terminiert
      sobald die laufende CUDA-Operation zurueckkommt.

    beam_size=1 (Greedy) ist klinisch ausreichend:
    - Das LLM glaettet kleinere Transkriptionsfehler im Folgeprozess
    - 30-50% schneller als beam_size=2
    - temperature=[0,...0.8] kompensiert Greedy-Schwaechen
    """
    import threading
    import time as _time

    # Audio-Dauer fuer MAX_REALTIME-Bedingung
    try:
        audio_duration = _get_duration(Path(audio_path))
    except Exception:
        audio_duration = 0.0  # Fallback: nur Stall-Detection, kein Realtime-Cap
    hard_ceiling_sec = (
        audio_duration * MAX_REALTIME_FACTOR + TRANSCRIBE_OVERHEAD
        if audio_duration > 0
        else None
    )

    transcribe_kwargs = dict(
        language="de",
        beam_size=1,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        word_timestamps=False,
        initial_prompt=WHISPER_INITIAL_PROMPT,
        temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
    )

    # Geteilter Zustand zwischen Consumer und Watchdog
    state = {
        "last_heartbeat": _time.monotonic(),
        "last_audio_pos": 0.0,
        "segments": [],
        "info": None,
        "exc":  None,
    }
    stop_event = threading.Event()
    started_at = _time.monotonic()

    def _consumer():
        try:
            gen, info = model.transcribe(audio_path, **transcribe_kwargs)
            state["info"] = info
            for seg in gen:
                if stop_event.is_set():
                    # Watchdog hat ausgeloest - sauber raus, was wir bisher
                    # haben behalten wir
                    break
                state["segments"].append(seg)
                state["last_heartbeat"] = _time.monotonic()
                state["last_audio_pos"] = float(getattr(seg, "end", 0.0) or 0.0)
        except Exception as e:
            state["exc"] = e

    consumer = threading.Thread(target=_consumer, daemon=True)
    consumer.start()

    # Watchdog-Loop im Caller-Thread (poll alle 5s)
    poll_interval = 5.0
    trip_reason: "tuple[str, str] | None" = None
    while consumer.is_alive():
        consumer.join(timeout=poll_interval)
        if not consumer.is_alive():
            break
        now = _time.monotonic()

        # Trip 1: Stall (kein Heartbeat seit STALL_TIMEOUT_SEC)
        stalled_for = now - state["last_heartbeat"]
        if stalled_for > STALL_TIMEOUT_SEC:
            trip_reason = (
                "stall",
                f"Kein Fortschritt seit {stalled_for:.0f}s "
                f"(letzte Audio-Position: {state['last_audio_pos']:.0f}s "
                f"von {audio_duration:.0f}s)"
            )
            break

        # Trip 2: zu langsam (Wallclock > Audio*RTF + OVERHEAD)
        if hard_ceiling_sec is not None:
            elapsed = now - started_at
            if elapsed > hard_ceiling_sec:
                trip_reason = (
                    "too_slow",
                    f"Wallclock {elapsed:.0f}s > Audio {audio_duration:.0f}s "
                    f"x {MAX_REALTIME_FACTOR} + {TRANSCRIBE_OVERHEAD}s "
                    f"({hard_ceiling_sec:.0f}s ceiling)"
                )
                break

    # Watchdog hat ausgeloest -> Consumer-Thread soll sauber rauslaufen
    if trip_reason is not None:
        stop_event.set()
        # Geben dem Thread noch 30s zum Beenden (laufende CUDA-Operation
        # kann nicht hart gekillt werden, kommt aber meist innerhalb
        # weniger Sekunden zurueck)
        consumer.join(timeout=30)
        kind, msg = trip_reason
        if kind == "stall":
            raise TranscriptionStalledError(msg)
        else:
            raise TranscriptionTooSlowError(msg)

    # Consumer ist sauber durchgelaufen
    if state["exc"] is not None:
        raise state["exc"]
    if state["info"] is None:
        raise RuntimeError("Whisper hat kein info-Objekt geliefert (interner Fehler)")
    return state["segments"], state["info"], 1




def _get_diarization_pipeline():
    """
    Lädt die pyannote Speaker-Diarization-Pipeline (gecacht).
    Gibt None zurück wenn pyannote nicht installiert oder deaktiviert ist.
    """
    global _diarization_pipeline
    if not settings.DIARIZATION_ENABLED:
        return None
    if _diarization_pipeline is not None:
        return _diarization_pipeline
    try:
        from pyannote.audio import Pipeline
        import torch
        logger.info("Lade pyannote Diarization-Pipeline: %s", settings.DIARIZATION_MODEL)
        pipeline = Pipeline.from_pretrained(
            settings.DIARIZATION_MODEL,
            token=settings.DIARIZATION_HF_TOKEN or None,
        )
        device = "cuda" if settings.WHISPER_DEVICE == "cuda" else "cpu"
        pipeline = pipeline.to(torch.device(device))
        _diarization_pipeline = pipeline
        logger.info("pyannote Pipeline geladen auf %s", device)
        return pipeline
    except ImportError:
        logger.warning(
            "pyannote.audio nicht installiert – Pausen-Heuristik wird verwendet. "
            "Installation: pip install pyannote.audio"
        )
        return None
    except Exception as e:
        logger.warning("pyannote Pipeline konnte nicht geladen werden: %s", e)
        return None


def _to_wav_for_diarization(audio_path: Path, tmp_dir: Path) -> Path:
    """
    Konvertiert eine Audiodatei zu 16kHz-Mono-WAV fuer pyannote.
    pyannote kann Browser-webm nicht direkt lesen (torchcodec fehlt →
    AudioDecoder-Fehler). WAV umgeht das komplett.
    """
    wav_path = tmp_dir / (audio_path.stem + "_diarization.wav")
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(audio_path),
            "-ar", "16000", "-ac", "1", "-f", "wav",
            str(wav_path),
        ],
        capture_output=True, check=True,
    )
    return wav_path


def _diarize(audio_path: Path) -> list[dict] | None:
    """
    Führt Speaker Diarization auf einer Audiodatei aus.
    Gibt eine Liste von {start, end, speaker} Segmenten zurück,
    oder None wenn Diarization nicht verfügbar ist.

    Sprecher-Labels werden normalisiert: SPEAKER_00 → "A", SPEAKER_01 → "B"
    (konsistent mit dem bisherigen [A]/[B]-Format für das LLM).

    Audio wird vorab zu 16kHz-Mono-WAV konvertiert damit pyannote kein
    torchcodec braucht (Browser-webm → AudioDecoder-Fehler ohne torchcodec).
    """
    pipeline = _get_diarization_pipeline()
    if pipeline is None:
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="systelios_diar_") as tmp_dir:
            wav_path = _to_wav_for_diarization(audio_path, Path(tmp_dir))
            # v19 fix: Pyannote ohne torchcodec schlaegt mit
            # "name 'AudioDecoder' is not defined" (NameError, nicht ImportError!)
            # fehl wenn pyannote intern decodieren muss. Workaround:
            # Waveform per soundfile laden (kein torch-Backend-Roulette) und
            # als Dict an die Pipeline geben - umgeht pyannotes Decoder komplett.
            #
            # Reihenfolge der Loader (Fallback-Kette):
            #   1. soundfile  - ffmpeg-frei, liest WAV/FLAC/OGG nativ
            #   2. torchaudio - falls soundfile fehlt
            #   3. filepath   - letzter Notnagel (braucht torchcodec)
            try:
                import soundfile as sf
                import torch
                data, sample_rate = sf.read(str(wav_path), dtype="float32")
                # soundfile gibt (samples,) mono oder (samples, channels) stereo
                # pyannote erwartet (channels, samples)
                if data.ndim == 1:
                    waveform = torch.from_numpy(data).unsqueeze(0)
                else:
                    waveform = torch.from_numpy(data.T)
                diarization = pipeline(
                    {"waveform": waveform, "sample_rate": sample_rate}
                )
            except ImportError:
                try:
                    import torch, torchaudio
                    waveform, sample_rate = torchaudio.load(str(wav_path))
                    diarization = pipeline(
                        {"waveform": waveform, "sample_rate": sample_rate}
                    )
                except ImportError:
                    # weder soundfile noch torchaudio verfuegbar - filepath-Fallback
                    diarization = pipeline(str(wav_path))
        # ── pyannote 4.x API-Kompatibilitaet ─────────────────────────────────
        # pyannote.audio 4.0 hat die Pipeline-Rueckgabe geaendert:
        #   - 3.x: pipeline(...) -> Annotation (hat .itertracks(yield_label=True))
        #   - 4.x: pipeline(...) -> DiarizeOutput (Annotation liegt in
        #          .speaker_diarization)
        # Wir extrahieren defensiv die Annotation aus beiden Varianten.
        annotation = (
            getattr(diarization, "speaker_diarization", None)
            or getattr(diarization, "diarization", None)
            or diarization
        )
        if not hasattr(annotation, "itertracks"):
            raise RuntimeError(
                f"Pyannote-Output hat keine itertracks-Methode "
                f"(Typ: {type(diarization).__name__}, "
                f"Attribute: {[a for a in dir(diarization) if not a.startswith('_')][:10]})"
            )
        segments = []
        speaker_map: dict[str, str] = {}
        labels = ["A", "B", "C", "D"]  # max 4 Sprecher realistisch
        for turn, _, speaker in annotation.itertracks(yield_label=True):
            if speaker not in speaker_map:
                idx = len(speaker_map)
                speaker_map[speaker] = labels[idx] if idx < len(labels) else str(idx)
            segments.append({
                "start":   turn.start,
                "end":     turn.end,
                "speaker": speaker_map[speaker],
            })
        logger.info(
            "Diarization: %d Segmente, %d Sprecher erkannt",
            len(segments), len(speaker_map)
        )
        return segments
    except Exception as e:
        logger.warning("Diarization fehlgeschlagen: %s – Pausen-Heuristik als Fallback", e)
        return None


def _assign_speaker_from_diarization(
    seg_start: float,
    seg_end: float,
    diarization: list[dict],
) -> str | None:
    """
    Findet den Sprecher für ein Whisper-Segment anhand zeitlicher Überlappung
    mit den pyannote-Diarization-Segmenten.
    Gibt den Sprecher mit der größten Überlappung zurück, oder None.
    """
    best_speaker = None
    best_overlap = 0.0
    for d in diarization:
        overlap = min(seg_end, d["end"]) - max(seg_start, d["start"])
        if overlap > best_overlap:
            best_overlap = overlap
            best_speaker = d["speaker"]
    return best_speaker


def _get_model(device: str, compute_type: str):
    """Gibt gecachtes Whisper-Modell zurueck, laed bei Bedarf neu.

    v19.7: Local-first-Strategie. Erst Ladeversuch ausschliesslich aus dem
    lokalen HF-Cache (local_files_only=True → kein Netzwerkkontakt, immun
    gegen HF-Hub-Stoerungen wie 504 Gateway Timeout). Nur wenn das Modell
    noch nie geladen wurde, Fallback auf Online-Download. Schlaegt auch der
    fehl, wird der rohe HfHubHTTPError in eine verstaendliche Fehlermeldung
    uebersetzt (Silent-Failure-Prinzip: laut & diagnostizierbar).
    """
    from faster_whisper import WhisperModel
    key = (settings.WHISPER_MODEL, device, compute_type)
    if key not in _model_cache:
        logger.info(
            "Whisper-Modell laden: %s auf %s (%s)",
            settings.WHISPER_MODEL, device, compute_type,
        )
        try:
            _model_cache[key] = WhisperModel(
                settings.WHISPER_MODEL,
                device=device,
                compute_type=compute_type,
                local_files_only=True,
            )
            logger.info(
                "Whisper '%s' aus lokalem HF-Cache geladen (offline, "
                "kein Hub-Kontakt).", settings.WHISPER_MODEL,
            )
        except Exception as cache_miss:
            logger.warning(
                "Whisper '%s' nicht im lokalen HF-Cache (%s) – "
                "lade von HuggingFace Hub (~3GB bei large-v3).",
                settings.WHISPER_MODEL, cache_miss,
            )
            try:
                _model_cache[key] = WhisperModel(
                    settings.WHISPER_MODEL,
                    device=device,
                    compute_type=compute_type,
                )
            except Exception as hub_err:
                raise RuntimeError(
                    f"Whisper-Modell '{settings.WHISPER_MODEL}' konnte weder "
                    f"aus dem lokalen HF-Cache noch von HuggingFace Hub "
                    f"geladen werden. Hub-Fehler: {hub_err}. "
                    "Wahrscheinliche Ursache: HuggingFace-Stoerung (z.B. 504 "
                    "Gateway Timeout) oder fehlende Netzverbindung bei leerem "
                    "Cache. Abhilfe: spaeter erneut versuchen oder Modell "
                    "vorab in den Cache laden (HF_HOME=/workspace/hf-cache, "
                    "siehe runpod-start.sh)."
                ) from hub_err
    return _model_cache[key]


async def check_whisper_model_available() -> bool:
    """Startup-Check (analog check_summary_model_available, v19.7): prueft
    ob das konfigurierte Whisper-Modell im lokalen HF-Cache liegt, OHNE es
    in den VRAM zu laden. Check-only, KEIN Auto-Download (Disk-full-Risiko).
    Laute Warnung wenn der erste Transkriptionsjob von HF Hub nachladen
    muesste und damit von der Hub-Verfuegbarkeit abhaengt (504-Risiko)."""
    import asyncio

    def _probe() -> None:
        # download_model mit local_files_only=True loest nur den Cache-Pfad
        # auf (kein Netzwerkkontakt, kein VRAM); wirft wenn nicht vorhanden.
        from faster_whisper import download_model
        download_model(settings.WHISPER_MODEL, local_files_only=True)

    try:
        await asyncio.to_thread(_probe)
        logger.info(
            "Whisper-Modell '%s' im lokalen HF-Cache vorhanden "
            "(Transkription offline-faehig).", settings.WHISPER_MODEL,
        )
        return True
    except ImportError:
        logger.warning(
            "faster-whisper nicht installiert – Transkription nicht verfuegbar."
        )
        return False
    except Exception:
        logger.warning(
            "Whisper-Modell '%s' NICHT im lokalen HF-Cache – erster "
            "Transkriptionsjob laedt ~3GB von HuggingFace Hub und schlaegt "
            "bei HF-Stoerung (z.B. 504 Gateway Timeout) fehl. Vorab-Download: "
            "HF_HOME=/workspace/hf-cache python -c \"from faster_whisper "
            "import download_model; download_model('%s')\"",
            settings.WHISPER_MODEL, settings.WHISPER_MODEL,
        )
        return False


def _parse_ffmpeg_stderr_duration(stderr: str) -> float:
    """Extrahiert die Audiodauer aus ffmpeg-stderr einer Voll-Dekodierung.

    v19.16 (T1): Zwei Quellen, in dieser Reihenfolge:
      1. 'Duration: HH:MM:SS.xx' aus dem Header - fehlt bei
         MediaRecorder-webm ("Duration: N/A", kein finalisierter Header).
      2. Die LETZTE Fortschrittszeile 'time=HH:MM:SS.xx' - bei vollstaendiger
         Dekodierung (-f null) ist das die echte Dauer, auch ohne Header.
         Belegt an aufnahme-79 (2026-08-04): Header 'N/A', time=01:02:38.71 -
         die alte Version fiel deshalb auf die Dateigroessen-Schaetzung
         zurueck, die 214 s zu kurz lag; die letzten 3,5 Minuten des
         Gespraechs wurden nie transkribiert.
    """
    import re as _re
    m = _re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", stderr)
    if m:
        d = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        if d > 0:
            return d
    times = _re.findall(r"time=(\d+):(\d+):(\d+\.\d+)", stderr)
    if times:
        h, mins, secs = times[-1]
        d = int(h) * 3600 + int(mins) * 60 + float(secs)
        if d > 0:
            return d
    return 0.0


def _estimate_duration_from_size(file_size: int) -> float:
    """v19.16 (T2): Dateigroessen-Schaetzung, bewusst nach OBEN gepuffert.

    Opus@24kbps (audioBitsPerSecond: 24000 im Browser) = 3000 Byte/s.
    Die alte Version teilte durch 1.05 ("Container-Overhead abziehen") und
    machte die Schaetzung damit KUERZER - das Gegenteil des dokumentierten
    Ziels "lieber zu lang schaetzen". Folge: der letzte Audio-Abschnitt
    wurde beim Chunking abgeschnitten. Jetzt +5 % Puffer nach oben.
    """
    return max(file_size / 3000 * 1.05, 1.0)


def _get_duration(file_path: Path) -> float:
    """
    Gibt Audiodauer in Sekunden zurueck.

    Fallback-Kette fuer Browser-webm (kein seekbarer Moov-Atom → ffprobe
    meldet 0.0 oder schlaegt fehl):

    1. ffprobe Standard (schnell, funktioniert fuer mp3/wav/ogg/m4a)
    2. ffprobe mit -count_packets (liest Paketanzahl × Zeitbasis aus dem
       Container – robust fuer webm ohne Duration-Header, ~1-2s overhead)
    3. ffmpeg stderr Duration-Parsing (komplettes Dekodieren, immer korrekt,
       ~5-10s overhead bei langen Dateien – aber wir laufen sowieso danach
       fuer silencedetect, also kein echter Mehraufwand)
    4. Schätzung via Dateigrösse (Opus@24kbps = 180 KB/min, konstant weil
       der Browser mit audioBitsPerSecond: 24000 aufnimmt)
    """
    import re

    # ── Stufe 1: ffprobe Standard ────────────────────────────────────────
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(file_path),
            ],
            capture_output=True, text=True, check=True,
        )
        duration = float(result.stdout.strip())
        if duration > 0:
            return duration
        # duration == 0.0 → Container hat kein Duration-Feld (Browser-webm)
        logger.debug("ffprobe Standard: duration=0 für %s – Fallback 2", file_path.name)
    except FileNotFoundError:
        raise RuntimeError(
            "ffprobe nicht gefunden. Bitte ffmpeg installieren: "
            "'apt-get install -y ffmpeg'"
        )
    except (ValueError, subprocess.CalledProcessError):
        logger.debug("ffprobe Standard fehlgeschlagen für %s – Fallback 2", file_path.name)

    # ── Stufe 2: ffprobe count_packets ──────────────────────────────────
    # Liest Paketanzahl × Zeitbasis – kein vollstaendiges Dekodieren noetig.
    # Funktioniert fuer webm/opus weil die Zeitbasis pro Stream im Header steht.
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "a:0",
                "-count_packets",
                "-show_entries", "stream=nb_read_packets,duration_ts,time_base",
                "-of", "json",
                str(file_path),
            ],
            capture_output=True, text=True, check=True,
        )
        import json as _json
        data = _json.loads(result.stdout)
        streams = data.get("streams", [])
        if streams:
            s = streams[0]
            # Direkte duration_ts × time_base (z.B. "1/1000")
            duration_ts = s.get("duration_ts")
            time_base   = s.get("time_base")
            if duration_ts and time_base and "/" in str(time_base):
                num, den = time_base.split("/")
                duration = int(duration_ts) * int(num) / int(den)
                if duration > 0:
                    logger.info(
                        "ffprobe count_packets: %.1fs für %s", duration, file_path.name
                    )
                    return duration
            # nb_read_packets-Schätzung entfernt: Opus-Paketlänge variiert
            # (Chrome nutzt oft 60ms statt 20ms) → systematisch falsche Dauer
            # bei Browser-webm. Fallback 3 (ffmpeg decode) ist zuverlässig.
    except Exception as e:
        logger.debug("ffprobe count_packets fehlgeschlagen: %s – Fallback 3", e)

    # ── Stufe 3: ffmpeg vollstaendiges Dekodieren (Duration aus stderr) ──
    # Gleicher subprocess wie in _find_silence_splits – bei langen Dateien
    # ~5-15s, aber wir brauchen das sowieso fuer silencedetect.
    try:
        result = subprocess.run(
            ["ffmpeg", "-i", str(file_path), "-f", "null", "-"],
            capture_output=True, text=True,
        )
        duration = _parse_ffmpeg_stderr_duration(result.stderr)
        if duration > 0:
            logger.info(
                "ffmpeg-Dekodierung: %.1fs für %s", duration, file_path.name
            )
            return duration
    except Exception as e:
        logger.debug("ffmpeg-Dekodierung fehlgeschlagen: %s – Fallback 4", e)

    # ── Stufe 4: Schätzung via Dateigrösse ──────────────────────────────
    # Opus@24kbps (audioBitsPerSecond: 24000 im Browser) = 3000 Byte/s.
    # webm-Container-Overhead: ~1-2%, vernachlaessigbar.
    # Ist konservativ: lieber etwas zu lang schätzen als zu kurz (→ kein Timeout).
    try:
        file_size = file_path.stat().st_size
        estimated = _estimate_duration_from_size(file_size)
        logger.warning(
            "Audiodauer unbekannt – Schätzung via Dateigrösse (+5%% Puffer): "
            "%.1f MB → ~%.0fs (~%.1f Min) für %s",
            file_size / 1_048_576, estimated, estimated / 60, file_path.name,
        )
        return estimated
    except Exception as e:
        logger.error("Alle Duration-Methoden fehlgeschlagen: %s", e)

    return 0.0


def _find_silence_splits(file_path: Path, chunk_max: int, duration: float = 0.0) -> list[float]:
    """
    Findet Stille-Grenzen in der Audiodatei via ffmpeg silencedetect.
    Gibt eine sortierte Liste von Schnitt-Zeitpunkten zurueck.
    Schnitte werden so gewaehlt dass Chunks <= chunk_max Sekunden sind.
    duration: bereits bekannte Dauer (0 = noch nicht ermittelt).
    """
    if duration <= 0:
        duration = _get_duration(file_path)

    # Stille-Segmente detektieren (mind. 0.5s Stille bei -40dB).
    # Diesen ffmpeg-Aufruf machen wir unabhaengig von duration,
    # weil wir aus dem stderr auch die Dauer parsen koennen (Fallback).
    result = subprocess.run(
        [
            "ffmpeg", "-i", str(file_path),
            "-af", "silencedetect=noise=-40dB:d=0.5",
            "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )

    # Falls duration nach _get_duration immer noch 0: aus ffmpeg-stderr lesen
    if duration <= 0:
        import re as _re
        m = _re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", result.stderr)
        if m:
            h, mins, secs = int(m.group(1)), int(m.group(2)), float(m.group(3))
            duration = h * 3600 + mins * 60 + secs
            logger.info(
                "_find_silence_splits: Duration aus ffmpeg-stderr: %.1fs", duration
            )

    if duration <= chunk_max:
        return []

    # Stille-Mitten aus stderr parsen
    silence_midpoints = []
    silence_start = None
    for line in result.stderr.splitlines():
        if "silence_start" in line:
            try:
                silence_start = float(line.split("silence_start: ")[1].split()[0])
            except (IndexError, ValueError):
                pass
        elif "silence_end" in line and silence_start is not None:
            try:
                silence_end = float(line.split("silence_end: ")[1].split()[0])
                silence_midpoints.append((silence_start + silence_end) / 2)
                silence_start = None
            except (IndexError, ValueError):
                pass

    # Splits waehlen: alle chunk_max Sekunden an naechstgelegener Stille
    splits = []
    last_split = 0.0
    for target in range(chunk_max, int(duration), chunk_max):
        # Naechste Stille zum Ziel-Zeitpunkt finden
        candidates = [t for t in silence_midpoints if last_split < t <= target + 30]
        if candidates:
            # Naechste Stille zum Ziel-Zeitpunkt
            best = min(candidates, key=lambda t: abs(t - target))
        else:
            # Keine Stille gefunden – hart am Ziel schneiden
            best = float(target)
        splits.append(best)
        last_split = best

    return splits


def _split_audio(file_path: Path, splits: list[float], tmp_dir: Path, duration: float = 0.0) -> list[Path]:
    """Schneidet Audio an den gegebenen Zeitpunkten via ffmpeg.
    
    duration: bekannte Gesamtdauer (0 = unbekannt, dann via ffmpeg ermitteln).
    Als letzte Boundary wird bei unbekannter Dauer kein '-to' uebergeben
    damit ffmpeg den Rest der Datei vollstaendig liest.
    """
    chunks = []
    end_duration = duration if duration > 0 else _get_duration(file_path)
    boundaries = [0.0] + splits + [end_duration]
    suffix = file_path.suffix.lower() or ".wav"

    for i, (start, end) in enumerate(zip(boundaries[:-1], boundaries[1:])):
        chunk_path = tmp_dir / f"chunk_{i:03d}{suffix}"
        cmd = ["ffmpeg", "-y", "-ss", str(start)]
        # v19.16 (T3): Der LETZTE Chunk bekommt NIE ein '-to' - ffmpeg liest
        # bis EOF. Die alte Bedingung (end < end_duration*1.01) schnitt bei
        # unterschaetzter Dauer (Browser-webm ohne Duration-Header, Stufe-4-
        # Schaetzung) das Aufnahme-Ende hart ab: aufnahme-79 verlor so die
        # letzten 214 s. Haenger faengt der per-Chunk-Watchdog ab.
        is_last = (i == len(boundaries) - 2)
        if not is_last and end > 0:
            cmd += ["-to", str(end)]
        cmd += ["-i", str(file_path), "-c", "copy", str(chunk_path)]
        subprocess.run(cmd, capture_output=True, check=True)
        chunks.append(chunk_path)
        logger.debug("Chunk %d: %.1fs – %.1fs → %s", i, start, end, chunk_path.name)

    return chunks


async def _ollama_free_vram() -> None:
    """
    Weist Ollama an das geladene Modell aus dem VRAM zu entladen.
    Nur aufrufen wenn WHISPER_FREE_OLLAMA_VRAM=true (kleine GPUs).
    Auf RTX 4090 nicht nötig – Whisper + Ollama passen gleichzeitig rein.
    """
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{settings.OLLAMA_HOST}/api/chat",
                json={
                    "model": settings.OLLAMA_MODEL,
                    "keep_alive": 0,
                    "messages": [{"role": "user", "content": ""}],
                },
            )
        logger.info("Ollama-Modell aus VRAM entladen (Platz fuer Whisper)")
    except Exception as e:
        logger.debug("Ollama VRAM-Freigabe nicht moeglich (ignoriert): %s", e)


async def _ollama_warmup() -> None:
    """
    Lädt das Ollama-Modell vorab in den VRAM (fire-and-forget).
    Wird nach Whisper-Transkription aufgerufen damit der erste LLM-Aufruf
    nicht 20-30s auf den Kaltstart warten muss.
    keep_alive=-1 = Ollama-Standard (Modell bleibt geladen).
    """
    import httpx
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            await client.post(
                f"{settings.OLLAMA_HOST}/api/chat",
                json={
                    "model":      settings.OLLAMA_MODEL,
                    "keep_alive": -1,
                    "messages":   [{"role": "user", "content": ""}],
                },
            )
        logger.info("Ollama-Modell vorgewaermt und bereit")
    except Exception as e:
        logger.debug("Ollama-Warmup nicht moeglich (ignoriert): %s", e)


def _free_gpu_after_transcription() -> None:
    """Gibt Whisper (CTranslate2) und pyannote (torch) GPU-Speicher frei.

    Wichtig: CTranslate2 (faster-whisper) reagiert NICHT auf
    torch.cuda.empty_cache() – sein VRAM wird erst bei Garbage-Collection des
    WhisperModel-Objekts frei. Darum del aus dem Cache + erzwungenes gc.collect().
    pyannote ist torch-basiert und wird zusaetzlich per empty_cache() geraeumt.

    Gated ueber WHISPER_UNLOAD_AFTER (Default True). Bei False bleibt das alte
    Verhalten (nur Whisper-Cache leeren, kein GC, pyannote bleibt resident).
    """
    global _diarization_pipeline

    # Whisper immer aus dem Cache nehmen (wie bisher).
    _model_cache.clear()

    if not settings.WHISPER_UNLOAD_AFTER:
        logger.info("Whisper-Modell aus Cache entfernt")
        return

    # pyannote-Pipeline von der GPU nehmen und Referenz loeschen.
    if _diarization_pipeline is not None:
        try:
            import torch
            _diarization_pipeline.to(torch.device("cpu"))
        except Exception:
            pass
        _diarization_pipeline = None

    # GC erzwingen -> CTranslate2- und pyannote-Objekte werden zerstoert,
    # ihr VRAM wird freigegeben.
    import gc
    gc.collect()

    # torch-Cache leeren (pyannote-Seite; CTranslate2 ist ueber GC schon frei).
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass

    logger.info("Whisper + pyannote VRAM freigegeben (zurueck an Ollama)")


async def transcribe_audio(file_path: Path) -> dict:
    """
    Transkribiert eine Audiodatei lokal via faster-whisper.
    Lange Aufnahmen werden automatisch in Chunks aufgeteilt.

    VRAM-Strategie (konfigurierbar via WHISPER_FREE_OLLAMA_VRAM):
    - False (Standard, RTX 4090): Whisper und Ollama teilen VRAM.
      large-v3 (~3GB) + qwen3:32b (~5GB) passen gleichzeitig.
      Nach Whisper: Ollama wird vorgewaermt → kein LLM-Kaltstart.
    - True (kleine GPUs <12GB): Ollama wird vor Whisper entladen,
      Whisper bekommt den gesamten VRAM. LLM-Kaltstart nach Transkription
      ist dann unvermeidbar.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError(
            "faster-whisper nicht installiert. "
            "Bitte 'pip install faster-whisper' ausfuehren."
        )

    if settings.WHISPER_FREE_OLLAMA_VRAM:
        await _ollama_free_vram()

    result = await _transcribe_local(file_path)

    # Whisper UND pyannote aus dem VRAM nehmen, damit Ollama/das LLM die
    # volle GPU zurueckbekommt (sonst Partial-Offload auf die CPU).
    _free_gpu_after_transcription()

    # Ollama vorwärmen / keep-alive senden – fire-and-forget.
    # Bei FREE_VRAM=True: lädt das Modell nach Whisper wieder in den VRAM.
    # Bei FREE_VRAM=False: verhindert Ollama-Inaktivitäts-Timeout.
    # Fehler im Warmup werden geloggt aber nie an den Caller weitergegeben.
    import asyncio
    # Referenz halten (RUF006, analog job_queue._spawn_db_task): sonst darf
    # der GC den laufenden Warmup-Task einsammeln. Modul-Set + discard-Callback.
    _warmup_task = asyncio.ensure_future(_ollama_warmup())
    _BG_WARMUP_TASKS.add(_warmup_task)
    _warmup_task.add_done_callback(_BG_WARMUP_TASKS.discard)

    # Audio bleibt erhalten — retention.py loescht nach 24h (v18)

    return result


async def _transcribe_local(file_path: Path) -> dict:
    """faster-whisper lokal mit automatischem Chunking fuer lange Aufnahmen."""
    import asyncio

    def _run() -> dict:
        duration = _get_duration(file_path)
        logger.info("Audio-Dauer: %.1fs (%.1f Min)", duration, duration / 60)

        if duration <= 0:
            # Alle Duration-Methoden haben versagt (sollte nach der neuen
            # Fallback-Kette nicht mehr vorkommen, aber als letzter Safety-Net).
            # Immer chunked transkribieren – _transcribe_chunked hat keinen
            # globalen Timeout sondern nur per-Chunk-Timeouts.
            logger.warning(
                "Audio-Dauer nach allen Fallbacks unbekannt – "
                "erzwinge Chunked-Transkription für %s", file_path.name
            )
            return _transcribe_chunked(file_path, duration=7200.0)

        if duration > CHUNK_MAX_SECONDS:
            return _transcribe_chunked(file_path, duration)
        else:
            return _transcribe_single(file_path, duration)

    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, _run)
    except Exception as e:
        err = str(e).lower()
        is_cuda_oom = (
            "cuda" in err
            or "illegal memory access" in err
            or "out of memory" in err
            or "cublas" in err
        )
        if is_cuda_oom and settings.WHISPER_DEVICE != "cpu":
            logger.warning(
                "CUDA-Fehler (%s) – Fallback auf CPU. "
                "WHISPER_DEVICE=cpu in .env dauerhaft setzen.", e
            )
            _model_cache.clear()
            # Gerät temporaer auf CPU setzen und nochmal versuchen
            original_device = settings.WHISPER_DEVICE
            original_compute = settings.WHISPER_COMPUTE_TYPE
            settings.WHISPER_DEVICE = "cpu"
            settings.WHISPER_COMPUTE_TYPE = "int8"
            try:
                return await loop.run_in_executor(None, _run)
            finally:
                settings.WHISPER_DEVICE = original_device
                settings.WHISPER_COMPUTE_TYPE = original_compute
        raise RuntimeError(f"Transkription fehlgeschlagen: {e}") from e


def _assign_speakers(segments) -> str:
    """
    Einfache Sprecher-Heuristik via Pausen zwischen Segmenten.

    Strategie: Eine Pause > SPEAKER_CHANGE_PAUSE_S zwischen zwei Segmenten
    signalisiert einen Sprecherwechsel. Zwei Sprecher werden abwechselnd
    als [A] und [B] markiert.

    Nicht perfekt – aber fuer Zweigespraeche (Therapeut/Klient) ausreichend
    um dem LLM den Gespraechsverlauf zu strukturieren.
    """
    SPEAKER_CHANGE_PAUSE_S = 1.2  # Pause ab der ein Wechsel angenommen wird

    result_lines = []
    current_speaker = "A"
    last_end = None

    for seg in segments:
        # Sprecherwechsel bei langer Pause
        if last_end is not None:
            pause = seg.start - last_end
            if pause >= SPEAKER_CHANGE_PAUSE_S:
                current_speaker = "B" if current_speaker == "A" else "A"

        text = seg.text.strip()
        if text:
            result_lines.append(f"[{current_speaker}]: {text}")

        last_end = seg.end

    return "\n".join(result_lines)


def _preprocess_transcript(text: str) -> str:
    """
    Bereinigt das Whisper-Transkript ohne inhaltliche Informationen zu verlieren.

    Schritt 1 – Füllwörter entfernen:
      Einzeln stehende Laute und Geraeusche die Whisper transkribiert
      aber keine Information tragen: äh, ähm, hm, mhm, ja ja, genau genau ...
      Nur wenn sie alleine in einem Segment stehen oder am Satzanfang/-ende.

    Schritt 2 – Exakte Duplikat-Segmente entfernen:
      Whisper halluziniert bei Stille manchmal denselben Satz mehrfach.
      Aufeinanderfolgende identische Zeilen werden auf eine reduziert.

    Schritt 3 – Sehr kurze bedeutungslose Segmente entfernen:
      Einzel-Zeichen oder reine Interpunktion ohne Text.
    """
    import re

    # Füllwörter die als komplettes Segment auftreten (case-insensitive)
    FILLER_PATTERN = re.compile(
        r"^(\[.\]:\s*)?"          # optionaler Sprecher-Marker
        r"(äh+|ähm+|hm+|mhm+|mmh+|hmm+|uh+|uhm+|em+|"
        r"ja\.?|nein\.?|okay\.?|ok\.?|"
        r"genau\.?|richtig\.?|stimmt\.?|"
        r"gut\.?|super\.?|alles klar\.?)"
        r"\s*$",
        re.IGNORECASE,
    )

    # Füllwörter am Satzanfang (nach Sprecher-Marker)
    FILLER_START = re.compile(
        r"(\[.\]:\s*)(äh+\s+|ähm+\s+|hm+\s+|mhm+\s+|mmh+\s+)",
        re.IGNORECASE,
    )

    lines = text.splitlines()
    result = []
    prev_clean = None

    for line in lines:
        # Füllwort-Zeile komplett überspringen
        if FILLER_PATTERN.match(line.strip()):
            continue

        # Füllwort am Satzanfang entfernen
        line = FILLER_START.sub(r"\1", line)

        # Sehr kurze Segmente überspringen (nur Satzzeichen, <3 Zeichen nach Marker)
        content = re.sub(r"^\[.\]:\s*", "", line).strip()
        if len(content) < 3:
            continue

        # Exakte Duplikate aufeinanderfolgender Zeilen entfernen
        clean = line.strip()
        if clean == prev_clean:
            continue

        result.append(clean)
        prev_clean = clean

    cleaned = "\n".join(result)

    orig_chars = len(text)
    new_chars  = len(cleaned)
    if orig_chars > 0:
        saved = (orig_chars - new_chars) / orig_chars * 100
        logger.info(
            "Transkript bereinigt: %d → %d Zeichen (%.1f%% reduziert)",
            orig_chars, new_chars, saved,
        )

    return cleaned


def _transcribe_single(file_path: Path, duration: float) -> dict:
    """
    Einzelne Datei transkribieren.
    Verwendet pyannote für Sprecher-Zuweisung wenn DIARIZATION_ENABLED=true,
    sonst Pausen-Heuristik.
    """
    model = _get_model(settings.WHISPER_DEVICE, settings.WHISPER_COMPUTE_TYPE)

    # Diarization parallel zur Transkription (gleicher VRAM, kein Konflikt)
    diarization = _diarize(file_path)

    segments, info, beam_used = _transcribe_audio_segment(
        model, str(file_path), timeout=int(duration * 1.5) + 30
    )
    if beam_used < 2:
        logger.info("Einzeldatei: beam_size=1 verwendet (Fallback)")

    if diarization:
        lines = []
        for seg in segments:
            text = seg.text.strip()
            if not text:
                continue
            speaker = _assign_speaker_from_diarization(
                seg.start, seg.end, diarization
            ) or "A"
            lines.append(f"[{speaker}]: {text}")
        text = "\n".join(lines)
    else:
        text = _assign_speakers(segments)

    text = _preprocess_transcript(text)
    # v19.16 (T4): Ende des letzten transkribierten Segments - Basis fuer den
    # Coverage-Check (Transkript deckt das Audio bis hierher ab).
    _last_end = max((float(getattr(s_, "end", 0.0) or 0.0) for s_ in segments), default=0.0)
    return {
        "transcript": text.strip(),
        "language": info.language,
        "duration_seconds": duration,
        "word_count": len(text.split()),
        "transcribed_until_s": _last_end,
    }


def _transcribe_chunked(file_path: Path, duration: float) -> dict:
    """
    Lange Audiodatei in Chunks aufteilen und sequenziell transkribieren.
    Chunks werden an Stille-Grenzen geschnitten.
    Sprecher-Zuordnung laeuft ueber alle Chunks konsistent durch.
    Vorverarbeitung laeuft nach Zusammenfuehren aller Chunks.

    Parallelisierung:
    - Diarization (pyannote) startet sofort parallel zum Audio-Splitting.
    - Whisper-Chunks bleiben sequenziell (model.transcribe ist nicht thread-safe).
    - Diarization-Ergebnis wird nach der letzten Chunk-Transkription eingewartet.
    - Bei 71-Min-Aufnahme: Diarization (~40s) läuft während Whisper (~8*45s=360s)
      läuft → kein Zeitverlust durch Diarization.

    Stabilitaetshinweise:
    - beam_size=1 (Greedy): verhindert CUDA-Deadlocks bei VAD+Beam-Search
    - Jeder Chunk hat try/except: ein hängender Chunk bricht nicht alles ab
    - segments-Generator wird mit explizitem Timeout konsumiert
    """
    logger.info(
        "Lange Aufnahme (%.1f Min) – splitte in %d-Min-Chunks",
        duration / 60, CHUNK_MAX_SECONDS // 60,
    )

    with tempfile.TemporaryDirectory(prefix="systelios_chunks_") as tmp_dir:
        tmp_path = Path(tmp_dir)

        splits = _find_silence_splits(file_path, CHUNK_MAX_SECONDS, duration=duration)
        chunks = _split_audio(file_path, splits, tmp_path, duration=duration)
        logger.info("Audio aufgeteilt in %d Chunks", len(chunks))

        # Diarization parallel zur Transkription starten
        # pyannote läuft im eigenen Thread während Whisper die Chunks abarbeitet
        import concurrent.futures as _cf
        diarization_future = None
        diarization_executor = None
        if settings.DIARIZATION_ENABLED and _get_diarization_pipeline() is not None:
            diarization_executor = _cf.ThreadPoolExecutor(max_workers=1)
            diarization_future = diarization_executor.submit(_diarize, file_path)
            logger.info("Diarization parallel gestartet")

        model = _get_model(settings.WHISPER_DEVICE, settings.WHISPER_COMPUTE_TYPE)
        # Segmente mit Zeitstempeln sammeln – Sprecher erst nach Diarization zuweisen
        all_segments: list[dict] = []   # {abs_start, abs_end, text}
        # Skip-Marker fuer abgebrochene/uebersprungene Chunks. Wird unten zwischen
        # Sprecherzuweisung und Final-Text als sichtbare Luecke eingefuegt damit der
        # Therapeut beim Reviewen sofort sieht wo Audio fehlt (statt unbemerkt
        # halluziniertem Text durch luckenbehaftete LLM-Inputs)
        skipped_chunks: list[dict] = []  # {abs_start, abs_end, idx, total, reason}
        language = "de"

        # Boundaries fuer Skip-Marker (Audio-Zeitfenster pro Chunk)
        boundaries_for_log = [0.0] + splits + [duration]

        for i, chunk_path in enumerate(chunks):
            chunk_start_sec = boundaries_for_log[i]
            chunk_end_sec   = boundaries_for_log[i + 1]
            chunk_dur_sec   = chunk_end_sec - chunk_start_sec
            logger.info(
                "Transkribiere Chunk %d/%d (%.1f-%.1fs, %.1f Min) ...",
                i + 1, len(chunks),
                chunk_start_sec, chunk_end_sec,
                chunk_dur_sec / 60,
            )
            try:
                segments, info, beam_used = _transcribe_audio_segment(
                    model, str(chunk_path)
                )
                if beam_used < 2:
                    logger.info("Chunk %d/%d: beam_size=1 Fallback", i + 1, len(chunks))
                language = info.language
            except TranscriptionStalledError as e:
                logger.error(
                    "Chunk %d/%d: STALL bei %.0f-%.0fs - %s - ueberspringe",
                    i + 1, len(chunks), chunk_start_sec, chunk_end_sec, e
                )
                skipped_chunks.append({
                    "abs_start": chunk_start_sec,
                    "abs_end":   chunk_end_sec,
                    "idx":       i + 1,
                    "total":     len(chunks),
                    "reason":    "Whisper-Stall (kein Fortschritt)",
                })
                continue
            except TranscriptionTooSlowError as e:
                logger.error(
                    "Chunk %d/%d: TOO_SLOW bei %.0f-%.0fs - %s - ueberspringe",
                    i + 1, len(chunks), chunk_start_sec, chunk_end_sec, e
                )
                skipped_chunks.append({
                    "abs_start": chunk_start_sec,
                    "abs_end":   chunk_end_sec,
                    "idx":       i + 1,
                    "total":     len(chunks),
                    "reason":    "Whisper zu langsam (Wallclock > 2x Audio-Dauer)",
                })
                continue
            except Exception as e:
                logger.error(
                    "Chunk %d/%d: Fehler (%s) bei %.0f-%.0fs - ueberspringe",
                    i + 1, len(chunks), e, chunk_start_sec, chunk_end_sec
                )
                skipped_chunks.append({
                    "abs_start": chunk_start_sec,
                    "abs_end":   chunk_end_sec,
                    "idx":       i + 1,
                    "total":     len(chunks),
                    "reason":    f"Fehler: {type(e).__name__}",
                })
                continue

            chunk_offset = splits[i - 1] if i > 0 else 0.0
            for seg in segments:
                text = seg.text.strip()
                if text:
                    all_segments.append({
                        "start": chunk_offset + seg.start,
                        "end":   chunk_offset + seg.end,
                        "text":  text,
                    })

        # Diarization-Ergebnis einwarten (sollte längst fertig sein)
        diarization = None
        if diarization_future is not None:
            try:
                diarization = diarization_future.result(timeout=30)
                if diarization:
                    logger.info("Diarization fertig – %d Segmente", len(diarization))
                else:
                    logger.warning("Diarization fehlgeschlagen – Pausen-Heuristik")
            except Exception as e:
                logger.warning("Diarization Timeout/Fehler: %s – Pausen-Heuristik", e)
            finally:
                if diarization_executor:
                    diarization_executor.shutdown(wait=False)

        # Sprecher-Zuweisung auf alle gesammelten Segmente
        # all_lines wird mit (start_time, line)-Tupeln aufgebaut damit
        # Skip-Marker chronologisch korrekt eingefuegt werden koennen.
        timed_lines: list[tuple[float, str]] = []
        current_speaker = "A"
        last_end_global = None
        for seg in all_segments:
            if diarization:
                speaker = _assign_speaker_from_diarization(
                    seg["start"], seg["end"], diarization
                ) or current_speaker
            else:
                if last_end_global is not None and (seg["start"] - last_end_global) >= 1.2:
                    current_speaker = "B" if current_speaker == "A" else "A"
                speaker = current_speaker
            timed_lines.append((seg["start"], f"[{speaker}]: {seg['text']}"))
            last_end_global = seg["end"]

        # Skip-Marker als sichtbare Luecken einfuegen. Format ist auffaellig
        # gewaehlt damit Therapeut beim Reviewen sofort sieht wo Audio fehlt
        # und entsprechend prueft. Auch das LLM in nachfolgenden Schritten
        # erkennt das Format und halluziniert dort nicht.
        for skip in skipped_chunks:
            mm_start = int(skip["abs_start"] // 60)
            ss_start = int(skip["abs_start"] % 60)
            mm_end   = int(skip["abs_end"]   // 60)
            ss_end   = int(skip["abs_end"]   % 60)
            marker = (
                f"\n[--- AUDIO LUECKE: Chunk {skip['idx']}/{skip['total']} "
                f"({mm_start:02d}:{ss_start:02d}-{mm_end:02d}:{ss_end:02d}) "
                f"NICHT TRANSKRIBIERT - {skip['reason']}. "
                f"Bitte das Audio fuer diese Stelle pruefen ---]\n"
            )
            timed_lines.append((skip["abs_start"], marker))

        # Chronologisch sortieren - Skip-Marker landen automatisch zwischen
        # den umgebenden Segmenten an der richtigen Audio-Zeit
        timed_lines.sort(key=lambda t: t[0])
        all_lines = [line for _, line in timed_lines]

        # WICHTIG: all_segments pruefen, NICHT all_lines!
        # all_lines enthaelt bereits die "[--- AUDIO LUECKE ---]"-Marker fuer
        # gescheiterte Chunks. Wenn alle Chunks scheitern besteht all_lines
        # nur aus Skip-Markern (76 "Woerter" Muelltext). Nur all_segments
        # enthaelt echte transkribierte Audio-Inhalte.
        if not all_segments:
            total_skipped_sec = sum(s["abs_end"] - s["abs_start"] for s in skipped_chunks)
            raise RuntimeError(
                f"Transkription fehlgeschlagen: alle {len(chunks)} Chunks "
                f"({total_skipped_sec/60:.1f} Min Audio) gescheitert. "
                "Pruefe CUDA-Speicher (nvidia-smi) und Server-Log."
            )

        if skipped_chunks:
            total_skipped_sec = sum(s["abs_end"] - s["abs_start"] for s in skipped_chunks)
            logger.warning(
                "Transkription mit Luecken: %d von %d Chunks uebersprungen "
                "(insgesamt %.1f Min Audio nicht transkribiert)",
                len(skipped_chunks), len(chunks), total_skipped_sec / 60,
            )

        full_text = "\n".join(all_lines)
        full_text = _preprocess_transcript(full_text)
        # v19.16 (T4): absolute Endzeit des letzten Segments (Chunk-Offsets
        # sind in all_segments bereits eingerechnet).
        _last_end = max((seg["end"] for seg in all_segments), default=0.0)
        return {
            "transcript": full_text.strip(),
            "language": language,
            "duration_seconds": duration,
            "word_count": len(full_text.split()),
            "transcribed_until_s": _last_end,
        }
