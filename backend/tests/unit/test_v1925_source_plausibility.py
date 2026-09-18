"""v19.25 Sprint Q: Quellen-Plausibilitaet (source_plausibility.py + QC + Job-API).

Regressionsbasis prompts.log 13.09.2026 (Job 0780690823): Kammer-Formular als
Verlaufsdoku (Vokabular-Dichte 2.6/1.000w; echte Verlaeufe 41-57), Vorlage
mit HTML + Mojibake; c.saur 14./15.09.: Stilvorlage nur Ueberschriften.
"""
from app.services import quality_check as QC
from app.services import source_plausibility as SP

KAMMER = """Landespsychotherapeutenkammer Baden-Württemberg
Mitgliedsnummer: Name: Bitte hier Ihre vollständige Postadresse angeben!
Erhebungsbogen/Antrag auf Ermäßigung des Kammerbeitrags 2026
Der Kammerbescheid wird im März 2026 erstellt und ist zum 30.04.2026 zu bezahlen.
Wer einen ermäßigten Beitrag bezahlen möchte, muss dies schriftlich beantragen. Ein schriftlicher
Antrag auf Ermäßigung 2026 kann nur bis 31.12.2026 gestellt werden.
Ich arbeite im Jahr 2026 ausschließlich im Ausland und bin deshalb freiwilliges Mitglied.
Ich bin zum Stichtag 01. Februar 2026 auch Pflichtmitglied einer Ärztekammer.
Mein Einkommen aus psychotherapeutischer Tätigkeit lag unter dem Grenzwert. Nachweis beigefügt.
""" * 6

VERLAUF = """### 12.08.2026
Einzelgespräch: Frau R. berichtet von anhaltender Anspannung und Schlafstörungen. Im Gespräch
zeigt sich ein innerer Anteil, der Kontrolle sichern will; Körperarbeit mit Fokus auf Atmung,
Ressourcenaktivierung. Sie geht stabil aus dem Kontakt.
### 13.08.2026
Gruppe non-verbal: Patientin gestaltet ein Bild zur Trauer um den Vater, spürbare Belastung,
Gruppe reagiert wertschätzend. Therapeutische Beziehung tragfähig.
""" * 12

HTML_VORLAGE = ("<p>Im Verlauf des station?ren Aufenthalts zeigte sich Frau N. zun?chst ersch?pft.</p>"
                "<p>In den Einzelgespr?chen wurde ein sch?tzender Anteil erkennbar, der f?r Sicherheit sorgt.</p>"
                "<br><p>Die Gruppentherapie erm?glichte Begegnung mit ?hnlich Betroffenen.</p>") * 8


class TestQ1:

    def test_kammer_formular_faellt_auf(self):
        words, dens = SP.verlauf_vocabulary_density(KAMMER)
        assert words >= 300 and dens < SP.VERLAUF_VOCAB_THRESHOLD
        w = SP.collect_source_warnings(verlauf_text=KAMMER)
        assert [x["code"] for x in w] == [SP.CODE_VERLAUF_UNPLAUSIBEL]
        assert w[0]["severity"] == "warning" and "falsches Dokument" in w[0]["message"]

    def test_echter_verlauf_still(self):
        words, dens = SP.verlauf_vocabulary_density(VERLAUF)
        assert words >= 300 and dens > 30
        assert SP.collect_source_warnings(verlauf_text=VERLAUF) == []

    def test_kurzer_text_nicht_geprueft(self):
        assert SP.collect_source_warnings(verlauf_text="Nur ein paar Worte ohne Bezug.") == []


class TestQ2:

    def test_html_und_mojibake(self):
        d = SP.encoding_damage(HTML_VORLAGE)
        assert d["damaged"] and d["html_tags"] >= 3 and d["mojibake"] >= SP.MOJIBAKE_MIN_ABS
        w = SP.collect_source_warnings(sources={"Antragsvorlage": HTML_VORLAGE, "Verlaufsdokumentation": VERLAUF})
        assert len(w) == 1 and w[0]["code"] == SP.CODE_SOURCE_ENCODING_DAMAGED and w[0]["source"] == "Antragsvorlage"
        assert "HTML-Tags" in w[0]["message"] and "'?' statt Umlaut" in w[0]["message"]

    def test_strip_html(self):
        out = SP.strip_html_tags("<p>Erster Absatz &amp; mehr.</p><br><ul><li>Punkt</li></ul>")
        assert "<" not in out and "&amp;" not in out
        assert "Erster Absatz & mehr." in out and "Punkt" in out
        assert out.count("\n") >= 1
        # kein Tag -> unveraendert (Identitaet, auch bei '<' als Vergleichszeichen)
        s = "Werte < 5 und > 2 bleiben."
        assert SP.strip_html_tags(s) == s

    def test_normales_fragezeichen_kein_alarm(self):
        t = ("Wie geht es Ihnen? Was hat sich verändert? " * 60)
        assert not SP.encoding_damage(t)["damaged"]


