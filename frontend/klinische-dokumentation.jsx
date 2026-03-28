import { useState, useRef, useCallback, useEffect } from "react";
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
    padding: 20px 16px 8px;
  }

  .nav-item {
    display: flex; align-items: stretch;
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
    display: flex; align-items: flex-start; gap: 12px;
    padding: 12px 16px;
    flex: 1;
  }
  .nav-step-num {
    width: 24px; height: 24px; border-radius: 50%;
    border: 1px solid rgba(255,255,255,0.22);
    color: rgba(255,255,255,0.45);
    font-size: 11px; font-weight: 700;
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0; margin-top: 1px;
    transition: all 0.15s;
  }
  .nav-item.active .nav-step-num {
    background: var(--st-red);
    border-color: var(--st-red);
    color: white;
  }
  .nav-item-title {
    font-size: 13px; font-weight: 400;
    color: rgba(255,255,255,0.72);
    line-height: 1.4; margin-bottom: 2px;
  }
  .nav-item.active .nav-item-title { color: #fff; font-weight: 600; }
  .nav-item-sub {
    font-size: 11px; font-weight: 300;
    color: rgba(255,255,255,0.35);
    line-height: 1.3;
  }

  .sidebar-footer {
    margin-top: auto;
    padding: 16px;
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
    padding: 24px 32px 20px;
  }
  .page-eyebrow {
    font-size: 10px; font-weight: 700;
    letter-spacing: 0.18em; text-transform: uppercase;
    color: var(--st-red); margin-bottom: 6px;
    display: flex; align-items: center; gap: 8px;
  }
  .page-eyebrow::after {
    content: ''; flex: 0 0 20px; height: 1px;
    background: var(--st-red); opacity: 0.6;
  }
  .page-header h2 {
    font-size: 22px; font-weight: 600;
    color: #fff; line-height: 1.25;
    letter-spacing: -0.01em; margin: 0;
  }
  .page-header p {
    font-size: 13px; color: rgba(255,255,255,0.45);
    margin-top: 4px; font-weight: 300;
    font-style: italic; margin-bottom: 0;
  }

  .page-body { padding: 24px 32px 40px; max-width: 900px; }

  /* ── STEP CARDS ── */
  .workflow { display: flex; flex-direction: column; gap: 10px; }

  .step-card {
    background: var(--st-white);
    border: 1px solid var(--st-gray-mid);
    border-radius: 5px;
    overflow: hidden;
    margin: 0;
  }
  .step-head {
    display: flex; align-items: center; gap: 12px;
    padding: 14px 20px;
    cursor: pointer; user-select: none;
    border-bottom: 1px solid transparent;
    transition: background 0.15s;
  }
  .step-head:hover { background: var(--st-gray-light); }
  .step-head.open { border-bottom-color: var(--st-gray-mid); }

  .step-num {
    width: 28px; height: 28px; border-radius: 50%;
    background: var(--st-red);
    color: white; font-size: 12px; font-weight: 700;
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0; margin: 0;
  }
  .step-label {
    flex: 1; font-size: 14px; font-weight: 600;
    color: var(--st-text); margin: 0;
  }
  .step-pill {
    font-size: 10px; font-weight: 700;
    letter-spacing: 0.08em; text-transform: uppercase;
    padding: 3px 9px; border-radius: 20px;
  }
  .pill-opt { background: var(--st-gray-light); color: var(--st-text-soft); }
  .pill-req { background: rgba(139,26,26,0.10); color: var(--st-red-mid); }
  .step-caret {
    font-size: 11px; color: var(--st-text-pale);
    transition: transform 0.18s; display: inline-block;
  }
  .step-caret.open { transform: rotate(180deg); }
  .step-body { padding: 20px 24px 24px; display: flex; flex-direction: column; gap: 16px; }

  /* ── UPLOAD ── */
  .upload-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }

  /* ── InputTabs ──────────────────────────────────────────────── */
  .input-tabs-wrap { display: flex; flex-direction: column; gap: 0; }
  .input-tabs-bar {
    display: flex; gap: 2px; border-bottom: 2px solid var(--st-gray-border);
    margin-bottom: 12px;
  }
  .input-tab {
    display: flex; align-items: center; gap: 5px;
    padding: 6px 14px; border: none; background: none; cursor: pointer;
    font-size: 12px; font-weight: 500; color: var(--st-text-soft);
    border-bottom: 2px solid transparent; margin-bottom: -2px;
    border-radius: 3px 3px 0 0; transition: all 0.12s;
  }
  .input-tab:hover { color: var(--st-text); background: var(--st-gray-light); }
  .input-tab.active {
    color: var(--st-red); border-bottom-color: var(--st-red);
    font-weight: 600; background: none;
  }
  .input-tab-icon { font-size: 13px; }
  .input-tabs-body { min-height: 80px; }
  .upload-col-label {
    font-size: 11px; font-weight: 600;
    letter-spacing: 0.06em; text-transform: uppercase;
    color: var(--st-text-soft); margin-bottom: 8px;
  }

  .dropzone {
    border: 2px dashed var(--st-gray-mid);
    border-radius: 5px;
    padding: 28px 20px; text-align: center;
    cursor: pointer; position: relative;
    transition: border-color 0.15s, background 0.15s;
    background: var(--st-cream);
    min-height: 110px;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
  }
  .dropzone:hover { border-color: var(--st-red); background: var(--st-red-pale); }
  .dropzone.drag { border-color: var(--st-red-mid); background: var(--st-red-pale); }
  .dropzone.filled { border-style: solid; border-color: var(--st-red); background: var(--st-red-pale); }
  .dropzone input[type=file] { position: absolute; inset: 0; opacity: 0; cursor: pointer; width: 100%; height: 100%; }
  .dz-icon { font-size: 24px; margin-bottom: 8px; opacity: 0.45; }
  .dz-label { font-size: 13px; font-weight: 600; color: var(--st-text-mid); margin: 0; }
  .dz-hint { font-size: 12px; color: var(--st-text-pale); margin-top: 4px; font-weight: 300; }
  .dz-file {
    display: flex; align-items: center; gap: 8px;
    font-size: 13px; font-weight: 600; color: var(--st-red-mid);
  }
  .dz-remove {
    background: none; border: none; cursor: pointer;
    color: var(--st-text-pale); font-size: 18px;
    padding: 0; line-height: 1;
  }
  .dz-remove:hover { color: var(--st-red); }

  /* ── OR DIVIDER ── */
  .or-row {
    display: flex; align-items: center; gap: 12px;
    font-size: 11px; font-weight: 600; letter-spacing: 0.08em;
    text-transform: uppercase; color: var(--st-text-pale);
    margin: 0;
  }
  .or-row::before, .or-row::after {
    content: ''; flex: 1; height: 1px; background: var(--st-gray-mid);
  }

  /* ── FIELDS ── */
  .field-label {
    font-size: 11px; font-weight: 700;
    color: var(--st-text-mid); margin-bottom: 6px; margin-top: 0;
    display: block; letter-spacing: 0.04em;
    text-transform: uppercase;
  }
  .field-note {
    font-size: 12px; color: var(--st-text-pale);
    font-weight: 300; font-style: italic; margin-top: 6px;
  }
  textarea {
    width: 100%; border: 1px solid var(--st-gray-mid);
    border-radius: 4px; padding: 12px 14px;
    font-size: 14px; line-height: 1.6;
    color: var(--st-text); background: var(--st-cream);
    resize: vertical; margin: 0;
    transition: border-color 0.15s, box-shadow 0.15s;
  }
  textarea:focus {
    outline: none; border-color: var(--st-red);
    box-shadow: 0 0 0 3px rgba(139,26,26,0.08);
    background: white;
  }

  /* ── PROMPT EDITOR ── */
  .prompt-box { border: 1px solid var(--st-gray-mid); border-radius: 4px; overflow: hidden; }
  .prompt-bar {
    display: flex; align-items: center; justify-content: space-between;
    padding: 10px 14px; background: var(--st-gray-light);
    border-bottom: 1px solid var(--st-gray-mid);
  }
  .prompt-bar-label {
    font-size: 11px; font-weight: 600; letter-spacing: 0.06em;
    text-transform: uppercase; color: var(--st-text-soft); margin: 0;
  }
  .btn-xs {
    font-size: 12px; padding: 4px 10px; border-radius: 3px;
    border: 1px solid var(--st-gray-border); background: white;
    color: var(--st-text-mid); cursor: pointer; font-weight: 500;
    transition: border-color 0.12s;
  }
  .btn-xs:hover { border-color: var(--st-red); }
  .prompt-box textarea { border: none; border-radius: 0; background: white; font-size: 13px; }
  .prompt-box textarea:focus { box-shadow: none; }

  /* ── DIAGNOSE TAGS ── */
  .tag-wrap { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
  .tag {
    display: inline-flex; align-items: center; gap: 5px;
    background: var(--st-red-pale); border: 1px solid rgba(139,26,26,0.20);
    border-radius: 3px; padding: 4px 10px;
    font-size: 13px; font-weight: 600; color: var(--st-red-mid);
  }
  .tag-x {
    background: none; border: none; cursor: pointer;
    color: rgba(139,26,26,0.45); font-size: 15px;
    padding: 0; line-height: 1; font-weight: 400;
  }
  .tag-x:hover { color: var(--st-red); }
  .tag-input {
    border: 1px dashed var(--st-gray-border); border-radius: 3px;
    padding: 4px 10px; font-size: 13px;
    color: var(--st-text); background: transparent;
    outline: none; width: 190px;
  }
  .tag-input:focus { border-color: var(--st-red); background: white; }

  /* ── INFO NOTE ── */
  .info-note {
    border-left: 3px solid var(--st-red);
    background: var(--st-red-pale);
    padding: 10px 14px; border-radius: 0 4px 4px 0;
    font-size: 13px; color: var(--st-text-mid); font-weight: 400;
    line-height: 1.55; margin: 0;
  }

  /* ── ACTION BAR ── */
  .action-bar {
    display: flex; justify-content: flex-end;
    padding-top: 16px; border-top: 1px solid var(--st-gray-mid);
    padding-bottom: 0; padding-left: 0; padding-right: 0;
  }

  /* ── BUTTONS ── */
  .btn-primary {
    background: var(--st-red); color: white;
    border: none; border-radius: 3px;
    padding: 10px 24px; font-size: 14px; font-weight: 600;
    cursor: pointer; letter-spacing: 0.01em;
    transition: background 0.15s, box-shadow 0.15s;
    display: inline-flex; align-items: center; gap: 8px;
    margin: 0;
  }
  .btn-primary:hover:not(:disabled) {
    background: var(--st-red-hover);
    box-shadow: 0 2px 10px rgba(139,26,26,0.28);
  }
  .btn-primary:disabled { opacity: 0.40; cursor: not-allowed; }

  .btn-secondary {
    background: white; color: var(--st-text-mid);
    border: 1px solid var(--st-gray-border); border-radius: 3px;
    padding: 10px 20px; font-size: 14px; font-weight: 600;
    cursor: pointer; letter-spacing: 0.01em;
    transition: background 0.15s, border-color 0.15s;
    display: inline-flex; align-items: center; gap: 6px;
  }
  .btn-secondary:hover { background: var(--st-gray-light); border-color: var(--st-text-soft); }

  /* ── OUTPUT ── */
  .output-card {
    background: white; border: 1px solid var(--st-gray-mid);
    border-radius: 5px; overflow: hidden; margin: 0;
  }
  .output-head {
    display: flex; align-items: center; justify-content: space-between;
    padding: 14px 20px; background: var(--st-sidebar);
  }
  .output-title {
    font-size: 15px; font-weight: 600; color: white; margin: 0;
  }
  .output-btns { display: flex; gap: 8px; }
  .btn-out {
    background: rgba(255,255,255,0.12);
    border: 1px solid rgba(255,255,255,0.22);
    color: white; border-radius: 3px;
    padding: 5px 14px; font-size: 12px; font-weight: 600;
    cursor: pointer; letter-spacing: 0.04em; text-transform: uppercase;
    transition: background 0.12s;
  }
  .btn-out:hover { background: rgba(255,255,255,0.22); }
  .output-tabs { display: flex; background: var(--st-gray-light); border-bottom: 1px solid var(--st-gray-mid); }
  .otab {
    padding: 10px 18px; font-size: 13px; font-weight: 600;
    cursor: pointer; border-bottom: 2px solid transparent;
    color: var(--st-text-soft); margin: 0;
    transition: all 0.12s;
  }
  .otab.on { color: var(--st-red); border-bottom-color: var(--st-red); }
  .output-text {
    padding: 24px; font-size: 14px; line-height: 1.8;
    color: var(--st-text-mid); min-height: 140px;
    white-space: pre-wrap; font-weight: 400; margin: 0;
  }
  .output-text.empty { color: var(--st-text-pale); font-style: italic; font-size: 13px; }

  /* ── SPINNER ── */
  @keyframes spin { to { transform: rotate(360deg); } }
  .spin {
    width: 14px; height: 14px;
    border: 2px solid rgba(255,255,255,0.3);
    border-top-color: white; border-radius: 50%;
    animation: spin 0.7s linear infinite;
    display: inline-block;
  }

  /* ── TOAST ── */
  .toast {
    position: fixed; bottom: 24px; right: 24px;
    background: var(--st-sidebar); color: white;
    padding: 12px 20px; border-radius: 4px;
    font-size: 13px; font-weight: 600;
    box-shadow: var(--shadow-md); z-index: 999;
    display: flex; align-items: center; gap: 10px;
  }
  .toast-dot {
    width: 7px; height: 7px; border-radius: 50%;
    background: var(--st-red); flex-shrink: 0;
  }

  /* scrollbar */
  #st-root ::-webkit-scrollbar { width: 5px; }
  #st-root ::-webkit-scrollbar-thumb { background: var(--st-gray-mid); border-radius: 3px; }
`;

// ── Prompts ─────────────────────────────────────────────────────
const P_DOKU = `Erstelle eine systemische Gesprächsdokumentation. Schreibe aktiv aus der Perspektive der Klientin/des Klienten – nicht über das Gespräch, sondern über die Person und ihre Themen. Gliedere den Text in folgende vier Abschnitte mit den jeweiligen Überschriften:

**Auftragsklärung**
Beschreibe worum es der Klientin/dem Klienten ging und was das gemeinsame Ziel des Gesprächs war. Beispiel: "Im Mittelpunkt stand..." oder "Frau X. kam mit dem Anliegen..."

**Relevante Gesprächsinhalte**
Schildere die wesentlichen Inhalte aus Sicht der Klientin/des Klienten: Symptome, Erlebensmuster, innere Anteile, Beziehungsdynamiken, Ressourcen. Konkrete Formulierungen statt allgemeiner Beschreibungen. Systemische und IFS-Begriffe wo passend (Manager-Anteile, Exile, Self-Energy etc.).

**Hypothesen und Entwicklungsperspektiven**
Formuliere systemische Hypothesen über Sinnzusammenhänge. Zeige Entwicklungsperspektiven auf – was wird möglich, wenn... Ressourcenorientiert und konkret.

**Einladungen**
Beschreibe die konkreten Aufgaben, Übungen oder Impulse die mitgegeben wurden – aktiv formuliert: "Frau X. wurde eingeladen, ..." oder "Als Übung wurde vereinbart, ..."

Stil: Fließtext pro Abschnitt, aktiv, konkret, systemisch-wertschätzend. Keine Sektion über den Gesprächsstil.`;

const P_ANAMNESE = `Erstelle Anamnese und psychopathologischen Befund aus systemischer Perspektive auf Basis der vorliegenden Unterlagen.

ANAMNESE (Fließtext):
Vorstellungsanlass und Hauptbeschwerden im Kontext des sozialen Systems. Beginn und Verlauf der Beschwerden, auslösende und aufrechterhaltende Faktoren im Familien- und Beziehungskontext, psychiatrische und somatische Vorgeschichte, Medikation, Familienanamnese mit Blick auf Muster und Überzeugungen, Sozialanamnese (Herkunft, Bildung, Beruf, Beziehungen, Kinder), Schlaf, Ernährung, Bewegung, Suchtmittel.

PSYCHOPATHOLOGISCHER BEFUND (AMDP):
Bewusstsein | Orientierung | Aufmerksamkeit | Gedächtnis | Formales Denken | Inhaltliches Denken | Wahrnehmung | Ich-Erleben | Affektivität | Antrieb | Psychomotorik | Suizidalität/Selbstverletzung

SYSTEMISCHE EINSCHÄTZUNG:
Hypothesen zu Sinnzusammenhängen, Funktionen der Symptome im System, relevante Beziehungsmuster und Ressourcen.

Diagnosen: {diagnosen}`;

const P_VERL = `Optionale Schwerpunkte für diesen Verlängerungsantrag:
– Welche Anteile / Themen besonders hervorheben?
– Besondere Wendepunkte oder Krisen im Verlauf?
– Spezifische noch ausstehende Therapieziele?

Leer lassen wenn keine besonderen Schwerpunkte gesetzt werden sollen.`;

const P_ENTL = `Optionale Schwerpunkte für diesen Entlassbericht:
– Welche Themen / Anteile besonders hervorheben?
– Besondere therapeutische Wendepunkte?
– Spezifische Empfehlungen für die Weiterbehandlung?

Beispiel: "Wächteranteil Türsteher, Gruppenarbeit, Entschluss zur räumlichen Trennung"

Leer lassen wenn keine besonderen Schwerpunkte gesetzt werden sollen.`;

// ── Helpers ──────────────────────────────────────────────────────
function Dropzone({ label, hint, accept, file, onFile, icon }) {
  const [drag, setDrag] = useState(false);

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
          <input type="file" accept={accept}
            onChange={(e) => { if (e.target.files && e.target.files[0]) onFile(e.target.files[0]); }} />
        </>
      )}
    </div>
  );
}

/**
 * InputTabs – kompakte Tab-Navigation für alternative Eingabemodi.
 * Kinder bekommen den aktiven Tab-ID als Argument (render-prop).
 * Beispiel:
 *   <InputTabs tabs={[{id:"a",icon:"🎙",label:"Audio"}, ...]}>
 *     {(active) => active === "a" && <Dropzone ... />}
 *   </InputTabs>
 */
/**
 * ModelSelector – kompakter Inline-Selektor für das LLM-Modell.
 * Lädt verfügbare Modelle vom Backend und zeigt sie als Buttons an.
 * Props: model (aktiver Wert), onChange (Callback), apiBase
 */
function ModelSelector({ model, onChange, apiBase }) {
  const [models, setModels] = useState([]);
  const [loading, setLoading] = useState(false);
  const [defaultModel, setDefaultModel] = useState("");

  useEffect(() => {
    setLoading(true);
    fetch(`${apiBase}/models`)
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data?.models?.length) {
          setModels(data.models);
          setDefaultModel(data.default || "");
          // Wenn noch kein Modell gewählt, Default setzen
          if (!model && data.default) onChange(data.default);
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [apiBase]);

  if (loading || models.length === 0) return null;

  // Effektiv aktives Modell: explizite Wahl > localStorage-Default > Server-Default
  const activeModel = model || defaultModel;

  return (
    <div style={{display:"flex", alignItems:"center", gap:4, flexWrap:"wrap"}}>
      <span style={{fontSize:11, fontWeight:600, color:"var(--st-text-soft)",
        textTransform:"uppercase", letterSpacing:"0.06em", marginRight:2}}>Modell</span>
      {models.map(m => {
        const isActive = activeModel === m.name || (!activeModel && m.is_default);
        const shortName = m.name.replace(/:latest$/, "");
        return (
          <button key={m.name} onClick={() => onChange(m.name)} title={m.name}
            style={{
              padding:"3px 8px", borderRadius:3, cursor:"pointer",
              fontSize:11, fontWeight: isActive ? 700 : 400,
              background: isActive ? "var(--st-red)" : "var(--st-gray-light)",
              color: isActive ? "white" : "var(--st-text-soft)",
              border: isActive ? "1px solid var(--st-red)" : "1px solid var(--st-gray-border)",
              transition:"all 0.12s", whiteSpace:"nowrap",
            }}>
            {shortName}
            {m.size_gb ? <span style={{opacity:0.75, fontSize:10}}> {m.size_gb}G</span> : null}
          </button>
        );
      })}
    </div>
  );
}

function InputTabs({ tabs, children, defaultTab }) {
  const [active, setActive] = useState(defaultTab || tabs[0]?.id);
  return (
    <div className="input-tabs-wrap">
      <div className="input-tabs-bar">
        {tabs.map(t => (
          <button
            key={t.id}
            className={"input-tab" + (active === t.id ? " active" : "")}
            onClick={() => setActive(t.id)}
            type="button"
          >
            <span className="input-tab-icon">{t.icon}</span>
            <span className="input-tab-label">{t.label}</span>
          </button>
        ))}
      </div>
      <div className="input-tabs-body">
        {children(active)}
      </div>
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

function Output({ text, loading, tabs, activeTab, onTab, onCopy, onDownload, extraButtons = [] }) {
  const empty = !text && !loading;
  return (
    <div className="output-card">
      <div className="output-head">
        <span className="output-title">Ergebnis</span>
        <div className="output-btns">
          {text && <button className="btn-out" onClick={onCopy}>Kopieren</button>}
          {text && onDownload && <button className="btn-out" onClick={onDownload}>Download</button>}
          {extraButtons.map((btn, i) => (
            <button key={i} className="btn-out" onClick={btn.onClick}>{btn.label}</button>
          ))}
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

// ── Job-Persistenz ───────────────────────────────────────────────
// Speichert laufende Jobs in localStorage damit Seitenreloads den Job
// nicht verlieren. Wird beim App-Start automatisch wiederaufgenommen.

const JOB_STORAGE_KEY = "systelios_active_job";

function saveActiveJob(jobId, page) {
  try {
    localStorage.setItem(JOB_STORAGE_KEY, JSON.stringify({ jobId, page, startedAt: Date.now() }));
  } catch (_) {}
}

function loadActiveJob() {
  try {
    const raw = localStorage.getItem(JOB_STORAGE_KEY);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch (_) { return null; }
}

function clearActiveJob() {
  try { localStorage.removeItem(JOB_STORAGE_KEY); } catch (_) {}
}

// Wandelt technische Fehlermeldungen in verständliche Texte um.
function friendlyError(e) {
  const msg = e?.message || String(e);
  if (msg === "Failed to fetch" || msg.includes("NetworkError") || msg.includes("fetch"))
    return "Server nicht erreichbar. Bitte überprüfe die Serveradresse in den Einstellungen.";
  if (msg.includes("502") || msg.includes("Bad Gateway"))
    return "Server antwortet nicht (502). Bitte warte einen Moment und versuche es erneut.";
  if (msg.includes("503") || msg.includes("Service Unavailable"))
    return "Server überlastet (503). Bitte versuche es in Kürze erneut.";
  if (msg.includes("401") || msg.includes("403"))
    return "Zugriff verweigert. Bitte überprüfe die Serveradresse in den Einstellungen.";
  if (msg.includes("404"))
    return "Endpunkt nicht gefunden (404). Bitte überprüfe die Serveradresse in den Einstellungen.";
  if (msg.includes("timeout") || msg.includes("Timeout"))
    return "Zeitüberschreitung – der Server hat zu lange nicht geantwortet.";
  return msg;
}

// Polling fuer eine bekannte job_id – wiederverwendbar fuer Resume.
// Stoppt automatisch wenn der Server status="cancelled" zurueckgibt.
async function pollJob(jobId, maxWaitSeconds = 1200) {
  const interval = 2;
  for (let i = 0; i < maxWaitSeconds / interval; i++) {
    await new Promise(res => setTimeout(res, interval * 1000));
    const poll = await fetch(`${getApiBase()}/jobs/${jobId}`);
    if (!poll.ok) continue;
    const job = await poll.json();
    if (job.status === "done")      return job;
    if (job.status === "error")     throw new Error(job.error_msg || "Job fehlgeschlagen");
    if (job.status === "cancelled") return null;  // Server hat abgebrochen
  }
  throw new Error("Timeout: Job dauert zu lange");
}

async function generate(workflow, prompt, userContent, files = {}, page = null) {
  const therapeutId = getConfluenceUser();
  const fd = new FormData();
  fd.append("workflow",   workflow);
  fd.append("prompt",     prompt);
  fd.append("transcript", userContent);
  if (therapeutId)      fd.append("therapeut_id",  therapeutId);
  if (files.audio)      fd.append("audio",          files.audio);
  if (files.selbst)     fd.append("selbstauskunft", files.selbst);
  if (files.vorbef)     fd.append("vorbefunde",     files.vorbef);
  if (files.style)      fd.append("style_file",     files.style);
  if (files.diagnosen)  fd.append("diagnosen",      files.diagnosen);
  if (files.bullets)    fd.append("bullets",        files.bullets);
  if (files.styleText)  fd.append("style_text",     files.styleText);
  if (files.model)      fd.append("model",           files.model);

  // Job starten
  const r = await fetch(`${getApiBase()}/jobs/generate`, { method: "POST", body: fd });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || r.statusText);

  const jobId = d.job_id;
  saveActiveJob(jobId, page);

  try {
    const job = await pollJob(jobId, 1200);
    clearActiveJob();
    return {
      text:        job.result_text   || "",
      befundText:  job.befund_text   || "",
      akutText:    job.akut_text     || "",
      jobId,
      hasTranscript: job.has_transcript || false,
    };
  } catch (e) {
    clearActiveJob();
    throw e;
  }
}

// Laedt das Transkript eines Jobs vom Backend und speichert es als .txt
async function downloadTranscript(jobId, filename = "transkript.txt") {
  const r = await fetch(`${getApiBase()}/jobs/${jobId}/transcript`);
  if (!r.ok) throw new Error("Transkript nicht verfügbar");
  const data = await r.json();
  const blob = new Blob([data.transcript], { type: "text/plain;charset=utf-8" });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href     = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

// ── Pages ────────────────────────────────────────────────────────
function P1({ toast, resumeJob, onResumed, model }) {
  const [audio, setAudio]   = useState(null);
  const [txtFile, setTxtFile] = useState(null);
  const [text, setText]     = useState("");
  const [bullets, setBullets] = useState("");
  const [style, setStyle]     = useState(null);
  const [styleText, setStyleText] = useState("");
  const [prompt, setPrompt] = useState(P_DOKU);
  const [out, setOut]           = useState("");
  const [lastJobId, setLastJobId] = useState(null);
  const [hasTranscript, setHasTranscript] = useState(false);
  const [busy, setBusy]         = useState(false);
  const [geschlecht, setGeschlecht] = useState("auto");
  const [kuerzel, setKuerzel]         = useState("");

  // Resume: laufenden Job nach Reload wieder aufnehmen
  useEffect(() => {
    if (!resumeJob || resumeJob.page !== "p1") return;
    setBusy(true);
    pollJob(resumeJob.jobId, 1200)
      .then(job => {
        if (!job) { setBusy(false); onResumed(); return; } // cancelled
        setOut(job.result_text || "");
        setLastJobId(resumeJob.jobId);
        setHasTranscript(job.has_transcript || false);
        onResumed();
      })
      .catch(e => { setOut("Fehler: " + friendlyError(e)); onResumed(); })
      .finally(() => setBusy(false));
  }, [resumeJob]);

  function cancelRun() {
    // Server-seitigen Job abbrechen – pollJob stoppt bei status="cancelled"
    const jobId = loadActiveJob()?.jobId;
    if (jobId) {
      fetch(`${getApiBase()}/jobs/${jobId}`, { method: "DELETE" }).catch(() => {});
    }
    clearActiveJob();
    setBusy(false);
  }

  async function run() {
    setBusy(true);
    setLastJobId(null);
    setHasTranscript(false);
    const k = kuerzel.trim().replace(/\.?$/, "."); // sicherstellen dass Punkt am Ende
    const nameHinweis = kuerzel.trim()
      ? ` Verwende als Namenskürzel durchgehend "${k}" (z.B. "Frau ${k}", "Herr ${k}", "Klient ${k}").`
      : "";
    const geschlechtHinweis = {
      "w":    `\n\nKLIENT-GESCHLECHT: weiblich – verwende durchgehend weibliche Formen (die Klientin, sie, ihr).${nameHinweis}`,
      "m":    `\n\nKLIENT-GESCHLECHT: männlich – verwende durchgehend männliche Formen (der Klient, er, ihm).${nameHinweis}`,
      "auto": `\n\nKLIENT-GESCHLECHT: Leite das Geschlecht aus dem Transkript ab (Namen, Pronomen, Anreden). Falls nicht erkennbar, verwende neutrale Formen.${nameHinweis}`,
    }[geschlecht];

    const promptMitGeschlecht = prompt + geschlechtHinweis;

    try {
      const result = await generate("dokumentation", promptMitGeschlecht, text || "", {
        audio: audio,
        style: style,
        styleText: styleText || null,
        bullets: bullets || null,
        model: model || null,
      }, "p1");
      setOut(result.text || "");
      setLastJobId(result.jobId);
      setHasTranscript(result.hasTranscript || false);
    }
    catch (e) { setOut("Fehler: " + friendlyError(e)); }
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
            <InputTabs
              tabs={[
                { id:"audio", icon:"🎙", label:"Aufnahme" },
                { id:"file",  icon:"📄", label:"Datei"    },
                { id:"text",  icon:"✏️", label:"Text"     },
              ]}
            >
              {(activeTab) => (<>
                {activeTab === "audio" && (
                  <Dropzone label="Aufnahme hochladen" hint=".mp3  .m4a  .wav" accept="audio/*" icon="&#127897;" file={audio} onFile={setAudio} />
                )}
                {activeTab === "file" && (
                  <Dropzone label="Transkript-Datei hochladen" hint=".txt  .docx" accept=".txt,.docx" icon="&#128196;" file={txtFile} onFile={setTxtFile} />
                )}
                {activeTab === "text" && (
                  <textarea rows={6} placeholder="Gesprächsinhalt direkt hier einfügen ..." value={text} onChange={(e) => setText(e.target.value)} style={{marginTop:0}} />
                )}
              </>)}
            </InputTabs>
          </Card>

          <Card num="B" title="Stichpunkte" badge="opt" open={false}>
            <label className="field-label">Relevante Themen und Beobachtungen</label>
            <textarea rows={4} placeholder={"- Bericht ueber das Wochenende\n- Schlafprobleme anhaltend\n- Fortschritt bei Expositionsuebung ..."} value={bullets} onChange={(e) => setBullets(e.target.value)} />
            <div className="field-note">Ergaenzt oder ersetzt das Transkript bei Bedarf</div>
          </Card>

          <Card num="C" title="Stilvorlage" badge="opt" open={false}>
            <InputTabs
              tabs={[
                { id:"file", icon:"📎", label:"Datei"  },
                { id:"text", icon:"✏️", label:"Text C&P" },
              ]}
            >
              {(activeTab) => (<>
                {activeTab === "file" && (<>
                  <Dropzone label="Beispieltext hochladen" hint="PDF, DOCX oder TXT" accept=".pdf,.docx,.txt" icon="&#128221;" file={style} onFile={setStyle} />
                  <div className="info-note" style={{marginTop:8}}>Der Schreibstil des hochgeladenen Textes wird bei der Generierung berücksichtigt.</div>
                </>)}
                {activeTab === "text" && (<>
                  <textarea rows={6} placeholder="Beispieldokumentation hier einfügen – der Schreibstil wird übernommen ..." value={styleText} onChange={(e) => setStyleText(e.target.value)} style={{marginTop:0}} />
                  <div className="field-note">Direkt eingefügter Beispieltext als Stilvorlage</div>
                </>)}
              </>)}
            </InputTabs>
          </Card>

          <Card num="D" title="Prompt anpassen" open={false}>
            <PromptEditor value={prompt} onChange={setPrompt} def={P_DOKU} />
          </Card>

          <div className="action-bar">
            {/* Geschlecht-Toggle + Kürzel */}
            <div style={{display:"flex", alignItems:"center", gap:6, marginRight:"auto", flexWrap:"wrap"}}>
              <span style={{fontSize:11, fontWeight:600, color:"var(--st-text-soft)", textTransform:"uppercase", letterSpacing:"0.06em"}}>Klient</span>
              {[
                { val:"w", label:"♀ weiblich" },
                { val:"m", label:"♂ männlich" },
                { val:"auto", label:"Auto"    },
              ].map(({ val, label }) => (
                <button key={val} onClick={() => setGeschlecht(val)} style={{
                  padding:"4px 10px", borderRadius:3, cursor:"pointer",
                  fontSize:12, fontWeight: geschlecht === val ? 700 : 400,
                  background: geschlecht === val ? "var(--st-red)" : "var(--st-gray-light)",
                  color: geschlecht === val ? "white" : "var(--st-text-soft)",
                  border: geschlecht === val ? "1px solid var(--st-red)" : "1px solid var(--st-gray-border)",
                  transition:"all 0.12s",
                }}>{label}</button>
              ))}
              <div style={{display:"flex", alignItems:"center", gap:4, marginLeft:4}}>
                <span style={{fontSize:11, color:"var(--st-text-soft)"}}>Kürzel</span>
                <input
                  type="text"
                  value={kuerzel}
                  onChange={e => setKuerzel(e.target.value)}
                  placeholder="K."
                  maxLength={8}
                  style={{
                    width:48, padding:"3px 6px", fontSize:12, borderRadius:3,
                    border:"1px solid var(--st-gray-border)", background:"var(--st-bg)",
                    color:"var(--st-text)", fontFamily:"inherit",
                  }}
                />
              </div>
            </div>
            {busy
              ? <button className="btn-secondary" onClick={cancelRun}>✕ Abbrechen</button>
              : <button className="btn-primary" onClick={run} disabled={!audio && !txtFile && !text && !bullets}>Verlaufsnotiz generieren</button>
            }
          </div>

          <Output text={out} loading={busy}
            onCopy={() => { navigator.clipboard.writeText(out); toast("In Zwischenablage kopiert"); }}
            extraButtons={hasTranscript ? [
              { label: "Transkript ↓", onClick: () => downloadTranscript(lastJobId) }
            ] : []} />

          {out && (
            <div style={{marginTop:12, textAlign:"right"}}>
              <button className="btn-secondary" onClick={() => {
                setAudio(null); setTxtFile(null); setText(""); setBullets("");
                setStyle(null); setStyleText(""); setOut("");
                setLastJobId(null); setHasTranscript(false);
                toast("Formular zurückgesetzt");
              }}>+ Neue Verlaufsnotiz</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function P2({ toast, resumeJob, onResumed, model }) {
  const [selbst, setSelbst]       = useState(null);
  const [befunde, setBefunde]     = useState(null);
  const [audio, setAudio]         = useState(null);
  const [txtFile, setTxtFile]     = useState(null);
  const [text, setText]           = useState("");
  const [dx, setDx]               = useState([]);
  const [style, setStyle]         = useState(null);
  const [styleText, setStyleText] = useState("");
  const [prompt, setPrompt]       = useState(P_ANAMNESE);
  const [out, setOut]             = useState("");
  const [befundOut, setBefundOut] = useState("");
  const [akutOut, setAkutOut]     = useState("");
  const [akutantrag, setAkutantrag] = useState(false);
  const [tab, setTab]             = useState("Anamnese");
  const [lastJobId, setLastJobId] = useState(null);
  const [hasTranscript, setHasTranscript] = useState(false);
  const [busy, setBusy]           = useState(false);
  const [geschlecht, setGeschlecht] = useState("auto");
  const [kuerzel, setKuerzel]     = useState("");

  // Resume: laufenden Job nach Reload wieder aufnehmen
  useEffect(() => {
    if (!resumeJob || resumeJob.page !== "p2") return;
    setBusy(true);
    pollJob(resumeJob.jobId, 1200)
      .then(job => {
        if (!job) { setBusy(false); onResumed(); return; } // cancelled
        setOut(job.result_text || "");
        setBefundOut(job.befund_text || "");
        setAkutOut(job.akut_text || "");
        setLastJobId(resumeJob.jobId);
        setHasTranscript(job.has_transcript || false);
        onResumed();
      })
      .catch(e => { setOut("Fehler: " + friendlyError(e)); onResumed(); })
      .finally(() => setBusy(false));
  }, [resumeJob]);

  function cancelRun() {
    const jobId = loadActiveJob()?.jobId;
    if (jobId) {
      fetch(`${getApiBase()}/jobs/${jobId}`, { method: "DELETE" }).catch(() => {});
    }
    clearActiveJob();
    setBusy(false);
  }

  async function run() {
    setBusy(true);
    setLastJobId(null);
    setHasTranscript(false);
    setBefundOut("");
    setAkutOut("");
    const dxStr = dx.length ? dx.join(", ") : "noch nicht festgelegt";

    const k = kuerzel.trim().replace(/\.?$/, ".");
    const nameHinweis = kuerzel.trim()
      ? ` Verwende als Namenskürzel durchgehend "${k}" (z.B. "Frau ${k}", "Herr ${k}").`
      : "";
    const geschlechtHinweis = {
      "w":    `\n\nKLIENT-GESCHLECHT: weiblich – verwende durchgehend weibliche Formen.${nameHinweis}`,
      "m":    `\n\nKLIENT-GESCHLECHT: männlich – verwende durchgehend männliche Formen.${nameHinweis}`,
      "auto": `\n\nKLIENT-GESCHLECHT: Leite das Geschlecht aus den Unterlagen ab. Falls nicht erkennbar, neutrale Formen verwenden.${nameHinweis}`,
    }[geschlecht];

    const sys = prompt.replace("{diagnosen}", dxStr) + geschlechtHinweis;
    try {
      const result = await generate("anamnese", sys, "", {
        selbst:    selbst,
        vorbef:    befunde,
        audio:     audio,
        style:     style,
        styleText: styleText || null,
        bullets:   akutantrag ? "akutantrag" : (text || null),
        model:     model || null,
      }, "p2");
      setOut(result.text || "");
      setBefundOut(result.befundText || "");
      setAkutOut(result.akutText || "");
      setLastJobId(result.jobId);
      setHasTranscript(result.hasTranscript || false);
    }
    catch (e) { setOut("Fehler: " + friendlyError(e)); }
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

          <Card num="B" title="Aufnahmegespräch" badge="opt" open={false}>
            <InputTabs tabs={[
              { id:"audio", icon:"🎙", label:"Aufnahme" },
              { id:"file",  icon:"📄", label:"Datei"    },
              { id:"text",  icon:"✏️", label:"Text"     },
            ]}>
              {(activeTab) => (<>
                {activeTab === "audio" && (
                  <Dropzone label="Aufnahme hochladen" hint=".mp3  .m4a  .wav" accept="audio/*" icon="&#127897;" file={audio} onFile={setAudio} />
                )}
                {activeTab === "file" && (
                  <Dropzone label="Transkript-Datei hochladen" hint=".txt  .docx" accept=".txt,.docx" icon="&#128196;" file={txtFile} onFile={setTxtFile} />
                )}
                {activeTab === "text" && (
                  <textarea rows={5} placeholder="Gesprächsinhalt des Aufnahmegesprächs direkt einfügen ..." value={text} onChange={(e) => setText(e.target.value)} style={{marginTop:0}} />
                )}
              </>)}
            </InputTabs>
          </Card>

          <Card num="C" title="Diagnosen" badge="req">
            <label className="field-label">ICD-10 oder ICD-11 Diagnosen</label>
            <Tags list={dx} onChange={setDx} />
            <div className="field-note">Enter oder Komma zum Hinzufuegen — z.B. F32.1, F41.1, Z73.0</div>
          </Card>

          <Card num="D" title="Stilvorlage" badge="opt" open={false}>
            <InputTabs tabs={[
              { id:"file", icon:"📎", label:"Datei"   },
              { id:"text", icon:"✏️", label:"Text C&P" },
            ]}>
              {(activeTab) => (<>
                {activeTab === "file" && (<>
                  <Dropzone label="Beispieltext hochladen" hint="PDF, DOCX oder TXT" accept=".pdf,.docx,.txt" icon="&#128221;" file={style} onFile={setStyle} />
                  <div className="info-note" style={{marginTop:8}}>Schreibstil des hochgeladenen Textes wird übernommen.</div>
                </>)}
                {activeTab === "text" && (<>
                  <textarea rows={6} placeholder="Beispieldokumentation hier einfügen ..." value={styleText} onChange={(e) => setStyleText(e.target.value)} style={{marginTop:0}} />
                  <div className="field-note">Direkt eingefügter Beispieltext als Stilvorlage</div>
                </>)}
              </>)}
            </InputTabs>
          </Card>

          <Card num="E" title="Prompt anpassen" open={false}>
            <PromptEditor value={prompt} onChange={setPrompt} def={P_ANAMNESE} />
          </Card>

          <div className="action-bar">
            <div style={{display:"flex", alignItems:"center", gap:6, marginRight:"auto", flexWrap:"wrap"}}>
              <span style={{fontSize:11, fontWeight:600, color:"var(--st-text-soft)", textTransform:"uppercase", letterSpacing:"0.06em"}}>Klient</span>
              {[
                { val:"w", label:"♀ weiblich" },
                { val:"m", label:"♂ männlich" },
                { val:"auto", label:"Auto"    },
              ].map(({ val, label }) => (
                <button key={val} onClick={() => setGeschlecht(val)} style={{
                  padding:"4px 10px", borderRadius:3, cursor:"pointer",
                  fontSize:12, fontWeight: geschlecht === val ? 700 : 400,
                  background: geschlecht === val ? "var(--st-red)" : "var(--st-gray-light)",
                  color: geschlecht === val ? "white" : "var(--st-text-soft)",
                  border: geschlecht === val ? "1px solid var(--st-red)" : "1px solid var(--st-gray-border)",
                  transition:"all 0.12s",
                }}>{label}</button>
              ))}
              <div style={{display:"flex", alignItems:"center", gap:4, marginLeft:4}}>
                <span style={{fontSize:11, color:"var(--st-text-soft)"}}>Kürzel</span>
                <input type="text" value={kuerzel} onChange={e => setKuerzel(e.target.value)}
                  placeholder="K." maxLength={8} style={{
                    width:48, padding:"3px 6px", fontSize:12, borderRadius:3,
                    border:"1px solid var(--st-gray-border)", background:"var(--st-bg)",
                    color:"var(--st-text)", fontFamily:"inherit",
                  }} />
              </div>
            </div>
            {busy
              ? <button className="btn-secondary" onClick={cancelRun}>✕ Abbrechen</button>
              : <>
                  <label style={{display:"flex", alignItems:"center", gap:6, cursor:"pointer",
                    fontSize:12, color:"var(--st-text-soft)", marginRight:8}}>
                    <input type="checkbox" checked={akutantrag}
                      onChange={e => setAkutantrag(e.target.checked)} style={{cursor:"pointer"}} />
                    Akutantrag
                  </label>
                  <button className="btn-primary" onClick={run} disabled={!selbst}>Anamnese und Befund generieren</button>
                </>
            }
          </div>

          <Output text={tab === "Anamnese" ? out : tab === "Psych. Befund" ? befundOut : akutOut} loading={busy}
            tabs={akutOut ? ["Anamnese", "Psych. Befund", "Akutantrag"] : ["Anamnese", "Psych. Befund"]}
            activeTab={tab} onTab={setTab}
            onCopy={() => {
              const t = tab === "Anamnese" ? out : tab === "Psych. Befund" ? befundOut : akutOut;
              navigator.clipboard.writeText(t);
              toast("Kopiert");
            }}
            extraButtons={hasTranscript ? [
              { label: "Transkript ↓", onClick: () => downloadTranscript(lastJobId) }
            ] : []} />

          {(out || befundOut) && (
            <div style={{marginTop:12, textAlign:"right"}}>
              <button className="btn-secondary" onClick={() => {
                setSelbst(null); setBefunde(null); setAudio(null); setTxtFile(null);
                setText(""); setDx([]); setStyle(null); setStyleText("");
                setOut(""); setBefundOut(""); setAkutOut(""); setAkutantrag(false);
                setLastJobId(null); setHasTranscript(false);
                toast("Formular zurückgesetzt");
              }}>+ Neue Anamnese</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function P3({ toast, resumeJob, onResumed, model }) {
  const [antrag, setAntrag]       = useState(null);
  const [verlauf, setVerlauf]     = useState(null);
  const [style, setStyle]         = useState(null);
  const [styleText, setStyleText] = useState("");
  const [fokus, setFokus]         = useState("");
  const [out, setOut]             = useState("");
  const [lastJobId, setLastJobId] = useState(null);
  const [busy, setBusy]           = useState(false);

  // Resume: laufenden Job nach Reload wieder aufnehmen
  useEffect(() => {
    if (!resumeJob || resumeJob.page !== "p3") return;
    setBusy(true);
    pollJob(resumeJob.jobId, 1200)
      .then(job => {
        if (!job) { setBusy(false); onResumed(); return; } // cancelled
        setOut(job.result_text || "");
        setLastJobId(resumeJob.jobId);
        onResumed();
      })
      .catch(e => { setOut("Fehler: " + friendlyError(e)); onResumed(); })
      .finally(() => setBusy(false));
  }, [resumeJob]);

  function cancelRun() {
    // Server-seitigen Job abbrechen – pollJob stoppt bei status="cancelled"
    const jobId = loadActiveJob()?.jobId;
    if (jobId) {
      fetch(`${getApiBase()}/jobs/${jobId}`, { method: "DELETE" }).catch(() => {});
    }
    clearActiveJob();
    setBusy(false);
  }

  async function run() {
    setBusy(true);
    setOut("");
    setLastJobId(null);
    try {
      const result = await generate("verlaengerung", "", "", {
        selbst:    antrag,   // Antragsvorlage → Diagnosen/Anamnese
        vorbef:    verlauf,  // Verlaufsdokumentation
        style:     style,
        styleText: styleText || null,
        bullets:   fokus || null,
        model:     model || null,
      }, "p3");
      setOut(result.text || "");
      setLastJobId(result.jobId);
    }
    catch (e) { setOut("Fehler: " + friendlyError(e)); }
    setBusy(false);
  }

  return (
    <div>
      <div className="page-header">
        <div className="page-eyebrow">Workflow 3</div>
        <h2>Verlängerungsantrag</h2>
        <p>Befüllt vorhandene Antragsvorlagen aus der Verlaufsdokumentation</p>
      </div>
      <div className="page-body">
        <div className="workflow">
          <Card num="A" title="Verlaufsdokumentation" badge="req">
            <Dropzone label="Verlaufsdokumentation hochladen" hint=".pdf — alle Verlaufsnotizen des Aufenthalts" accept=".pdf" icon="&#128202;" file={verlauf} onFile={setVerlauf} />
            <div className="info-note" style={{marginTop:8}}>Alle Verlaufsnotizen des stationären Aufenthalts als PDF.</div>
          </Card>

          <Card num="B" title="Antragsvorlage" badge="opt" open={false}>
            <Dropzone label="Vorlage / Vorheriger Antrag hochladen" hint=".docx oder .pdf — Diagnosen und Anamnese werden entnommen" accept=".docx,.pdf" icon="&#128196;" file={antrag} onFile={setAntrag} />
            <div className="info-note" style={{marginTop:8}}>Diagnosen, Anamnese und Befund werden aus dieser Vorlage für den neuen Antrag übernommen.</div>
          </Card>

          <Card num="C" title="Stilvorlage" badge="opt" open={false}>
            <InputTabs tabs={[
              { id:"file", icon:"📎", label:"Datei"   },
              { id:"text", icon:"✏️", label:"Text C&P" },
            ]}>
              {(activeTab) => (<>
                {activeTab === "file" && (
                  <Dropzone label="Beispieltext hochladen" hint="PDF, DOCX oder TXT" accept=".pdf,.docx,.txt" icon="&#128221;" file={style} onFile={setStyle} />
                )}
                {activeTab === "text" && (<>
                  <textarea rows={5} placeholder="Beispiel-Verlängerungsantrag einfügen ..." value={styleText} onChange={(e) => setStyleText(e.target.value)} style={{marginTop:0}} />
                  <div className="field-note">Schreibstil des eingefügten Texts wird übernommen</div>
                </>)}
              </>)}
            </InputTabs>
          </Card>

          <Card num="D" title="Fokus-Themen" badge="opt" open={false}>
            <label className="field-label">Schwerpunkte für diesen Antrag</label>
            <textarea rows={4}
              placeholder={"Optionale Schwerpunkte, z.B.:\n– Wächteranteil Türsteher\n– Gruppenarbeit, soziale Integration\n– Entschluss zur räumlichen Trennung"}
              value={fokus}
              onChange={e => setFokus(e.target.value)}
            />
            <div className="field-note">Werden als Hinweis an das Modell weitergegeben – nur Themen die in der Verlaufsdoku belegt sind werden aufgegriffen.</div>
          </Card>

          <div className="action-bar">
            {busy
              ? <button className="btn-secondary" onClick={cancelRun}>✕ Abbrechen</button>
              : <button className="btn-primary" onClick={run} disabled={!verlauf}>Verlängerungsantrag erstellen</button>
            }
          </div>

          <Output text={out} loading={busy}
            onCopy={() => { navigator.clipboard.writeText(out); toast("Kopiert"); }} />

          {out && (
            <div style={{marginTop:12, textAlign:"right"}}>
              <button className="btn-secondary" onClick={() => {
                setVerlauf(null); setAntrag(null); setStyle(null); setStyleText("");
                setFokus(""); setOut(""); setLastJobId(null);
                toast("Formular zurückgesetzt");
              }}>+ Neuer Verlängerungsantrag</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function P4({ toast, resumeJob, onResumed, model }) {
  const [bericht, setBericht]     = useState(null);
  const [verlauf, setVerlauf]     = useState(null);
  const [style, setStyle]         = useState(null);
  const [styleText, setStyleText] = useState("");
  const [fokus, setFokus]         = useState("");
  const [out, setOut]             = useState("");
  const [lastJobId, setLastJobId] = useState(null);
  const [busy, setBusy]           = useState(false);

  // Resume: laufenden Job nach Reload wieder aufnehmen
  useEffect(() => {
    if (!resumeJob || resumeJob.page !== "p4") return;
    setBusy(true);
    pollJob(resumeJob.jobId, 1200)
      .then(job => {
        if (!job) { setBusy(false); onResumed(); return; } // cancelled
        setOut(job.result_text || "");
        setLastJobId(resumeJob.jobId);
        onResumed();
      })
      .catch(e => { setOut("Fehler: " + friendlyError(e)); onResumed(); })
      .finally(() => setBusy(false));
  }, [resumeJob]);

  function cancelRun() {
    // Server-seitigen Job abbrechen – pollJob stoppt bei status="cancelled"
    const jobId = loadActiveJob()?.jobId;
    if (jobId) {
      fetch(`${getApiBase()}/jobs/${jobId}`, { method: "DELETE" }).catch(() => {});
    }
    clearActiveJob();
    setBusy(false);
  }

  async function run() {
    setBusy(true);
    setOut("");
    setLastJobId(null);
    try {
      const result = await generate("entlassbericht", "", "", {
        selbst:    bericht,  // Vorbericht/Verlängerungsantrag → Diagnosen/Anamnese/Befund
        vorbef:    verlauf,  // Verlaufsdokumentation
        style:     style,
        styleText: styleText || null,
        bullets:   fokus || null,
        model:     model || null,
      }, "p4");
      setOut(result.text || "");
      setLastJobId(result.jobId);
    }
    catch (e) { setOut("Fehler: " + friendlyError(e)); }
    setBusy(false);
  }

  return (
    <div>
      <div className="page-header">
        <div className="page-eyebrow">Workflow 4</div>
        <h2>Entlassbericht</h2>
        <p>Synthetisiert alle Verlaufsnotizen zu einem vollständigen Entlassbericht</p>
      </div>
      <div className="page-body">
        <div className="workflow">
          <Card num="A" title="Verlaufsdokumentation" badge="req">
            <Dropzone label="Verlaufsdokumentation hochladen" hint=".pdf — gesamte Dokumentation des Aufenthalts" accept=".pdf" icon="&#128202;" file={verlauf} onFile={setVerlauf} />
            <div className="info-note" style={{marginTop:8}}>Alle Verlaufsnotizen des stationären Aufenthalts als PDF.</div>
          </Card>

          <Card num="B" title="Berichtsvorlage" badge="opt" open={false}>
            <Dropzone label="Vorlage hochladen" hint=".docx — Vorlage mit vorhandener Struktur" accept=".docx" icon="&#128196;" file={bericht} onFile={setBericht} />
            <div className="info-note" style={{marginTop:8}}>Optional: DOCX-Vorlage — Struktur wird übernommen und befüllt.</div>
          </Card>

          <Card num="C" title="Stilvorlage" badge="opt" open={false}>
            <InputTabs tabs={[
              { id:"file", icon:"📎", label:"Datei"   },
              { id:"text", icon:"✏️", label:"Text C&P" },
            ]}>
              {(activeTab) => (<>
                {activeTab === "file" && (
                  <Dropzone label="Beispieltext hochladen" hint="PDF, DOCX oder TXT" accept=".pdf,.docx,.txt" icon="&#128221;" file={style} onFile={setStyle} />
                )}
                {activeTab === "text" && (<>
                  <textarea rows={5} placeholder="Beispiel-Entlassbericht einfügen ..." value={styleText} onChange={(e) => setStyleText(e.target.value)} style={{marginTop:0}} />
                  <div className="field-note">Schreibstil des eingefügten Texts wird übernommen</div>
                </>)}
              </>)}
            </InputTabs>
          </Card>

          <Card num="D" title="Fokus-Themen" badge="opt" open={false}>
            <label className="field-label">Schwerpunkte für diesen Entlassbericht</label>
            <textarea rows={4}
              placeholder={"Optionale Schwerpunkte, z.B.:\n– Wächteranteil Türsteher, Arbeit mit inneren Anteilen\n– Gruppenarbeit und soziale Integration\n– Familien- und Paardynamik\n– Entschluss zur räumlichen Trennung"}
              value={fokus}
              onChange={e => setFokus(e.target.value)}
            />
            <div className="field-note">Werden als Hinweis an das Modell weitergegeben – nur Themen die in der Verlaufsdoku belegt sind werden aufgegriffen.</div>
          </Card>

          <div className="action-bar">
            {busy
              ? <button className="btn-secondary" onClick={cancelRun}>✕ Abbrechen</button>
              : <button className="btn-primary" onClick={run} disabled={!verlauf}>Entlassbericht erstellen</button>
            }
          </div>

          <Output text={out} loading={busy}
            onCopy={() => { navigator.clipboard.writeText(out); toast("Kopiert"); }} />

          {out && (
            <div style={{marginTop:12, textAlign:"right"}}>
              <button className="btn-secondary" onClick={() => {
                setVerlauf(null); setBericht(null); setStyle(null); setStyleText("");
                setFokus(""); setOut(""); setLastJobId(null);
                toast("Formular zurückgesetzt");
              }}>+ Neuer Entlassbericht</button>
            </div>
          )}
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
// API_BASE: dynamisch aus localStorage/window lesen damit URL-Änderungen
// sofort ohne Seitenneuladung greifen
function getApiBase() {
  // window.SYSTELIOS_API_BASE hat Vorrang (vom Confluence Macro gesetzt, immer verfügbar)
  const fromWindow = (typeof window !== "undefined" && window.SYSTELIOS_API_BASE) || "";
  // localStorage als Fallback – kann in Confluence-iframes blockiert sein
  let stored = "";
  try { stored = localStorage.getItem("systelios_backend_url") || ""; } catch (_) {}
  const raw = fromWindow || stored;
  if (!raw) return "http://localhost:8000/api";
  return raw.replace(/\/$/, "").replace(/\/api$/, "") + "/api";
}

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
  const [textInput, setTextInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [liste, setListe] = useState(null);
  const [ladebusy, setLadebusy] = useState(false);

  // Abschnitte die für Verlängerung/Entlassbericht relevant sind
  const ABSCHNITTE_HINWEIS = [
    "Aktuelle Anamnese",
    "Verlauf und Begründung der weiteren Verlängerung",
    "Problemrelevante Vorgeschichte",
    "Biographische Anamnese",
    "Psychotherapeutischer Verlauf",
  ];
  const hatAbschnitte = ["verlaengerung", "entlassbericht"].includes(dokumenttyp);

  // Bibliothek automatisch laden beim ersten Render
  const didMount = useRef(false);
  if (!didMount.current) {
    didMount.current = true;
    if (therapeutId) Promise.resolve().then(() => ladeListe());
  }

  async function hochladen() {
    const hasFile = !!file;
    const hasText = textInput.trim().length > 30;
    if (!therapeutId.trim() || (!hasFile && !hasText)) return;
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("therapeut_id", therapeutId.trim());
      fd.append("dokumenttyp",  dokumenttyp);
      fd.append("ist_statisch", istStatisch ? "true" : "false");
      if (hasText) {
        fd.append("text_content", textInput.trim());
      } else {
        fd.append("beispiel_file", file);
      }

      const r = await fetch(`${getApiBase()}/style/upload`, { method: "POST", body: fd });
      if (!r.ok) {
        const err = await r.json();
        throw new Error(err.detail || r.statusText);
      }
      const data = await r.json();
      const hinweis = hatAbschnitte ? " · nur relevante Abschnitte" : "";
      toast(`✓ Gespeichert: ${data.dokumenttyp_label} · ${data.word_count} Wörter${data.ist_statisch ? " · Anker" : ""}${hinweis}`);
      setFile(null);
      setTextInput("");
      await ladeListe();
    } catch (e) {
      toast("Fehler: " + friendlyError(e));
    }
    setBusy(false);
  }

  async function ladeListe() {
    if (!therapeutId.trim()) return;
    setLadebusy(true);
    try {
      const r = await fetch(`${getApiBase()}/style/${encodeURIComponent(therapeutId.trim())}`);
      if (!r.ok) throw new Error(r.statusText);
      setListe(await r.json());
    } catch (e) {
      toast("Fehler beim Laden: " + friendlyError(e));
    }
    setLadebusy(false);
  }

  async function loeschen(id) {
    try {
      const r = await fetch(`${getApiBase()}/style/embedding/${id}`, { method: "DELETE" });
      if (!r.ok) throw new Error((await r.json()).detail);
      toast("Beispiel gelöscht");
      await ladeListe();
    } catch (e) {
      toast("Fehler: " + friendlyError(e));
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

            <InputTabs tabs={[
              { id:"file", icon:"📄", label:"Datei" },
              { id:"text", icon:"✏️", label:"Text einfügen" },
            ]}>
              {(activeTab) => (<>
                {activeTab === "file" && (
                  <Dropzone
                    label="Beispieltext hochladen"
                    hint="PDF, DOCX oder TXT · typischer Text dieses Therapeuten"
                    accept=".pdf,.docx,.txt"
                    icon="📝"
                    file={file}
                    onFile={setFile}
                  />
                )}
                {activeTab === "text" && (
                  <textarea
                    rows={7}
                    placeholder={hatAbschnitte
                      ? "Relevante Abschnitte einfügen:\n• Aktuelle Anamnese\n• Verlauf und Begründung\n• Problemrelevante Vorgeschichte\n• Biographische Anamnese\n• Psychotherapeutischer Verlauf"
                      : "Beispieltext direkt einfügen – Gesprächsdokumentation oder Anamnese des Therapeuten ..."}
                    value={textInput}
                    onChange={e => setTextInput(e.target.value)}
                    style={{ marginTop: 0 }}
                  />
                )}
              </>)}
            </InputTabs>

            {hatAbschnitte && (
              <div className="info-note" style={{ marginTop: 8 }}>
                <strong>Hinweis:</strong> Für {dokumenttyp === "verlaengerung" ? "Verlängerungsanträge" : "Entlassberichte"} werden
                nur die therapeutenspezifischen Abschnitte als Stilvorlage verwendet:
                {" "}{ABSCHNITTE_HINWEIS.join(", ")}.
                Standardisierte Felder (Diagnosen, Medikation etc.) werden automatisch herausgefiltert.
              </div>
            )}

            <div className="info-note" style={{ marginTop: hatAbschnitte ? 6 : 10 }}>
              Der Text wird automatisch vektorisiert. Beim Generieren sucht das System
              die passendsten Beispiele heraus — kein manuelles Zuweisen nötig.
            </div>

            <div className="action-bar" style={{ marginTop: 14 }}>
              <button
                className="btn-primary"
                onClick={hochladen}
                disabled={busy || (!file && textInput.trim().length < 30) || !therapeutId.trim()}
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

// Style einmalig in <head> injizieren – vermeidet ungültiges HTML
// und Stacking-Context-Probleme durch <style> im div
function useHeadStyle(css) {
  useEffect(() => {
    const el = document.createElement("style");
    el.setAttribute("data-st", "systelios");
    el.textContent = css;
    // Eventuell vorhandenes altes Tag ersetzen
    const old = document.querySelector("style[data-st='systelios']");
    if (old) old.remove();
    document.head.appendChild(el);
    return () => el.remove();
  }, []);
}

const NAVS = [
  { id: "p1", n: "1", title: "Gesprächsdokumentation", sub: "Verlaufsnotiz" },
  { id: "p2", n: "2", title: "Anamnese & Befund",       sub: "Aufnahmegespräch" },
  { id: "p3", n: "3", title: "Verlängerungsantrag",     sub: "Kostenübernahme" },
  { id: "p4", n: "4", title: "Entlassbericht",          sub: "Abschlussbericht" },
  { id: "p5", n: "✦", title: "Stilprofil-Bibliothek",   sub: "Beispiele verwalten" },
];

export default function App() {
  useHeadStyle(S);
  const [page, setPage]       = useState("p1");
  const [msg, setMsg]         = useState(null);
  const [showSettings, setShowSettings] = useState(false);
  const [resumeJob, setResumeJob] = useState(null); // { jobId, page } falls ein Job wiederhergestellt wird
  const [selectedModel, setSelectedModel] = useState(() => {
    try { return localStorage.getItem("systelios_model") || ""; } catch (_) { return ""; }
  });
  const [backendUrl, setBackendUrl] = useState(() => {
    try { return localStorage.getItem("systelios_backend_url") || window.SYSTELIOS_API_BASE || ""; }
    catch (_) { return window.SYSTELIOS_API_BASE || ""; }
  });
  const [urlInput, setUrlInput] = useState(() => {
    try { return localStorage.getItem("systelios_backend_url") || window.SYSTELIOS_API_BASE || ""; }
    catch (_) { return window.SYSTELIOS_API_BASE || ""; }
  });

  // URL aus Confluence-Macro-Parameter (data-api am Container)?
  // Wenn gesetzt, brauchen Therapeuten den Settings-Dialog nicht.
  const [urlFromMacro] = useState(() => {
    try {
      const container = document.querySelector('[id^="systelios-root-"]');
      const macroApi = container?.dataset?.api?.trim();
      return !!macroApi;
    } catch (_) { return false; }
  });

  // Modell-Wahl persistent speichern
  function handleModelChange(m) {
    setSelectedModel(m);
    try { localStorage.setItem("systelios_model", m); } catch (_) {}
  }

  // Beim Start: prüfen ob ein laufender Job existiert
  useEffect(() => {
    const saved = loadActiveJob();
    if (!saved) return;

    // Job-Status vom Server prüfen
    fetch(`${getApiBase()}/jobs/${saved.jobId}`)
      .then(r => r.ok ? r.json() : null)
      .then(job => {
        if (!job) { clearActiveJob(); return; }
        if (job.status === "done" || job.status === "error") {
          clearActiveJob();
          return;
        }
        // Job läuft noch – zur richtigen Seite navigieren und Resume anzeigen
        if (saved.page) setPage(saved.page);
        setResumeJob(saved);
      })
      .catch(() => clearActiveJob());
  }, []);

  const saveUrl = () => {
    let url = urlInput.trim().replace(/\/+$/, ""); // trailing slash entfernen
    // https:// ergänzen falls kein Protokoll angegeben
    if (url && !url.startsWith("http://") && !url.startsWith("https://")) {
      url = "https://" + url;
    }
    try { localStorage.setItem("systelios_backend_url", url); } catch (_) {}
    window.SYSTELIOS_API_BASE = url;
    setBackendUrl(url);
    setUrlInput(url);
    setBackendOffline(false); // Reset – wird beim nächsten Health-Check aktualisiert
    setShowSettings(false);
    toast("Backend-URL gespeichert");
  };

  const toast = useCallback((t) => {
    setMsg(t);
    setTimeout(() => setMsg(null), 2400);
  }, []);

  // Beim ersten Start ohne URL: Settings automatisch öffnen
  // NICHT wenn die URL aus dem Confluence-Macro kommt (data-api)
  const firstRun = !backendUrl && !urlFromMacro;

  // Backend-Erreichbarkeit prüfen (alle 30 Sekunden)
  const [backendOffline, setBackendOffline] = useState(false);
  useEffect(() => {
    if (!backendUrl) return;
    let cancelled = false;
    const check = () => {
      fetch(`${getApiBase()}/health`, { signal: AbortSignal.timeout(5000) })
        .then(r => { if (!cancelled) setBackendOffline(!r.ok); })
        .catch(() => { if (!cancelled) setBackendOffline(true); });
    };
    check(); // sofort prüfen
    const interval = setInterval(check, 30000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [backendUrl]);

  return (
    <div id="st-root" style={{
      display:"flex",
      flexDirection:"row",
      minHeight:"600px",
      width:"100%",
      border:"1px solid rgba(0,0,0,0.08)",
      borderRadius:"8px"
    }}>

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
        {/* Resume-Banner wenn ein laufender Job nach Reload wiederhergestellt wird */}
        {resumeJob && (
          <div style={{
            background:"#fffbe6", borderBottom:"1px solid #f0d060",
            padding:"10px 24px", fontSize:13, color:"#7a6000",
            display:"flex", alignItems:"center", gap:10
          }}>
            <span style={{fontSize:16}}>⏳</span>
            <span>Job läuft noch – Ergebnis erscheint automatisch wenn fertig.</span>
            <button onClick={() => { clearActiveJob(); setResumeJob(null); }} style={{
              marginLeft:"auto", background:"none", border:"1px solid #c0a030",
              borderRadius:3, padding:"2px 10px", fontSize:12, cursor:"pointer", color:"#7a6000"
            }}>Abbrechen</button>
          </div>
        )}
        {page === "p1" && <P1 toast={toast} resumeJob={resumeJob} onResumed={() => setResumeJob(null)} model={selectedModel} />}
        {page === "p2" && <P2 toast={toast} resumeJob={resumeJob} onResumed={() => setResumeJob(null)} model={selectedModel} />}
        {page === "p3" && <P3 toast={toast} resumeJob={resumeJob} onResumed={() => setResumeJob(null)} model={selectedModel} />}
        {page === "p4" && <P4 toast={toast} resumeJob={resumeJob} onResumed={() => setResumeJob(null)} model={selectedModel} />}
        {page === "p5" && <P5 toast={toast} />}
      </main>

      {/* Settings Modal – via Portal damit position:fixed korrekt funktioniert */}
      {(showSettings || firstRun || backendOffline) && createPortal(
        <div style={{
          position:"fixed", inset:0, background:"rgba(0,0,0,0.55)",
          display:"flex", alignItems:"center", justifyContent:"center",
          zIndex:1000
        }} onClick={(e) => { if(e.target===e.currentTarget && !firstRun && !backendOffline) setShowSettings(false); }}>
          <div style={{
            background:"#fff", borderRadius:8, padding:"32px 28px", width:480,
            boxShadow:"0 8px 40px rgba(0,0,0,0.25)"
          }}>
            {/* Offline-Banner */}
            {backendOffline && !firstRun && (
              <div style={{
                background:"#fef2f2", border:"1px solid #fca5a5", borderRadius:6,
                padding:"12px 16px", marginBottom:20, display:"flex", alignItems:"flex-start", gap:10
              }}>
                <span style={{fontSize:20, lineHeight:1}}>⚠</span>
                <div>
                  <div style={{fontSize:14, fontWeight:600, color:"#991b1b", marginBottom:4}}>
                    Backend nicht erreichbar
                  </div>
                  <div style={{fontSize:12, color:"#7f1d1d", lineHeight:1.5}}>
                    Verbindung zu <code style={{background:"#fee2e2", padding:"1px 4px", borderRadius:3, fontSize:11}}>{backendUrl}</code> fehlgeschlagen.
                    Bitte prüfe ob der Server läuft oder ändere die URL.
                  </div>
                </div>
              </div>
            )}
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
              {urlFromMacro && (
                <span style={{color:"#16a34a", fontWeight:500}}>
                  ✓ URL wird über das Confluence-Macro konfiguriert.<br />
                </span>
              )}
              Testphase RunPod: <code style={{background:"#f0eeea",padding:"1px 5px",borderRadius:3}}>https://&lt;pod-id&gt;-8000.proxy.runpod.net</code><br />
              Produktion: <code style={{background:"#f0eeea",padding:"1px 5px",borderRadius:3}}>http://systelios-server:8000</code>
            </div>

            <div style={{marginBottom:8, fontSize:12, fontWeight:600, color:"#444", textTransform:"uppercase", letterSpacing:"0.06em"}}>
              Standard-Modell
            </div>
            <div style={{marginBottom:6}}>
              <ModelSelector model={selectedModel} onChange={handleModelChange} apiBase={getApiBase()} />
            </div>
            <div style={{fontSize:11, color:"#a0a49e", marginBottom:20, lineHeight:1.6}}>
              Gilt für alle Workflows (Gesprächsdokumentation, Anamnese, Verlängerung, Entlassbericht).<br />
              Reasoning-Modelle (z.B. deepseek-r1) für tiefe Hypothesenarbeit, Standard-Modelle schneller.
            </div>

            <div style={{display:"flex", gap:10, justifyContent:"flex-end"}}>
              {!firstRun && !backendOffline && (
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
      , document.body)}

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
