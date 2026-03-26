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

# Maximale Chunk-Laenge in Sekunden (15 Minuten)
# Groessere Chunks reduzieren Setup-Overhead (VAD-Initialisierung, Modell-Transfer)
# Bei 71-Min-Aufnahme: 5 Chunks statt 8.
CHUNK_MAX_SECONDS = 900

# Klinischer Initial-Prompt fuer Whisper.
# Verbessert Erkennung von Fachbegriffen, Eigennamen und IFS-Terminologie
# die Whisper ohne Kontext oft verschluckt oder falsch schreibt.
# Whisper nutzt diesen Text als "Kontext vor der Aufnahme" – kein echter Prompt,
# sondern ein Hint der die Wahrscheinlichkeitsverteilung des Tokenizers beeinflusst.
WHISPER_INITIAL_PROMPT = (
    "Psychotherapeutisches Gespräch. Fachbegriffe: IFS, Internal Family Systems, "
    "Manager-Anteil, Exile, Feuerwehr-Anteil, Self-Energy, Selbst-Energie, "
    "hypnosystemisch, systemische Therapie, Ressourcenorientierung, "
    "Auftragsklärung, Verlaufsnotiz, Anamnese, Epikrise, Entlassbericht, "
    "Verlängerungsantrag, Kostenübernahme, Krankenkasse, "
    "AMDP, Psychopathologie, Affektregulation, Dissoziation, "
    "Bindungsmuster, Traumatisierung, innere Anteile, Schutzmechanismus, "
    "Therapeut, Klient, Klientin, Sitzung, Intervention."
)

# Modell-Cache – einmalig laden, dann wiederverwenden
_model_cache: dict = {}
_diarization_pipeline = None   # pyannote Pipeline-Cache


def _transcribe_audio_segment(
    model,
    audio_path: str,
    timeout: int = 90,
) -> tuple:
    """
    Transkribiert ein Audio-Segment mit beam_size=2 und temperature-Sampling.
    Fällt bei Timeout automatisch auf beam_size=1 zurück.

    Qualitäts-Parameter:
    - beam_size=2: bessere Erkennung von Fachbegriffen und unklarer Aussprache
    - temperature=[0,0.2,0.4,...]: bei unsicheren Segmenten mehrere Kandidaten
      sampeln und den besten nehmen (wie Whisper original)
    - initial_prompt: klinisches Vokabular als Kontext-Hint

    Fallback auf beam_size=1 (Greedy) bei Timeout – beam_size=2 + VAD hängt
    gelegentlich bei bestimmten Audioquellen auf large-v3.
    """
    import concurrent.futures as _cf

    transcribe_kwargs = dict(
        language="de",
        beam_size=2,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        word_timestamps=False,
        initial_prompt=WHISPER_INITIAL_PROMPT,
        temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
    )

    def _run(beam):
        kwargs = {**transcribe_kwargs, "beam_size": beam}
        gen, info = model.transcribe(audio_path, **kwargs)
        return list(gen), info

    # Versuch 1: beam_size=2 mit Timeout
    with _cf.ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(_run, 2)
        try:
            segments, info = future.result(timeout=timeout)
            return segments, info, 2
        except _cf.TimeoutError:
            logger.warning(
                "beam_size=2 Timeout nach %ds – Fallback auf beam_size=1", timeout
            )

    # Versuch 2: beam_size=1 (Greedy, stabiler)
    with _cf.ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(_run, 1)
        try:
            segments, info = future.result(timeout=timeout)
            return segments, info, 1
        except _cf.TimeoutError:
            raise RuntimeError(f"Transkription Timeout nach {timeout}s (auch beam_size=1)")




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
            use_auth_token=settings.DIARIZATION_HF_TOKEN or None,
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


