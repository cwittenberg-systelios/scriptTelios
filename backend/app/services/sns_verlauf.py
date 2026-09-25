"""
SNS-Verlaufsauswertung (v19.41) - deterministischer Kern.

Idiographische Systemmodellierung nach Schiepek: Auswertung der SNS-Exporte
(HSF-Basisbogen, individueller Fragebogen, Faktorexport) zu einem
Faktenblock, aus dem Stage B (LLM) den Bericht schreibt. Alles hier ist
reproduzierbar und ohne LLM; das Modell bekommt ausschliesslich die hier
berechneten Zahlen.

Nur numpy + Standardbibliothek (kein pandas/scipy).

Eingabe (v19.41, E1/E2): SNS-Userexport (.xlsx, beide Boegen mit Rohwerten,
Tagebuch und Kommentaren) + SNS-XML des individuellen Bogens (Itemtexte und
Faktorzuordnung, Spalten des Userexports = Fragenreihenfolge im XML). Die
Faktorstrukturen sind Konstanten (HSF-XML im Repo, ISM_FAKTOREN).

Aufbau:
  1. Parser         - SNS-Userexport (.xlsx), SNS-CSV, Fragebogen-XML
  2. HSF-Konfig     - aus resources/sns/HSF_kurz_Basis.xml (D11=B)
  3. Dynamische Komplexitaet (Schiepek & Strunk 2010) + SNS-Kritikalitaet
  4. Musterdetektion - Uebergaenge, Vorlaeufer, Phasen, Einbrueche, Recurrence
  5. analyse()      - alles zusammen -> SnsAnalyse (Arrays) + to_fakten() (JSON)
"""
from __future__ import annotations

import csv
import datetime as dt
import functools
import io
import logging
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# ── Konfiguration ─────────────────────────────────────────────────────────────

HSF_XML_PATH = Path(__file__).resolve().parents[1] / "resources" / "sns" / "HSF_kurz_Basis.xml"
HSF_QUESTIONNAIRE_NAME = "HSF kurz Basis"

# HSF-Faktor-Ids (aus dem XML): III = Symptome (hoch = Belastung), IX = ohne Wertung.
HSF_BELASTUNG_FAKTOR_IDS: frozenset[int] = frozenset({2})
HSF_NEUTRAL_FAKTOR_IDS: frozenset[int] = frozenset({8})

# Kurznamen der HSF-Items (Praefix-Match auf den Itemtext, nicht per Index).
HSF_KURZNAMEN: tuple[tuple[str, str], ...] = (
    ("In der Klinik fühle ich mich sicher", "Sicherheit Klinik"),
    ("Ich fühle mich in meiner Gruppe wohl", "Wohlfühlen Gruppe"),
    ("Wenn ich an die Kommunikation mit den Therapeuten", "Augenhöhe"),
    ("Wenn ich an die Arbeit mit den Therapeuten", "Unterstützung Therapie"),
    ("Meine Symptome sind heute", "Symptombelastung"),
    ("Heute konnte ich gut mit meinen Emotionen", "Emotionsregulation"),
    ("Heute machen mir meine Gefühle Sinn", "Sinnhaftigkeit Gefühle"),
    ("Heute konnte ich eine Beobachterposition", "Beobachterposition"),
    ("Heute habe ich Wahlmöglichkeiten", "Wahlmöglichkeiten"),
    ("Mir ist es heute gelungen, wohlwollend", "Selbstwohlwollen"),
    ("Heute habe ich die Impulse meines Körpers", "Körperwahrnehmung"),
    ("Mein Selbstwertgefühl", "Selbstwert"),
    ("Heute konnte ich gut für meine Bedürfnisse", "Selbstfürsorge"),
    ("Heute habe ich einen Zugang zu meinen Stärken", "Zugang Stärken"),
    ("Es wird mir immer besser möglich, meine Probleme", "Selbstwirksamkeit"),
    ("Wenn ich daran denke, was gerade in der Therapie", "Bedeutsamkeit Therapie"),
    ("Heute bin ich zuversichtlich", "Zuversicht"),
    ("Heute habe ich mein Energieniveau", "Energie"),
    ("Heute habe ich meinen Aufmerksamkeitsfokus", "Fokus"),
)

# Ressourcen-Komposit (Uebergangs-/Einbruchsdetektion): Faktoren IV-VIII ohne
# "Bedeutsamkeit Therapie", plus "Energie" - entspricht der Pilotliste 6-15, 17, 18.
HSF_KOMPOSIT_KURZNAMEN: frozenset[str] = frozenset({
    "Emotionsregulation", "Sinnhaftigkeit Gefühle", "Beobachterposition",
    "Wahlmöglichkeiten", "Selbstwohlwollen", "Körperwahrnehmung", "Selbstwert",
    "Selbstfürsorge", "Zugang Stärken", "Selbstwirksamkeit", "Zuversicht", "Energie",
})

# Individueller Bogen: Faktor III (id 2) = Hindernisse, hoch = Belastung, ausser umgepolt.
ISM_BELASTUNG_FAKTOR_ID = 2
ISM_ROEMISCH = {0: "I", 1: "II", 2: "III", 3: "IV", 4: "V", 5: "VI"}


@dataclass(frozen=True)
class AnalyseParams:
    """Stellschrauben der Detektion (Spec Abschnitt 4)."""
    dk_window: int = 7
    dk_z: float = 1.645             # 95 % einseitig (SNS-Logik)
    dk_min_prev: int = 5
    dk_horizon: int | None = None   # None = alle Vorwerte
    shift_k: int = 4
    min_shift: float = 10.0
    major_shift: float = 20.0
    min_gap: int = 7
    vorlaeufer_lookback: int = 7
    vorlaeufer_resonanz: int = 3
    einbruch_drop: float = 10.0
    krise_min_tage: int = 3
    konstant_sd: float = 1.0
    decken_schwelle: float = 95.0
    decken_anteil: float = 0.30
    polung_r_grenze: float = -0.3          # darunter: Warnung "Polung fraglich"
    polung_korrektur_grenze: float = -0.5  # darunter: automatisch umpolen (E3=A)
    anfang_ende_tage: int = 3


# ═════════════════════════════════════════════════════════════════════════════
# 1. Parser
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class SnsSeries:
    username: str
    questionnaire: str
    items: list[str]                      # Itemtexte aus Kopfzeile
    dates: list[dt.date]                  # Messtage (Spalte DATE)
    values: np.ndarray                    # shape (n_days, n_items), NaN = fehlend
    comments: dict[dt.date, str] = field(default_factory=dict)
    imputed: list[dt.date] = field(default_factory=list)   # 'x'-Zeilen
    imputed_values: dict[dt.date, list[float]] = field(default_factory=dict)


def parse_sns_csv(text: str, keep_imputed: bool = False) -> SnsSeries:
    """SNS-Rohdatenexport (Zeitreihen CSV).

    Format:  Z1 'sep=;'  Z2 'Username:;<code>'  Z3 'Questionnaire:;<name>'
             Z4 'DATE:;FILLING DATE:;QUESTIONNAIRE COMMENT:;"<item 1>";...'
             ab Z5 Daten. DATE = 'YYYY-MM-DD HH:MM:SS.0' (Soll-Tag),
             FILLING DATE = tatsaechlicher Ausfuellzeitpunkt (kann Folgetag sein).
             Zeilen mit fuehrendem 'x' = nicht ausgefuellt; SNS traegt dann die
             Vortageswerte ein -> als FEHLEND behandeln (ausser keep_imputed).
    Die uebertragenen Werte der x-Zeilen bleiben in `imputed_values`, damit
    sns_factor_z() sie mitzaehlen kann.
    """
    lines = text.lstrip("﻿").splitlines()
    if not lines or not lines[0].strip().lower().startswith("sep="):
        raise ValueError("kein SNS-CSV (sep=-Zeile fehlt)")
    if len(lines) < 5:
        raise ValueError("SNS-CSV unvollständig (weniger als 5 Zeilen)")
    username = lines[1].split(";", 1)[1].strip() if ";" in lines[1] else ""
    questionnaire = lines[2].split(";", 1)[1].strip() if ";" in lines[2] else ""
    rows = list(csv.reader(io.StringIO("\n".join(lines[3:])), delimiter=";"))
    header = rows[0]
    items = [h.strip() for h in header[3:] if h.strip()]
    if not items:
        raise ValueError("SNS-CSV ohne Item-Spalten")
    dates: list[dt.date] = []
    vals: list[list[float]] = []
    comments: dict[dt.date, str] = {}
    imputed: list[dt.date] = []
    imputed_values: dict[dt.date, list[float]] = {}
    for r in rows[1:]:
        if not r or not r[0].strip():
            continue
        raw = r[0].strip()
        is_x = raw.lower().startswith("x")
        try:
            d = dt.date.fromisoformat(raw.lstrip("xX").strip()[:10])
        except ValueError as exc:
            raise ValueError(f"SNS-CSV: ungültiges Datum '{raw}'") from exc
        cells = r[3:3 + len(items)]
        cells += [""] * (len(items) - len(cells))
        v = [_to_float(x) for x in cells]
        if is_x:
            imputed.append(d)
            imputed_values[d] = v
            if not keep_imputed:
                v = [math.nan] * len(items)
        if d in dates:                       # Doppelzeile: letzte gewinnt
            k = dates.index(d)
            vals[k] = v
        else:
            dates.append(d)
            vals.append(v)
        if not is_x and len(r) > 2 and r[2].strip():
            comments[d] = r[2].strip()
    if not dates:
        raise ValueError("SNS-CSV ohne Datenzeilen")
    order = np.argsort([d.toordinal() for d in dates], kind="stable")
    dates = [dates[i] for i in order]
    arr = np.array([vals[i] for i in order], dtype=float)
    return SnsSeries(username, questionnaire, items, dates, arr, comments,
                     sorted(imputed), imputed_values)


