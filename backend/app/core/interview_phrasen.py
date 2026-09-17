"""
Phrasen der Gespraechsfuehrung im Interview-Modus (v19.24, B1/C2).

Quittung  - kurze Bestaetigung VOR einer Rueckfrage ("Verstanden.").
Ueberleitung - Bruecke zur naechsten Frage, wenn KEINE Rueckfrage folgt
              und die Antwort nicht leer war ("Gut, dann weiter:").

Bewusst ohne LLM und ohne inhaltliches Echo (C2): neutrale Varianten,
Du-Form, zufaellig ohne direkte Wiederholung. Das Frontend spricht
Quittung + Rueckfrage bzw. Ueberleitung + naechste Frage als eine Sequenz
(speech.js) - Server-TTS spaeter ersetzt nur die Ausgabeschicht.

Nicht jede Antwort wird bestaetigt: Quittungen gibt es nur vor
Rueckfragen, Ueberleitungen nur bei tatsaechlich gegebener Antwort.
"""
from __future__ import annotations

import random

QUITTUNGEN: tuple[str, ...] = (
    "Verstanden.",
    "Alles klar.",
    "Okay.",
    "Gut, danke.",
    "Ja, verstanden.",
)

UEBERLEITUNGEN: tuple[str, ...] = (
    "Gut, dann weiter.",
    "Danke. Nächste Frage.",
    "Okay, weiter geht's.",
    "Alles klar. Dann zur nächsten Frage.",
    "Danke.",
)

# Quittungen vor einer Trigger-Nachfrage (Suizidalitaet): ruhiger, ohne
# "Gut"/"Okay", damit die Bestaetigung nicht wie Zustimmung zum Inhalt klingt.
QUITTUNGEN_ERNST: tuple[str, ...] = (
    "Verstanden.",
    "Ja, verstanden.",
    "Danke, dass du das sagst.",
)


def _pick(varianten: tuple[str, ...], seed: int, vorherige: str | None = None) -> str:
    """Deterministisch je Seed (Frage-Index + Session), aber nie dieselbe
    Phrase wie beim vorherigen Aufruf, wenn eine Alternative existiert."""
    rng = random.Random(seed)
    kandidaten = [v for v in varianten if v != vorherige] or list(varianten)
    return rng.choice(kandidaten)


def quittung(seed: int, *, ernst: bool = False, vorherige: str | None = None) -> str:
    return _pick(QUITTUNGEN_ERNST if ernst else QUITTUNGEN, seed, vorherige)


def ueberleitung(seed: int, *, vorherige: str | None = None) -> str:
    return _pick(UEBERLEITUNGEN, seed, vorherige)
