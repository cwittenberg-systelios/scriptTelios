#!/usr/bin/env python3
"""
s0_run.py - S0-Prompt-Prototyp "Thematischer Entlassbericht" ohne UI.

Schickt fuer jeden Eval-Fall (EB-FrauM, EB-HerrR) zwei Jobs an das laufende
scriptTelios-Backend (P4 / workflow=entlassbericht):

  statusquo   - Standard-Anweisung aus GET /api/workflows, Fokus-Themen leer
  thematisch  - Block A (5-teilige thematische Anweisung) + Block B (Fallformel)

Jobs laufen SEQUENZIELL (OLLAMA_NUM_PARALLEL=1). Ergebnisse landen in
--out als <fall>__<variante>.md (Berichtstext) und .json (Metadaten,
Stage-1-Summary, QC), dazu eine summary.md.

Aufruf (auf einem Rechner, der das Backend erreicht):

    python3 s0_run.py --eval-dir /pfad/scripTelioEvalData \
        --user c.wittenberg --secret "$CONFLUENCE_SHARED_SECRET"

    # Backend ohne Auth (AUTH_ENABLED=False, z.B. direkt auf dem Pod):
    python3 s0_run.py --eval-dir ... --base-url http://localhost:8000

Optionen: --cases, --variants, --model, --style-file, --dry-run, --timeout.
Braucht nur `requests` (pip install requests).
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("Bitte `pip install requests` ausfuehren.")

# ─────────────────────────────────────────────────────────────────────────────
# Block A - thematische Workflow-Anweisung (ersetzt WORKFLOW_INSTRUCTIONS_DEFAULT)
# ─────────────────────────────────────────────────────────────────────────────

INSTRUCTIONS_THEMATISCH = """\
Schreibe den psychotherapeutischen Verlaufsteil eines Entlassberichts als
zusammenhängenden Fließtext ohne Überschriften, ohne Aufzählungen, ohne
Einleitung und ohne Abschluss.

Der Bericht folgt NICHT der Reihenfolge der Therapieformen, sondern dem
therapeutischen Prozess des Klienten/der Klientin. Er hat FÜNF Teile, die
nahtlos ineinander übergehen (ALLE FÜNF MÜSSEN VORKOMMEN):

Teil 1 – AUFTRAG (kurz, 1 Absatz):
Mit welchem Anliegen und welchem Veränderungswunsch kam der Klient/die
Klientin – in seinen/ihren eigenen Worten, wie im Aufnahmegespräch und in
den Auftragsklärungen dokumentiert. Dazu der Zustand zu Therapiebeginn.

Teil 2 – ERARBEITUNG DES ZENTRALEN THEMAS (1–2 Absätze):
Welches Muster wurde im Verlauf als zentral erkannt (siehe FOKUS-THEMEN,
falls angegeben; sonst aus den dokumentierten Hypothesen der Verlaufsdoku).
Beschreibe, wie sich dieses Muster gezeigt hat, mit welcher Sinnhaftigkeit
es gewürdigt wurde (Schutzfunktion, biographischer Kontext) und wann/wo im
Verlauf es erarbeitet wurde. Erkläre das Muster HIER EINMAL vollständig –
in den folgenden Teilen wird es nicht neu hergeleitet, sondern nur
weitergeführt.

Teil 3 – PROZESSFORTSCHRITTE (Hauptteil):
Für JEDE dokumentierte Therapieform – Einzeltherapie, Gruppentherapie,
nonverbale Therapien (Kunst-, Musik-, Körperpsychotherapie/Körperarbeit) –
ein EIGENER Absatz. Jeder Absatz beantwortet: Welcher neue Schritt im
Umgang mit dem zentralen Thema wurde GENAU DORT möglich? Welcher Wendepunkt,
welche konkrete Erfahrung, welche Beziehungsdynamik? Nur dokumentierte
Verfahren nennen. Keine Wiederholung dessen, was Teil 2 schon erklärt hat –
ein kurzer Rückbezug („dieses Muster zeigte sich in der Gruppe darin, dass
…“) genügt. Eine Therapieform darf nur fehlen, wenn die Quellen sie nicht
dokumentieren.

Teil 4 – REFLEXION UND SYMPTOMVERÄNDERUNG (kompakt):
Wie der Klient/die Klientin den eigenen Prozess zum Abschluss reflektiert
(sofern eine Prozessreflexion vorliegt: in indirekter Rede, ohne Zitate,
ohne Dank/Feedback ans Team). Dann die Symptomatik im Vergleich zur
Aufnahme, verbliebener Bedarf, Ressourcen, Prognose. Prä-/Post-Testwerte nur,
wenn sie wörtlich in den Quellen stehen.

