"""v19.21 (S1): Drift-Schutz fuer die Workflow-Default-Prompts.

prompts.py ist die einzige Quelle; frontend/src/prompt-defaults.jsx wird per
backend/scripts/export_prompt_defaults.py daraus generiert. Dieser Test wird
rot, sobald jemand prompts.py aendert, ohne die .jsx neu zu erzeugen (oder
die .jsx von Hand editiert). Gleiche Pruefung laeuft in lint_gate.sh.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "export_prompt_defaults.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("export_prompt_defaults", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_generierte_jsx_entspricht_prompts_py():
    mod = _load_script()
    if not mod.TARGET.exists():
        pytest.skip("frontend/src/prompt-defaults.jsx nicht im Checkout (Backend-only-Deploy)")
    assert mod.TARGET.read_text(encoding="utf-8") == mod.render(), (
        "prompt-defaults.jsx ist nicht mehr synchron zu prompts.py - "
        "bitte `python3 backend/scripts/export_prompt_defaults.py` ausfuehren"
    )


def test_alle_workflows_im_export_und_im_manifest():
    from app.core.workflows import WORKFLOWS
    from app.services.prompts import WORKFLOW_INSTRUCTIONS_DEFAULT
    mod = _load_script()
    exported = {key for _, key in mod.WORKFLOW_CONSTANTS}
    assert exported == set(WORKFLOW_INSTRUCTIONS_DEFAULT.keys())
    # Jeder Frontend-Workflow mit Default-Anweisung landet im Manifest
    from app.api.workflow_manifest import list_workflows
    m = list_workflows()
    assert m["befund_vorlage"]
    for w in m["workflows"]:
        if w["key"] in WORKFLOW_INSTRUCTIONS_DEFAULT:
            assert w["instructions_default"] == WORKFLOW_INSTRUCTIONS_DEFAULT[w["key"]]
    assert {w.key for w in WORKFLOWS} >= exported


def test_template_literal_escaping():
    mod = _load_script()
    assert mod._js_template_literal("a`b${c}\\d") == "a\\`b\\${c}\\\\d"
