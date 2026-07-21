"""
Tests fuer v19.7 S1/S2: Structured Outputs (Ollama format=JSON-Schema).

S1 (llm.py):
  - generate_text mit response_format: JSON-Parsing, Parse-Error-Flag,
    Primer-Ausschluss, Postprocessing-Bypass, Telemetrie-Flag
  - _generate_ollama: format-Feld nur bei response_format im Payload

S2 (prompts.py):
  - parse_befund_slots (Umlaute, Grossschreibung, Duplikate, leere Vorlage)
  - build_befund_slot_schema
  - fill_befund_vorlage (safe-replace, 'nicht erhoben'-Default, kein
    Slot-Rest, Extra-Keys ignoriert, geschweifte Klammern im Fixtext)
  - build_befund_structured_prompt (Slot-Listen, Fallback-Signal bei
    slotloser Vorlage, keine rohen Build-Zeit-Platzhalter)

Kein LLM-Aufruf: _generate_ollama wird gemockt.
"""
import json

import pytest

from app.services import llm as llm_mod
from app.services.llm import generate_text
from app.services.prompts import (
    BEFUND_VORLAGE,
    build_befund_slot_schema,
    build_befund_structured_prompt,
    fill_befund_vorlage,
    parse_befund_slots,
)


# ─────────────────────────────────────────────────────────────────────────────
# S2: parse_befund_slots
# ─────────────────────────────────────────────────────────────────────────────


class TestParseBefundSlots:

    def test_default_vorlage_slots(self):
        slots = parse_befund_slots(BEFUND_VORLAGE)
        assert len(slots) >= 25
        assert "konzentration" in slots
        assert "Zwänge" in slots            # Grossschreibung + Umlaut
        assert "schuldgefühle" in slots     # Umlaut
        assert "suizidalität_vergangenheit" in slots
        # Reihenfolge des ersten Auftretens
        assert slots.index("konzentration") < slots.index("stimmung")

    def test_duplikate_dedupliziert_reihenfolge_erhalten(self):
        v = "A {x}. B {y}. C {x}."
        assert parse_befund_slots(v) == ["x", "y"]

    def test_leere_vorlage(self):
        assert parse_befund_slots("") == []
        assert parse_befund_slots(None) == []

    def test_vorlage_ohne_slots(self):
        assert parse_befund_slots("Fester Text ohne Platzhalter.") == []

    def test_mehrzeilige_klammern_ignoriert(self):
        # Slot-Namen gehen nicht ueber Zeilengrenzen (kein Kliniktext-Match)
        assert parse_befund_slots("{a\nb}") == []


# ─────────────────────────────────────────────────────────────────────────────
# S2: build_befund_slot_schema
# ─────────────────────────────────────────────────────────────────────────────


class TestBuildSchema:

    def test_schema_struktur(self):
        schema = build_befund_slot_schema(["a", "Zwänge"])
        assert schema["type"] == "object"
        assert schema["properties"] == {
            "a": {"type": "string"},
            "Zwänge": {"type": "string"},
        }
        assert schema["required"] == ["a", "Zwänge"]

    def test_schema_json_serialisierbar(self):
        slots = parse_befund_slots(BEFUND_VORLAGE)
        json.dumps(build_befund_slot_schema(slots), ensure_ascii=False)


# ─────────────────────────────────────────────────────────────────────────────
# S2: fill_befund_vorlage
# ─────────────────────────────────────────────────────────────────────────────


class TestFillVorlage:

    def test_fuellt_werte(self):
        v = "Konzentration {konzentration}. Stimmung {stimmung}."
        out = fill_befund_vorlage(v, {"konzentration": "reduziert",
                                      "stimmung": "gedrückt"})
        assert out == "Konzentration reduziert. Stimmung gedrückt."

    def test_fehlender_wert_wird_nicht_erhoben(self):
        v = "A {x}. B {y}."
        out = fill_befund_vorlage(v, {"x": "wert"})
        assert out == "A wert. B nicht erhoben."

    def test_leerer_und_none_wert_wird_nicht_erhoben(self):
        v = "A {x}. B {y}."
        out = fill_befund_vorlage(v, {"x": "", "y": None})
        assert out == "A nicht erhoben. B nicht erhoben."

    def test_kein_slot_rest_im_output(self):
        out = fill_befund_vorlage(BEFUND_VORLAGE, {})
        assert "{" not in out or "}" not in out.replace("(z.B.", "")
        for slot in parse_befund_slots(BEFUND_VORLAGE):
            assert "{" + slot + "}" not in out

    def test_extra_keys_ignoriert(self):
        v = "A {x}."
        out = fill_befund_vorlage(v, {"x": "ok", "hallucinated": "boese"})
        assert out == "A ok."
        assert "boese" not in out

    def test_mehrfach_okkurrenz_alle_ersetzt(self):
        v = "{x} und nochmal {x}."
        assert fill_befund_vorlage(v, {"x": "w"}) == "w und nochmal w."

    def test_fixtext_wortidentisch(self):
        """Kernzusage von S2: Diff zwischen Vorlage und Output == nur Slots."""
        values = {s: "X" for s in parse_befund_slots(BEFUND_VORLAGE)}
        out = fill_befund_vorlage(BEFUND_VORLAGE, values)
        expected = BEFUND_VORLAGE
        for s in parse_befund_slots(BEFUND_VORLAGE):
            expected = expected.replace("{" + s + "}", "X")
        assert out == expected

    def test_werte_werden_getrimmt(self):
        v = "A {x}."
        assert fill_befund_vorlage(v, {"x": "  wert  "}) == "A wert."


