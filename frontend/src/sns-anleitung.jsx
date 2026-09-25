// ────────────────────────────────────────────────────────────────────────────
// src/sns-anleitung.jsx — v19.41: Export-Anleitungen fuer P7 (SNS-Verlauf).
//
// Kurzform als Textzeile, ausfuehrlich als schematisierte SNS-Bildschirme
// (inline SVG, keine Bilddateien): pro Schritt ein Mini-Screen, das anzu-
// klickende Element rot umrandet und nummeriert. Klickwege nach den SNS-
// Screenshots vom 25.09.2026 (systelios.sns-live.de).
// ────────────────────────────────────────────────────────────────────────────

const C = {
  top: "#2b2b2b", side: "#eef1f5", sideTxt: "#3a4a5c", activeBg: "#1f4f86", activeTxt: "#ffffff",
  page: "#ffffff", line: "#d5dbe3", txt: "#2b2b2b", soft: "#7a8594", blue: "#1f4f86",
  link: "#2a64a8", mark: "#971321", orange: "#e0622d", head: "#e9eef4",
};
const W = 270;
const H = 150;
const SIDE = 80;

// Nummerierter Klickmarker (rote Umrandung + Badge)
function Mark({ x, y, w, h, n, r = 3 }) {
  return (
    <g>
      <rect x={x - 2} y={y - 2} width={w + 4} height={h + 4} rx={r + 2} fill="none" stroke={C.mark} strokeWidth="2" />
      <circle cx={x + w + 1} cy={y - 1} r="7.5" fill={C.mark} />
      <text x={x + w + 1} y={y + 2.5} fontSize="10" fontWeight="700" fill="#fff" textAnchor="middle">{n}</text>
    </g>
  );
}

// Grundgeruest eines SNS-Bildschirms: dunkle Kopfleiste, Menue links, Inhalt rechts.
// nav: [{ label, sub, active, mark }]
function Screen({ nav, children, title }) {
  let y = 34;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title}
         style={{ width: "100%", height: "auto", display: "block", borderRadius: 4, border: `1px solid ${C.line}` }}>
      <rect x="0" y="0" width={W} height={H} fill={C.page} />
      <rect x="0" y="0" width={W} height="20" fill={C.top} />
      <rect x="7" y="6" width="10" height="1.6" fill="#fff" /><rect x="7" y="9.4" width="10" height="1.6" fill="#fff" />
      <rect x="7" y="12.8" width="10" height="1.6" fill="#fff" />
      <text x={W - 8} y="13.5" fontSize="7" fill="#fff" textAnchor="end">SNS</text>
      <rect x="0" y="20" width={SIDE} height={H - 20} fill={C.side} />
      {nav.map((it, i) => {
        const h = it.sub ? 13 : 16;
        const top = y;
        y += h;
        const x = it.sub ? 16 : 8;
        return (
          <g key={i}>
            {it.active && <rect x="0" y={top} width={SIDE} height={h} fill={C.activeBg} />}
            <text x={x} y={top + h / 2 + 3} fontSize={it.sub ? 7.5 : 8.5} fontWeight={it.sub ? 400 : 600}
                  fill={it.active ? C.activeTxt : C.sideTxt}>{it.label}{it.open ? "  ▾" : ""}</text>
            {it.mark && <Mark x={1} y={top + 1} w={SIDE - 12} h={h - 2} n={it.mark} r={1} />}
          </g>
        );
      })}
      <g transform={`translate(${SIDE + 10} 28)`}>{children}</g>
    </svg>
  );
}

const CW = W - SIDE - 20;   // Breite des Inhaltsbereichs

function Suche({ y = 22, text, mark }) {
  return (
    <g>
      <rect x={CW - 110} y={y} width="110" height="16" rx="8" fill="#fff" stroke={C.blue} strokeWidth="1" />
      <text x={CW - 102} y={y + 11} fontSize="7.5" fill={C.txt}>{text}</text>
      <text x={CW - 12} y={y + 11.5} fontSize="8" fill={C.blue}>⌕</text>
      {mark && <Mark x={CW - 110} y={y} w={110} h={16} n={mark} r={6} />}
    </g>
  );
}

function Titel({ text, size = 14 }) {
  return <text x="0" y="14" fontSize={size} fontWeight="600" fill={C.txt}>{text}</text>;
}

