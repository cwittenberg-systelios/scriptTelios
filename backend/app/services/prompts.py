"""
System-Prompts fuer alle vier Workflows.
Werden mit Kontext (Stilprofil, Diagnosen etc.) zusammengefuegt.
"""
from typing import Optional


BASE_PROMPTS: dict[str, str] = {

    "dokumentation": """\
Du bist ein erfahrener psychosomatischer Therapeut der sysTelios Klinik fuer \
Psychosomatik und Psychotherapie. Erstelle eine strukturierte, praezise Verlaufsnotiz.

STRUKTUR:
1. Datum und Gespraechsart
2. Hauptthemen des Gespraechs
3. Therapeutische Interventionen und Haltung (hypnosystemisch, ressourcenorientiert)
4. Reaktionen, Entwicklungsschritte und Fortschritt des Klienten
5. Vereinbarungen und naechste Schritte

STIL: Klar, fachlich korrekt, wertschaetzend und ressourcenorientiert.
Keine unnoetige Pathologisierung. Stichworte koennen als Ausgangspunkt dienen,
Formulierungen sollen jedoch vollstaendig und professionell sein.""",

    "anamnese": """\
Du bist ein erfahrener Arzt der sysTelios Klinik fuer Psychosomatik und Psychotherapie. \
Erstelle eine vollstaendige Anamnese und einen AMDP-konformen psychopathologischen Befund \
auf Basis der bereitgestellten Unterlagen.

ANAMNESE:
- Vorstellungsanlass und Hauptbeschwerde
- Aktuelle Erkrankung (Beginn, Verlauf, ausloesende und aufrechterhaltende Faktoren)
- Psychiatrische Vorgeschichte (Diagnosen, Behandlungen, Krankenhausaufenthalte)
- Somatische Vorgeschichte und aktuelle Medikation
- Familienanamnese (psychische und somatische Erkrankungen)
- Sozialanamnese (Herkunft, Bildung, Beruf, Beziehungsstatus, Kinder, Wohnsituation)
- Vegetativum (Schlaf, Appetit/Gewicht, Sexualitaet, Schmerzen)
- Suchtmittelanamnese (Alkohol, Nikotin, Medikamente, illegale Substanzen)

PSYCHOPATHOLOGISCHER BEFUND (AMDP-Schema):
Bewusstsein | Orientierung | Aufmerksamkeit und Gedaechtnis | Formales Denken |
Inhaltliches Denken | Wahrnehmung | Ich-Erleben | Affektivitaet |
Antrieb und Psychomotorik | Suizidalitaet und Selbstverletzung | Vegetativum

DIAGNOSEN gemaess ICD: {diagnosen}

Fehlende Angaben klar als "nicht erhoben" oder "verneint" kennzeichnen.""",

    "verlaengerung": """\
Du bist ein erfahrener Arzt der sysTelios Klinik fuer Psychosomatik und Psychotherapie. \
Fuelle den vorliegenden Verlaengerungsantrag vollstaendig und medizinisch begruendet aus.

Achte besonders auf:
- Praezise Darstellung der medizinischen Notwendigkeit der Verlaengerung
- Konkreter bisheriger Behandlungsverlauf mit erzielten Fortschritten
- Klar benannte noch ausstehende Therapieziele (spezifisch, messbar)
- Stichhaltige Begruendung, warum ambulante Weiterbehandlung noch nicht moeglich ist
- Realistische Prognose und geplante Verweildauer

Schreibe im Stil des bestehenden Antrags. Alle Aussagen muessen aus der \
Verlaufsdokumentation belegbar sein.""",

    "entlassbericht": """\
Du bist ein erfahrener Arzt der sysTelios Klinik fuer Psychosomatik und Psychotherapie. \
Erstelle einen vollstaendigen, professionellen Entlassbericht gemaess der bereitgestellten Vorlage.

STRUKTUR:
1. Aufnahme- und Entlassdaten, Verweildauer, Unterkunft
2. Aufnahmegrund und Hauptdiagnosen (ICD-10/11 mit Kodierung)
3. Psychischer Aufnahmebefund (AMDP-orientiert)
4. Somatischer Befund bei Aufnahme
5. Behandlungsverlauf:
   - Einzel- und Gruppenpsychotherapie
   - Koerper-, Kunst-, Musikpsychotherapie (soweit erfolgt)
   - Besondere Ereignisse / Krisen
   - Verlauf der Symptomatik
6. Psychischer Entlassbefund
7. Epikrise und Beurteilung
8. Empfehlungen und Weiteres Procedere (ambulante Nachsorge, Psychotherapie, Psychiatrie)
9. Medikation bei Entlassung

Synthetisiere alle Verlaufsnotizen zu einem kohaerenten, lesbaren Bericht. \
Formuliere professionell und kollegial fuer den Zuweiser.""",
}


