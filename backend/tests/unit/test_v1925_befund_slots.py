"""v19.25 Sprint B: Structured-Befund ohne Satzfragmente.

Regressionsbasis: prompts.log 15.09.2026, Job 7d548664 (l.falter) - der
Befund enthielt 'nicht erhoben. reduziert. ausgeprägt. nein.' als eigene
Saetze. B1: Standalone-Slots ohne Wert weglassen; B2: Schema-Descriptions;
B3: Nachbarwort-Duplikate; B4: QC BEFUND_FRAGMENT.
"""
import json
import re

from app.services import quality_check as QC
from app.services.prompts import (
    BEFUND_VORLAGE,
    befund_value_is_empty,
    build_befund_slot_schema,
    build_befund_structured_prompt,
    classify_befund_slots,
    fill_befund_vorlage,
    parse_befund_slots,
)

# Slot-Werte des Logs vom 15.09.2026 (Job 7d548664), 1:1 rekonstruiert.
LOG_VALUES_20260915 = {
    "konzentration": "subjektiv eingeschränkt",
    "formalgedanke": "grübelnd, eingeengt",
    "fokus_denken": "mit Fokus auf berufliche Anforderungen und Schülerverhalten",
    "phobien_angst": "keine spezifischen Phobien, aber allgemeine Angst vor beruflicher Überforderung",
    "Zwänge": "nicht erhoben",
    "vermeidung": "Vermeidung von beruflichen Konfliktsituationen",
    "stimmung": "depressiv gedrückt",
    "schwingung": "eingeschränkt",
    "affektlage": "labil",
    "freud_interessen": "reduziert",
    "erschöpfung": "ausgeprägt",
    "antrieb": "vermindert",
    "hoffnung_insuffizienz": "Hoffnungslosigkeit bezüglich beruflicher Situation",
    "schuldgefühle": "nicht erwähnt",
    "selbstwert": "erniedrigt",
    "gefühlsregulation": "gestört",
    "impulskontrolle": "nicht beeinträchtigt",
    "ambivalenz": "nicht erwähnt",
    "innere_unruhe": "erhöht",
    "zirkadian": "nicht erhoben",
    "schlaf": "Durchschlafstörungen",
    "appetenz": "Appetenz Appetitlosigkeit",
    "aggressiv_selbstverletzend": "kein Anhalt für aggressive oder selbstverletzende Impulse",
    "sozialer_rückzug": "tendenziell sozial isoliert im Berufskontext",
    "essverhalten": "Essattacken als Bewältigungsstrategie",
    "suchtverhalten": "nicht erwähnt",
    "somatisierung": "vegetative Symptome wie Durchschlafstörungen und Appetitlosigkeit",
    "suizidalität_vergangenheit": "nein",
}

_SENT = re.compile(r"(?<=[.!?])\s+")


def _fragments(text: str) -> list[str]:
    out = []
    for s in _SENT.split(text):
        s = s.strip()
        if not s:
            continue
        w = s.rstrip(".").split()
        if len(w) == 1 or s[:1].islower() or s.rstrip(".").lower() in ("nicht erhoben", "nicht erwähnt", "nein"):
            out.append(s)
    return out


class TestClassify:

    def test_default_vorlage(self):
        kinds = classify_befund_slots(BEFUND_VORLAGE)
        assert kinds["Zwänge"] == "standalone"
        assert kinds["freud_interessen"] == "standalone"
        assert kinds["suizidalität_vergangenheit"] == "standalone"
        assert kinds["antrieb"] == "inline"
        assert kinds["konzentration"] == "inline"
        assert kinds["affektlage"] == "inline"

    def test_textanfang_ist_standalone(self):
        assert classify_befund_slots("{a}. Dann {b}.") == {"a": "standalone", "b": "inline"}


class TestEmptyValues:

    def test_varianten(self):
        for v in ("", None, "nicht erhoben", "Nicht erhoben.", "nicht erwähnt",
                  "nicht erhoben (keine Angabe)", "-", "k.A.", "unbekannt"):
            assert befund_value_is_empty(v), v

    def test_nein_ist_kein_leerwert(self):
        assert not befund_value_is_empty("nein")
        assert not befund_value_is_empty("vermindert")


class TestFillB1:

    def test_log_15_09_ohne_fragmente(self):
        out = fill_befund_vorlage(BEFUND_VORLAGE, LOG_VALUES_20260915)
        assert _fragments(out) == [], _fragments(out)
        assert "nicht erhoben" not in out
        assert "nicht erwähnt" not in out
        assert ". ." not in out and ".." not in out
        assert "  " not in out
        # Fixtext bleibt erhalten
        assert out.startswith("Im Gespräch offen, wach, bewusstseinsklar")
        assert out.endswith("von akuter Suizidalität klar distanziert.")
        # Kurzwerte bekommen ein Label
        assert "Freude und Interessen: reduziert." in out
        assert "Erschöpfung: ausgeprägt." in out
        assert "Innere Unruhe: erhöht." in out
        assert "Suizidalität in der Vorgeschichte: verneint." in out
        # Standalone-Saetze beginnen gross
        assert "Keine spezifischen Phobien" in out

    def test_standalone_leer_wird_samt_punkt_entfernt(self):
        assert fill_befund_vorlage("{a}. Text {b}. {c}.", {"a": "nicht erhoben", "b": "x", "c": "Voller Satz hier"}) \
            == "Text x. Voller Satz hier."
        assert fill_befund_vorlage("Fest. {a}. Ende.", {"a": ""}) == "Fest. Ende."

    def test_inline_leer_bleibt_nicht_erhoben(self):
        # bisheriges Verhalten (v19.7) fuer Inline-Slots unveraendert
        assert fill_befund_vorlage("A {x}. B {y}.", {"x": "wert"}) == "A wert. B nicht erhoben."

    def test_standalone_wert_mit_eigenem_punkt_nicht_verdoppelt(self):
        assert fill_befund_vorlage("Fest. {a}. Ende.", {"a": "Keine Zwänge."}) == "Fest. Keine Zwänge. Ende."

    def test_kein_slot_rest(self):
        out = fill_befund_vorlage(BEFUND_VORLAGE, {})
        for s in parse_befund_slots(BEFUND_VORLAGE):
            assert "{" + s + "}" not in out
        assert _fragments(out) == []


