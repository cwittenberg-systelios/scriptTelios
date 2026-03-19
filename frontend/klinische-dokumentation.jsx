import { useState, useRef, useCallback } from "react";
import { createPortal } from "react-dom";

// sysTelios CI – angepasst an Confluence-Intranet-Screenshot:
// Sidebar: Dunkelgrau/Anthrazit (#2c2c2c) / Highlight: Dunkelrot #8b1a1a / Neutral Grau-Töne / System-Schrift
const S = `
  /* Scoped auf #st-root – überschreibt Confluence-CSS zuverlässig */
  #st-root, #st-root *, #st-root *::before, #st-root *::after {
    box-sizing: border-box !important;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Helvetica Neue', Arial, sans-serif !important;
  }

  :root {
    --st-sidebar:      #2c2c2c;
    --st-sidebar-mid:  #3a3a3a;
    --st-sidebar-light:#4a4a4a;
    --st-red:          #8b1a1a;
    --st-red-mid:      #7a1515;
    --st-red-hover:    #6a1010;
    --st-red-pale:     #f5e8e8;
    --st-cream:        #f8f8f8;
    --st-white:        #ffffff;
    --st-gray-light:   #f4f4f4;
    --st-gray-mid:     #e0e0e0;
    --st-gray-border:  #cccccc;
    --st-text:         #1a1a1a;
    --st-text-mid:     #333333;
    --st-text-soft:    #666666;
    --st-text-pale:    #999999;
    --radius-sm:       3px;
    --radius:          4px;
    --shadow:          0 2px 8px rgba(0,0,0,0.10);
    --shadow-md:       0 4px 20px rgba(0,0,0,0.13);
  }

  #st-root {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
    background: var(--st-cream);
    color: var(--st-text);
    font-size: 15px;
    line-height: 1.6;
    font-weight: 400;
  }

  /* ── SIDEBAR ── */
  .sidebar {
    width: 260px;
    min-width: 260px;
    flex-shrink: 0;
    background: var(--st-sidebar);
    display: flex;
    flex-direction: column;
    z-index: 200;
    min-height: 100%;
    border-radius: 8px 0 0 8px;
    overflow: hidden;
  }

  .sidebar-section-label {
    font-size: 10px; font-weight: 700;
    letter-spacing: 0.16em; text-transform: uppercase;
    color: rgba(255,255,255,0.28);
    padding: 20px 16px 8px !important;
  }

  .nav-item {
    display: flex !important; align-items: stretch !important;
    cursor: pointer;
    border-left: 3px solid transparent;
    transition: background 0.15s;
    position: relative;
  }
  .nav-item:hover { background: rgba(255,255,255,0.06); }
  .nav-item.active {
    background: rgba(255,255,255,0.10);
    border-left-color: var(--st-red);
  }
  .nav-item-inner {
    display: flex !important; align-items: flex-start !important; gap: 12px !important;
    padding: 12px 16px !important;
    flex: 1;
  }
  .nav-step-num {
    width: 24px !important; height: 24px !important; border-radius: 50% !important;
    border: 1px solid rgba(255,255,255,0.22);
    color: rgba(255,255,255,0.45);
    font-size: 11px !important; font-weight: 700 !important;
    display: flex !important; align-items: center !important; justify-content: center !important;
    flex-shrink: 0 !important; margin-top: 1px !important;
    transition: all 0.15s;
  }
  .nav-item.active .nav-step-num {
    background: var(--st-red);
    border-color: var(--st-red);
    color: white;
  }
  .nav-item-title {
    font-size: 13px !important; font-weight: 400;
    color: rgba(255,255,255,0.72);
    line-height: 1.4 !important; margin-bottom: 2px !important;
  }
  .nav-item.active .nav-item-title { color: #fff; font-weight: 600; }
  .nav-item-sub {
    font-size: 11px !important; font-weight: 300;
    color: rgba(255,255,255,0.35);
    line-height: 1.3 !important;
  }

  .sidebar-footer {
    margin-top: auto !important;
    padding: 16px !important;
    border-top: 1px solid rgba(255,255,255,0.08);
    font-size: 10px; font-weight: 300;
    color: rgba(255,255,255,0.25);
    line-height: 1.6;
    letter-spacing: 0.02em;
  }

  /* ── MAIN ── */
  .main { flex: 1; min-height: 100vh; overflow-x: hidden; border-radius: 0 8px 8px 0; }

  .page-header {
    background: var(--st-sidebar);
    padding: 24px 32px 20px !important;
  }
  .page-eyebrow {
    font-size: 10px; font-weight: 700;
    letter-spacing: 0.18em; text-transform: uppercase;
    color: var(--st-red); margin-bottom: 6px !important;
    display: flex !important; align-items: center !important; gap: 8px !important;
  }
  .page-eyebrow::after {
    content: ''; flex: 0 0 20px; height: 1px;
    background: var(--st-red); opacity: 0.6;
  }
  .page-header h2 {
    font-size: 22px !important; font-weight: 600 !important;
    color: #fff !important; line-height: 1.25 !important;
    letter-spacing: -0.01em; margin: 0 !important;
  }
  .page-header p {
    font-size: 13px !important; color: rgba(255,255,255,0.45) !important;
    margin-top: 4px !important; font-weight: 300 !important;
    font-style: italic; margin-bottom: 0 !important;
  }

  .page-body { padding: 24px 32px 40px !important; max-width: 900px; }

  /* ── STEP CARDS ── */
  .workflow { display: flex !important; flex-direction: column !important; gap: 10px !important; }

  .step-card {
    background: var(--st-white);
    border: 1px solid var(--st-gray-mid);
    border-radius: 5px;
    overflow: hidden;
    margin: 0 !important;
  }
  .step-head {
    display: flex !important; align-items: center !important; gap: 12px !important;
    padding: 14px 20px !important;
    cursor: pointer; user-select: none;
    border-bottom: 1px solid transparent;
    transition: background 0.15s;
  }
  .step-head:hover { background: var(--st-gray-light); }
  .step-head.open { border-bottom-color: var(--st-gray-mid); }

  .step-num {
    width: 28px !important; height: 28px !important; border-radius: 50% !important;
    background: var(--st-red);
    color: white !important; font-size: 12px !important; font-weight: 700 !important;
    display: flex !important; align-items: center !important; justify-content: center !important;
    flex-shrink: 0 !important; margin: 0 !important;
  }
  .step-label {
    flex: 1; font-size: 14px !important; font-weight: 600 !important;
    color: var(--st-text); margin: 0 !important;
  }
  .step-pill {
    font-size: 10px; font-weight: 700;
    letter-spacing: 0.08em; text-transform: uppercase;
    padding: 3px 9px !important; border-radius: 20px;
  }
  .pill-opt { background: var(--st-gray-light); color: var(--st-text-soft); }
  .pill-req { background: rgba(139,26,26,0.10); color: var(--st-red-mid); }
  .step-caret {
    font-size: 11px; color: var(--st-text-pale);
    transition: transform 0.18s; display: inline-block;
  }
  .step-caret.open { transform: rotate(180deg); }
  .step-body { padding: 20px 24px 24px !important; display: flex !important; flex-direction: column !important; gap: 16px !important; }

  /* ── UPLOAD ── */
  .upload-grid { display: grid !important; grid-template-columns: 1fr 1fr !important; gap: 16px !important; }
  .upload-col-label {
    font-size: 11px !important; font-weight: 600 !important;
    letter-spacing: 0.06em; text-transform: uppercase;
    color: var(--st-text-soft); margin-bottom: 8px !important;
  }

  .dropzone {
    border: 2px dashed var(--st-gray-mid) !important;
    border-radius: 5px;
    padding: 28px 20px !important; text-align: center;
    cursor: pointer; position: relative;
    transition: border-color 0.15s, background 0.15s;
    background: var(--st-cream);
    min-height: 110px !important;
    display: flex !important; flex-direction: column !important;
    align-items: center !important; justify-content: center !important;
  }
  .dropzone:hover { border-color: var(--st-red) !important; background: var(--st-red-pale); }
  .dropzone.drag { border-color: var(--st-red-mid) !important; background: var(--st-red-pale); }
  .dropzone.filled { border-style: solid !important; border-color: var(--st-red) !important; background: var(--st-red-pale); }
  .dropzone input[type=file] { position: absolute; inset: 0; opacity: 0; cursor: pointer; width: 100%; height: 100%; }
  .dz-icon { font-size: 24px !important; margin-bottom: 8px !important; opacity: 0.45; }
  .dz-label { font-size: 13px !important; font-weight: 600 !important; color: var(--st-text-mid); margin: 0 !important; }
  .dz-hint { font-size: 12px !important; color: var(--st-text-pale); margin-top: 4px !important; font-weight: 300; }
  .dz-file {
    display: flex !important; align-items: center !important; gap: 8px !important;
    font-size: 13px !important; font-weight: 600; color: var(--st-red-mid);
  }
  .dz-remove {
    background: none !important; border: none !important; cursor: pointer;
    color: var(--st-text-pale); font-size: 18px !important;
    padding: 0 !important; line-height: 1;
  }
  .dz-remove:hover { color: var(--st-red); }

  /* ── OR DIVIDER ── */
  .or-row {
    display: flex !important; align-items: center !important; gap: 12px !important;
    font-size: 11px !important; font-weight: 600; letter-spacing: 0.08em;
    text-transform: uppercase; color: var(--st-text-pale);
    margin: 0 !important;
  }
  .or-row::before, .or-row::after {
    content: ''; flex: 1; height: 1px; background: var(--st-gray-mid);
  }

  /* ── FIELDS ── */
  .field-label {
    font-size: 11px !important; font-weight: 700 !important;
    color: var(--st-text-mid); margin-bottom: 6px !important; margin-top: 0 !important;
    display: block; letter-spacing: 0.04em;
    text-transform: uppercase;
  }
  .field-note {
    font-size: 12px !important; color: var(--st-text-pale);
    font-weight: 300; font-style: italic; margin-top: 6px !important;
  }
  textarea {
    width: 100% !important; border: 1px solid var(--st-gray-mid) !important;
    border-radius: 4px; padding: 12px 14px !important;
    font-size: 14px !important; line-height: 1.6 !important;
    color: var(--st-text); background: var(--st-cream);
    resize: vertical; margin: 0 !important;
    transition: border-color 0.15s, box-shadow 0.15s;
  }
  textarea:focus {
    outline: none !important; border-color: var(--st-red) !important;
    box-shadow: 0 0 0 3px rgba(139,26,26,0.08) !important;
    background: white;
  }

  /* ── PROMPT EDITOR ── */
  .prompt-box { border: 1px solid var(--st-gray-mid); border-radius: 4px; overflow: hidden; }
  .prompt-bar {
    display: flex !important; align-items: center !important; justify-content: space-between !important;
    padding: 10px 14px !important; background: var(--st-gray-light);
    border-bottom: 1px solid var(--st-gray-mid);
  }
  .prompt-bar-label {
    font-size: 11px !important; font-weight: 600; letter-spacing: 0.06em;
    text-transform: uppercase; color: var(--st-text-soft); margin: 0 !important;
  }
  .btn-xs {
    font-size: 12px !important; padding: 4px 10px !important; border-radius: 3px;
    border: 1px solid var(--st-gray-border); background: white;
    color: var(--st-text-mid); cursor: pointer; font-weight: 500;
    transition: border-color 0.12s;
  }
  .btn-xs:hover { border-color: var(--st-red); }
  .prompt-box textarea { border: none !important; border-radius: 0; background: white; font-size: 13px !important; }
  .prompt-box textarea:focus { box-shadow: none !important; }

  /* ── DIAGNOSE TAGS ── */
  .tag-wrap { display: flex !important; flex-wrap: wrap !important; gap: 8px !important; align-items: center !important; }
  .tag {
    display: inline-flex !important; align-items: center !important; gap: 5px !important;
    background: var(--st-red-pale); border: 1px solid rgba(139,26,26,0.20);
    border-radius: 3px; padding: 4px 10px !important;
    font-size: 13px !important; font-weight: 600; color: var(--st-red-mid);
  }
  .tag-x {
    background: none !important; border: none !important; cursor: pointer;
    color: rgba(139,26,26,0.45); font-size: 15px !important;
    padding: 0 !important; line-height: 1; font-weight: 400;
  }
  .tag-x:hover { color: var(--st-red); }
  .tag-input {
    border: 1px dashed var(--st-gray-border) !important; border-radius: 3px;
    padding: 4px 10px !important; font-size: 13px !important;
    color: var(--st-text); background: transparent;
    outline: none; width: 190px;
  }
  .tag-input:focus { border-color: var(--st-red) !important; background: white; }

  /* ── INFO NOTE ── */
  .info-note {
    border-left: 3px solid var(--st-red) !important;
    background: var(--st-red-pale);
    padding: 10px 14px !important; border-radius: 0 4px 4px 0;
    font-size: 13px !important; color: var(--st-text-mid); font-weight: 400;
    line-height: 1.55; margin: 0 !important;
  }

  /* ── ACTION BAR ── */
  .action-bar {
    display: flex !important; justify-content: flex-end !important;
    padding-top: 16px !important; border-top: 1px solid var(--st-gray-mid);
    padding-bottom: 0 !important; padding-left: 0 !important; padding-right: 0 !important;
  }

  /* ── BUTTONS ── */
  .btn-primary {
    background: var(--st-red); color: white !important;
    border: none !important; border-radius: 3px;
    padding: 10px 24px !important; font-size: 14px !important; font-weight: 600 !important;
    cursor: pointer; letter-spacing: 0.01em;
    transition: background 0.15s, box-shadow 0.15s;
    display: inline-flex !important; align-items: center !important; gap: 8px !important;
    margin: 0 !important;
  }
  .btn-primary:hover:not(:disabled) {
    background: var(--st-red-hover);
    box-shadow: 0 2px 10px rgba(139,26,26,0.28);
  }
  .btn-primary:disabled { opacity: 0.40; cursor: not-allowed; }

  /* ── OUTPUT ── */
  .output-card {
    background: white; border: 1px solid var(--st-gray-mid);
    border-radius: 5px; overflow: hidden; margin: 0 !important;
  }
  .output-head {
    display: flex !important; align-items: center !important; justify-content: space-between !important;
    padding: 14px 20px !important; background: var(--st-sidebar);
  }
  .output-title {
    font-size: 15px !important; font-weight: 600 !important; color: white !important; margin: 0 !important;
  }
  .output-btns { display: flex !important; gap: 8px !important; }
  .btn-out {
    background: rgba(255,255,255,0.12);
    border: 1px solid rgba(255,255,255,0.22);
    color: white !important; border-radius: 3px;
    padding: 5px 14px !important; font-size: 12px !important; font-weight: 600;
    cursor: pointer; letter-spacing: 0.04em; text-transform: uppercase;
    transition: background 0.12s;
  }
  .btn-out:hover { background: rgba(255,255,255,0.22); }
  .output-tabs { display: flex !important; background: var(--st-gray-light); border-bottom: 1px solid var(--st-gray-mid); }
  .otab {
    padding: 10px 18px !important; font-size: 13px !important; font-weight: 600;
    cursor: pointer; border-bottom: 2px solid transparent;
    color: var(--st-text-soft); margin: 0 !important;
    transition: all 0.12s;
  }
  .otab.on { color: var(--st-red); border-bottom-color: var(--st-red); }
  .output-text {
    padding: 24px !important; font-size: 14px !important; line-height: 1.8 !important;
    color: var(--st-text-mid); min-height: 140px;
    white-space: pre-wrap; font-weight: 400; margin: 0 !important;
  }
  .output-text.empty { color: var(--st-text-pale); font-style: italic; font-size: 13px !important; }

  /* ── SPINNER ── */
  @keyframes spin { to { transform: rotate(360deg); } }
  .spin {
    width: 14px !important; height: 14px !important;
    border: 2px solid rgba(255,255,255,0.3);
    border-top-color: white; border-radius: 50%;
    animation: spin 0.7s linear infinite;
    display: inline-block;
  }

  /* ── TOAST ── */
  .toast {
    position: fixed; bottom: 24px; right: 24px;
    background: var(--st-sidebar); color: white;
    padding: 12px 20px !important; border-radius: 4px;
    font-size: 13px !important; font-weight: 600;
    box-shadow: var(--shadow-md); z-index: 999;
    display: flex !important; align-items: center !important; gap: 10px !important;
  }
  .toast-dot {
    width: 7px !important; height: 7px !important; border-radius: 50%;
    background: var(--st-red); flex-shrink: 0;
  }

  /* scrollbar */
  #st-root ::-webkit-scrollbar { width: 5px; }
  #st-root ::-webkit-scrollbar-thumb { background: var(--st-gray-mid); border-radius: 3px; }
`;

