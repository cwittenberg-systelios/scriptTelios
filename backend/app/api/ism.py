"""
POST /api/ism/xml - SNS-XML aus einem (ggf. im Frontend editierten)
ISM-Fragebogen rendern (PX, v19.18).

Warum ein Endpoint statt eines JS-Renderers im Frontend: der XML-Renderer
lebt genau EINMAL (Single-Source-of-Truth, app/services/ism.render_sns_xml)
und ist dort per Golden-Test gegen die SNS-Referenzstruktur abgesichert.
Das Frontend schickt den editierten Zustand der Vorschau und bekommt den
fertigen XML-String fuer Kopieren/Download zurueck.
"""
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.ism import IsmFragebogen, render_sns_xml

router = APIRouter()
logger = logging.getLogger(__name__)


class IsmXmlRequest(BaseModel):
    """Editierter Fragebogen + Name (aus der SNS-Kennung, Pflichtfeld im
    Frontend - z.B. 'MJ28585IND individualisiert')."""
    name: str = Field(min_length=1, max_length=120)
    fragebogen: IsmFragebogen


class IsmXmlResponse(BaseModel):
    xml: str
    filename: str


@router.post("/ism/xml", response_model=IsmXmlResponse, tags=["ISM"])
async def ism_xml(req: IsmXmlRequest) -> IsmXmlResponse:
    """Rendert SNS-konformes XML aus dem uebergebenen Fragebogen.

    Validierung uebernimmt Pydantic (IsmFragebogen); strukturell invalide
    Payloads werden von FastAPI mit 422 abgelehnt, bevor dieser Handler
    laeuft. Hier nur noch Rendering + Dateiname.
    """
    try:
        xml = render_sns_xml(req.fragebogen, req.name.strip())
    except Exception as e:  # pragma: no cover - Renderer ist deterministisch
        logger.error("ISM-XML-Rendering fehlgeschlagen: %s", e)
        raise HTTPException(status_code=500, detail=f"XML-Rendering fehlgeschlagen: {e}") from e

    # Dateiname aus der Kennung: nur URL-/dateisystem-sichere Zeichen.
    stem = "".join(
        c for c in req.name.strip() if c.isalnum() or c in ("-", "_")
    ) or "ism_fragebogen"
    return IsmXmlResponse(xml=xml, filename=f"{stem}.xml")
