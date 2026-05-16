# Patch-Plan v19.3.0 — Input-Kompression Phase 1

**Status:** Plan, noch nicht implementiert.
**Voraussetzung:** v19.2.2 (Stage-1 Verlauf/Transkript-Summaries) deployed.
**Strategie:** "C" aus Hybrid-Ansatz — Extractive Pre-Filter + Workflow-spezifische Stage-1-Prompts. Nicht-disruptiv, ergänzt bestehende Stage-1, ersetzt sie nicht.
**Abgrenzung:**
  - v19.2.3 (Continuation-Retry) ist orthogonal — kann parallel oder vorher deployed werden
  - Phase 2 (Hierarchical Refine / Map-Reduce) und Phase 3 (RAG über pgvector) folgen separat

---

## 1. Motivation

Aktuell wird in Stage-1 ein generisches Single-Pass-Summary erzeugt: ein Prompt fuer alle Workflows. Folgen:

- **Stage-1 weiss nicht, wofuer summarisiert wird** → spaetere Stage-2-Sektion verlangt Details, die Stage-1 als "irrelevant" verworfen hat
- **Verlauf-Texte enthalten 20-30% Boilerplate** (Briefkoepfe, Anschriften, wiederkehrende Stempel, Disclaimer, Seitenzahlen, formelhafte Floskeln) — verbrauchen Tokens, tragen aber keine klinische Information
- **Vorberichte aus Voraufenthalten** sind oft noch boilerplate-lastiger (50%+ Formularkopf-Anteil bei Standardbriefen)

Hebel:
- **20-30% Token-Einsparung** allein durch Boilerplate-Entfernung (deterministisch, kein Qualitaetsrisiko)
- **Weitere ~15% Einsparung** durch fokussierte Stage-1 (workflow-spezifisch)
- Kombiniert: **35-45% Stage-1-Inputreduktion** → Stage-2 hat mehr Output-Budget innerhalb von MAX_SAFE_CTX

---

## 2. Komponenten

### 2.1 Extractive Pre-Filter (LLM-free)

Neuer Module `backend/app/services/prefilter.py`. Wird VOR Stage-1 auf rohe Eingabe-Dokumente angewendet (Verlauf, Vorbericht, Transkript-Rohtext).

#### Filter-Schichten

```python
def prefilter(text: str, doc_type: Literal["verlauf", "vorbericht", "transkript"]) -> tuple[str, dict]:
    """
    Entfernt deterministisch erkennbare Boilerplate.
    Returns (gefilterter_text, statistik_dict).

    statistik_dict:
        - chars_before, chars_after, reduction_pct
        - removed_blocks: list[dict]  # {pattern: str, count: int}
    """
```

**Layer 1: Strukturelle Boilerplate (universal):**
- Seitenzahlen / Seitenangaben: `r"^-?\s*Seite\s+\d+(\s*von\s*\d+)?\s*-?$"` zeilenweise
- Datumsangaben am Zeilenrand (Headers/Footers): `r"^\d{1,2}\.\d{1,2}\.\d{2,4}\s*$"`
- Stempelreste / Wasserzeichen: `r"^\s*\*+\s*VERTRAULICH\s*\*+\s*$"`
- Confluence-Artefakte: `r"^\[image\d*\]$"`, `r"^\{.*?\}$"`
- Mehrfache Leerzeilen: `r"\n{3,}"` → `"\n\n"`

**Layer 2: Briefkopf-Block (`doc_type` in `("vorbericht", "entlassbericht_extern")`):**
- Erkennung: erste 30 Zeilen mit Klinik-Namen-Patterns (`r"(klinik|krankenhaus|hospital|reha|zentrum)"i`)
- Heuristik: Block zwischen Beginn und erstem klinischen Schlagwort (`"diagnose"`, `"aufnahme"`, `"anamnese"`, `"befund"`, `"verlauf"`) verwerfen
- Sicherheits-Cap: max 600 Zeichen ueberspringen (sonst riskant)

