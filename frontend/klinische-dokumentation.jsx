import { useState, useRef, useCallback, useEffect, useMemo } from "react";
import { createPortal } from "react-dom";
import { apiFetch, getApiBase, getConfluenceUser } from "./src/api.jsx";
import { P0 } from "./src/panels/P0.jsx";
import { P1 } from "./src/panels/P1.jsx";
import { P2, P2b } from "./src/panels/P2.jsx";
import { P3, P3b } from "./src/panels/P3.jsx";
import { P4 } from "./src/panels/P4.jsx";
import { P5 } from "./src/panels/P5.jsx";
import { clearActiveJob, friendlyError, loadActiveJob } from "./src/shared.jsx";
import { S, useHeadStyle } from "./src/styles.jsx";


const NAVS = [
  { id: "p0",  n: "⏺",  title: "Aufnahmen",              sub: "Aufzeichnungen verwalten" },
  { id: "p1",  n: "1",  title: "Gesprächsdokumentation", sub: "Verlaufsnotiz" },
  { id: "p2",  n: "2",  title: "Anamnese & Befund",      sub: "Aufnahmegespräch" },
  { id: "p2b", n: "2b", title: "Akutantrag",             sub: "Begründung Akutaufnahme" },
  { id: "p3",  n: "3",  title: "Verlängerungsantrag",    sub: "Kostenübernahme" },
  { id: "p3b", n: "3b", title: "Folgeverlängerung",      sub: "Anschluss-Verlängerung" },
  { id: "p4",  n: "4",  title: "Entlassbericht",         sub: "Abschlussbericht" },
  { id: "p5",  n: "✦",  title: "Stilprofil-Bibliothek",  sub: "Beispiele verwalten" },
];

