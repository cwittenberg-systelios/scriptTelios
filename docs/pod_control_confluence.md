# Pod-Steuerung über Confluence

Serverstatus einsehen, Pod starten/stoppen, No-GPU behandeln und ein
Statusprotokoll führen — direkt aus Confluence. Architektur: **Cloudflare-Worker =
Logik** (hält Secrets, spricht RunPod, führt Cron + Protokoll), **Confluence =
reiner UI-Layer** (HTML/JS-Snippet, ruft nur die Worker-API). Keine
Funktionsduplizierung.

```
Confluence HTML-Macro  ──HMAC-signedFetch──▶  Cloudflare-Worker  ──GraphQL──▶  RunPod
(confluence-pod-macro.html)                   (backend/scripts/cloudflareworker.js)
```

---

## 1. Cloudflare-Worker (Proxy)

Datei: `backend/scripts/cloudflareworker.js`.

**Bindings / Env**

| Name | Typ | Zweck |
|------|-----|-------|
| `RUNPOD_API_KEY` | Secret | RunPod-API-Key |
| `RUNPOD_POD_ID` | Secret | Seed/Fallback für die Pod-ID (danach mutabel in KV) |
| `CONFLUENCE_SHARED_SECRET` | Secret | HMAC-Shared-Secret (identisch mit Backend & Snippet) |
| `CONFLUENCE_ORIGIN` | Var | erlaubte CORS-Origin(s), kommagetrennt, z.B. `https://confluence.systelios.de` |
| `LOGS` | KV-Namespace | Statusprotokoll **und** mutable Pod-ID |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Secret (optional) | Benachrichtigungen |

**KV-Keys im `LOGS`-Namespace**
- `state:podId` — aktuell gesetzte Pod-ID (mutabel, via `/setPodId`). Kein zweiter
  Namespace nötig.
- `state:selfcheck` — letzter Self-Check-Report als JSON (vom `*/15`-Cron gesetzt).
- `state:selfcheckStatus` — letzter Self-Check-Gesamtstatus (`ok`/`degraded`/`down`/
  `stopped`) zur Telegram-Entprellung (Alarm nur bei Statuswechsel).
- `<epoch_ms>` — je ein JSON-Protokolleintrag (`{time, user, action, status, …}`).

**Deploy**
1. KV-Namespace anlegen und als `LOGS` binden (`wrangler.toml`).
2. Secrets setzen (`wrangler secret put RUNPOD_API_KEY` usw.), inkl.
   `CONFLUENCE_SHARED_SECRET`.
3. `CONFLUENCE_ORIGIN` als Var setzen (die Confluence-Basis-URL).
4. Deployen. Cron läuft weiter: Auto-Start `0 7 * * 1-5`, Auto-Stop `0 18 * * 1-5`
   (UTC). Cron ruft die Logik intern (User `cron`), ohne Auth.
5. **Neu (Self-Check):** Cron-Trigger `*/15 * * * *` in der Worker-Konfiguration
   (`wrangler.toml` unter `[triggers] crons`) ergänzen. Er prüft alle 15 min den
   Pod-Self-Check und meldet **nur bei Statuswechsel** per Telegram; bei gestopptem
   Pod passiert nichts (kein Fehlalarm). Setzt `state:selfcheck` / `state:selfcheckStatus`.

**Endpunkte** (alle außer `/` erfordern HMAC-Auth)

| Methode | Pfad | Zweck |
|---------|------|-------|
| GET | `/` | Ping (ohne Auth) |
| GET | `/state` | `{podId, desiredStatus, hasGpu, trackedRunning, anyRunning, otherRunning[]}` |
| GET | `/pods` | alle Account-Pods (Doppelstart-Erkennung) |
| GET | `/selfcheck` | Live-Self-Check vom Pod (`{status, checks{…}}`); `status:"stopped"` wenn Pod aus |
| POST | `/start` | Pod starten (mit Doppelstart-Schutz; Antwort enthält `noGpu`) |
| POST | `/stop` | Pod stoppen |
| POST | `/setPodId` | `{podId}` → mutable Pod-ID setzen |
| POST | `/recover` | `{podId?}` → optionaler ID-Swap + Resume |
| GET | `/logs` | Statusprotokoll (neueste zuerst, inkl. `user`) |
| GET | `/debug` | Auth-/Env-Diagnose (nur Metadaten) |
| POST | `/testrun` | Backend-Testlauf triggern |

