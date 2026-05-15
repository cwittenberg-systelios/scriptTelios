# Patch-Plan v19.2.3 — Truncation-Detector + Continuation-Retry

**Status:** Plan, noch nicht implementiert.
**Voraussetzung:** v19.2.2 deployed (Anti-Think Defense-in-Depth + Stage-1 Verlauf-Summary).
**Empfohlene Reihenfolge:** Erst max_tokens-Patch in `workflows.py` deployen (`dokumentation: 2048→3500`), 10-20 P1-Jobs durchschicken, Telemetrie auswerten. Bleiben `cap_hit=true`-Fälle → v19.2.3 implementieren.

---

## 1. Motivation

Symptom: Mid-word abgeschnittene Sätze am Output-Ende, primär bei P1 (`dokumentation`).

Beispiel:
> "...halten wir eine Verlängerung um weitere 14 Tage aus psychotherapeutischer Sicht für dringend indiziert. In diesem Zeitraum sollen die bisherigen Fortschritte vertieft und neue Handlung"

> "...eine funktionale Lösung darstellt, um mit der inneren Ambivalenz und der Angst vor dem Alleinsein umz"

Ursache: `num_predict` cap-hit während Streaming. Output bricht ab, ohne sauberen Satzabschluss.

`max_tokens=3500` (Patch v19.2.3-pre) deckt 80%+ der Fälle ab. Restliche Fälle:
- Thinking-Leak verbraucht Output-Budget (selbst mit v19.2 Anti-Think nicht 100% wasserdicht)
- Modell overshootet `word_limit` deutlich
- Andere Workflows mit knappem Budget (z.B. `akutantrag`)

→ Continuation-Retry als robuste, workflow-agnostische Absicherung.

---

## 2. Komponenten

### 2.1 Truncation-Detector (`postprocessing.py`)

Neue Funktion ~30 Zeilen:

```python
def detect_truncated_ending(text: str) -> tuple[bool, str]:
    """
    Erkennt ob ein generierter Text unvollstaendig endet.

    Returns:
        (is_truncated, reason) wobei reason in:
        - "ok"              → Text endet sauber
        - "mid_word"        → endet im Wortfragment (z.B. "umz")
        - "no_sentence_end" → endet ohne .!?:;
        - "open_quote"      → unausgeglichene Anfuehrungszeichen
        - "open_bracket"    → unausgeglichene Klammern
    """
    import re

    stripped = text.rstrip()
    if not stripped:
        return (True, "empty")

    # Sauber: endet auf Satzendung oder gueltigen Schlussmarker
    SENTENCE_END = ".!?:;)"
    CLOSING_QUOTES = '"\u201d\u00bb\u203a\''
    if stripped[-1] in SENTENCE_END or stripped[-1] in CLOSING_QUOTES:
        return (False, "ok")

    # Mid-word: letztes Token endet auf Buchstaben (kein Satzzeichen)
    last_token = stripped.split()[-1] if stripped.split() else ""
    if re.match(r"^[a-zaeoeueA-ZAeOeUess]+$", last_token):
        return (True, "mid_word")

    return (True, "no_sentence_end")
```

**Unit-Tests** (`tests/test_postprocessing_truncation.py`):
- Satz mit `.` → `(False, "ok")`
- "umz" → `(True, "mid_word")`
- "die wir wieder hochfahren" → `(True, "no_sentence_end")`
- "..." (Ellipsis) → `(False, "ok")` (eigentlich Edge-Case, soll als ok zaehlen)
- Leerer String → `(True, "empty")`
- Text endet auf `)` → `(False, "ok")`
- Text endet auf Anfuehrungszeichen → `(False, "ok")`

### 2.2 Continuation-Retry (`llm.py`)

Erweiterung in `generate_text` nach erfolgreichem Hauptcall, ~60 Zeilen:

