"""
Grafiken der SNS-Verlaufsauswertung (v19.41, D3=B): matplotlib, Agg-Backend,
PNG 200 dpi, Breite 17 cm - identisch fuer Vorschau (Frontend) und DOCX.

Rueckgabe von render_all(): {schluessel: {"png_b64": str, "titel": str, "nr": int}}.
"""
from __future__ import annotations

import base64
import io
import logging

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from app.services.sns_verlauf import SnsAnalyse  # noqa: E402

logger = logging.getLogger(__name__)

CM = 1 / 2.54
WIDTH = 17 * CM
DPI = 200

# Farbrollen (kategoriale Palette, feste Reihenfolge; Akzent = Klinik-Rot)
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
ACCENT = "#971321"
GRID = "#e6e6e3"
INK = "#0b0b0b"
INK2 = "#52514e"
PHASE_BAND = ["#f4f4f2", "#ffffff"]

plt.rcParams.update({
    "font.size": 7.5, "axes.titlesize": 8.5, "axes.labelsize": 7.5,
    "axes.edgecolor": INK2, "axes.linewidth": 0.6, "xtick.color": INK2, "ytick.color": INK2,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.5, "axes.axisbelow": True,
    "legend.frameon": False, "legend.fontsize": 7, "figure.dpi": DPI, "savefig.dpi": DPI,
    "font.family": "DejaVu Sans",
})


def _png(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _xaxis(ax, a: SnsAnalyse):
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m."))
    ax.set_xlim(a.days[0], a.days[-1])


def _phase_bands(ax, a: SnsAnalyse, label: bool = True):
    for k, ph in enumerate(a.phasen):
        ax.axvspan(ph["start"], ph["ende"], color=PHASE_BAND[k % 2], zorder=0, lw=0)
        if label:
            ax.text(ph["start"], ax.get_ylim()[1], f" {ph['label']}", va="top", ha="left",
                    fontsize=6.5, color=INK2)
    for u in a.uebergaenge:
        if u["typ"] == "ordnungsuebergang":
            ax.axvline(u["datum"], color=ACCENT, lw=1.0, ls="-", zorder=3)
        elif u.get("vorlaeufer") and u["vorlaeufer"]["kritisch"]:
            ax.axvline(u["datum"], color=ACCENT, lw=0.8, ls="--", zorder=3)


def _event_markers(ax, a: SnsAnalyse, events: list[dict] | None, y: float):
    if not events:
        return
    idx = {d.isoformat(): d for d in a.days}
    for n, ev in enumerate(events, start=1):
        d = idx.get(ev.get("datum"))
        if d is None:
            continue
        ax.annotate(str(n), (d, y), xytext=(0, 3), textcoords="offset points", ha="center",
                    fontsize=6, color=INK, bbox={"boxstyle": "circle,pad=0.15", "fc": "white",
                                                  "ec": INK2, "lw": 0.5})


# ── 1 HSF-Faktorverlauf ──────────────────────────────────────────────────────

def plot_hsf_faktoren(a: SnsAnalyse, events: list[dict] | None = None) -> str:
    fak = [f for f in a.hsf_faktoren if f["polung"] != 0]
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(WIDTH, 10.5 * CM), sharex=True,
                                  gridspec_kw={"height_ratios": [3, 2], "hspace": 0.08})
    ax.plot(a.days, a.comp, color=INK, lw=2.0, label="Ressourcen-Komposit", zorder=4)
    ax.set_ylim(0, 105)
    _phase_bands(ax, a)
    _event_markers(ax, a, events, 100)
    for i, f in enumerate(fak):
        w = f["werte"]
        if f["polung"] < 0:
            w = 100 - w
            lab = f"{f['name']} {f['kurz']} (umgepolt)"
        else:
            lab = f"{f['name']} {f['kurz']}"
        ax2.plot(a.days, w, color=SERIES[i % len(SERIES)], lw=1.2, label=lab, alpha=0.95)
    ax2.set_ylim(0, 105)
    _phase_bands(ax2, a, label=False)
    ax.set_ylabel("Komposit (0–100)")
    ax2.set_ylabel("HSF-Faktoren (0–100)")
    ax.legend(loc="lower right", ncol=1)
    ax2.legend(loc="lower center", ncol=3, bbox_to_anchor=(0.5, -0.55))
    _xaxis(ax2, a)
    ax.set_title("HSF-Basisbogen: Ressourcen-Komposit und Faktoren, Phasen (P), "
                 "Ordnungsübergang (rot)", loc="left")
    return _png(fig)


