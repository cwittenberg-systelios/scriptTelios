"""Audit-Logging-Middleware: loggt alle API-Zugriffe ohne Textinhalte."""
import json
import logging
import time
from pathlib import Path
from logging.handlers import RotatingFileHandler

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.config import settings

_audit_logger = logging.getLogger("systelios.audit")


def _setup_audit_logger() -> None:
    if _audit_logger.handlers:
        return
    log_path = Path(settings.AUDIT_LOG_PATH)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        str(log_path), maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    _audit_logger.addHandler(handler)
    _audit_logger.setLevel(logging.INFO)
    _audit_logger.propagate = False


_setup_audit_logger()


# Pfade die nicht geloggt werden sollen (zu rauschintensiv)
_SKIP_PATHS = {"/api/health", "/health", "/metrics", "/favicon.ico"}


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        duration_ms = int((time.time() - start) * 1000)

        path = request.url.path
        if path in _SKIP_PATHS:
            return response

        user = getattr(request.state, "user_id", None) or "-"
        entry = {
            "ts": int(time.time()),
            "user": user,
            "method": request.method,
            "path": path,
            "status": response.status_code,
            "duration_ms": duration_ms,
            "ip": request.client.host if request.client else "-",
        }
        # Job-ID aus Pfad extrahieren wenn vorhanden
        if "/jobs/" in path:
            parts = path.split("/jobs/")
            if len(parts) > 1:
                entry["job_id"] = parts[1].split("/")[0][:36]

        try:
            _audit_logger.info(json.dumps(entry, ensure_ascii=False))
        except Exception:
            pass

        return response