```python
# Nach erstem LLM-Call, vor Postprocessing:
from app.services.postprocessing import detect_truncated_ending

is_trunc, trunc_reason = detect_truncated_ending(result["text"])
hit_cap = bool(result.get("telemetry", {}).get("tokens_hit_cap"))

continuation_used = False
continuation_tokens = 0

# Trigger: cap_hit ODER truncation_detector positiv
if (hit_cap or is_trunc) and trunc_reason != "ok":
    logger.warning(
        "Output truncated detected (cap_hit=%s, reason=%s), Continuation-Retry...",
        hit_cap, trunc_reason
    )

    # Letzte 1500 Zeichen als Kontext für Continuation
    tail = result["text"][-1500:]
    cont_user = (
        "Der folgende Text endet abgeschnitten. Setze ihn nahtlos fort, "
        "ohne den letzten Satz zu wiederholen oder zu modifizieren. "
        "Beginne genau dort, wo er aufhoert, und schliesse den Abschnitt "
        "mit einem sauberen Satzende ab (mindestens ein vollstaendiger Satz, "
        "maximal drei). Antworte AUSSCHLIESSLICH mit dem Fortsetzungstext, "
        "ohne Wiederholung, Markdown oder Erklaerungen.\n\n"
        f"--- TEXT ENDE ---\n{tail}\n--- HIER FORTSETZEN ---"
    )

    cont_result = await _generate_ollama(
        system_prompt=system_prompt,
        user_content=cont_user,
        max_tokens=1500,
        model=effective_model,
        assistant_primer="",  # kein Primer fuer Continuation
    )

    cont_text = cont_result["text"].strip()

    # Optional: zweite Truncation-Pruefung des Suffix
    is_trunc2, _ = detect_truncated_ending(cont_text)
    if is_trunc2:
        # Rekursive Continuation max 1x mehr (Stack-Schutz)
        # Implementierung: counter-Argument oder einfacher Re-Call
        logger.warning("Continuation selbst truncated - akzeptieren als degraded")

    # Merge: einfaches Concat (Prompt sagt explizit "setze fort")
    # Leading space falls Continuation nicht mit Whitespace beginnt
    if cont_text and not cont_text[0].isspace():
        result["text"] = result["text"] + " " + cont_text
    else:
        result["text"] = result["text"] + cont_text

    continuation_used = True
    continuation_tokens = cont_result.get("token_count", 0)
    logger.info(
        "Continuation-Retry erfolgreich: +%d Tokens, final endet auf '%s'",
        continuation_tokens, result["text"][-50:].replace("\n", " "),
    )

# Final ending-check
is_final_trunc, final_reason = detect_truncated_ending(result["text"])

# Telemetrie ergaenzen
result.setdefault("telemetry", {}).update({
    "truncated_ending":     is_trunc,
    "truncation_reason":    trunc_reason,
    "continuation_used":    continuation_used,
    "continuation_tokens":  continuation_tokens,
    "final_ending_ok":      not is_final_trunc,
})
```

### 2.3 Telemetrie-Felder

Neue Felder im `generation_telemetry`-JSONB-Objekt (`jobs.generation_telemetry`):

| Feld | Typ | Beschreibung |
|---|---|---|
| `truncated_ending` | bool | Truncation-Detector am ersten Output positiv |
| `truncation_reason` | str | `"ok"`, `"mid_word"`, `"no_sentence_end"`, `"open_quote"`, `"open_bracket"`, `"empty"` |
| `continuation_used` | bool | Continuation-Retry wurde ausgeloest |
| `continuation_tokens` | int | Token-Verbrauch des Continuation-Calls |
| `final_ending_ok` | bool | Nach Continuation: Output endet sauber |

**Keine Schema-Migration noetig** — JSONB-Spalte existiert seit v19.1.

### 2.4 Eval-Suite (`test_eval.py`)

Hard-Fail-Schwelle erweitern:

```python
# Bisher (v19.1):
if telemetry.get("degraded") or telemetry.get("tokens_hit_cap"):
    score = 0.0
    failure_reason = "degraded_generation"

# Neu (v19.2.3):
if telemetry.get("degraded"):
    score = 0.0
    failure_reason = "degraded_generation"
elif not telemetry.get("final_ending_ok", True):
    # Nur Hard-Fail wenn auch Continuation gescheitert ist
    score = 0.0
    failure_reason = "truncated_after_continuation"
# tokens_hit_cap allein ist KEIN Hard-Fail mehr, wenn final_ending_ok=true
# (Continuation hat repariert)
```

### 2.5 Eval-Report (`eval_report.py`, optional)

Neue Spalte `Cont` in Per-Workflow-Detail-Tabellen:
- `—` falls Pre-v19.2.3 (kein Feld in Telemetrie)
- `OK` falls Continuation nicht noetig (`continuation_used=false` UND `final_ending_ok=true`)
- `C` (orange) falls Continuation erfolgreich (`continuation_used=true` UND `final_ending_ok=true`)
- `X` (rot) falls Continuation gescheitert (`final_ending_ok=false`)

Neue KPI-Zeile in "Generierungs-Stabilitaet"-Sektion:
- "Continuation-Retry-Rate" — % Jobs mit `continuation_used=true`
- "Final-Ending-OK-Rate" — % Jobs mit `final_ending_ok=true`

---

## 3. Erwartete Wirkung

