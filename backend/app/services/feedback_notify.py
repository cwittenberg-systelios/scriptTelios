"""
backend/app/services/feedback_notify.py — Push-Benachrichtigung bei Feedback (Sprint F1).

Konfigurierbarer Notifier mit Kanal-Abstraktion:
  FEEDBACK_NOTIFY = "off"      → keine Benachrichtigung (Default)
  FEEDBACK_NOTIFY = "worker"   → EMPFOHLEN: der Cloudflare Worker versendet.
                                 Der Pod meldet nur "es gab ein Feedback";
                                 die Telegram-Credentials bleiben ausschliesslich
                                 im Cloudflare Secrets Store. Kein neues Secret
                                 auf dem Pod - signiert wird mit dem ohnehin
                                 vorhandenen CONFLUENCE_SHARED_SECRET.
                                 Braucht nur WORKER_NOTIFY_URL (nicht geheim).
  FEEDBACK_NOTIFY = "telegram" → Fallback: Pod sendet direkt an Telegram
                                 (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID nötig).
                                 Nur sinnvoll wenn der Worker nicht erreichbar
                                 sein soll - dupliziert den Bot-Token auf die
                                 Maschine mit den Klientendaten.

Konfigurationsort: /workspace/.env auf dem Pod. runpod-start.sh sourct diese
Datei mit "set -a", die Werte landen also in der Prozessumgebung von uvicorn.
Sie liegt auf dem persistenten Network Volume und überlebt Stop/Resume.
Der gleiche Bot bedient die Selfcheck-Alerts des Cloudflare Workers — dessen
Secrets-Store-Bindings sind für das Backend aber NICHT sichtbar (der Worker
versendet seine Alerts selbst und startet den Pod nur per podResume, ohne
Env-Übergabe). Die Werte müssen daher separat auf dem Pod stehen.

Weitere Kanäle (z.B. SMTP) können als _send_<kanal>() ergänzt und in
_CHANNELS registriert werden.

DATENSCHUTZ (Entscheidung 2026-07-21, Cars10):
  Die Push-Nachricht enthält NIEMALS den Feedback-Freitext und keinerlei
  Klienteninhalte — Telegram-Server liegen außerhalb der EU. Inhalt ist
  ausschließlich: Rating, Workflow, Job-ID, Therapeuten-Login.
  Der Freitext bleibt allein in feedback.log auf dem Pod (EU-Infrastruktur).

Fehlertoleranz: notify_feedback() ist fire-and-forget — jede Exception wird
geloggt, aber niemals propagiert. Die Feedback-Speicherung darf nie am
Push scheitern.
"""
import asyncio
import hashlib
import hmac
import logging
import time

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_TELEGRAM_TIMEOUT_S = 10
_WORKER_TIMEOUT_S = 10
# Kennung, unter der das Backend beim Worker signiert (taucht im KV-Log des
# Workers auf). Kein Confluence-Login - macht Backend-Aufrufe unterscheidbar.
_NOTIFY_USER = "backend"
# Hinweis: Kanaele bekommen sowohl den fertigen Text als auch die
# strukturierten Felder uebergeben. Kein Modul-Zwischenspeicher - der waere
# bei zwei gleichzeitigen Feedbacks eine Race Condition.


def _build_message(rating: int, workflow: str, job_id: str, user: str) -> str:
    """Baut den Push-Text. Bewusst ohne Freitext (siehe Modul-Docstring)."""
    stars = "\u2b50" * max(1, min(5, rating))
    kurz_id = (job_id or "?")[:8]
    return (
        f"{stars} {rating}/5 Feedback\n"
        f"Workflow: {workflow or '?'}\n"
        f"Job: {kurz_id}\n"
        f"Von: {user or '?'}"
    )


async def _send_telegram(text: str, payload: dict) -> None:
    token = (settings.TELEGRAM_BOT_TOKEN or "").strip()
    chat_id = (settings.TELEGRAM_CHAT_ID or "").strip()
    missing = [n for n, v in (("TELEGRAM_BOT_TOKEN", token),
                              ("TELEGRAM_CHAT_ID", chat_id)) if not v]
    if missing:
        logger.warning(
            "Feedback-Push übersprungen: %s fehlt/fehlen in der Pod-Umgebung. "
            "Achtung: die gleichnamigen Cloudflare-Worker-Secrets zählen NICHT — "
            "der Worker versendet seine Selfcheck-Alerts selbst, das Backend hat "
            "keinen Zugriff darauf. Werte in /workspace/.env eintragen "
            "(wird von runpod-start.sh mit 'set -a' exportiert) und Pod neu starten.",
            ", ".join(missing),
        )
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient(timeout=_TELEGRAM_TIMEOUT_S) as client:
        r = await client.post(url, json={"chat_id": chat_id, "text": text})
        r.raise_for_status()


async def _send_worker(text: str, payload: dict) -> None:
    """
    Meldet die Benachrichtigung an den Cloudflare Worker, der sie versendet.

    Der Worker baut den Nachrichtentext selbst aus den strukturierten Feldern
    neu — er ist bewusst kein Freitext-Relay (siehe handleNotify() in
    cloudflareworker.js). Deshalb wird hier ``payload`` uebergeben und
    ``text`` gar nicht erst mitgeschickt.
    """
    url = (settings.WORKER_NOTIFY_URL or "").strip()
    if not url:
        logger.warning(
            "Feedback-Push übersprungen: WORKER_NOTIFY_URL ist nicht gesetzt. "
            "In /workspace/.env eintragen (z.B. https://control.example.tld/notify) "
            "und Pod neu starten."
        )
        return
    async with httpx.AsyncClient(timeout=_WORKER_TIMEOUT_S) as client:
        r = await client.post(url, json={"kind": "feedback", **payload},
                              headers=_signed_headers())
        r.raise_for_status()