**Layer 3: Disclaimer/Footer (universal):**
- Datenschutz-Floskeln: `r"^\s*(Diese Information|Dieser Bericht).{20,400}\s*$"`m
- Signaturen: `r"^(Mit freundlichen Gruessen|Hochachtungsvoll|i\.\s*A\.|i\.\s*V\.).*?(\n\n|$)"`s
- Standardisierte Schlussformeln (TBD: Klinik-spezifische Patterns einsammeln)

**Layer 4 (optional, Phase 1b):** TextRank/BM25-Selection — auskommentiert lassen, erst aktivieren wenn Phase 1a evaluiert ist.

#### Integration

In `extraction.py`, nach dem Auslesen von Verlauf/Vorbericht/Transkript, **vor** der Uebergabe an Stage-1:

```python
verlauf_raw = extract_verlauf_from_confluence(...)
verlauf_clean, stats = prefilter(verlauf_raw, doc_type="verlauf")
logger.info("Prefilter: %d → %d chars (-%.1f%%)", stats["chars_before"], stats["chars_after"], stats["reduction_pct"])

# Telemetrie ergaenzen
job_telemetry["prefilter_verlauf_reduction"] = stats["reduction_pct"]
job_telemetry["prefilter_verlauf_blocks_removed"] = sum(b["count"] for b in stats["removed_blocks"])
```

#### Sicherheits-Garantien

- **Niemals klinische Schluesselwoerter entfernen.** Whitelist-Check vor jedem Layer-2/3-Cut: Wenn der zu entfernende Block irgendeines der Tokens aus `KLINISCHES_GLOSSAR` enthaelt → Cut abbrechen.
- **Max-Reduktion-Limit:** Wenn Filter mehr als 60% des Texts entfernen wuerde → Rollback, log Warning, sende ungefilterten Text an Stage-1. Schutz vor Pattern-Misfire bei untypischen Inputs.
- **Unit-Tests** mit synthetischen Boilerplate-Beispielen (mind. 15 Faelle).

### 2.2 Workflow-spezifische Stage-1 Prompts

Aktuell: Eine generische Stage-1-Funktion `summarize_verlauf()` in `verlauf_summary.py`.

Neu: Eine Funktion pro Workflow, oder eine Funktion mit `workflow`-Parameter, die intern den passenden Prompt waehlt.

```python
def summarize_verlauf_for_workflow(
    verlauf_text: str,
    workflow: WorkflowLiteral,
) -> str:
    """Workflow-aware Verlauf-Summary."""
    prompt = STAGE1_PROMPTS[workflow]
    return generate_stage1(prompt=prompt, content=verlauf_text)
```

`STAGE1_PROMPTS` ist ein Dict, eine Vorlage pro Workflow. Siehe Abschnitt 3 fuer die zu spezifizierenden Inhalte.

---

## 3. Workflow-spezifische Stage-1 Prompts — Vorlagen + offene Fragen

Die folgenden Skeletts sind als **Diskussionsgrundlage** gedacht. Jeder Prompt hat den gleichen Aufbau:

```
[ZIEL] Was Stage-2 spaeter aus dem Summary brauchen wird
[FOKUS-FELDER] Welche Inhalte priorisiert extrahieren
[IGNORE-FELDER] Was bewusst weglassen
[LAENGE] Soll-Wortzahl Summary
[FORMAT] Strukturierter Text / Stichpunkte / Fliesstext
```

### 3.1 `dokumentation` (P1) — Gespraechsdokumentation

```
[ZIEL] Stage-2 erstellt eine knappe Dokumentation der EINZELNEN Sitzung. 
       Stage-1 destilliert aus Transkript/Notizen die Kerninhalte.

[FOKUS-FELDER]
  - Hauptthema der Sitzung
  - Aktueller psychischer Zustand des Patienten (Affekt, Antrieb, Schwingungsfaehigkeit)
  - Eingesetzte Interventionen / Methoden (z.B. Imaginationsuebung, Schemaarbeit)
  - Vereinbarungen / Hausaufgaben fuer die naechste Sitzung
  - Beobachtete Veraenderungen vs. letzter Sitzung
  
[IGNORE-FELDER]
  - Smalltalk-Anteile zu Beginn/Ende
  - Wiederholungen aus vorherigen Sitzungen, sofern nicht relevant
  - [OFFEN: Pflichtfelder gemaess Klinik-Standard?]
  - [OFFEN: Sollen "Stoerungsmodell"-Bezuege zwingend erfasst werden?]
  - [OFFEN: Wie mit Suizidalitaet/Krisenanzeichen umgehen — immer erfassen, nie auslassen?]

[LAENGE] 150-300 Woerter (kompakter Stage-1 fuer kurzen Stage-2)
[FORMAT] Strukturierte Stichpunkte unter Feldueberschriften
```