| Metrik | Vor v19.2.3-pre | Nach max_tokens-Patch | Nach v19.2.3 |
|---|---|---|---|
| P1 cap_hit Rate | 30-50% (geschaetzt) | 5-15% (geschaetzt) | <2% effektiv truncated |
| Final-Ending-OK Rate | unbekannt | ~85-95% | >98% |
| Latenz P1 | Baseline | unveraendert | +5-15s wenn Continuation greift |
| Latenz Avg | Baseline | unveraendert | +1-3s (gewichteter Schnitt) |

---

## 4. Risiken & Mitigation

| Risiko | Wahrscheinlichkeit | Mitigation |
|---|---|---|
| Continuation halluziniert | mittel | Strikter Prompt "ohne Wiederholung, ohne Modifikation"; tail-Kontext gibt Anker |
| Continuation duplicates last sentence | mittel | Prompt-Anweisung "nicht wiederholen"; optional: Levenshtein-Check der ersten 100 Zeichen Suffix gegen letzte 100 Zeichen Original |
| Continuation selbst truncated | gering | Recursion-Counter (max 2x), sonst degraded markieren |
| Zusatz-Latenz unakzeptabel | gering | Continuation nur bei tatsaechlichem Trigger (cap_hit oder detect_truncated_ending) |
| Falsch-positiver Detector | gering | Unit-Tests fuer Edge-Cases (Ellipsis, Klammer, Anfuehrungszeichen) |
| Stage-1 Summaries auch truncated | mittel | Detector auch in `verlauf_summary.py` und `transcript_summary.py` einbauen — separater Patch |

---

## 5. Diff-Zusammenfassung

| Datei | Aenderung |
|---|---|
| `backend/app/services/postprocessing.py` | +30 Zeilen (`detect_truncated_ending`) |
| `backend/app/services/llm.py` | +60 Zeilen (Continuation-Block in `generate_text`) |
| `backend/tests/test_postprocessing_truncation.py` | **NEU** ~80 Zeilen (Detector-Unit-Tests) |
| `backend/tests/test_llm_continuation.py` | **NEU** ~120 Zeilen (Continuation-Retry mit gemocktem LLM) |
| `backend/tests/test_eval.py` | +10 Zeilen (Hard-Fail-Logik erweitert) |
| `backend/scripts/eval_report.py` | +30 Zeilen (Cont-Spalte + KPI-Zeilen, optional) |

**Gesamt: ~330 neue Zeilen.**

---

## 6. Definition of Done

- [ ] `detect_truncated_ending` implementiert + 7 Unit-Tests grün
- [ ] Continuation-Retry in `generate_text` integriert mit Telemetrie-Feldern
- [ ] Eval-Suite: `final_ending_ok=false` triggert Hard-Fail
- [ ] Eval-Lauf gegen v19.2.3 zeigt:
  - `cap_hit`-Rate kann hoch bleiben — Indikator
  - `final_ending_ok`-Rate >98% — Wirkungsnachweis
  - `continuation_used`-Rate dokumentiert die effektive Continuation-Frequenz
- [ ] Mindestens 3 reale P1-Fälle, die vorher mid-word abbrachen, jetzt sauber endend
- [ ] Eval-Report zeigt neue Cont-Spalte korrekt
- [ ] Keine neuen Halluzinationen in den Continuation-Suffixen (Stichprobe 5x manuell pruefen)

---

## 7. Was NICHT dazu gehoert

- Continuation in Stage-1-Summaries (`verlauf_summary.py`, `transcript_summary.py`) — separater Patch v19.2.4 falls noetig
- Streaming-Anpassungen (`on_progress`) — Continuation laeuft komplett im Backend
- Frontend-Anzeige "Output wurde fortgesetzt" — kein User-Mehrwert, nur Telemetrie/Eval
- Aenderungen an `max_tokens` weiterer Workflows — separat bei Bedarf

---

## 8. Offene Fragen

- Soll `tokens_hit_cap=true` allein als Trigger reichen, oder erst wenn auch `detect_truncated_ending` positiv? — Empfehlung: ODER-Logik, weil Cap-Hit ohne Truncation-Sichtbarkeit (z.B. cap genau auf Satzgrenze) trotzdem auf knappes Budget hindeutet.
- Recursion-Counter: 1 oder 2 max Continuations? — Empfehlung: 2 (Headroom fuer pathologische Faelle), aber mit Telemetrie-Feld `continuation_attempts` zur Diagnose.
- Continuation-Prompt: System-Prompt vom Originalcall wiederverwenden oder vereinfachen? — Empfehlung: wiederverwenden, damit Stil/Wir-Form/etc. konsistent bleiben.

---

*Ende v19.2.3 Patch-Plan. Implementierung beginnt nach max_tokens-Eval-Lauf.*
