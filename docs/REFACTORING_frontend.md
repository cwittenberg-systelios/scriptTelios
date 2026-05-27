# Frontend-Refactoring-Plan: `klinische-dokumentation.jsx`

**Stand:** 2026-05-27
**Aktuelle Größe:** 5259 Zeilen, 66 Top-Level-Declarations, 1 Datei

Ziel: Codebase übersichtlicher, Duplikation reduzieren, Boden für Multi-Job-Rollout auf P2–P4 bereiten. Stabilität geht vor Geschwindigkeit – jede Phase einzeln testbar.

---

## Befunde

### A. Echter toter Code (~95 Zeilen)

| Item | Zeile(n) | Status |
|---|---|---|
| `idbLoadAudio()` | 970–983 | Definiert, **nie aufgerufen**. Audio-IDB ist halbgebaut: speichern beim Aufnehmen, aber kein Restore-Pfad. |
| `idbSaveAudio()` + `idbClearAudio()` + Audio-Branch in `idbOpen()` | 940–993 | Wird nur gerufen, lebt aber wirkungslos (siehe oben). Komplette Persistierung entscheiden oder rauswerfen. |
| `const DOKUMENTTYPEN = DOKUMENTTYPEN_FALLBACK` | 4636 | Aliasing-Constant – das aliasierte Symbol wird nirgends gelesen. |
| `_FALLBACK`-Suffix-Duplette | 4625, 4643 | Wird beibehalten als Initial-State von `useWorkflowManifest()`, das ist OK. Aber das Aliasing in 4636 ist redundant. |

### B. Massive Duplikation (~600 Zeilen Potenzial)

**1) `generate()` vs `startJob()`** – ~40 Zeilen identische FormData-Bauerei (2215–2280 ↔ 2284–2330).
Unterschied: Antwortbehandlung (Polling+Parsing bei `generate`, sofortige Rückgabe bei `startJob`).

**2) P2 / P2b / P3 / P3b / P4 Boilerplate** – fünfmal ~85 Zeilen identisches Muster:
- useState-Block (`out`, `outWarn`, `job`, `lastJobId`, `busy`, `currentJobId`, `abortRef`, `geschlecht`, `kuerzel`)
- Resume-`useEffect` (gleiche Struktur, anderer `page`-String)
- `cancelRun()` (identisch, modulo State-Namen)
- `run()`-Vorlauf (AbortController, `geschlechtHinweis`, `patientNameExplicit`)

**3) Patient-Kürzel + Geschlecht-Logik** – 5× nahezu wortgleich kopiert:
```js
const nameHinweis = kuerzel.trim() ? ` Verwende als Namenskürzel ...` : "";
const geschlechtHinweis = { "w": ..., "m": ..., "auto": ... }[geschlecht];
let patientNameExplicit = null;
if (kuerzel.trim()) { if (geschlecht === "w") ... else if (geschlecht === "m") ... else ... }
```

**4) Action-Bar JSX (♀/♂/Auto-Toggle + Kürzel-Input)** – 5× mit gleichen Inline-Styles und Logik.

### C. Organisation

5259 Zeilen mischen sieben Concern-Layer:
- API-Helper (`apiFetch`, `getApiBase`, `generate`, `startJob`, `pollJob`, `repair*`, `downloadViaApi`)
- Hooks (`useJobResult`, `useQualityCheck`, `useWorkflowManifest`)
- UI-Bausteine (`Card`, `Dropzone`, `Output`, `Tags`, `LabelEdit`, `RepairBundle`, `QualityCheckPanel`, …)
- Page-Komponenten (`P0–P5`)
- Prompt-Konstanten (`P_DOKU`, `P_ANAMNESE`, `P_BEFUND_VORLAGE`, …)
- 707-Zeilen-CSS (Template-Literal `const S`)
- Confluence-Glue (`getConfluenceUser`, `getApiBase`)

Format-Helper sind zerstreut (`fmtSec`/`fmtMB` bei 923, `_fmtTime` bei 2798). Nichts ist falsch – nur unübersichtlich.

---

## Drei-Phasen-Vorschlag

### Phase 1 – Dead-Code-Purge

**Risiko:** minimal. Keine UI-Pfade betroffen.
**Win:** ~30–95 Zeilen weniger.
**Aufwand:** 15 Min.

**Konkrete Schritte:**
1. `idbLoadAudio()` entfernen (Zeile 970–983)
2. `const DOKUMENTTYPEN = DOKUMENTTYPEN_FALLBACK` und Kommentar entfernen (Zeile 4634–4636)
3. **Entscheidung treffen**: Audio-IDB komplett raus oder Restore-Pfad einbauen?
   - Bei **raus**: zusätzlich `idbSaveAudio`, `idbClearAudio`, alle Audio-`AUDIO_IDB_*`-Konstanten, Audio-Branch in `idbOpen` entfernen. Aufruferseiten (`AudioRecorder.onstop`, `AudioInput.handleFile`, `P1.onJobStarted`) anpassen. → ~80 Zeilen weniger.
   - Bei **Restore einbauen**: `idbLoadAudio` in `AudioInput.useEffect` aufrufen + an `onFile` weiterreichen. → Audio überlebt Reload (UX-Win).