**Auth (HMAC-SHA256):** Header `X-Systelios-User` / `X-Systelios-Timestamp` /
`X-Systelios-Signature`; `sig = HMAC-SHA256(CONFLUENCE_SHARED_SECRET, "<user>:<ts>")`
hex; Zeitfenster ±300 s. Identisch zu `backend/app/core/auth.py`.

---

## 2. Confluence HTML-Snippet

Datei: `confluence-pod-macro.html` (Snippet für das **HTML-Macro**, *kein* User-Macro).

**Installation**
1. Seite bearbeiten → Macro einfügen → **HTML** (muss vom Admin aktiviert sein).
2. Kompletten Dateiinhalt in den Macro-Body einfügen.
3. Im Skript-Block oben die zwei Konstanten setzen:
   - `PROXY_URL` = Worker-URL (z.B. `https://xxx.workers.dev`)
   - `AUTH_SECRET` = identisch mit `CONFLUENCE_SHARED_SECRET`
4. Seite speichern. `CONFLUENCE_ORIGIN` im Worker muss die Seiten-Origin erlauben.

**Bedienung**
- **Ampel:** grün „Server läuft" (getrackter Pod), gelb „Anderer Pod läuft"
  (Fremd-Pod), grau „gestoppt".
- **Start/Stop.** Start bricht ab, wenn bereits ein (auch fremder) Pod läuft.
- **Fremd-Pod übernehmen:** „Übernehmen"-Button im gelben Banner → der Worker trackt
  danach den laufenden Pod.
- **Pod-ID setzen / migrieren:** Feld + „Setzen" (nur ID) bzw. „Migrieren & Starten"
  (ID + Resume).
- **No-GPU:** rotes Banner mit „Erneut versuchen"; alternativ Ersatz-Pod-ID eintragen
  und „Migrieren & Starten". Zwei weitere Optionen:
  - „**Automatisch wiederholen**": startet eine manuell angestoßene Retry-Schleife auf
    **demselben** Pod (alle 90 s ein neuer `podResume`-Versuch), bis eine GPU frei wird;
    jederzeit über „Automatik stoppen" abbrechbar. Läuft nur im offenen Tab.
  - „**Gestoppte Pods durchprobieren**": probiert der Reihe nach alle gestoppten
    Account-Pods durch (via `/recover` + `/state`), übernimmt den ersten mit GPU;
    bekommt keiner eine GPU → Hinweis „manuell eingreifen".
- **Systemprüfung:** Ampeln je Subsystem (Ollama, Modelle, Datenbank, Speicher, GPU,
  Whisper) plus Gesamtstatus, gelesen aus `/selfcheck`. Zustände: **OK** (grün),
  **eingeschränkt** (gelb), **Startup** (blau — Pod läuft, Backend fährt noch hoch, im
  4-min-Fenster nach Start), **Störung** (rot), **Pod aus** (grau). Poll alle 60 s,
  während „Startup" alle 15 s (schneller Wechsel auf grün). Kein Telegram-Alarm bei
  „Startup"/„Pod aus".
- **Statusprotokoll:** wer/wann/welche Aktion, gelesen aus `/logs` (Single Source =
  Worker-KV).
- Poll alle 15 s; bei Proxy-Down Backoff bis 60 s; Pause bei verstecktem Tab.

---

## 3. Sicherheit

Das `AUTH_SECRET` steht im Seiten-HTML → jede/r mit Lese-/Quelltext-Zugriff auf die
Seite kann es lesen. Das Secret erlaubt **nur Pod-Start/-Stop**, keinen Datenzugriff.
Konsequenzen:
- Zugriffsschutz = **Confluence-Seitenberechtigungen**. Seite (und Space) auf die
  berechtigten Personen beschränken.
- Bei breiter Freigabe das Secret **rotieren** (im Worker + im Snippet + im Backend
  gleichzeitig ändern).
- HMAC braucht einen **HTTPS-Kontext** (`crypto.subtle`); Confluence über HTTPS
  aufrufen.

---

## 4. Rollback

Der Worker behält die bisherige Funktionalität (Cron, Telegram, Health-Poll). Um zur
reinen Automatik zurückzukehren:
- Snippet-Macro von der Seite entfernen — der Worker läuft mit Cron unverändert weiter.
- Für einen vollständigen Rückbau der v20-Änderungen den Worker aus dem Git-Stand vor
  v20 wiederherstellen (`git checkout <pre-v20> -- backend/scripts/cloudflareworker.js`).