Teil 5 – THERAPIEEMPFEHLUNGEN (kompakter Abschluss, DARF NICHT FEHLEN):
Empfehlungen für die ambulante Weiterbehandlung als Vertiefung des in
Teil 2–3 beschriebenen Weges: Therapieform, Schwerpunkte, Frequenz,
Nachsorge.

Zuordnung zur Gesamtstruktur: Teil 1–3 bilden den Behandlungsverlauf
(~70 %), Teil 4 die Epikrise (~20 %), Teil 5 die Empfehlungen (~10 %).
Stil folgt der Vorlage (Wir-Sicht oder empathische 3. Person), NIE
objektiv-distanzierter Berichtston.
"""

# ─────────────────────────────────────────────────────────────────────────────
# Block B - handgemachte Fallformeln (Fokus-Themen) je Fall
# ─────────────────────────────────────────────────────────────────────────────

CASES: dict[str, dict] = {
    "EB-FrauM": {
        "patientenname": "Frau Musterfrau",
        "geschlecht": "w",
        "fokus": """\
AUFTRAG (Aufnahme 17.12.): innere Entlastung und emotionale Selbstregulation;
besserer Umgang mit Angst, innerer Anspannung und Grübeln; weniger
Perfektionismus und Selbstabwertung; ein freundlicherer, akzeptierender
Umgang mit sich selbst; eigene Bedürfnisse klarer wahrnehmen und vertreten,
sich in Beziehungen weniger zurücknehmen.

ZENTRALES THEMA 1 – Angst als alter Schutz, innere Sicherheit aus sich
selbst: Angst vor Krankheit, Kontrollverlust und Sterben als Schutz- und
Beziehungsregulationsmechanismus verstanden (Einzel 23.12.); der als „Tod“
benannte Anteil verliert sein bedrohliches Bild, Traumahintergrund mit
kontrollierendem Vater erarbeitet (Einzel 30.12.); Angstkrise mit
Ohnmachtserleben, Pendeln zwischen Angst und sicherem Ort in der
Körperarbeit (30.12.); kontrollierender, abwertender Anteil als Schutz vor
Überflutung (Einzel 08.01.); Angstanteil ernst nehmen, aber nicht allein
entscheiden lassen (Körperarbeit 05.01.).

ZENTRALES THEMA 2 – Anpassung und Funktionieren vs. eigene Bedürfnisse und
Grenzen (Selbstwert): Grenzen bei Bekannten schwerer als bei Fremden, immer
„beim Gegenüber“ (Bezugsgruppe 06.01.); Nicht-Zeigen eines gelungenen
Bildes als Schutz (Kunsttherapie 09.01., Einzel 13.01.); Elternbesuch als
Belastungsprobe zwischen Bindungswunsch und Selbstschutz (Einzel 13.01.,
Anteile-Aufstellung Gruppe non-verbal 14.01., Körperarbeit 15.01.);
danach „erstmals sich selbst in den Mittelpunkt gestellt“, Muster von
Anpassung, Wachsamkeit und Funktionieren verliert Steuerungsmacht (Einzel
20.01.); Prozessreflexion 22.01.: Selbstwert neu kennengelernt, „in einer
Liebesbeziehung zu sich selbst“; Ü-Gruppe: Entscheidung zu bleiben trotz
Abreiseimpuls (25.–28.01.).

SYMPTOMVERÄNDERUNG: deutliche Reduktion von Angst (20.01.); fühlt sich
stabil, zugleich angstbesetzt vor Zuhause (26.01.); leichter Anstieg der
Belastung im Übergang. Keine Testwerte in der Verlaufsdoku.
""",
    },
    "EB-HerrR": {
        "patientenname": "Herr Rademacher",
        "geschlecht": "m",
        "fokus": """\
AUFTRAG (erste Einzelgespräche 11./15.12.): familiäre Erfahrungen und deren
Einfluss auf das aktuelle Leben verstehen; eigene Gefühle und
Verhaltensmuster verstehen, insbesondere in Beziehungen und
Selbstwahrnehmung; den blockierenden inneren „Türsteher“ verstehen, der
ihn depressiv werden lässt.

ZENTRALES THEMA 1 – Der „Türsteher“: Selbstbild der Faulheit und
Leistungs-/Funktionsdruck: großer, muskulöser, blockierender Anteil, der
andere Anteile (sportlich, sozial) an der Entfaltung hindert; emotionale
Brücke: Lehrerin nach Sitzenbleiben in der 7. Klasse – „nicht dumm, nur zu
faul“ (Einzel 15.12.); Hypothese Schutz vor Versagens- und
Beschämungserfahrungen, ADS-typische Funktionsweise (Einzel 12.01.);
Wendepunkt Kunsttherapie 08.01.: Türsteher mit humorvollem Blick als
„Fatman“ gezeichnet, daneben „Mr. Motivator“ (fliegengroß), 5×5-Minuten-
Aufgaben, Fatman hat 2 Joker; Motivation entsteht nur über ein „Wofür“
(Einzel 12.01.); Zufriedenheit als Zustand, den alte Funktions- und
Kontrollanteile stören (Einzel 21.01.).

