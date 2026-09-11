import { useState, useRef, useCallback, useEffect } from "react";
import { createPortal } from "react-dom";
import { apiFetch, getApiBase, getConfluenceUser , ensureServer, announceServerState, isServerDownError, SERVER_STATE_EVENT } from "./src/api.js";
import { P0 } from "./src/panels/P0.jsx";
import { P1 } from "./src/panels/P1.jsx";
import { P2, P2b } from "./src/panels/P2.jsx";
import { P3, P3b } from "./src/panels/P3.jsx";
import { P4 } from "./src/panels/P4.jsx";
import { P5 } from "./src/panels/P5.jsx";
import { P6 } from "./src/panels/P6.jsx";
import { clearActiveJob, friendlyError, loadActiveJob } from "./src/shared.js";
import { useDndGuard } from "./src/dnd-guard.jsx";
import { S, useHeadStyle } from "./src/styles.jsx";


const NAVS = [
  { id: "p0",  n: "⏺",  title: "Aufnahmen",              sub: "Aufzeichnungen verwalten" },
  { id: "p1",  n: "1",  title: "Gesprächsdokumentation", sub: "Verlaufsnotiz" },
  { id: "p2",  n: "2",  title: "Anamnese & Befund",      sub: "Aufnahmegespräch" },
  { id: "p2b", n: "2b", title: "Akutantrag",             sub: "Begründung Akutaufnahme" },
  { id: "p3",  n: "3",  title: "Verlängerungsantrag",    sub: "Kostenübernahme" },
  { id: "p3b", n: "3b", title: "Folgeverlängerung",      sub: "Anschluss-Verlängerung" },
  { id: "p4",  n: "4",  title: "Entlassbericht",         sub: "Abschlussbericht" },
  { id: "p6",  n: "6",  title: "ISM-Fragebogen",         sub: "SNS-Prozessmonitoring" },
  { id: "p5",  n: "✦",  title: "Stilprofil-Bibliothek",  sub: "Beispiele verwalten" },
];