---

## 5. Troubleshooting

| Symptom | Ursache / Lösung |
|---------|------------------|
| „HMAC nicht möglich: crypto.subtle fehlt" | Confluence über **HTTPS** aufrufen. |
| „PROXY_URL/AUTH_SECRET noch nicht eingetragen" | Konstanten im Snippet setzen. |
| 401 / „Auth-Fehler" | `AUTH_SECRET` ≠ `CONFLUENCE_SHARED_SECRET`, oder Uhr-Zeit driftet (>300 s). |
| CORS-Fehler im Browser | `CONFLUENCE_ORIGIN` im Worker auf die Seiten-Origin setzen. |
| „Proxy nicht erreichbar" | Worker-URL falsch oder Worker down; UI backoff-t automatisch. |
| Start erzeugt keinen 2. Pod, meldet „anderer Pod läuft" | Erwartetes Verhalten (Doppelstart-Schutz). Ggf. „Übernehmen". |
| Rotes No-GPU-Banner | RunPod hat keine GPU zugewiesen; „Erneut versuchen" oder Ersatz-Pod-ID migrieren. |
| Systemprüfung zeigt „Pod aus", obwohl Pod läuft | Pod-ID im Worker (`state:podId`) zeigt auf den falschen Pod → „Übernehmen"/„Setzen". |
| Systemprüfung „Modelle" rot | Pflichtmodell fehlt in Ollama (`ollama pull …`). Kein Auto-Pull auf dem Pod (Disk-Schutz). |
| Kein Telegram bei Störung | Nur bei **Statuswechsel** wird gemeldet; `TELEGRAM_BOT_TOKEN`/`_CHAT_ID` gesetzt? |

---

## 6. Backend `/api/selfcheck`

Neuer FastAPI-Endpunkt (`backend/app/api/selfcheck.py`, in `main.py` unter `/api`
registriert). Prüft und aggregiert (Ergebnis 20 s gecacht):

| Subsystem | Prüfung | Bewertung |
|-----------|---------|-----------|
| `ollama` | `GET {OLLAMA_HOST}/api/tags` erreichbar | fehlt → **down** |
| `models` | Pflichtmodelle (`OLLAMA_MODEL`, `SUMMARY_MODEL`, alle `WORKFLOW_MODEL`, `EMBEDDING_MODEL`) installiert | fehlt → **degraded** (`missing[]`) |
| `db` | `SELECT 1` über `engine` (Timeout 3 s) | fehlt → **down** |
| `disk` | freier Platz in `/workspace` ≥ 10 GB | wenig → **degraded** |
| `gpu` | `nvidia-smi` listet GPU + freien VRAM | fehlt → **down** |
| `whisper` | konfiguriertes Modell/Device (nur Info) | — |

Gesamtstatus: `down`, wenn Ollama/DB/GPU fehlen; sonst `degraded` bei fehlenden
Modellen oder wenig Platz; sonst `ok`. Modell-Match: mit Tag exakt (`gemma4:31b`),
ohne Tag tag-agnostisch (`mistral-small3.2` ~ `…:latest`).

Tests: `backend/tests/unit/test_selfcheck.py` (Matching + Aggregation + gemockte Probes).

---

## v19.21 — Pod-Lifecycle v2: Terminate & Redeploy

