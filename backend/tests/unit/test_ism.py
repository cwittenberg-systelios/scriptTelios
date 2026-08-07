"""
v19.18 (PX): Tests fuer den ISM-Fragebogen-Workflow.

Deckt ab:
  - Registry-Sync (workflows.py, prompts.py, config.py, DOKUMENTTYPEN)
  - clamp_n_items-Grenzen
  - JSON-Schema-Form (Ollama format-Parameter)
  - Prompt-Builder (Kernel + Instructions + Faktoren + Itemanzahl,
    keine konkurrierenden Laengen-Anker-Tokens)
  - validate_ism_payload (valide / hard errors)
  - run_ism_quality_check (alle Issue-Codes + Dispatch aus run_quality_check)
  - render_sns_xml Golden-Test: Strukturgleichheit zur SNS-Referenz
    (BeispielOutput.xml der Prozessdiagnostik) via ElementTree-Assertions,
    CDATA-Escaping, Attribut-Set, Faktorblock
  - POST /api/ism/xml (Endpoint via TestClient)
"""
import json
import re
import xml.etree.ElementTree as ET

import pytest

from app.services.ism import (
    ISM_DEFAULT_ITEMS,
    ISM_FAKTOREN,
    ISM_MAX_ITEMS,
    ISM_MIN_ITEMS,
    IsmFragebogen,
    IsmItem,
    build_ism_json_schema,
    build_ism_system_prompt,
    build_ism_user_content,
    clamp_n_items,
    render_sns_xml,
    run_ism_quality_check,
    validate_ism_payload,
)


def _valid_payload(n_items: int = 6) -> dict:
    """Baut ein strukturell valides LLM-Payload mit n Items ueber die
    Faktoren 0..min(n,6)-1 verteilt."""
    items = []
    for i in range(n_items):
        items.append({
            "faktor_id": i % 6,
            "frage": f"Heute konnte ich Schritt {i + 1} für mich gehen.",
            "pol_min": "ich übe noch...",
            "pol_max": f"...und Schritt {i + 1} ist gelungen",
        })
    return {
        "begruessung": "Hallo, schön dass du dir Zeit für dich nimmst.",
        "verabschiedung": "Danke - einen guten Abend dir.",
        "items": items,
    }


# ══════════════════════════════════════════════════════════════════
# Registry-Sync
# ══════════════════════════════════════════════════════════════════

class TestIsmRegistry:

    def test_workflow_registriert(self):
        from app.core.workflows import WORKFLOW_BY_KEY, WORKFLOW_KEYS
        assert "ism_fragebogen" in WORKFLOW_KEYS
        spec = WORKFLOW_BY_KEY["ism_fragebogen"]
        assert spec.label == "ISM-Fragebogen"
        assert spec.is_structural is False

    def test_dokumenttypen_und_labels(self):
        from app.models.db import DOKUMENTTYPEN, DOKUMENTTYP_LABELS
        assert "ism_fragebogen" in DOKUMENTTYPEN
        assert DOKUMENTTYP_LABELS["ism_fragebogen"] == "ISM-Fragebogen"

    def test_prompt_registry_eintraege(self):
        from app.services.prompts import (
            BASE_PROMPTS,
            STRUCTURAL_WORKFLOWS,
            WORKFLOW_INSTRUCTIONS_DEFAULT,
        )
        assert "ism_fragebogen" in BASE_PROMPTS
        assert len(BASE_PROMPTS["ism_fragebogen"]) > 100
        assert "ism_fragebogen" in WORKFLOW_INSTRUCTIONS_DEFAULT
        assert len(WORKFLOW_INSTRUCTIONS_DEFAULT["ism_fragebogen"]) > 100
        # ISM ist KEIN struktureller Workflow (keine Stilschablone)
        assert "ism_fragebogen" not in STRUCTURAL_WORKFLOWS

    def test_modell_routing_gemma(self):
        from app.core.config import settings
        assert settings.model_for_workflow("ism_fragebogen") == "gemma4:31b"

    def test_faktoren_konstante(self):
        assert len(ISM_FAKTOREN) == 6
        assert [f["id"] for f in ISM_FAKTOREN] == [0, 1, 2, 3, 4, 5]
        assert ISM_FAKTOREN[0]["name"] == "I Zielerleben"
        assert ISM_FAKTOREN[5]["name"] == "VI Utilisierung"


# ══════════════════════════════════════════════════════════════════
# clamp_n_items
# ══════════════════════════════════════════════════════════════════