export default function App() {
  useHeadStyle(S);
  const [page, setPage]       = useState("p0");
  const [msg, setMsg]         = useState(null);
  const [showSettings, setShowSettings] = useState(false);
  const [resumeJob, setResumeJob] = useState(null); // { jobId, page } falls ein Job wiederhergestellt wird

  // v19.5.3: Kein globales "Standard-Modell" mehr. Die Modellwahl erfolgt pro
  // Call im JobModelPicker des jeweiligen Workflows; ohne Wahl nimmt das Backend
  // den Workflow-Default (model_for_workflow). Es gibt daher keinen
  // selectedModel-State/localStorage-Wert mehr, der in die Calls fliesst.

  // Stilbibliothek – State im Root, damit Daten beim Tab-Öffnen bereits vorliegen
  const [stilListe, setStilListe]       = useState(null);
  const [stilLadebusy, setStilLadebusy] = useState(false);

  const ladeStilListe = useCallback(async () => {
    const tid = getConfluenceUser();
    if (!tid.trim()) return;
    setStilLadebusy(true);
    try {
      const r = await apiFetch(`${getApiBase()}/style/${encodeURIComponent(tid.trim())}`);
      if (r.ok) setStilListe(await r.json());
    } catch (_) {}
    setStilLadebusy(false);
  }, []);

  const loeschenStil = useCallback(async (id) => {
    const r = await apiFetch(`${getApiBase()}/style/embedding/${id}`, { method: "DELETE" });
    if (!r.ok) throw new Error((await r.json()).detail);
    await ladeStilListe();
  }, [ladeStilListe]);

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

  // v19.5.3: Einmalige Bereinigung eines evtl. noch in localStorage
  // persistierten (veralteten) Modellnamens aus dem entfernten globalen
  // ModelSelector. Ein dort verbliebenes 'qwen3:32b' wurde bisher als
  // Job-Modell mitgeschickt -> Ollama-404 ('model qwen3:32b not found').
  useEffect(() => {
    try { localStorage.removeItem("systelios_model"); } catch (_) {}
  }, []);

  // Stilbibliothek beim Start vorladen (parallel, kein Warten auf Tab-Öffnen)
  useEffect(() => {
    ladeStilListe();
  }, [ladeStilListe]);

  // Beim Start: prüfen ob ein laufender Job existiert
  useEffect(() => {
    const saved = loadActiveJob();
    if (!saved) return;

    // Job-Status vom Server prüfen
    apiFetch(`${getApiBase()}/jobs/${saved.jobId}`)
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

  // 1c: Globales st-nav Event — ermöglicht AudioInput und andere Komponenten
  // ohne prop-drilling zu P0 zu navigieren (z.B. "Neue Aufnahme in P0"-Link)
  useEffect(() => {
    const handler = (e) => {
      if (e.detail) setPage(e.detail);
    };
    window.addEventListener("st-nav", handler);
    return () => window.removeEventListener("st-nav", handler);
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

  const toast = useCallback((t, opts) => {
    setMsg(t);
    setTimeout(() => setMsg(null), (opts && opts.duration) || 2400);
  }, []);

  // Beim ersten Start ohne URL: Settings automatisch öffnen
  // NICHT wenn die URL aus dem Confluence-Macro kommt (data-api)
  const firstRun = !backendUrl && !urlFromMacro;

  // Backend-Erreichbarkeit prüfen (alle 30 Sekunden)
  const [backendOffline, setBackendOffline] = useState(false);
  const wasOfflineRef = useRef(false);
  useEffect(() => {
    if (!backendUrl) return;
    let cancelled = false;
    const check = () => {
      apiFetch(`${getApiBase()}/health`, { signal: AbortSignal.timeout(5000) })
        .then(r => {
          if (cancelled) return;
          const offline = !r.ok;
          setBackendOffline(offline);
          if (!offline && wasOfflineRef.current) {
            window.dispatchEvent(new CustomEvent("st-health-ok"));
          }
          wasOfflineRef.current = offline;
        })
        .catch(() => { if (!cancelled) { setBackendOffline(true); wasOfflineRef.current = true; } });
    };
    check();
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
          {backendOffline && (
            <div style={{
              background:"rgba(168,40,30,0.3)", border:"1px solid rgba(168,40,30,0.6)",
              borderRadius:4, padding:"8px 10px", marginBottom:8,
              fontSize:11, color:"rgba(255,200,200,0.9)", lineHeight:1.5
            }}>
              ⚠ Server nicht erreichbar
            </div>
          )}
          <button
            onClick={() => setShowSettings(true)}
            style={{
              display:"flex", alignItems:"center", gap:7,
              background:"rgba(255,255,255,0.08)",
              border:"1px solid rgba(255,255,255,0.15)",
              borderRadius:4, padding:"6px 12px", cursor:"pointer",
              color:"rgba(255,255,255,0.75)", fontSize:11, fontWeight:600,
              width:"100%", letterSpacing:"0.04em"
            }}
          >
            <span style={{fontSize:14}}>⚙</span>
            Einstellungen
          </button>
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
        {page === "p0"  && <P0 toast={toast} />}
        {page === "p1"  && <P1  toast={toast} resumeJob={resumeJob} onResumed={() => setResumeJob(null)} />}
        {page === "p2"  && <P2  toast={toast} resumeJob={resumeJob} onResumed={() => setResumeJob(null)} />}
        {page === "p2b" && <P2b toast={toast} resumeJob={resumeJob} onResumed={() => setResumeJob(null)} />}
        {page === "p3"  && <P3  toast={toast} resumeJob={resumeJob} onResumed={() => setResumeJob(null)} />}
        {page === "p3b" && <P3b toast={toast} resumeJob={resumeJob} onResumed={() => setResumeJob(null)} />}
        {page === "p4"  && <P4  toast={toast} resumeJob={resumeJob} onResumed={() => setResumeJob(null)} />}
        {page === "p5" && <P5
          toast={toast}
          liste={stilListe}
          ladebusy={stilLadebusy}
          ladeListe={ladeStilListe}
          loeschen={async (id) => {
            try { await loeschenStil(id); toast("Beispiel gelöscht"); }
            catch (e) { toast("Fehler: " + friendlyError(e)); }
          }}
        />}
      </main>

      {/* Settings Modal – via Portal damit position:fixed korrekt funktioniert */}
      {showSettings && createPortal(
        <div style={{
          position:"fixed", inset:0, background:"rgba(0,0,0,0.55)",
          display:"flex", alignItems:"center", justifyContent:"center",
          zIndex:1000
        }} onClick={(e) => { if(e.target===e.currentTarget && !firstRun && !backendOffline) setShowSettings(false); }}>
          <div style={{
            background:"#fff", borderRadius:8, padding:"32px 28px", width:420,
            boxShadow:"0 8px 40px rgba(0,0,0,0.25)"
          }}>
            <div style={{marginBottom:20}}>
              <div style={{fontSize:18, fontWeight:700, color:"#2c2c2c", marginBottom:6}}>
                ⚙ Einstellungen
              </div>
            </div>

            <div style={{fontSize:12, color:"#666", marginBottom:20, lineHeight:1.6}}>
              Die Modellwahl erfolgt jetzt pro Workflow direkt im jeweiligen
              Formular (Karte „Prompt &amp; Modellauswahl"). Ein globales
              Standard-Modell gibt es nicht mehr — ohne explizite Wahl nutzt der
              Server das für den Workflow hinterlegte Modell.
            </div>

            <div style={{display:"flex", gap:10, justifyContent:"flex-end"}}>
              <button onClick={() => setShowSettings(false)} style={{
                padding:"8px 20px", borderRadius:4, border:"1px solid #ccc",
                background:"#fff", cursor:"pointer", fontSize:13, color:"#666"
              }}>
                Schließen
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