**Akzeptanz:** alle existierenden Funktionen unverändert. Esbuild-Parse grün. Smoke-Test: P0–P5 öffnen, Audio aufnehmen in P0, P1-Generierung anstoßen.

**Reihenfolge:** kann sofort losgehen, unabhängig von allem anderen.

---

### Phase 2 – Helper-Extraktion (Single-File bleibt)

**Risiko:** gering. Keine Architektur-Änderung, keine Build-Änderung.
**Win:** ~600 Zeilen weniger. Pro P-Komponente: 200–300 → 120–180 Zeilen.
**Aufwand:** ~1 Tag pro Komponente, sequentiell.

**Neue Bausteine (alle in derselben Datei):**

```js
// Pure Functions
buildJobFormData(workflow, prompt, content, files)
  → ersetzt FormData-Bau in generate() UND startJob()

buildPatientNameExplicit(geschlecht, kuerzel)
  → "Frau M." | "Herr S." | "M." | null

buildGenderHint(geschlecht, kuerzel)
  → Suffix-String für den Prompt
```

```jsx
// React-Bausteine
<ClientControls
  geschlecht={...} setGeschlecht={...}
  kuerzel={...} setKuerzel={...}
/>
  → ersetzt den ♀/♂/Auto-Toggle + Kürzel-Input-Block in P1–P4

useResumeJob(page, { onJob, onError, onResumed })
  → ersetzt das Resume-useEffect in P2/P2b/P3/P3b/P4
  (P1 hat schon eigene Resume-Logik)

useRunController()
  → kapselt AbortController + busy-State + cancelRun()-Pattern
```

**Reihenfolge der Anwendung** (eine Komponente nach der anderen, jeweils manuell durchgetestet):

1. **`generate()` + `startJob()` auf `buildJobFormData` umstellen** – kein UI-Risiko, nur interne Refaktorisierung
2. **`<ClientControls>` extrahieren** – neue Komponente, in P1 erst einbauen, dann in P2/P2b/P3/P3b/P4 nacheinander
3. **`buildPatientNameExplicit` + `buildGenderHint`** – in jeder `run()`-Funktion einsetzen
4. **`useResumeJob`** – Pilotweise in P2 testen, dann auf P2b/P3/P3b/P4 ausrollen
5. **`useRunController`** – analog: P2 zuerst, dann Rest

**Akzeptanz:** Jeder Schritt einzeln getestet:
- Esbuild-Parse grün
- Smoke-Test der jeweiligen Page: Form ausfüllen, Generate, Output erscheint, Cancel funktioniert, Resume nach Reload greift
- Generate-Output identisch (gleicher Backend-Payload)

**Reihenfolge im Sprint-Kontext:** kann **vor** dem Multi-Job-Rollout auf P2/P3/P4 stattfinden – macht den Rollout danach trivial.

---

### Phase 3 – Multi-File-Split

**Risiko:** mittel. Build-Tooling muss mitspielen.
**Win:** Files unter 500 Zeilen pro Modul, klare Concern-Trennung, parallele Edits möglich.
**Aufwand:** ~1–2 Tage inklusive Build-Verifikation.

**Vorgeschlagene Struktur:**
```
frontend/
├── klinische-dokumentation.jsx   (App + NAVS + Routing, ~150 Zeilen)
├── styles.js                     (const S = `…`)
├── prompts.js                    (P_DOKU, P_ANAMNESE, P_VERL, …)
├── api/
│   ├── client.js                 (apiFetch, getApiBase, downloadViaApi)
│   ├── jobs.js                   (startJob, generate, pollJob, repair*, downloadTranscript)
│   └── recordings.js             (Recording-Endpoints)
├── hooks/
│   ├── useJobResult.js
│   ├── useQualityCheck.js
│   ├── useWorkflowManifest.js
│   ├── useResumeJob.js           (neu aus Phase 2)
│   └── useRunController.js       (neu aus Phase 2)
├── components/
│   ├── Card.jsx
│   ├── Output.jsx
│   ├── Dropzone.jsx
│   ├── Tags.jsx
│   ├── LabelEdit.jsx
│   ├── JobProgressBar.jsx
│   ├── RepairBundle.jsx
│   ├── QualityCheckPanel.jsx
│   ├── RepairPreviewModal.jsx
│   ├── ResultVersionsTabs.jsx
│   ├── ClientControls.jsx        (neu aus Phase 2)
│   ├── ModelSelector.jsx
│   ├── AudioRecorder.jsx
│   ├── AudioInput.jsx
│   ├── JobListPane.jsx           (Sprint B)
│   └── JobDetailPane.jsx         (Sprint B)
├── pages/
│   ├── P0.jsx                    (Aufnahmen)
│   ├── P1.jsx                    (Gesprächsdokumentation)
│   ├── P2.jsx                    (Anamnese & Befund)
│   ├── P2b.jsx                   (Akutantrag)
│   ├── P3.jsx                    (Verlängerung)
│   ├── P3b.jsx                   (Folgeverlängerung)
│   ├── P4.jsx                    (Entlassbericht)
│   └── P5.jsx                    (Stilprofil-Bibliothek)
└── utils/
    ├── format.js                 (fmtSec, fmtMB, _fmtTime)
    ├── friendlyError.js
    ├── confluence.js             (getConfluenceUser, getApiBase, useHeadStyle)
    └── idb.js                    (idbOpen, offlineQueue*)
```