export default function App() {
  useHeadStyle(S);
  useDndGuard(); // v19.7: blockiert Confluence-Attachment-Upload per DnD seitenweit
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

  // Backend-URL: Confluence-Makro (window.SYSTELIOS_API_BASE) oder aeltere
  // localStorage-Einstellung. Der Settings-Dialog hat seit v19 kein URL-Feld
  // mehr (saveUrl/urlInput in v19.21 als toter Code entfernt).
  const [backendUrl] = useState(() => {
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

  // v19.15 (Sprint E): Panelwechsel broadcasten. Keep-Mounted-Panels können
  // so beim Sichtbarwerden ihre Daten auffrischen (z.B. AudioInput-Aufnahmeliste),
  // ohne dass die App-Shell die Panel-Interna kennen muss.
  useEffect(() => {
    try {
      window.dispatchEvent(new CustomEvent("st-page-changed", { detail: page }));
    } catch (_) { /* ignorieren */ }
  }, [page]);



  const toast = useCallback((t, opts) => {
    setMsg(t);
    setTimeout(() => setMsg(null), (opts && opts.duration) || 2400);
  }, []);

  // Beim ersten Start ohne URL: Settings automatisch öffnen
  // NICHT wenn die URL aus dem Confluence-Macro kommt (data-api)
  const firstRun = !backendUrl && !urlFromMacro;

  // Backend-Status prüfen (alle 30 Sekunden) — nutzt /selfcheck statt nur
  // /health, damit die Sidebar nicht nur "erreichbar" sondern den echten
  // Aggregatzustand aller Subsysteme (ollama/models/db/disk/gpu) zeigen kann.
  //   serverStatus: null (noch unbekannt) | "ok" | "degraded" | "down"
  const [serverStatus, setServerStatus] = useState(null);
  // v19.21 (B6/B9): Lifecycle-Zustand der Statusbox — "stopped" (Pod aus,
  // wird beim ersten Auftrag gestartet), "starting" (Deploy laeuft),
  // "no_server" (10 min keine GPU), "blocked_night", "insecure" (kein https).
  const [lifecycle, setLifecycle] = useState(null);
  const insecure = (() => {
    try { return typeof window !== "undefined" && window.location.protocol !== "https:" && !/^(localhost|127\.0\.0\.1)$/.test(window.location.hostname); }
    catch (_) { return false; }
  })();
  const wasOfflineRef = useRef(false);
  const startingSinceRef = useRef(0);
  // Start-on-Intent-Ereignisse aus api.js/P0 (ensureServer-Antworten)
  useEffect(() => {
    const onState = (e) => {
      const st = e.detail && e.detail.status;
      if (st === "starting") { startingSinceRef.current = Date.now(); setLifecycle("starting"); }
      else if (st === "no_server") setLifecycle("no_server");
      else if (st === "blocked_night") setLifecycle("blocked_night");
    };
    window.addEventListener(SERVER_STATE_EVENT, onState);
    return () => window.removeEventListener(SERVER_STATE_EVENT, onState);
  }, []);
  useEffect(() => {
    if (!backendUrl || insecure) return;
    let cancelled = false;
    const check = () => {
      apiFetch(`${getApiBase()}/selfcheck`, { signal: AbortSignal.timeout(8000) })
        .then(async r => {
          if (cancelled) return;
          if (!r.ok) {
            // Cloudflare-Origin-Fehler (Tunnel down) = Pod aus, kein Serverfehler.
            setServerStatus(isServerDownError(r) ? "stopped" : "down");
            wasOfflineRef.current = true;
            return;
          }
          let status;
          try {
            const data = await r.json();
            status = (data && data.status) || "down";
          } catch { status = "down"; }
          setServerStatus(status);
          const offline = status === "down";
          // Bestehende Listener (z.B. Recording-Reload) erwarten st-health-ok
          // beim Übergang von offline -> wieder erreichbar. Beibehalten.
          if (!offline && wasOfflineRef.current) {
            window.dispatchEvent(new CustomEvent("st-health-ok"));
          }
          if (!offline) { setLifecycle(null); startingSinceRef.current = 0; }
          wasOfflineRef.current = offline;
        })
        .catch((e) => {
          if (cancelled) return;
          // Netzwerkfehler = Tunnel/Pod aus → "stopped" (kein Fehler, nur aus)
          setServerStatus(isServerDownError(e) ? "stopped" : "down");
          wasOfflineRef.current = true;
        });
    };
    check();
    // Waehrend eines Starts haeufiger pollen (10 s), sonst 30 s.
    const interval = setInterval(() => {
      const starting = startingSinceRef.current && (Date.now() - startingSinceRef.current) < 10 * 60 * 1000;
      if (starting || (Date.now() % 30000) < 10000) check();
    }, 10000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [backendUrl, insecure]);

  // Rückwärtskompatibler Alias: mehrere Stellen (Overlay-Schließen etc.)
  // fragen weiterhin "ist der Server offline?".
  const backendOffline = serverStatus === "down" || serverStatus === "stopped";
  // Effektiver Anzeige-Zustand der Box
  const boxState = insecure ? "insecure"
    : (serverStatus === "stopped" || serverStatus === "down") && lifecycle ? lifecycle
    : serverStatus;
  const retryStart = async () => {
    const ens = await ensureServer();
    announceServerState(ens);
  };

  return (
    <div id="st-root" className="st-scope" style={{
      display:"flex",
      flexDirection:"row",
      minHeight:"600px",
      width:"100%",
      border:"1px solid rgba(0,0,0,0.08)",
      borderRadius:"8px"
    }}>

      <div className="sidebar">
        <div className="sidebar-section-label">KI-Dokumentation</div>

        {/* Server-Statusnotiz — oberste Stelle der Seitenleiste.
            Quelle: /selfcheck-Aggregat (serverStatus). Drei Zustaende:
              ok       -> gruen  ("Server bereit — alle Systeme aktiv")
              degraded -> gelb   (erreichbar, aber Modelle/Disk nicht ok)
              down     -> rot    ("Server nicht erreichbar")
            null (erster Check ausstehend) -> kein Kasten. */}
        {serverStatus === "ok" && (
          <div style={{
            background:"rgba(46,125,50,0.28)", border:"1px solid rgba(76,175,80,0.55)",
            borderRadius:4, padding:"8px 10px", marginBottom:10,
            fontSize:11, color:"rgba(200,240,200,0.95)", lineHeight:1.5
          }}>
            ● Server bereit — alle Systeme aktiv
          </div>
        )}
        {serverStatus === "degraded" && (
          <div style={{
            background:"rgba(180,130,20,0.28)", border:"1px solid rgba(230,170,40,0.55)",
            borderRadius:4, padding:"8px 10px", marginBottom:10,
            fontSize:11, color:"rgba(245,225,170,0.95)", lineHeight:1.5
          }}>
            ● Server l&#228;uft — einzelne Dienste eingeschr&#228;nkt
          </div>
        )}
        {boxState === "down" && (
          <div style={{
            background:"rgba(168,40,30,0.3)", border:"1px solid rgba(168,40,30,0.6)",
            borderRadius:4, padding:"8px 10px", marginBottom:10,
            fontSize:11, color:"rgba(255,200,200,0.9)", lineHeight:1.5
          }}>
            ⚠ Server läuft, antwortet aber fehlerhaft
          </div>
        )}
        {/* v19.21 (B6): Lifecycle-Zustaende — Ruhezustand grau und kompakt, nur echte Probleme rot */}
        {boxState === "stopped" && (
          <div style={{ padding:"4px 2px", marginBottom:10, fontSize:11, color:"rgba(255,255,255,0.5)", lineHeight:1.5 }}>
            ○ Kein Server aktiv — wird beim ersten Auftrag automatisch gestartet (ca. 3–6 min)
          </div>
        )}
        {boxState === "starting" && (
          <div style={{
            background:"rgba(30,90,160,0.3)", border:"1px solid rgba(60,130,220,0.6)",
            borderRadius:4, padding:"8px 10px", marginBottom:10,
            fontSize:11, color:"rgba(200,225,255,0.95)", lineHeight:1.5
          }}>
            ◐ Server startet … Auftrag startet automatisch, sobald der Server bereit ist. Seite nicht neu laden.
          </div>
        )}
        {boxState === "no_server" && (
          <div style={{
            background:"rgba(168,40,30,0.3)", border:"1px solid rgba(168,40,30,0.6)",
            borderRadius:4, padding:"8px 10px", marginBottom:10,
            fontSize:11, color:"rgba(255,200,200,0.9)", lineHeight:1.5
          }}>
            ⚠ Kein Server verfügbar — 10 min lang keine GPU frei.
            <button onClick={retryStart} style={{marginTop:6,display:"block",padding:"3px 8px",fontSize:11,cursor:"pointer"}}>Erneut versuchen</button>
          </div>
        )}
        {boxState === "blocked_night" && (
          <div style={{ padding:"4px 2px", marginBottom:10, fontSize:11, color:"rgba(255,255,255,0.5)", lineHeight:1.5 }}>
            ○ Kein Server aktiv — Auto-Start erst ab 5 Uhr
          </div>
        )}
        {boxState === "insecure" && (
          <div style={{
            background:"rgba(168,40,30,0.3)", border:"1px solid rgba(168,40,30,0.6)",
            borderRadius:4, padding:"8px 10px", marginBottom:10,
            fontSize:11, color:"rgba(255,200,200,0.9)", lineHeight:1.5
          }}>
            ⚠ Keine sichere Verbindung — bitte die Seite über https aufrufen
          </div>
        )}

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
          {/* AI-Act-Transparenzhinweis (Art. 50 Abs. 1): permanenter Systemhinweis.
              Bewusst KEINE Kennzeichnung pro Dokument — Krankenakte/Kassenantraege sind
              keine Veroeffentlichung i.S.v. Art. 50 Abs. 4; fachliche Pruefung erfolgt
              stets durch die behandelnde Person (Nachweis via job_id in prompts.log). */}
          <div style={{
            background:"rgba(255,255,255,0.06)", border:"1px solid rgba(255,255,255,0.12)",
            borderRadius:4, padding:"8px 10px", marginBottom:8,
            fontSize:10.5, color:"rgba(255,255,255,0.55)", lineHeight:1.5
          }}>
            KI-Entw&#252;rfe — fachliche Pr&#252;fung erforderlich.
          </div>
          <div style={{fontSize:11,color:"rgba(255,255,255,0.35)",lineHeight:1.6,marginBottom:10}}>
            scriptTelios · v0.1 · sysTelios Klinik f&#252;r Psychosomatik und Psychotherapie
          </div>
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
        {/* Sprint B2 (S1): Keep-Mounted. Alle Panels bleiben dauerhaft
            gemountet, inaktive werden nur per display:none versteckt. Damit
            ueberlebt SAEMTLICHER Panel-State (Textfelder, File-Uploads in
            Dropzones, Output, Repair-State, Scroll) den Tab-Wechsel - vorher
            fuehrte das conditional Rendering zum Unmount + Totalverlust.
            F5-Persistenz fuer Textfelder liefert zusaetzlich useDraftCache
            in den Panels. Mount-Nebeneffekte geprueft: getUserMedia nur
            on-click, P0-Polling nur bei pending Items. */}
        <div style={{display: page === "p0"  ? "" : "none"}}><P0 toast={toast} /></div>
        <div style={{display: page === "p1"  ? "" : "none"}}><P1  toast={toast} resumeJob={resumeJob} onResumed={() => setResumeJob(null)} /></div>
        <div style={{display: page === "p2"  ? "" : "none"}}><P2  toast={toast} resumeJob={resumeJob} onResumed={() => setResumeJob(null)} /></div>
        <div style={{display: page === "p2b" ? "" : "none"}}><P2b toast={toast} resumeJob={resumeJob} onResumed={() => setResumeJob(null)} /></div>
        <div style={{display: page === "p3"  ? "" : "none"}}><P3  toast={toast} resumeJob={resumeJob} onResumed={() => setResumeJob(null)} /></div>
        <div style={{display: page === "p3b" ? "" : "none"}}><P3b toast={toast} resumeJob={resumeJob} onResumed={() => setResumeJob(null)} /></div>
        <div style={{display: page === "p4"  ? "" : "none"}}><P4  toast={toast} resumeJob={resumeJob} onResumed={() => setResumeJob(null)} /></div>
        <div style={{display: page === "p6" ? "" : "none"}}><P6 toast={toast} resumeJob={resumeJob} onResumed={() => setResumeJob(null)} /></div>
        <div style={{display: page === "p5" ? "" : "none"}}><P5
          toast={toast}
          liste={stilListe}
          ladebusy={stilLadebusy}
          ladeListe={ladeStilListe}
          loeschen={async (id) => {
            try { await loeschenStil(id); toast("Beispiel gelöscht"); }
            catch (e) { toast("Fehler: " + friendlyError(e)); }
          }}
        /></div>
      </main>

      {/* Settings Modal – via Portal damit position:fixed korrekt funktioniert */}
      {/* Kein .st-scope-Wrapper: das Settings-Modal nutzt ausschliesslich
          Inline-Styles und behaelt bewusst die Confluence-Schrift. */}
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
        <div className="st-scope">
          <div className="toast">
            <span className="toast-dot" />
            {msg}
          </div>
        </div>,
        document.body
      )}
    </div>
  );
}

// Mount: siehe main.jsx (einziger Einstiegspunkt, v19.21 S3).

