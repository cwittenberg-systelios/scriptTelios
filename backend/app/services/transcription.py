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

# Maximale Chunk-Laenge in Sekunden (10 Minuten)
CHUNK_MAX_SECONDS = 600

# Modell-Cache – einmalig laden, dann wiederverwenden
_model_cache: dict = {}


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


async def transcribe_audio(file_path: Path) -> dict:
    """
    Transkribiert eine Audiodatei lokal via faster-whisper.
    Lange Aufnahmen werden automatisch in Chunks aufgeteilt.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError(
            "faster-whisper nicht installiert. "
            "Bitte 'pip install faster-whisper' ausfuehren."
        )

    result = await _transcribe_local(file_path)

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


def _transcribe_single(file_path: Path, duration: float) -> dict:
    """Einzelne Datei transkribieren."""
    model = _get_model(settings.WHISPER_DEVICE, settings.WHISPER_COMPUTE_TYPE)
    segments, info = model.transcribe(
        str(file_path),
        language="de",
        beam_size=2,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
    )
    text = " ".join(s.text.strip() for s in segments)
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

        model = _get_model(settings.WHISPER_DEVICE, settings.WHISPER_COMPUTE_TYPE)
        all_texts = []
        language = "de"

        for i, chunk_path in enumerate(chunks):
            logger.info("Transkribiere Chunk %d/%d ...", i + 1, len(chunks))
            segments, info = model.transcribe(
                str(chunk_path),
                language="de",
                beam_size=2,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
            )
            chunk_text = " ".join(s.text.strip() for s in segments)
            if chunk_text.strip():
                all_texts.append(chunk_text.strip())
            language = info.language

        full_text = " ".join(all_texts)
        return {
            "transcript": full_text.strip(),
            "language": language,
            "duration_seconds": duration,
            "word_count": len(full_text.split()),
        }