### 3.2 `anamnese` (P2)

```
[ZIEL] Stage-2 generiert Anamnese-Fliesstext + getrennten psychischen Befund 
       (intern als 2-Call-Split). Stage-1 muss BEIDE Sub-Calls bedienen.

[FOKUS-FELDER]
  - Biografische Eckdaten (Familie, Schule, Beruf, Beziehung)
  - Soziale Situation aktuell (Wohnsituation, soziales Netz, Beruf)
  - Vor-Diagnosen / Vorbehandlungen / Medikation
  - Aktuelle Beschwerdesymptomatik mit Beginn/Verlauf
  - Auslösefaktoren / Lebensereignisse
  - Ressourcen (Hobbys, Bezugspersonen, Bewaeltigungsstrategien)
  
[IGNORE-FELDER]
  - [OFFEN: Erwartet die Klinik eine bestimmte Reihenfolge der Anamnese-Bereiche?]
  - [OFFEN: Was ist die Mindest-Detailtiefe pro Lebensphase 
           (Kindheit/Adoleszenz/Erwachsenenalter)?]
  - [OFFEN: Soll Befund-Material (Affekt, Stimmung, kognitive Funktionen) 
           in Stage-1 schon separiert werden — oder erst in Stage-2 sortiert?]

[LAENGE] 400-600 Woerter (umfangreichster Stage-1)
[FORMAT] Strukturierte Stichpunkte unter Feldueberschriften, getrennte Sektion 
         "Befund-relevant" am Ende
```

### 3.3 `verlaengerung` (P3)

```
[ZIEL] Stage-2 begruendet die VERLAENGERUNG der Behandlung um weitere 14 Tage.
       Stage-1 destilliert aus aktuellem Verlauf, was die Verlaengerung rechtfertigt.

[FOKUS-FELDER]
  - Therapiefortschritte der LETZTEN 14 Tage (nicht der gesamten Behandlung)
  - Verbleibende Symptomatik / unbearbeitete Themen
  - Indikationsbegruendung: Warum reichen weitere 14 Tage?
  - Behandlungsschwerpunkte fuer die geplante Verlaengerung
  - Risiken bei vorzeitigem Behandlungsende
  
[IGNORE-FELDER]
  - Initialphase / Aufnahme (ueber 30 Tage zurueckliegend)
  - [OFFEN: Welche konkreten Erfolgsmarker gelten als "Fortschritt" 
           (BSS-Verbesserung, GAF-Steigerung, andere)?]
  - [OFFEN: Soll Stage-1 explizit nach "Fluchttendenzen"/Abbruchrisiken filtern?]
  - [OFFEN: Wie mit unklarer Indikationslage umgehen — markieren oder 
           ausformulieren?]

[LAENGE] 300-500 Woerter
[FORMAT] Strukturierte Stichpunkte; Schwerpunkt "Letzte 14 Tage" als eigene Sektion
```

### 3.4 `folgeverlaengerung` (FVA)

