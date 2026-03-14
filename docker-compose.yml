"""
DOCX-Befuellungs-Service fuer Workflow 3 (Verlaengerungsantrag) und 4 (Entlassbericht).

Strategie:
1. Vorlage einlesen (python-docx)
2. Platzhalter erkennen ({{FELD}} oder [FELD])
3. LLM generiert Inhalte fuer jeden Platzhalter
4. Ausgabe-DOCX speichern und zum Download bereitstellen
"""
import logging
import re
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

# Erkannte Platzhalter-Muster
_PLACEHOLDER_PATTERNS = [
    r"\{\{([^}]+)\}\}",   # {{FELDNAME}}
    r"\[([A-Z][A-Z _]+)\]",  # [FELDNAME] (nur Grossbuchstaben)
]


def find_placeholders(text: str) -> list[str]:
    """Findet alle Platzhalter in einem Text."""
    found = []
    for pattern in _PLACEHOLDER_PATTERNS:
        found.extend(re.findall(pattern, text))
    return list(dict.fromkeys(found))  # Duplikate entfernen, Reihenfolge beibehalten


async def fill_docx_template(
    template_path: Path,
    verlauf_text: str,
    generated_text: str,
    output_dir: Path,
    workflow: str,
) -> Path:
    """
    Befuellt eine DOCX-Vorlage mit dem generierten Inhalt.

    Falls die Vorlage Platzhalter enthaelt, werden diese ersetzt.
    Andernfalls wird der generierte Text als neuer Abschnitt angehaengt.
    """
    import asyncio

    def _run():
        try:
            from docx import Document
            from docx.shared import Pt
        except ImportError:
            raise RuntimeError("python-docx nicht installiert: pip install python-docx")

        doc = Document(str(template_path))
        full_template_text = "\n".join(p.text for p in doc.paragraphs)
        placeholders = find_placeholders(full_template_text)

        if placeholders:
            logger.info("Platzhalter gefunden: %s", placeholders)
            _fill_by_placeholder(doc, generated_text, placeholders)
        else:
            logger.info("Keine Platzhalter – Inhalt wird als neuer Abschnitt eingefuegt")
            _append_generated(doc, generated_text)

        # Ausgabedatei speichern
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{workflow}_{uuid.uuid4().hex[:8]}.docx"
        out_path = output_dir / filename
        doc.save(str(out_path))
        logger.info("DOCX gespeichert: %s", out_path)
        return out_path

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _run)


def _fill_by_placeholder(doc, generated_text: str, placeholders: list[str]) -> None:
    """Ersetzt Platzhalter in allen Absaetzen und Tabellen."""
    # Generierter Text als Quelle: zeilenweise aufteilen fuer sinnvolle Zuordnung
    lines = [l.strip() for l in generated_text.split("\n") if l.strip()]

    def get_replacement(placeholder: str) -> str:
        """Einfaches Matching: sucht passende Zeile im generierten Text."""
        needle = placeholder.lower().replace("_", " ")
        for line in lines:
            if needle in line.lower():
                # Nimm den Teil nach dem Doppelpunkt, falls vorhanden
                if ":" in line:
                    return line.split(":", 1)[1].strip()
                return line
        return f"[{placeholder}]"  # Unausgefuellt belassen

    def replace_in_para(para) -> None:
        for run in para.runs:
            for pattern in _PLACEHOLDER_PATTERNS:
                run.text = re.sub(
                    pattern,
                    lambda m: get_replacement(m.group(1)),
                    run.text,
                )

    for para in doc.paragraphs:
        replace_in_para(para)

    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    replace_in_para(para)


def _append_generated(doc, generated_text: str) -> None:
    """Fuegt generierten Text als neuen Abschnitt am Ende ein."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor

    doc.add_page_break()
    header = doc.add_heading("Generierter Inhalt (KI-Entwurf)", level=1)

    for line in generated_text.split("\n"):
        line = line.strip()
        if not line:
            doc.add_paragraph("")
            continue
        if line.startswith("#"):
            level = min(line.count("#"), 3)
            doc.add_heading(line.lstrip("# "), level=level)
        else:
            p = doc.add_paragraph(line)
            p.style.font.size = Pt(11)
