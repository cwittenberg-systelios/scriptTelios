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
from sqlalchemy import select, func
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


@router.post("/style/upload", response_model=StyleEmbeddingUploadResponse)
async def upload_style_example(
    therapeut_id:  Annotated[str,  Form(description="Name oder ID des Therapeuten")],
    dokumenttyp:   Annotated[str,  Form(description="dokumentation | anamnese | verlaengerung | entlassbericht")],
    ist_statisch:  Annotated[bool, Form(description="True = Anker-Beispiel (wird immer eingeschlossen)")] = False,
    beispiel_file: UploadFile = File(..., description="Beispieltext (PDF, DOCX oder TXT)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Laedt einen Beispieltext hoch, extrahiert den Text, erzeugt ein
    Embedding und speichert alles in der Datenbank.
    """
    if dokumenttyp not in DOKUMENTTYPEN:
        raise HTTPException(
            status_code=422,
            detail=f"Ungueltiger Dokumenttyp. Erlaubt: {', '.join(DOKUMENTTYPEN)}",
        )

    if not therapeut_id.strip():
        raise HTTPException(status_code=422, detail="therapeut_id darf nicht leer sein")

    # ── Text extrahieren ──────────────────────────────────────────
    path = await save_upload(beispiel_file, ALLOWED_DOCS)
    try:
        raw_text = await extract_text(path)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Text konnte nicht extrahiert werden: {e}")

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
        source_file=beispiel_file.filename,
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


@router.get("/style/{therapeut_id}", response_model=StyleEmbeddingListResponse)
async def list_style_examples(
    therapeut_id: str,
    dokumenttyp: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Listet alle Beispiele eines Therapeuten auf, optional gefiltert nach Dokumenttyp."""
    if dokumenttyp and dokumenttyp not in DOKUMENTTYPEN:
        raise HTTPException(status_code=422, detail=f"Ungueltiger Dokumenttyp: {dokumenttyp}")

    q = select(StyleEmbedding).where(StyleEmbedding.therapeut_id == therapeut_id)
    if dokumenttyp:
        q = q.where(StyleEmbedding.dokumenttyp == dokumenttyp)
    q = q.order_by(StyleEmbedding.dokumenttyp, StyleEmbedding.ist_statisch.desc(), StyleEmbedding.created_at.desc())

    result = await db.execute(q)
    rows = result.scalars().all()

    return StyleEmbeddingListResponse(
        therapeut_id=therapeut_id,
        total=len(rows),
        embeddings=[
            StyleEmbeddingInfo(
                embedding_id=r.id,
                dokumenttyp=r.dokumenttyp,
                dokumenttyp_label=DOKUMENTTYP_LABELS[r.dokumenttyp],
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
