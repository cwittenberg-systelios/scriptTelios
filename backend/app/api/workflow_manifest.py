"""
GET /api/workflows  - liefert das Workflow-Manifest ans Frontend.

Ist ein duenner HTTP-Adapter ueber das Datenmodell in
`app/core/workflows.py`. Datei heisst bewusst NICHT workflows.py, um
Verwechslung mit dem Core-Modul zu vermeiden:

  - app/core/workflows.py           = Datenmodell + Single Source of Truth
                                       (WORKFLOWS, WorkflowSpec, Lookups)
  - app/api/workflow_manifest.py    = HTTP-Endpoint (dieser File)

Frontend nutzt die ausgelieferte Liste fuer:
  - Dropdown-Auswahl (Dokumenttyp-Picker)
  - is_structural-Flag fuer Conditional-Rendering
    (z.B. zeige Stilbeispiel-Upload nur fuer strukturelle Workflows)
  - word_limit als UI-Anzeige ("Erwartete Laenge: 280-650 Woerter")
  - color_hex fuer Visualisierungen

Das Frontend sollte beim Start eine kurze GET-Anfrage machen und das
Manifest cachen - der Endpoint ist read-only und liefert immer dieselben
Daten zwischen Deployments.

Registrierung in app/main.py:
    from app.api import workflow_manifest
    app.include_router(workflow_manifest.router)
"""
from fastapi import APIRouter

from app.core.workflows import to_manifest

router = APIRouter()


@router.get("/api/workflows", tags=["workflows"])
def list_workflows() -> dict:
    """Liefert das Workflow-Manifest fuer das Frontend.

    Response-Struktur:
      {
        "workflows": [
          {
            "key": "anamnese",
            "label": "Anamnese",
            "short_label": "Anamnese",
            "is_structural": true,
            "word_limit": [280, 650],
            "max_tokens": 3000,
            "expected_tokens": 1500,
            "color_hex": "#2d7a3a",
            "instructions_default": "Erstelle eine vollständige ..."   # v19.21 S1
          },
          ...
        ],
        "befund_vorlage": "Im Gespräch offen, wach, ..."               # v19.21 S1
      }

    Wenn ein Workflow ergaenzt/umbenannt/umkalibriert wird, taucht das
    automatisch hier auf - keine separate Frontend-Aenderung noetig
    (ausser fuer workflow-spezifische UI-Logik).
    """
    # v19.21 (S1): Default-Anweisungen aus prompts.py mitliefern - dieselbe
    # Quelle, aus der frontend/src/prompt-defaults.jsx generiert wird. Damit
    # koennen Tools/Eval/Confluence-Makros den produktiven Default abfragen,
    # statt eine eigene Kopie zu pflegen. Lazy-Import: prompts.py ist gross.
    from app.services.prompts import BEFUND_VORLAGE, WORKFLOW_INSTRUCTIONS_DEFAULT
    workflows = to_manifest()
    for w in workflows:
        w["instructions_default"] = WORKFLOW_INSTRUCTIONS_DEFAULT.get(w["key"], "")
    return {"workflows": workflows, "befund_vorlage": BEFUND_VORLAGE}