ZENTRALES THEMA 2 – Emotionale Dämpfung („Coolness“) und der Teufelskreis
in der Partnerschaft: Muster aus der Herkunftsfamilie (funktionale Eltern,
„Jammern“ abgewertet, alkoholkranker Vater), innerer Kritiker „egal was ich
mache, es ist falsch“; systemischer Teufelskreis Rückhaltung → Distanz der
Partnerin → Ohnmacht → Rückzug (Einzel, Beziehungskrise); Perspektivwechsel:
nicht die Beziehung sichern müssen, sondern ein gutes Leben auch für die
Kinder unabhängig vom Beziehungsausgang denken; Abkehr vom Schuldkonzept
(Einzel 21.01.); Abschluss: gemeinsame Entscheidung für räumliche Trennung,
Wohnungssuche, „eigenen Raum füllen, innen wie außen“ (Abschlusskontakt
29.01.).

SYMPTOMVERÄNDERUNG: erster Durchbruch, dann Stillstand/Motivationslosigkeit
(08.01.), größere innere Ruhe bei anhaltender kritischer Stimme (12.01.);
Ü-Gruppe: viel Erkenntnis, „kaum offene Baustellen mehr“ (29.01.);
Medikation (Sertralin-Anpassung diskutiert) nicht in den Verlaufsteil.
Keine Testwerte in der Verlaufsdoku.
""",
    },
}

STYLE_PREFIX = "Orientiere dich an folgendem Text als Beispiel:"


# ─────────────────────────────────────────────────────────────────────────────
# HTTP-Helfer
# ─────────────────────────────────────────────────────────────────────────────

def auth_headers(user: str | None, secret: str | None) -> dict:
    """HMAC-Header wie im Confluence-Makro (backend/app/core/auth.py)."""
    if not user or not secret:
        return {}
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), f"{user}:{ts}".encode(), hashlib.sha256).hexdigest()
    return {
        "X-Systelios-User": user,
        "X-Systelios-Timestamp": ts,
        "X-Systelios-Signature": sig,
    }


def fetch_default_instructions(base: str, hdr: dict) -> str:
    r = requests.get(f"{base}/api/workflows", headers=hdr, timeout=30)
    r.raise_for_status()
    for w in r.json().get("workflows", []):
        if w.get("key") == "entlassbericht":
            txt = w.get("instructions_default") or ""
            if txt.strip():
                return txt
    sys.exit("Konnte instructions_default fuer 'entlassbericht' nicht aus /api/workflows lesen.")


def submit(base: str, user: str | None, secret: str | None, *, instructions: str,
           fokus: str, case_dir: Path, meta: dict, style_file: str,
           model: str | None) -> str:
    verlauf = case_dir / "tpVerlaufsdokumentation.pdf"
    vorlage = case_dir / "entlassbericht.docx"
    style_p = case_dir / style_file
    for p in (verlauf, vorlage, style_p):
        if not p.exists():
            sys.exit(f"Fehlt: {p}")
    style_text = style_p.read_text(encoding="utf-8").strip()
    if style_text.startswith(STYLE_PREFIX):
        style_text = style_text[len(STYLE_PREFIX):].strip()

    data = {
        "workflow": "entlassbericht",
        "workflow_instructions": instructions,
        "bullets": fokus or "",
        "style_text": style_text,
        "patientenname": meta["patientenname"],
        "geschlecht": meta["geschlecht"],
        "therapeut_id": user or "s0-eval",
    }
    if model:
        data["model"] = model
    files = {
        "verlaufsdoku": (verlauf.name, verlauf.read_bytes(), "application/pdf"),
        "antragsvorlage": (
            vorlage.name, vorlage.read_bytes(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    }
    r = requests.post(f"{base}/api/jobs/generate", data=data, files=files,
                      headers=auth_headers(user, secret), timeout=120)
    if r.status_code >= 400:
        sys.exit(f"POST /api/jobs/generate -> {r.status_code}: {r.text[:500]}")
    return r.json()["job_id"]


def wait(base: str, user: str | None, secret: str | None, job_id: str,
         *, timeout: int, interval: int) -> dict:
    t0 = time.time()
    last = ""
    while True:
        r = requests.get(f"{base}/api/jobs/{job_id}", headers=auth_headers(user, secret), timeout=60)
        r.raise_for_status()
        j = r.json()
        line = f"  {j.get('status')} {j.get('progress') or ''}% {j.get('progress_phase') or ''} {j.get('progress_detail') or ''}".rstrip()
        if line != last:
            print(line, flush=True)
            last = line
        if j.get("status") in ("done", "error"):
            return j
        if time.time() - t0 > timeout:
            sys.exit(f"Timeout nach {timeout}s bei Job {job_id}")
        time.sleep(interval)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default="https://api.systelios.win")
    ap.add_argument("--user", default=None, help="X-Systelios-User (bei AUTH_ENABLED)")
    ap.add_argument("--secret", default=None, help="CONFLUENCE_SHARED_SECRET (bei AUTH_ENABLED)")
    ap.add_argument("--eval-dir", required=True, type=Path, help="Wurzel von scripTelioEvalData")
    ap.add_argument("--out", default=Path("s0_results"), type=Path)
    ap.add_argument("--cases", nargs="+", default=list(CASES), choices=list(CASES))
    ap.add_argument("--variants", nargs="+", default=["statusquo", "thematisch"],
                    choices=["statusquo", "thematisch"])
    ap.add_argument("--model", default=None, help="Ollama-Modell (Default: Backend-Default)")
    ap.add_argument("--style-file", default="vorlage.txt", help="Stilbeispiel im Fallordner")
    ap.add_argument("--timeout", type=int, default=1800, help="Sekunden pro Job")
    ap.add_argument("--interval", type=int, default=10, help="Poll-Intervall in Sekunden")
    ap.add_argument("--dry-run", action="store_true", help="Nur Payload anzeigen, nichts senden")
    a = ap.parse_args()

    base = a.base_url.rstrip("/")
    hdr = auth_headers(a.user, a.secret)

    # Erreichbarkeit / Pod-Autostart: /api/health weckt den Pod nicht - bei
    # gestopptem Pod vorher ueber das Confluence-Makro bzw. control.systelios.win
    # starten. Hier nur pruefen.
    if not a.dry_run:
        try:
            requests.get(f"{base}/api/health", headers=hdr, timeout=30).raise_for_status()
        except Exception as e:  # noqa: BLE001
            sys.exit(f"Backend nicht erreichbar ({base}/api/health): {e}\n"
                     "Pod laeuft? Auth-Daten korrekt?")

    default_instr = None if a.dry_run else fetch_default_instructions(base, hdr)
    a.out.mkdir(parents=True, exist_ok=True)
    rows = []

    for case in a.cases:
        meta = CASES[case]
        case_dir = a.eval_dir / case
        for variant in a.variants:
            if variant == "statusquo":
                instr, fokus = (default_instr or "<Default aus /api/workflows>"), ""
            else:
                instr, fokus = INSTRUCTIONS_THEMATISCH, meta["fokus"]
            tag = f"{case}__{variant}"
            print(f"\n=== {tag}")
            if a.dry_run:
                print(f"  verlaufsdoku={case_dir/'tpVerlaufsdokumentation.pdf'}")
                print(f"  antragsvorlage={case_dir/'entlassbericht.docx'}  style={case_dir/a.style_file}")
                print(f"  instructions[:80]={instr[:80]!r}  fokus_len={len(fokus)}")
                continue

            t0 = time.time()
            job_id = submit(base, a.user, a.secret, instructions=instr, fokus=fokus,
                            case_dir=case_dir, meta=meta, style_file=a.style_file, model=a.model)
            print(f"  job_id={job_id}")
            j = wait(base, a.user, a.secret, job_id, timeout=a.timeout, interval=a.interval)
            dur = round(time.time() - t0)

            text = j.get("result_text") or ""
            (a.out / f"{tag}.md").write_text(text, encoding="utf-8")
            payload = {
                "case": case, "variant": variant, "job_id": job_id,
                "status": j.get("status"), "error_msg": j.get("error_msg"),
                "model_used": j.get("model_used"), "duration_s": j.get("duration_s") or dur,
                "words": len(text.split()),
                "source_warnings": j.get("source_warnings"),
                "quality_check": j.get("quality_check"),
                "verlauf_summary_audit": j.get("verlauf_summary_audit"),
                "verlauf_summary_text": j.get("verlauf_summary_text"),
                "instructions": instr, "fokus": fokus,
            }
            (a.out / f"{tag}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            qc = j.get("quality_check") or {}
            rows.append((case, variant, j.get("status"), len(text.split()),
                         j.get("model_used"), payload["duration_s"],
                         qc.get("score") if isinstance(qc, dict) else None,
                         (j.get("error_msg") or "")[:60]))
            print(f"  -> {j.get('status')}  {len(text.split())} Woerter  {payload['duration_s']}s")

    if rows:
        lines = ["| Fall | Variante | Status | Wörter | Modell | Dauer s | QC-Score | Fehler |",
                 "|---|---|---|---|---|---|---|---|"]
        lines += ["| " + " | ".join("" if v is None else str(v) for v in r) + " |" for r in rows]
        (a.out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n" + "\n".join(lines))
        print(f"\nErgebnisse in {a.out.resolve()}")


if __name__ == "__main__":
    main()
