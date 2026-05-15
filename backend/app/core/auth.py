"""
Confluence HMAC-Auth für scriptTelios Backend.

Das Confluence-Macro läuft in einer bereits LDAP-authentifizierten Session.
Der Backend vertraut dem von Confluence gemeldeten Username, sofern dieser
mit HMAC-SHA256 über ein Shared Secret signiert ist.

Header pro Request:
    X-Systelios-User: <username>
    X-Systelios-Timestamp: <unix_seconds>
    X-Systelios-Signature: <hex_hmac_sha256>

Schutz vor Replay: Timestamp darf max AUTH_TIMESTAMP_WINDOW_SEC alt sein.
"""
import hmac
import hashlib
import time
from fastapi import Request, HTTPException, status

from app.core.config import settings


class AuthError(HTTPException):
    def __init__(self, detail: str):
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def _compute_signature(user: str, timestamp: str) -> str:
    """HMAC-SHA256(secret, user + ':' + timestamp) als Hex-String."""
    msg = f"{user}:{timestamp}".encode("utf-8")
    key = settings.CONFLUENCE_SHARED_SECRET.encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def verify_signature(user: str, timestamp: str, signature: str) -> bool:
    """Prüft HMAC und Zeitstempel-Fenster."""
    if not user or not timestamp or not signature:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    age = abs(time.time() - ts)
    if age > settings.AUTH_TIMESTAMP_WINDOW_SEC:
        return False
    expected = _compute_signature(user, timestamp)
    return hmac.compare_digest(expected, signature)


async def get_current_user(request: Request) -> str:
    """
    FastAPI-Dependency: liefert den validierten Username aus dem Confluence-Header.

    Bei deaktivierter Auth (Dev-Modus) und keinem confluence user wird "dev-user" zurückgegeben.
    Bei fehlender/falscher Signatur wird HTTP 401 geworfen.
    """
    user = request.headers.get("X-Systelios-User", "")
    timestamp = request.headers.get("X-Systelios-Timestamp", "")
    signature = request.headers.get("X-Systelios-Signature", "")

    if not settings.AUTH_ENABLED and not user:
        request.state.user_id = "dev-user"
        return "dev-user"

    if not verify_signature(user, timestamp, signature):
        raise AuthError("Ungültige oder fehlende Authentifizierung")

    request.state.user_id = user
    return user
