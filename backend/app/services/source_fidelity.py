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


# (Anzeige-Label, Wortstamm-zum-Suchen in lower-case)
METHOD_TERMS: list[tuple[str, str]] = [
    # Klinik-Vokabular (deutsch) - nur belegt, wenn in der Quelle
    ("Manager",                      "manager"),
    ("Antreiber",                    "antreiber"),
    ("Richter",                      "richter"),
    ("Feuerbekämpfer",               "feuerbekämpf"),
    ("Verbannte",                    "verbannt"),
    ("inneres Kind",                 "inneres kind"),
    ("Ego-State",                    "ego-state"),
    ("Ich-Zustand",                  "ich-zustand"),
    ("Schutzanteil",                 "schutzanteil"),
    ("Schutzschild",                 "schutzschild"),
    ("Hypnosystemik",                "hypnosystem"),
    ("Schema-Modus",                 "schema-modus"),
    ("EMDR",                         "emdr"),
    ("Reframing",                    "reframing"),
    ("Externalisierung",             "external"),
    ("zirkulaere Frage",             "zirkul"),
    ("Stuhlarbeit",                  "stuhlarbeit"),
    ("IFS (Verfahrensname)",         "ifs"),
    # Englische/Fremdbegriffe, die die sysTelios-Klinik NICHT nutzt ->
    # bei Auftreten fast immer aufgestuelpt (auch die Quelle hat sie nie).
    ("Self-Energy (untypisch)",      "self-energy"),
    ("Self-Leadership (untypisch)",  "self-leadership"),
    ("Exile (engl., untypisch)",     "exile"),
    ("Feuerwehr-Anteil (untypisch)", "feuerwehr"),
]

# Standard-Hausaufgaben, die das Modell gern erfindet, wenn keine konkrete
# Einladung vereinbart wurde.
HOMEWORK_TERMS: list[tuple[str, str]] = [
    ("Notizbuch",                    "notizbuch"),
    ("Tagebuch",                     "tagebuch"),
    ("Journal",                      "journal"),
    ("Achtsamkeitsuebung",           "achtsamkeitsüb"),
    ("aufschreiben/notieren",        "aufschreib"),
]


def find_imposed_vocab(output_text: str, source_text: str) -> list[str]:
    """Labels, deren Wortstamm im Output, aber NICHT in der Quelle vorkommt.

    Verfahrensbegriffe + Hausaufgaben in einer Liste (Hausaufgaben mit Praefix).
    Leere/zu duenne Quelle -> [] (nicht pruefbar; Aufrufer entscheidet, ob der
    Check ueberhaupt sinnvoll ist).
    """
    if not source_text or not source_text.strip():
        return []
    out_lo = output_text.lower()
    src_lo = source_text.lower()
    imposed = [
        label for label, stem in METHOD_TERMS
        if stem in out_lo and stem not in src_lo
    ]
    imposed += [
        f"Hausaufgabe '{label}'" for label, stem in HOMEWORK_TERMS
        if stem in out_lo and stem not in src_lo
    ]
    return imposed