class TestClampNItems:

    @pytest.mark.parametrize("raw,expected", [
        (None, ISM_DEFAULT_ITEMS),
        (6, 6),
        (4, 4),
        (12, 12),
        (3, ISM_MIN_ITEMS),
        (1, ISM_MIN_ITEMS),
        (13, ISM_MAX_ITEMS),
        (99, ISM_MAX_ITEMS),
        ("8", 8),
        ("quatsch", ISM_DEFAULT_ITEMS),
    ])
    def test_grenzen(self, raw, expected):
        assert clamp_n_items(raw) == expected


# ══════════════════════════════════════════════════════════════════
# Schema + Prompt-Builder
# ══════════════════════════════════════════════════════════════════

class TestIsmPrompts:

    def test_schema_form(self):
        s = build_ism_json_schema()
        assert s["type"] == "object"
        assert set(s["required"]) == {"begruessung", "verabschiedung", "items"}
        item_props = s["properties"]["items"]["items"]["properties"]
        assert set(item_props) == {"faktor_id", "frage", "pol_min", "pol_max"}
        # Bewusst keine Array-Kardinalitaeten (Ollama-Grammatik-Kompat.)
        assert "minItems" not in s["properties"]["items"]
        assert "maxItems" not in s["properties"]["items"]

    def test_system_prompt_komposition(self):
        p = build_ism_system_prompt(None, 6)
        # Kernel-Marker
        assert "AUSGABEFORM" in p
        assert "QUELLENREGEL" in p
        # Alle 6 Faktoren benannt
        for f in ISM_FAKTOREN:
            assert f["name"] in p
        # Itemanzahl-Anweisung
        assert "GENAU 6 Items" in p
        # JSON-only-Schluss
        assert "AUSSCHLIESSLICH" in p

    def test_system_prompt_custom_instructions(self):
        p = build_ism_system_prompt("MEINE SPEZIALANWEISUNG XYZ", 9)
        assert "MEINE SPEZIALANWEISUNG XYZ" in p
        assert "GENAU 9 Items" in p

    def test_keine_konkurrierenden_laengen_anker(self):
        """Regression gegen test_v13_single_length_instruction_per_workflow:
        Weder BASE_PROMPT noch INSTRUCTIONS_DEFAULT duerfen Anker-Tokens
        enthalten - build_system_prompt haengt den einzigen ZIELLAENGE-Block
        selbst an."""
        anchor_token_pattern = re.compile(
            r'(?:^|\n)\s*(?:ZIELLÄNGE|VERBINDLICHES TEXTLIMIT|LÄNGE:\s'
            r'|Mindestens \d+ Wörter|Richtwert \d)',
        )
        from app.services.prompts import (
            BASE_PROMPTS, WORKFLOW_INSTRUCTIONS_DEFAULT,
        )
        assert not anchor_token_pattern.findall(BASE_PROMPTS["ism_fragebogen"])
        assert not anchor_token_pattern.findall(
            WORKFLOW_INSTRUCTIONS_DEFAULT["ism_fragebogen"])

    def test_build_system_prompt_generisch_funktioniert(self):
        """Registry-Konsistenz: der generische Builder darf den Workflow
        bauen (Sync-Tests iterieren ueber WORKFLOW_KEYS)."""
        from app.services.prompts import build_system_prompt
        p = build_system_prompt(workflow="ism_fragebogen",
                                word_limits=(50, 400))
        assert "AUSGABEFORM" in p
        assert p.count("ZIELLÄNGE") == 1

    def test_user_content(self):
        u = build_ism_user_content("T: Wie geht es Ihnen?\nK: Besser.",
                                   themen="- Abgrenzung\n- Schlaf")
        assert "THERAPIEGESPRÄCH" in u
        assert "Abgrenzung" in u
        u2 = build_ism_user_content("Transkript.")
        assert "THEMEN" not in u2


# ══════════════════════════════════════════════════════════════════
# Validierung
# ══════════════════════════════════════════════════════════════════

