#!/usr/bin/env python3
"""
export_prompt_defaults.py - erzeugt frontend/src/prompt-defaults.jsx aus
app/services/prompts.py (v19.21, Sprint S1).

Hintergrund: Bis v19.20 gab es zwei handgepflegte Fassungen der Workflow-
Default-Anweisungen - eine im Backend (WORKFLOW_INSTRUCTIONS_DEFAULT, genutzt
von Eval/Tests/ISM) und eine im Frontend (prompt-defaults.jsx, das was
produktiv im LLM ankommt). Beide waren monatelang auseinandergelaufen
(Code-Review 2026-09-09, Befund 1.1). Seit S1 ist prompts.py die EINZIGE
Quelle; diese Datei wird daraus generiert und NICHT von Hand editiert.

Aufrufe (aus beliebigem Verzeichnis):
    python3 backend/scripts/export_prompt_defaults.py          # schreibt die .jsx
    python3 backend/scripts/export_prompt_defaults.py --check  # Exit 1 bei Drift

Der --check-Modus laeuft in backend/scripts/lint_gate.sh und im Unit-Test
tests/unit/test_prompt_defaults_sync.py; der Schreib-Modus als npm
"prebuild" vor jedem `vite build`.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
TARGET = REPO_DIR / "frontend" / "src" / "prompt-defaults.jsx"

# Reihenfolge = Reihenfolge im Export; Namen sind die historischen
# Frontend-Konstanten (Panels importieren sie weiterhin).
WORKFLOW_CONSTANTS: list[tuple[str, str]] = [
    ("P_DOKU",       "dokumentation"),
    ("P_ANAMNESE",   "anamnese"),
    ("P_VERL",       "verlaengerung"),
    ("P_VERL_FOLGE", "folgeverlaengerung"),
    ("P_AKUT",       "akutantrag"),
    ("P_ENTL",       "entlassbericht"),
    ("P_ISM",        "ism_fragebogen"),
]

HEADER = """\
// ────────────────────────────────────────────────────────────────────────────
// src/prompt-defaults.jsx — GENERIERT, NICHT VON HAND EDITIEREN.
//
// Quelle: backend/app/services/prompts.py
//         (WORKFLOW_INSTRUCTIONS_DEFAULT, BEFUND_VORLAGE)
// Generator: backend/scripts/export_prompt_defaults.py
//   - `npm run build` ruft ihn als "prebuild" auf,
//   - `lint_gate.sh --check` und tests/unit/test_prompt_defaults_sync.py
//     schlagen bei Drift fehl.
//
// Aenderungen an Default-Prompts bitte ausschliesslich in prompts.py.
// (v19.21 S1: vorher zwei handgepflegte, auseinandergelaufene Fassungen.)
// ────────────────────────────────────────────────────────────────────────────
"""


def _js_template_literal(text: str) -> str:
    """Escaped einen Text fuer ein JS-Template-Literal (Backticks, ${, Backslash)."""
    return (
        text.replace("\\", "\\\\")
            .replace("`", "\\`")
            .replace("${", "\\${")
    )


def render() -> str:
    sys.path.insert(0, str(BACKEND_DIR))
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    from app.services.prompts import BEFUND_VORLAGE, WORKFLOW_INSTRUCTIONS_DEFAULT

    parts = [HEADER, "\n"]
    for const, key in WORKFLOW_CONSTANTS:
        text = WORKFLOW_INSTRUCTIONS_DEFAULT[key]
        parts.append(f"// {key}\nconst {const} = `{_js_template_literal(text)}`;\n\n")
    parts.append(
        f"// Befundvorlage (Anamnese, Psychischer Befund)\n"
        f"const P_BEFUND_VORLAGE = `{_js_template_literal(BEFUND_VORLAGE)}`;\n\n"
    )
    names = [c for c, _ in WORKFLOW_CONSTANTS] + ["P_BEFUND_VORLAGE"]
    parts.append("export { " + ", ".join(names) + " };\n")
    return "".join(parts)


def main(argv: list[str]) -> int:
    content = render()
    if "--check" in argv:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current == content:
            print(f"[prompt-defaults] OK - {TARGET.relative_to(REPO_DIR)} entspricht prompts.py")
            return 0
        print(
            f"[prompt-defaults] DRIFT - {TARGET.relative_to(REPO_DIR)} weicht von "
            "prompts.py ab. Neu erzeugen mit:\n"
            "    python3 backend/scripts/export_prompt_defaults.py",
            file=sys.stderr,
        )
        return 1
    TARGET.write_text(content, encoding="utf-8")
    print(f"[prompt-defaults] geschrieben: {TARGET.relative_to(REPO_DIR)} ({len(content)} Zeichen)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