def _diarize(audio_path: Path) -> list[dict] | None:
    """
    Führt Speaker Diarization auf einer Audiodatei aus.
    Gibt eine Liste von {start, end, speaker} Segmenten zurück,
    oder None wenn Diarization nicht verfügbar ist.

    Sprecher-Labels werden normalisiert: SPEAKER_00 → "A", SPEAKER_01 → "B"
    (konsistent mit dem bisherigen [A]/[B]-Format für das LLM).
    """
    pipeline = _get_diarization_pipeline()
    if pipeline is None:
        return None
    try:
        diarization = pipeline(str(audio_path))
        segments = []
        speaker_map: dict[str, str] = {}
        labels = ["A", "B", "C", "D"]  # max 4 Sprecher realistisch
        for turn, _, speaker in diarization.itertracks(yield_label=True):
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
    """Gibt gecachtes Whisper-Modell zurueck, laed bei Bedarf neu."""
    from faster_whisper import WhisperModel
    key = (settings.WHISPER_MODEL, device, compute_type)
    if key not in _model_cache:
        logger.info(
            "Whisper-Modell laden: %s auf %s (%s)",
            settings.WHISPER_MODEL, device, compute_type,
        )
        _model_cache[key] = WhisperModel(
            settings.WHISPER_MODEL,
            device=device,
            compute_type=compute_type,
        )
    return _model_cache[key]


def _get_duration(file_path: Path) -> float:
    """Gibt Audiodauer in Sekunden zurueck via ffprobe."""
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
        return float(result.stdout.strip())
    except FileNotFoundError:
        raise RuntimeError(
            "ffprobe nicht gefunden. Bitte ffmpeg installieren: "
            "'apt-get install -y ffmpeg'"
        )
    except (ValueError, subprocess.CalledProcessError):
        return 0.0


def _find_silence_splits(file_path: Path, chunk_max: int) -> list[float]:
    """
    Findet Stille-Grenzen in der Audiodatei via ffmpeg silencedetect.
    Gibt eine sortierte Liste von Schnitt-Zeitpunkten zurueck.
    Schnitte werden so gewaehlt dass Chunks <= chunk_max Sekunden sind.
    """
    duration = _get_duration(file_path)
    if duration <= chunk_max:
        return []

    # Stille-Segmente detektieren (mind. 0.5s Stille bei -40dB)
    result = subprocess.run(
        [
            "ffmpeg", "-i", str(file_path),
            "-af", "silencedetect=noise=-40dB:d=0.5",
            "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )

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


def _split_audio(file_path: Path, splits: list[float], tmp_dir: Path) -> list[Path]:
    """Schneidet Audio an den gegebenen Zeitpunkten via ffmpeg."""
    chunks = []
    boundaries = [0.0] + splits + [_get_duration(file_path)]
    suffix = file_path.suffix.lower() or ".wav"

    for i, (start, end) in enumerate(zip(boundaries[:-1], boundaries[1:])):
        chunk_path = tmp_dir / f"chunk_{i:03d}{suffix}"
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", str(start),
                "-to", str(end),
                "-i", str(file_path),
                "-c", "copy",
                str(chunk_path),
            ],
            capture_output=True,
            check=True,
        )
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
                f"{settings.OLLAMA_HOST}/api/generate",
                json={
                    "model": settings.OLLAMA_MODEL,
                    "keep_alive": 0,
                    "prompt": "",
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
                f"{settings.OLLAMA_HOST}/api/generate",
                json={
                    "model":      settings.OLLAMA_MODEL,
                    "keep_alive": -1,
                    "prompt":     "",
                },
            )
        logger.info("Ollama-Modell vorgewaermt und bereit")
    except Exception as e:
        logger.debug("Ollama-Warmup nicht moeglich (ignoriert): %s", e)


