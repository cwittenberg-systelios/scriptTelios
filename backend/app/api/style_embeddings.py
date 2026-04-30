"""
API-Endpunkte fuer Stilprofil-Verwaltung via pgvector.

POST /api/style/upload
    Beispieltext eines Therapeuten hochladen, vektorisieren und speichern.
    Pflichtfelder: therapeut_id, dokumenttyp
    Optional: ist_statisch (Anker-Beispiel)

GET  /api/style/{therapeut_id}
    Alle Beispiele eines Therapeuten auflisten (ohne Embeddings).

GET  /api/style/{therapeut_id}/{dokumenttyp}
    Beispiele gefiltert nach Dokumenttyp.

DELETE /api/style/embedding/{embedding_id}
    Einzelnes Beispiel loeschen.
"""
import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.files import save_upload, ALLOWED_DOCS
from app.models.db import StyleEmbedding, DOKUMENTTYPEN, DOKUMENTTYP_LABELS
from app.models.schemas import (
    StyleEmbeddingUploadResponse,
    StyleEmbeddingInfo,
    StyleEmbeddingListResponse,
)
from app.services.embeddings import get_embedding
from app.services.extraction import extract_text

router = APIRouter()
logger = logging.getLogger(__name__)


# Relevante Abschnitte für Verlängerung/Entlassbericht.
# Andere Abschnitte (Diagnosen, Medikation, standardisierte Felder)
# sind nicht therapeutenspezifisch und werden nicht als Stilvorlage genutzt.
RELEVANTE_ABSCHNITTE = [
    "Aktuelle Anamnese",
    "Verlauf und Begründung der weiteren Verlängerung",
    "Bisheriger Verlauf und Begründung der Verlängerung",
    "Bisheriger Verlauf",
    "Problemrelevante Vorgeschichte",
    "Biographische Anamnese",
    "Psychotherapeutischer Verlauf",
    "Psychotherapeutischer Behandlungsverlauf",
    "Zusammenfassung (Begründung der Notwendigkeit)",
    "Begründung der Notwendigkeit",
    "Begründung für Akutaufnahme",
    "Begründung für die Akutaufnahme",
    "Akutbegründung",
]

# Dokumenttypen bei denen Abschnitts-Filterung sinnvoll ist
DOKUMENTTYPEN_MIT_ABSCHNITTEN = {"verlaengerung", "folgeverlaengerung", "entlassbericht", "akutantrag", "anamnese"}


def _extrahiere_relevante_abschnitte(text: str) -> str:
    """
    Extrahiert nur die therapeutenspezifischen Abschnitte aus strukturierten
    Dokumenten (Verlängerungsantrag, Entlassbericht).

    Andere Abschnitte (Diagnosen, Medikation, Krankenkasse, standardisierte
    Felder) werden entfernt – sie sind nicht vom Therapeuten frei formuliert
    und würden das Stilprofil verfälschen.

    Gibt den gefilterten Text zurück, oder den Original-Text wenn keine
    bekannten Abschnitte gefunden wurden (Fallback).
    """
    import re
    lines = text.split("\n")
    in_relevant = False
    collected: list[str] = []
    found_any = False

    for line in lines:
        line_stripped = line.strip()
        # Prüfen ob diese Zeile eine bekannte relevante Überschrift ist
        is_relevant_heading = any(
            abschnitt.lower() in line_stripped.lower()
            for abschnitt in RELEVANTE_ABSCHNITTE
        )
        # Prüfen ob es eine andere Abschnittsüberschrift ist (beendet relevanten Block)
        is_any_heading = bool(re.match(r"^[A-ZÄÖÜ][\w\s-]{3,50}:?$", line_stripped))

        if is_relevant_heading:
            in_relevant = True
            found_any = True
            collected.append(line)
        elif is_any_heading and not is_relevant_heading and in_relevant:
            in_relevant = False
        elif in_relevant:
            collected.append(line)

    if not found_any:
        logger.debug("Keine bekannten Abschnitte gefunden – verwende gesamten Text")
        return text

    result = "\n".join(collected).strip()
    logger.info(
        "Abschnitts-Filterung: %d → %d Zeichen (%d Abschnitte gefunden)",
        len(text), len(result), found_any
    )
    return result if result else text