// ── Prompts ─────────────────────────────────────────────────────
const P_DOKU = `Du bist systemischer Psychotherapeut in der sysTelios Klinik für Psychosomatik und Psychotherapie. Erstelle eine Gesprächsdokumentation in Fließtext mit folgenden Abschnitten:

Auftragsklärung
Thema und Ziele des Gesprächs – worum ging es, was sollte erreicht werden?

Relevante Gesprächsinhalte
Wichtige Kontextinformationen über Symptome, dahinterliegende Bedürfnisse, beteiligte Personen und deren Verhaltensmuster.

Hypothesen und Entwicklungsperspektiven
Hypothesen über Sinnzusammenhänge und Ursachen, Lösungsideen und -optionen aus systemischer Sicht.

Einladungen
Todos, Aufgaben, Übungen oder Impulse, die der Klientin/dem Klienten mitgegeben wurden.

Stil: Fließtext, professionell, systemisch-wertschätzend, ressourcenorientiert. Keine Aufzählungen außer bei den Einladungen.`;

const P_ANAMNESE = `Du bist systemischer Psychotherapeut in der sysTelios Klinik für Psychosomatik und Psychotherapie. Erstelle Anamnese und psychopathologischen Befund aus systemischer Perspektive.

ANAMNESE (Fließtext):
Beschreibe den Vorstellungsanlass und die Hauptbeschwerden im Kontext des sozialen Systems der Klientin/des Klienten. Gehe ein auf: Beginn und Verlauf der Beschwerden, auslösende und aufrechterhaltende Faktoren im Familien- und Beziehungskontext, psychiatrische und somatische Vorgeschichte, Medikation, Familienanamnese mit Blick auf Muster und Überzeugungen, Sozialanamnese (Herkunft, Bildung, Beruf, Beziehungen, Kinder), Schlaf, Ernährung, Bewegung, Suchtmittel.

PSYCHOPATHOLOGISCHER BEFUND (AMDP):
Bewusstsein | Orientierung | Aufmerksamkeit | Gedächtnis | Formales Denken | Inhaltliches Denken | Wahrnehmung | Ich-Erleben | Affektivität | Antrieb | Psychomotorik | Suizidalität/Selbstverletzung

SYSTEMISCHE EINSCHÄTZUNG:
Hypothesen zu Sinnzusammenhängen, Funktionen der Symptome im System, relevante Beziehungsmuster und Ressourcen.

Diagnosen: {diagnosen}`;