def _to_float(x: str) -> float:
    x = x.strip().replace(",", ".")
    if not x:
        return math.nan
    try:
        return float(x)
    except ValueError:
        return math.nan


def _parse_export_date(v) -> dt.date | None:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    t = str(v or "").strip()
    for fmt in ("%m/%d/%Y %H:%M", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return dt.datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    return None


def parse_sns_userexport(data: bytes) -> list[SnsSeries]:
    """Siehe _parse_sns_userexport; unterdrueckt die openpyxl-Warnung zu den
    unbekannten Excel-Extensions des SNS-Exports (tritt beim Lesen der Zeilen auf)."""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return _parse_sns_userexport(data)


def _parse_sns_userexport(data: bytes) -> list[SnsSeries]:
    """SNS-Userexport (.xlsx, 'SNS Datasheet'): ein Blatt je Fragebogen.

    Blattaufbau: 'User' / 'Questionnaire' in Spalte C/D, Kopfzeile mit
    'Trigger Date' | 'Filling Date' | ... | 'Final Comment', darunter eine
    Zeile mit den Itemnummern 1..n, dann die Tageszeilen. Filling Date ' - '
    = nicht ausgefuellt, SNS hat die Vortageswerte eingetragen -> fehlend
    (Werte bleiben in imputed_values). Itemtexte fehlen im Export: `items`
    enthaelt Platzhalter 'Item k' und wird per apply_titles() aus dem XML
    gesetzt (Spaltenreihenfolge = Fragenreihenfolge im SNS-XML).
    """
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover - Abhaengigkeit in requirements.txt
        raise ValueError("openpyxl fehlt - Userexport kann nicht gelesen werden") from exc
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - jede Lesestoerung ist ein Formatfehler
        raise ValueError(f"Userexport nicht lesbar (.xlsx erwartet): {exc}") from exc
    out: list[SnsSeries] = []
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        user = quest = ""
        head_i = None
        for i, r in enumerate(rows[:20]):
            cells = [str(c).strip() if c is not None else "" for c in r]
            for j, c in enumerate(cells[:-1]):
                if c == "User":
                    user = cells[j + 1]
                elif c == "Questionnaire":
                    quest = cells[j + 1]
            if "Trigger Date" in cells:
                head_i = i
                break
        if head_i is None:
            continue
        head = [str(c).strip() if c is not None else "" for c in rows[head_i]]
        c_date = head.index("Trigger Date")
        c_fill = head.index("Filling Date") if "Filling Date" in head else c_date + 1
        c_com = head.index("Final Comment") if "Final Comment" in head else None
        nums = rows[head_i + 1] if head_i + 1 < len(rows) else []
        item_cols = [j for j, c in enumerate(nums) if c is not None and str(c).strip() not in ("",)
                     and j not in (c_date, c_fill, c_com) and _to_float(str(c)) == _to_float(str(c))]
        if not item_cols:
            continue
        dates: list[dt.date] = []
        vals: list[list[float]] = []
        comments: dict[dt.date, str] = {}
        imputed: list[dt.date] = []
        imputed_values: dict[dt.date, list[float]] = {}
        for r in rows[head_i + 2:]:
            if not r or c_date >= len(r):
                continue
            d = _parse_export_date(r[c_date])
            if d is None:
                continue
            v = [_to_float(str(r[j])) if j < len(r) and r[j] is not None else math.nan for j in item_cols]
            fill = str(r[c_fill]).strip() if c_fill < len(r) and r[c_fill] is not None else ""
            is_x = fill in ("", "-")
            if is_x:
                imputed.append(d)
                imputed_values[d] = v
                v = [math.nan] * len(item_cols)
            elif c_com is not None and c_com < len(r) and r[c_com] and str(r[c_com]).strip():
                comments[d] = str(r[c_com]).strip()
            if d in dates:
                vals[dates.index(d)] = v
            else:
                dates.append(d)
                vals.append(v)
        if not dates:
            continue
        order = np.argsort([d.toordinal() for d in dates], kind="stable")
        out.append(SnsSeries(
            user, quest, [f"Item {k + 1}" for k in range(len(item_cols))],
            [dates[i] for i in order], np.array([vals[i] for i in order], float),
            comments, sorted(imputed), imputed_values,
        ))
    if not out:
        raise ValueError("Userexport enthält keine Fragebogen-Blätter (Kopfzeile 'Trigger Date' fehlt)")
    return out


@dataclass
class SnsQuestion:
    titel: str
    faktoren: list[int]
    gewichte: list[float]
    pol_min: str
    pol_max: str
    change_poles: bool = False
    skala: tuple[float, float] = (0.0, 100.0)


@dataclass
class SnsQuestionnaire:
    name: str
    faktoren: dict[int, dict]          # id -> {'name','beschreibung','operation'}
    fragen: list[SnsQuestion]
    quelle: str = "xml"                # 'xml' | 'hsf'


def parse_sns_questionnaire_xml(text: str) -> SnsQuestionnaire:
    """Liest <questionnaire> mit <factors> und <questions> (SLIDER).

    factors='0' bzw. '0,2' und weights='1.0' bzw. '1.0,0.5' sind kommagetrennt.
    Gleiches Format wie ism.render_sns_xml() und die SNS-Kopiervorlagen.
    """
    try:
        root = ET.fromstring(text.strip().lstrip("﻿"))
    except ET.ParseError as exc:
        raise ValueError(f"Fragebogen-XML nicht lesbar: {exc}") from exc
    if root.tag != "questionnaire":
        raise ValueError("Fragebogen-XML: Wurzelelement <questionnaire> fehlt")

    def lab(node) -> str:
        if node is None:
            return ""
        el = node.find("label")
        return ((el.text if el is not None else node.text) or "").strip()

    faktoren: dict[int, dict] = {}
    for f in root.iterfind("./factors/factor"):
        try:
            fid = int(f.get("id", ""))
        except ValueError:
            continue
        faktoren[fid] = {
            "name": lab(f.find("name")),
            "beschreibung": lab(f.find("description")),
            "operation": f.get("operation", "SUM"),
        }
    fragen: list[SnsQuestion] = []
    for q in root.iterfind("./questions/question"):
        fs = [int(x) for x in (q.get("factors") or "").split(",") if x.strip() != ""]
        ws = [float(x) for x in (q.get("weights") or "1.0").split(",") if x.strip()]
        ws = (ws + [ws[-1]] * len(fs))[:len(fs)] if ws else [1.0] * len(fs)
        mn, mx = q.find("min"), q.find("max")
        lo = _to_float(mn.get("value", "0")) if mn is not None else 0.0
        hi = _to_float(mx.get("value", "100")) if mx is not None else 100.0
        fragen.append(SnsQuestion(
            lab(q.find("title")), fs, ws, lab(mn), lab(mx),
            change_poles=(q.get("changePoles", "false").lower() == "true"),
            skala=(0.0 if math.isnan(lo) else lo, 100.0 if math.isnan(hi) else hi),
        ))
    return SnsQuestionnaire(lab(root.find("name")), faktoren, fragen)


@functools.lru_cache(maxsize=1)
def load_hsf_basis() -> SnsQuestionnaire:
    """HSF-Basisbogen als Klinikkonstante (D11=B): das SNS-Export-XML im Repo."""
    fb = parse_sns_questionnaire_xml(HSF_XML_PATH.read_text(encoding="utf-8"))
    fb.quelle = "hsf"
    return fb


def apply_titles(series: SnsSeries, fb: SnsQuestionnaire, label: str) -> SnsSeries:
    """Itemtexte per Position aus dem XML setzen (Userexport hat nur Nummern)."""
    if len(series.items) != len(fb.fragen):
        raise ValueError(
            f"{label}: Userexport hat {len(series.items)} Items, das Fragebogen-XML "
            f"{len(fb.fragen)} - passt das XML zu diesem Bogen?")
    series.items = [q.titel for q in fb.fragen]
    return series


def select_sheets(sheets: list[SnsSeries], ind_name: str | None) -> tuple[SnsSeries, SnsSeries | None]:
    """HSF-Blatt ueber den Fragebogennamen, individuelles Blatt ueber den Namen
    im XML (sonst das einzige verbleibende Blatt)."""
    hsf = [s for s in sheets if HSF_QUESTIONNAIRE_NAME.lower() in (s.questionnaire or "").lower()]
    if not hsf:
        raise ValueError(f"Userexport enthält kein Blatt „{HSF_QUESTIONNAIRE_NAME}“")
    rest = [s for s in sheets if s is not hsf[0]]
    if not rest:
        return hsf[0], None
    if ind_name:
        hit = [s for s in rest if _norm(s.questionnaire) == _norm(ind_name)]
        if len(hit) == 1:
            return hsf[0], hit[0]
    if len(rest) == 1:
        return hsf[0], rest[0]
    raise ValueError("Userexport enthält mehrere weitere Fragebögen ("
                     + ", ".join(s.questionnaire for s in rest)
                     + ") und keiner heißt wie das Fragebogen-XML")


def _norm(s: str) -> str:
    s = re.sub(r"(\.\.\.|…)\s*$", "", s.strip())
    return re.sub(r"\W+", " ", s.lower()).strip()


def match_csv_items(csv_items: list[str], fb: SnsQuestionnaire) -> dict[int, int]:
    """Ordnet CSV-Spalten (Itemtext, bei SNS ggf. mit '...' gekuerzt) den
    XML-Fragen zu: exakter Treffer, sonst eindeutiger Praefix-Treffer.
    Rueckgabe {csv_index: xml_index}; nicht zuordenbare Spalten fehlen."""
    titles = [_norm(q.titel) for q in fb.fragen]
    out: dict[int, int] = {}
    for i, it in enumerate(csv_items):
        n = _norm(it)
        if not n:
            continue
        hits = [j for j, t in enumerate(titles) if t == n] or \
               [j for j, t in enumerate(titles) if t.startswith(n) or n.startswith(t)]
        if len(hits) == 1:
            out[i] = hits[0]
    return out


def hsf_kurzname(titel: str, fallback_index: int) -> str:
    n = _norm(titel)
    for prefix, kurz in HSF_KURZNAMEN:
        p = _norm(prefix)
        if n.startswith(p) or p.startswith(n):
            return kurz
    return f"HSF-Item {fallback_index + 1}"


def sns_factor_z(values: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Reproduziert den SNS-Faktorexport: gewichtete Summe der Items
    (operation='SUM'), z-standardisiert mit SD (n-1) ueber ALLE Zeilen -
    SNS zaehlt uebertragene ('x')-Tage mit. `values` daher inkl. x-Zeilen
    mit den uebertragenen Werten uebergeben (und bereits umgepolt, falls
    changePoles)."""
    s = (values * weights).sum(1)
    sd = s.std(ddof=1) if len(s) > 1 else 0.0
    if not sd or math.isnan(sd):
        return np.zeros(len(s))
    return (s - s.mean()) / sd


# ═════════════════════════════════════════════════════════════════════════════
# 2. Dynamische Komplexitaet (Schiepek & Strunk 2010)
# ═════════════════════════════════════════════════════════════════════════════

def turning_points(y: np.ndarray) -> list[int]:
    """Umkehrpunkte wie im SNS (kalibriert 25.09.2026 gegen den SNS-Export):
    Vorzeichenwechsel zwischen aufeinanderfolgenden Differenzen ungleich 0;
    der Umkehrpunkt liegt am Ende eines Plateaus (letzter Punkt vor dem
    Richtungswechsel). Anfang und Ende des Fensters sind immer enthalten;
    ein Plateau am Fensteranfang ist KEIN Umkehrpunkt."""
    m = len(y)
    pts = [0]
    last = 0
    for k in range(m - 1):
        d = y[k + 1] - y[k]
        if d == 0:
            continue
        sg = 1 if d > 0 else -1
        if last != 0 and sg != last:
            pts.append(k)
        last = sg
    if pts[-1] != m - 1:
        pts.append(m - 1)
    return pts


def fluctuation(y: np.ndarray, lo: float = 0.0, hi: float = 100.0) -> float:
    """Fluktuationsmass F (Schiepek & Strunk 2010, SNS-identisch): Summe der
    Amplituden zwischen Umkehrpunkten, jeweils geteilt durch deren Abstand,
    normiert auf s*(m-1) mit s = Skalenbreite des Items."""
    m = len(y)
    s = hi - lo
    if m < 2 or s <= 0:
        return 0.0
    pts = turning_points(y)
    f = sum(abs(y[pts[i + 1]] - y[pts[i]]) / (pts[i + 1] - pts[i]) for i in range(len(pts) - 1))
    return float(f / (s * (m - 1)))


def distribution(y: np.ndarray, lo: float = 0.0, hi: float = 100.0) -> float:
    """Verteilungsmass D (SNS-identisch): sortierte Werte gegen die
    Gleichverteilung ueber die Itemskala [lo, hi]; ueber alle Teilintervalle
    (c,d) und alle Paare (a,b) darin werden die positiven Abweichungen
    (ideal - real, absolut in Skaleneinheiten) aufsummiert und auf die Summe
    der Idealabstaende normiert."""
    ys = np.sort(y)
    m = len(ys)
    if m < 2 or hi <= lo:
        return 0.0
    ideal = np.linspace(lo, hi, m)
    dev = dmax = 0.0
    for c in range(m - 1):
        for d in range(c + 1, m):
            for a in range(c, d):
                for b in range(a + 1, d + 1):
                    di = ideal[b] - ideal[a]
                    dy = ys[b] - ys[a]
                    dev += max(0.0, di - dy)
                    dmax += di
    return float(1.0 - dev / dmax) if dmax else 0.0


def interpolate_gaps(x: np.ndarray, limit: int = 1) -> np.ndarray:
    """Lineare Interpolation innenliegender Luecken bis Laenge `limit`
    (nicht mehr fuer die DK verwendet - SNS ueberspringt fehlende Tage)."""
    x = x.copy()
    n = len(x)
    i = 0
    while i < n:
        if np.isnan(x[i]):
            j = i
            while j < n and np.isnan(x[j]):
                j += 1
            if 0 < i and j < n and (j - i) <= limit:
                x[i:j] = np.linspace(x[i - 1], x[j], j - i + 2)[1:-1]
            i = j
        else:
            i += 1
    return x


def dynamic_complexity(x: np.ndarray, window: int = 7, lo: float = 0.0, hi: float = 100.0,
                       label: str = "end") -> np.ndarray:
    """DK je Tag. Wie im SNS laeuft das Fenster ueber die Folge der
    tatsaechlichen Messwerte (fehlende/uebertragene Tage entfallen, keine
    Interpolation). label='end': Wert am letzten Tag des Fensters (unsere
    Darstellung, 'verfuegbar ab'), label='start': am ersten Tag (SNS-Export).
    NaN, wo kein vollstaendiges Fenster endet bzw. beginnt."""
    out = np.full(len(x), np.nan)
    idx = np.where(np.isfinite(x))[0]
    vals = x[idx]
    for i in range(window - 1, len(vals)):
        w = vals[i - window + 1:i + 1]
        pos = idx[i] if label == "end" else idx[i - window + 1]
        out[pos] = fluctuation(w, lo, hi) * distribution(w, lo, hi)
    return out


def dk_critical_ci(dk: np.ndarray, z: float = 1.645, min_prev: int = 5,
                   horizon: int | None = None) -> np.ndarray:
    """True, wenn DK(t) oberhalb des einseitigen Konfidenzintervalls
    (Mittel + z*SD) der vorangegangenen DK-Werte liegt. `horizon` = Gedaechtnis
    (Anzahl vorheriger Werte), None = alle. z=1.645 ~ 95 %, 2.326 ~ 99 %."""
    out = np.zeros(len(dk), bool)
    for t in range(len(dk)):
        if np.isnan(dk[t]):
            continue
        prev = dk[:t][~np.isnan(dk[:t])]
        if horizon:
            prev = prev[-horizon:]
        if len(prev) >= min_prev and dk[t] > prev.mean() + z * prev.std(ddof=1):
            out[t] = True
    return out


# ═════════════════════════════════════════════════════════════════════════════
# 3. Statistik-Helfer (ohne scipy)
# ═════════════════════════════════════════════════════════════════════════════

def kendall_tau_trend(y: np.ndarray) -> tuple[float, float]:
    """Kendall tau-b von y gegen die Zeit + zweiseitiges p (Normalapprox.)."""
    y = y[~np.isnan(y)]
    n = len(y)
    if n < 4 or np.all(y == y[0]):
        return math.nan, math.nan
    t = np.arange(n)
    s = 0.0
    for i in range(n - 1):
        s += float(np.sum(np.sign(t[i + 1:] - t[i]) * np.sign(y[i + 1:] - y[i])))
    n0 = n * (n - 1) / 2
    _, cnt = np.unique(y, return_counts=True)
    ties = float(np.sum(cnt * (cnt - 1) / 2))
    denom = math.sqrt(n0 * (n0 - ties))
    tau = s / denom if denom else math.nan
    var = (n * (n - 1) * (2 * n + 5) - float(np.sum(cnt * (cnt - 1) * (2 * cnt + 5)))) / 18
    zval = s / math.sqrt(var) if var > 0 else 0.0
    p = math.erfc(abs(zval) / math.sqrt(2))
    return float(tau), float(p)


def nancorr(a: np.ndarray, b: np.ndarray, min_n: int = 5) -> float:
    ok = ~np.isnan(a) & ~np.isnan(b)
    if ok.sum() < min_n:
        return math.nan
    x, y = a[ok], b[ok]
    if np.std(x) == 0 or np.std(y) == 0:
        return math.nan
    return float(np.corrcoef(x, y)[0, 1])


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    """Rangkorrelation (fuer die Kalibrierung gegen SNS)."""
    ok = ~np.isnan(a) & ~np.isnan(b)
    if ok.sum() < 3:
        return math.nan
    ra = np.argsort(np.argsort(a[ok])).astype(float)
    rb = np.argsort(np.argsort(b[ok])).astype(float)
    return nancorr(ra, rb, min_n=3)


# ═════════════════════════════════════════════════════════════════════════════
# 4. Musterdetektion
# ═════════════════════════════════════════════════════════════════════════════

def detect_transitions(comp: np.ndarray, k: int = 4, min_shift: float = 10.0,
                       major_shift: float = 20.0, min_gap: int = 7) -> list[dict]:
    """Ordnungsuebergaenge als Niveausprung im Ressourcen-Komposit.

    shift(t) = mean(comp[t:t+k]) - mean(comp[t-k:t]); Kandidaten = lokale Maxima
    mit shift >= min_shift, gierig nach Groesse ausgewaehlt, Mindestabstand min_gap.
    typ = 'ordnungsuebergang' ab major_shift, sonst 'niveauverschiebung'.
    Der Uebergangstag selbst muss einen Messwert haben.
    """
    n = len(comp)
    shift = np.full(n, np.nan)
    for t in range(k, n - k + 1):
        a, b = comp[t - k:t], comp[t:t + k]
        if (not np.isnan(comp[t]) and np.sum(~np.isnan(a)) >= k - 1
                and np.sum(~np.isnan(b)) >= k - 1):
            shift[t] = np.nanmean(b) - np.nanmean(a)
    chosen: list[int] = []
    for t in np.argsort(-np.nan_to_num(shift, nan=-1e9), kind="stable"):
        if np.isnan(shift[t]) or shift[t] < min_shift:
            break
        if all(abs(t - c) >= min_gap for c in chosen):
            chosen.append(int(t))
    return [{"index": t, "shift": float(shift[t]),
             "typ": "ordnungsuebergang" if shift[t] >= major_shift else "niveauverschiebung"}
            for t in sorted(chosen)]


def critical_instability_before(dk_mean: np.ndarray, t: int, lookback: int = 7,
                                resonanz: np.ndarray | None = None,
                                min_resonanz: int = 3) -> dict | None:
    """DK-Gipfel im Fenster [t-lookback, t-1] VOR einem Uebergang. 'kritisch', wenn
    er ueber dem 75. Perzentil der gesamten DK-Reihe liegt ODER am Gipfeltag
    mindestens `min_resonanz` Items nach SNS-Logik kritisch sind."""
    lo = max(0, t - lookback)
    seg = dk_mean[lo:t]
    if len(seg) == 0 or np.all(np.isnan(seg)):
        return None
    i = lo + int(np.nanargmax(seg))
    thr = float(np.nanpercentile(dk_mean[~np.isnan(dk_mean)], 75))
    res = int(resonanz[i]) if resonanz is not None else 0
    return {"index": i, "dk": float(dk_mean[i]), "p75": thr, "resonanz": res,
            "kritisch": bool(dk_mean[i] > thr or res >= min_resonanz)}


def recovery_episodes(comp: np.ndarray, drop: float = 10.0) -> list[dict]:
    """Einbrueche im Komposit: Tag t mit comp[t] <= Referenz - drop, Referenz =
    Median der 5 vorangehenden gueltigen Tage. Erholung = erster Tag danach mit
    comp >= Referenz - drop/3. Liefert Dauer (Tage bis Erholung)."""
    eps: list[dict] = []
    n, t = len(comp), 5
    while t < n:
        prev = comp[max(0, t - 5):t]
        prev = prev[~np.isnan(prev)]
        if len(prev) >= 3 and not np.isnan(comp[t]):
            ref = float(np.median(prev))
            if comp[t] <= ref - drop:
                u = t + 1
                while u < n and (np.isnan(comp[u]) or comp[u] < ref - drop / 3):
                    u += 1
                eps.append({"start": t, "tiefe": ref - float(comp[t]),
                            "dauer": (u - t) if u < n else None,
                            "erholt": u if u < n else None})
                t = u
                continue
        t += 1
    return eps


def recurrence_matrix(X: np.ndarray) -> np.ndarray:
    """Euklidische Distanz der z-standardisierten Tagesvektoren (NaN -> 0 = Mittel)."""
    if X.size == 0:
        return np.zeros((X.shape[0], X.shape[0]))
    mu, sd = np.nanmean(X, 0), np.nanstd(X, 0, ddof=1)
    Z = np.nan_to_num((X - mu) / np.where(sd > 0, sd, 1))
    return np.sqrt(((Z[:, None, :] - Z[None, :, :]) ** 2).sum(-1))


def block_distance(R: np.ndarray, a: range, b: range) -> float:
    vals = [R[i, j] for i in a for j in b if i != j]
    return float(np.mean(vals)) if vals else math.nan


def longest_run(mask: np.ndarray, stop: int | None = None) -> tuple[int, int] | None:
    """Laengste zusammenhaengende True-Serie in mask[:stop] -> (start, ende_exkl.)."""
    best: tuple[int, int] | None = None
    n = len(mask) if stop is None else min(stop, len(mask))
    i = 0
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            if best is None or (j - i) > (best[1] - best[0]):
                best = (i, j)
            i = j
        else:
            i += 1
    return best


# ═════════════════════════════════════════════════════════════════════════════
# 5. Gesamtanalyse
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class ItemInfo:
    key: str                 # eindeutiger Schluessel ("hsf:3" / "ind:1")
    kurz: str                # Kurzname fuer Tabellen/Grafiken
    titel: str
    bogen: str               # 'hsf' | 'ind'
    col: int                 # Spalte in X
    faktor_ids: list[int]
    gewichte: list[float]
    polung: int              # +1: hoch = Ressource, -1: hoch = Belastung, 0: neutral
    change_poles: bool = False
    pol_min: str = ""
    pol_max: str = ""
    skala: tuple[float, float] = (0.0, 100.0)   # Itemskala aus dem XML (HSF: min 1 ab Item 7)
    polung_korrigiert: bool = False              # E3=A: gegen das XML automatisch umgepolt
    polung_r: float | None = None                # r mit dem Komposit VOR einer Korrektur


@dataclass
class SnsAnalyse:
    """Ergebnis der deterministischen Auswertung (Arrays fuer Grafiken +
    to_fakten() fuer das LLM / die Persistenz)."""
    params: AnalyseParams
    days: list[dt.date]
    hsf: SnsSeries
    ind: SnsSeries | None
    hsf_fb: SnsQuestionnaire
    ind_fb: SnsQuestionnaire | None
    items: list[ItemInfo]
    X: np.ndarray                     # Kalender x Items, Rohwerte (NaN fehlend)
    Xp: np.ndarray                    # umgepolt (hoch = Ressource), neutral unveraendert
    comp: np.ndarray                  # Ressourcen-Komposit
    hsf_faktoren: list[dict]          # id, name, kurz, cols, werte (Kalender)
    ism_faktoren: list[dict]          # id, name, cols, werte 0-100 (Kalender), z (Kalender)
    var_cols: list[int]
    DK: np.ndarray                    # Kalender x var_cols
    dk_mean: np.ndarray
    kritisch: np.ndarray              # Kalender x var_cols (bool)
    resonanz: np.ndarray
    uebergaenge: list[dict]
    phasen: list[dict]
    einbrueche: list[dict]
    R: np.ndarray
    flags: list[str]
    hinweise: list[str]
    fakten: dict

    def to_fakten(self) -> dict:
        return self.fakten


def _kalender(start: dt.date, end: dt.date) -> list[dt.date]:
    return [start + dt.timedelta(k) for k in range((end - start).days + 1)]


def _to_calendar(s: SnsSeries, days: list[dt.date]) -> np.ndarray:
    idx = {d: i for i, d in enumerate(days)}
    M = np.full((len(days), len(s.items)), np.nan)
    for d, row in zip(s.dates, s.values, strict=True):
        if d in idx:
            M[idx[d]] = row
    return M


def _polung_hsf(faktor_ids: list[int]) -> int:
    if not faktor_ids:
        return 1
    fid = faktor_ids[0]
    if fid in HSF_BELASTUNG_FAKTOR_IDS:
        return -1
    if fid in HSF_NEUTRAL_FAKTOR_IDS:
        return 0
    return 1


def _polung_ind(q: SnsQuestion) -> int:
    if q.faktoren and q.faktoren[0] == ISM_BELASTUNG_FAKTOR_ID and not q.change_poles:
        return -1
    return 1


def _polarize(col: np.ndarray, polung: int, skala: tuple[float, float] = (0.0, 100.0)) -> np.ndarray:
    if polung < 0:
        return skala[0] + skala[1] - col
    return col


def _kurz_ind(titel: str, faktor_ids: list[int], n: int = 34) -> str:
    t = re.sub(r"\s+", " ", titel.strip())
    t = re.sub(r"(\.\.\.|…)$", "", t).strip()
    if len(t) > n:
        t = t[:n - 1].rstrip() + "…"
    prefix = ISM_ROEMISCH.get(faktor_ids[0], "?") if faktor_ids else "?"
    return f"{prefix} · {t}"


def _rnd(x, nd: int = 1):
    if x is None:
        return None
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(xf) or math.isinf(xf):
        return None
    return round(xf, nd)


def _iso(d: dt.date | None) -> str | None:
    return d.isoformat() if d else None


def analyse_userexport(xlsx: bytes, ind_xml_text: str,
                       params: AnalyseParams | None = None) -> SnsAnalyse:
    """Produktiver Einstieg (E1/E2): SNS-Userexport + SNS-XML des individuellen Bogens."""
    ind_fb = parse_sns_questionnaire_xml(ind_xml_text)
    hsf, ind = select_sheets(parse_sns_userexport(xlsx), ind_fb.name)
    apply_titles(hsf, load_hsf_basis(), "HSF-Basisbogen")
    if ind is None:
        raise ValueError("Userexport enthält kein Blatt des individuellen Fragebogens")
    apply_titles(ind, ind_fb, "Individueller Fragebogen")
    return analyse_series(hsf, ind, ind_fb, params)


def analyse(hsf_text: str, ind_text: str | None = None, ind_xml_text: str | None = None,
            ind_fb: SnsQuestionnaire | None = None, params: AnalyseParams | None = None) -> SnsAnalyse:
    """Einstieg ueber die SNS-Zeitreihen-CSVs (Tests, Kalibrierung). Itemtexte
    stehen in der CSV-Kopfzeile und werden per Text dem XML zugeordnet."""
    hsf = parse_sns_csv(hsf_text)
    ind = parse_sns_csv(ind_text) if ind_text and ind_text.strip() else None
    if ind_fb is None and ind_xml_text and ind_xml_text.strip():
        ind_fb = parse_sns_questionnaire_xml(ind_xml_text)
    return analyse_series(hsf, ind, ind_fb, params)


def _with_imputed(s: SnsSeries) -> SnsSeries:
    """Kopie mit den von SNS uebertragenen Werten an den x-Tagen."""
    v = s.values.copy()
    for i, d in enumerate(s.dates):
        if d in s.imputed_values:
            v[i] = s.imputed_values[d]
    return SnsSeries(s.username, s.questionnaire, list(s.items), list(s.dates), v,
                     dict(s.comments), list(s.imputed), dict(s.imputed_values))


def analyse_series(hsf: SnsSeries, ind: SnsSeries | None = None, ind_fb: SnsQuestionnaire | None = None,
                   params: AnalyseParams | None = None) -> SnsAnalyse:
    """Vollstaendige deterministische Auswertung auf geparsten Reihen."""
    p = params or AnalyseParams()
    hsf_fb = load_hsf_basis()
    ind_raw = _with_imputed(ind) if ind is not None else None

    hinweise: list[str] = []
    flags: list[str] = []

    # ── Kalender (erster bis letzter AUSGEFUELLTER Tag) ─────────────────────
    all_dates = [d for d in hsf.dates if d not in hsf.imputed] + \
                ([d for d in ind.dates if d not in ind.imputed] if ind else [])
    if not all_dates:
        raise ValueError("keine ausgefüllten Messtage")
    days = _kalender(min(all_dates), max(all_dates))
    n = len(days)
    H = _to_calendar(hsf, days)

    # ── Items: HSF ─────────────────────────────────────────────────────────
    items: list[ItemInfo] = []
    hsf_map = match_csv_items(hsf.items, hsf_fb)
    if len(hsf_map) < len(hsf.items):
        fehlend = [hsf.items[i] for i in range(len(hsf.items)) if i not in hsf_map]
        hinweise.append(
            f"{len(fehlend)} HSF-Spalte(n) nicht dem Basisbogen zuordenbar: "
            + "; ".join(t[:40] for t in fehlend)
        )
    for i, titel in enumerate(hsf.items):
        q = hsf_fb.fragen[hsf_map[i]] if i in hsf_map else None
        fids = q.faktoren if q else []
        items.append(ItemInfo(
            key=f"hsf:{i}", kurz=hsf_kurzname(titel, i), titel=titel, bogen="hsf", col=i,
            faktor_ids=list(fids), gewichte=list(q.gewichte) if q else [1.0],
            polung=_polung_hsf(fids), pol_min=q.pol_min if q else "", pol_max=q.pol_max if q else "",
            skala=q.skala if q else (0.0, 100.0),
        ))

    # ── Items: individuell ─────────────────────────────────────────────────
    I = np.zeros((n, 0))
    ind_map: dict[int, int] = {}
    if ind is not None:
        I = _to_calendar(ind, days)
        if ind_fb is not None:
            ind_map = match_csv_items(ind.items, ind_fb)
            nicht = [ind.items[i] for i in range(len(ind.items)) if i not in ind_map]
            if nicht:
                hinweise.append(
                    f"{len(nicht)} Item(s) des individuellen Bogens nicht im Fragebogen-XML "
                    "gefunden: " + "; ".join(t[:40] for t in nicht)
                )
        for i, titel in enumerate(ind.items):
            q = ind_fb.fragen[ind_map[i]] if (ind_fb is not None and i in ind_map) else None
            fids = list(q.faktoren) if q else []
            items.append(ItemInfo(
                key=f"ind:{i}", kurz=_kurz_ind(titel, fids), titel=titel, bogen="ind",
                col=len(hsf.items) + i, faktor_ids=fids,
                gewichte=list(q.gewichte) if q else [1.0],
                polung=_polung_ind(q) if q else 1, change_poles=bool(q and q.change_poles),
                pol_min=q.pol_min if q else "", pol_max=q.pol_max if q else "",
                skala=q.skala if q else (0.0, 100.0),
            ))
    else:
        flags.append("KEIN_INDIVIDUELLER_BOGEN")

    X = np.hstack([H, I]) if I.shape[1] else H
    Xp = X.copy()
    for it in items:
        Xp[:, it.col] = _polarize(X[:, it.col], it.polung, it.skala)

    # ── Komposit ───────────────────────────────────────────────────────────
    comp_cols = [it.col for it in items if it.bogen == "hsf" and it.kurz in HSF_KOMPOSIT_KURZNAMEN]
    if len(comp_cols) < 4:
        # Fallback: alle HSF-Items mit Wertung
        comp_cols = [it.col for it in items if it.bogen == "hsf" and it.polung != 0]
        hinweise.append("Ressourcen-Komposit aus allen bewerteten HSF-Items gebildet (Fallback)")
    with np.errstate(all="ignore"):
        comp = np.array([np.nanmean(r) if (~np.isnan(r)).any() else np.nan
                         for r in Xp[:, comp_cols]])

    # ── Polungspruefung individueller Items (E3=A) ─────────────────────────
    # Die Polung kommt aus dem XML (Faktor III ohne changePoles = Belastung).
    # In der Praxis wird changePoles beim Umformulieren oft nicht gesetzt;
    # widerspricht der Verlauf der Annahme klar (r mit dem HSF-Komposit
    # < polung_korrektur_grenze), wird das Item automatisch umgepolt.
    for it in items:
        if it.bogen != "ind" or it.polung == 0:
            continue
        r0 = nancorr(Xp[:, it.col], comp)
        it.polung_r = r0 if np.isfinite(r0) else None
        if np.isfinite(r0) and r0 < p.polung_korrektur_grenze:
            it.polung = -it.polung
            it.polung_korrigiert = True
            Xp[:, it.col] = _polarize(X[:, it.col], it.polung, it.skala)
            r_txt = f"{r0:.2f}".replace(".", ",")
            hinweise.append(
                f"Item „{it.kurz}“ automatisch umgepolt (Verlauf korreliert mit r = "
                f"{r_txt} gegen die im XML angenommene Richtung)")
    if any(it.polung_korrigiert for it in items):
        flags.append("POLUNG_KORRIGIERT")

    # ── HSF-Faktoren ───────────────────────────────────────────────────────
    hsf_faktoren: list[dict] = []
    for fid, f in sorted(hsf_fb.faktoren.items()):
        cols = [it.col for it in items if it.bogen == "hsf" and fid in it.faktor_ids]
        if not cols:
            continue
        with np.errstate(all="ignore"):
            w = np.array([np.nanmean(r) if (~np.isnan(r)).any() else np.nan for r in X[:, cols]])
        hsf_faktoren.append({
            "id": fid, "name": f["name"], "kurz": f["beschreibung"].split("\n")[0].strip(),
            "cols": cols, "werte": w,
            "polung": -1 if fid in HSF_BELASTUNG_FAKTOR_IDS else (0 if fid in HSF_NEUTRAL_FAKTOR_IDS else 1),
        })

    # ── Variierende Items, DK, Kritikalitaet ───────────────────────────────
    with np.errstate(all="ignore"):
        sds = np.array([np.nanstd(X[:, k]) if (~np.isnan(X[:, k])).sum() > 1 else 0.0
                        for k in range(X.shape[1])])
    var_cols = [k for k in range(X.shape[1]) if sds[k] > p.konstant_sd]
    konstante = [items[k].kurz for k in range(X.shape[1]) if sds[k] <= p.konstant_sd]
    if konstante:
        flags.append("KONSTANTE_ITEMS")
    DK = (np.column_stack([dynamic_complexity(X[:, k], p.dk_window, *items[k].skala) for k in var_cols])
          if var_cols else np.zeros((n, 0)))
    with np.errstate(all="ignore"):
        dk_mean = np.array([np.nanmean(r) if (~np.isnan(r)).any() else np.nan for r in DK]) \
            if var_cols else np.full(n, np.nan)
    kritisch = (np.column_stack([dk_critical_ci(DK[:, j], p.dk_z, p.dk_min_prev, p.dk_horizon)
                                 for j in range(DK.shape[1])])
                if var_cols else np.zeros((n, 0), bool))
    resonanz = kritisch.sum(1) if var_cols else np.zeros(n, int)

    # ── Uebergaenge + Vorlaeufer ───────────────────────────────────────────
    uebergaenge = detect_transitions(comp, p.shift_k, p.min_shift, p.major_shift, p.min_gap)
    for u in uebergaenge:
        u["datum"] = days[u["index"]]
        v = critical_instability_before(dk_mean, u["index"], p.vorlaeufer_lookback,
                                        resonanz, p.vorlaeufer_resonanz)
        if v:
            v["datum"] = days[v["index"]]
        u["vorlaeufer"] = v

    # ── ISM-Faktoren (individueller Bogen) ─────────────────────────────────
    ism_faktoren: list[dict] = []
    ind_items = [it for it in items if it.bogen == "ind"]
    if ind is not None and ind_fb is not None:
        ir_idx = {d: i for i, d in enumerate(ind_raw.dates)}
        for fid, f in sorted(ind_fb.faktoren.items()):
            cols = [it.col for it in ind_items if fid in it.faktor_ids]
            if not cols:
                ism_faktoren.append({"id": fid, "name": f["name"], "cols": [], "werte": None,
                                     "z": None, "besetzt": False})
                continue
            with np.errstate(all="ignore"):
                w = np.array([np.nanmean(r) if (~np.isnan(r)).any() else np.nan
                              for r in Xp[:, cols]])
            # z wie im SNS (gewichtete Summe, z ueber alle Zeilen inkl. x-Tage),
            # aber auf den gepolten Werten (hoch = Ressource) - bei Items ohne
            # Umpolung identisch mit dem SNS-Faktorwert.
            raw_cols = [c - len(hsf.items) for c in cols]
            V = ind_raw.values[:, raw_cols].astype(float).copy()
            wts = []
            for c in cols:
                it = items[c]
                j = it.faktor_ids.index(fid)
                wts.append(it.gewichte[j] if j < len(it.gewichte) else 1.0)
                V[:, cols.index(c)] = _polarize(V[:, cols.index(c)], it.polung, it.skala)
            ok = ~np.isnan(V).any(1)
            zraw = np.full(len(ind_raw.dates), np.nan)
            if ok.sum() > 1:
                zraw[ok] = sns_factor_z(V[ok], np.array(wts))
            z_cal = np.full(n, np.nan)
            for d, zi in zip(ind_raw.dates, zraw, strict=True):
                if d in ir_idx and d in days and d not in ind.imputed:
                    z_cal[days.index(d)] = zi
            ism_faktoren.append({"id": fid, "name": f["name"], "cols": cols, "werte": w,
                                 "z": z_cal, "besetzt": True})
        if any(not f["besetzt"] for f in ism_faktoren):
            flags.append("ISM_FAKTOR_UNBESETZT")
    elif ind is not None:
        flags.append("ISM_OHNE_ZUORDNUNG")

    # ── Phasen ─────────────────────────────────────────────────────────────
    grenzen = sorted({u["index"] for u in uebergaenge
                      if u["typ"] == "ordnungsuebergang"
                      or (u["vorlaeufer"] and u["vorlaeufer"]["kritisch"])})
    erster = grenzen[0] if grenzen else n
    krise: tuple[int, int] | None = None
    fI = next((f for f in ism_faktoren if f["id"] == 0 and f["besetzt"]), None)
    if fI is not None and fI["z"] is not None and np.isfinite(fI["z"]).sum() >= p.krise_min_tage:
        mask = np.nan_to_num(fI["z"], nan=0.0) < 0
        krise = longest_run(mask, erster)
    else:
        vor = comp[:erster]
        if np.isfinite(vor).sum() >= 2 * p.krise_min_tage:
            med = float(np.nanmedian(vor))
            mask = np.nan_to_num(comp, nan=med) < med
            krise = longest_run(mask, erster)
    if krise and (krise[1] - krise[0]) >= p.krise_min_tage and krise[0] > 0:
        grenzen = sorted(set(grenzen) | {krise[0]})
    else:
        krise = None
    bounds = [0] + grenzen + [n]
    phasen: list[dict] = []
    for k in range(len(bounds) - 1):
        a, b = bounds[k], bounds[k + 1]
        if b <= a:
            continue
        typ = "verlauf"
        if krise and a == krise[0]:
            typ = "krise"
        elif k == 0:
            typ = "ankommen"
        elif any(u["index"] == a and u["typ"] == "ordnungsuebergang" for u in uebergaenge):
            typ = "uebergang"
        elif a in grenzen:
            typ = "niveauverschiebung"
        with np.errstate(all="ignore"):
            ph = {
                "id": k + 1, "label": f"P{k + 1}", "start": days[a], "ende": days[b - 1],
                "start_index": a, "ende_index": b, "tage": b - a, "typ": typ,
                "komposit_mittel": float(np.nanmean(comp[a:b])) if np.isfinite(comp[a:b]).any() else None,
                "dk_mittel": float(np.nanmean(dk_mean[a:b])) if np.isfinite(dk_mean[a:b]).any() else None,
                "resonanz_max": int(resonanz[a:b].max()) if b > a else 0,
                "name": None,
            }
        phasen.append(ph)

    # ── Einbrueche ─────────────────────────────────────────────────────────
    einbrueche = recovery_episodes(comp, p.einbruch_drop)
    for e in einbrueche:
        e["datum"] = days[e["start"]]
        e["erholt_am"] = days[e["erholt"]] if e["erholt"] is not None else None

    # ── Recurrence ─────────────────────────────────────────────────────────
    R = recurrence_matrix(X[:, var_cols]) if var_cols else np.zeros((n, n))
    blk = min(7, max(1, n // 4))
    rec = {
        "block_anfang": _rnd(block_distance(R, range(0, blk), range(0, blk))),
        "block_ende": _rnd(block_distance(R, range(n - blk, n), range(n - blk, n))),
        "anfang_ende": _rnd(block_distance(R, range(0, blk), range(n - blk, n))),
        "blocklaenge": blk,
        "phasen_distanz": [[_rnd(block_distance(R, range(a["start_index"], a["ende_index"]),
                                                range(b["start_index"], b["ende_index"])))
                            for b in phasen] for a in phasen],
        "einbruch_distanz": [
            {"datum": _iso(e["datum"]),
             "zu_anfang": _rnd(float(np.mean(R[e["start"], 0:blk]))),
             "zu_ende": _rnd(float(np.mean(R[e["start"], n - blk:n])))}
            for e in einbrueche
        ],
    }

    # ── ISM-Kennwerte ──────────────────────────────────────────────────────
    haupt = next((u for u in uebergaenge if u["typ"] == "ordnungsuebergang"), None) \
        or (max(uebergaenge, key=lambda u: u["shift"]) if uebergaenge else None)
    krisen_phase = next((ph for ph in phasen if ph["typ"] == "krise"), None)
    letzte = phasen[-1] if phasen else None

    def _sprung(w: np.ndarray) -> float | None:
        if haupt is None or w is None:
            return None
        t, k = haupt["index"], p.shift_k
        a, b = w[max(0, t - k):t], w[t:t + k]
        if np.isfinite(a).sum() == 0 or np.isfinite(b).sum() == 0:
            return None
        return float(np.nanmean(b) - np.nanmean(a))

    def _mean_in(w: np.ndarray | None, ph: dict | None) -> float | None:
        if w is None or ph is None:
            return None
        seg = w[ph["start_index"]:ph["ende_index"]]
        return float(np.nanmean(seg)) if np.isfinite(seg).any() else None

    def _min_in(w: np.ndarray | None, ph: dict | None) -> float | None:
        if w is None or ph is None:
            return None
        seg = w[ph["start_index"]:ph["ende_index"]]
        return float(np.nanmin(seg)) if np.isfinite(seg).any() else None

    ism_out: dict | None = None
    if ind is not None and ind_fb is not None:
        fak_list = []
        for f in ism_faktoren:
            w = f["werte"]
            tau = kendall_tau_trend(w) if w is not None else (math.nan, math.nan)
            fak_list.append({
                "id": f["id"], "roemisch": ISM_ROEMISCH.get(f["id"], "?"), "name": f["name"],
                "besetzt": f["besetzt"],
                "items": [items[c].kurz for c in f["cols"]],
                "item_titel": [items[c].titel for c in f["cols"]],
                "phasenmittel": {ph["label"]: _rnd(_mean_in(w, ph)) for ph in phasen},
                "sprung_uebergang": _rnd(_sprung(w)),
                "krisen_minimum": _rnd(_min_in(w, krisen_phase)),
                "endniveau": _rnd(_mean_in(w, letzte)),
                "mittel": _rnd(float(np.nanmean(w))) if w is not None and np.isfinite(w).any() else None,
                "tau": _rnd(tau[0], 2),
            })
        besetzt = [f for f in fak_list if f["besetzt"]]
        spruenge = [f for f in besetzt if f["sprung_uebergang"] is not None]
        tragend = [f["roemisch"] for f in sorted(spruenge, key=lambda f: -f["sprung_uebergang"])]
        anker = max((f for f in besetzt if f["krisen_minimum"] is not None),
                    key=lambda f: f["krisen_minimum"], default=None)
        langsam = min((f for f in besetzt if f["endniveau"] is not None),
                      key=lambda f: (f["endniveau"], f["sprung_uebergang"] or 0), default=None)
        if anker:
            flags.append("ISM_ANKER_FAKTOR")
        if langsam:
            flags.append("ISM_LANGSAMSTER_FAKTOR")
        neg_serie = None
        if fI is not None and fI["z"] is not None:
            run = longest_run(np.nan_to_num(fI["z"], nan=0.0) < 0)
            if run and run[1] - run[0] >= 2:
                neg_serie = {"start": _iso(days[run[0]]), "ende": _iso(days[run[1] - 1]),
                             "tage": run[1] - run[0]}
        ism_out = {
            "quelle": ind_fb.quelle, "fragebogen_name": ind_fb.name,
            "faktoren": fak_list,
            "unbesetzt": [f["roemisch"] for f in fak_list if not f["besetzt"]],
            "tragende_faktoren": tragend,
            "anker_faktor": anker["roemisch"] if anker else None,
            "langsamster_faktor": langsam["roemisch"] if langsam else None,
            "faktor_I_negative_serie": neg_serie,
            "items": [{
                "kurz": it.kurz, "titel": it.titel, "faktor": ISM_ROEMISCH.get(it.faktor_ids[0], "?")
                if it.faktor_ids else None, "faktor_ids": it.faktor_ids,
                "polung": ("hoch = Belastung (umgepolt)" if it.polung < 0 else "hoch = Ressource")
                + (" – automatisch korrigiert" if it.polung_korrigiert else ""),
                "polung_korrigiert": it.polung_korrigiert,
                "pol_min": it.pol_min, "pol_max": it.pol_max,
                "ereignisbezogen": bool(re.search(r"\b(heute|einen schritt|gewagt|geschafft)\b",
                                                  it.titel, re.I)),
            } for it in ind_items],
        }
    elif ind is not None:
        ism_out = {"quelle": None, "fragebogen_name": ind.questionnaire, "faktoren": [],
                   "unbesetzt": [], "tragende_faktoren": [], "anker_faktor": None,
                   "langsamster_faktor": None, "faktor_I_negative_serie": None,
                   "items": [{"kurz": it.kurz, "titel": it.titel, "faktor": None,
                              "faktor_ids": [], "polung": "hoch = Ressource (angenommen)",
                              "pol_min": "", "pol_max": "", "ereignisbezogen": False}
                             for it in ind_items]}

    # ── Polungs-Plausibilitaet ─────────────────────────────────────────────
    polung_check = []
    for it in items:
        if it.polung == 0:
            continue
        r = nancorr(Xp[:, it.col], comp) if it.col not in comp_cols else math.nan
        fraglich = bool(np.isfinite(r) and r < p.polung_r_grenze)
        if it.bogen == "ind" or fraglich:
            polung_check.append({"item": it.kurz, "r": _rnd(r, 2), "fraglich": fraglich,
                                 "korrigiert": it.polung_korrigiert,
                                 "r_vor_korrektur": _rnd(it.polung_r, 2) if it.polung_korrigiert else None})
    if any(c["fraglich"] for c in polung_check):
        flags.append("POLUNG_FRAGLICH")

    # ── Trends, Anfang/Ende, Kopplung ──────────────────────────────────────
    k3 = p.anfang_ende_tage

    def _ae(w: np.ndarray) -> tuple[float | None, float | None]:
        v = w[np.isfinite(w)]
        if len(v) < 2:
            return None, None
        return float(np.mean(v[:k3])), float(np.mean(v[-k3:]))

    item_rows = []
    for it in items:
        col = X[:, it.col]
        a, e = _ae(col)
        tau, pv = kendall_tau_trend(col)
        item_rows.append({
            "kurz": it.kurz, "bogen": it.bogen, "titel": it.titel,
            "faktor": (next((f["name"] for f in hsf_faktoren if it.col in f["cols"]), None)
                       if it.bogen == "hsf" else
                       (ISM_ROEMISCH.get(it.faktor_ids[0]) if it.faktor_ids else None)),
            "polung": {1: "+", -1: "−", 0: "neutral"}[it.polung],
            "sd": _rnd(sds[it.col]), "konstant": it.kurz in konstante,
            "anfang": _rnd(a), "ende": _rnd(e),
            "delta": _rnd(e - a) if a is not None and e is not None else None,
            "tau": _rnd(tau, 2), "p": _rnd(pv, 3),
            "decke": _decke(col[-7:], p),
        })
    if any(r["decke"] and r["polung"] == "+" and not r["konstant"] for r in item_rows):
        flags.append("DECKENEFFEKT_ENDE")

    hsf_rows = []
    for f in hsf_faktoren:
        a, e = _ae(f["werte"])
        tau, pv = kendall_tau_trend(f["werte"])
        hsf_rows.append({
            "id": f["id"], "name": f["name"], "kurz": f["kurz"], "items": [items[c].kurz for c in f["cols"]],
            "polung": {1: "+", -1: "−", 0: "neutral"}[f["polung"]],
            "mittel": _rnd(float(np.nanmean(f["werte"]))) if np.isfinite(f["werte"]).any() else None,
            "anfang": _rnd(a), "ende": _rnd(e),
            "delta": _rnd(e - a) if a is not None and e is not None else None,
            "tau": _rnd(tau, 2), "p": _rnd(pv, 3),
            "phasenmittel": {ph["label"]: _rnd(_mean_in(f["werte"], ph)) for ph in phasen},
        })
    ca, ce = _ae(comp)
    ctau, cp = kendall_tau_trend(comp)

    kopplung = []
    reihen = [(f["kurz"] or f["name"], f["werte"]) for f in hsf_faktoren if f["polung"] != 0] + \
             [(f["name"], f["werte"]) for f in ism_faktoren if f["besetzt"]]
    for i in range(len(reihen)):
        for j in range(i + 1, len(reihen)):
            r0 = nancorr(reihen[i][1], reihen[j][1])
            r1 = nancorr(reihen[i][1][:-1], reihen[j][1][1:])
            if np.isfinite(r0):
                kopplung.append({"a": reihen[i][0], "b": reihen[j][0], "r": _rnd(r0, 2),
                                 "r_lag1": _rnd(r1, 2)})
    kopplung.sort(key=lambda k: -abs(k["r"] or 0))

    # ── Luecken ────────────────────────────────────────────────────────────
    hsf_set = set(hsf.dates) - set(hsf.imputed)
    hsf_fehlend = [d for d in days if d not in hsf_set]
    ind_real = (set(ind.dates) - set(ind.imputed)) if ind else set()
    ind_fehlend = [d for d in days if ind_real and d >= min(ind_real) and d not in ind_real]
    if hsf.imputed or hsf_fehlend or (ind and (ind.imputed or ind_fehlend)):
        flags.append("LUECKEN")

    # ── DK-Zusammenfassung ─────────────────────────────────────────────────
    dk_out: dict = {"fenster": p.dk_window, "z": p.dk_z, "items_variierend": [items[k].kurz for k in var_cols]}
    if var_cols and np.isfinite(dk_mean).any():
        imax = int(np.nanargmax(dk_mean))
        rmax = int(np.argmax(resonanz))
        dk_out.update({
            "dk_mittel_max": {"datum": _iso(days[imax]), "wert": _rnd(dk_mean[imax], 3)},
            "resonanz_max": {"datum": _iso(days[rmax]), "anzahl": int(resonanz[rmax]),
                             "items": [items[var_cols[j]].kurz for j in range(len(var_cols)) if kritisch[rmax, j]]},
            "p75": _rnd(float(np.nanpercentile(dk_mean[np.isfinite(dk_mean)], 75)), 3),
            "kritische_tage": [
                {"datum": _iso(days[t]), "anzahl": int(resonanz[t]),
                 "items": [items[var_cols[j]].kurz for j in range(len(var_cols)) if kritisch[t, j]]}
                for t in range(n) if resonanz[t] >= 2
            ],
            "phasen_dk_mittel": {ph["label"]: _rnd(ph["dk_mittel"], 3) for ph in phasen},
        })
    else:
        dk_out.update({"dk_mittel_max": None, "resonanz_max": None, "p75": None,
                       "kritische_tage": [], "phasen_dk_mittel": {}})
        hinweise.append("Zu wenig Messtage für die Dynamische Komplexität (Fenster 7)")

    fakten = {
        "version": 1,
        "zeitraum": {"start": _iso(days[0]), "ende": _iso(days[-1]), "tage": n,
                     "messtage_hsf": len(hsf_set), "messtage_ind": len(set(ind.dates) - set(ind.imputed)) if ind else 0,
                     "tagebucheintraege": len(hsf.comments)},
        "sns": {"username": hsf.username, "hsf_fragebogen": hsf.questionnaire,
                "ind_fragebogen": ind.questionnaire if ind else None},
        "luecken": {"hsf_fehlend": [_iso(d) for d in hsf_fehlend],
                    "hsf_x": [_iso(d) for d in hsf.imputed if days[0] <= d <= days[-1]],
                    "ind_fehlend": [_iso(d) for d in ind_fehlend],
                    "ind_x": [_iso(d) for d in ind.imputed if days[0] <= d <= days[-1]] if ind else []},
        "hsf": {"faktoren": hsf_rows, "items": [r for r in item_rows if r["bogen"] == "hsf"],
                "komposit": {"items": [items[c].kurz for c in comp_cols], "anfang": _rnd(ca), "ende": _rnd(ce),
                             "delta": _rnd(ce - ca) if ca is not None and ce is not None else None,
                             "tau": _rnd(ctau, 2), "p": _rnd(cp, 3),
                             "phasenmittel": {ph["label"]: _rnd(ph["komposit_mittel"]) for ph in phasen}},
                "konstante_items": konstante},
        "ism": ism_out,
        "ind_items": [r for r in item_rows if r["bogen"] == "ind"],
        "dk": dk_out,
        "uebergaenge": [{
            "datum": _iso(u["datum"]), "shift": _rnd(u["shift"]), "typ": u["typ"],
            "vorlaeufer": ({"datum": _iso(u["vorlaeufer"]["datum"]), "dk": _rnd(u["vorlaeufer"]["dk"], 3),
                            "p75": _rnd(u["vorlaeufer"]["p75"], 3), "resonanz": u["vorlaeufer"]["resonanz"],
                            "kritisch": u["vorlaeufer"]["kritisch"]} if u["vorlaeufer"] else None),
            "phasengrenze": u["index"] in grenzen,
        } for u in uebergaenge],
        "ordnungsuebergang": _iso(haupt["datum"]) if haupt and haupt["typ"] == "ordnungsuebergang" else None,
        "phasen": [{"id": ph["id"], "label": ph["label"], "start": _iso(ph["start"]), "ende": _iso(ph["ende"]),
                    "tage": ph["tage"], "typ": ph["typ"], "komposit_mittel": _rnd(ph["komposit_mittel"]),
                    "dk_mittel": _rnd(ph["dk_mittel"], 3), "resonanz_max": ph["resonanz_max"], "name": None}
                   for ph in phasen],
        "einbrueche": [{"datum": _iso(e["datum"]), "tiefe": _rnd(e["tiefe"]), "dauer": e["dauer"],
                        "erholt_am": _iso(e["erholt_am"])} for e in einbrueche],
        "einbrueche_vor_nach": _einbrueche_vor_nach(einbrueche, haupt),
        "recurrence": rec,
        "kopplung": kopplung[:8],
        "polung_check": polung_check,
        "flags": sorted(set(flags)),
        "hinweise": hinweise,
        "parameter": {"dk_fenster": p.dk_window, "dk_z": p.dk_z, "shift_k": p.shift_k,
                      "min_shift": p.min_shift, "major_shift": p.major_shift,
                      "einbruch_drop": p.einbruch_drop},
    }

    return SnsAnalyse(
        params=p, days=days, hsf=hsf, ind=ind, hsf_fb=hsf_fb, ind_fb=ind_fb, items=items,
        X=X, Xp=Xp, comp=comp, hsf_faktoren=hsf_faktoren, ism_faktoren=ism_faktoren,
        var_cols=var_cols, DK=DK, dk_mean=dk_mean, kritisch=kritisch, resonanz=resonanz,
        uebergaenge=uebergaenge, phasen=phasen, einbrueche=einbrueche, R=R,
        flags=sorted(set(flags)), hinweise=hinweise, fakten=fakten,
    )


def _decke(tail: np.ndarray, p: AnalyseParams) -> bool:
    v = tail[np.isfinite(tail)]
    return bool(len(v) and float(np.mean(v >= p.decken_schwelle)) >= p.decken_anteil)


def _einbrueche_vor_nach(einbrueche: list[dict], haupt: dict | None) -> dict:
    if haupt is None:
        return {"vor": None, "nach": None}

    def _agg(eps: list[dict]) -> dict:
        d = [e["dauer"] for e in eps if e["dauer"] is not None]
        return {"anzahl": len(eps), "dauer_mittel": _rnd(float(np.mean(d))) if d else None,
                "dauer_max": max(d) if d else None}
    return {"vor": _agg([e for e in einbrueche if e["start"] < haupt["index"]]),
            "nach": _agg([e for e in einbrueche if e["start"] >= haupt["index"]])}


# ── Tagebuch-Quelle fuer Stage A / Pseudonymisierung ─────────────────────────

def diary_entries(a: SnsAnalyse) -> list[dict]:
    """Je Tag: HSF-Tagebuch + Kommentar zum individuellen Bogen (Kernanliegen)."""
    out = []
    kom = a.ind.comments if a.ind is not None else {}
    for d in a.days:
        t = a.hsf.comments.get(d, "")
        k = kom.get(d, "")
        if t or k:
            out.append({"datum": d.isoformat(), "tagebuch": t, "kommentar": k})
    return out