# ── 2 ISM-Faktoren ───────────────────────────────────────────────────────────

def plot_ism_faktoren(a: SnsAnalyse) -> str | None:
    besetzt = [f for f in a.ism_faktoren if f["besetzt"]]
    if not besetzt:
        return None
    fI = next((f for f in besetzt if f["id"] == 0), None)
    n_small = len(besetzt)
    cols = 3
    rows = int(np.ceil(n_small / cols))
    fig = plt.figure(figsize=(WIDTH, (5.5 + 3.0 * rows) * CM))
    gs = fig.add_gridspec(rows + 1, cols, height_ratios=[2.2] + [1] * rows, hspace=0.55, wspace=0.25)
    ax = fig.add_subplot(gs[0, :])
    if fI is not None and fI["z"] is not None:
        z = fI["z"]
        colors = [SERIES[0] if v >= 0 else ACCENT for v in np.nan_to_num(z)]
        ax.bar(a.days, np.nan_to_num(z), color=colors, width=0.8, lw=0)
        ax.axhline(0, color=INK2, lw=0.6)
        ax.set_ylabel("z (SNS)")
        ax.set_title("Individueller Fragebogen: Faktor I Zielerleben (z-Werte wie SNS) "
                     "und ISM-Faktoren (0–100)", loc="left")
    else:
        ax.set_title("Individueller Fragebogen: ISM-Faktoren (0–100)", loc="left")
    _phase_bands(ax, a)
    _xaxis(ax, a)
    for k, f in enumerate(besetzt):
        axk = fig.add_subplot(gs[1 + k // cols, k % cols])
        axk.plot(a.days, f["werte"], color=SERIES[k % len(SERIES)], lw=1.3)
        axk.set_ylim(0, 105)
        _phase_bands(axk, a, label=False)
        axk.set_title(f["name"], loc="left", fontsize=7.5)
        axk.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO, interval=2))
        axk.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m."))
        axk.set_xlim(a.days[0], a.days[-1])
        axk.tick_params(labelsize=6)
    return _png(fig)


# ── 3 DK + Resonanz ──────────────────────────────────────────────────────────

def plot_dk_resonanz(a: SnsAnalyse) -> str:
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(WIDTH, 9 * CM), sharex=True,
                                  gridspec_kw={"height_ratios": [2, 1], "hspace": 0.1})
    ax.plot(a.days, a.dk_mean, color=SERIES[6], lw=1.6, label="DK (Mittel über variierende Items)")
    fin = np.isfinite(a.dk_mean)
    if fin.any():
        imax = int(np.nanargmax(a.dk_mean))
        ax.plot([a.days[imax]], [a.dk_mean[imax]], "o", color=ACCENT, ms=5, zorder=5)
        ax.annotate(f"Max {a.days[imax]:%d.%m.}", (a.days[imax], a.dk_mean[imax]),
                    xytext=(6, 2), textcoords="offset points", fontsize=6.5, color=ACCENT)
        ax.set_ylim(0, float(np.nanmax(a.dk_mean)) * 1.25 + 1e-6)
    _phase_bands(ax, a)
    ax.set_ylabel("Dynamische Komplexität")
    ax.legend(loc="upper right")
    ax2.bar(a.days, a.resonanz, color=SERIES[1], width=0.8, lw=0)
    ax2.set_ylabel("kritische Items")
    ax2.set_ylim(0, max(1, int(a.resonanz.max()) + 1))
    _phase_bands(ax2, a, label=False)
    _xaxis(ax2, a)
    ax.set_title("Dynamische Komplexität (Fenster 7 Messtage, Wert am Fensterende) und Resonanz "
                 f"(Items über dem {int(round(100 * (1 - 0.05)))}-%-Konfidenzintervall)", loc="left")
    return _png(fig)