const P_VERL = `Du bist systemischer Psychotherapeut in der sysTelios Klinik für Psychosomatik und Psychotherapie. Verfasse die Begründung für eine Verlängerung des stationären Aufenthalts.

Beschreibe im Fließtext:
– den bisherigen Behandlungsverlauf aus systemischer Sicht: welche Themen wurden bearbeitet, welche Veränderungen sind erkennbar, welche Muster haben sich gezeigt?
– die medizinische und therapeutische Notwendigkeit der Verlängerung: was ist noch offen, welche Ziele wurden noch nicht erreicht?
– konkrete ausstehende Therapieziele, insbesondere bezogen auf das soziale System, Beziehungsgestaltung und Alltagsintegration
– die Begründung des weiteren stationären Bedarfs gegenüber ambulanter Versorgung
– Prognose und voraussichtlicher weiterer Verlauf

Stil: Fachlich, klar begründet, systemisch-ressourcenorientiert.`;

const P_ENTL = `Du bist systemischer Psychotherapeut in der sysTelios Klinik für Psychosomatik und Psychotherapie. Erstelle einen vollständigen Entlassbericht gemäß der vorliegenden Vorlage.

Struktur:
1. Aufnahme- und Entlassdaten, Verweildauer
2. Aufnahmegrund und Hauptdiagnosen (ICD-10/11)
3. Psychischer und somatischer Aufnahmebefund
4. Behandlungsverlauf – beschreibe die wesentlichen Themen, systemischen Hypothesen, therapeutischen Interventionen, erlebten Krisen und Entwicklungsschritte im Fließtext
5. Psychischer Entlassbefund
6. Systemische Epikrise – Einschätzung der Symptomfunktion im Familien- und Beziehungssystem, relevante Muster, Ressourcen und offene Fragen
7. Empfehlungen und weiteres Procedere, insbesondere zur ambulanten Weiterbehandlung und Systemeinbindung
8. Medikation bei Entlassung

Stil: Fließtext, fachlich, systemisch-wertschätzend, ressourcenorientiert.`;