function Tabelle({ y, kopf, zeilen, markZeile, markNr }) {
  return (
    <g>
      <rect x="0" y={y} width={CW} height="14" fill={C.head} />
      <text x="5" y={y + 10} fontSize="6.5" fontWeight="700" fill={C.blue}>{kopf}</text>
      {zeilen.map((z, i) => {
        const yy = y + 14 + i * 16;
        const hit = i === markZeile;
        return (
          <g key={i}>
            <line x1="0" x2={CW} y1={yy + 16} y2={yy + 16} stroke={C.line} strokeWidth="0.8" />
            <text x="5" y={yy + 11} fontSize="7.5" fill={hit ? C.link : C.soft} fontWeight={hit ? 600 : 400}>{z}</text>
            {hit && <Mark x={2} y={yy + 2} w={Math.min(CW - 20, z.length * 4.2 + 8)} h={12} n={markNr} r={2} />}
          </g>
        );
      })}
    </g>
  );
}

function Knopf({ x, y, w, text, filled = false, mark }) {
  return (
    <g>
      <rect x={x} y={y} width={w} height="15" rx="7.5" fill={filled ? C.blue : "#fff"} stroke={C.blue} strokeWidth="1" />
      <text x={x + w / 2} y={y + 10.3} fontSize="6.8" fontWeight="600" fill={filled ? "#fff" : C.blue} textAnchor="middle">{text}</text>
      {mark && <Mark x={x} y={y} w={w} h={15} n={mark} r={6} />}
    </g>
  );
}

const NAV_KLIENTEN = [
  { label: "Übersicht" }, { label: "Diagramme" }, { label: "Nutzer", open: true },
  { label: "Klienten", sub: true, active: true, mark: 1 }, { label: "Professional", sub: true },
  { label: "Nutzerarchiv", sub: true }, { label: "Fragebögen" },
];
const NAV_KLIENTEN_PLAIN = NAV_KLIENTEN.map((n) => ({ ...n, mark: undefined }));
const NAV_EDITOR = [
  { label: "Übersicht" }, { label: "Diagramme" }, { label: "Nutzer" }, { label: "Fragebögen", open: true },
  { label: "Fragebogen Editor", sub: true, active: true, mark: 1 }, { label: "Schedules", sub: true },
  { label: "Meine Fragebögen", sub: true },
];
const NAV_EDITOR_PLAIN = NAV_EDITOR.map((n) => ({ ...n, mark: undefined }));

// ── Userexport (.xlsx) ───────────────────────────────────────────────────────
const SCHRITTE_USEREXPORT = [
  {
    text: "Nutzer › Klienten öffnen, Kennung ins Suchfeld",
    bild: (
      <Screen title="Nutzer, Klienten, Suche" nav={NAV_KLIENTEN}>
        <Titel text="Nutzer" />
        <Suche text="WJ28718IND" mark={2} />
        <text x="0" y="52" fontSize="6.5" fill={C.soft}>◉ Alle Klienten</text>
      </Screen>
    ),
  },
  {
    text: "Auf den Nutzernamen klicken",
    bild: (
      <Screen title="Klient:in öffnen" nav={NAV_KLIENTEN_PLAIN}>
        <Titel text="Nutzer" />
        <Suche text="WJ28718IND" />
        <Tabelle y={48} kopf="NUTZERNAME" zeilen={["WJ28718IND", "…"]} markZeile={0} markNr={3} />
      </Screen>
    ),
  },
  {
    text: "Pfeil ▾ am Export-Knopf → „Export XLS“",
    bild: (
      <Screen title="Export XLS" nav={NAV_KLIENTEN_PLAIN}>
        <Titel text="WJ28718IND" size={11} />
        <Knopf x={CW - 64} y={20} w={58} text="EXPORT  ▾" filled mark={4} />
        <rect x={CW - 130} y="40" width="110" height="54" fill="#fff" stroke={C.line} />
        <rect x={CW - 130} y="40" width="110" height="26" fill="#f3f5f8" />
        <text x={CW - 125} y="51" fontSize="8.5" fontWeight="600" fill={C.txt}>Export XLS</text>
        <text x={CW - 125} y="61" fontSize="6" fill={C.soft}>Alle Daten dieses Users</text>
        <Mark x={CW - 130} y={40} w={110} h={26} n={5} r={1} />
        <text x={CW - 125} y="78" fontSize="7.5" fill={C.soft}>Compliance Report</text>
        <line x1={CW - 126} x2={CW - 58} y1="75.5" y2="75.5" stroke={C.mark} strokeWidth="1" />
        <text x={CW - 125} y="88" fontSize="6" fill={C.mark}>nicht dieser</text>
      </Screen>
    ),
  },
];