# ── 4 Komplexitaets-Resonanz-Diagramm ────────────────────────────────────────

def plot_krd(a: SnsAnalyse) -> str:
    if not a.var_cols:
        fig, ax = plt.subplots(figsize=(WIDTH, 3 * CM))
        ax.text(0.5, 0.5, "keine variierenden Items", ha="center", va="center")
        ax.axis("off")
        return _png(fig)
    order = sorted(range(len(a.var_cols)), key=lambda j: (a.items[a.var_cols[j]].bogen != "hsf", j))
    M = a.DK[:, order].T
    labels = [a.items[a.var_cols[j]].kurz for j in order]
    n_hsf = sum(1 for j in order if a.items[a.var_cols[j]].bogen == "hsf")
    fig, ax = plt.subplots(figsize=(WIDTH, (2.5 + 0.32 * len(labels)) * CM))
    ax.grid(False)
    x = np.arange(len(a.days) + 1)
    y = np.arange(len(labels) + 1)
    im = ax.pcolormesh(x, y, np.ma.masked_invalid(M), cmap="Blues", vmin=0,
                       vmax=float(np.nanmax(M)) if np.isfinite(M).any() else 1, shading="flat")
    K = a.kritisch[:, order].T
    yy, xx = np.nonzero(K)
    ax.scatter(xx + 0.5, yy + 0.5, s=6, color=ACCENT, lw=0, zorder=3)
    if 0 < n_hsf < len(labels):
        ax.axhline(n_hsf, color=INK, lw=0.8)
    for u in a.uebergaenge:
        if u["typ"] == "ordnungsuebergang":
            ax.axvline(u["index"] + 0.5, color=ACCENT, lw=1.0)
    ax.set_yticks(np.arange(len(labels)) + 0.5)
    ax.set_yticklabels(labels, fontsize=6.3)
    ax.invert_yaxis()
    ticks = [i for i, d in enumerate(a.days) if d.weekday() == 0]
    ax.set_xticks([t + 0.5 for t in ticks])
    ax.set_xticklabels([a.days[t].strftime("%d.%m.") for t in ticks])
    ax.set_xlim(0, len(a.days))
    cb = fig.colorbar(im, ax=ax, fraction=0.025, pad=0.01)
    cb.set_label("DK", fontsize=7)
    ax.set_title("Komplexitäts-Resonanz-Diagramm (Punkt = kritisch, Linie = HSF | individuell)",
                 loc="left")
    return _png(fig)


# ── 5 Recurrence Plot ────────────────────────────────────────────────────────

def plot_recurrence(a: SnsAnalyse) -> str:
    fig, ax = plt.subplots(figsize=(WIDTH * 0.7, WIDTH * 0.7))
    ax.grid(False)
    n = len(a.days)
    im = ax.imshow(a.R, cmap="Blues_r", origin="lower", interpolation="nearest")
    for ph in a.phasen[1:]:
        ax.axhline(ph["start_index"] - 0.5, color=ACCENT, lw=0.8)
        ax.axvline(ph["start_index"] - 0.5, color=ACCENT, lw=0.8)
    ticks = [i for i, d in enumerate(a.days) if d.weekday() == 0]
    ax.set_xticks(ticks)
    ax.set_xticklabels([a.days[t].strftime("%d.%m.") for t in ticks], rotation=45, ha="right")
    ax.set_yticks(ticks)
    ax.set_yticklabels([a.days[t].strftime("%d.%m.") for t in ticks])
    ax.set_xlim(-0.5, n - 0.5)
    ax.set_ylim(-0.5, n - 0.5)
    cb = fig.colorbar(im, ax=ax, fraction=0.04, pad=0.02)
    cb.set_label("Distanz (dunkel = ähnlich)", fontsize=7)
    ax.set_title("Recurrence Plot der Tagesprofile, Phasengrenzen rot", loc="left")
    return _png(fig)