// ── Helpers ──────────────────────────────────────────────────────
function Dropzone({ label, hint, accept, file, onFile, icon }) {
  const [drag, setDrag] = useState(false);
  const ref = useRef(null);

  function onDrop(e) {
    e.preventDefault(); setDrag(false);
    const f = e.dataTransfer.files[0];
    if (f) onFile(f);
  }

  let cls = "dropzone";
  if (drag) cls += " drag";
  if (file) cls += " filled";

  return (
    <div className={cls}
      onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
      onDragLeave={() => setDrag(false)}
      onDrop={onDrop}
      onClick={() => { if (!file && ref.current) ref.current.click(); }}
    >
      {file ? (
        <div className="dz-file">
          <span>{icon}</span>
          <span style={{ maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{file.name}</span>
          <button className="dz-remove" onClick={(e) => { e.stopPropagation(); onFile(null); }}>&#215;</button>
        </div>
      ) : (
        <>
          <div className="dz-icon">{icon}</div>
          <div className="dz-label">{label}</div>
          {hint && <div className="dz-hint">{hint}</div>}
          <input ref={ref} type="file" accept={accept}
            onChange={(e) => { if (e.target.files && e.target.files[0]) onFile(e.target.files[0]); }} />
        </>
      )}
    </div>
  );
}

function Card({ num, title, badge, open: defaultOpen = true, children }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="step-card">
      <div className={"step-head" + (open ? " open" : "")} onClick={() => setOpen(!open)}>
        <div className="step-num">{num}</div>
        <div className="step-label">{title}</div>
        {badge && (
          <span className={"step-pill " + (badge === "opt" ? "pill-opt" : "pill-req")}>
            {badge === "opt" ? "Optional" : "Erforderlich"}
          </span>
        )}
        <span className={"step-caret" + (open ? " open" : "")}>&#9660;</span>
      </div>
      {open && <div className="step-body">{children}</div>}
    </div>
  );
}

function PromptEditor({ value, onChange, def }) {
  return (
    <div className="prompt-box">
      <div className="prompt-bar">
        <span className="prompt-bar-label">Prompt-Vorlage</span>
        <button className="btn-xs" onClick={() => onChange(def)}>Zuruecksetzen</button>
      </div>
      <textarea rows={8} value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

function Output({ text, loading, tabs, activeTab, onTab, onCopy, onDownload }) {
  const empty = !text && !loading;
  return (
    <div className="output-card">
      <div className="output-head">
        <span className="output-title">Ergebnis</span>
        <div className="output-btns">
          {text && <button className="btn-out" onClick={onCopy}>Kopieren</button>}
          {text && onDownload && <button className="btn-out" onClick={onDownload}>Download</button>}
        </div>
      </div>
      {tabs && (
        <div className="output-tabs">
          {tabs.map((t) => (
            <div key={t} className={"otab" + (activeTab === t ? " on" : "")} onClick={() => onTab(t)}>{t}</div>
          ))}
        </div>
      )}
      <div className={"output-text" + (empty ? " empty" : "")}>
        {loading ? "Wird generiert ..." : (text || "Der generierte Text erscheint hier.")}
      </div>
    </div>
  );
}

function Tags({ list, onChange }) {
  const [val, setVal] = useState("");
  function add() {
    const v = val.trim();
    if (v && !list.includes(v)) { onChange([...list, v]); setVal(""); }
  }
  return (
    <div className="tag-wrap">
      {list.map((d) => (
        <span key={d} className="tag">
          {d}
          <button className="tag-x" onClick={() => onChange(list.filter((x) => x !== d))}>&#215;</button>
        </span>
      ))}
      <input className="tag-input" placeholder="ICD-Code + Enter ..."
        value={val} onChange={(e) => setVal(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(); } }}
        onBlur={add}
      />
    </div>
  );
}

// Ruft das sysTelios Backend asynchron auf (Job-Queue mit Polling).
// Gibt Promise<string> zurueck – wartet bis Job fertig ist.
async function generate(workflow, prompt, userContent, files = {}) {
  const therapeutId = getConfluenceUser();
  const fd = new FormData();
  fd.append("workflow",   workflow);
  fd.append("prompt",     prompt);
  fd.append("transcript", userContent);
  if (therapeutId)     fd.append("therapeut_id",  therapeutId);
  if (files.audio)     fd.append("audio",          files.audio);
  if (files.selbst)    fd.append("selbstauskunft", files.selbst);
  if (files.vorbef)    fd.append("vorbefunde",     files.vorbef);
  if (files.style)     fd.append("style_file",     files.style);
  if (files.diagnosen) fd.append("diagnosen",      files.diagnosen);

  // Job starten
  const r = await fetch(`${API_BASE}/jobs/generate`, { method: "POST", body: fd });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || r.statusText);

  const jobId = d.job_id;

  // Polling bis fertig (max. 10 Minuten)
  const maxWait = 600;
  const interval = 2;
  for (let i = 0; i < maxWait / interval; i++) {
    await new Promise(res => setTimeout(res, interval * 1000));
    const poll = await fetch(`${API_BASE}/jobs/${jobId}`);
    const job  = await poll.json();
    if (job.status === "done")  return job.result_text || "";
    if (job.status === "error") throw new Error(job.error_msg || "Job fehlgeschlagen");
  }
  throw new Error("Timeout: Job dauert zu lange");
}

// ── Pages ────────────────────────────────────────────────────────
function P1({ toast }) {
  const [audio, setAudio]   = useState(null);
  const [txtFile, setTxtFile] = useState(null);
  const [text, setText]     = useState("");
  const [bullets, setBullets] = useState("");
  const [style, setStyle]   = useState(null);
  const [prompt, setPrompt] = useState(P_DOKU);
  const [out, setOut]       = useState("");
  const [busy, setBusy]     = useState(false);

  async function run() {
    setBusy(true);
    const u = (text    ? "TRANSKRIPT:\n" + text + "\n\n" : "")
            + (bullets ? "STICHPUNKTE:\n" + bullets + "\n" : "");
    try {
      setOut(await generate("dokumentation", prompt, u || "Verlaufsnotiz erstellen.", {
        audio: audio,
        style: style,
      }));
    }
    catch (e) { setOut("Fehler: " + e.message); }
    setBusy(false);
  }

  return (
    <div>
      <div className="page-header">
        <div className="page-eyebrow">Workflow 1</div>
        <h2>Gespr&auml;chsdokumentation</h2>
        <p>Strukturierte Verlaufsnotizen aus Aufnahmen oder Transkripten</p>
      </div>
      <div className="page-body">
        <div className="workflow">
          <Card num="A" title="Gesprächsmaterial" badge="opt" open={true}>
            <div className="upload-grid">
              <div>
                <div className="upload-col-label">Audio-Aufnahme</div>
                <Dropzone label="Aufnahme hochladen" hint=".mp3  .m4a  .wav" accept="audio/*" icon="&#127897;" file={audio} onFile={setAudio} />
              </div>
              <div>
                <div className="upload-col-label">Transkript-Datei</div>
                <Dropzone label="Datei hochladen" hint=".txt  .docx" accept=".txt,.docx" icon="&#128196;" file={txtFile} onFile={setTxtFile} />
              </div>
            </div>
            <div className="or-row">oder direkt eingeben</div>
            <div>
              <label className="field-label">Transkript / Gespr&auml;chsinhalt</label>
              <textarea rows={5} placeholder="Gesprächsinhalt direkt hier einfügen ..." value={text} onChange={(e) => setText(e.target.value)} />
            </div>
          </Card>

          <Card num="B" title="Stichpunkte" badge="opt" open={false}>
            <label className="field-label">Relevante Themen und Beobachtungen</label>
            <textarea rows={4} placeholder={"- Bericht ueber das Wochenende\n- Schlafprobleme anhaltend\n- Fortschritt bei Expositionsuebung ..."} value={bullets} onChange={(e) => setBullets(e.target.value)} />
            <div className="field-note">Ergaenzt oder ersetzt das Transkript bei Bedarf</div>
          </Card>

          <Card num="C" title="Stilvorlage" badge="opt" open={false}>
            <Dropzone label="Beispieltext hochladen" hint="PDF, DOCX oder TXT" accept=".pdf,.docx,.txt" icon="&#128221;" file={style} onFile={setStyle} />
            <div className="info-note">Der Schreibstil des hochgeladenen Textes wird bei der Generierung beruecksichtigt.</div>
          </Card>

          <Card num="D" title="Prompt anpassen" open={false}>
            <PromptEditor value={prompt} onChange={setPrompt} def={P_DOKU} />
          </Card>

          <div className="action-bar">
            <button className="btn-primary" onClick={run} disabled={busy || (!audio && !txtFile && !text && !bullets)}>
              {busy ? <span className="spin" /> : null}
              {busy ? "Generiere ..." : "Verlaufsnotiz generieren"}
            </button>
          </div>

          <Output text={out} loading={busy}
            onCopy={() => { navigator.clipboard.writeText(out); toast("In Zwischenablage kopiert"); }} />
        </div>
      </div>
    </div>
  );
}

function P2({ toast }) {
  const [selbst, setSelbst]   = useState(null);
  const [befunde, setBefunde] = useState(null);
  const [audio, setAudio]     = useState(null);
  const [txtFile, setTxtFile] = useState(null);
  const [text, setText]       = useState("");
  const [dx, setDx]           = useState([]);
  const [style, setStyle]     = useState(null);
  const [prompt, setPrompt]   = useState(P_ANAMNESE);
  const [out, setOut]         = useState("");
  const [tab, setTab]         = useState("Anamnese");
  const [busy, setBusy]       = useState(false);

  async function run() {
    setBusy(true);
    const dxStr = dx.length ? dx.join(", ") : "noch nicht festgelegt";
    const sys = prompt.replace("{diagnosen}", dxStr);
    const u = (text ? "AUFNAHMEGESPRAECH:\n" + text + "\n\n" : "")
            + "DIAGNOSEN: " + dxStr + "\n\nAnamnese und psychopathologischen Befund erstellen.";
    try { setOut(await generate("anamnese", sys, u)); }
    catch (e) { setOut("Fehler: " + e.message); }
    setBusy(false);
  }

  return (
    <div>
      <div className="page-header">
        <div className="page-eyebrow">Workflow 2</div>
        <h2>Anamnese &amp; Psychopathologischer Befund</h2>
        <p>Aus Selbstauskunft, Vorbefunden und Aufnahmegespraech</p>
      </div>
      <div className="page-body">
        <div className="workflow">
          <Card num="A" title="Selbstauskunft und weitere Befunde" badge="req">
            <div className="upload-grid">
              <div>
                <div className="upload-col-label">Selbstauskunft des Klienten</div>
                <Dropzone label="PDF hochladen" hint="Ausgefuellter Patientenfragebogen" accept=".pdf" icon="&#128203;" file={selbst} onFile={setSelbst} />
              </div>
              <div>
                <div className="upload-col-label">Vorbefunde / weitere Befunde</div>
                <Dropzone label="PDF oder Bild hochladen" hint=".pdf  .jpg  .png" accept=".pdf,image/*" icon="&#127973;" file={befunde} onFile={setBefunde} />
              </div>
            </div>
          </Card>

          <Card num="B" title="Aufnahmegespraech" badge="opt" open={false}>
            <div className="upload-grid">
              <div>
                <div className="upload-col-label">Audio-Aufnahme</div>
                <Dropzone label="Aufnahme hochladen" hint=".mp3  .m4a  .wav" accept="audio/*" icon="&#127897;" file={audio} onFile={setAudio} />
              </div>
              <div>
                <div className="upload-col-label">Transkript-Datei</div>
                <Dropzone label="Datei hochladen" hint=".txt  .docx" accept=".txt,.docx" icon="&#128196;" file={txtFile} onFile={setTxtFile} />
              </div>
            </div>
            <div className="or-row">oder direkt eingeben</div>
            <div>
              <label className="field-label">Transkript Aufnahmegespraech</label>
              <textarea rows={4} placeholder="Gespraechsinhalt des Aufnahmegespraechs ..." value={text} onChange={(e) => setText(e.target.value)} />
            </div>
          </Card>

          <Card num="C" title="Diagnosen" badge="req">
            <label className="field-label">ICD-10 oder ICD-11 Diagnosen</label>
            <Tags list={dx} onChange={setDx} />
            <div className="field-note">Enter oder Komma zum Hinzufuegen — z.B. F32.1, F41.1, Z73.0</div>
          </Card>

          <Card num="D" title="Stilvorlage" badge="opt" open={false}>
            <Dropzone label="Beispieltext hochladen" hint="PDF, DOCX oder TXT" accept=".pdf,.docx,.txt" icon="&#128221;" file={style} onFile={setStyle} />
          </Card>

          <Card num="E" title="Prompt anpassen" open={false}>
            <PromptEditor value={prompt} onChange={setPrompt} def={P_ANAMNESE} />
          </Card>

          <div className="action-bar">
            <button className="btn-primary" onClick={run} disabled={busy || !selbst}>
              {busy ? <span className="spin" /> : null}
              {busy ? "Generiere ..." : "Anamnese und Befund generieren"}
            </button>
          </div>

          <Output text={out} loading={busy}
            tabs={["Anamnese", "Psych. Befund"]} activeTab={tab} onTab={setTab}
            onCopy={() => { navigator.clipboard.writeText(out); toast("Kopiert"); }} />
        </div>
      </div>
    </div>
  );
}

function P3({ toast }) {
  const [antrag, setAntrag]   = useState(null);
  const [verlauf, setVerlauf] = useState(null);
  const [style, setStyle]     = useState(null);
  const [prompt, setPrompt]   = useState(P_VERL);
  const [out, setOut]         = useState("");
  const [busy, setBusy]       = useState(false);

  async function run() {
    setBusy(true);
    await new Promise((r) => setTimeout(r, 1400));
    setOut("VERLAENGERUNGSANTRAG\n\nVorlage: " + (antrag ? antrag.name : "-") + "\nVerlaufsdokumentation: " + (verlauf ? verlauf.name : "-") + "\n\nIn der Produktionsversion wird das DOCX serverseitig befuellt.\n\nBegruendung der Verlaengerung:\nDer Klient befindet sich seit [Aufnahmedatum] in stationaerer Behandlung. Der bisherige Verlauf zeigt erste therapeutische Fortschritte. Die angestrebten Therapieziele sind noch nicht vollstaendig erreicht. Eine Verlaengerung wird aus medizinischer Sicht als notwendig erachtet.\n\n[Vollstaendiger Antrag wird aus der Verlaufsdokumentation generiert]");
    setBusy(false);
  }

  return (
    <div>
      <div className="page-header">
        <div className="page-eyebrow">Workflow 3</div>
        <h2>Verlaengerungsantrag</h2>
        <p>Befuellt vorhandene Antragsvorlagen aus der Verlaufsdokumentation</p>
      </div>
      <div className="page-body">
        <div className="workflow">
          <Card num="A" title="Verlaengerungsantrag-Vorlage" badge="req">
            <Dropzone label="Antrag hochladen" hint=".docx — vorhandene Vorlage mit Feldern" accept=".docx" icon="&#128196;" file={antrag} onFile={setAntrag} />
            <div className="info-note">Die Vorlage wird analysiert und alle Felder werden aus der Verlaufsdokumentation befuellt.</div>
          </Card>

          <Card num="B" title="Verlaufsdokumentation" badge="req">
            <Dropzone label="Verlaufsdokumentation hochladen" hint=".pdf — alle Verlaufsnotizen des Aufenthalts" accept=".pdf" icon="&#128202;" file={verlauf} onFile={setVerlauf} />
          </Card>

          <Card num="C" title="Stilvorlage" badge="opt" open={false}>
            <Dropzone label="Beispieltext hochladen" hint="PDF, DOCX oder TXT" accept=".pdf,.docx,.txt" icon="&#128221;" file={style} onFile={setStyle} />
          </Card>

          <Card num="D" title="Prompt anpassen" open={false}>
            <PromptEditor value={prompt} onChange={setPrompt} def={P_VERL} />
          </Card>

          <div className="action-bar">
            <button className="btn-primary" onClick={run} disabled={busy || !antrag || !verlauf}>
              {busy ? <span className="spin" /> : null}
              {busy ? "Generiere ..." : "Antrag ausfuellen"}
            </button>
          </div>

          <Output text={out} loading={busy}
            onCopy={() => { navigator.clipboard.writeText(out); toast("Kopiert"); }}
            onDownload={() => toast("Download wird vorbereitet (DOCX)")} />
        </div>
      </div>
    </div>
  );
}

function P4({ toast }) {
  const [bericht, setBericht] = useState(null);
  const [verlauf, setVerlauf] = useState(null);
  const [style, setStyle]     = useState(null);
  const [prompt, setPrompt]   = useState(P_ENTL);
  const [out, setOut]         = useState("");
  const [busy, setBusy]       = useState(false);

  async function run() {
    setBusy(true);
    await new Promise((r) => setTimeout(r, 1400));
    setOut("ENTLASSBERICHT\n\nVorlage: " + (bericht ? bericht.name : "-") + "\nVerlaufsdokumentation: " + (verlauf ? verlauf.name : "-") + "\n\nIn der Produktionsversion wird die DOCX-Vorlage vollstaendig befuellt.\n\nBehandlungsverlauf:\nDer Klient wurde mit [Diagnosen] aufgenommen. Im Verlauf konnten therapeutische Fortschritte erzielt werden ...\n\nEmpfehlungen:\n- Ambulante Weiterbehandlung\n- Regelmaessige Kontrolltermine\n- Fortfuehrung der vereinbarten Eigenubungen\n\n[Vollstaendiger Bericht wird synthetisiert]");
    setBusy(false);
  }

  return (
    <div>
      <div className="page-header">
        <div className="page-eyebrow">Workflow 4</div>
        <h2>Entlassbericht</h2>
        <p>Synthetisiert alle Verlaufsnotizen zu einem vollstaendigen Entlassbericht</p>
      </div>
      <div className="page-body">
        <div className="workflow">
          <Card num="A" title="Entlassbericht-Vorlage" badge="req">
            <Dropzone label="Vorlage hochladen" hint=".docx — Vorlage mit vorhandener Struktur" accept=".docx" icon="&#128196;" file={bericht} onFile={setBericht} />
            <div className="info-note">Die Struktur der Vorlage wird uebernommen und mit Inhalten aus der Verlaufsdokumentation befuellt.</div>
          </Card>

          <Card num="B" title="Verlaufsdokumentation" badge="req">
            <Dropzone label="Verlaufsdokumentation hochladen" hint=".pdf — gesamte Dokumentation des Aufenthalts" accept=".pdf" icon="&#128202;" file={verlauf} onFile={setVerlauf} />
          </Card>

          <Card num="C" title="Stilvorlage" badge="opt" open={false}>
            <Dropzone label="Beispieltext hochladen" hint="PDF, DOCX oder TXT" accept=".pdf,.docx,.txt" icon="&#128221;" file={style} onFile={setStyle} />
          </Card>

          <Card num="D" title="Prompt anpassen" open={false}>
            <PromptEditor value={prompt} onChange={setPrompt} def={P_ENTL} />
          </Card>

          <div className="action-bar">
            <button className="btn-primary" onClick={run} disabled={busy || !bericht || !verlauf}>
              {busy ? <span className="spin" /> : null}
              {busy ? "Generiere ..." : "Entlassbericht erstellen"}
            </button>
          </div>

          <Output text={out} loading={busy}
            onCopy={() => { navigator.clipboard.writeText(out); toast("Kopiert"); }}
            onDownload={() => toast("Download wird vorbereitet (DOCX)")} />
        </div>
      </div>
    </div>
  );
}

// ── Stilprofil-Verwaltung ─────────────────────────────────────────
const DOKUMENTTYPEN = [
  { value: "dokumentation",  label: "Gesprächsdokumentation" },
  { value: "anamnese",       label: "Anamnese" },
  { value: "verlaengerung",  label: "Verlängerungsantrag" },
  { value: "entlassbericht", label: "Entlassbericht" },
];

// ── Confluence-Konfiguration ─────────────────────────────────────
// API_BASE: aus window.SYSTELIOS_API_BASE (gesetzt im Confluence User Macro)
//           Fallback auf localhost für lokale Entwicklung
const API_BASE = (typeof window !== "undefined" && window.SYSTELIOS_API_BASE)
  ? window.SYSTELIOS_API_BASE.replace(/\/$/, "").replace(/\/api$/, "") + "/api"
  : "http://localhost:8000/api";

// Therapeuten-Name aus Confluence AJS (gesetzt im Macro vor dem Bundle)
function getConfluenceUser() {
  if (typeof window !== "undefined" && window.SYSTELIOS_USER) {
    return window.SYSTELIOS_USER;
  }
  return "";
}

function P5({ toast }) {
  const [therapeutId] = useState(getConfluenceUser);  // read-only aus Confluence
  const [dokumenttyp, setDokumenttyp] = useState("dokumentation");
  const [istStatisch, setIstStatisch] = useState(false);
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [liste, setListe] = useState(null);
  const [ladebusy, setLadebusy] = useState(false);

  // Bibliothek automatisch laden beim ersten Render
  const didMount = useRef(false);
  if (!didMount.current) {
    didMount.current = true;
    if (therapeutId) Promise.resolve().then(() => ladeListe());
  }

  async function hochladen() {
    if (!therapeutId.trim() || !file) return;
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("therapeut_id",  therapeutId.trim());
      fd.append("dokumenttyp",   dokumenttyp);
      fd.append("ist_statisch",  istStatisch ? "true" : "false");
      fd.append("beispiel_file", file);

      const r = await fetch(`${API_BASE}/style/upload`, { method: "POST", body: fd });
      if (!r.ok) {
        const err = await r.json();
        throw new Error(err.detail || r.statusText);
      }
      const data = await r.json();
      toast(`✓ Gespeichert: ${data.dokumenttyp_label} · ${data.word_count} Wörter${data.ist_statisch ? " · Anker" : ""}`);
      setFile(null);
      await ladeListe();
    } catch (e) {
      toast("Fehler: " + e.message);
    }
    setBusy(false);
  }

  async function ladeListe() {
    if (!therapeutId.trim()) return;
    setLadebusy(true);
    try {
      const r = await fetch(`${API_BASE}/style/${encodeURIComponent(therapeutId.trim())}`);
      if (!r.ok) throw new Error(r.statusText);
      setListe(await r.json());
    } catch (e) {
      toast("Fehler beim Laden: " + e.message);
    }
    setLadebusy(false);
  }

  async function loeschen(id) {
    try {
      const r = await fetch(`${API_BASE}/style/embedding/${id}`, { method: "DELETE" });
      if (!r.ok) throw new Error((await r.json()).detail);
      toast("Beispiel gelöscht");
      await ladeListe();
    } catch (e) {
      toast("Fehler: " + e.message);
    }
  }

  // Gruppiere Liste nach Dokumenttyp
  const grouped = liste ? DOKUMENTTYPEN.map(dt => ({
    ...dt,
    items: (liste.embeddings || []).filter(e => e.dokumenttyp === dt.value),
  })).filter(g => g.items.length > 0) : [];

  return (
    <div>
      <div className="page-header">
        <div className="page-eyebrow">Verwaltung</div>
        <h2>Stilprofil-Bibliothek</h2>
        <p>
          Beispieltexte hochladen · werden automatisch vektorisiert und beim Generieren verwendet
          {therapeutId ? (
            <span style={{ marginLeft: 10, padding: "2px 10px",
                           background: "var(--st-red-pale)", color: "var(--st-red-mid)",
                           borderRadius: 20, fontSize: 12, fontWeight: 700 }}>
              {therapeutId}
            </span>
          ) : null}
        </p>
      </div>
      <div className="page-body">
        <div className="workflow">

          {/* Kein Therapeuten-Input – Name kommt aus Confluence */}
          {!therapeutId && (
            <Card num="!" title="Benutzername nicht erkannt">
              <p style={{ color: "var(--st-red)", fontSize: 13 }}>
                Der Benutzername konnte nicht aus Confluence gelesen werden.
                Bitte sicherstellen, dass <code>window.SYSTELIOS_USER</code> im
                Confluence User Macro gesetzt ist.
              </p>
            </Card>
          )}

          {/* ── Neues Beispiel hochladen ── */}
          <Card num="B" title="Neues Beispiel hochladen">
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, marginBottom: 14 }}>
              <div>
                <label className="field-label">Dokumenttyp <span style={{ color: "var(--st-red)" }}>*</span></label>
                <select
                  value={dokumenttyp}
                  onChange={e => setDokumenttyp(e.target.value)}
                  style={{ width: "100%", padding: "8px 10px", border: "1px solid var(--st-gray-border)",
                           borderRadius: "var(--radius)", fontFamily: "inherit", fontSize: 14,
                           background: "white", cursor: "pointer" }}
                >
                  {DOKUMENTTYPEN.map(dt => (
                    <option key={dt.value} value={dt.value}>{dt.label}</option>
                  ))}
                </select>
              </div>
              <div style={{ display: "flex", alignItems: "flex-end" }}>
                <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer",
                                fontSize: 14, color: "var(--st-text-mid)", paddingBottom: 2 }}>
                  <input
                    type="checkbox"
                    checked={istStatisch}
                    onChange={e => setIstStatisch(e.target.checked)}
                    style={{ width: 16, height: 16, cursor: "pointer" }}
                  />
                  <span>
                    <strong>Anker-Beispiel</strong>
                    <span style={{ color: "var(--st-text-soft)", marginLeft: 4 }}>
                      (wird immer eingeschlossen)
                    </span>
                  </span>
                </label>
              </div>
            </div>

            <Dropzone
              label="Beispieltext hochladen"
              hint="PDF, DOCX oder TXT · typischer Text dieses Therapeuten für diesen Dokumenttyp"
              accept=".pdf,.docx,.txt"
              icon="📝"
              file={file}
              onFile={setFile}
            />

            <div className="info-note" style={{ marginTop: 10 }}>
              Das Dokument wird automatisch vektorisiert. Beim nächsten Generieren sucht das System
              die passendsten Beispiele heraus — kein manuelles Zuweisen nötig.
            </div>

            <div className="action-bar" style={{ marginTop: 14 }}>
              <button
                className="btn-primary"
                onClick={hochladen}
                disabled={busy || !file || !therapeutId.trim()}
              >
                {busy ? <span className="spin" /> : null}
                {busy ? "Wird gespeichert …" : "Beispiel speichern"}
              </button>
            </div>
          </Card>

          {/* ── Bibliothek anzeigen ── */}
          {liste && (
            <Card num="C" title={`Bibliothek: ${liste.therapeut_id} · ${liste.total} Beispiel${liste.total !== 1 ? "e" : ""}`}>
              {grouped.length === 0 ? (
                <p style={{ color: "var(--st-text-soft)", fontStyle: "italic", fontSize: 13 }}>
                  Noch keine Beispiele vorhanden.
                </p>
              ) : grouped.map(group => (
                <div key={group.value} style={{ marginBottom: 20 }}>
                  <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.1em",
                                textTransform: "uppercase", color: "var(--st-red)",
                                borderBottom: "1px solid var(--st-gray-border)",
                                paddingBottom: 4, marginBottom: 8 }}>
                    {group.label} · {group.items.length} Beispiel{group.items.length !== 1 ? "e" : ""}
                  </div>
                  {group.items.map(item => (
                    <div key={item.embedding_id} style={{
                      display: "flex", alignItems: "flex-start", gap: 10,
                      padding: "8px 10px", marginBottom: 6,
                      background: item.ist_statisch ? "var(--st-red-pale)" : "var(--st-gray-light)",
                      borderRadius: "var(--radius)",
                      border: item.ist_statisch ? "1px solid rgba(139,26,26,0.25)" : "1px solid var(--st-gray-border)",
                    }}>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 12, color: "var(--st-text-soft)", marginBottom: 3 }}>
                          {item.ist_statisch && (
                            <span style={{ background: "var(--st-red)", color: "white",
                                           fontSize: 10, padding: "1px 5px", borderRadius: 3,
                                           marginRight: 6, fontWeight: 700 }}>ANKER</span>
                          )}
                          {item.word_count} Wörter ·{" "}
                          {new Date(item.created_at).toLocaleDateString("de-DE")}
                        </div>
                        <div style={{ fontSize: 13, color: "var(--st-text-mid)",
                                      overflow: "hidden", textOverflow: "ellipsis",
                                      display: "-webkit-box", WebkitLineClamp: 2,
                                      WebkitBoxOrient: "vertical" }}>
                          {item.text_preview}
                        </div>
                      </div>
                      <button
                        onClick={() => loeschen(item.embedding_id)}
                        style={{ flexShrink: 0, background: "none", border: "none",
                                 color: "var(--st-text-soft)", cursor: "pointer",
                                 fontSize: 16, padding: "2px 4px", lineHeight: 1 }}
                        title="Löschen"
                      >×</button>
                    </div>
                  ))}
                </div>
              ))}
            </Card>
          )}

        </div>
      </div>
    </div>
  );
}

