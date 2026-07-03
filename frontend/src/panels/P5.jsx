// ────────────────────────────────────────────────────────────────────────────
// src/panels/P5.jsx — extrahiert aus klinische-dokumentation.jsx (R4, 2026-07-01).
// Chunk-Inhalte byte-identisch verschoben; nur Import/Export-Header sind neu.
// ────────────────────────────────────────────────────────────────────────────
import { useState, useRef, useCallback, useEffect, useMemo } from "react";
import { apiFetch, getApiBase, getConfluenceUser } from "../api.jsx";
import { useWorkflowManifest } from "../hooks.jsx";
import { friendlyError } from "../shared.jsx";
import { Card, Dropzone, InputTabs } from "../ui.jsx";


function P5({ toast, liste, ladebusy, ladeListe, loeschen }) {
  const [therapeutId] = useState(getConfluenceUser);  // read-only aus Confluence
  const [dokumenttyp, setDokumenttyp] = useState("dokumentation");
  const [istStatisch, setIstStatisch] = useState(false);
  const [file, setFile] = useState(null);
  const [textInput, setTextInput] = useState("");
  const [busy, setBusy] = useState(false);

  // v13: Workflow-Manifest dynamisch vom Backend laden (mit Fallback).
  const { workflows: dokTypen, structural: structuralWfs } = useWorkflowManifest();

  // Abschnitte die für strukturelle Workflows relevant sind
  const ABSCHNITTE_HINWEIS = [
    "Aktuelle Anamnese",
    "Verlauf und Begründung der weiteren Verlängerung",
    "Problemrelevante Vorgeschichte",
    "Biographische Anamnese",
    "Psychotherapeutischer Verlauf",
  ];
  // v13: aus dem Manifest abgeleitet statt hardcoded ["verlaengerung", "entlassbericht"].
  // Aktuell zeigt der Hinweis sich fuer Verlaengerung + Entlassbericht (beide strukturell);
  // wenn Akutantrag/Folgeverlaengerung auch UI-relevante Abschnitte bekommen, einfach
  // is_structural=true im Backend lassen und filtern hier feiner.
  const hatAbschnitte = dokumenttyp === "verlaengerung" || dokumenttyp === "entlassbericht";

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

      const r = await apiFetch(`${getApiBase()}/style/upload`, { method: "POST", body: fd });
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

  // Gruppiere Liste nach Dokumenttyp (v13: dokTypen kommt aus useWorkflowManifest)
  const grouped = liste ? dokTypen.map(dt => ({
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
                  {dokTypen.map(dt => (
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

export { P5 };
