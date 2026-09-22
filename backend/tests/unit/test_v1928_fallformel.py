"""v19.28 (S2/S3): Stage 1b Fallformel + thematische Prompt-Pfade."""
import pytest

from app.services import fallformel as ff
from app.services.fallformel import (
    KEIN_MUSTER_SATZ, MAX_THEMEN, build_fallformel, detect_fallformel_issues,
    fallformel_prompt_block, normalize_eb_struktur, parse_themenkandidaten,
    select_themen, split_sections,
)

FF = """### Auftrag
Frau M. kam mit dem Wunsch nach innerer Entlastung (Aufnahme 17.12.).

### Themenkandidaten
1. **Angst als alter Schutz** – Angst als Schutz- und Beziehungsregulation verstanden. Belege: (Einzel 23.12.), (Einzel 30.12.)
2. **Anpassung vs. eigene Bedürfnisse** – Grenzen fallen bei Bekannten schwer.
   Belege: (Bezugsgruppe 06.01.), (Einzel 20.01.)
3. **Perfektionismus** – Belege: (Aufnahme 17.12.), (Einzel 08.01.)
4. **Viertes Thema** – zu viel. Belege: (Einzel 13.01.)

### Wendepunkte je Modalität
- Einzeltherapie: Elternbesuch als Belastungsprobe (Einzel 13.01., 20.01.)
- Gruppentherapie: Anteile-Aufstellung (Gruppe non-verbal 14.01.)
- Nonverbale Therapien: Pendeln zwischen Angst und sicherem Ort (Körperarbeit 30.12.)

### Symptomveränderung
Deutliche Reduktion der Angst (Einzel 20.01.), leichter Anstieg der Belastung im Übergang (26.01.).

### Offene Themen
Stabilisierung im Alltag (Prozessreflexion 22.01.).
"""

SRC = "17.12.2025 Aufnahme. 23.12.2025 Einzel. 30.12.2025 Einzel. 06.01.2026 Bezugsgruppe. 08.01. Einzel. 13.01. Einzel. 14.01. Gruppe. 20.01. Einzel. 22.01. PR. 26.01. Gruppe."


class TestParsing:
    def test_split_sections(self):
        sec = split_sections(FF)
        assert set(sec) == {"Auftrag", "Themenkandidaten", "Wendepunkte je Modalität", "Symptomveränderung", "Offene Themen"}
        assert sec["Offene Themen"].startswith("Stabilisierung")

    def test_parse_themen_mehrzeilig(self):
        items = parse_themenkandidaten(FF)
        assert len(items) == 4
        assert items[1].startswith("**Anpassung")
        assert "(Einzel 20.01.)" in items[1]  # Folgezeile zusammengefuehrt

    def test_kein_muster(self):
        txt = FF.replace("1. **Angst", f"{KEIN_MUSTER_SATZ}\n1. **Angst")
        assert parse_themenkandidaten(txt) == []

    def test_select_default_kuerzt_auf_max(self):
        out = select_themen(FF, None)
        items = parse_themenkandidaten(out)
        assert len(items) == MAX_THEMEN
        assert items[0].startswith("**Angst")
        assert "Viertes" not in out
        # Rest bleibt erhalten, Reihenfolge der Abschnitte stabil
        assert out.index("### Auftrag") < out.index("### Themenkandidaten") < out.index("### Offene Themen")
        assert "Elternbesuch" in out

    def test_select_auswahl_und_neunummerierung(self):
        out = select_themen(FF, [2, 0])
        items = parse_themenkandidaten(out)
        assert [i.split("**")[1] for i in items] == ["Perfektionismus", "Angst als alter Schutz"]
        assert "1. **Perfektionismus" in out and "2. **Angst" in out

    def test_select_ungueltig_faellt_auf_erstes(self):
        assert len(parse_themenkandidaten(select_themen(FF, [9]))) == 1

    def test_select_ohne_themen_unveraendert(self):
        txt = "### Auftrag\nx\n\n### Themenkandidaten\n" + KEIN_MUSTER_SATZ
        assert select_themen(txt, [0]) == txt


class TestIssues:
    def test_sauber(self):
        issues = detect_fallformel_issues(select_themen(FF, None), SRC)
        assert not [i for i in issues if i["severity"] in ("critical", "high")]

    def test_zu_viele_themen(self):
        assert any(i["type"] == "zu_viele_themen" for i in detect_fallformel_issues(FF, SRC))

    def test_abschnitt_fehlt(self):
        txt = FF.replace("### Offene Themen", "### Sonstiges")
        assert any(i["type"] == "abschnitt_fehlt" and "Offene" in i["detail"] for i in detect_fallformel_issues(txt, SRC))

    def test_datum_ohne_quelle(self):
        txt = FF.replace("(Einzel 23.12.)", "(Einzel 24.12.)")
        hit = [i for i in detect_fallformel_issues(txt, SRC) if i["type"] == "datum_ohne_quelle"]
        assert hit and "24.12." in hit[0]["detail"]

    def test_verfahren_halluzination_wiederverwendet(self):
        txt = FF.replace("Pendeln zwischen", "EMDR-gestütztes Pendeln zwischen")
        assert any(i["type"] == "verfahren_halluzination" for i in detect_fallformel_issues(txt, SRC))

    def test_leer(self):
        assert detect_fallformel_issues("", SRC)[0]["severity"] == "critical"