# ─────────────────────────────────────────────────────────────────────────────
# S2: build_befund_structured_prompt
# ─────────────────────────────────────────────────────────────────────────────


class TestBuildStructuredPrompt:

    def test_default_vorlage(self):
        sys_p, slots, schema = build_befund_structured_prompt(
            diagnosen=["F33.1"], befund_vorlage=None, source_text=None,
        )
        assert slots == parse_befund_slots(BEFUND_VORLAGE)
        assert schema["required"] == slots
        assert "F33.1" in sys_p
        assert "nicht erhoben" in sys_p
        assert BEFUND_VORLAGE[:40] in sys_p  # Vorlage als Kontext enthalten

    def test_editierte_vorlage(self):
        v = "Kurzer Befund: Stimmung {stimmung}, Antrieb {antrieb}."
        sys_p, slots, schema = build_befund_structured_prompt(
            befund_vorlage=v,
        )
        assert slots == ["stimmung", "antrieb"]
        assert "- stimmung" in sys_p
        assert "- antrieb" in sys_p

    def test_slotlose_vorlage_signalisiert_freitext_pfad(self):
        sys_p, slots, schema = build_befund_structured_prompt(
            befund_vorlage="Fester Text ohne Platzhalter.",
        )
        assert slots == []
        assert sys_p == ""
        assert schema == {}

    def test_keine_rohen_buildzeit_platzhalter(self):
        sys_p, _, _ = build_befund_structured_prompt()
        assert "[Patient/in]" not in sys_p
        assert "[Name]" not in sys_p


# ─────────────────────────────────────────────────────────────────────────────
# S1: generate_text mit response_format (Ollama gemockt)
# ─────────────────────────────────────────────────────────────────────────────


def _mock_ollama(monkeypatch, response_text, capture):
    async def fake_generate_ollama(system_prompt, user_content, max_tokens,
                                   model=None, assistant_primer="",
                                   temperature_override=None,
                                   response_format=None):
        capture.update({
            "assistant_primer": assistant_primer,
            "response_format":  response_format,
        })
        return {
            "text":        response_text,
            "model_used":  "mock-model",
            "token_count": 42,
            "telemetry":   {"think_ratio": 0.0},
        }
    monkeypatch.setattr(llm_mod, "_generate_ollama", fake_generate_ollama)


class TestGenerateTextStructured:

    @pytest.mark.asyncio
    async def test_valides_json_wird_geparst(self, monkeypatch):
        cap = {}
        _mock_ollama(monkeypatch, '{"stimmung": "gedrückt"}', cap)
        result = await generate_text(
            "sys", "user", max_tokens=100, workflow="befund",
            response_format={"type": "object",
                             "properties": {"stimmung": {"type": "string"}},
                             "required": ["stimmung"]},
        )
        assert result["structured_parse_error"] is False
        assert result["structured_data"] == {"stimmung": "gedrückt"}
        assert result["telemetry"]["structured_output"] is True
        # Primer muss leer sein (wuerde JSON brechen)
        assert cap["assistant_primer"] == ""
        # Schema wurde durchgereicht
        assert cap["response_format"]["required"] == ["stimmung"]
        # Postprocessing-Bypass: Roh-JSON bleibt unangetastet in result["text"]
        assert result["text"] == '{"stimmung": "gedrückt"}'

    @pytest.mark.asyncio
    async def test_parse_fehler_setzt_flag(self, monkeypatch):
        cap = {}
        _mock_ollama(monkeypatch, "Dies ist kein JSON (Ollama < 0.5).", cap)
        result = await generate_text(
            "sys", "user", max_tokens=100, workflow="befund",
            response_format={"type": "object", "properties": {},
                             "required": []},
        )
        assert result["structured_parse_error"] is True
        assert result["structured_data"] is None
        assert result["telemetry"]["structured_output"] is True

    @pytest.mark.asyncio
    async def test_ohne_response_format_kein_structured_flag(self, monkeypatch):
        cap = {}
        _mock_ollama(monkeypatch, "Ein plausibler Befundtext. " * 40, cap)
        result = await generate_text(
            "sys", "user", max_tokens=100, workflow="befund",
        )
        assert "structured_data" not in result
        assert "structured_output" not in (result.get("telemetry") or {})
        # Kein response_format an Ollama durchgereicht
        assert cap["response_format"] is None
