// ────────────────────────────────────────────────────────────────────────────
// src/qa.jsx — extrahiert aus klinische-dokumentation.jsx (R4, 2026-07-01).
// Chunk-Inhalte byte-identisch verschoben; nur Import/Export-Header sind neu.
// ────────────────────────────────────────────────────────────────────────────
import { useState, useEffect } from "react";
import { createPortal } from "react-dom";
import { fetchRepairResult, repairPreview, repairStart } from "./api.js";
import { friendlyError } from "./shared.js";
import { JobProgressBar } from "./ui.jsx";



// ── Sprint 4: interaktives QualityCheckPanel ───────────────────────
// data:           QC-Bundle (oder null - dann rendert nichts)
// acceptedCodes:  Array von codes die ausgewaehlt sind
// userHint:       Textarea-Wert
// onToggle:       fn(code) - Checkbox an/aus
// onHintChange:   fn(string) - Textarea-Onchange
// onRepair:       fn() - Submit-Button. Eltern-Komponente baut Preview-Call.
// repairBusy:     blockiert Buttons
// repairError:    Fehlermeldung
// readOnly:       wenn true, nur Anzeige (z.B. fuer Repair-QC nach Repair-Submit)
/**
 * v19.26b: Vorher/Nachher fuer automatische Umformulierungen (DIAGNOSE_ENTFERNT,
 * GRAMMAR_AUTOFIXED). code_detail.pairs = [{before, after, how}]; after == null
 * heisst "unveraendert belassen" (Pruefung nicht bestanden).
 */
const QC_PAIR_HOW = {
  llm: "Modell",
  fallback: "deterministisch (Klausel gestrichen)",
  herrn: "Deklination",
  klebebug: "Klebefehler",
};

function QcPairsDetail({ pairs }) {
  if (!Array.isArray(pairs) || pairs.length === 0) return null;
  return (
    <details className="qc-pairs">
      <summary>Vorher/Nachher anzeigen ({pairs.length})</summary>
      <ol className="qc-pairs-list">
        {pairs.map((p, i) => {
          const how = String(p.how || "");
          const label = QC_PAIR_HOW[how] || (how.startsWith("llm_abgelehnt") ? "Modellvorschlag abgelehnt" : how);
          return (
            <li key={i} className="qc-pair">
              <div className="qc-pair-before"><span className="qc-pair-tag">vorher</span><s>{p.before}</s></div>
              {p.after ? (
                <div className="qc-pair-after"><span className="qc-pair-tag">nachher</span>{p.after}</div>
              ) : (
                <div className="qc-pair-kept"><span className="qc-pair-tag">belassen</span>unverändert – Umformulierung nicht übernommen</div>
              )}
              {label && <div className="qc-pair-how">{label}</div>}
            </li>
          );
        })}
      </ol>
    </details>
  );
}

// ── v19.27 (D8): Fehlende Begriffe eines Stichpunkts / uebergangene Punkte
// (code_detail.fehlend bei MISSING_STICHPUNKT / STICHPUNKTE_IGNORIERT).
function QcFehlendDetail({ fehlend }) {
  if (!Array.isArray(fehlend) || fehlend.length === 0) return null;
  return (
    <ul className="qc-fehlend">
      {fehlend.map((f, i) => <li key={i}>{String(f)}</li>)}
    </ul>
  );
}