**Reihenfolge:**

1. **Vite-Config prüfen.** Aktuelles `vite.config.js` checken, ob es bereits ein Bundle aus einer Entry erzeugt (vermutlich ja). Dann reicht `entry.jsx`/Confluence-Macro-Pfad unverändert.
2. **Bottom-up extrahieren.** Erst Utils (keine Cross-Refs zwischen ihnen), dann Components (refen Utils), dann Hooks (refen Components), dann Pages (refen alles).
3. **Smoke-Test nach jedem Modul.** Bundle bauen, ins Confluence-Macro laden, klicken.
4. **Letzte Datei:** `klinische-dokumentation.jsx` schrumpft auf den App-Component + NAVS + Routing.

**Akzeptanz:**
- Produktion-Build hat identische Größe ±5% (kein Tree-Shaking-Verlust)
- Alle Smoke-Tests aus Phase 1+2 grün
- Confluence-Macro lädt unverändert

**Wann:** nur sinnvoll wenn (a) Team >1 Dev wird, oder (b) File >7000 Zeilen erreicht, oder (c) Build-Performance leidet.

---

## Empfehlung – konkrete nächste Schritte

| Zeitpunkt | Was | Begründung |
|---|---|---|
| **Sofort** | Phase 1 | Risikofreie Bereinigung, klares Signal "Codebase ist gepflegt". Bei der Audio-IDB-Entscheidung kann Cars10 zwischen "weg" und "vollständig einbauen" wählen. |
| **Vor dem P2/P3/P4-Multi-Job-Rollout** | Phase 2 in der Reihenfolge oben | Ersparnis bei Sprint C kompensiert den Aufwand sofort. Jeder neue Multi-Job-P-Component startet von einer schlankeren Basis. |
| **Wenn Phase 2 stabil läuft** (~Monate) | Phase 3 | Strukturelle Verbesserung, kein direkter Funktionsgewinn. |

## Was sicher NICHT angefasst wird

- **`Card`, `Output`, `Dropzone`, `Tags`, `LabelEdit`** – funktionieren, klar abgegrenzt, kein Refactor-Bedarf
- **`RepairBundle`, `QualityCheckPanel`, `RepairPreviewModal`** – komplexe Geschäftslogik, frisch (v19 Phase C). Nicht antasten ohne Grund.
- **`useJobResult`, `useQualityCheck`** – Single-Source-of-Truth für Generate-Output-State. Kein Duplikat in Sicht.
- **`P0`** – schon eigenständig. Multi-Job-Pattern aus P1 könnte später P0 simplifizieren, aber das ist Symmetrie um der Symmetrie willen.
- **`AudioRecorder`, `AudioInput`** – 268 + 170 Zeilen, aber gut isoliert und gerade durch Phase 1 entschieden, ob die IDB-Schicht weg muss.

## Definition of Done je Phase

**Phase 1:**
- [ ] `idbLoadAudio` weg
- [ ] `DOKUMENTTYPEN`-Aliasing weg
- [ ] Audio-IDB-Entscheidung getroffen + umgesetzt
- [ ] Esbuild-Parse grün
- [ ] Smoke-Test P0+P1 manuell durch

**Phase 2 je Komponente:**
- [ ] Diff < 30 Zeilen netto (es geht runter, nicht hoch)
- [ ] Esbuild-Parse grün
- [ ] Smoke-Test: Generate, Cancel, Resume
- [ ] Optional: Backend-Payload identisch zu vorher (via DevTools Network)

**Phase 3:**
- [ ] Bundle-Größe ±5%
- [ ] Alle Smoke-Tests grün
- [ ] Confluence-Macro lädt
- [ ] Kein Modul über 500 Zeilen