class TestValidateIsmPayload:

    def test_valide(self):
        fb, errors = validate_ism_payload(_valid_payload(6), 6)
        assert errors == []
        assert isinstance(fb, IsmFragebogen)
        assert len(fb.items) == 6

    def test_kein_dict(self):
        fb, errors = validate_ism_payload(["liste"], 6)
        assert fb is None
        assert errors and "JSON-Objekt" in errors[0]

    def test_fehlende_felder(self):
        payload = _valid_payload()
        del payload["items"][0]["pol_max"]
        fb, errors = validate_ism_payload(payload, 6)
        assert fb is None
        assert any("pol_max" in e for e in errors)

    def test_faktor_id_ausserhalb(self):
        payload = _valid_payload()
        payload["items"][2]["faktor_id"] = 7
        fb, errors = validate_ism_payload(payload, 6)
        assert fb is None
        assert any("faktor_id" in e or "7" in e for e in errors)

    def test_abweichende_itemanzahl_ist_kein_hard_error(self):
        """D1c: Itemanzahl-Abweichung ist weich (Log + Vorschau-Editierung),
        kein Abbruchgrund."""
        fb, errors = validate_ism_payload(_valid_payload(5), 6)
        assert errors == []
        assert fb is not None and len(fb.items) == 5


# ══════════════════════════════════════════════════════════════════
# QualityCheck
# ══════════════════════════════════════════════════════════════════

class TestIsmQualityCheck:

    def test_valide_ohne_issues_ausser_info(self):
        issues = run_ism_quality_check(
            json.dumps(_valid_payload(6), ensure_ascii=False))
        # 6 Items ueber 6 Faktoren -> volle Abdeckung, keine Issues
        assert issues == []

    def test_json_invalid_critical(self):
        issues = run_ism_quality_check("kein json {{{")
        assert len(issues) == 1
        assert issues[0].code == "ISM_JSON_INVALID"
        assert issues[0].severity == "critical"

    def test_pole_identisch(self):
        payload = _valid_payload()
        payload["items"][1]["pol_max"] = payload["items"][1]["pol_min"]
        issues = run_ism_quality_check(json.dumps(payload, ensure_ascii=False))
        assert any(i.code == "ISM_POLE_IDENTISCH" for i in issues)

    def test_wir_form(self):
        payload = _valid_payload()
        payload["items"][0]["frage"] = "Heute haben wir uns gut abgegrenzt."
        issues = run_ism_quality_check(json.dumps(payload, ensure_ascii=False))
        assert any(i.code == "ISM_WIR_FORM" for i in issues)

    def test_duplikat(self):
        payload = _valid_payload()
        payload["items"][3]["frage"] = payload["items"][0]["frage"]
        issues = run_ism_quality_check(json.dumps(payload, ensure_ascii=False))
        assert any(i.code == "ISM_ITEM_DUPLIKAT" for i in issues)

    def test_faktor_unbesetzt_info(self):
        payload = _valid_payload(4)  # deckt nur Faktoren 0-3
        issues = run_ism_quality_check(json.dumps(payload, ensure_ascii=False))
        infos = [i for i in issues if i.code == "ISM_FAKTOR_UNBESETZT"]
        assert len(infos) == 1
        assert infos[0].severity == "info"
        assert infos[0].code_detail["uncovered_factor_ids"] == [4, 5]

    def test_dispatch_aus_run_quality_check(self):
        """Der zentrale run_quality_check delegiert fuer ISM an den
        strukturellen QC statt der Fliesstext-Checks (kein LENGTH_TOO_SHORT
        fuer kompaktes JSON, kein SOURCE_FIDELITY gegen das Transkript)."""
        from app.services.quality_check import run_quality_check
        text = json.dumps(_valid_payload(6), ensure_ascii=False)
        issues = run_quality_check(
            text, "ism_fragebogen",
            source_text="Voellig anderes Transkript ohne Item-Woerter.",
        )
        codes = {i.code for i in issues}
        assert all(c.startswith("ISM_") for c in codes)
        assert "LENGTH_TOO_SHORT" not in codes
        assert not any(c.startswith("SOURCE_FIDELITY") for c in codes)

    def test_dispatch_leerer_text_bleibt_generisch(self):
        from app.services.quality_check import run_quality_check
        issues = run_quality_check("", "ism_fragebogen")
        assert len(issues) == 1
        assert issues[0].severity == "critical"


# ══════════════════════════════════════════════════════════════════
# XML-Renderer (Golden-Test gegen SNS-Referenzstruktur)
# ══════════════════════════════════════════════════════════════════