class TestQ3:

    def test_c_saur_ueberschriften(self):
        ex = "Orga:\n\nAnliegen:\n\nIntervention:\n\nErgebnis/so geht Klient:in raus\n\nkein Hinweis auf Suizidalität"
        q = SP.style_example_quality(ex)
        assert q["heading_only"] and q["too_short"]
        w = SP.collect_source_warnings(style_examples=[ex], style_source="style_library")
        assert len(w) == 1 and w[0]["code"] == SP.CODE_STYLE_EXAMPLE_TOO_SHORT and w[0]["severity"] == "info"
        assert "Stilbibliothek" in w[0]["message"] and "nur Ueberschriften" in w[0]["message"]

    def test_echtes_beispiel_still(self):
        ex = ("Frau M. beschreibt, dass ein Teil von ihr immer wieder in alte Muster zurückfällt, "
              "sobald sie sich unter Druck erlebt. Im Gespräch wurde deutlich, wie eng dieses Erleben mit "
              "früheren Erfahrungen verknüpft ist. " * 3)
        assert SP.collect_source_warnings(style_examples=[ex], style_source="file_upload") == []


class TestQCUndJob:

    def test_qc_uebersetzt_warnungen(self):
        w = SP.collect_source_warnings(verlauf_text=KAMMER, sources={"Antragsvorlage": HTML_VORLAGE},
                                       style_examples=["Orga:\nAnliegen:"], style_source="text_input")
        issues = QC.run_quality_check("Ein Text.", "entlassbericht", source_warnings=w)
        codes = [i.code for i in issues]
        assert QC.ISSUE_CODE_VERLAUF_UNPLAUSIBEL in codes
        assert QC.ISSUE_CODE_SOURCE_ENCODING_DAMAGED in codes
        assert QC.ISSUE_CODE_STYLE_EXAMPLE_TOO_SHORT in codes
        by = {i.code: i for i in issues}
        assert by[QC.ISSUE_CODE_VERLAUF_UNPLAUSIBEL].severity == QC.SEVERITY_WARNING
        assert by[QC.ISSUE_CODE_STYLE_EXAMPLE_TOO_SHORT].severity == QC.SEVERITY_INFO
        assert by[QC.ISSUE_CODE_VERLAUF_UNPLAUSIBEL].code_detail["source"] == "Verlaufsdokumentation"
        assert "Neu-Generierung" in by[QC.ISSUE_CODE_VERLAUF_UNPLAUSIBEL].repair_hint
        # Serialisierung (Job-API) bleibt gueltig
        ser = QC.serialize_issues(issues, workflow="entlassbericht")
        assert ser["summary"]["warning"] >= 2

    def test_qc_ignoriert_fremde_codes_und_none(self):
        assert [i for i in QC.run_quality_check("T.", "dokumentation", source_warnings=None)
                if i.code in QC._SOURCE_WARNING_CODES] == []
        assert [i for i in QC.run_quality_check("T.", "dokumentation", source_warnings=[{"code": "X_Y"}])
                if i.code in QC._SOURCE_WARNING_CODES] == []

    def test_job_to_dict_enthaelt_source_warnings(self):
        from app.services.job_queue import JobState
        j = JobState(job_id="j", workflow="entlassbericht", description="")
        assert j.to_dict()["source_warnings"] == []
        j.source_warnings = [{"code": "VERLAUF_UNPLAUSIBEL", "severity": "warning", "source": "V", "message": "m"}]
        assert j.to_dict()["source_warnings"][0]["code"] == "VERLAUF_UNPLAUSIBEL"