// ── Fragebogen-XML ───────────────────────────────────────────────────────────
const SCHRITTE_XML = [
  {
    text: "Fragebögen › Fragebogen Editor, Kennung ins Suchfeld",
    bild: (
      <Screen title="Fragebogen Editor, Suche" nav={NAV_EDITOR}>
        <Titel text="Fragebögen" />
        <Suche text="WJ28718IND" mark={2} />
        <text x="0" y="52" fontSize="6.5" fill={C.soft}>Filter: ◉ Alle</text>
      </Screen>
    ),
  },
  {
    text: "Individuellen Bogen der Klient:in anklicken",
    bild: (
      <Screen title="Individuellen Bogen öffnen" nav={NAV_EDITOR_PLAIN}>
        <Titel text="Fragebögen" />
        <Suche text="WJ28718IND" />
        <Tabelle y={48} kopf="NAME"
                 zeilen={["WJ28718IND_individueller Frage…", "…"]} markZeile={0} markNr={3} />
      </Screen>
    ),
  },
  {
    text: "„Export im XML-Format“ klicken",
    bild: (
      <Screen title="Export im XML-Format" nav={NAV_EDITOR_PLAIN}>
        <text x="0" y="11" fontSize="8" fontWeight="600" fill={C.txt}>WJ28718IND_indiv. Fragebogen</text>
        <Knopf x={0} y={22} w={92} text="EXPORT IM XML-FORMAT" filled mark={4} />
        <Knopf x={98} y={22} w={44} text="IMPORT." />
        <rect x="0" y="48" width={CW} height="20" fill="#fff" stroke={C.line} />
        <rect x="0" y="48" width="14" height="20" fill={C.orange} />
        <text x="4.5" y="61" fontSize="9" fontWeight="700" fill="#fff">!</text>
        <text x="19" y="57" fontSize="5.8" fill={C.txt}>Dieser Fragebogen wird bereits verwendet …</text>
        <text x="19" y="65" fontSize="5.8" fill={C.soft}>→ normal, Export funktioniert trotzdem</text>
        <rect x="0" y="76" width="36" height="13" fill={C.blue} />
        <text x="18" y="85" fontSize="6" fill="#fff" textAnchor="middle">GENERAL</text>
        <text x="44" y="85" fontSize="6" fill={C.link}>FAKTOREN   ITEMS   VORSCHAU</text>
      </Screen>
    ),
  },
];

function Schritte({ schritte }) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginTop: 8 }}>
      {schritte.map((s, i) => (
        <figure key={i} style={{ flex: "1 1 220px", maxWidth: 340, margin: 0 }}>
          {s.bild}
          <figcaption style={{ fontSize: 11.5, marginTop: 4, color: "var(--st-text)" }}>{s.text}</figcaption>
        </figure>
      ))}
    </div>
  );
}

function ExportAnleitung({ kurz, schritte, hinweis }) {
  return (
    <div className="info-note" style={{ marginTop: 8 }}>
      <div><b>So geht&apos;s:</b> {kurz}</div>
      <details style={{ marginTop: 4 }}>
        <summary style={{ cursor: "pointer", fontSize: 12, color: "var(--st-text-soft)" }}>Schritt für Schritt (Grafik)</summary>
        <Schritte schritte={schritte} />
        {hinweis && <div style={{ marginTop: 6, fontSize: 11.5 }}>{hinweis}</div>}
      </details>
    </div>
  );
}

const ANLEITUNG_USEREXPORT = {
  kurz: <>SNS → <b>Nutzer › Klienten</b> → Kennung suchen und öffnen → <b>Export ▾ › Export XLS</b></>,
  schritte: SCHRITTE_USEREXPORT,
  hinweis: <>Entlassene Klient:innen: Filter „Alle Klienten“. Datei: <code>SNS_User_&lt;Kennung&gt;_Export-….xlsx</code></>,
};

const ANLEITUNG_XML = {
  kurz: <>SNS → <b>Fragebögen › Fragebogen Editor</b> → Kennung suchen → individuellen Bogen öffnen → <b>Export im XML-Format</b></>,
  schritte: SCHRITTE_XML,
  hinweis: <>Immer das XML aus SNS nehmen, nicht die Datei aus Workflow 6. Bei mehreren Versionen („(2)“) den zugewiesenen Bogen wählen.</>,
};

export { ExportAnleitung, ANLEITUNG_USEREXPORT, ANLEITUNG_XML };