function QualityCheckPanel({
  data,
  acceptedCodes = [],
  userHint = "",
  onToggle = null,
  onHintChange = null,
  onRepair = null,
  repairBusy = false,
  repairError = null,
  readOnly = false,
  // v19.29: Item-Bezug (ISM): Issues mit code_detail.item_index werden
  // klickbar und rufen onFocusItem(index) auf.
  onFocusItem = null,
}) {
  if (!data || !Array.isArray(data.issues)) {
    return null;
  }
  const issues  = data.issues;
  const summary = data.summary || {};
  // v19.27 (D8): keine Beanstandungen -> Status statt leerem Panel.
  if (issues.length === 0) {
    const n = summary.checks_run;
    return (
      <div className="qc-panel qc-ok">
        <div className="qc-head">
          <span className="qc-title">Interne Qualitätsprüfung</span>
          <span className="qc-counts">
            <span className="qc-badge qc-ok">
              {n ? `alle ${n} Checks bestanden` : "keine Beanstandungen"}
            </span>
          </span>
        </div>
      </div>
    );
  }
  const critN   = summary.critical || 0;
  const warnN   = summary.warning  || 0;
  const infoN   = summary.info     || 0;
  const topSev = critN > 0 ? "critical" : warnN > 0 ? "warning" : "info";
  const interactive = !readOnly && (onToggle || onHintChange);
  const canSubmit = interactive && !repairBusy
    && (acceptedCodes.length > 0 || (userHint && userHint.trim().length > 0));

  return (
    <div className={"qc-panel qc-" + topSev}>
      <div className="qc-head">
        <span className="qc-title">Interne Qualitätsprüfung</span>
        <span className="qc-counts">
          {critN > 0 && <span className="qc-badge qc-critical">{critN} kritisch</span>}
          {warnN > 0 && <span className="qc-badge qc-warning">{warnN} Hinweis</span>}
          {infoN > 0 && <span className="qc-badge qc-info">{infoN} Info</span>}
        </span>
      </div>
      <ul className="qc-list">
        {issues.map((iss, idx) => {
          const checked = acceptedCodes.includes(iss.code);
          const sev = iss.severity || "info";
          return (
            <li key={iss.code || idx} className={"qc-item qc-" + sev}>
              {interactive && onToggle ? (
                <label className="qc-checkbox-label">
                  <input
                    type="checkbox"
                    className="qc-checkbox"
                    checked={checked}
                    disabled={repairBusy}
                    onChange={() => onToggle(iss.code)}
                  />
                  <span className="qc-marker" />
                </label>
              ) : (
                <span className="qc-marker" />
              )}
              <span className="qc-msg">
                {onFocusItem && iss.code_detail && Number.isInteger(iss.code_detail.item_index) ? (
                  <button
                    type="button"
                    className="qc-item-link"
                    title="Zum Item springen"
                    onClick={() => onFocusItem(iss.code_detail.item_index)}
                  >{iss.message}</button>
                ) : iss.message}
                <QcPairsDetail pairs={iss.code_detail && iss.code_detail.pairs} />
                <QcFehlendDetail fehlend={iss.code_detail && iss.code_detail.fehlend} />
              </span>
              <span className="qc-code" title={iss.repair_hint || ""}>{iss.code}</span>
            </li>
          );
        })}
      </ul>
      {interactive && (
        <div className="qc-repair-form">
          <label className="qc-hint-label">
            <span className="qc-hint-label-text">
              Zusätzlicher Hinweis (optional, max. 500 Zeichen):
            </span>
            <textarea
              className="qc-hint-textarea"
              value={userHint}
              maxLength={500}
              onChange={(e) => onHintChange && onHintChange(e.target.value)}
              disabled={repairBusy}
              placeholder="z.B. „Bitte empathischer formulieren und Behandlungsverlauf um Achtsamkeitsübungen ergänzen."
            />
            <span className="qc-hint-counter">{userHint.length}/500</span>
          </label>
          {repairError && (
            <div className="qc-repair-error">⚠️ {repairError}</div>
          )}
          <div className="qc-repair-actions">
            <button
              type="button"
              className="qc-repair-btn"
              disabled={!canSubmit}
              onClick={() => onRepair && onRepair()}
              title={
                !canSubmit
                  ? "Mindestens ein Hinweis auswählen oder Text eingeben"
                  : "Preview zur Bestätigung anzeigen"
              }
            >
              {repairBusy ? "Lädt..." : "Text überarbeiten lassen"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Sprint 4: RepairPreviewModal ────────────────────────────────────
// Zeigt den final_prompt vom Backend, erlaubt Editieren.
// onConfirm(promptOrNull): null = Original-Prompt verwenden
// onCancel():               Modal weg, kein Repair starten
function RepairPreviewModal({
  prompt = "",
  busy = false,
  error = null,
  onConfirm,
  onCancel,
}) {
  const [editedPrompt, setEditedPrompt] = useState(prompt);
  const [edited, setEdited] = useState(false);
  // Wenn der Prompt neu reinkommt (z.B. zweiter Preview-Aufruf), State angleichen
  useEffect(() => {
    setEditedPrompt(prompt);
    setEdited(false);
  }, [prompt]);

  const handleSubmit = () => {
    if (busy) return;
    onConfirm(edited ? editedPrompt : null);
  };

  return createPortal(
    <div className="st-scope">
    <div className="qc-modal-backdrop" onClick={!busy ? onCancel : undefined}>
      <div className="qc-modal" onClick={(e) => e.stopPropagation()}>
        <div className="qc-modal-head">
          <span className="qc-modal-title">Repair-Prompt prüfen</span>
          <button
            type="button"
            className="qc-modal-x"
            onClick={onCancel}
            disabled={busy}
            aria-label="Schließen"
          >&#215;</button>
        </div>
        <div className="qc-modal-body">
          <p className="qc-modal-hint">
            Folgender Prompt wird an das Modell gesendet. Du kannst ihn vor dem
            Versand bearbeiten – beachte dabei die Sicherheits-Marker
            (<code>&gt;&gt;&gt;ORIGINAL-TEXT&lt;&lt;&lt;</code>, <code>&gt;&gt;&gt;NUTZERHINWEIS&lt;&lt;&lt;</code>)
            nicht zu zerstören.
          </p>
          <textarea
            className="qc-modal-textarea"
            value={editedPrompt}
            onChange={(e) => { setEditedPrompt(e.target.value); setEdited(true); }}
            disabled={busy}
            spellCheck={false}
          />
          {error && <div className="qc-repair-error">⚠️ {error}</div>}
        </div>
        <div className="qc-modal-foot">
          <button
            type="button"
            className="btn-secondary"
            onClick={onCancel}
            disabled={busy}
          >Abbrechen</button>
          <button
            type="button"
            className="qc-repair-btn"
            onClick={handleSubmit}
            disabled={busy}
          >
            {busy ? "Generiere Überarbeitung..." : (edited ? "Eigenen Prompt senden" : "Bestätigen & Überarbeiten")}
          </button>
        </div>
      </div>
    </div>
    </div>,
    document.body,
  );
}

// ── Sprint 4: ResultVersionsTabs ────────────────────────────────────
// Schmaler Tab-Switch zwischen Original und letzter Überarbeitung.
// Wird ueber <Output> gerendert; bei nicht-Repair-Jobs nicht angezeigt.
function ResultVersionsTabs({ hasRepair, active, onChange, disabled = false }) {
  if (!hasRepair) return null;
  return (
    <div className="qc-versions-tabs">
      <button
        type="button"
        className={"qc-versions-tab" + (active === "original" ? " active" : "")}
        onClick={() => !disabled && onChange("original")}
        disabled={disabled}
      >Original</button>
      <button
        type="button"
        className={"qc-versions-tab" + (active === "repair" ? " active" : "")}
        onClick={() => !disabled && onChange("repair")}
        disabled={disabled}
      >Überarbeitet</button>
    </div>
  );
}

// ── Sprint 4: RepairBundle ──────────────────────────────────────────
// Kapselt die komplette Repair-UX (Panel + Modal + API-Handler).
// Pages rendern darunter eine einzige Zeile <RepairBundle job ops toast />.
function RepairBundle({ job, ops, toast }) {
  // Repair-Button im QC-Panel: erst Preview anfordern, dann Modal oeffnen
  async function handleTrigger() {
    if (!job.repairTargetJobId) {
      ops.setRepairError("Kein Original-Job - Repair nicht moeglich");
      return;
    }
    ops.setRepairBusy(true);
    ops.setRepairError(null);
    try {
      const preview = await repairPreview(
        job.repairTargetJobId, job.acceptedCodes, job.userHint,
      );
      ops.openModal(preview.final_prompt);
    } catch (e) {
      ops.setRepairError(friendlyError(e));
    } finally {
      ops.setRepairBusy(false);
    }
  }

  // Modal-Bestaetigung: optional customPrompt (wenn Therapeut Preview editiert hat)
  // v19.4 C-1: NICHT mehr blockierend pollen. Job nur starten, Modal sofort
  // schliessen, ein JobProgressBar uebernimmt den Fortschritt (analog zur
  // normalen Generierung). Das Ergebnis wird im Terminal-Handler geholt.
  async function handleConfirm(customPrompt) {
    ops.setRepairBusy(true);
    ops.setRepairError(null);
    try {
      const { repairJobId } = await repairStart(
        job.repairTargetJobId, job.acceptedCodes, job.userHint, customPrompt,
      );
      ops.setRepairProgressJobId(repairJobId);
      ops.closeModal();
    } catch (e) {
      ops.setRepairError(friendlyError(e));
    } finally {
      ops.setRepairBusy(false);
    }
  }

  // Terminal-Event des laufenden Repair-Jobs (vom JobProgressBar geliefert).
  async function handleRepairTerminal(type) {
    const repairJobId = job.repairProgressJobId;
    if (type !== "done") {
      ops.setRepairProgressJobId(null);
      if (type === "error") ops.setRepairError("Überarbeitung fehlgeschlagen");
      else                  toast && toast("Überarbeitung abgebrochen");
      return;
    }
    try {
      const result = await fetchRepairResult(repairJobId, job.repairTargetJobId);
      if (!result) {                      // cancelled zwischen done-Event und GET
        ops.setRepairProgressJobId(null);
        toast && toast("Überarbeitung abgebrochen");
        return;
      }
      ops.applyRepair(result);            // setzt repairProgressJobId selbst auf null
      toast && toast("Überarbeitung erstellt");
    } catch (e) {
      ops.setRepairProgressJobId(null);
      ops.setRepairError(friendlyError(e));
    }
  }

  return (
    <>
      <QualityCheckPanel
        data={job.qualityCheck}
        acceptedCodes={job.acceptedCodes}
        userHint={job.userHint}
        onToggle={ops.toggleCode}
        onHintChange={ops.setUserHint}
        onRepair={handleTrigger}
        repairBusy={job.repairBusy}
        repairError={job.repairError}
        // Wenn der User gerade die Repair-Version anschaut, wird das Panel
        // read-only - sonst koennte er ein zweites Repair auf die Repair-Version
        // triggern, was Plan-Phase-C ausschliesst.
        readOnly={job.activeVersion === "repair"}
      />
      {/* v19.4 C-1: laeuft nach Modal-Close — Fortschritt der Ueberarbeitung
          via SSE/Polling, analog zur normalen Generierung. */}
      {job.repairProgressJobId && (
        <div className="qc-repair-progress">
          <div className="qc-repair-progress-label">Überarbeitung läuft …</div>
          <JobProgressBar
            jobId={job.repairProgressJobId}
            onTerminal={handleRepairTerminal}
          />
        </div>
      )}
      {job.showRepairModal && (
        <RepairPreviewModal
          prompt={job.modalPrompt}
          busy={job.repairBusy}
          error={job.repairError}
          onConfirm={handleConfirm}
          onCancel={ops.closeModal}
        />
      )}
    </>
  );
}

export { QualityCheckPanel, QcPairsDetail, QcFehlendDetail, RepairPreviewModal, ResultVersionsTabs, RepairBundle };