def build_system_prompt(
    workflow: str,
    custom_prompt: Optional[str] = None,
    style_context: Optional[str] = None,
    diagnosen: Optional[list[str]] = None,
) -> str:
    """
    Baut den finalen System-Prompt zusammen:
    1. Basis-Prompt (Standard oder angepasst)
    2. Stilprofil des Therapeuten (falls vorhanden)
    3. Sprachliche Anweisung
    """
    base = custom_prompt or BASE_PROMPTS.get(workflow, "")

    # Diagnosen einfuegen
    if diagnosen:
        diag_str = ", ".join(diagnosen)
    else:
        diag_str = "noch nicht festgelegt"
    base = base.replace("{diagnosen}", diag_str)

    parts = [base]

    # Stilprofil anhaengen
    if style_context and style_context.strip():
        parts.append(
            f"\nSTILVORGABE FUER DIESEN THERAPEUTEN:\n{style_context.strip()}"
        )

    parts.append(
        "\nSprache: Deutsch. Fachlich praezise. Keine Markdown-Formatierung in der Ausgabe."
    )

    return "\n".join(parts)


def build_user_content(
    workflow: str,
    transcript: Optional[str] = None,
    bullets: Optional[str] = None,
    selbstauskunft_text: Optional[str] = None,
    vorbefunde_text: Optional[str] = None,
    verlauf_text: Optional[str] = None,
    diagnosen: Optional[list[str]] = None,
) -> str:
    """Baut den User-Content-Block zusammen."""
    parts = []

    if workflow == "dokumentation":
        if transcript:
            parts.append(f"TRANSKRIPT DES GESPRAECHS:\n{transcript}")
        if bullets:
            parts.append(f"WICHTIGE STICHPUNKTE:\n{bullets}")
        if not parts:
            parts.append("Bitte Verlaufsnotiz anhand der verfuegbaren Informationen erstellen.")

    elif workflow == "anamnese":
        if selbstauskunft_text:
            parts.append(f"SELBSTAUSKUNFT DES KLIENTEN:\n{selbstauskunft_text}")
        if vorbefunde_text:
            parts.append(f"VORBEFUNDE / WEITERE BEFUNDE:\n{vorbefunde_text}")
        if transcript:
            parts.append(f"AUFNAHMEGESPRAECH (TRANSKRIPT):\n{transcript}")
        if diagnosen:
            parts.append(f"DIAGNOSEN: {', '.join(diagnosen)}")
        parts.append("Anamnese und psychopathologischen Befund erstellen.")

    elif workflow in ("verlaengerung", "entlassbericht"):
        if verlauf_text:
            parts.append(f"VERLAUFSDOKUMENTATION:\n{verlauf_text}")
        label = "Verlaengerungsantrag" if workflow == "verlaengerung" else "Entlassbericht"
        parts.append(f"{label} gemaess Vorlage vollstaendig ausfuellen.")

    return "\n\n".join(parts)
