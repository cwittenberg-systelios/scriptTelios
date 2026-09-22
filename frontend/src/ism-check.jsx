/**
 * scriptTelios Frontend – v19.29: Live-QC fuer den ISM-Fragebogen (P6).
 *
 * useIsmLiveCheck(initialQc)
 *   qc            aktueller QC-Stand (serialize_issues-Format) oder null
 *   setQc         Initialwert setzen (z.B. quality_check des Jobs)
 *   check(ism)    POST /api/ism/check auf dem editierten Stand; laufende
 *                 Requests werden abgebrochen. Trigger laut D4=B: Blur eines
 *                 Feldes, Item hinzufuegen/loeschen, vor dem Export – nicht
 *                 bei jedem Tastendruck.
 *   busy          Request laeuft
 *
 * issuesForItem(qc, index) – Issues mit code_detail.item_index === index.
 * exportNeedsConfirm(qc)    – true bei critical/warning (D3=A).
 */
import { useCallback, useRef, useState } from "react";
import { apiFetch, getApiBase } from "./api.js";

export function issuesForItem(qc, index) {
  if (!qc || !Array.isArray(qc.issues)) return [];
  return qc.issues.filter(
    (i) => i && i.code_detail && Number.isInteger(i.code_detail.item_index)
      && i.code_detail.item_index === index,
  );
}

export function exportNeedsConfirm(qc) {
  const s = (qc && qc.summary) || {};
  return (s.critical || 0) + (s.warning || 0) > 0;
}

export function confirmExportText(qc) {
  const s = (qc && qc.summary) || {};
  const parts = [];
  if (s.critical) parts.push(`${s.critical} kritisch`);
  if (s.warning) parts.push(`${s.warning} Hinweis${s.warning === 1 ? "" : "e"}`);
  return `Qualitätsprüfung: ${parts.join(", ")} offen.\n\nTrotzdem exportieren?`;
}

export function useIsmLiveCheck(initialQc = null) {
  const [qc, setQc] = useState(initialQc);
  const [busy, setBusy] = useState(false);
  const ctrlRef = useRef(null);

  const check = useCallback(async (ism) => {
    if (!ism) return null;
    if (ctrlRef.current) ctrlRef.current.abort();
    const ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
    ctrlRef.current = ctrl;
    setBusy(true);
    try {
      const r = await apiFetch(`${getApiBase()}/ism/check`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fragebogen: ism }),
        signal: ctrl ? ctrl.signal : undefined,
      });
      if (!r.ok) return null;
      const data = await r.json();
      if (ctrlRef.current !== ctrl) return null;   // von neuerem Request ueberholt
      setQc(data);
      return data;
    } catch (e) {
      if (e && e.name === "AbortError") return null;
      return null;   // Live-Check ist Komfort – nie blockierend
    } finally {
      if (ctrlRef.current === ctrl) { ctrlRef.current = null; setBusy(false); }
    }
  }, []);

  return { qc, setQc, check, busy };
}