```
[ZIEL] Stage-2 begruendet WEITERE Verlaengerung nach bereits erfolgter 
       Erst-Verlaengerung. Wir-Perspektive, Bezug zum vorherigen Antrag.

[FOKUS-FELDER]
  - Kumulativer Therapieverlauf seit Aufnahme (nicht nur letzte 14 Tage)
  - Was wurde im VORHERIGEN Verlaengerungszeitraum erreicht / nicht erreicht
  - Neue Indikation: Warum trotz Verlaengerung noch nicht entlassbar?
  - Realistische Behandlungsziele fuer Folge-Zeitraum
  - Plausibilisierung gegenueber MDK/Krankenkasse
  
[IGNORE-FELDER]
  - Anamnese-Detail (steht im Erstantrag)
  - [OFFEN: Welchen Detailgrad zum VORHERIGEN Antrag braucht Stage-2 
           (kurze Erwaehnung vs. expliziter Rueckbezug auf Antrags-Inhalte)?]
  - [OFFEN: Soll Stage-1 zwischen "geplant aber nicht erreicht" und 
           "ungeplant aufgetretene neue Themen" unterscheiden?]
  - [OFFEN: Gibt es eine Klinik-interne Faustregel zur Anzahl maximal 
           sinnvoller Folgeverlaengerungen (3? 4?) — relevant fuer Argumentation]

[LAENGE] 300-500 Woerter
[FORMAT] Strukturierte Stichpunkte; Zwei Sektionen: 
         "Bilanz Erst-Verlaengerung" + "Notwendigkeit Folge-Verlaengerung"
```

### 3.5 `akutantrag`

```
[ZIEL] Stage-2 begruendet AKUTBEHANDLUNG bei Krisensituation. 
       Stage-1 destilliert die Krisendynamik.

[FOKUS-FELDER]
  - Akute Symptomatik / Krisenmerkmale
  - Auslöser / Trigger der aktuellen Krise
  - Suizidalitaet / Selbst- und Fremdgefaehrdung (zwingend, falls vorhanden)
  - Behandlungsversuche im ambulanten Setting / Warum reichten die nicht?
  - Indikation fuer stationaere Akutbehandlung
  
[IGNORE-FELDER]
  - Detaillierte Biografie (nicht akut-relevant)
  - [OFFEN: Welche Akutkriterien sind formal Pflicht (ICD-10 F-Kategorien? 
           BSS-Score? andere)?]
  - [OFFEN: Wie konkret muss die "ambulante Vorgeschichte" beschrieben werden 
           — Anzahl Sitzungen, Therapeut/in mit Namen, oder generisch?]
  - [OFFEN: Soll Stage-1 zwischen "selbstgefahrend" und "fremdgefaehrdend" 
           strikt trennen, oder gemeinsam erfassen?]

[LAENGE] 200-350 Woerter (kompakter, akut → praegnant)
[FORMAT] Strukturierte Stichpunkte; Sektion "Akut-Indikatoren" als Erste
```

### 3.6 `entlassbericht` (P4)

```
[ZIEL] Stage-2 erstellt umfassenden Entlassbericht mit gesamter 
       Behandlungstrajektorie. Stage-1 muss die volle Bandbreite abdecken.

[FOKUS-FELDER]
  - Initial-Status (Aufnahmebefund, Eingangsdiagnose)
  - Behandlungsverlauf in Phasen / Wendepunkte
  - Eingesetzte Methoden und Interventionen
  - Erreichte Veraenderungen pro Symptombereich
  - End-Status (Entlassbefund)
  - Empfehlung fuer ambulante Weiterbehandlung
  - Medikation bei Aufnahme vs. Entlassung
  
[IGNORE-FELDER]
  - Einzelsitzungs-Inhalte ohne Verlaufs-Relevanz
  - [OFFEN: Welche Sektionen sind durch Klinik-Standard zwingend 
           (Diagnostik / Verlauf / Befund / Empfehlung)?]
  - [OFFEN: Soll Stage-1 chronologisch oder thematisch strukturieren? 
           (P4-Stage-2 macht thematisch, aber Stage-1 koennte chronologisch hilfreich sein)]
  - [OFFEN: Wie tief sollen Vorberichte aus Voraufenthalten einbezogen werden 
           (kurze Erwaehnung vs. eigenes Stage-1-Sub-Summary)?]
  - [OFFEN: Wie viele Sitzungen / welche Dichte ist normal bei einem 
           durchschnittlichen Aufenthalt — beeinflusst Detailtiefe?]

[LAENGE] 500-800 Woerter (umfangreichster Stage-1 ueberhaupt)
[FORMAT] Chronologisch strukturierte Stichpunkte mit Phasen-Ueberschriften 
         (Aufnahme / Mitte / Vor Entlassung)
```

