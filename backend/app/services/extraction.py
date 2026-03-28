"""
Dokumenten-Extraktions-Service – sysTelios KI-Dokumentation
============================================================

Dreistufige Fallback-Kette fuer PDF-Extraktion:

  Stufe 1 - pdfplumber      Maschinenlesbares PDF (digital ausgefuellt)
  Stufe 2 - Tesseract OCR   Guter Scan, klare Handschrift, gedruckter Text
  Stufe 3 - Ollama Vision   Schlechter Scan, unleserliche Handschrift,
                            komplexe Formularlayouts

Qualitaetspruefung nach jeder Stufe:
  - Mindestlaenge (Zeichenanzahl)
  - Lesbarkeitsquotient (Anteil alphanumerischer Zeichen)
  - Sprachplausibilitaet (deutsche Stoppwoerter erkennbar)
  - Tesseract Confidence-Score (falls verfuegbar)
  - Wiederholungsrate (OCR-Artefakt-Erkennung)

Weitere Formate:
  DOCX  - python-docx (inkl. Tabellen, Kopf-/Fusszeilen)
  TXT   - direkte UTF-8/Latin-1-Dekodierung
  Bild  - Tesseract dann Ollama Vision Fallback

Datenschutz:
  Alle Verarbeitungsschritte laufen lokal. Kein externer API-Aufruf.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import re
import unicodedata
from pathlib import Path
from typing import NamedTuple

import httpx
from PIL import Image, ImageEnhance, ImageFilter

from app.core.config import settings

logger = logging.getLogger(__name__)

MIN_CHARS = 80
MIN_READABILITY = 0.72
MIN_CONFIDENCE = 45.0

DE_STOPWORDS = frozenset([
    "und", "der", "die", "das", "ist", "ein", "eine", "zu", "in", "von",
    "mit", "auf", "nicht", "sich", "dem", "des", "den", "bei", "durch",
    "nach", "wie", "auch", "an", "oder", "hat", "wird", "sind", "kann",
    "als", "im", "am", "um", "so", "aber", "noch", "aus", "er", "sie",
    "wir", "ich", "mein", "sein", "haben", "werden", "dass", "patient",
    "klient", "therapie", "behandlung", "datum", "bericht", "name",
    "alter", "diagnose", "beschwerden", "anamnese", "befund",
])

TESS_CONFIG = "--oem 3 --psm 6 -l deu"


class ExtractionResult(NamedTuple):
    text: str
    method: str
    quality: float
    pages: int
    warnings: list


class TextQuality(NamedTuple):
    ok: bool
    score: float
    reason: str


# ── Public API ────────────────────────────────────────────────────────────────

async def extract_text(file_path: Path) -> str:
    result = await extract_text_with_meta(file_path)
    for w in result.warnings:
        logger.warning("[OCR] %s - %s", file_path.name, w)
    logger.info(
        "[OCR] %s -> Methode: %s | Qualitaet: %.0f%% | Seiten: %d",
        file_path.name, result.method, result.quality * 100, result.pages,
    )
    return result.text


async def extract_text_with_meta(file_path: Path) -> ExtractionResult:
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return await _extract_pdf(file_path)
    elif suffix in (".docx", ".doc"):
        return await _extract_docx(file_path)
    elif suffix == ".txt":
        return await _extract_txt(file_path)
    elif suffix in (".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".webp"):
        return await _extract_image(file_path)
    else:
        raise ValueError(
            f"Nicht unterstuetztes Dateiformat: '{suffix}'. "
            "Unterstuetzt: PDF, DOCX, TXT, JPG, PNG, TIFF, BMP, WEBP"
        )


async def extract_style_context(file_path: Path, llm_generate_fn) -> str:
    raw_text = await extract_text(file_path)
    if len(raw_text.strip()) < 100:
        return ""
    sample = raw_text[:3000]
    style_prompt = (
        "Analysiere den Schreibstil des folgenden klinischen Textes eines Therapeuten. "
        "Beschreibe in 3-5 praegnanten Saetzen: Satzlaenge, Fachbegriff-Dichte, "
        "Formulierungsgewohnheiten, Tonalitaet und besondere sprachliche Merkmale. "
        "Schreibe die Beschreibung als direkte Anweisung fuer einen anderen Schreiber "
        "(beginne mit Schreibe in einem Stil der ...)."
    )
    result = await llm_generate_fn(
        system_prompt=style_prompt,
        user_content=f"TEXT ZUM ANALYSIEREN:\n{sample}",
        max_tokens=300,
    )
    return result.get("text", "")


# ── Qualitaetspruefung ────────────────────────────────────────────────────────

def _assess_quality(text: str, min_chars: int = MIN_CHARS) -> TextQuality:
    stripped = text.strip()
    if len(stripped) < min_chars:
        return TextQuality(False, 0.0, f"Zu kurz ({len(stripped)} < {min_chars} Zeichen)")
    readable = sum(
        1 for c in stripped
        if c.isalnum() or c.isspace() or c in ".,;:!?-()"
    )
    readability = readable / len(stripped)
    if readability < MIN_READABILITY:
        return TextQuality(False, readability,
            f"Zu viele Sonderzeichen (Lesbarkeit {readability:.0%})")
    if _has_excessive_repetition(stripped):
        return TextQuality(False, 0.2, "Excessive Zeichenwiederholungen (OCR-Artefakt)")
    words = re.findall(r"\b\w+\b", stripped.lower())
    if words:
        hits = sum(1 for w in words if w in DE_STOPWORDS)
        lang_score = min(1.0, hits / max(1, len(words) * 0.05))
    else:
        lang_score = 0.0
    score = (readability * 0.5) + (lang_score * 0.3) + (min(1.0, len(stripped) / 500) * 0.2)
    if lang_score < 0.1 and len(words) > 20:
        return TextQuality(False, score,
            f"Text erscheint nicht deutsch (lang_score: {lang_score:.2f})")
    return TextQuality(True, min(1.0, score), "OK")


def _has_excessive_repetition(text: str) -> bool:
    return bool(re.search(r"([^\s\-_])\1{6,}", text))


def _normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"[^\S\n\t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[\x0c\x0b]", "\n\n", text)
    return text.strip()


# ── Bild-Vorverarbeitung ──────────────────────────────────────────────────────

def _preprocess_image_for_ocr(img: Image.Image) -> Image.Image:
    img = img.convert("L")
    w, h = img.size
    if w < 1200:
        scale = max(1.5, 1800 / w)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    img = img.filter(ImageFilter.SHARPEN)
    img = img.filter(ImageFilter.UnsharpMask(radius=1, percent=150, threshold=3))
    return ImageEnhance.Contrast(img).enhance(1.8)


def _image_to_base64(img: Image.Image, max_size: int = 1600) -> str:
    w, h = img.size
    if max(w, h) > max_size:
        ratio = max_size / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode()


# ── Stufe 1: pdfplumber ───────────────────────────────────────────────────────

def _pdfplumber_extract(file_path: Path):
    import pdfplumber
    parts = []
    with pdfplumber.open(str(file_path)) as pdf:
        pages = len(pdf.pages)
        for page in pdf.pages:
            t = page.extract_text(x_tolerance=3, y_tolerance=3)
            if t and t.strip():
                parts.append(t.strip())
            for table in page.extract_tables():
                rows = [" | ".join(str(c).strip() for c in row if c and str(c).strip())
                        for row in table]
                rows = [r for r in rows if r]
                if rows:
                    parts.append("\n".join(rows))
    return _normalize_text("\n\n".join(parts)), pages


# ── Stufe 2: Tesseract ────────────────────────────────────────────────────────

def _tesseract_extract_pdf(file_path: Path):
    import pdf2image, pytesseract
    images = pdf2image.convert_from_path(str(file_path), dpi=300, fmt="PNG")
    parts, confidences = [], []
    for img in images:
        processed = _preprocess_image_for_ocr(img)
        try:
            data = pytesseract.image_to_data(
                processed, lang="deu", config=TESS_CONFIG,
                output_type=pytesseract.Output.DICT)
            valid = [c for c in data["conf"] if isinstance(c, (int, float)) and c > 0]
            if valid:
                confidences.append(sum(valid) / len(valid))
        except Exception:
            pass
        text = pytesseract.image_to_string(processed, lang="deu", config=TESS_CONFIG)
        if text.strip():
            parts.append(text.strip())
    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
    return _normalize_text("\n\n".join(parts)), len(images), avg_conf


def _tesseract_extract_image(img: Image.Image):
    import pytesseract
    processed = _preprocess_image_for_ocr(img)
    try:
        data = pytesseract.image_to_data(
            processed, lang="deu", config=TESS_CONFIG,
            output_type=pytesseract.Output.DICT)
        valid = [c for c in data["conf"] if isinstance(c, (int, float)) and c > 0]
        confidence = sum(valid) / len(valid) if valid else 0.0
    except Exception:
        confidence = 0.0
    text = pytesseract.image_to_string(processed, lang="deu", config=TESS_CONFIG)
    return _normalize_text(text), confidence


# ── Stufe 3: Ollama Vision ────────────────────────────────────────────────────

async def _check_vision_model_available() -> bool:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.OLLAMA_HOST}/api/tags")
            if r.status_code == 200:
                models = [m["name"] for m in r.json().get("models", [])]
                return any(settings.VISION_MODEL in m for m in models)
    except Exception:
        pass
    return False


async def _ollama_vision_page(b64_image: str, page_num: int, total_pages: int) -> str:
    prompt = (
        f"Dies ist Seite {page_num} von {total_pages} eines medizinischen Dokuments "
        "der sysTelios Klinik. "
        "Extrahiere den GESAMTEN Text vollstaendig und exakt. "
        "Behalte die Struktur bei. "
        "Angekreuzte Checkboxen mit [X], nicht angekreuzte mit [ ]. "
        "Nur der extrahierte Text, keine Kommentare."
    )
    payload = {
        "model": settings.VISION_MODEL,
        "prompt": prompt,
        "images": [b64_image],
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 2048},
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(f"{settings.OLLAMA_HOST}/api/generate", json=payload)
        r.raise_for_status()
    return r.json().get("response", "").strip()


async def _ollama_vision_extract_pdf(file_path: Path):
    import pdf2image
    loop = asyncio.get_event_loop()
    images = await loop.run_in_executor(
        None, lambda: pdf2image.convert_from_path(str(file_path), dpi=200, fmt="PNG"))
    total = len(images)
    parts = []
    for i, img in enumerate(images, 1):
        b64 = _image_to_base64(img)
        try:
            page_text = await _ollama_vision_page(b64, i, total)
            if page_text:
                parts.append(f"[Seite {i}]\n{page_text}")
        except Exception as e:
            logger.warning("[Vision] Seite %d fehlgeschlagen: %s", i, e)
            parts.append(f"[Seite {i} - Fehler]")
    return _normalize_text("\n\n".join(parts)), total


async def _ollama_vision_extract_image(img: Image.Image) -> str:
    return await _ollama_vision_page(_image_to_base64(img), 1, 1)


# ── PDF Fallback-Kette ────────────────────────────────────────────────────────

async def _extract_pdf(file_path: Path) -> ExtractionResult:
    warnings = []
    loop = asyncio.get_event_loop()

    # Stufe 1: pdfplumber
    logger.debug("[OCR] PDF Stufe 1: pdfplumber - %s", file_path.name)
    try:
        text, pages = await loop.run_in_executor(None, _pdfplumber_extract, file_path)
        q = _assess_quality(text)
        if q.ok:
            return ExtractionResult(text, "pdfplumber", q.score, pages, warnings)
        warnings.append(f"Stufe 1 unzureichend: {q.reason}")
    except ImportError:
        warnings.append("pdfplumber nicht installiert")
    except Exception as e:
        warnings.append(f"Stufe 1 Fehler: {e}")
        logger.warning("[OCR] Stufe 1 fehlgeschlagen: %s", e)

    # Stufe 2: Tesseract
    logger.debug("[OCR] PDF Stufe 2: Tesseract - %s", file_path.name)
    try:
        text, pages, conf = await loop.run_in_executor(None, _tesseract_extract_pdf, file_path)
        q = _assess_quality(text)
        conf_ok = conf == 0 or conf >= MIN_CONFIDENCE
        if conf > 0 and not conf_ok:
            warnings.append(f"Tesseract-Konfidenz niedrig: {conf:.1f}")
        if q.ok and conf_ok:
            return ExtractionResult(text, "tesseract", q.score, pages, warnings)
        reason = q.reason if not q.ok else f"Konfidenz zu niedrig ({conf:.1f})"
        warnings.append(f"Stufe 2 unzureichend: {reason}")
    except ImportError:
        warnings.append("pytesseract/pdf2image nicht installiert")
    except Exception as e:
        warnings.append(f"Stufe 2 Fehler: {e}")
        logger.warning("[OCR] Stufe 2 fehlgeschlagen: %s", e)

    # Stufe 3: Ollama Vision
    logger.debug("[OCR] PDF Stufe 3: Ollama Vision - %s", file_path.name)
    if not await _check_vision_model_available():
        warnings.append(f"Ollama Vision nicht verfuegbar (ollama pull {settings.VISION_MODEL})")
        raise RuntimeError(
            f"PDF '{file_path.name}': Alle Extraktionsstufen fehlgeschlagen. "
            f"Details: {chr(59).join(warnings)}"
        )
    try:
        text, pages = await _ollama_vision_extract_pdf(file_path)
        q = _assess_quality(text, min_chars=50)
        if q.ok:
            return ExtractionResult(text, "ollama_vision", q.score, pages, warnings)
        warnings.append(f"Stufe 3 unzureichend: {q.reason}")
    except Exception as e:
        warnings.append(f"Stufe 3 Fehler: {e}")
        logger.error("[OCR] Stufe 3 fehlgeschlagen: %s", e)

    raise RuntimeError(
        f"PDF '{file_path.name}': Alle Extraktionsstufen fehlgeschlagen. "
        f"Details: {chr(59).join(warnings)}"
    )


# ── DOCX ─────────────────────────────────────────────────────────────────────

async def _extract_docx(file_path: Path) -> ExtractionResult:
    loop = asyncio.get_event_loop()

    def _run():
        from docx import Document
        doc = Document(str(file_path))
        parts, warns = [], []
        for section in doc.sections:
            for para in section.header.paragraphs:
                if para.text.strip():
                    parts.append(para.text.strip())
        for para in doc.paragraphs:
            t = para.text.strip()
            if t:
                parts.append(f"## {t}" if para.style.name.startswith("Heading") else t)
        for table in doc.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
        for section in doc.sections:
            for para in section.footer.paragraphs:
                if para.text.strip():
                    parts.append(para.text.strip())
        imgs = sum(1 for rel in doc.part.rels.values() if "image" in rel.reltype)
        if imgs:
            warns.append(f"DOCX enthaelt {imgs} eingebettete Bilder (nicht extrahiert)")
        return _normalize_text("\n".join(parts)), warns

    try:
        text, warns = await loop.run_in_executor(None, _run)
        q = _assess_quality(text)
        return ExtractionResult(text, "docx", q.score, 1, warns)
    except Exception as e:
        raise RuntimeError(f"DOCX-Extraktion fehlgeschlagen: {e}") from e


# ── TXT ──────────────────────────────────────────────────────────────────────

async def _extract_txt(file_path: Path) -> ExtractionResult:
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        text = file_path.read_text(encoding="latin-1", errors="replace")
    text = _normalize_text(text)
    q = _assess_quality(text)
    return ExtractionResult(text, "txt", q.score, 1, [])


# ── Bild ─────────────────────────────────────────────────────────────────────

async def _extract_image(file_path: Path) -> ExtractionResult:
    warnings = []
    loop = asyncio.get_event_loop()
    try:
        img = await loop.run_in_executor(None, Image.open, str(file_path))
    except Exception as e:
        raise RuntimeError(f"Bild konnte nicht geoeffnet werden: {e}") from e

    # Stufe 1: Tesseract
    try:
        text, conf = await loop.run_in_executor(None, _tesseract_extract_image, img)
        q = _assess_quality(text)
        if q.ok and (conf == 0 or conf >= MIN_CONFIDENCE):
            return ExtractionResult(text, "image_tess", q.score, 1, warnings)
        reason = q.reason if not q.ok else f"Konfidenz {conf:.1f}"
        warnings.append(f"Tesseract unzureichend: {reason}")
    except Exception as e:
        warnings.append(f"Tesseract Fehler: {e}")

    # Stufe 2: Ollama Vision
    if not await _check_vision_model_available():
        raise RuntimeError(
            f"Bild-OCR fehlgeschlagen und Ollama Vision nicht verfuegbar. "
            f"Bitte: ollama pull {settings.VISION_MODEL}"
        )
    try:
        text = await _ollama_vision_extract_image(img)
        q = _assess_quality(text, min_chars=30)
        if q.ok:
            return ExtractionResult(text, "image_vision", q.score, 1, warnings)
        warnings.append(f"Ollama Vision unzureichend: {q.reason}")
    except Exception as e:
        warnings.append(f"Ollama Vision Fehler: {e}")

    raise RuntimeError(
        f"Bild '{file_path.name}' konnte nicht extrahiert werden. "
        f"Details: {chr(59).join(warnings)}"
    )