### Warum
Ein **gestoppter** Pod bleibt an seinen Host gebunden; beim Resume muss genau dessen
GPU frei sein („no GPU available"). Bisher hieß das: neuen Pod von Hand anlegen,
ID über `/setPodId` eintragen. Ab v19.21 **terminiert** jeder Stopp den Pod und jeder
Start legt per `podFindAndDeployOnDemand` einen neuen an — RunPod sucht dabei
RZ-weit nach einer freien RTX PRO 4500 (kein Fallback auf andere GPU-Typen).
Alles Zustandsbehaftete liegt auf dem Network Volume (`/workspace`: Modelle,
`.env` mit Tunnel-Token, venv, HF-Cache); `runpod-start.sh` ist auf ephemere
Container-Disk ausgelegt. Der Cloudflare-Tunnel zeigt nach dem Boot automatisch
auf den neuen Pod.

### Aktivierung (einmalig, bei LAUFENDEM Pod)
1. Worker deployen (`misc/cloudflareworker.js`), Makro aktualisieren.
2. Optional zuerst Env `DRY_RUN=1` setzen: Terminate/Deploy werden nur
   protokolliert; `/start`/`/stop` liefern das geplante GraphQL-Payload.
3. Confluence-Makro → **„Spec aus laufendem Pod übernehmen"** (`POST /spec/capture`).
   Der Worker liest Image, Docker-Args, Ports, Container-Disk, Volume, Datacenter,
   GPU-Typ, CPU/RAM aus dem laufenden Pod und speichert sie als `state:podSpec`.
   Secrets werden **nicht** ins KV übernommen (Keys mit TOKEN/SECRET/KEY/PASS…,
   RunPod-Auto-Variablen); die Env kommt beim Boot aus `/workspace/.env`.
4. „Spec anzeigen" prüfen: `imageName`, `networkVolumeId`, `gpuTypeId`,
   `dataCenterId` müssen gesetzt sein. Fehlt etwas → `POST /spec/set {spec:{…}}`.
5. `DRY_RUN` entfernen. Ab jetzt: Lifecycle-Panel zeigt „Terminate & Redeploy".

**Rollback:** „Spec löschen" (`POST /spec/clear`) → Worker verhält sich wieder wie
v19.10 (Stop/Resume). Der `/recover`-Pfad mit manuellem ID-Swap bleibt erhalten.

### Endpunkte
| Endpoint | Zweck |
|---|---|
| `GET /spec` | gespeicherte Spec anzeigen (`invalidRaw` = KV-Inhalt unparsebar) |
| `POST /spec/capture` | Spec aus laufendem Pod lesen und speichern |
| `POST /spec/set` | `{ spec: {...} }` — Felder ergänzen/korrigieren (Merge) |
| `POST /spec/clear` | Spec löschen → Resume-Modus |
| `POST /ensure` | idempotenter Start (siehe unten) |
| `GET /state` | enthält jetzt `lifecycle: {mode, dryRun, gpuTypeId, specCapturedAt, wantRunning, noServerSince}` |

### `/ensure` — Start-on-Intent
Antworten: `ok` (läuft), `starting` (Deploy ausgelöst oder Boot < 5 min; bei
`reason: "no_gpu"` läuft das Retry-Fenster), `no_server` (10 min lang keine GPU
frei; `retry_allowed: true`), `blocked_night` (23–05 Uhr), `error`.
Aufrufer: Confluence-Makro (Start-Button im Redeploy-Modus, „Erneut versuchen"),
scriptTelios-App (`startJob()` bei Generieren, P0-Upload bei Fehlschlag) und der
15-min-Cron (führt ein offenes Retry-Fenster fort, falls niemand mehr pollt).

Retry-Fenster: `state:wantRunning = {since, attempts, lastAttempt}`, höchstens ein
Deploy-Versuch pro Minute, nach 10 Minuten `state:noServerSince` + Telegram.
Ein erneuter `/ensure` nach `no_server` öffnet ein frisches Fenster.

### App-Seite (Confluence-User-Makro)
Neuer Makro-Parameter **Pod-Proxy URL** (`proxyUrl`) → `window.SYSTELIOS_PROXY_BASE`.
Ohne den Parameter verhält sich die App wie bisher (kein Auto-Start). Mit Parameter:
- Sidebar-Statusbox: grau „Kein Server aktiv — wird beim ersten Auftrag automatisch
  gestartet", blau „Server startet …", rot „Kein Server verfügbar" + Button,
  rot „Keine sichere Verbindung — bitte über https aufrufen" (Seite per http geladen;
  Ursache: CORS-Allowlist des Workers kennt nur den https-Origin).
- Generieren bei gestopptem Pod: der Auftrag wartet in der App (Formular + Dateien
  im Speicher) und wird beim `st-health-ok` automatisch abgeschickt. **Seite bis
  dahin nicht neu laden** (Dateien wären weg).
- Aufnahme-Upload bei gestopptem Pod: Offline-Queue (IndexedDB) wie bisher, plus
  Server-Anstoß; Upload folgt automatisch.

### KV-Keys (neu)
`state:podSpec`, `state:wantRunning`, `state:noServerSince`. `state:podId` zeigt
nach einem Deploy auf den neuen Pod; nach Terminate bleibt die alte ID stehen
(RunPod liefert dafür `NO_STATUS`, der Worker wertet das als „gestoppt").

### Kosten-Guard
Vor jedem Deploy prüft `/pods`, dass kein anderer Pod läuft (`OTHER_RUNNING` →
kein Deploy). Nach jedem Terminate wird die alte ID nicht mehr resümiert.