# ── 6 Hantelplot Anfang/Ende ─────────────────────────────────────────────────

def plot_hantel(a: SnsAnalyse) -> str:
    rows = [r for r in a.fakten["hsf"]["items"] if r["anfang"] is not None] + \
           [r for r in a.fakten["ind_items"] if r["anfang"] is not None]
    rows = sorted(rows, key=lambda r: (r["bogen"] != "hsf", -(r["delta"] or 0)))
    fig, ax = plt.subplots(figsize=(WIDTH, (2.5 + 0.34 * len(rows)) * CM))
    y = np.arange(len(rows))
    for i, r in enumerate(rows):
        col = SERIES[2] if (r["delta"] or 0) >= 0 else SERIES[1]
        if r["polung"] == "−":
            col = SERIES[2] if (r["delta"] or 0) <= 0 else SERIES[1]
        ax.plot([r["anfang"], r["ende"]], [i, i], color=col, lw=1.6, zorder=2)
        ax.plot(r["anfang"], i, "o", color="white", mec=INK2, ms=5, zorder=3)
        ax.plot(r["ende"], i, "o", color=col, ms=5, zorder=3)
    ax.set_yticks(y)
    ax.set_yticklabels([r["kurz"] + (" (−)" if r["polung"] == "−" else "") for r in rows], fontsize=6.5)
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.set_xlabel("Mittel der ersten (○) und letzten (●) drei Messtage")
    n_hsf = sum(1 for r in rows if r["bogen"] == "hsf")
    if 0 < n_hsf < len(rows):
        ax.axhline(n_hsf - 0.5, color=INK, lw=0.8)
    ax.set_title("Anfang und Ende je Item (grün = Entwicklung in Ressourcenrichtung)", loc="left")
    return _png(fig)


TITEL = {
    "hsf_faktoren": "HSF-Basisbogen: Komposit und Faktoren",
    "ism_faktoren": "Individueller Fragebogen: Zielerleben und ISM-Faktoren",
    "dk_resonanz": "Dynamische Komplexität und Resonanz",
    "krd": "Komplexitäts-Resonanz-Diagramm",
    "recurrence": "Recurrence Plot",
    "hantel": "Anfang und Ende je Item",
}


def render_all(a: SnsAnalyse, events: list[dict] | None = None) -> dict[str, dict]:
    """Alle Grafiken; Schluessel in Berichtsreihenfolge. Fehlende (kein ISM) fallen weg."""
    out: dict[str, dict] = {}
    plan = [
        ("hsf_faktoren", lambda: plot_hsf_faktoren(a, events)),
        ("ism_faktoren", lambda: plot_ism_faktoren(a)),
        ("dk_resonanz", lambda: plot_dk_resonanz(a)),
        ("krd", lambda: plot_krd(a)),
        ("recurrence", lambda: plot_recurrence(a)),
        ("hantel", lambda: plot_hantel(a)),
    ]
    nr = 0
    for key, fn in plan:
        try:
            png = fn()
        except Exception as exc:  # noqa: BLE001 - eine Grafik darf den Bericht nicht kippen
            logger.exception("sns_plots: Grafik %s fehlgeschlagen: %s", key, exc)
            png = None
        if png:
            nr += 1
            out[key] = {"png_b64": png, "titel": TITEL[key], "nr": nr}
    return out

