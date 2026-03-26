import logging
import sys
from app.core.config import settings


def setup_logging() -> None:
    fmt = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    try:
        handlers.append(logging.FileHandler(settings.LOG_FILE, encoding="utf-8"))
    except OSError:
        pass  # Kein Schreibzugriff – nur Console

    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format=fmt,
        handlers=handlers,
    )
    # Externe Libraries ruhig stellen
    for lib in ("httpx", "httpcore", "multipart"):
        logging.getLogger(lib).setLevel(logging.WARNING)
