"""
Unit-Tests fuer detect_summary_hallucination_signals (v19.2 Schritt 3).

Conservative-by-design: false positives sind okay, false negatives gefaehrlich.
"""
from app.services.verlauf_summary import detect_summary_hallucination_signals


class TestVerfahrenHalluzination:
    """Therapieverfahren in Summary die nicht im Source vorkommen → high."""

    def test_emdr_in_summary_aber_nicht_in_quelle(self):
        summary = "Therapeut setzte EMDR ein, um die belastenden Erinnerungen zu bearbeiten."
        source = "Patient sprach ueber traumatische Erlebnisse. Stuhlarbeit half bei der Anteilearbeit."
        issues = detect_summary_hallucination_signals(summary, source)
        types = [i["type"] for i in issues]
        assert "verfahren_halluzination" in types
        emdr_issue = [i for i in issues if "EMDR" in i["detail"]][0]
        assert emdr_issue["severity"] == "high"

    def test_ifs_namentlich_in_quelle_keine_warnung(self):
        summary = "Es wurde mit IFS und Anteilearbeit gearbeitet."
        source = "Im Verlauf der Behandlung kam IFS zum Einsatz, klassische Anteilearbeit."
        issues = detect_summary_hallucination_signals(summary, source)
        verfahren_issues = [
            i for i in issues if i["type"] == "verfahren_halluzination"
        ]
        assert verfahren_issues == []

    def test_mehrere_verfahren_einzeln_gemeldet(self):
        summary = "Mit EMDR und Schematherapie wurde gearbeitet."
        source = "Es gab klinische Gespraeche."
        issues = detect_summary_hallucination_signals(summary, source)
        details = " ".join(i["detail"] for i in issues)
        assert "EMDR" in details
        assert "Schematherapie" in details


class TestIcdHalluzinationCritical:
    """ICD-Codes in Summary die nicht in Source vorkommen → critical."""

    def test_icd_in_summary_nicht_in_quelle_critical(self):
        summary = "Diagnose F33.2 (rezidivierende depressive Stoerung, schwer)."
        source = "Patient leidet unter wiederkehrenden depressiven Phasen."
        issues = detect_summary_hallucination_signals(summary, source)
        icd_issues = [i for i in issues if i["type"] == "icd_halluzination"]
        assert len(icd_issues) == 1
        assert icd_issues[0]["severity"] == "critical"
        assert "F33.2" in icd_issues[0]["detail"]

    def test_icd_in_quelle_keine_warnung(self):
        summary = "Hauptdiagnose F33.1."
        source = "Diagnose: F33.1 — Verlauf seit Aufnahme stabil."
        issues = detect_summary_hallucination_signals(summary, source)
        icd_issues = [i for i in issues if i["type"] == "icd_halluzination"]
        assert icd_issues == []

    def test_mehrere_erfundene_icds_einzeln(self):
        summary = "Diagnosen: F33.1, F41.1, Z73.0"
        source = "Klinischer Bericht ohne ICD-Angabe."
        issues = detect_summary_hallucination_signals(summary, source)
        icds_in_issues = [
            d["detail"] for d in issues if d["type"] == "icd_halluzination"
        ]
        assert len(icds_in_issues) == 3


class TestWortlautHalluzinationMedium:
    """Direkte-Zitat-Wendungen in Summary die nicht in Source vorkommen → medium."""

    def test_zitat_wendung_erfunden(self):
        summary = 'Die Patientin sagte: "Ich kann nicht mehr."'
        source = "Patientin schilderte Erschoepfung und Ueberforderung."
        issues = detect_summary_hallucination_signals(summary, source)
        wendung_issues = [
            i for i in issues if i["type"] == "wortlaut_halluzination"
        ]
        assert len(wendung_issues) >= 1
        assert wendung_issues[0]["severity"] == "medium"

    def test_zitat_in_quelle_keine_warnung(self):
        summary = 'Patientin sagte: "Mir fehlt der Antrieb"'
        source = 'Im Erstgespraech sagte: "Mir fehlt der Antrieb." Klares Symptombild.'
        issues = detect_summary_hallucination_signals(summary, source)
        wendung_issues = [
            i for i in issues if i["type"] == "wortlaut_halluzination"
        ]
        assert wendung_issues == []


