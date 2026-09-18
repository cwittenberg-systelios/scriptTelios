"""v19.25 Sprint G: deterministische Grammatik-Fixes im Postprocessing.

Regressionsbasis prompts.log 11.-17.09.2026:
  G1 "von Herr G." (8 Treffer in 4 P1-Dokus)   -> "von Herrn G."
  G2 "Aufenthaltsvon Frau R." (2/2 Verlaengerungen) -> "Aufenthalts von Frau R."
  G3 Telemetrie grammar_fixes -> QC GRAMMAR_AUTOFIXED (info)
"""
from app.services import quality_check as QC
from app.services.postprocessing import (
    fix_herrn_deklination,
    fix_kompositum_klebebugs,
    postprocess_output,
)

LOG_HERR = [
    ("sowie eine hohe psychische Instabilität von Herr H., der sich", "von Herrn H., der sich"),
    ("stand das aktuelle Befinden von Herr W., wobei", "von Herrn W., wobei"),
    ("das gemeinsame Gespräch von Herr G. und seiner Partnerin", "von Herrn G. und seiner"),
    ("Zurückgepfiffelseins bei Herr G., wobei beide", "bei Herrn G., wobei"),
    ("Die emotionale Distanzierung von Herr G. lässt sich", "von Herrn G. lässt"),
    ("Bemerkenswert ist die Beobachtung von Herr G., dass er", "von Herrn G., dass"),
    ("Die Tendenz von Herr G., sich in einer Fürsorgerrolle", "von Herrn G., sich"),
]


class TestHerrn:

    def test_log_treffer(self):
        for src, want in LOG_HERR:
            out, n = fix_herrn_deklination(src)
            assert n == 1 and want in out, (src, out)

    def test_nominativ_bleibt(self):
        for s in ("Herr G. berichtet von Erschöpfung.", "Im Gespräch zeigte sich Herr G. offen.",
                  "Herr G. und Frau S. kamen gemeinsam.", "Sehr geehrter Herr Doktor,"):
            out, n = fix_herrn_deklination(s)
            assert n == 0 and out == s, s

    def test_weitere_praepositionen_und_genitiv(self):
        out, n = fix_herrn_deklination(
            "Gemeinsam mit Herr N. wurde vereinbart, dass für Herr N. ein Plan entsteht; "
            "die Sicht des Herr N. auf seine Partnerin, gegenüber Herr N. offen."
        )
        assert n == 4
        assert "mit Herrn N." in out and "für Herrn N." in out and "des Herrn N." in out and "gegenüber Herrn N." in out

    def test_voller_nachname(self):
        out, n = fix_herrn_deklination("Nach Angaben von Herr Müller ist")
        assert n == 1 and "von Herrn Müller" in out

    def test_gross_klein_praeposition(self):
        out, n = fix_herrn_deklination("Von Herr G. wurde berichtet")
        assert n == 1 and out.startswith("Von Herrn G.")

    def test_ohne_herr_schnell(self):
        assert fix_herrn_deklination("Frau S. berichtet.") == ("Frau S. berichtet.", 0)
        assert fix_herrn_deklination("") == ("", 0)


class TestKlebefehler:

    def test_aufenthaltsvon(self):
        src = "Im bisherigen Verlauf des stationären Aufenthaltsvon Frau R. haben wir sie"
        assert fix_kompositum_klebebugs(src) == \
            "Im bisherigen Verlauf des stationären Aufenthalts von Frau R. haben wir sie"

    def test_generisch_funktionswort(self):
        assert fix_kompositum_klebebugs("Zu Beginn des Verlaufsbei Frau R.") == "Zu Beginn des Verlaufs bei Frau R."
        assert fix_kompositum_klebebugs("des Aufenthaltswurde deutlich") == "des Aufenthalts wurde deutlich"

    def test_echte_komposita_bleiben(self):
        for w in ("Aufenthaltsdauer", "Gesprächsmitte", "Verlaufsdokumentation", "Antragsvorlage",
                  "Gesprächsinhalte", "Berichtszeitraum", "Behandlungsverlauf"):
            assert fix_kompositum_klebebugs(f"Die {w} war lang.") == f"Die {w} war lang."


class TestStatsUndQC:

    def test_postprocess_stats(self):
        stats = {}
        out = postprocess_output(
            "Im Verlauf des Aufenthaltsvon Herr G. zeigte sich die Beobachtung von Herr G. als hilfreich.",
            workflow="dokumentation", stats=stats,
        )
        assert "Aufenthalts von Herrn G." in out and "Beobachtung von Herrn G." in out
        assert stats == {"klebebugs": 1, "herrn": 2, "total": 3}

    def test_stats_leer_bei_sauberem_text(self):
        stats = {}
        postprocess_output("Herr G. berichtet.", stats=stats)
        assert stats["total"] == 0

    def test_qc_info(self):
        issues = QC.run_quality_check("Ein sauberer Text.", "dokumentation",
                                      grammar_fixes={"klebebugs": 1, "herrn": 2, "total": 3})
        hit = [i for i in issues if i.code == QC.ISSUE_CODE_GRAMMAR_AUTOFIXED]
        assert len(hit) == 1 and hit[0].severity == QC.SEVERITY_INFO
        assert "2x 'Herr' -> 'Herrn'" in hit[0].message and "1x Klebefehler" in hit[0].message

    def test_qc_still_ohne_fixes(self):
        for gf in (None, {}, {"total": 0}):
            assert [i for i in QC.run_quality_check("Text.", "dokumentation", grammar_fixes=gf)
                    if i.code == QC.ISSUE_CODE_GRAMMAR_AUTOFIXED] == []
