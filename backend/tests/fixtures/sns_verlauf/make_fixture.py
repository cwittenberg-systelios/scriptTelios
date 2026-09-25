"""
Synthetische SNS-Fixture fuer die Verlaufsauswertung (v19.41, D8=A).

Erzeugt deterministisch (Seed 20260925):
  hsf.csv          40 Tage, 19 HSF-Items, Tagebuchspalte, eine x-Zeile (Tag 20),
                   ein FILLING DATE am Folgetag (Tag 5)
  individuell.csv  5 Items ab Tag 8, Itemtexte mit '...' gekuerzt, gleiche x-Zeile
  individuell.xml  Fragebogen im Format der Kopiervorlage (Faktoren 0-4 besetzt, VI leer)
  userexport.xlsx  SNS-Userexport wie im Produktivbetrieb: Blatt HSF + Blatt individueller
                   Bogen (nur Itemnummern), Tagebuch bzw. Kommentare zum Kernanliegen,
                   nicht ausgefuellte Tage mit Filling Date ' - ' (Tag 20 und ein
                   uebertragener Tag nach dem letzten Messtag)
  sollwerte.json   eingefrorene Kennwerte (siehe test_sns_verlauf.py)

Dynamik des Ressourcenniveaus (Tag 0-39):
  0-10  ~40, SD 8            Ankommen
  11-16 ~30, SD 20           Krise mit erhoehter Varianz
  17    Sprung auf ~70       Ordnungsuebergang
  25-27 zweite kleine Instabilitaet
  ab 28 ~85, SD 5            Stabilisierung

Aufruf:  python tests/fixtures/sns_verlauf/make_fixture.py [--freeze]
         --freeze schreibt sollwerte.json neu (nur nach bewusster Aenderung!).
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SEED = 20260925
START = dt.date(2026, 3, 2)
N_DAYS = 40
X_DAY = 20          # x-Zeile (nicht ausgefuellt, SNS traegt Vortag ein)
LATE_DAY = 5        # FILLING DATE am Folgetag
IND_START = 8

HSF_ITEMS = [
    "In der Klinik fühle ich mich sicher und unterstützt.",
    "Ich fühle mich in meiner Gruppe wohl.",
    "Wenn ich an die Kommunikation mit den Therapeuten denke, erlebe ich mich auf Augenhöhe.",
    "Wenn ich an die Arbeit mit den Therapeuten denke, fühle ich mich unterstützt.",
    "Meine Symptome sind heute … ",
    "Heute konnte ich gut mit meinen Emotionen umgehen.",
    "Heute machen mir meine Gefühle Sinn. ( Ich konnte sie Ereignissen zuordnen.)",
    "Heute konnte ich eine Beobachterposition in Bezug auf mein eigenes Erleben einnehmen.",
    "Heute habe ich Wahlmöglichkeiten im Umgang mit meinen Problemen erlebt.",
    "Mir ist es heute gelungen, wohlwollend auf mich und meinen Prozess zu schauen. ",
    "Heute habe ich die Impulse meines Körpers bewusst wahrgenommen.",
    "Mein Selbstwertgefühl war in meinem Erleben heute…",
    " Heute konnte ich gut für meine Bedürfnisse sorgen",
    "Heute habe ich einen Zugang zu meinen Stärken und Kompetenzen gespürt.",
    "Es wird mir immer besser möglich, meine Probleme aus eigener Kraft zu lösen.",
    "Wenn ich daran denke, was gerade in der Therapie passiert, empfinde ich das als bedeutsam für meinen Prozess. ",
    "Heute bin ich zuversichtlich, dass ich meine Ziele erreichen werde.",
    "Heute habe ich mein Energieniveau erlebt als:",
    "Heute habe ich meinen Aufmerksamkeitsfokus überwiegend gerichtet auf: ",
]

IND_ITEMS_FULL = [
    ("Heute bin ich einen Schritt näher an meinem Ziel, mich abzugrenzen und für mich einzustehen.", 0),
    ("Heute konnte ich meine Ruhe in der Natur als Kraftquelle nutzen.", 1),
    ("Heute haben Ängste und Selbstzweifel mein Handeln bestimmt.", 2),
    ("Heute habe ich mich in meinem Körper wohl und selbstfürsorglich erlebt.", 3),
    ("Heute konnte ich mich davon verabschieden, es allen recht machen zu wollen.", 4),
]

TAGEBUCH = [
    "Ankommen in der Klinik, alles noch fremd. Zimmer bezogen.",
    "Erste Gruppe, viel zugehört. Abends müde.",
    "Spaziergang im Wald, Kopf etwas freier.",
    "Einzelgespräch, über das Ziel gesprochen: mich abgrenzen.",
    "Telefonat mit meinem Partner Jonas, danach unruhig.",
    "Bogen erst am nächsten Morgen ausgefüllt, gestern war ich zu erschöpft.",
    "Kunsttherapie, Bild vom Druck in der Brust gemalt.",
    "Gruppe war anstrengend, viel Vergleichen.",
    "Neuer Bogen begonnen. Podest-Treppe: ich stehe unten.",
    "Ruhiger Tag, Lesen, Tee.",
    "Gespräch mit meiner Tochter Lea, sie vermisst mich.",
    "Ängste, Selbstzweifel: heute stark. Kaum geschlafen.",
    "Streit in der Gruppe, ich habe mich zurückgezogen.",
    "Rollenspiel in der Gruppe, ich habe zum ersten Mal Nein gesagt. Zittern danach.",
    "Nachwirkungen vom Rollenspiel, alles durcheinander.",
    "Mit Mira lange geredet, sie versteht mich.",
    "Medikation wurde heute angepasst, Dosis erhöht.",
    "Etwas hat sich gedreht: ich habe Jonas gesagt, dass ich am Wochenende nicht komme.",
    "Erstaunlich ruhig. Es allen recht machen: heute nicht.",
    "Wald, Sonne, Energie.",
    "",
    "Gruppe gut, ich habe etwas eingebracht.",
    "Belastungserprobung zu Hause geplant, etwas Sorge.",
    "Wochenende zu Hause: anstrengend, aber ich habe meine Grenzen gehalten.",
    "Rückkehr in die Klinik, erleichtert.",
    "Rückfall in alte Gedanken, Selbstzweifel kamen kurz zurück.",
    "Wieder gefangen, aber schneller raus als früher.",
    "Einzelgespräch: Rückblick auf die Krise, Muster erkannt.",
    "Stabil. Frau Dr. Berger sagt, der Verlauf sei gut.",
    "Kopfschmerzen seit zwei Tagen, neu für mich.",
    "Abschied vorbereiten, Nachsorge besprochen.",
    "Ruhig, zufrieden, klarer Kopf.",
    "Letzte Gruppe, Feedback bekommen: ich wirke klarer.",
    "Packen begonnen.",
    "Gespräch mit Jonas über die Zeit danach, sachlich geblieben.",
    "Mit Lea telefoniert, gelacht.",
    "Vorfreude und etwas Wehmut.",
    "Abschlussgespräch, Ziele für die Zeit nach der Klinik.",
    "Spaziergang, Dankbarkeit.",
    "Entlassung morgen.",
]

KOMMENTARE_I = {
    8: "Podest-Treppe: unten. Angst, Selbstzweifel: viel.",
    13: "Podest-Treppe: eine Stufe hoch! Nein gesagt.",
    17: "Podest-Treppe: oben angekommen, es allen recht machen: nein.",
    25: "Podest-Treppe: kurz runter, Selbstzweifel.",
    33: "Podest-Treppe: oben, stabil.",
}


def _level(rng: np.random.Generator) -> np.ndarray:
    lvl = np.zeros(N_DAYS)
    for t in range(N_DAYS):
        if t <= 10:
            lvl[t] = 40 + rng.normal(0, 8)
        elif t <= 16:
            lvl[t] = 30 + rng.normal(0, 20)
        elif t < 25:
            lvl[t] = 70 + rng.normal(0, 6)
        elif t <= 27:
            lvl[t] = 60 + rng.normal(0, 15)
        else:
            lvl[t] = 85 + rng.normal(0, 5)
    return np.clip(lvl, 0, 100)


def _clip(a):
    return np.clip(np.round(a), 0, 100).astype(int)


def build(rng: np.random.Generator):
    lvl = _level(rng)
    H = np.zeros((N_DAYS, 19))
    H[:, 0] = 100                                  # konstant
    H[:, 1] = _clip(75 + 0.2 * (lvl - 50) + rng.normal(0, 6, N_DAYS))
    H[:, 2] = 100                                  # konstant
    H[:, 3] = _clip(85 + 0.1 * (lvl - 50) + rng.normal(0, 5, N_DAYS))
    H[:, 4] = _clip(100 - lvl + rng.normal(0, 8, N_DAYS))          # Symptome (Belastung)
    for k in range(5, 15):
        H[:, k] = _clip(lvl + rng.normal(0, 7, N_DAYS))
    H[:, 15] = _clip(70 + 0.3 * (lvl - 50) + rng.normal(0, 8, N_DAYS))
    H[:, 16] = _clip(lvl + rng.normal(0, 7, N_DAYS))
    H[:, 17] = _clip(lvl * 0.8 + 10 + rng.normal(0, 8, N_DAYS))    # Energie
    H[:, 18] = _clip(50 + 0.4 * (lvl - 50) + rng.normal(0, 10, N_DAYS))  # Fokus

    Iv = np.zeros((N_DAYS, 5))
    Iv[:, 0] = _clip(lvl + rng.normal(0, 9, N_DAYS))               # I Ziel
    Iv[:, 1] = _clip(0.6 * lvl + 30 + rng.normal(0, 6, N_DAYS))    # II Ressource (Anker)
    Iv[:, 2] = _clip(100 - lvl + rng.normal(0, 9, N_DAYS))         # III Hindernis (belastungsseitig)
    Iv[:, 3] = _clip(lvl + rng.normal(0, 8, N_DAYS))               # IV
    Iv[:, 4] = _clip(0.5 * lvl + 15 + rng.normal(0, 7, N_DAYS))    # V (langsam)
    return H, Iv


def _csv(username: str, qname: str, items: list[str], M: np.ndarray, comments: dict[int, str],
         start_day: int, x_days: set[int], late_days: set[int]) -> str:
    lines = ["sep=;", f"Username:;{username}", f"Questionnaire:;{qname}"]
    head = ["DATE:", "FILLING DATE:", "QUESTIONNAIRE COMMENT:"] + [f'"{t}"' for t in items]
    lines.append(";".join(head))
    prev = None
    for t in range(start_day, N_DAYS):
        d = START + dt.timedelta(t)
        row = M[t]
        if t in x_days and prev is not None:
            row = prev                             # SNS traegt Vortag ein
            date_cell = f"x{d.isoformat()} 20:00:00.0"
            fill = ""
            com = ""
        else:
            date_cell = f"{d.isoformat()} 20:00:00.0"
            fd = d + dt.timedelta(1) if t in late_days else d
            fill = f"{fd.isoformat()} {'07:45:12' if t in late_days else '20:31:05'}.0"
            com = comments.get(t, "").replace('"', "'")
        cells = [date_cell, fill, f'"{com}"' if com else ""] + [str(int(v)) for v in row]
        lines.append(";".join(cells))
        prev = row
    return "\n".join(lines) + "\n"


def _xml(items, faktor_iii_ressource: bool = False) -> str:
    sys.path.insert(0, str(HERE.parents[2]))
    from app.services.ism import ISM_FAKTOREN, ISM_XML_FACTOR_COLOR
    parts = ["<questionnaire type='PROCESS' allowComments='true' randomize='true' "
             "defaultLanguage='de' languages='de' securityType='PUBLIC'>",
             "<name><![CDATA[Fixture individueller Fragebogen]]></name>",
             "<welcomeText><label lang='de'><![CDATA[<p>Guten Abend.</p>]]></label></welcomeText>",
             "<goodbyeText><label lang='de'><![CDATA[<p>Danke.</p>]]></label></goodbyeText>",
             "<instruction><label lang='de'><![CDATA[Tagebuch]]></label></instruction>",
             "<description><label lang='de'><![CDATA[Fixture]]></label></description>",
             "<factors>"]
    for f in ISM_FAKTOREN:
        parts.append(f"<factor id='{f['id']}' color='{ISM_XML_FACTOR_COLOR}' operation='SUM'>"
                     f"<name><label lang='de'><![CDATA[{f['name']}]]></label></name>"
                     f"<description><label lang='de'><![CDATA[{f['beschreibung_xml']}]]></label>"
                     "</description></factor>")
    parts.append("</factors><questions>")
    for titel, fid in items:
        cp = "true" if (fid == 2 and faktor_iii_ressource) else "false"
        parts.append("<question type='SLIDER' allowComments='false' positionLocked='false' "
                     f"weights='1.0' changePoles='{cp}' factors='{fid}'>"
                     f"<title><label lang='de'><![CDATA[{titel}]]></label></title>"
                     "<min value='0'><label lang='de'><![CDATA[gar nicht]]></label></min>"
                     "<max value='100'><label lang='de'><![CDATA[sehr]]></label></max></question>")
    parts.append("</questions><categories></categories></questionnaire>")
    return "".join(parts)


def _sheet(wb, title: str, user: str, qname: str, M: np.ndarray, comments: dict[int, str],
           start_day: int, x_days: set[int], late_days: set[int]) -> None:
    """Blatt im Layout des SNS-Userexports ('SNS Datasheet')."""
    ws = wb.create_sheet(title[:31])
    n_items = M.shape[1]
    ws.append([])
    ws.append([None, None, "SNS Datasheet"])
    ws.append([None, None, "User", user])
    ws.append([None, None, "Questionnaire", qname])
    ws.append([])
    ws.append([None, "Trigger Date", "Filling Date"] + [None] * n_items + ["Final Comment"])
    ws.append([None, None, None] + [float(k + 1) for k in range(n_items)] + [None])
    prev = None
    for t in range(start_day, N_DAYS + 1):          # + ein uebertragener Tag am Ende
        d = START + dt.timedelta(t)
        carry = t in x_days or t == N_DAYS
        row = prev if carry else M[t]
        if carry:
            fill, com = " - ", None
        else:
            fd = d + dt.timedelta(1) if t in late_days else d
            fill = fd.strftime("%m/%d/%Y") + (" 07:45" if t in late_days else " 20:31")
            com = comments.get(t) or None
        ws.append([None, d.strftime("%m/%d/%Y") + " 15:00", fill] + [float(v) for v in row] + [com])
        prev = row


def _userexport(H: np.ndarray, Iv: np.ndarray, tb: dict[int, str]) -> bytes:
    import io

    import openpyxl
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    fixed = dt.datetime(2026, 9, 25, 12, 0, 0)
    wb.properties.created = fixed
    wb.properties.modified = fixed
    _sheet(wb, "FX12345IND-HSF kurz Basis", "FX12345IND", "HSF kurz Basis", H, tb, 0, {X_DAY}, {LATE_DAY})
    _sheet(wb, "FX12345IND-Fixture individueller", "FX12345IND", "Fixture individueller Fragebogen",
           Iv, KOMMENTARE_I, IND_START, {X_DAY}, set())
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def write_all(freeze: bool = False) -> dict:
    rng = np.random.default_rng(SEED)
    H, Iv = build(rng)
    tb = {t: TAGEBUCH[t] for t in range(N_DAYS) if TAGEBUCH[t]}
    hsf_csv = _csv("FX12345IND", "HSF kurz Basis", HSF_ITEMS, H, tb, 0, {X_DAY}, {LATE_DAY})
    ind_titles = [t[:60] + "..." if len(t) > 60 else t for t, _ in IND_ITEMS_FULL]
    ind_csv = _csv("FX12345IND", "Fixture individueller Fragebogen", ind_titles, Iv, {}, IND_START,
                   {X_DAY}, set())
    xml = _xml(IND_ITEMS_FULL)
    xml_res = _xml(IND_ITEMS_FULL, faktor_iii_ressource=True)

    xlsx = _userexport(H, Iv, tb)

    (HERE / "hsf.csv").write_text(hsf_csv, encoding="utf-8")
    (HERE / "individuell.csv").write_text(ind_csv, encoding="utf-8")
    (HERE / "individuell.xml").write_text(xml, encoding="utf-8")
    (HERE / "individuell_iii_ressource.xml").write_text(xml_res, encoding="utf-8")
    (HERE / "userexport.xlsx").write_bytes(xlsx)

    out = {"hsf_csv": hsf_csv, "ind_csv": ind_csv, "xml": xml, "xlsx": xlsx}
    if freeze:
        sys.path.insert(0, str(HERE.parents[2]))
        from app.services.sns_verlauf import analyse_userexport
        a = analyse_userexport(xlsx, xml)
        f = a.fakten
        soll = {
            "zeitraum": f["zeitraum"],
            "luecken": f["luecken"],
            "ordnungsuebergang": f["ordnungsuebergang"],
            "uebergaenge": f["uebergaenge"],
            "phasen": [{k: ph[k] for k in ("label", "start", "ende", "typ")} for ph in f["phasen"]],
            "dk_mittel_max": f["dk"]["dk_mittel_max"],
            "resonanz_max": {"datum": f["dk"]["resonanz_max"]["datum"],
                             "anzahl": f["dk"]["resonanz_max"]["anzahl"]},
            "kritische_tage": [(k["datum"], k["anzahl"]) for k in f["dk"]["kritische_tage"]],
            "einbrueche": f["einbrueche"],
            "konstante_items": f["hsf"]["konstante_items"],
            "flags": f["flags"],
            "ism_unbesetzt": f["ism"]["unbesetzt"],
            "ism_tragend": f["ism"]["tragende_faktoren"],
            "ism_anker": f["ism"]["anker_faktor"],
            "ism_langsam": f["ism"]["langsamster_faktor"],
            "ism_sprung": {x["roemisch"]: x["sprung_uebergang"] for x in f["ism"]["faktoren"]},
            "recurrence": {k: f["recurrence"][k] for k in ("block_anfang", "block_ende", "anfang_ende")},
            "polung_check": f["polung_check"],
            "komposit": {k: f["hsf"]["komposit"][k] for k in ("anfang", "ende", "tau")},
        }
        (HERE / "sollwerte.json").write_text(json.dumps(soll, ensure_ascii=False, indent=2), encoding="utf-8")
        out["soll"] = soll
    return out


if __name__ == "__main__":
    res = write_all(freeze="--freeze" in sys.argv)
    if "soll" in res:
        print(json.dumps(res["soll"], ensure_ascii=False, indent=2))
    else:
        print("Fixture-Dateien geschrieben (ohne --freeze keine sollwerte.json).")
