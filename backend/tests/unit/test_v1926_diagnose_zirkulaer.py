"""v19.26: Zirkulaere Diagnose-Erklaerung in der Anamnese (P2).

Regressionsfall prompts.log.2026-09-08, Job cae59639 (c.wittenberg):
  "Hinzu kommen Flashbacks und ein starkes Grübeln im Rahmen einer
   Posttraumatischen Belastungsstörung (PTBS) und einer rezidivierenden
   depressiven Störung."
Ebene 1 Prompt (A3c), Ebene 2 QC satzweise (DIAGNOSE_ZIRKULAER critical,
DIAGNOSE_IM_TEXT warning, Attribution ok), Ebene 3 satzgenauer Rewrite mit
Verifikation + deterministischem Fallback.
"""
import pytest

from app.services import diagnose_rewrite as DR
from app.services import quality_check as QC

LOG_TEXT = (
    "Frau L. stellt sich mit massiver Erschöpfung und einem komplexen Leidensbild vor, das sich über "
    "Jahre entwickelt hat. Die aktuelle Krise wurde durch die Trennung von ihrem Ehemann nach 22-jähriger "
    "Ehe sowie den Tod ihrer Mutter ausgelöst. Frau L. schildert eine tiefgreifende Erschöpfung mit "
    "anhaltender Müdigkeit, Konzentrationsstörungen, Wortfindungsproblemen und Schlafstörungen, die ihren "
    "beruflichen Alltag als Pfarrerin stark beeinträchtigen. Hinzu kommen Flashbacks und ein starkes "
    "Grübeln im Rahmen einer Posttraumatischen Belastungsstörung (PTBS) und einer rezidivierenden "
    "depressiven Störung. Die Symptome haben sich trotz ambulanter Therapie verschlimmert, sodass ein "
    "stationärer Aufenthalt notwendig erscheint."
)
LOG_SENT = ("Hinzu kommen Flashbacks und ein starkes Grübeln im Rahmen einer Posttraumatischen "
            "Belastungsstörung (PTBS) und einer rezidivierenden depressiven Störung.")


# ── Ebene 2: QC ─────────────────────────────────────────────────────────────

class TestKlassifikation:

    def test_log_satz_zirkulaer(self):
        c = QC.diagnose_sentences(LOG_TEXT)
        assert len(c) == 1 and c[0]["kind"] == "zirkulaer"
        assert "PTBS" in c[0]["labels"]

    @pytest.mark.parametrize("sent,kind", [
        ("Frau M. leide, wie die Diagnose F33.1 zeigt, an einer rezidivierenden depressiven Störung.", "zirkulaer"),
        ("Die Beschwerden bestehen vor dem Hintergrund einer mittelgradigen depressiven Episode.", "zirkulaer"),
        ("Aufgrund ihrer Angststörung vermeide sie Menschenmengen.", "zirkulaer"),
        ("Die Schlafstörungen seien bedingt durch die Depression.", "zirkulaer"),
        ("Laut Vorbefund wurde 2019 eine PTBS diagnostiziert.", "attribuiert"),
        ("Von seiner Therapeutin wurde ein Verdacht auf ADHS geäußert.", "attribuiert"),
        ("Familiär gibt es Hinweise auf eine unbehandelte Depression bei seiner Mutter.", "attribuiert"),
        ("Sie sei damals wegen einer Essstörung in Behandlung gewesen.", "attribuiert"),
        ("Sie habe seit Jahren eine Depression.", "nennung"),
        ("Es besteht eine Panikstörung mit Agoraphobie.", "nennung"),
        ("Diagnose F43.1 laut Einweisung.", "nennung"),   # ICD nie attribuiert
    ])
    def test_faelle(self, sent, kind):
        c = QC.diagnose_sentences(sent)
        assert len(c) == 1 and c[0]["kind"] == kind, c

    def test_ohne_diagnose_leer(self):
        assert QC.diagnose_sentences("Bereits vor zwei Jahren erlitt er einen Burnout, was zum Auslöser wurde.") == []
        assert QC.diagnose_sentences("Sie berichtet von Flashbacks, Grübeln und Schlafstörungen.") == []


