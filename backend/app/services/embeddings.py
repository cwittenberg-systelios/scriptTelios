"""
Embedding-Service fuer Stilprofil-Retrieval via pgvector.

Verwendet nomic-embed-text ueber die Ollama API.
Dimensionen: 768 (nomic-embed-text Standard).

Ablauf bei Generierung:
  1. Statische Anker-Beispiele des Therapeuten immer einschliessen
  2. Top-K semantisch aehnlichste Beispiele per Kosinus-Distanz ergaenzen
  3. Max. MAX_EXAMPLES Beispiele gesamt in den Prompt
"""
import logging
from typing import Optional

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.db import StyleEmbedding

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "nomic-embed-text"
EMBEDDING_DIM   = 768
MAX_EXAMPLES    = 5   # Maximale Anzahl Beispiele im Prompt
TOP_K_SEMANTIC  = 3   # Wie viele semantisch aehnliche Beispiele gesucht werden


async def get_embedding(text: str) -> list[float] | None:
    """
    Erzeugt einen Vektor fuer den gegebenen Text via Ollama.
    Gibt None zurueck wenn Ollama nicht erreichbar oder Modell fehlt.
    """
    payload = {"model": EMBEDDING_MODEL, "prompt": text}

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            r = await client.post(
                f"{settings.OLLAMA_HOST}/api/embeddings",
                json=payload,
            )
            r.raise_for_status()
            data = r.json()
            embedding = data.get("embedding")
            if not embedding or len(embedding) != EMBEDDING_DIM:
                logger.warning(
                    "Unerwartete Embedding-Dimension: %d (erwartet %d)",
                    len(embedding) if embedding else 0,
                    EMBEDDING_DIM,
                )
                return None
            return embedding
        except httpx.ConnectError:
            logger.warning(
                "Ollama nicht erreichbar fuer Embeddings (%s). "
                "Statische Beispiele werden verwendet.",
                settings.OLLAMA_HOST,
            )
            return None
        except Exception as e:
            logger.warning("Embedding-Fehler: %s", e)
            return None


async def retrieve_style_examples(
    db: AsyncSession,
    therapeut_id: str,
    dokumenttyp: str,
    query_text: str,
) -> str:
    """
    Gibt den zusammengesetzten Stilkontext fuer den Prompt zurueck.

    Strategie:
      - Statische Anker-Beispiele (ist_statisch=True) immer einschliessen
      - Semantisch aehnlichste Beispiele per pgvector-Suche ergaenzen
      - Fallback: neueste Beispiele wenn kein Embedding verfuegbar
    """
    # ── 1. Statische Anker-Beispiele ──────────────────────────────
    static_q = (
        select(StyleEmbedding)
        .where(
            StyleEmbedding.therapeut_id == therapeut_id,
            StyleEmbedding.dokumenttyp == dokumenttyp,
            StyleEmbedding.ist_statisch == True,  # noqa: E712
        )
        .limit(MAX_EXAMPLES)
    )
    static_result = await db.execute(static_q)
    static_examples = static_result.scalars().all()

    remaining_slots = MAX_EXAMPLES - len(static_examples)
    semantic_examples = []

    if remaining_slots > 0:
        # ── 2. Semantische Suche ──────────────────────────────────
        query_embedding = await get_embedding(query_text)

        if query_embedding is not None:
            # IDs der statischen Beispiele ausschliessen
            static_ids = [e.id for e in static_examples]

            # pgvector Kosinus-Distanz (<=>)
            # Nur Beispiele desselben Therapeuten + Dokumenttyps
            vec_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

            exclude_clause = ""
            if static_ids:
                ids_str = ", ".join(f"'{i}'" for i in static_ids)
                exclude_clause = f"AND id NOT IN ({ids_str})"

            sql = text(f"""
                SELECT id, text, word_count, ist_statisch
                FROM style_embeddings
                WHERE therapeut_id = :therapeut_id
                  AND dokumenttyp  = :dokumenttyp
                  AND embedding    IS NOT NULL
                  {exclude_clause}
                ORDER BY embedding <=> :vec::vector
                LIMIT :k
            """)
            rows = await db.execute(
                sql,
                {
                    "therapeut_id": therapeut_id,
                    "dokumenttyp": dokumenttyp,
                    "vec": vec_str,
                    "k": remaining_slots,
                },
            )
            for row in rows:
                # Pseudo-Objekt fuer einheitliche Verarbeitung
                class _Ex:
                    pass
                ex = _Ex()
                ex.text = row.text
                ex.ist_statisch = row.ist_statisch
                semantic_examples.append(ex)

        else:
            # Fallback: neueste Beispiele ohne Embedding-Suche
            fallback_q = (
                select(StyleEmbedding)
                .where(
                    StyleEmbedding.therapeut_id == therapeut_id,
                    StyleEmbedding.dokumenttyp == dokumenttyp,
                    StyleEmbedding.ist_statisch == False,  # noqa: E712
                )
                .order_by(StyleEmbedding.created_at.desc())
                .limit(remaining_slots)
            )
            fallback_result = await db.execute(fallback_q)
            semantic_examples = fallback_result.scalars().all()

    all_examples = list(static_examples) + semantic_examples

    if not all_examples:
        return ""

    # ── 3. Stilkontext zusammensetzen ─────────────────────────────
    parts = []
    for i, ex in enumerate(all_examples, 1):
        marker = " [Anker]" if ex.ist_statisch else ""
        parts.append(f"--- Beispiel {i}{marker} ---\n{ex.text.strip()}")

    return "\n\n".join(parts)