class TestBuild:
    @pytest.mark.asyncio
    async def test_build_kuerzt_und_liefert_audit(self, monkeypatch):
        seen = {}

        async def fake(system_prompt, user_content, **kw):
            seen["system"] = system_prompt
            seen["user"] = user_content
            seen["kw"] = kw
            return {"text": FF, "telemetry": {"eval_count": 10}}

        monkeypatch.setattr(ff, "stage1_generate", fake)
        res = await build_fallformel(verlauf_text="Verlauf " + SRC, antragsvorlage_text="Anamnese",
                                     prozessreflexion_text="Reflexion", patient_initial="Frau M.",
                                     model="ollama/x", raw_source_text=SRC)
        assert len(res["themen"]) == MAX_THEMEN
        assert "Viertes" not in res["text"]
        assert res["degraded"] is False
        assert any(i["type"] == "zu_viele_themen" for i in res["issues"])
        assert ">>>ANTRAGSVORLAGE<<<" in seen["user"] and ">>>PROZESSREFLEXION<<<" in seen["user"]
        assert seen["kw"]["model"] == "ollama/x"
        assert "### Themenkandidaten" in seen["system"]

    @pytest.mark.asyncio
    async def test_build_leer_wirft(self, monkeypatch):
        async def fake(*a, **kw):
            return {"text": "", "telemetry": {}}
        monkeypatch.setattr(ff, "stage1_generate", fake)
        with pytest.raises(RuntimeError):
            await build_fallformel(verlauf_text="x")


class TestSchalter:
    @pytest.mark.parametrize("v,exp", [("thematisch", "thematisch"), ("THEMATISCH ", "thematisch"),
                                       ("modalitaet", "modalitaet"), (None, "modalitaet"), ("foo", "modalitaet")])
    def test_normalize(self, v, exp):
        assert normalize_eb_struktur(v) == exp

    def test_prompt_block(self):
        b = fallformel_prompt_block(FF)
        assert b.startswith("FALLFORMEL")
        assert "Wendepunkte SEINER" in b and KEIN_MUSTER_SATZ in b


class TestPromptPfade:
    def test_system_prompt_thematisch(self):
        from app.services.prompts import (
            BASE_PROMPT_EB_THEMATISCH_ZUSATZ, FEW_SHOT_ENTLASSBERICHT,
            FEW_SHOT_ENTLASSBERICHT_THEMATISCH, WORKFLOW_INSTRUCTIONS_EB_THEMATISCH, build_system_prompt,
        )
        style = "Beispieltext eines anderen Patienten. " * 30
        th = build_system_prompt("entlassbericht", workflow_instructions=WORKFLOW_INSTRUCTIONS_EB_THEMATISCH,
                                 style_context=style, eb_struktur="thematisch")
        sq = build_system_prompt("entlassbericht", style_context=style, eb_struktur="modalitaet")
        assert "Im Verlauf kristallisierte sich als zentrales Muster" in th   # thematischer Few-Shot
        assert "Im Verlauf kristallisierte sich als zentrales Muster" not in sq
        assert BASE_PROMPT_EB_THEMATISCH_ZUSATZ.strip() in th
        assert BASE_PROMPT_EB_THEMATISCH_ZUSATZ.strip() not in sq
        # thematisch: Schablone nur als Schreibstil, nicht als Struktur
        assert "STILBEISPIEL DES THERAPEUTEN – NUR SCHREIBSTIL" in th
        assert "STRUKTURELLE SCHABLONE" in sq
        assert "in der Struktur des Stilbeispiels" not in th
        # Status quo unveraendert: alter Few-Shot vorhanden
        assert "Im Einzelprozess stand die hypnosystemische Anteilearbeit" in sq
        assert FEW_SHOT_ENTLASSBERICHT_THEMATISCH != FEW_SHOT_ENTLASSBERICHT

    def test_user_content_thematisch_mit_fallformel(self):
        from app.services.prompts import build_user_content
        th = build_user_content("entlassbericht", verlaufsdoku_text="V", antragsvorlage_text="A",
                                prozessreflexion_text="R", fokus_themen="Fokus X",
                                eb_struktur="thematisch", fallformel_text=FF)
        assert "FALLFORMEL (Gerüst" in th
        assert th.index("VERLAUFSDOKUMENTATION") < th.index("FALLFORMEL") < th.index("THERAPEUTISCHE SCHWERPUNKTE")
        assert "Beginne Teil 4" in th and "unmittelbar vor der Epikrise" not in th
        assert "fünf Teilen" in th

    def test_user_content_statusquo_unveraendert(self):
        from app.services.prompts import build_user_content
        sq = build_user_content("entlassbericht", verlaufsdoku_text="V", prozessreflexion_text="R",
                                eb_struktur="modalitaet", fallformel_text=FF)
        assert "FALLFORMEL" not in sq
        assert "unmittelbar vor der Epikrise" in sq
        assert "Epikrise (~20%)" in sq
        default = build_user_content("entlassbericht", verlaufsdoku_text="V", prozessreflexion_text="R")
        assert default == sq
