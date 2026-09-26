// ────────────────────────────────────────────────────────────────────────────
// src/panels/P7.jsx — v19.41: SNS-Verlaufsauswertung (idiographische
// Systemmodellierung nach Schiepek).
//
// Inputs:
//   A (req)  SNS-Userexport (.xlsx): beide Boegen, Rohwerte, Tagebuch, Kommentare
//   B (req)  SNS-XML des individuellen Bogens (Itemtexte + Faktorzuordnung;
//            Spaltenreihenfolge im Userexport = Fragenreihenfolge im XML)
//   C (opt)  Prompt/Modell (Default P_SNS), Vorname fuer die Pseudonymisierung
//   Action-Bar: Klient (Geschlecht + Kuerzel, Pflicht wie P1)
//
// Output (result_text = JSON, siehe app/services/sns_pipeline.py):
//   Bericht (9 Abschnitte, editierbar) + Grafiken (PNG aus dem Backend) +
//   Kennwert-Tabellen + QC. DOCX rendert POST /api/sns/docx aus dem
//   EDITIERTEN Text (F2=B); Live-QC ueber POST /api/sns/check.
// ────────────────────────────────────────────────────────────────────────────
import { useMemo, useRef, useState } from "react";
import { apiFetch, getApiBase } from "../api.js";
import { useDraftCache } from "../hooks.jsx";
import { P_SNS } from "../prompt-defaults.jsx";
import { QualityCheckPanel } from "../qa.jsx";
import { buildPatientName, friendlyError } from "../shared.js";
import { Card, Dropzone, FeedbackButton, JobModelPicker, JobProgressBar, PromptEditor } from "../ui.jsx";
import { KlientControls, useWorkflowRun, WorkflowActionBar } from "../workflow-run.jsx";
import { ANLEITUNG_USEREXPORT, ANLEITUNG_XML, ExportAnleitung } from "../sns-anleitung.jsx";

const P7_DRAFT_DEFAULT = { prompt: P_SNS, kuerzel: "", geschlecht: "", vorname: "" };

const FLAG_LABELS = {
  KEIN_INDIVIDUELLER_BOGEN: "kein individueller Bogen",
  KONSTANTE_ITEMS: "konstante Items",
  LUECKEN: "Lücken/übertragene Tage",
  DECKENEFFEKT_ENDE: "Deckeneffekt am Ende",
  ISM_FAKTOR_UNBESETZT: "ISM-Faktor unbesetzt",
  ISM_LANGSAMSTER_FAKTOR: "langsamster Faktor",
  ISM_ANKER_FAKTOR: "Anker-Faktor",
  ISM_OHNE_ZUORDNUNG: "ohne Faktorzuordnung",
  PLATEAU_ALS_EINBRUCH: "Plateau als Einbruch",
  MEDIKATION_IM_UEBERGANGSFENSTER: "Medikation im Übergangsfenster",
  SOMATIK_NEU: "neue Somatik",
  POLUNG_FRAGLICH: "Polung fraglich",
  POLUNG_KORRIGIERT: "Polung automatisch korrigiert",
  SUIZIDALITAET_IN_QUELLE: "Suizidalität/Selbstverletzung im Tagebuch",
};

function fmtDate(iso) {
  if (!iso) return "–";
  const [y, m, d] = iso.split("-");
  return `${d}.${m}.${y}`;
}
function fmtNum(v, nd = 1) {
  if (v === null || v === undefined) return "–";
  return typeof v === "number" ? v.toFixed(nd).replace(".", ",") : String(v);
}

