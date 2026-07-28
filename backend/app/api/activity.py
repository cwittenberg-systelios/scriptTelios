"""
backend/app/api/activity.py — Lebenszeichen des Frontends (Sprint v19.10).

POST /api/activity/heartbeat wird vom Aufnahme-Recorder im Minutentakt
gesendet, solange eine Aufnahme laeuft. Zweck: der Idle-Auto-Stopp im
Cloudflare-Worker darf den Pod nicht mitten in einer Sitzung herunterfahren —
waehrend der Aufnahme sieht der Pod sonst keinerlei Traffic (Upload erfolgt
erst am Ende).

Die Buchung selbst erledigt ActivityMiddleware fuer jeden nicht ignorierten
Request. Der Endpunkt ist deshalb bewusst leer: er existiert nur, damit das
Frontend ueberhaupt etwas anfassen kann, das guenstig ist und keine
Seiteneffekte hat.

Auth wie ueberall ueber den HMAC-verifizierten X-Systelios-User-Header.
"""
from fastapi import APIRouter, Depends, Response

from app.core.auth import get_current_user

router = APIRouter()


@router.post("/activity/heartbeat", status_code=204)
async def heartbeat(user: str = Depends(get_current_user)) -> Response:
    return Response(status_code=204)
