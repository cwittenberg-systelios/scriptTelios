"""
sysTelios KI-Dokumentation – FastAPI Backend
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import transcribe, generate, documents, health
from app.core.config import settings
from app.core.logging import setup_logging

setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("sysTelios Backend startet (Modell: %s)", settings.LLM_MODEL)
    yield
    logger.info("sysTelios Backend beendet")


app = FastAPI(
    title="sysTelios KI-Dokumentation",
    description="Backend fuer KI-gestuetzte klinische Dokumentation",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router,     prefix="/api",      tags=["Health"])
app.include_router(transcribe.router, prefix="/api",      tags=["Transkription"])
app.include_router(generate.router,   prefix="/api",      tags=["Generierung"])
app.include_router(documents.router,  prefix="/api",      tags=["Dokumente"])