// Kompakte Kennwert-Tabelle (nur Darstellung; Werte kommen aus fakten).
function KennwertTabelle({ title, head, rows }) {
  if (!rows || rows.length === 0) return null;
  return (
    <div style={{ margin: "10px 0" }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: "var(--st-text-soft)", marginBottom: 4 }}>{title}</div>
      <table style={{ borderCollapse: "collapse", fontSize: 11.5, width: "100%" }}>
        <thead>
          <tr>{head.map((h) => <th key={h} style={{ textAlign: "left", padding: "3px 6px", borderBottom: "1px solid var(--st-gray-border)", fontWeight: 600 }}>{h}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>{r.map((c, j) => <td key={j} style={{ padding: "3px 6px", borderBottom: "1px solid var(--st-gray-light)" }}>{c}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function P7({ toast, resumeJob, onResumed }) {
  const [jobModel, setJobModel] = useState("");
  const [xlsx, setXlsx] = useState(null);
  const [xml, setXml] = useState(null);

  const [draft, updateDraft, clearDraft] = useDraftCache("st_draft_p7", P7_DRAFT_DEFAULT);
  const { prompt, kuerzel, geschlecht, vorname } = draft;
  const draftDirty = useMemo(() => JSON.stringify(draft) !== JSON.stringify(P7_DRAFT_DEFAULT), [draft]);

  // res = geparstes Ergebnis-JSON; text = editierbarer Bericht
  const [res, setRes] = useState(null);
  const [text, setText] = useState("");
  const [resError, setResError] = useState(null);
  const [qc, setQc] = useState(null);
  const [docxBusy, setDocxBusy] = useState(false);
  const [tab, setTab] = useState("bericht");
  const textRef = useRef("");
  textRef.current = text;

  function applyResult(j) {
    setQc(j.quality_check || null);
    try {
      const parsed = JSON.parse(j.result_text || "");
      if (!parsed || typeof parsed.text !== "string" || !parsed.fakten) throw new Error("Ergebnisstruktur unvollständig");
      setRes(parsed);
      setText(parsed.text);
      setResError(null);
      setTab("bericht");
    } catch (e) {
      setRes(null); setText("");
      setResError("Das Ergebnis konnte nicht gelesen werden (" + (e.message || e) + ").");
    }
  }

  const wr = useWorkflowRun({
    workflow: "sns_verlauf", page: "p7", resumeJob, onResumed,
    onResult: applyResult,
    onError: (msg) => { setRes(null); setText(""); setResError(msg); },
  });
  const { busy, currentJobId, lastJobId } = wr;

  const patientName = buildPatientName(kuerzel, geschlecht);
  const canGenerate = !!xlsx && !!xml && !!patientName;

  function run() {
    setRes(null); setText(""); setResError(null); setQc(null);
    wr.start(prompt, "", {
      snsExport: xlsx, snsXml: xml,
      snsVorname: vorname.trim() || null,
      patientName, geschlecht,
      model: jobModel || null,
    });
  }

  // ── Live-QC auf dem editierten Text (Blur) ────────────────────────────
  async function checkNow() {
    if (!res) return;
    try {
      const r = await apiFetch(`${getApiBase()}/sns/check`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: textRef.current, result: resultPayload() }),
      });
      if (r.ok) setQc(await r.json());
    } catch (_) { /* Live-Check ist Komfort, kein Blocker */ }
  }

  // Ergebnis ohne Text/Grafiken-Duplikate fuer Check; DOCX braucht Grafiken.
  function resultPayload(withGrafiken = false) {
    if (!res) return {};
    const { text: _t, grafiken, ...rest } = res;
    return withGrafiken ? { ...rest, grafiken } : rest;
  }

  // ── DOCX-Export (Backend-Renderer, F2=B) ─────────────────────────────
  async function downloadDocx() {
    if (!res) return;
    setDocxBusy(true);
    try {
      const r = await apiFetch(`${getApiBase()}/sns/docx`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          kuerzel: res.kuerzel || patientName || "Klient", anrede: res.anrede || "Klientin",
          text: textRef.current, result: resultPayload(true),
        }),
      });
      if (!r.ok) {
        let detail = r.statusText;
        try { detail = JSON.stringify((await r.json()).detail); } catch (_) {}
        throw new Error(detail);
      }
      let filename = "ISM-Auswertung.docx";
      const cd = r.headers.get("content-disposition");
      if (cd) { const m = cd.match(/filename\*?=(?:UTF-8'')?["']?([^"';]+)["']?/i); if (m) filename = decodeURIComponent(m[1]); }
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = filename;
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      toast("DOCX heruntergeladen");
    } catch (e) {
      toast("DOCX fehlgeschlagen: " + friendlyError(e));
    } finally {
      setDocxBusy(false);
    }
  }

  function copyText() {
    navigator.clipboard?.writeText(text).then(() => toast("Bericht kopiert")).catch(() => {});
  }

  const f = res?.fakten;
  const grafiken = res?.grafiken ? Object.values(res.grafiken).sort((a, b) => a.nr - b.nr) : [];

  return (
    <div>
      <div className="page-header">
        <div className="page-eyebrow">Workflow 7</div>
        <h2>ISM-Auswertung</h2>
        <p>Idiographische Systemmodellierung aus dem SNS-Prozessmonitoring – Übergänge, Komplexität, ISM-Faktoren, Bericht als DOCX</p>
      </div>
      <div className="page-body">
        <div className="workflow">
          <Card num="A" title="SNS-Userexport" badge="req">
            <Dropzone label="Userexport hochladen" hint=".xlsx — enthält HSF-Basisbogen und individuellen Bogen mit Tagebuch"
              accept=".xlsx" icon="&#128202;" file={xlsx} onFile={setXlsx} />
            <ExportAnleitung {...ANLEITUNG_USEREXPORT} />
          </Card>

          <Card num="B" title="Fragebogen-XML des individuellen Bogens" badge="req">
            <Dropzone label="Fragebogen-XML hochladen" hint=".xml — Itemtexte und Faktorzuordnung des individuellen Bogens"
              accept=".xml" icon="&#128196;" file={xml} onFile={setXml} />
            <ExportAnleitung {...ANLEITUNG_XML} />
            <div className="field-note" style={{ marginTop: 6 }}>
              Widerspricht ein Verlauf der Polung im XML deutlich, wird das Item automatisch umgepolt und im Bericht vermerkt.
            </div>
          </Card>

          <Card num="C" title="Prompt/Modell anpassen (advanced)" badge="opt" open={false} hasContent={prompt !== P_SNS || !!vorname}>
            <label className="field-label">Vorname der Klient:in (nur für die Pseudonymisierung der Tagebuchtexte)</label>
            <input type="text" value={vorname} onChange={(e) => updateDraft({ vorname: e.target.value })} maxLength={40}
              placeholder="optional" style={{ width: 200, padding: "3px 6px", fontSize: 12, borderRadius: 3,
                border: "1px solid var(--st-gray-border)", background: "var(--st-bg)", color: "var(--st-text)", marginBottom: 10 }} />
            <JobModelPicker workflow="sns_verlauf" value={jobModel} onChange={setJobModel} />
            <PromptEditor value={prompt} onChange={(v) => updateDraft({ prompt: v })} def={P_SNS} />
            <div className="field-note">Inhaltliche Anweisungen für die Interpretation. Abschnittsstruktur, Zahlen- und Zitatregel liegen im Backend und sind nicht editierbar; alle Kennwerte werden deterministisch berechnet.</div>
          </Card>

          <WorkflowActionBar
            busy={wr.busy} onRun={run} onCancel={wr.cancel}
            runLabel="ISM-Auswertung erstellen"
            disabled={!canGenerate}
            title={!xlsx ? "SNS-Userexport (.xlsx) erforderlich" : !xml ? "Fragebogen-XML erforderlich" : !patientName ? "Kürzel erforderlich" : ""}
          >
            <KlientControls geschlecht={geschlecht} onGeschlecht={(v) => updateDraft({ geschlecht: v })}
              kuerzel={kuerzel} onKuerzel={(v) => updateDraft({ kuerzel: v })} />
          </WorkflowActionBar>

          {/* ── Ergebnis ──────────────────────────────────────────────── */}
          <div className="output-card">
            <div className="output-head">
              <span className="output-title">ISM-Auswertung{res ? ` – ${res.kuerzel}` : ""}</span>
              <div className="output-btns">
                {res && <button className="btn-out" onClick={copyText}>Bericht kopieren</button>}
                {res && <button className="btn-out" onClick={downloadDocx} disabled={docxBusy}>{docxBusy ? "…" : "DOCX herunterladen"}</button>}
              </div>
            </div>
            {res && (
              <div className="output-tabs">
                {[["bericht", "Bericht"], ["grafiken", "Grafiken"], ["kennwerte", "Kennwerte"]].map(([id, label]) => (
                  <div key={id} className={"otab" + (tab === id ? " on" : "")} onClick={() => setTab(id)}>{label}</div>
                ))}
              </div>
            )}
            <div className={"output-text" + (!res && !busy && !resError ? " empty" : "")} style={{ whiteSpace: "normal" }}>
              {busy && (currentJobId ? <JobProgressBar jobId={currentJobId} /> : "Wird generiert ...")}
              {!busy && resError && <span style={{ color: "var(--st-error,#b00)", fontWeight: 500 }}>{resError}</span>}
              {!busy && !res && !resError && "Die ISM-Auswertung erscheint hier."}

              {!busy && res && tab === "bericht" && (
                <div>
                  {res.flags && res.flags.length > 0 && (
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 8 }}>
                      {res.flags.map((fl) => (
                        <span key={fl} title={fl} style={{ fontSize: 10.5, padding: "2px 7px", borderRadius: 10,
                          background: fl === "SUIZIDALITAET_IN_QUELLE" || fl === "POLUNG_FRAGLICH" ? "#f8e1e4" : "var(--st-gray-light)",
                          color: "var(--st-text-soft)" }}>{FLAG_LABELS[fl] || fl}</span>
                      ))}
                    </div>
                  )}
                  <textarea
                    value={text}
                    onChange={(e) => setText(e.target.value)}
                    onBlur={checkNow}
                    rows={Math.min(60, Math.max(20, text.split("\n").length + 2))}
                    style={{ width: "100%", fontFamily: "inherit", fontSize: 13, lineHeight: 1.5 }}
                  />
                  <QualityCheckPanel data={qc} readOnly />
                  <div className="field-note" style={{ marginTop: 8 }}>
                    Änderungen am Text fließen in den DOCX-Export. Tabellen und Abbildungen werden aus den berechneten Kennwerten ergänzt.
                    {res.hinweise && res.hinweise.length > 0 && <> · Hinweise: {res.hinweise.join(" | ")}</>}
                  </div>
                </div>
              )}

              {!busy && res && tab === "grafiken" && (
                <div>
                  {grafiken.map((g) => (
                    <div key={g.nr} style={{ marginBottom: 14 }}>
                      <img src={`data:image/png;base64,${g.png_b64}`} alt={g.titel} style={{ width: "100%", maxWidth: 900, display: "block" }} />
                      <div style={{ fontSize: 11, color: "var(--st-text-soft)", marginTop: 3 }}>Abbildung {g.nr}: {g.titel}</div>
                      {g.legende?.length > 0 && (
                        <ol className="p7-legende" style={{ fontSize: 11, color: "var(--st-text-soft)", margin: "4px 0 0", paddingLeft: 0, listStyle: "none", display: "flex", flexWrap: "wrap", gap: "2px 14px" }}>
                          {g.legende.map((l) => <li key={l}>{l}</li>)}
                        </ol>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {!busy && res && tab === "kennwerte" && f && (
                <div>
                  <div style={{ fontSize: 12, marginBottom: 6 }}>
                    Zeitraum {fmtDate(f.zeitraum.start)} – {fmtDate(f.zeitraum.ende)} · {f.zeitraum.messtage_hsf} HSF-Messtage
                    {f.ordnungsuebergang ? <> · Ordnungsübergang <b>{fmtDate(f.ordnungsuebergang)}</b></> : " · kein Ordnungsübergang"}
                  </div>
                  <KennwertTabelle title="Phasen" head={["Phase", "Zeitraum", "Typ", "Name", "Komposit", "DK"]}
                    rows={(res.phasen || f.phasen).map((p) => [p.label, `${fmtDate(p.start)} – ${fmtDate(p.ende)}`, p.typ, p.name || "–", fmtNum(p.komposit_mittel), fmtNum(p.dk_mittel, 3)])} />
                  <KennwertTabelle title="Übergänge" head={["Datum", "Typ", "Sprung", "Vorläufer (DK)", "kritisch"]}
                    rows={f.uebergaenge.map((u) => [fmtDate(u.datum), u.typ, fmtNum(u.shift), u.vorlaeufer ? `${fmtDate(u.vorlaeufer.datum)} (${fmtNum(u.vorlaeufer.dk, 3)})` : "–", u.vorlaeufer ? (u.vorlaeufer.kritisch ? "ja" : "nein") : "–"])} />
                  {f.ism && f.ism.faktoren && f.ism.faktoren.length > 0 && (
                    <KennwertTabelle title={`ISM-Faktoren (Zuordnung: ${f.ism.quelle || "keine"})`} head={["Faktor", "Items", "Sprung", "Krisen-Min.", "Endniveau"]}
                      rows={f.ism.faktoren.map((x) => [x.name, x.besetzt ? x.items.join("; ") : "unbesetzt", fmtNum(x.sprung_uebergang), fmtNum(x.krisen_minimum), fmtNum(x.endniveau)])} />
                  )}
                  <KennwertTabelle title="HSF-Faktoren" head={["Faktor", "Anfang", "Ende", "Δ", "τ"]}
                    rows={f.hsf.faktoren.map((x) => [`${x.name} ${x.kurz}`, fmtNum(x.anfang), fmtNum(x.ende), fmtNum(x.delta), fmtNum(x.tau, 2)])} />
                  <KennwertTabelle title="Einbrüche" head={["Datum", "Tiefe", "Dauer (Tage)"]}
                    rows={f.einbrueche.map((e) => [fmtDate(e.datum), fmtNum(e.tiefe), e.dauer ?? "offen"])} />
                  {f.polung_check && f.polung_check.some((c) => c.korrigiert) && (
                    <div className="upload-warn" style={{ marginTop: 8 }}>
                      Automatisch umgepolt: {f.polung_check.filter((c) => c.korrigiert).map((c) => `${c.item} (r vorher ${fmtNum(c.r_vor_korrektur, 2)})`).join(", ")} – Polung im SNS-Fragebogen anpassen.
                    </div>
                  )}
                  {f.polung_check && f.polung_check.some((c) => c.fraglich) && (
                    <div className="upload-warn" style={{ marginTop: 8 }}>
                      Polung fraglich: {f.polung_check.filter((c) => c.fraglich).map((c) => `${c.item} (r ${fmtNum(c.r, 2)})`).join(", ")} – bitte im Fragebogen prüfen.
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>

          <FeedbackButton jobId={lastJobId} workflow="sns_verlauf" toast={toast} />

          {(res || resError || draftDirty || xlsx || xml) && !busy && (
            <div style={{ marginTop: 12, textAlign: "right" }}>
              <button className="btn-secondary" onClick={() => {
                setXlsx(null); setXml(null);
                clearDraft();
                setRes(null); setText(""); setResError(null); setQc(null);
                wr.reset();
                toast("Formular zurückgesetzt");
              }}>+ Neue Verlaufsauswertung</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export { P7, FLAG_LABELS };
