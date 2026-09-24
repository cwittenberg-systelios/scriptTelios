// ────────────────────────────────────────────────────────────────────────────
// src/interview-lease.js — Reservierung fuer laufende Interviews (v19.34).
//
// Solange ein Interview laeuft (eine GPU), starten neue Auftraege nicht. Das
// Backend verlaengert die Reservierung bei jedem Turn/Diktat selbst; hier
// wird sie freigegeben, sobald das Interview nicht mehr aktiv ist (fertig,
// verworfen, Doku erzeugt, Komponente weg, Tab geschlossen). Faellt die
// Freigabe aus, verfaellt sie serverseitig nach 5 min ohne Aktivitaet.
// ────────────────────────────────────────────────────────────────────────────
import { useEffect } from "react";
import { interviewLease } from "./api.js";

function useInterviewLease(sessionId, active) {
  useEffect(() => {
    if (!sessionId || !active) return undefined;
    const onUnload = () => { interviewLease(sessionId, "release"); };
    if (typeof window !== "undefined") window.addEventListener("beforeunload", onUnload);
    return () => {
      if (typeof window !== "undefined") window.removeEventListener("beforeunload", onUnload);
      interviewLease(sessionId, "release");
    };
  }, [sessionId, active]);
}

export { useInterviewLease };
