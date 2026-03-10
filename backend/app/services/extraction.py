"""
Dokumenten-Extraktions-Service.

- PDF  → pdfplumber (strukturiert) + Tesseract OCR (Fallback)
- DOCX → python-docx
- TXT  → direkt
- Bild → Tesseract OCR
"""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


async def extract_text(file_path: Path) -> str:
    """Extrahiert Text aus einer Datei anhand der Dateiendung."""
    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        return await _extract_pdf(file_path)
    elif suffix in (".docx", ".doc"):
        return await _extract_docx(file_path)
    elif suffix == ".txt":
        return file_path.read_text(encoding="utf-8", errors="replace")
    elif suffix in (".jpg", ".jpeg", ".png", ".tiff", ".bmp"):
        return await _extract_image_ocr(file_path)
    else:
        raise ValueError(f"Nicht unterstuetztes Dateiformat: {suffix}")


async def _extract_pdf(file_path: Path) -> str:
    import asyncio

    def _run():
        try:
            import pdfplumber
            text_parts = []
            with pdfplumber.open(str(file_path)) as pdf:
                for page in pdf.pages:
                    t = page.extract_text()
                    if t:
                        text_parts.append(t.strip())
            text = "\n\n".join(text_parts)
            if len(text.strip()) > 50:
                return text
        except Exception as e:
            logger.warning("pdfplumber fehlgeschlagen: %s", e)

        # OCR-Fallback
        return _ocr_pdf(file_path)

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _run)


def _ocr_pdf(file_path: Path) -> str:
    """Konvertiert PDF-Seiten zu Bildern und fuehrt OCR durch."""
    try:
        import pdf2image
        import pytesseract

        images = pdf2image.convert_from_path(str(file_path), dpi=200)
        parts = []
        for img in images:
            text = pytesseract.image_to_string(img, lang="deu")
            if text.strip():
                parts.append(text.strip())
        return "\n\n".join(parts)
    except ImportError:
        logger.error("pdf2image oder pytesseract nicht installiert (OCR-Fallback nicht verfuegbar)")
        return "[PDF konnte nicht gelesen werden – OCR nicht verfuegbar]"
    except Exception as e:
        logger.error("OCR fehlgeschlagen: %s", e)
        return f"[OCR-Fehler: {e}]"


async def _extract_docx(file_path: Path) -> str:
    import asyncio

    def _run():
        try:
            from docx import Document
            doc = Document(str(file_path))
            parts = []
            for para in doc.paragraphs:
                if para.text.strip():
                    parts.append(para.text.strip())
            # Tabellen
            for table in doc.tables:
                for row in table.rows:
                    cells = [c.text.strip() for c in row.cells if c.text.strip()]
                    if cells:
                        parts.append(" | ".join(cells))
            return "\n".join(parts)
        except Exception as e:
            raise RuntimeError(f"DOCX konnte nicht gelesen werden: {e}") from e

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _run)


async def _extract_image_ocr(file_path: Path) -> str:
    import asyncio

    def _run():
        try:
            import pytesseract
            from PIL import Image
            img = Image.open(str(file_path))
            return pytesseract.image_to_string(img, lang="deu").strip()
        except ImportError:
            raise RuntimeError("pytesseract / Pillow nicht installiert")
        except Exception as e:
            raise RuntimeError(f"Bild-OCR fehlgeschlagen: {e}") from e

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _run)


async def extract_style_context(file_path: Path, llm_generate_fn) -> str:
    """
    Extrahiert Stilmerkmale aus einem Beispieltext des Therapeuten.
    Gibt ein kompaktes Stil-Prompt-Fragment zurueck.
    """
    raw_text = await extract_text(file_path)
    if len(raw_text) < 100:
        return ""

    # Auf 3000 Zeichen kuerzen (reicht fuer Stilanalyse)
    sample = raw_text[:3000]

    style_prompt = (
        "Analysiere den Schreibstil des folgenden klinischen Textes eines Therapeuten. "
        "Beschreibe in 3-5 praegnanten Saetzen: Satzlaenge, Fachbegriff-Dichte, "
        "Formulierungsgewohnheiten, Tonalitaet und besondere sprachliche Merkmale. "
        "Schreibe die Beschreibung als direkte Anweisung fuer einen anderen Schreiber "
        "(beginne mit 'Schreibe in einem Stil, der ...')."
    )

    result = await llm_generate_fn(
        system_prompt=style_prompt,
        user_content=f"TEXT ZUM ANALYSIEREN:\n{sample}",
        max_tokens=300,
    )
    return result.get("text", "")
