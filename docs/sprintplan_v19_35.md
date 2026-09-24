# Sprintplan v19.35 – Server-Vorlesen zum Testen: Browser | Piper | Chatterbox

Basis: 4230a30 + v19.34. Ziel: Die drei Stimmen im echten Dialog vergleichen
und dann entscheiden. Entscheidungen (24.09.): Umschalter für **alle**
Nutzer, Chatterbox **auf der CPU** (kein Risiko für gemma im VRAM).

Architektur: TTS läuft als **eigener kleiner Dienst** in einem **eigenen venv**
(`/workspace/venv-tts`, Port 8011, nur localhost). Das hat zwei Gründe.
`piper-tts` bringt `onnxruntime` mit, `chatterbox-tts` pinnt `torch==2.6.0`.
Beides darf das Backend-venv (pyannote, faster-whisper) nicht verbiegen.
Das Backend reicht die Anfragen nur durch (Auth, Protokoll).

| # | Schritt | Test |
|---|---|---|
| S1 | `backend/tts_service/tts_server.py`: nur Standardbibliothek plus die Engines. `GET /engines`, `POST /synthesize {text, engine}` → WAV. Engines werden erst beim ersten Aufruf geladen, je Engine ein Lock, CPU-Threads begrenzt (`TTS_PIPER_THREADS` 2, `TTS_CHATTERBOX_THREADS` 8), kleiner LRU-Cache für wiederkehrende Sätze. Chatterbox nur mit `TTS_CHATTERBOX_ENABLED=true`. | Unit mit Fake-Engine |
| S2 | `backend/scripts/setup_tts.sh [--chatterbox]`: legt das venv an, installiert piper-tts und die Stimme `de_DE-thorsten-high`, optional torch-CPU 2.6 und chatterbox-tts. `runpod-start.sh` startet den Dienst, wenn `TTS_ENABLED=true` und das venv existiert. | bash -n |
| S3 | Backend: `GET /interview/tts/engines` (Browser immer, dazu die Server-Engines mit `available`/Grund) und `POST /interview/tts` (Proxy, Text max. 600 Zeichen). perf-Zeile `kind: interview_tts` (Engine, Zeichen, Synthesezeit, Audiolänge, **kein Text**). `perf_report` zeigt die TTS-Zeiten je Engine. | Unit mit gemocktem Dienst |
| S4 | `speech.js`: Server-Anbieter mit gleicher Schnittstelle (`say`/`sayStream`/`cancel`). Pro Satz ein Abruf, Satz n+1 wird schon geholt, während Satz n läuft. Wiedergabe über `<audio>`, `cancel` bricht Abrufe und Wiedergabe ab. Umschalter-Anbieter delegiert je nach gewählter Engine. Fällt der Server aus, wird Browser genutzt, mit Hinweis. | Jest |
| S5 | UI: Auswahl „Stimme: Browser / Piper / Chatterbox“ neben „Vorlesen“ in Dialog und Fragenkarten (localStorage `st_tts_engine`). Nicht verfügbare Engines sind ausgegraut mit Grund. | Jest |
| S6 | CHANGELOG, `.env.example`, Bundle | alle Gates |

Einrichtung auf dem Pod (einmalig): `bash backend/scripts/setup_tts.sh --chatterbox`,
danach `TTS_ENABLED=true` und `TTS_CHATTERBOX_ENABLED=true` in `/workspace/.env`
eintragen und das Backend neu starten.