class TestDedupeB3:

    def test_nachbarwort_vorne(self):
        assert fill_befund_vorlage("Konzentration subjektiv {k}.", {"k": "subjektiv eingeschränkt"}) \
            == "Konzentration subjektiv eingeschränkt."

    def test_nachbarwort_hinten(self):
        assert fill_befund_vorlage("bei insgesamt {a} Affektlage.", {"a": "labiler Affektlage"}) \
            == "bei insgesamt labiler Affektlage."

    def test_label_wiederholung(self):
        assert fill_befund_vorlage("Appetenz {ap}.", {"ap": "Appetenz Appetitlosigkeit"}) \
            == "Appetenz Appetitlosigkeit."

    def test_einzelwort_bleibt(self):
        assert fill_befund_vorlage("Antrieb {a}.", {"a": "Antrieb"}) == "Antrieb Antrieb."

    def test_kurzwert_label_regeln(self):
        assert fill_befund_vorlage("F. {s}. E.", {"s": "deutlich erhöht"}) == "F. S: deutlich erhöht. E."
        assert fill_befund_vorlage("F. {s}. E.", {"s": "Keine Zwänge"}) == "F. Keine Zwänge. E."


class TestSchemaB2:

    def test_descriptions_nach_slot_art(self):
        slots = parse_befund_slots(BEFUND_VORLAGE)
        schema = build_befund_slot_schema(slots, BEFUND_VORLAGE)
        assert schema["required"] == slots
        assert "vollstaendiger Satz" in schema["properties"]["Zwänge"]["description"]
        d = schema["properties"]["antrieb"]["description"]
        assert "NUR der fehlende Wert" in d and "Antrieb [WERT]." in d
        assert "{" not in d  # andere Slots im Kontext neutralisiert
        json.dumps(schema, ensure_ascii=False)

    def test_ohne_vorlage_wie_bisher(self):
        assert build_befund_slot_schema(["a"]) == {
            "type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"],
        }

    def test_prompt_regeln(self):
        sys_p, slots, schema = build_befund_structured_prompt(befund_vorlage=None)
        assert "EIGENER Satz" in sys_p
        assert "vom System weggelassen" in sys_p
        assert "description" in schema["properties"]["antrieb"]


class TestQCB4:

    LOG_BEFUND = (
        "Im Gespräch offen, wach. Formalgedanklich grübelnd, eingeengt. "
        "keine spezifischen Phobien. nicht erhoben. Vermeidung von Konflikten. "
        "reduziert. ausgeprägt. Antrieb vermindert. nicht erwähnt. erhöht. nein. "
        "Zum Zeitpunkt der Aufnahme von akuter Suizidalität klar distanziert."
    )

    def test_log_befund_erkannt(self):
        text = "Anamnese-Text.\n\n" + QC.BEFUND_SEPARATOR + "\n\n" + self.LOG_BEFUND
        issues = [i for i in QC.run_quality_check(text, "anamnese") if i.code == QC.ISSUE_CODE_BEFUND_FRAGMENT]
        assert len(issues) == 1
        assert issues[0].severity == QC.SEVERITY_WARNING
        assert issues[0].code_detail["count"] >= 6
        assert "nicht erhoben." in issues[0].code_detail["fragments"]

    def test_sauberer_befund_still(self):
        clean = fill_befund_vorlage(BEFUND_VORLAGE, LOG_VALUES_20260915)
        text = "Anamnese.\n\n" + QC.BEFUND_SEPARATOR + "\n\n" + clean
        assert [i for i in QC.run_quality_check(text, "anamnese") if i.code == QC.ISSUE_CODE_BEFUND_FRAGMENT] == []

    def test_abkuerzungen_kein_fehlalarm(self):
        text = "A.\n\n" + QC.BEFUND_SEPARATOR + "\n\nKeine Ich-Störungen (z.B. Depersonalisation). Antrieb vermindert."
        assert [i for i in QC.run_quality_check(text, "anamnese") if i.code == QC.ISSUE_CODE_BEFUND_FRAGMENT] == []

    def test_nur_anamnese(self):
        assert [i for i in QC.run_quality_check("reduziert. erhöht. nein.", "dokumentation")
                if i.code == QC.ISSUE_CODE_BEFUND_FRAGMENT] == []
