# Sprintplan v19.33 – Dialog-Latenz: Messung, feste Kontextgröße, Vorladen, GPU-Profil

Basis: `v19_QA_v02` @ 75d54bd **+ v19.32 + commit_patch.sh** (Reihenfolge beim Einspielen).
Anlass: Live-Test 24.09. Die erste Transkription dauerte Minuten, die ersten zwei
Dialog-Antworten waren sehr langsam, danach ging es schnell. Das Ollama-Log
bestätigt: (1) mistral war vorgeladen und musste für gemma raus, (2) gemma wurde
mit `num_ctx=2048` geladen und bei der nächsten Anfrage mit anderem `num_ctx`
neu geladen.

Weitere Befunde aus dem Code, die in dieselbe Richtung gehen:
- `_ollama_warmup()` nach jeder Aufnahme-Transkription und
  `_wait_for_ollama_ready()` vor jeder P0-Transkription pingen
  `OLLAMA_MODEL` (mistral). Liegt gerade gemma für den Dialog im Speicher,
  wird es dadurch verdrängt.
- Die Browser-Stimmen „Google …“ und „… Online (Natural)“ rechnen in der
  Cloud. `pickGermanVoice()` hat sie bevorzugt, damit gingen vorgelesene
  Texte an Google bzw. Microsoft.

| # | Schritt | Test |
|---|---|---|
| S1 | **Messung.** `llm_chat` liefert `perf` (Zeit bis zum ersten Wort, Ladezeit und Prompt-/Generierungszeit laut Ollama, `num_ctx`). Das Diktat liefert `perf` (Audio-Länge, Whisper-Ladezeit, Transkription). Pro Schritt eine JSON-Zeile in `performance.log` (`kind: interview_chat / interview_transcribe`) inkl. laufender Jobs. Das Frontend schickt seine Rundlaufzeiten als `client_perf` mit. `perf_report.py` bekommt einen Abschnitt „Interview“ mit Latenzen und **Nutzung (Dialoge pro Woche)**, das ist der Indikator für den Umstieg auf 2 GPUs. | Unit: perf-Felder, Log-Zeile, Report-Abschnitt |
| S2 | **Feste Kontextgröße** (`LLM_FIXED_CTX`, Default aus). Ist sie an, nutzt jeder LLM-Call `num_ctx = LLM_NUM_CTX_CAP` (Jobs, Verdichtung, Dialog, Warmups). Der OOM-Rückfall auf 8192 bleibt. | Unit: an/aus für `_estimate_num_ctx` und den Chat |
| S3 | **Warmups verdrängen nichts mehr.** Neu: `llm.ollama_loaded_models()` (`/api/ps`). `_ollama_warmup()` und `_wait_for_ollama_ready()` pingen das bereits geladene Modell und laden `OLLAMA_MODEL` nur, wenn keins geladen ist. Beides mit fester Kontextgröße, falls aktiv. | Unit mit gemocktem Ollama |
| S4 | **Vorladen beim Öffnen des Interviews.** `POST /interview/warmup` lädt im Hintergrund Whisper und das Dialog-Modell (gemma, feste Kontextgröße). Das Frontend ruft den Endpoint aus `warmupInterviewServer()` auf, das gilt für beide Interview-Formen. | Unit: Endpoint startet beides und blockiert nicht |
| S5 | **Schalter `GPU_PROFILE=single\|dual`.** `runpod-start.sh`: single → `OLLAMA_MAX_LOADED_MODELS=1`, dual → 3 (gemma, mistral, nomic). Stehen weniger als 2 GPUs zur Verfügung, wird mit Warnung auf single zurückgestellt. Backend bei dual: beim Start alle Routing-Modelle plus Whisper vorladen, Whisper bleibt nach Transkriptionen geladen. `OLLAMA_NUM_PARALLEL` bleibt 1 und ist separat per `.env` änderbar. | Unit: Profil-Logik, Skript-Syntax |
| S6 | **Browser-Vorlesen.** Nur Stimmen, die im Rechner selbst laufen (`localService`), keine Cloud-Stimmen. Vor dem ersten Satz nach einer Pause läuft eine kurze Stille über WebAudio, damit das erste Wort nicht verschluckt wird. Ohne lokale deutsche Stimme erscheint ein Hinweis statt Cloud-Ausgabe. | Jest |
| S7 | Eval-Skript zeigt Zeit bis zum ersten Wort und Ladezeit, dazu CHANGELOG, `.env.example` und Bundle. | pytest, Jest, ESLint, ruff, Build |

Abnahme auf dem Pod (nach dem Einspielen, `LLM_FIXED_CTX=true` in `/workspace/.env`):
`ollama ps` zeigt „100% GPU“, im Eval ist `load_s` ab dem zweiten Turn ≈ 0, in
`perf_report.py --last 24h` hat der Abschnitt Interview Werte.

Nicht in diesem Sprint: Vorrang des Dialogs vor Jobs (nach der Messung
entscheiden), Server-TTS (erst Hörtest), `NUM_PARALLEL=2` (erst mit 2 GPUs).
