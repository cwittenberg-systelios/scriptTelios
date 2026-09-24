"""v19.36 - Vision-OCR: Payload (Modell-Override, fester num_ctx, keep_alive)
und Eval-Skript (synthetische Formulare, Checkbox-Auswertung)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from app.core.config import settings
from app.services.extraction import vision_payload

BACKEND = Path(__file__).resolve().parents[2]


def _ev():
    spec = importlib.util.spec_from_file_location("eval_vision_ocr", BACKEND / "scripts" / "eval_vision_ocr.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ev = _ev()


class TestPayload:
    def test_llava_ohne_keepalive_und_dynamisch(self, monkeypatch):
        monkeypatch.setattr(settings, "LLM_FIXED_CTX", False)
        monkeypatch.setattr(settings, "VISION_MODEL", "llava")
        p = vision_payload("P", "b64")
        assert p["model"] == "llava" and p["images"] == ["b64"] and p["think"] is False
        assert "keep_alive" not in p and "num_ctx" not in p["options"]

    def test_gemma_mit_festem_ctx_bleibt_geladen(self, monkeypatch):
        monkeypatch.setattr(settings, "LLM_FIXED_CTX", True)
        monkeypatch.setattr(settings, "LLM_NUM_CTX_CAP", 32768)
        monkeypatch.setattr(settings, "WORKFLOW_MODEL", {"dokumentation": "gemma4:31b"})
        p = vision_payload("P", "b64", model="gemma4:31b")
        assert p["model"] == "gemma4:31b" and p["options"]["num_ctx"] == 32768 and p["keep_alive"] == -1


class TestAuswertung:
    def test_parse_varianten(self):
        text = ("Kürzel: Frau B.\n[X] Schlafstörungen\n[ ] Kopfschmerzen\n☒ Tinnitus\n☐ Schwindel\n"
                "[✓] Atemnot\nRückenschmerzen\n")
        got = ev.parse_checkboxes(text, ["Schlafstörungen", "Kopfschmerzen", "Tinnitus", "Schwindel",
                                         "Atemnot", "Rückenschmerzen", "Herzrasen"])
        assert got == {"Schlafstörungen": True, "Kopfschmerzen": False, "Tinnitus": True, "Schwindel": False,
                       "Atemnot": True, "Rückenschmerzen": None, "Herzrasen": None}

    def test_score_zaehlt_fehlerarten(self):
        truth = {"checkboxes": {"A-Schmerz": True, "B-Schmerz": False, "C-Schmerz": True, "D-Schmerz": False},
                 "felder": {"Kürzel": "Herr K.", "Hausarzt": "Dr. Weber"}}
        text = "Kürzel: Herr K.\n[X] A-Schmerz\n[X] B-Schmerz\n[ ] C-Schmerz\nHausarzt: Dr. Schulz"
        r = ev.score(truth, text)
        assert (r["richtig"], r["falsch_an"], r["falsch_aus"], r["fehlt"]) == (1, 1, 1, 1)
        assert r["felder_richtig"] == 1

    def test_formulare_deterministisch_und_mit_wahrheit(self):
        a, b = ev.synthetic_pages(), ev.synthetic_pages()
        assert [p[0] for p in a] == ["sauber-0", "sauber-1", "leicht-2", "leicht-3", "schlecht-4", "schlecht-5"]
        assert a[4][2] == b[4][2] and len(a[4][2]["checkboxes"]) == 10
        assert a[4][1].tobytes() == b[4][1].tobytes()

    async def test_run_mit_attrappe(self, monkeypatch, tmp_path):
        from app.services import extraction
        pages = ev.synthetic_pages()[:2]
        seq = iter(pages * 2)          # je Modell alle Seiten in Reihenfolge

        async def avail(model=None):
            return model != "fehlt:1b"

        async def perfekter_leser(b64, page, total, model=None):
            truth = next(seq)[2]
            lines = [f"{k}: {v}" for k, v in truth["felder"].items()]
            lines += [("[X] " if on else "[ ] ") + lbl for lbl, on in truth["checkboxes"].items()]
            return "\n".join(lines)
        monkeypatch.setattr(extraction, "_check_vision_model_available", avail)
        monkeypatch.setattr(extraction, "_ollama_vision_page", perfekter_leser)

        async def caps(model):
            return ["completion", "vision"]
        monkeypatch.setattr(ev, "_capabilities", caps)
        res = await ev.run(["gemma4:31b", "fehlt:1b"], pages, tmp_path)
        assert list(res) == ["gemma4:31b"]
        s = res["gemma4:31b"]["summe"]
        assert s["boxen_richtig"] == 1.0 and s["felder_quote"] == 1.0 and s["fehler"] == 0
        assert (tmp_path / "gemma4_31b__sauber-0.txt").exists()