### 3.7 Offene Meta-Fragen (workflow-uebergreifend)

- **Konsistenz:** Sollen alle Stage-1 das gleiche Format produzieren (Stichpunkte) oder darf Format pro Workflow variieren? Empfehlung: einheitlich Stichpunkte, leichter zu verarbeiten.
- **Multi-Source:** Soll Stage-1 bei mehreren Eingabe-Docs (Verlauf + Vorbericht + Transkript) EIN gemeinsames Summary erzeugen, oder pro Doc eines (heute: pro Doc)? Empfehlung: pro Doc, aber mit workflow-spezifischem Prompt — vermeidet Cross-Doc-Konflikte.
- **Wir-Perspektive:** Stage-1 ist intern (nicht user-facing) — braucht sie schon Wir-Form, oder reicht neutrale Schreibe? Empfehlung: neutrale Form in Stage-1, Wir-Form erst in Stage-2. Spart Tokens.
- **Glossar-Anwendung:** Soll `KLINISCHES_GLOSSAR` schon in Stage-1 angewendet werden (z.B. "depressive Verstimmung" statt "schlechte Laune")? Empfehlung: ja, weil dann Stage-2 nicht mehr uebersetzen muss.

---

## 4. Erwartete Wirkung

| Metrik | Vor v19.3.0 | Nach v19.3.0 |
|---|---|---|
| Verlauf-Input nach Prefilter | 100% | 70-80% (-20-30%) |
| Vorbericht-Input nach Prefilter | 100% | 50-70% (-30-50%) |
| Stage-1-Output-Qualitaet (Eval) | Baseline | +5-10% relevanzgewichtet |
| Stage-2-Input-Tokens gesamt | Baseline | -25-40% |
| Cap-Hit-Rate Stage-2 | Baseline | -50% (durch mehr Output-Headroom) |
| Eval-Gesamt-Score (LLM-Jury) | ~85% | 88-92% (vorsichtig geschaetzt) |

---

## 5. Risiken & Mitigation

| Risiko | Wahrscheinlichkeit | Mitigation |
|---|---|---|
| Prefilter entfernt klinisch relevante Inhalte | mittel | Glossar-Whitelist-Check vor jedem Cut; 60%-Max-Reduktion-Limit |
| Workflow-spezifische Prompts driften | mittel | Prompt-Versionierung in `STAGE1_PROMPTS_V1`, `_V2`; Eval-Suite testet alle Versionen |
| Stage-1 verliert workflow-uebergreifende Info | gering | Multi-Source-Strategie (pro Doc ein Summary, nicht alle in einen Topf) |
| Prefilter-Patterns Klinik-spezifisch | hoch | Patterns in Config-Datei `prefilter_patterns.yml`, leicht erweiterbar; Telemetrie zeigt Reduction-Rate pro Pattern |
| Mehraufwand bei Prompt-Wartung (6 statt 1) | mittel | Gemeinsame Praefix/Suffix-Templates; nur Inhalt variiert pro Workflow |
| Stage-1-Latenz steigt durch laengere Prompts | gering | Stage-1-Prompts sind klein, Latenz dominiert von Input-Verarbeitung — netto schneller wegen weniger Input |

---

## 6. Diff-Zusammenfassung

| Datei | Aenderung |
|---|---|
| `backend/app/services/prefilter.py` | **NEU** ~120 Zeilen (4 Filter-Layer, Safety-Guards, Telemetrie) |
| `backend/app/services/verlauf_summary.py` | +40 Zeilen (workflow-Parameter, STAGE1_PROMPTS-Lookup) |
| `backend/app/services/transcript_summary.py` | +40 Zeilen (analog, workflow-Parameter) |
| `backend/app/services/stage1_prompts.py` | **NEU** ~200 Zeilen (6 Prompt-Templates + Helper) |
| `backend/app/services/extraction.py` | +20 Zeilen (prefilter-Aufruf vor Stage-1) |
| `backend/config/prefilter_patterns.yml` | **NEU** Patterns als YAML, Klinik-spezifisch erweiterbar |
| `backend/tests/test_prefilter.py` | **NEU** ~150 Zeilen (15+ Boilerplate-Beispiele) |
| `backend/tests/test_stage1_prompts.py` | **NEU** ~100 Zeilen (Snapshot-Tests pro Workflow) |
| `backend/scripts/eval_report.py` | +20 Zeilen (Prefilter-Reduction-Rate + Stage-1-Quality-Spalten) |