class TestQC:

    def _codes(self, text, wf="anamnese", dx=None):
        return {i.code: i for i in QC.run_quality_check(text, wf, diagnosen=dx)}

    def test_log_text_critical(self):
        by = self._codes(LOG_TEXT, dx=["F43.1", "F33.1"])
        z = by[QC.ISSUE_CODE_DIAGNOSE_ZIRKULAER]
        assert z.severity == QC.SEVERITY_CRITICAL
        assert z.code_detail["count"] == 1 and "PTBS" in z.code_detail["labels"]
        assert QC.ISSUE_CODE_DIAGNOSE_IM_TEXT not in by

    def test_ohne_diagnosen_trotzdem(self):
        assert QC.ISSUE_CODE_DIAGNOSE_ZIRKULAER in self._codes(LOG_TEXT)

    def test_attribuiert_still(self):
        t = ("Laut Vorbefund wurde 2019 eine PTBS diagnostiziert. Von seiner Therapeutin wurde ein "
             "Verdacht auf ADHS geäußert. Sie schildert Flashbacks und Grübeln.")
        by = self._codes(t)
        assert QC.ISSUE_CODE_DIAGNOSE_ZIRKULAER not in by and QC.ISSUE_CODE_DIAGNOSE_IM_TEXT not in by

    def test_nennung_warning(self):
        by = self._codes("Sie habe seit Jahren eine Depression. Der Schlaf sei schlecht.")
        assert by[QC.ISSUE_CODE_DIAGNOSE_IM_TEXT].severity == QC.SEVERITY_WARNING
        assert QC.ISSUE_CODE_DIAGNOSE_ZIRKULAER not in by

    def test_nur_anamnese_und_nur_anamnese_teil(self):
        assert QC.ISSUE_CODE_DIAGNOSE_ZIRKULAER not in self._codes(LOG_TEXT, wf="verlaengerung")
        t = "Frau L. schildert Erschöpfung.\n\n###BEFUND###\n\nStimmung gedrückt im Rahmen einer Depression."
        assert QC.ISSUE_CODE_DIAGNOSE_ZIRKULAER not in self._codes(t)

    def test_entfernt_info(self):
        issues = QC.run_quality_check("Text.", "anamnese",
                                      dx_rewrite={"sentences": 1, "replaced": 1, "kept": 0, "mode": "llm",
                                                  "pairs": [{"before": "a", "after": "b", "how": "llm"}]})
        hit = [i for i in issues if i.code == QC.ISSUE_CODE_DIAGNOSE_ENTFERNT]
        assert len(hit) == 1 and hit[0].severity == QC.SEVERITY_INFO and hit[0].code_detail["pairs"]
        assert [i for i in QC.run_quality_check("Text.", "anamnese", dx_rewrite={"replaced": 0})
                if i.code == QC.ISSUE_CODE_DIAGNOSE_ENTFERNT] == []


# ── Ebene 1: Prompt ─────────────────────────────────────────────────────────

def test_prompt_regel_a3c():
    from app.services.prompts import build_system_prompt
    sp = build_system_prompt("anamnese", workflow_instructions=None, source_text="x")
    assert "zirkulär" in sp and "im Rahmen einer PTBS" in sp
    assert "NUR attribuiert" in sp and "Vergangenheitsform" in sp


# ── Ebene 3: Rewrite ────────────────────────────────────────────────────────