// ── App ──────────────────────────────────────────────────────────
const NAVS = [
  { id: "p1", n: "1", title: "Gesprächsdokumentation", sub: "Verlaufsnotiz" },
  { id: "p2", n: "2", title: "Anamnese & Befund",       sub: "Aufnahmegespräch" },
  { id: "p3", n: "3", title: "Verlängerungsantrag",     sub: "Kostenübernahme" },
  { id: "p4", n: "4", title: "Entlassbericht",          sub: "Abschlussbericht" },
  { id: "p5", n: "✦", title: "Stilprofil-Bibliothek",   sub: "Beispiele verwalten" },
];

export default function App() {
  const [page, setPage]       = useState("p1");
  const [msg, setMsg]         = useState(null);
  const [showSettings, setShowSettings] = useState(false);
  const [backendUrl, setBackendUrl] = useState(
    () => localStorage.getItem("systelios_backend_url") || window.SYSTELIOS_API_BASE || ""
  );
  const [urlInput, setUrlInput] = useState(
    () => localStorage.getItem("systelios_backend_url") || window.SYSTELIOS_API_BASE || ""
  );

  const saveUrl = () => {
    let url = urlInput.trim().replace(/\/+$/, ""); // trailing slash entfernen
    // https:// ergänzen falls kein Protokoll angegeben
    if (url && !url.startsWith("http://") && !url.startsWith("https://")) {
      url = "https://" + url;
    }
    localStorage.setItem("systelios_backend_url", url);
    window.SYSTELIOS_API_BASE = url;
    setBackendUrl(url);
    setUrlInput(url);
    setShowSettings(false);
    toast("Backend-URL gespeichert");
  };

  const toast = useCallback((t) => {
    setMsg(t);
    setTimeout(() => setMsg(null), 2400);
  }, []);

  // Beim ersten Start ohne URL: Settings automatisch öffnen
  const firstRun = !backendUrl;

  return (
    <div id="st-root" style={{
      display:"flex",
      flexDirection:"row",
      minHeight:"600px",
      width:"100%",
      position:"relative",
      border:"1px solid rgba(0,0,0,0.08)",
      borderRadius:"8px"
    }}>
      <style>{S}</style>

      <div className="sidebar">
        <div className="sidebar-section-label">KI-Dokumentation</div>

        {NAVS.map((n) => (
          <div key={n.id} className={"nav-item" + (page === n.id ? " active" : "")} onClick={() => setPage(n.id)}>
            <div className="nav-item-inner">
              <div className="nav-step-num">{n.n}</div>
              <div>
                <div className="nav-item-title">{n.title}</div>
                <div className="nav-item-sub">{n.sub}</div>
              </div>
            </div>
          </div>
        ))}

        <div className="sidebar-footer">
          <div style={{fontSize:11,color:"rgba(255,255,255,0.35)",lineHeight:1.6,marginBottom:10}}>
            scriptTelios · v0.1 · sysTelios Klinik f&#252;r Psychosomatik und Psychotherapie
          </div>
          <button
            onClick={() => { setUrlInput(backendUrl); setShowSettings(true); }}
            style={{
              display:"flex", alignItems:"center", gap:7,
              background: backendUrl ? "rgba(255,255,255,0.08)" : "rgba(168,40,30,0.6)",
              border: backendUrl ? "1px solid rgba(255,255,255,0.15)" : "1px solid rgba(168,40,30,0.8)",
              borderRadius:4, padding:"6px 12px", cursor:"pointer",
              color:"rgba(255,255,255,0.75)", fontSize:11, fontWeight:600,
              width:"100%", letterSpacing:"0.04em"
            }}
          >
            <span style={{fontSize:14}}>⚙</span>
            {backendUrl ? "Backend-URL ändern" : "⚠ Backend-URL fehlt"}
          </button>
          {backendUrl && (
            <div style={{
              marginTop:6, fontSize:10, color:"rgba(255,255,255,0.25)",
              overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap"
            }} title={backendUrl}>
              {backendUrl.replace("https://","").replace("http://","")}
            </div>
          )}
        </div>
      </div>

      <main className="main">
        {page === "p1" && <P1 toast={toast} />}
        {page === "p2" && <P2 toast={toast} />}
        {page === "p3" && <P3 toast={toast} />}
        {page === "p4" && <P4 toast={toast} />}
        {page === "p5" && <P5 toast={toast} />}
      </main>

      {/* Settings Modal */}
      {(showSettings || firstRun) && (
        <div style={{
          position:"fixed", inset:0, background:"rgba(0,0,0,0.55)",
          display:"flex", alignItems:"center", justifyContent:"center",
          zIndex:1000
        }} onClick={(e) => { if(e.target===e.currentTarget && !firstRun) setShowSettings(false); }}>
          <div style={{
            background:"#fff", borderRadius:8, padding:"32px 28px", width:480,
            boxShadow:"0 8px 40px rgba(0,0,0,0.25)"
          }}>
            <div style={{marginBottom:20}}>
              <div style={{fontSize:18, fontWeight:700, color:"#2c2c2c", marginBottom:6}}>
                ⚙ Backend-URL einstellen
              </div>
              <div style={{fontSize:13, color:"#777c74", lineHeight:1.6}}>
                Tragt hier die URL des sysTelios-Backends ein.<br />
                Diese wird im Browser gespeichert und bleibt beim nächsten Aufruf erhalten.
              </div>
            </div>

            <div style={{marginBottom:8, fontSize:12, fontWeight:600, color:"#444", textTransform:"uppercase", letterSpacing:"0.06em"}}>
              Backend URL
            </div>
            <input
              type="text"
              value={urlInput}
              onChange={e => setUrlInput(e.target.value)}
              onKeyDown={e => e.key === "Enter" && saveUrl()}
              placeholder="https://abc123-8000.proxy.runpod.net"
              autoFocus
              style={{
                width:"100%", padding:"10px 12px", borderRadius:4,
                border:"1px solid #ccc", fontSize:13, fontFamily:"monospace",
                boxSizing:"border-box", marginBottom:8
              }}
            />
            <div style={{fontSize:11, color:"#a0a49e", marginBottom:20, lineHeight:1.6}}>
              Testphase RunPod: <code style={{background:"#f0eeea",padding:"1px 5px",borderRadius:3}}>https://&lt;pod-id&gt;-8000.proxy.runpod.net</code><br />
              Produktion: <code style={{background:"#f0eeea",padding:"1px 5px",borderRadius:3}}>http://systelios-server:8000</code>
            </div>

            <div style={{display:"flex", gap:10, justifyContent:"flex-end"}}>
              {!firstRun && (
                <button onClick={() => setShowSettings(false)} style={{
                  padding:"8px 20px", borderRadius:4, border:"1px solid #ccc",
                  background:"#fff", cursor:"pointer", fontSize:13, color:"#666"
                }}>
                  Abbrechen
                </button>
              )}
              <button onClick={saveUrl} disabled={!urlInput.trim()} style={{
                padding:"8px 24px", borderRadius:4, border:"none",
                background: urlInput.trim() ? "#8b1a1a" : "#ccc",
                cursor: urlInput.trim() ? "pointer" : "not-allowed",
                fontSize:13, fontWeight:600, color:"#fff"
              }}>
                Speichern
              </button>
            </div>
          </div>
        </div>
      )}

      {msg && createPortal(
        <div className="toast">
          <span className="toast-dot" />
          {msg}
        </div>,
        document.body
      )}
    </div>
  );
}

// ── Mount ─────────────────────────────────────────────────────────────────────
import { createRoot } from "react-dom/client";
const container = document.getElementById("systelios-app");
if (container) createRoot(container).render(<App />);
