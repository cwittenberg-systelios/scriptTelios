# Einwilligung v1.1 — geschärfte Formulierung Serverstandort (G3a)

**Änderungsvorschlag gegenüber v1.0 · 12.08.2026 · zur DSB-Abstimmung**

## Problem in v1.0

§1 verspricht: *„Alle Datenverarbeitung findet ausschließlich auf Servern der sysTelios Klinik statt."* — Während der Entwicklungs-/Pilotphase läuft die Verarbeitung auf einem exklusiv gemieteten, dedizierten GPU-Server eines Infrastrukturanbieters. Das ist mit „Servern der sysTelios Klinik" wörtlich nicht gedeckt. Zusätzlich ist der aktuelle Anbieter (RunPod) ein US-Unternehmen — auch bei EU-Rechenzentrum bleibt ein Drittland-Restrisiko (CLOUD Act), das die pauschale Zusage angreifbar macht.

## Vorgeschlagene Neufassung §1 (dritter Absatz, „Wichtig:")

> **Wichtig:** Die gesamte KI-Verarbeitung Ihrer Daten findet auf dedizierten Servern statt, die ausschließlich von der sysTelios Klinik genutzt und kontrolliert werden und sich in Rechenzentren innerhalb der Europäischen Union befinden. Ihre Daten werden zu keinem Zeitpunkt an externe KI-Dienste wie OpenAI, Google, Microsoft oder andere Cloud-KI-Anbieter übermittelt. Die eingesetzten KI-Modelle laufen vollständig auf dieser eigenen Infrastruktur und werden nicht mit Ihren Daten trainiert.

Begründung der Wortwahl: „dedizierte, ausschließlich von der Klinik genutzte und kontrollierte Server in der EU" ist wahr für beide Betriebsformen (gemieteter dedizierter EU-Server heute, klinikeigene Hardware künftig) und behält die eigentliche Schutzzusage bei — keine externen KI-Dienste, kein Training, EU-Standort.

## Neuer Absatz §1a — Transparenz zur Infrastruktur (Entwicklungsphase)

> Während der Einführungsphase wird hierfür ein bei einem spezialisierten Rechenzentrumsanbieter exklusiv angemieteter Server (Standort EU) eingesetzt; mit dem Anbieter besteht ein Auftragsverarbeitungsvertrag nach Art. 28 DSGVO. Der Anbieter hat keinen inhaltlichen Zugriff auf Ihre Daten und verarbeitet diese nicht für eigene Zwecke. Nach Abschluss der Einführungsphase wird die Verarbeitung auf klinikeigene Hardware in den Räumen der sysTelios Klinik überführt.

## Offene Punkte für DSB (Voraussetzung für Echtdaten unter v1.1)

| # | Punkt | Status |
|---|---|---|
| 1 | AVV (Art. 28) mit RunPod vorhanden und geprüft? | ☐ klären |
| 2 | Drittlandtransfer-Bewertung: US-Mutterkonzern trotz EU-Region → SCC/TIA erforderlich? | ☐ klären |
| 3 | Falls 1/2 negativ: Echtdaten-Pilot erst nach On-Prem-Migration (dann entfällt §1a ersatzlos) | Rückfalloption |

**Hinweis:** Die Neufassung ist bewusst so geschnitten, dass beim On-Prem-Umzug nur §1a gestrichen wird — §1 bleibt unverändert gültig. Bereits eingeholte v1.1-Einwilligungen müssen dann nicht erneuert werden.

## Neuer Passus §6a — Qualitätssicherung während der Einführungsphase (TIA-Option a)

Einzufügen als eigener Absatz direkt nach dem Einwilligungstext in §6; zusätzlich wird der Aufzählung „Ich habe verstanden, dass:“ ein vierter Punkt angefügt (s. u.).

> **6a. Qualitätssicherung während der Einführungsphase**
> Während der Einführungsphase von scriptTelios prüfen wir laufend die Qualität und Fehlerfreiheit der KI-gestützten Dokumentation. Hierzu werden Verarbeitungsdaten (Transkripte, erstellte Entwürfe sowie Qualitäts- und Rückmeldedaten der behandelnden Therapeut:innen) für einen begrenzten Zeitraum von höchstens **[90 Tagen]** in gesicherter Form aufbewahrt und ausschließlich durch das für scriptTelios verantwortliche Klinikpersonal ausgewertet, das der Schweigepflicht nach § 203 StGB unterliegt. Nach Ablauf dieser Frist werden die Daten gelöscht oder vollständig anonymisiert. Diese Auswertung dient der Patientensicherheit und der Qualität Ihrer Dokumentation (Erkennung und Behebung von Fehlern des Systems). Ein Training der KI-Modelle mit Ihren identifizierbaren Daten findet nicht statt.

Ergänzung des vierten Aufzählungspunkts in §6 („Ich habe verstanden, dass:“):

> - Verarbeitungsdaten während der Einführungsphase zeitlich begrenzt (max. [90 Tage]) zur Qualitätssicherung ausgewertet und danach gelöscht oder anonymisiert werden (Punkt 6a).

**Begründung der Verortung in §6 (statt separater Opt-in):** Die Fehler- und Qualitätsanalyse ist während der Pilotphase Bestandteil des sicheren Betriebs des Werkzeugs, mit dem die Behandlungsdokumentation der einwilligenden Person selbst erstellt wird (Art. 9 Abs. 2 lit. a und h DSGVO; Qualitätssicherung als Teil der Versorgung). Ein separates Opt-out würde den sicheren Pilotbetrieb für diese Person faktisch verhindern. **[DSB prüft: Kopplungsverbots-Würdigung.]** Falls der DSB eine getrennte Einwilligung verlangt, Fallback-Variante: Punkt 6a als eigenes Ankreuzfeld zwischen §6 und §7 aufnehmen; bei Ablehnung ist die Teilnahme am Pilot dann ausgeschlossen und es wird konventionell dokumentiert.

**Technische Folgepflicht:** Die [90-Tage-]Frist muss in der Retention-Konfiguration für alle betroffenen Artefakte technisch durchgesetzt werden — insbesondere prompts.log (enthält Transkript-/Prompt-Inhalte) und ggf. feedback.log, dessen bisherige Aufbewahrung (10 Jahre) für identifizierbare Inhalte nicht mit 6a vereinbar wäre. Eigener Mini-Sprint erforderlich.

## §7 (optionale wissenschaftliche Weiterverwendung) — vorerst gestrichen

Die gesamte optionale Einwilligung §7 aus v1.0 (anonymisierte wissenschaftliche Weiterverwendung inkl. Anonymisierungs-Erläuterung, Verwendungszwecken, Negativliste und beiden Ankreuzfeldern) wird in v1.1 **nicht aufgenommen**. Sie wird erst in der Produktivphase (v1.2) wieder eingeführt, wenn der Anonymisierungsprozess operativ steht. Folgeänderungen: bisheriger §8 (Bestätigung und Unterschrift) wird zu §7; der Hinweis in §6a bezieht sich nur noch auf das Trainingsverbot, ohne Verweis auf eine Forschungsklausel. Bereits nach v1.0 erteilte §7-Einwilligungen bleiben davon unberührt, werden aber während der Pilotphase nicht ausgeübt.

## Sonstige Änderungen v1.0 → v1.1

Keine weiteren. Versionszeile am Dokumentende auf *„Version 1.1 – Stand: August 2026"* anheben.