def _signed_headers() -> dict:
    """
    HMAC-Header im Schema aus core/auth.py — dasselbe, das der Worker prueft.
    Kein neues Secret noetig: CONFLUENCE_SHARED_SECRET liegt ohnehin auf dem Pod.
    """
    ts = str(int(time.time()))
    user = _NOTIFY_USER
    sig = hmac.new(
        settings.CONFLUENCE_SHARED_SECRET.encode("utf-8"),
        f"{user}:{ts}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-Systelios-User": user,
        "X-Systelios-Timestamp": ts,
        "X-Systelios-Signature": sig,
        "Content-Type": "application/json",
    }


# Kanal-Registry — neue Kanäle hier eintragen.
_CHANNELS = {
    "worker": _send_worker,
    "telegram": _send_telegram,
}


async def notify_feedback(rating: int, workflow: str, job_id: str, user: str) -> None:
    """
    Versendet die Feedback-Push-Nachricht über den konfigurierten Kanal.
    Fire-and-forget-tauglich: wirft nie.
    """
    channel = (settings.FEEDBACK_NOTIFY or "off").strip().lower()
    if channel in ("", "off", "none", "0", "false"):
        return
    sender = _CHANNELS.get(channel)
    if sender is None:
        logger.warning("FEEDBACK_NOTIFY=%r ist kein bekannter Kanal (%s) — Push übersprungen.",
                       channel, ", ".join(sorted(_CHANNELS)))
        return
    try:
        await sender(
            _build_message(rating, workflow, job_id, user),
            {"rating": rating, "workflow": workflow,
             "jobId": (job_id or "")[:8], "user": user},
        )
        logger.info("Feedback-Push via %s versendet (Job %s).", channel, (job_id or "?")[:8])
    except Exception as e:  # noqa: BLE001 — bewusst breit: Push darf nie durchschlagen
        logger.warning("Feedback-Push via %s fehlgeschlagen: %s", channel, e)


# RUF006: referenzlose Tasks darf der GC jederzeit einsammeln — asyncio haelt
# selbst nur schwache Referenzen. Ohne dieses Set kann ein Push mitten im
# HTTP-Call verschwinden (still, ohne Logzeile). Gleiche Konvention wie die
# Task-Referenzen im lifespan von main.py.
_background_tasks: set[asyncio.Task] = set()


def notify_feedback_background(rating: int, workflow: str, job_id: str, user: str) -> None:
    """Startet notify_feedback als Hintergrund-Task (nicht awaiten)."""
    try:
        task = asyncio.get_running_loop().create_task(
            notify_feedback(rating, workflow, job_id, user)
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    except RuntimeError:
        # Kein laufender Loop (z.B. Sync-Testkontext) — dann synchron best effort.
        try:
            asyncio.run(notify_feedback(rating, workflow, job_id, user))
        except Exception as e:  # noqa: BLE001
            logger.warning("Feedback-Push (sync-Fallback) fehlgeschlagen: %s", e)


def log_effective_config() -> None:
    """
    Beim Start aufgerufen (lifespan in main.py). Macht eine Fehlkonfiguration
    sofort im Log sichtbar, statt sie erst beim ersten Feedback aufzudecken —
    bis dahin sieht der Nutzer nur "kein Push kommt an".
    """
    raw = settings.FEEDBACK_NOTIFY or "off"
    channel = raw.strip().lower()
    if channel in ("", "off", "none", "0", "false"):
        logger.info("Feedback-Push: deaktiviert (FEEDBACK_NOTIFY=%r).", raw)
        return
    if channel not in _CHANNELS:
        logger.warning("Feedback-Push: FEEDBACK_NOTIFY=%r ist kein bekannter Kanal "
                       "(bekannt: %s) — es wird nichts versendet.",
                       raw, ", ".join(sorted(_CHANNELS)))
        return
    if channel == "worker":
        if not (settings.WORKER_NOTIFY_URL or "").strip():
            logger.warning(
                "Feedback-Push: FEEDBACK_NOTIFY=worker, aber WORKER_NOTIFY_URL fehlt "
                "— es wird NICHTS versendet. In /workspace/.env eintragen.")
            return
        if not (settings.CONFLUENCE_SHARED_SECRET or "").strip():
            logger.warning("Feedback-Push: CONFLUENCE_SHARED_SECRET fehlt — der "
                           "Worker wird die Signatur ablehnen (401).")
            return
    if channel == "telegram":
        missing = [n for n, v in (("TELEGRAM_BOT_TOKEN", settings.TELEGRAM_BOT_TOKEN),
                                  ("TELEGRAM_CHAT_ID", settings.TELEGRAM_CHAT_ID))
                   if not (v or "").strip()]
        if missing:
            logger.warning(
                "Feedback-Push: FEEDBACK_NOTIFY=telegram, aber %s fehlt/fehlen in der "
                "Pod-Umgebung — es wird NICHTS versendet. Die gleichnamigen "
                "Cloudflare-Worker-Secrets zaehlen nicht; Werte gehoeren in "
                "/workspace/.env.", ", ".join(missing))
            return
    logger.info("Feedback-Push aktiv (Kanal: %s).", channel)
