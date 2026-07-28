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
    def __init__(self, detail: str, headers: dict | None = None):
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED,
                         detail=detail, headers=headers)


def _compute_signature(user: str, timestamp: str) -> str:
    """HMAC-SHA256(secret, user + ':' + timestamp) als Hex-String."""
    msg = f"{user}:{timestamp}".encode("utf-8")
    key = settings.CONFLUENCE_SHARED_SECRET.encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def verify_signature(user: str, timestamp: str, signature: str) -> bool:
    """Prüft HMAC und Zeitstempel-Fenster. True nur, wenn beides passt.

    Für differenzierte Fehlermeldungen (v19.14b: Uhr-Drift vs. Signatur) siehe
    check_auth(); diese Funktion bleibt als einfaches bool-Prädikat erhalten.
    """
    ok, _reason, _skew = check_auth(user, timestamp, signature)
    return ok


def check_auth(user: str, timestamp: str, signature: str):
    """
    Prüft HMAC-Signatur und Zeitstempel-Fenster und liefert den GRUND zurück.

    Returns:
        (ok, reason, skew_seconds)
        - ok:     True bei gültiger, frischer Signatur
        - reason: "ok" | "missing" | "bad_timestamp" | "clock_skew" | "bad_signature"
        - skew_seconds: bei reason="clock_skew" die Abweichung in Sekunden
          (positiv = Gerät geht nach, negativ = Gerät geht vor), sonst None

    v19.14b: Der Uhr-Drift-Fall ("clock_skew") wird bewusst VOR der
    Signaturprüfung geprüft und getrennt gemeldet. Ein Therapeut mit falsch
    gestellter Systemuhr bekam vorher dasselbe generische 401 wie bei einem
    echten Auth-Fehler und hielt das System für defekt — dabei ist es ein
    lokales Uhrproblem, das er selbst beheben kann. Im Audit-Log war der Fall
    ebenfalls nicht unterscheidbar (user ist bei jedem 401 "-").

    Die Reihenfolge (erst Zeitfenster, dann HMAC) ist unkritisch für die
    Sicherheit: Eine abgelaufene Signatur wird so oder so abgelehnt; wir
    verraten nur, DASS sie abgelaufen ist, nicht ob sie gültig gewesen WÄRE.
    Die Serverzeit ist ohnehin über den Date-Header jeder Antwort lesbar.
    """
    if not user or not timestamp or not signature:
        return (False, "missing", None)
    try:
        ts = int(timestamp)
    except ValueError:
        return (False, "bad_timestamp", None)
    skew = time.time() - ts
    if abs(skew) > settings.AUTH_TIMESTAMP_WINDOW_SEC:
        return (False, "clock_skew", int(skew))
    expected = _compute_signature(user, timestamp)
    if not hmac.compare_digest(expected, signature):
        return (False, "bad_signature", None)
    return (True, "ok", None)


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

    ok, reason, skew = check_auth(user, timestamp, signature)
    if not ok:
        if reason == "clock_skew":
            # v19.14b: dem Nutzer die SELBST behebbare Ursache nennen. Betrag +
            # Richtung helfen beim Stellen der Uhr; der Header macht den Fall
            # im Reverse-Proxy-Log und in den Browser-Devtools eindeutig.
            richtung = "vor" if skew < 0 else "nach"
            minuten = abs(skew) / 60.0
            raise AuthError(
                f"Die Uhr dieses Geräts geht rund {minuten:.0f} Minuten {richtung} "
                f"(zulässig sind {settings.AUTH_TIMESTAMP_WINDOW_SEC // 60} Minuten "
                f"Abweichung). Bitte die Systemzeit auf automatisch/synchronisiert "
                f"stellen und neu laden.",
                headers={"X-Systelios-Auth-Error": "clock_skew",
                         "X-Systelios-Clock-Skew": str(skew)},
            )
        raise AuthError("Ungültige oder fehlende Authentifizierung",
                        headers={"X-Systelios-Auth-Error": reason})

    request.state.user_id = user
    return user