class TestImplausibleCount:
    """Sitzungs-Anzahl in Summary die nicht zu Quelle passt → medium."""

    def test_uebertriebene_anzahl_meldet(self):
        # 50 Sitzungen genannt, aber nur 2 Datums-Anker im Source
        summary = "Patient nahm an 50 Einzelgespraechen teil, jedes mit eigener Reflexion."
        source = "10.03.2026 — Einzelgespraech. 12.03.2026 — Einzelgespraech."
        issues = detect_summary_hallucination_signals(summary, source)
        anzahl_issues = [
            i for i in issues if i["type"] == "anzahl_implausibel"
        ]
        assert len(anzahl_issues) >= 1
        assert anzahl_issues[0]["severity"] == "medium"

    def test_plausible_anzahl_keine_warnung(self):
        summary = "Patientin nahm an 5 Sitzungen teil."
        source = (
            "10.03.2026 — Sitzung 1. 12.03.2026 — Sitzung 2. "
            "14.03.2026 — Sitzung 3. 16.03.2026 — Sitzung 4. "
            "18.03.2026 — Sitzung 5."
        )
        issues = detect_summary_hallucination_signals(summary, source)
        anzahl_issues = [
            i for i in issues if i["type"] == "anzahl_implausibel"
        ]
        assert anzahl_issues == []

    def test_kleine_zahl_nie_gemeldet(self):
        # Auch wenn 3 Sitzungen vs 0 Datums-Anker — bei n<=5 nicht melden
        summary = "Es gab 3 Einzelgespraeche."
        source = "Klinischer Verlauf ohne explizite Datums-Anker."
        issues = detect_summary_hallucination_signals(summary, source)
        anzahl_issues = [
            i for i in issues if i["type"] == "anzahl_implausibel"
        ]
        assert anzahl_issues == []


class TestNoIssuesForFaithfulSummary:
    """Eine quellentreue Summary darf keine Issues haben."""

    def test_faithful_summary_keine_issues(self):
        summary = (
            "### Sitzungsübersicht\n"
            "8 Einzelgespraeche zwischen 01.03. und 15.03., zusaetzlich "
            "2 Gruppensitzungen.\n\n"
            "### Bearbeitete Themen\n"
            "Selbstabwertung im Familienkontext (am 02.03., 09.03.). "
            "Patientin bringt Gefuehl der Nicht-Anerkennung ein.\n\n"
            "### Therapeutische Interventionen\n"
            "Es wurde an Beziehungsmustern gearbeitet, mit Anteilearbeit "
            "und IFS.\n\n"
            "### Beobachtete Entwicklung\n"
            "Verlauf im Protokoll als stabilisierend beschrieben."
        )
        source = (
            "01.03.2026 — Einzelgespraech: Patientin beschreibt Selbstabwertung "
            "im Familienkontext. 02.03.2026 — Vertiefung des Themas. "
            "Heute IFS-Sitzung mit Anteilearbeit. 09.03.2026 — Patientin bringt "
            "erneut Gefuehl der Nicht-Anerkennung ein. 15.03.2026 — Stabilisierend."
        )
        issues = detect_summary_hallucination_signals(summary, source)
        # Erlaube max 0 critical, 0 high
        critical = [i for i in issues if i["severity"] == "critical"]
        high = [i for i in issues if i["severity"] == "high"]
        assert critical == []
        assert high == []


class TestEdgeCases:
    def test_leere_summary(self):
        assert detect_summary_hallucination_signals("", "Quelle hier.") == []

    def test_leere_quelle(self):
        assert detect_summary_hallucination_signals("Summary hier.", "") == []

    def test_beide_leer(self):
        assert detect_summary_hallucination_signals("", "") == []
