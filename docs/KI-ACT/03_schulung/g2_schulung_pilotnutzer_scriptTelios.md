# scriptTelios — Schulung Pilot-Nutzer:innen (KI-Kompetenz, AI Act Art. 4)

**Version 1.0-Entwurf · 12.08.2026 · Dauer: ca. 60 Min. · Zielgruppe: 10 Pilot-Therapeut:innen**

---

## Teil A — Was ist scriptTelios, was ist es nicht (10 Min.)

**Ist:** Ein Entwurfswerkzeug. Es transkribiert Sitzungsaufnahmen (lokal, ohne Cloud-KI-Dienste) und erstellt daraus sowie aus Quelldokumenten Entwürfe für Gesprächsdokumentation, Anamnese/Befund, Anträge, Verlängerungen und Entlassberichte.

**Ist nicht:** Ein Diagnose- oder Entscheidungssystem. Es bewertet keine Patient:innen, trifft keine Therapieentscheidungen und ersetzt keine fachliche Beurteilung. **Jeder Entwurf ist erst nach Ihrer Prüfung und Freigabe ein Dokument.** Die fachliche und rechtliche Verantwortung liegt vollständig bei Ihnen.

**Datenschutz-Kernfakt für Patientengespräche:** Alle Verarbeitung läuft auf dedizierten, von der Klinik kontrollierten Servern in der EU. Es werden keine Daten an OpenAI, Google, Microsoft o. ä. übermittelt. Die KI-Modelle werden nicht mit Patientendaten trainiert.

## Teil B — Wie die KI arbeitet und wo sie irrt (15 Min.)

1. **Sprachmodelle erzeugen plausiblen Text, keine geprüfte Wahrheit.** Sie formulieren auf Basis der Quellen — können dabei aber Inhalte verdichten, umdeuten oder (selten) hinzuerfinden („Konfabulation").
2. **Eingebaute Schutzmechanismen — und ihre Grenzen:**
   - *Source Gate:* Ohne Transkript/Quelle bricht der Job ab, statt zu erfinden.
   - *Coverage-Badges:* Zeigen Lücken in der Transkript-Abdeckung. Ein CRITICAL-Badge heißt: Teile der Sitzung fehlen im Entwurf.
   - *Quellentreue-QC:* Warnt bei Inhalten ohne Beleg in den Quellen. **Eine Warnung ist ein Prüfauftrag, kein Fehlerprotokoll — und das Fehlen von Warnungen ist keine Richtigkeitsgarantie.**
   - *Sprach-QC:* Warnt bei Wir-Form (wo unzulässig) und pathologisierender Sprache.
3. **Typische Fehlerbilder, auf die Sie achten:** verwechselte Sprecherzuordnung bei Diarisierung, falsch übernommene Zahlen/Testwerte (Regel: Zahlen immer gegen Quelle prüfen), zu glatte Zusammenfassungen, die Ambivalenzen einebnen, Namens-/Geschlechtsfehler.

## Teil C — Ihre Pflichten im Umgang (15 Min.)

1. **Vollständige Prüfung vor Übernahme.** Kein Copy-Paste ungelesener Entwürfe in die Akte oder in Anträge. Bei Anträgen an Kostenträger: inhaltliche Korrektheit jeder Aussage prüfen.
2. **Einwilligung zuerst.** KI-gestützte Dokumentation nur bei vorliegender, unterschriebener Einwilligung der Patient:in. Bei Widerruf: keine weitere KI-Verarbeitung, konventionell dokumentieren.
3. **Nur vorgesehene Nutzung.** Keine Zweckentfremdung (z. B. keine „Was würdest du diagnostizieren?"-Anfragen), keine Uploads klinikfremder Daten, keine Nutzung anderer KI-Tools (ChatGPT etc.) für Patientendaten — das wäre eine Datenschutzverletzung.
4. **Aufnahme-Hygiene.** Aufnahmen nur über die vorgesehene Funktion; Original-Audio wird nach Transkription automatisch gelöscht — lokale Privatkopien sind unzulässig.
5. **Fehler und Auffälligkeiten melden** (s. Teil D). Bei schwerwiegender Fehlfunktion: Nutzung stoppen, melden.

## Teil D — Feedback und Ansprechpartner (5 Min.)

- Feedback-Funktion im Tool nutzen (fließt in feedback.log ein und ist mit dem konkreten Job verknüpft — so können wir Ihre Rückmeldung exakt nachvollziehen).
- Technische Fragen: C. Wittenberg (c.wittenberg@systelios.de)
- Datenschutzfragen: Datenschutzbeauftragte:r ([einsetzen]) · datenschutz@systelios.de

## Teil E — Praxisübung (15 Min.)

Gemeinsamer Durchlauf an einem synthetischen Fall: Aufnahme → Entwurf → QC-Badges interpretieren → einen absichtlich eingebauten Fehler finden (Testwert weicht von Quelle ab) → korrigieren → freigeben.

---

## Teilnahmebestätigung (Art. 4 AI Act — bitte bei der Klinik ablegen)

Ich habe an der Schulung teilgenommen, die Funktionsweise und Grenzen von scriptTelios verstanden und bestätige, dass ich Entwürfe vor Übernahme fachlich prüfe.

| Name | Datum | Unterschrift |
|---|---|---|
| | | |

**Auffrischung:** bei wesentlichen Systemänderungen, spätestens jährlich.
