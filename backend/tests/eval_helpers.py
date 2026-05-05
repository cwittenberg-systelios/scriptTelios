"""
Hilfsfunktionen für Stilvorlagen-Verarbeitung im Eval-Setup.

Wird von tests/test_eval.py verwendet UND von tests/test_prompts_v16.py
importiert. Wichtig: Dieses Modul darf KEINE Fixtures laden oder andere
schwergewichtigen Imports am Modul-Top haben - sonst kann test_prompts_v16.py
es nicht importieren ohne das ganze Eval-Fixture-Setup mitzuziehen.

v13 Strategie 3: Multi-Vorlagen-Unterstützung pro Testcase.
"""
from pathlib import Path
import logging
import re

logger = logging.getLogger(__name__)


def discover_style_siblings(primary_path: Path) -> list:
    """
    Multi-Vorlagen-Erkennung pro Testcase.

    Wenn primary_path = "vorlage.txt" (oder "vorlage.docx"), findet diese
    Funktion alle Geschwister-Dateien gleichen Stamms im selben Verzeichnis:
        vorlage.txt, vorlage2.txt, vorlage3.txt, ...
        vorlage.docx, vorlage2.docx, ...
        vorlage_01.txt, vorlage_02.txt, ...
        beispiel.txt, beispiel2.txt, ...

    Pattern: <stem>[<sep>?<digits>]?<suffix>  wobei <sep> in {"", "_", "-"}.
    Returns:
      Sortierte Liste der gefundenen Dateien (primary zuerst, dann nach
      Nummer aufsteigend). Mindestens [primary_path] wenn nichts anderes.
      Leere Liste wenn primary_path nicht existiert.
    """
    if not primary_path.exists():
        return []
    parent = primary_path.parent
    stem = primary_path.stem.lower()
    suffix = primary_path.suffix.lower()

    # Stamm ohne abschließende Ziffern/Trenner extrahieren.
    # "vorlage" → "vorlage", "vorlage2" → "vorlage", "vorlage_01" → "vorlage"
    m = re.match(r"^(.*?)[_\-]?\d*$", stem)
    base_stem = m.group(1) if m and m.group(1) else stem

    siblings = []
    for f in parent.iterdir():
        if not f.is_file() or f.suffix.lower() != suffix:
            continue
        fstem = f.stem.lower()
        # Match: base_stem (exakt) ODER base_stem + opt. Trenner + Ziffern
        sm = re.match(rf"^{re.escape(base_stem)}([_\-]?(\d+))?$", fstem)
        if sm:
            num = int(sm.group(2)) if sm.group(2) else 1
            siblings.append((num, f))

    if not siblings:
        return [primary_path]

    # Nach Nummer sortieren (1, 2, 3, ...)
    siblings.sort(key=lambda t: t[0])
    return [f for _, f in siblings]


def build_multi_style_text(paths: list, docx_extractor=None) -> str:
    """
    Verkettet mehrere Stilvorlagen mit dem Marker-Format, das
    app.services.prompts.split_style_examples wieder splitten kann.

    Format identisch zu retrieve_style_examples (embeddings.py):
        --- Beispiel 1 ---
        <Inhalt vorlage.txt>

        --- Beispiel 2 ---
        <Inhalt vorlage2.txt>

    Bei nur EINEM Pfad wird KEIN Marker eingefügt (Backwards-Compat:
    Single-Style-Pfad bleibt im Wortlaut wie vorher).

    Args:
      paths: Liste von Path-Objekten (.txt oder .docx)
      docx_extractor: optional callable(Path) -> str für DOCX-Extraktion.
        Wenn None und ein .docx ist dabei, wird die Datei übersprungen.
        Erlaubt Tests ohne python-docx-Abhängigkeit.
    """
    if not paths:
        return ""

    contents = []
    for p in paths:
        try:
            if p.suffix.lower() == ".txt":
                txt = p.read_text(encoding="utf-8").strip()
            elif p.suffix.lower() in (".docx", ".doc"):
                if docx_extractor is None:
                    logger.warning(
                        "DOCX %s übersprungen: kein docx_extractor übergeben",
                        p,
                    )
                    continue
                txt = docx_extractor(p).strip()
            else:
                txt = ""
            if txt:
                contents.append(txt)
        except Exception as e:
            logger.warning("Stilvorlage nicht lesbar: %s (%s)", p, e)

    if not contents:
        return ""
    if len(contents) == 1:
        # Single-Vorlage: kein Marker (Backwards-Compat)
        return contents[0]

    # Multi-Vorlage: Marker im Format aus retrieve_style_examples
    parts = [
        f"--- Beispiel {i} ---\n{txt}"
        for i, txt in enumerate(contents, 1)
    ]
    return "\n\n".join(parts)
