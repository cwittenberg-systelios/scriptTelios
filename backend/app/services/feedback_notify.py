"""
backend/app/services/feedback_notify.py — Push-Benachrichtigung bei Feedback (Sprint F1).

Konfigurierbarer Notifier mit Kanal-Abstraktion:
  FEEDBACK_NOTIFY = "off"      → keine Benachrichtigung (Default)
  FEEDBACK_NOTIFY = "telegram" → Telegram-Bot-Nachricht
                                 (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID nötig;
                                  gleicher Bot wie die Selfcheck-Alerts des
                                  Cloudflare Workers — Vars müssen zusätzlich
                                  im RUNPOD_STARTCOMMAND exportiert werden)

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
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_TELEGRAM_TIMEOUT_S = 10


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


async def _send_telegram(text: str) -> None:
    token = (settings.TELEGRAM_BOT_TOKEN or "").strip()
    chat_id = (settings.TELEGRAM_CHAT_ID or "").strip()
    if not token or not chat_id:
        logger.warning(
            "FEEDBACK_NOTIFY=telegram, aber TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID "
            "fehlen — Push übersprungen."
        )
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient(timeout=_TELEGRAM_TIMEOUT_S) as client:
        r = await client.post(url, json={"chat_id": chat_id, "text": text})
        r.raise_for_status()


# Kanal-Registry — neue Kanäle hier eintragen.
_CHANNELS = {
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
        await sender(_build_message(rating, workflow, job_id, user))
        logger.info("Feedback-Push via %s versendet (Job %s).", channel, (job_id or "?")[:8])
    except Exception as e:  # noqa: BLE001 — bewusst breit: Push darf nie durchschlagen
        logger.warning("Feedback-Push via %s fehlgeschlagen: %s", channel, e)


def notify_feedback_background(rating: int, workflow: str, job_id: str, user: str) -> None:
    """Startet notify_feedback als Hintergrund-Task (nicht awaiten)."""
    try:
        asyncio.get_running_loop().create_task(
            notify_feedback(rating, workflow, job_id, user)
        )
    except RuntimeError:
        # Kein laufender Loop (z.B. Sync-Testkontext) — dann synchron best effort.
        try:
            asyncio.run(notify_feedback(rating, workflow, job_id, user))
        except Exception as e:  # noqa: BLE001
            logger.warning("Feedback-Push (sync-Fallback) fehlgeschlagen: %s", e)