**Gesamt: ~690 neue Zeilen + 60 Aenderungen.**

---

## 7. Definition of Done

- [ ] `prefilter()` implementiert mit 4 Layern + Safety-Guards
- [ ] 15+ Unit-Tests mit realen Boilerplate-Beispielen aus aktuellen Klinik-Dokumenten gruen
- [ ] `prefilter_patterns.yml` mit min. 20 Patterns aus systelios-Dokumenten
- [ ] Glossar-Whitelist-Check verhindert Cuts mit klinisch relevanten Begriffen
- [ ] 60%-Max-Reduction-Limit aktiv, Fallback auf ungefilterten Text bei Trigger
- [ ] `STAGE1_PROMPTS` Dict mit allen 6 Workflows ausgefuellt (Offene Fragen aus Abschnitt 3 geklaert!)
- [ ] `summarize_verlauf_for_workflow()` und `summarize_transcript_for_workflow()` integriert
- [ ] Snapshot-Tests fuer alle 6 Stage-1-Prompts gruen
- [ ] Telemetrie-Felder `prefilter_*_reduction` in `generation_telemetry` gespeichert
- [ ] Eval-Lauf zeigt:
  - Mittlere Reduktion durch Prefilter: 20-40% (je nach doc_type)
  - Cap-Hit-Rate Stage-2: -50% gegenueber v19.2.x
  - Eval-Score Gesamt: nicht schlechter als v19.2.x, idealerweise +3-5pp
- [ ] Mindestens 3 reale Faelle pro Workflow manuell gegen-gepruefte Outputs vs. v19.2.x

---

## 8. Was NICHT dazu gehoert

- Hierarchical/Refine fuer Verlaufstexte (Phase 2)
- Map-Reduce fuer Transkripte (Phase 2)
- RAG ueber Vorberichte mit pgvector (Phase 3)
- Schema-getriebene Extraktion in JSON (Phase 4, ggf. nicht noetig)
- Aenderungen an Stage-2-Prompts (separat, falls noetig)
- Continuation-Retry — das ist v19.2.3, orthogonal

---

## 9. Naechste Schritte / Vor Implementierung zu klaeren

**Domain-Spezifikation (Cars10):**

1. **Offene Fragen pro Workflow** (Abschnitt 3.1-3.6) beantworten — diese definieren `FOKUS-FELDER`/`IGNORE-FELDER` final.
2. **Klinik-Pflichtfelder** identifizieren: Welche Inhalte MUESSEN in jedem Workflow vorkommen, unabhaengig von Stage-1-Filter? Diese werden zur Glossar-Whitelist hinzugefuegt.
3. **Beispiel-Dokumente sammeln:** 3 reale Verlaeufe + 3 reale Vorberichte pro Workflow als Test-Fixtures fuer Prefilter-Unit-Tests und Stage-1-Snapshots.
4. **Klinik-Boilerplate-Patterns sammeln:** Welche wiederkehrenden Floskeln/Briefkopfe/Stempel sind klinik-spezifisch (sysTelios) vs. uebergreifend?
5. **Meta-Fragen aus 3.7** entscheiden (insb. ob Glossar schon in Stage-1 angewendet wird).

**Technische Entscheidungen (gemeinsam):**

6. Modul-Aufteilung: `STAGE1_PROMPTS` als separates File (`stage1_prompts.py`) oder in `prompts.py` integrieren?
7. `prefilter_patterns.yml` vs. Python-Konstanten — YAML ermoeglicht Aenderung ohne Code-Deploy, Python ist statisch validierbar.
8. Reihenfolge im Eval-Report: Prefilter-Reduktion als eigene Sektion oder als Spalte pro Workflow?

---

*Ende v19.3.0 Patch-Plan. Implementierung beginnt nach Klaerung der offenen Fragen aus Abschnitt 9.*
