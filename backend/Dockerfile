# ════════════════════════════════════════════════════════════════
#  sysTelios KI-Dokumentation – Backend Dockerfile
#  Basis: Python 3.12 slim
# ════════════════════════════════════════════════════════════════

FROM python:3.12-slim

# System-Abhaengigkeiten:
#   tesseract-ocr   – OCR fuer gescannte PDFs
#   tesseract-ocr-deu – deutsches Sprachpaket
#   poppler-utils   – pdf2image (PDF → Bild)
#   libmagic1       – Dateityp-Erkennung
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-deu \
        poppler-utils \
        libmagic1 \
        ffmpeg \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Abhaengigkeiten zuerst (Docker Cache nutzen)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Anwendungscode
COPY . .

# Verzeichnisse fuer Uploads und Outputs anlegen
RUN mkdir -p uploads outputs

# Whisper-Modell vorab herunterladen (optional – spart Zeit beim ersten Start)
# RUN python -c "from faster_whisper import WhisperModel; WhisperModel('medium', device='cpu')"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