class TestRenderSnsXml:

    def _render(self, n_items: int = 6) -> str:
        fb = IsmFragebogen.model_validate(_valid_payload(n_items))
        return render_sns_xml(fb, "MJ28585IND individualisiert")

    def test_wohlgeformt_und_wurzelattribute(self):
        xml = self._render()
        root = ET.fromstring(xml)
        assert root.tag == "questionnaire"
        # Wurzelattribute exakt wie SNS-Referenz (BeispielOutput.xml)
        assert root.attrib == {
            "type": "PROCESS",
            "allowComments": "true",
            "randomize": "true",
            "defaultLanguage": "de",
            "languages": "de",
            "securityType": "PUBLIC",
        }

    def test_kopfbloecke_reihenfolge(self):
        root = ET.fromstring(self._render())
        tags = [c.tag for c in root]
        assert tags == [
            "name", "welcomeText", "goodbyeText", "instruction",
            "description", "factors", "questions", "categories",
        ]
        assert root.find("name").text == "MJ28585IND individualisiert"
        # Begruessung als <p>-HTML im welcomeText
        assert "<p>" in root.find("welcomeText/label").text

    def test_faktorblock_statisch(self):
        root = ET.fromstring(self._render())
        factors = root.findall("factors/factor")
        assert len(factors) == 6
        for i, f in enumerate(factors):
            assert f.attrib == {
                "id": str(i), "color": "#0A4B71", "operation": "SUM",
            }
            assert f.find("name/label").text == ISM_FAKTOREN[i]["name"]
            assert f.find("name/label").attrib == {"lang": "de"}
            assert f.find("description/label").text \
                == ISM_FAKTOREN[i]["beschreibung_xml"]

    def test_question_attribute_und_pole(self):
        root = ET.fromstring(self._render())
        questions = root.findall("questions/question")
        assert len(questions) == 6
        for q in questions:
            assert q.attrib["type"] == "SLIDER"
            assert q.attrib["allowComments"] == "false"
            assert q.attrib["positionLocked"] == "false"
            assert q.attrib["weights"] == "1.0"
            assert q.attrib["changePoles"] == "false"       # A4
            assert q.attrib["factors"] in {"0", "1", "2", "3", "4", "5"}
            assert q.find("min").attrib == {"value": "0"}    # A5
            assert q.find("max").attrib == {"value": "100"}
            assert q.find("title/label").text
            assert q.find("min/label").text
            assert q.find("max/label").text

    def test_items_nach_faktor_sortiert(self):
        payload = _valid_payload(6)
        # Absichtlich unsortiert
        payload["items"] = list(reversed(payload["items"]))
        fb = IsmFragebogen.model_validate(payload)
        root = ET.fromstring(render_sns_xml(fb, "X"))
        fids = [int(q.attrib["factors"])
                for q in root.findall("questions/question")]
        assert fids == sorted(fids)

    def test_cdata_escaping(self):
        """']]>' im Inhalt darf das CDATA nicht sprengen; Sonderzeichen
        ueberleben das Parsen unveraendert."""
        payload = _valid_payload(4)
        payload["items"][0]["frage"] = "Heute war <alles> ]]> & mehr ok."
        fb = IsmFragebogen.model_validate(payload)
        xml = render_sns_xml(fb, "X")
        root = ET.fromstring(xml)  # muss parsebar bleiben
        titles = [q.find("title/label").text
                  for q in root.findall("questions/question")]
        assert "Heute war <alles> ]]> & mehr ok." in titles

    def test_struktur_kompatibel_zur_referenzprobe(self):
        """Golden-Probe: dieselben Pfade, die die SNS-Referenz nutzt,
        existieren im Renderer-Output (Import-Kompatibilitaet)."""
        referenz_pfade = [
            "name", "welcomeText/label", "goodbyeText/label",
            "instruction/label", "description/label",
            "factors/factor/name/label", "factors/factor/description/label",
            "questions/question/title/label",
            "questions/question/min/label", "questions/question/max/label",
            "categories",
        ]
        root = ET.fromstring(self._render())
        for pfad in referenz_pfade:
            assert root.find(pfad) is not None, f"Pfad fehlt: {pfad}"


# ══════════════════════════════════════════════════════════════════
# Endpoint POST /api/ism/xml
# ══════════════════════════════════════════════════════════════════

class TestIsmXmlEndpoint:

    @pytest.fixture()
    def client(self):
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as c:
            yield c

    def test_xml_export(self, client):
        r = client.post("/api/ism/xml", json={
            "name": "MJ28585IND individualisiert",
            "fragebogen": _valid_payload(5),
        })
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["filename"] == "MJ28585INDindividualisiert.xml"
        root = ET.fromstring(data["xml"])
        assert root.find("name").text == "MJ28585IND individualisiert"
        assert len(root.findall("questions/question")) == 5

    def test_invalide_payload_422(self, client):
        r = client.post("/api/ism/xml", json={
            "name": "X",
            "fragebogen": {"begruessung": "Hi", "verabschiedung": "Bye",
                            "items": []},
        })
        assert r.status_code == 422