class TestVerify:

    def test_ok(self):
        ok, why = DR.verify_rewrite(LOG_SENT, "Hinzu kommen Flashbacks und ein starkes Grübeln.")
        assert ok, why

    @pytest.mark.parametrize("cand,why", [
        ("Hinzu kommen Flashbacks und Grübeln im Rahmen einer PTBS.", "diagnose_bleibt"),
        ("Hinzu kommen Flashbacks und Grübeln aufgrund ihrer Erkrankung.", "rahmen_bleibt"),
        ("Hier ist der umformulierte Satz: Flashbacks und Grübeln.", "meta_text"),
        ("Grübeln.", "laenge_1_von_18"),
        ("Hinzu kommen seit 3 Jahren Flashbacks und ein starkes Grübeln.", "neue_zahl"),
        ("Hinzu kommen bei Herr K. Flashbacks und ein starkes Grübeln.", "neuer_name"),
        ("", "leer"),
    ])
    def test_abgelehnt(self, cand, why):
        ok, got = DR.verify_rewrite(LOG_SENT, cand)
        assert not ok and got == why


class TestFallback:

    def test_log_satz(self):
        assert DR.strip_frame_clause(LOG_SENT) == "Hinzu kommen Flashbacks und ein starkes Grübeln."

    def test_kein_rahmen_am_ende(self):
        assert DR.strip_frame_clause("Aufgrund ihrer Angststörung vermeide sie Menschenmengen.") is None

    def test_restsatz_zu_kurz(self):
        assert DR.strip_frame_clause("Grübeln bestehe im Rahmen einer PTBS.") is None


def _gen(answers):
    calls = []

    async def generate(system_prompt, user_content, **kw):
        calls.append(user_content)
        a = answers[min(len(calls), len(answers)) - 1]
        return {"text": "", "structured_data": {"satz": a} if a is not None else None,
                "structured_parse_error": a is None, "telemetry": {}}
    return generate, calls


@pytest.mark.asyncio
async def test_rewrite_llm_erfolgreich():
    gen, calls = _gen(["Hinzu kommen Flashbacks und ein starkes Grübeln, das sie nachts wachhält."])
    out, tel = await DR.rewrite_diagnose_sentences(LOG_TEXT, generate=gen)
    assert len(calls) == 1 and LOG_SENT in calls[0]
    assert "PTBS" not in out and "Hinzu kommen Flashbacks und ein starkes Grübeln, das sie nachts wachhält." in out
    assert out.startswith("Frau L. stellt sich") and out.endswith("notwendig erscheint.")
    assert tel["sentences"] == 1 and tel["replaced"] == 1 and tel["kept"] == 0 and tel["mode"] == "llm"
    assert tel["pairs"][0]["before"] == LOG_SENT
    # QC danach: kein ZIRKULAER mehr
    assert QC.ISSUE_CODE_DIAGNOSE_ZIRKULAER not in {i.code for i in QC.run_quality_check(out, "anamnese")}


@pytest.mark.asyncio
async def test_rewrite_llm_abgelehnt_fallback():
    gen, _ = _gen(["Hinzu kommen Flashbacks und Grübeln im Rahmen einer PTBS."])   # Label bleibt
    out, tel = await DR.rewrite_diagnose_sentences(LOG_TEXT, generate=gen)
    assert "Hinzu kommen Flashbacks und ein starkes Grübeln. Die Symptome" in out
    assert tel["replaced"] == 1 and tel["mode"] == "fallback" and tel["pairs"][0]["how"] == "fallback"


@pytest.mark.asyncio
async def test_rewrite_llm_fehler_und_kein_fallback_bleibt_original():
    async def boom(*a, **k):
        raise RuntimeError("ollama down")
    text = "Aufgrund ihrer Angststörung vermeide sie Menschenmengen seit Monaten."
    out, tel = await DR.rewrite_diagnose_sentences(text, generate=boom)
    assert out == text and tel["replaced"] == 0 and tel["kept"] == 1 and tel["pairs"][0]["how"] == "llm_fehler"


@pytest.mark.asyncio
async def test_rewrite_nichts_zu_tun_kein_llm_call():
    async def boom(*a, **k):
        raise AssertionError("darf nicht aufgerufen werden")
    t = "Laut Vorbefund wurde 2019 eine PTBS diagnostiziert. Sie schildert Flashbacks."
    out, tel = await DR.rewrite_diagnose_sentences(t, generate=boom)
    assert out == t and tel["sentences"] == 0 and tel["mode"] == "none"
