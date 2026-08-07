"""
backend/app/middleware/activity.py — Aktivitaets-Tracking fuer den
Idle-Auto-Stopp (Sprint v19.10).

Der Cloudflare-Worker stoppt den Pod, wenn er laenger als eine tageszeit-
abhaengige Schwelle unbenutzt ist. Dafuer braucht er ein Idle-Mass. Dieses
Modul haelt den Zeitstempel des letzten "echten" Requests; `selfcheck.py`
liefert ihn als `activity.idle_sec` aus.

WARUM EIN REQUEST-ZEITSTEMPEL UND NICHT NUR JOB-AKTIVITAET:
Waehrend einer laufenden Aufnahme erreicht den Pod sonst gar nichts —
`POST /api/recordings` laedt erst am Ende hoch, `Recording` hat kein
`updated_at`, und das 30-s-Polling im Frontend startet nur bei Items mit
Status uploading/transcribing. Ein reiner Job-Zaehler wuerde den Pod mitten
in der Sitzung als idle melden. Das Frontend sendet deshalb waehrend der
Aufnahme einen Heartbeat (siehe app/api/activity.py).

WARUM /api/health UND /api/selfcheck AUSGENOMMEN SIND:
Die pollt der Worker selbst (Selfcheck alle 15 min, Health-Warteschleife alle
30 s beim Start). Wuerden sie als Aktivitaet zaehlen, waere der Pod nie idle
und der Auto-Stopp wuerde nie ausloesen.

WARUM GET NICHT ZAEHLT (v19.10c):
Das Makro pollt im Hintergrund, ohne dass jemand davorsitzt — P1 laedt die
Job-Liste alle 5 s (P1.jsx:127), P0 die Aufnahmen alle 30 s. Ein offener Tab
hat den Idle-Zaehler damit dauerhaft nahe 0 gehalten: der Stopp nach 18:00
loeste nie aus, erst die Nachtabschaltung um 23:00 griff (die prueft keine
Aktivitaet). Genau das sollte der Auto-Stopp verhindern.

Statuspolling ist Maschinenverkehr, kein Nutzungssignal. Die Trennung laeuft
deshalb ueber die HTTP-Methode: GET fragt ab, alles andere veraendert etwas
und ist damit eine bewusste Handlung — Job starten (POST /jobs/generate),
Aufnahme hochladen (POST /recordings), Reparatur, Loeschen, Retry, und der
Aufnahme-Heartbeat (POST /activity/heartbeat, bewusst POST statt GET).

Bewusst in Kauf genommen: GET /jobs/{id}/transcript und /download sind echte
Benutzung, zaehlen aber nicht mehr. Folge ist hoechstens, dass der Pod 30 min
nach dem letzten Abruf statt nach dem letzten Klick herunterfaehrt — waehrend
eine Aufnahme laeuft, haelt ohnehin der Heartbeat dagegen.
"""
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

# Maschinen-Polling, das keine Benutzeraktivitaet darstellt.
_IGNORED_PATHS = {
    "/api/health",
    "/health",
    "/api/selfcheck",
    "/metrics",
    "/favicon.ico",
}

# Startwert = Prozessstart. Ein frisch gebooteter Pod ist damit "idle 0" und
# wird nicht sofort nach dem manuellen Start wieder heruntergefahren.
_last_activity: float = time.time()


def touch() -> None:
    """Aktivitaet vermerken."""
    global _last_activity
    _last_activity = time.time()


def idle_seconds() -> float:
    """Sekunden seit dem letzten gebuchten Request (nie negativ)."""
    return max(0.0, time.time() - _last_activity)


# Nur diese Methoden gelten als Benutzung. GET/HEAD/OPTIONS sind Abfrage bzw.
# Preflight und werden vom Frontend im Sekundentakt automatisch erzeugt.
_ACTIVE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class ActivityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if (request.method in _ACTIVE_METHODS
                and request.url.path not in _IGNORED_PATHS):
            touch()
        return await call_next(request)
