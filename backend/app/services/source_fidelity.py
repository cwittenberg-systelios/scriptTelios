"""Quellentreue-Pruefung (source fidelity).

Meldet Verfahrens-/Methoden-Vokabular (IFS-/Ego-State-Anteilssprache etc.) und
Standard-Hausaufgaben, die im ERZEUGTEN Text stehen, aber NICHT in der QUELLE
(Transkript + extrahierte Eingabedokumente + Auftrags-Prompt) belegt sind - also
vom Modell aufgestuelpt wurden. Konservativ: einfacher Wortstamm-Substring-Abgleich,
untertreibt eher (lieber ein paar echte Faelle verpassen als falsch-positiv flaggen).

SINGLE SOURCE OF TRUTH fuer die Begriffslisten - genutzt von:
  - Produktions-QA          app/services/quality_check.py (run_quality_check)
  - Eval-Framework          tests/eval/test_eval.py (EvalResult.check_source_fidelity)
  - Standalone-Probe        tests/eval/faithfulness_probe.py

Klinik-Hinweis (sysTelios): die deutsche Anteilssprache ist massgeblich (Manager,
Antreiber, Richter, Feuerbekaempfer, Verbannte, inneres Kind ...). Die englischen
IFS-Begriffe (Self-Energy, Exile, Feuerwehr-Anteil) werden an der Klinik NICHT
verwendet - tauchen sie im Output auf, sind sie fast immer aufgestuelpt (auch die
Quelle enthaelt sie nie) und werden als "untypisch" mitgeprueft.
"""
from __future__ import annotations

import re


# (Anzeige-Label, Wortstamm-zum-Suchen in lower-case)
METHOD_TERMS: list[tuple[str, str]] = [
    # Spezifische Anteilsnamen der sysTelios-Klinik - konkrete Entitaeten. Tauchen sie
    # im Output auf, ohne in der Quelle belegt zu sein, ist das eine ERFUNDENE SPEZIFIK
    # (kein blosses Zusammenfassungs-Label). Geringes Synonym-Risiko, weil dies die
    # kanonischen Begriffe der Klinik sind - die Quelle nutzt dieselben Worte.
    ("Manager",                      "manager"),
    ("Antreiber",                    "antreiber"),
    ("Richter",                      "richter"),
    ("Feuerbekämpfer",               "feuerbekämpf"),
    ("Verbannte",                    "verbannt"),
    ("inneres Kind",                 "inneres kind"),
    # Englische/Fremdbegriffe, die die sysTelios-Klinik NIE nutzt -> bei Auftreten
    # praktisch immer aufgestuelpt (die Quelle enthaelt sie in keiner Form).
    ("Self-Energy (untypisch)",      "self-energy"),
    ("Self-Leadership (untypisch)",  "self-leadership"),
    ("Exile (engl., untypisch)",     "exile"),
    ("Feuerwehr-Anteil (untypisch)", "feuerwehr"),
    # v19.6 (#7): weitere englische IFS-Begriffe, die die sysTelios-Klinik NIE
    # nutzt -> bei Auftreten praktisch immer aufgestuelpt. Wortgrenze schuetzt
    # (kommen im Deutschen nicht als Teilwort vor; 'protector' != dt. 'Protektor').
    ("Firefighter (engl., untypisch)", "firefighter"),
    ("Protector (engl., untypisch)",   "protector"),
    ("Manager-Part (engl., untypisch)", "manager-part"),
]

# BEWUSST NICHT in der Liste (Stand v19.5, datengestuetzt): generische Verfahrens-/
# Konzept-Label (IFS, Reframing, Externalisierung, Hypnosystemik, Schema-Modus, EMDR,
# Stuhlarbeit, zirkulaere Frage) und generische Anteils-Kategorien (Schutzanteil,
# Schutzschild, Ego-State/Ich-Zustand). Grund: das sind Zusammenfassungs-Vokabeln -
# Kliniker benennen damit, was passiert ist, oft mit anderem Wort als die Quelle
# ('IFS' fuer 'Anteilearbeit', 'Schutzanteil' fuer 'Waechteranteil', 'Reframing' fuer
# Glaubenssatz-Arbeit). Binaeres Flaggen erzeugt nur Rauschen - im Baseline 16384 waren
# ALLE drei Treffer (IFS, Reframing, Schutzanteil) belegte Konzepte, nur anders
# formuliert. Ueber-Nutzung von Methodensprache wird im Prompt gesteuert (Item-1-Gating
# in prompts.py), nicht per Eval-Flag. Ego-State bleibt im KLINISCHES_GLOSSAR als
# erkanntes Vokabular - nur eben nicht in der Fidelity-Pruefung.

# Standard-Hausaufgaben, die das Modell gern erfindet, wenn keine konkrete
# Einladung vereinbart wurde.
HOMEWORK_TERMS: list[tuple[str, str]] = [
    ("Notizbuch",                    "notizbuch"),
    ("Tagebuch",                     "tagebuch"),
    ("Journal",                      "journal"),
    ("Achtsamkeitsuebung",           "achtsamkeitsüb"),
    ("aufschreiben/notieren",        "aufschreib"),
    # v19.6 (#7): weitere Standard-Hausaufgaben-Artefakte, die das Modell gern
    # erfindet. Spezifische Komposita (bewusst KEIN bare 'protokoll' -> kollidiert
    # mit legitimer Therapie-/Verlaufsdoku).
    ("Arbeitsblatt",                 "arbeitsblatt"),
    ("Stimmungstagebuch",            "stimmungstagebuch"),
    ("Gedankenprotokoll",            "gedankenprotokoll"),
    ("Wochenprotokoll",              "wochenprotokoll"),
]


def _stem_present(stem: str, text_lo: str) -> bool:
    """Wortstamm an einer WORTGRENZE in text_lo (lower-case)?

    Wortgrenze vorne (\\b) verhindert Substring-Fehltreffer - z.B. 'richter' in
    'Berichterstattung', frueher auch 'ifs' in 'Tarifs'. Das Suffix bleibt frei,
    damit Flexionen matchen ('verbannt' -> 'Verbannte'/'Verbannten', 'manager' ->
    'Managerin'). Gilt fuer Output UND Quelle gleich."""
    return re.search(r"\b" + re.escape(stem), text_lo) is not None


def find_imposed_vocab(output_text: str, source_text: str) -> list[str]:
    """Labels, deren Wortstamm im Output, aber NICHT in der Quelle vorkommt.

    Verfahrensbegriffe + Hausaufgaben in einer Liste (Hausaufgaben mit Praefix).
    Matching an Wortgrenzen (siehe _stem_present). Leere/zu duenne Quelle -> []
    (nicht pruefbar; Aufrufer entscheidet, ob der Check ueberhaupt sinnvoll ist).
    """
    if not source_text or not source_text.strip():
        return []
    out_lo = output_text.lower()
    src_lo = source_text.lower()
    imposed = [
        label for label, stem in METHOD_TERMS
        if _stem_present(stem, out_lo) and not _stem_present(stem, src_lo)
    ]
    imposed += [
        f"Hausaufgabe '{label}'" for label, stem in HOMEWORK_TERMS
        if _stem_present(stem, out_lo) and not _stem_present(stem, src_lo)
    ]
    return imposed