@router.post("/style/upload", response_model=StyleEmbeddingUploadResponse)
async def upload_style_example(
    therapeut_id:   Annotated[str,  Form(description="Name oder ID des Therapeuten")],
    dokumenttyp:    Annotated[str,  Form(description="dokumentation | anamnese | verlaengerung | entlassbericht")],
    ist_statisch:   Annotated[bool, Form(description="True = Anker-Beispiel (wird immer eingeschlossen)")] = False,
    text_content:   Annotated[Optional[str], Form(description="Direkt eingefügter Text (Alternative zu Datei-Upload)")] = None,
    beispiel_file:  Optional[UploadFile] = File(None, description="Beispieltext (PDF, DOCX oder TXT)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Laedt einen Beispieltext hoch oder nimmt direkt eingefügten Text entgegen,
    extrahiert relevante Abschnitte, erzeugt ein Embedding und speichert alles.

    Zwei Eingabemodi:
    - Datei-Upload: beispiel_file (PDF, DOCX, TXT)
    - C&P-Text: text_content (direkt eingefügter Text)

    Bei Verlängerung/Entlassbericht werden nur die therapeutenspezifischen
    Abschnitte extrahiert (Anamnese, Verlauf, Vorgeschichte etc.) –
    standardisierte Felder werden ignoriert.
    """
    if dokumenttyp not in DOKUMENTTYPEN:
        raise HTTPException(
            status_code=422,
            detail=f"Ungueltiger Dokumenttyp. Erlaubt: {', '.join(DOKUMENTTYPEN)}",
        )

    if not therapeut_id.strip():
        raise HTTPException(status_code=422, detail="therapeut_id darf nicht leer sein")

    if not text_content and (not beispiel_file or not beispiel_file.filename):
        raise HTTPException(status_code=422, detail="Entweder text_content oder beispiel_file ist erforderlich")

    # ── Text extrahieren ──────────────────────────────────────────
    source_filename = None
    docx_path = None  # Fuer Abschnittsextraktion bei DOCX
    if text_content and text_content.strip():
        raw_text = text_content.strip()
        source_filename = None
    else:
        path = await save_upload(beispiel_file, ALLOWED_DOCS)
        source_filename = beispiel_file.filename
        # Bei DOCX koennen wir strukturierte Abschnittsextraktion nutzen
        if path.suffix.lower() in (".docx", ".doc"):
            docx_path = path
        try:
            raw_text = await extract_text(path)
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Text konnte nicht extrahiert werden: {e}")

    # ── Abschnitts-Filterung: nur relevante Abschnitte speichern ──
    # Bei DOCX: verlaessliche Heading-Extraktion (erkennt Bold-Headings, Plain-Text-Headings,
    # End-Marker wie Grussformeln). Bei anderen Formaten: Fallback auf primitive Text-Filterung.
    if dokumenttyp in DOKUMENTTYPEN_MIT_ABSCHNITTEN:
        if docx_path is not None:
            from app.services.extraction import extract_docx_section
            section_text = extract_docx_section(docx_path, dokumenttyp)
            if section_text and len(section_text.split()) >= 30:
                raw_text = section_text
                logger.info("Stilbibliothek: Abschnitt via extract_docx_section (%d Woerter)",
                            len(raw_text.split()))
            else:
                # Fallback wenn DOCX-Section-Extraktion nichts findet
                raw_text = _extrahiere_relevante_abschnitte(raw_text)
        else:
            # Nicht-DOCX: Text-basierte Filterung
            raw_text = _extrahiere_relevante_abschnitte(raw_text)

    if len(raw_text.strip()) < 30:
        raise HTTPException(status_code=422, detail="Dokument scheint leer zu sein oder konnte nicht gelesen werden")

    word_count = len(raw_text.split())

    # ── Embedding erzeugen ────────────────────────────────────────
    embedding = await get_embedding(raw_text[:4000])   # max. ~4000 Zeichen fuer Embedding
    if embedding is None:
        logger.warning(
            "Embedding fuer Therapeut '%s' / Typ '%s' nicht verfuegbar – "
            "Beispiel wird ohne Vektor gespeichert (Fallback auf Datumsreihenfolge).",
            therapeut_id, dokumenttyp,
        )

    # ── In Datenbank speichern ────────────────────────────────────
    entry = StyleEmbedding(
        therapeut_id=therapeut_id.strip(),
        dokumenttyp=dokumenttyp,
        text=raw_text,
        word_count=word_count,
        source_file=source_filename,
        embedding=embedding,
        ist_statisch=ist_statisch,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)

    logger.info(
        "Stilbeispiel gespeichert: Therapeut='%s', Typ='%s', Woerter=%d, Anker=%s, Embedding=%s",
        therapeut_id, dokumenttyp, word_count, ist_statisch,
        "ja" if embedding else "nein (Fallback)",
    )

    return StyleEmbeddingUploadResponse(
        embedding_id=entry.id,
        therapeut_id=entry.therapeut_id,
        dokumenttyp=entry.dokumenttyp,
        dokumenttyp_label=DOKUMENTTYP_LABELS[entry.dokumenttyp],
        word_count=entry.word_count or 0,
        ist_statisch=entry.ist_statisch,
        created_at=entry.created_at,
    )


# ── Fallback-Kette fuer Stilbeispiele (v18) ──────────────────────────────────
#
# Wenn ein Therapeut fuer `akutantrag` oder `folgeverlaengerung` noch keine
# eigenen Stilbeispiele hochgeladen hat, faellt das Backend auf
# `verlaengerung` zurueck. Begruendung: alle drei Antragstypen folgen
# strukturell aehnlichen Mustern (sektionsbasierter Fliesstext, Wir-Perspektive,
# medizinische Begruendung). Das LLM bekommt mit einem Verlaengerungs-Stil
# einen brauchbaren Anhaltspunkt fuer Tonalitaet, Satzbau und Aufbau.
#
# Die Reihenfolge in der Liste ist die Praeferenz-Reihenfolge:
# 1. Eigene Beispiele dieses Typs zuerst probieren
# 2. Falls nichts vorhanden: Fallback-Typ
# 3. Falls auch nichts: leere Liste -> Frontend zeigt "noch keine Beispiele"

STYLE_FALLBACK_CHAIN: dict[str, list[str]] = {
    "akutantrag":         ["akutantrag", "verlaengerung"],
    "folgeverlaengerung": ["folgeverlaengerung", "verlaengerung"],
    # andere Workflows haben keine Fallbacks
}


@router.get("/style/{therapeut_id}", response_model=StyleEmbeddingListResponse)
async def list_style_examples(
    therapeut_id: str,
    dokumenttyp: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Listet alle Beispiele eines Therapeuten auf, optional gefiltert nach Dokumenttyp.

    v18: Falls fuer `akutantrag` oder `folgeverlaengerung` keine eigenen
    Beispiele vorliegen, werden Verlaengerungs-Beispiele als Fallback
    mitgeliefert. Die Antwort markiert Fallback-Eintraege via dem
    `dokumenttyp_label`-Feld mit dem Praefix `(Fallback)` damit das
    Frontend dem Nutzer transparent anzeigen kann woher der Stil kommt.
    """
    if dokumenttyp and dokumenttyp not in DOKUMENTTYPEN:
        raise HTTPException(status_code=422, detail=f"Ungueltiger Dokumenttyp: {dokumenttyp}")

    # Erste Abfrage: alle Eintraege dieses Therapeuten in der gewuenschten Form
    q = select(StyleEmbedding).where(StyleEmbedding.therapeut_id == therapeut_id)
    if dokumenttyp:
        q = q.where(StyleEmbedding.dokumenttyp == dokumenttyp)
    q = q.order_by(StyleEmbedding.dokumenttyp, StyleEmbedding.ist_statisch.desc(), StyleEmbedding.created_at.desc())

    result = await db.execute(q)
    rows = list(result.scalars().all())

    # v18 Fallback: wenn fuer akutantrag/folgeverlaengerung nichts kam,
    # versuchen wir die Fallback-Kette (typisch: -> verlaengerung)
    fallback_used: Optional[str] = None
    if not rows and dokumenttyp and dokumenttyp in STYLE_FALLBACK_CHAIN:
        for fb_typ in STYLE_FALLBACK_CHAIN[dokumenttyp][1:]:  # [0] ist der Original-Typ
            fb_q = (
                select(StyleEmbedding)
                .where(StyleEmbedding.therapeut_id == therapeut_id)
                .where(StyleEmbedding.dokumenttyp == fb_typ)
                .order_by(
                    StyleEmbedding.ist_statisch.desc(),
                    StyleEmbedding.created_at.desc(),
                )
            )
            fb_result = await db.execute(fb_q)
            fb_rows = list(fb_result.scalars().all())
            if fb_rows:
                rows = fb_rows
                fallback_used = fb_typ
                logger.info(
                    "Stilbibliothek-Fallback: Therapeut '%s' hat keine '%s'-Beispiele, "
                    "nutze %d '%s'-Beispiele als Fallback",
                    therapeut_id, dokumenttyp, len(rows), fb_typ,
                )
                break

    def _label_for(r: StyleEmbedding) -> str:
        base_label = DOKUMENTTYP_LABELS[r.dokumenttyp]
        if fallback_used and r.dokumenttyp == fallback_used:
            return f"(Fallback) {base_label}"
        return base_label

    return StyleEmbeddingListResponse(
        therapeut_id=therapeut_id,
        total=len(rows),
        embeddings=[
            StyleEmbeddingInfo(
                embedding_id=r.id,
                dokumenttyp=r.dokumenttyp,
                dokumenttyp_label=_label_for(r),
                word_count=r.word_count,
                ist_statisch=r.ist_statisch,
                created_at=r.created_at,
                text_preview=r.text[:200] + ("…" if len(r.text) > 200 else ""),
            )
            for r in rows
        ],
    )


@router.delete("/style/embedding/{embedding_id}")
async def delete_style_example(
    embedding_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Loescht ein einzelnes Stilbeispiel."""
    result = await db.execute(
        select(StyleEmbedding).where(StyleEmbedding.id == embedding_id)
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Beispiel nicht gefunden")

    await db.delete(entry)
    await db.commit()
    logger.info("Stilbeispiel geloescht: %s", embedding_id)
    return {"deleted": embedding_id}