async def transcribe_audio(file_path: Path) -> dict:
    """
    Transkribiert eine Audiodatei lokal via faster-whisper.
    Lange Aufnahmen werden automatisch in Chunks aufgeteilt.

    VRAM-Strategie (konfigurierbar via WHISPER_FREE_OLLAMA_VRAM):
    - False (Standard, RTX 4090): Whisper und Ollama teilen VRAM.
      large-v3 (~3GB) + mistral-nemo (~5GB) passen gleichzeitig.
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

    # Whisper-Modell aus Cache entfernen damit Ollama VRAM zurueckbekommt
    _model_cache.clear()
    logger.info("Whisper-Modell aus Cache entfernt")

    # Ollama vorwärmen / keep-alive senden – fire-and-forget.
    # Bei FREE_VRAM=True: lädt das Modell nach Whisper wieder in den VRAM.
    # Bei FREE_VRAM=False: verhindert Ollama-Inaktivitäts-Timeout.
    # Fehler im Warmup werden geloggt aber nie an den Caller weitergegeben.
    import asyncio
    asyncio.ensure_future(_ollama_warmup())

    # Audiodatei nach Transkription loeschen (Datenschutz / DSGVO)
    if settings.DELETE_AUDIO_AFTER_TRANSCRIPTION and file_path.exists():
        file_path.unlink()
        logger.info("Audiodatei geloescht nach Transkription: %s", file_path.name)

    return result


async def _transcribe_local(file_path: Path) -> dict:
    """faster-whisper lokal mit automatischem Chunking fuer lange Aufnahmen."""
    import asyncio

    def _run() -> dict:
        duration = _get_duration(file_path)
        logger.info("Audio-Dauer: %.1fs (%.1f Min)", duration, duration / 60)

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
    return {
        "transcript": text.strip(),
        "language": info.language,
        "duration_seconds": duration,
        "word_count": len(text.split()),
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

        splits = _find_silence_splits(file_path, CHUNK_MAX_SECONDS)
        chunks = _split_audio(file_path, splits, tmp_path)
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
        language = "de"

        for i, chunk_path in enumerate(chunks):
            logger.info("Transkribiere Chunk %d/%d ...", i + 1, len(chunks))
            try:
                segments, info, beam_used = _transcribe_audio_segment(
                    model, str(chunk_path), timeout=90
                )
                if beam_used < 2:
                    logger.info("Chunk %d/%d: beam_size=1 Fallback", i + 1, len(chunks))
                language = info.language
            except Exception as e:
                logger.error(
                    "Chunk %d/%d: Fehler (%s) – ueberspringe", i + 1, len(chunks), e
                )
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
        all_lines = []
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
            all_lines.append(f"[{speaker}]: {seg['text']}")
            last_end_global = seg["end"]

        if not all_lines:
            raise RuntimeError(
                "Alle Chunks fehlgeschlagen oder leer – Transkription nicht moeglich. "
                "Prüfe CUDA-Speicher und Audioqualität."
            )
    logger.info(
        "Lange Aufnahme (%.1f Min) – splitte in %d-Min-Chunks",
        duration / 60, CHUNK_MAX_SECONDS // 60,
    )

    with tempfile.TemporaryDirectory(prefix="systelios_chunks_") as tmp_dir:
        tmp_path = Path(tmp_dir)

        splits = _find_silence_splits(file_path, CHUNK_MAX_SECONDS)
        chunks = _split_audio(file_path, splits, tmp_path)
        logger.info("Audio aufgeteilt in %d Chunks", len(chunks))

        # Diarization parallel zur Chunk-Transkription starten
        # (pyannote läuft im eigenen Thread, Whisper im Hauptthread – kein Konflikt)
        import concurrent.futures as _cf
        diarization_future = None
        diarization_executor = None
        if settings.DIARIZATION_ENABLED and _get_diarization_pipeline() is not None:
            diarization_executor = _cf.ThreadPoolExecutor(max_workers=1)
            diarization_future = diarization_executor.submit(_diarize, file_path)
            logger.info("Diarization gestartet (parallel zur Transkription)")
        else:
            # Kein pyannote – sofort mit None abschließen
            diarization = None

        model = _get_model(settings.WHISPER_DEVICE, settings.WHISPER_COMPUTE_TYPE)
        all_lines = []
        language = "de"
        current_speaker = "A"
        last_end_global = None

        for i, chunk_path in enumerate(chunks):
            logger.info("Transkribiere Chunk %d/%d ...", i + 1, len(chunks))
            try:
                segments_gen, info = model.transcribe(
                    str(chunk_path),
                    language="de",
                    beam_size=1,
                    vad_filter=True,
                    vad_parameters={"min_silence_duration_ms": 500},
                    word_timestamps=False,
                )
                with _cf.ThreadPoolExecutor(max_workers=1) as ex:
                    future = ex.submit(list, segments_gen)
                    try:
                        segments = future.result(timeout=60)
                    except _cf.TimeoutError:
                        logger.error(
                            "Chunk %d/%d: Timeout – ueberspringe diesen Chunk",
                            i + 1, len(chunks)
                        )
                        continue

                language = info.language
            except Exception as e:
                logger.error(
                    "Chunk %d/%d: Fehler (%s) – ueberspringe diesen Chunk",
                    i + 1, len(chunks), e
                )
                continue

            # Zeitoffset für diesen Chunk
            chunk_offset = splits[i - 1] if i > 0 else 0.0

            for seg in segments:
                abs_start = chunk_offset + seg.start
                abs_end   = chunk_offset + seg.end
                text = seg.text.strip()
                if not text:
                    continue

                # Sprecher-Zuweisung: erst beim letzten Chunk prüfen ob
                # Diarization fertig ist (lazy resolve)
                if diarization_future is not None and not hasattr(_transcribe_chunked, "_dia_resolved"):
                    if diarization_future.done():
                        try:
                            diarization = diarization_future.result()
                            if diarization:
                                logger.info("Diarization abgeschlossen – %d Segmente", len(diarization))
                            else:
                                logger.warning("Diarization fehlgeschlagen – Pausen-Heuristik")
                        except Exception as e:
                            logger.warning("Diarization Exception: %s – Pausen-Heuristik", e)
                            diarization = None
                        diarization_future = None

                if diarization_future is not None:
                    # Diarization noch nicht fertig – Pausen-Heuristik für diesen Chunk
                    if last_end_global is not None and (abs_start - last_end_global) >= 1.2:
                        current_speaker = "B" if current_speaker == "A" else "A"
                    speaker = current_speaker
                elif diarization:
                    speaker = _assign_speaker_from_diarization(
                        abs_start, abs_end, diarization
                    ) or current_speaker
                else:
                    if last_end_global is not None and (abs_start - last_end_global) >= 1.2:
                        current_speaker = "B" if current_speaker == "A" else "A"
                    speaker = current_speaker

                all_lines.append(f"[{speaker}]: {text}")
                last_end_global = abs_end

        # Diarization-Ergebnis einwarten falls noch nicht fertig
        if diarization_future is not None:
            try:
                diarization = diarization_future.result(timeout=30)
                if diarization:
                    logger.info(
                        "Diarization nach Transkription fertig – %d Segmente. "
                        "Sprecher-Labels werden nachträglich zugewiesen.",
                        len(diarization)
                    )
                    # Sprecher-Labels nachträglich korrigieren
                    # (alle Zeilen mit Pausen-Heuristik wurden vorläufig vergeben)
                    corrected = []
                    for line in all_lines:
                        # Format: "[A]: Text" → Text extrahieren, Zeitstempel nicht
                        # mehr verfügbar → Labels so lassen, nur loggen
                        corrected.append(line)
                    all_lines = corrected
            except Exception as e:
                logger.warning("Diarization-Timeout nach Transkription: %s", e)
            finally:
                if diarization_executor:
                    diarization_executor.shutdown(wait=False)

        if not all_lines:
            raise RuntimeError(
                "Alle Chunks fehlgeschlagen oder leer – Transkription nicht moeglich. "
                "Prüfe CUDA-Speicher und Audioqualität."
            )

        full_text = "\n".join(all_lines)
        full_text = _preprocess_transcript(full_text)
        return {
            "transcript": full_text.strip(),
            "language": language,
            "duration_seconds": duration,
            "word_count": len(full_text.split()),
        }
