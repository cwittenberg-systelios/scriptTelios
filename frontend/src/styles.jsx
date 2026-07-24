// ────────────────────────────────────────────────────────────────────────────
// src/styles.jsx — extrahiert aus klinische-dokumentation.jsx (R4, 2026-07-01).
// Chunk-Inhalte byte-identisch verschoben; nur Import/Export-Header sind neu.
// ────────────────────────────────────────────────────────────────────────────
import { useState, useRef, useCallback, useEffect, useMemo } from "react";


const S = `
  /* Scoped auf .st-scope (App-Root + Portal-Wrapper) – kein Leak in Confluence */
  .st-scope, .st-scope *, .st-scope *::before, .st-scope *::after {
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

  .st-scope {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
    color: var(--st-text);
    font-size: 15px;
    line-height: 1.6;
    font-weight: 400;
  }

  /* App-Chrome nur am App-Root, nicht an den Portal-Wrappern */
  #st-root { background: var(--st-cream); }

  /* ── SIDEBAR ── */
  .st-scope .sidebar {
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

  .st-scope .sidebar-section-label {
    font-size: 10px; font-weight: 700;
    letter-spacing: 0.16em; text-transform: uppercase;
    color: rgba(255,255,255,0.28);
    padding: 20px 16px 8px;
  }

  .st-scope .nav-item {
    display: flex; align-items: stretch;
    cursor: pointer;
    border-left: 3px solid transparent;
    transition: background 0.15s;
    position: relative;
  }
  .st-scope .nav-item:hover { background: rgba(255,255,255,0.06); }
  .st-scope .nav-item.active {
    background: rgba(255,255,255,0.10);
    border-left-color: var(--st-red);
  }
  .st-scope .nav-item-inner {
    display: flex; align-items: flex-start; gap: 12px;
    padding: 12px 16px;
    flex: 1;
  }
  .st-scope .nav-step-num {
    width: 24px; height: 24px; border-radius: 50%;
    border: 1px solid rgba(255,255,255,0.22);
    color: rgba(255,255,255,0.45);
    font-size: 11px; font-weight: 700;
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0; margin-top: 1px;
    transition: all 0.15s;
  }
  .st-scope .nav-item.active .nav-step-num {
    background: var(--st-red);
    border-color: var(--st-red);
    color: white;
  }
  .st-scope .nav-item-title {
    font-size: 13px; font-weight: 400;
    color: rgba(255,255,255,0.72);
    line-height: 1.4; margin-bottom: 2px;
  }
  .st-scope .nav-item.active .nav-item-title { color: #fff; font-weight: 600; }
  .st-scope .nav-item-sub {
    font-size: 11px; font-weight: 300;
    color: rgba(255,255,255,0.35);
    line-height: 1.3;
  }

  .st-scope .sidebar-footer {
    margin-top: auto;
    padding: 16px;
    border-top: 1px solid rgba(255,255,255,0.08);
    font-size: 10px; font-weight: 300;
    color: rgba(255,255,255,0.25);
    line-height: 1.6;
    letter-spacing: 0.02em;
  }

  /* ── MAIN ── */
  .st-scope .main { flex: 1; min-height: 100vh; overflow-x: hidden; border-radius: 0 8px 8px 0; }

  .st-scope .page-header {
    background: var(--st-sidebar);
    padding: 24px 32px 20px;
  }
  .st-scope .page-eyebrow {
    font-size: 10px; font-weight: 700;
    letter-spacing: 0.18em; text-transform: uppercase;
    color: var(--st-red); margin-bottom: 6px;
    display: flex; align-items: center; gap: 8px;
  }
  .st-scope .page-eyebrow::after {
    content: ''; flex: 0 0 20px; height: 1px;
    background: var(--st-red); opacity: 0.6;
  }
  .st-scope .page-header h2 {
    font-size: 22px; font-weight: 600;
    color: #fff; line-height: 1.25;
    letter-spacing: -0.01em; margin: 0;
  }
  .st-scope .page-header p {
    font-size: 13px; color: rgba(255,255,255,0.45);
    margin-top: 4px; font-weight: 300;
    font-style: italic; margin-bottom: 0;
  }

  .st-scope .page-body { padding: 24px 32px 40px; max-width: 900px; }

  /* ── STEP CARDS ── */
  .st-scope .workflow { display: flex; flex-direction: column; gap: 10px; }

  .st-scope .step-card {
    background: var(--st-white);
    border: 1px solid var(--st-gray-mid);
    border-radius: 5px;
    overflow: hidden;
    margin: 0;
  }
  .st-scope .step-head {
    display: flex; align-items: center; gap: 12px;
    padding: 14px 20px;
    cursor: pointer; user-select: none;
    border-bottom: 1px solid transparent;
    transition: background 0.15s;
  }
  .st-scope .step-head:hover { background: var(--st-gray-light); }
  .st-scope .step-head.open { border-bottom-color: var(--st-gray-mid); }

  .st-scope .step-num {
    width: 28px; height: 28px; border-radius: 50%;
    background: var(--st-red);
    color: white; font-size: 12px; font-weight: 700;
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0; margin: 0;
  }
  .st-scope .step-label {
    flex: 1; font-size: 14px; font-weight: 600;
    color: var(--st-text); margin: 0;
  }
  .st-scope .step-pill {
    font-size: 10px; font-weight: 700;
    letter-spacing: 0.08em; text-transform: uppercase;
    padding: 3px 9px; border-radius: 20px;
  }
  .st-scope .pill-opt { background: var(--st-gray-light); color: var(--st-text-soft); }
  .st-scope .pill-req { background: rgba(139,26,26,0.10); color: var(--st-red-mid); }
  .st-scope .step-caret {
    font-size: 11px; color: var(--st-text-pale);
    transition: transform 0.18s; display: inline-block;
  }
  .st-scope .step-caret.open { transform: rotate(180deg); }
  .st-scope .step-body { padding: 20px 24px 24px; display: flex; flex-direction: column; gap: 16px; }

  /* ── UPLOAD ── */
  .st-scope .upload-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }

  /* ── InputTabs ──────────────────────────────────────────────── */
  .st-scope .input-tabs-wrap { display: flex; flex-direction: column; gap: 0; }
  .st-scope .input-tabs-bar {
    display: flex; gap: 2px; border-bottom: 2px solid var(--st-gray-border);
    margin-bottom: 12px;
  }
  .st-scope .input-tab {
    display: flex; align-items: center; gap: 5px;
    padding: 6px 14px; border: none; background: none; cursor: pointer;
    font-size: 12px; font-weight: 500; color: var(--st-text-soft);
    border-bottom: 2px solid transparent; margin-bottom: -2px;
    border-radius: 3px 3px 0 0; transition: all 0.12s;
  }
  .st-scope .input-tab:hover { color: var(--st-text); background: var(--st-gray-light); }
  .st-scope .input-tab.active {
    color: var(--st-red); border-bottom-color: var(--st-red);
    font-weight: 600; background: none;
  }
  .st-scope .input-tab-icon { font-size: 13px; }
  .st-scope .input-tabs-body { min-height: 80px; }
  .st-scope .upload-col-label {
    font-size: 11px; font-weight: 600;
    letter-spacing: 0.06em; text-transform: uppercase;
    color: var(--st-text-soft); margin-bottom: 8px;
  }

  /* ── Audio-Rekorder ────────────────────────────────────────── */
  .st-scope .audio-input-wrap { display: flex; flex-direction: column; gap: 12px; }
  .st-scope .audio-mode-toggle { display: flex; gap: 6px; font-size: 11px; }
  .st-scope .audio-mode-btn {
    flex: 1; padding: 6px 10px; border: 1px solid var(--st-gray-mid);
    background: #fff; border-radius: 4px; cursor: pointer; font-weight: 500;
    color: var(--st-text-soft); transition: all 0.12s;
  }
  .st-scope .audio-mode-btn.active {
    border-color: var(--st-red); background: var(--st-red-pale);
    color: var(--st-red); font-weight: 600;
  }
  .st-scope .audio-mode-btn:hover:not(.active) { background: var(--st-gray-light); }
  .st-scope .p0-picker { margin-top: 8px; }
  .st-scope .p0-picker-list {
    max-height: 220px;  /* ~5 Items à ~40px sichtbar, Rest per Scrollbar */
    overflow-y: auto;
    padding-right: 4px;  /* etwas Platz für Scrollbar */
  }
  .st-scope .p0-hint { padding: 12px; color: var(--fg-muted); font-size: 13px; text-align: center; }
  .st-scope .p0-picker-item {
    display: flex; justify-content: space-between; align-items: center;
    padding: 10px 12px; margin-bottom: 4px; border-radius: 5px;
    border: 1px solid var(--border); cursor: pointer; font-size: 13px;
    background: var(--card-bg);
  }
  .st-scope .p0-picker-item:hover { background: var(--st-gray-light); border-color: var(--st-red); }
  .st-scope .p0-picker-label { font-weight: 600; }
  .st-scope .p0-picker-meta { color: var(--fg-muted); font-size: 11px; }
  .st-scope .recorder-box {
    border: 2px solid var(--st-gray-mid); border-radius: 5px;
    padding: 18px; text-align: center; background: var(--st-cream);
    min-height: 110px; display: flex; flex-direction: column;
    align-items: center; justify-content: center; gap: 10px;
  }
  .st-scope .recorder-box.recording { border-color: var(--st-red); background: #fff5f5; }
  .st-scope .rec-status { font-size: 13px; font-weight: 600; color: var(--st-text-mid); }
  .st-scope .rec-status.active { color: var(--st-red); }
  .st-scope .rec-timer {
    font-family: ui-monospace, monospace; font-size: 22px;
    font-weight: 600; color: var(--st-text);
  }
  .st-scope .rec-dot {
    display: inline-block; width: 8px; height: 8px; border-radius: 50%;
    background: var(--st-red); margin-right: 6px; vertical-align: middle;
    animation: rec-pulse 1.2s ease-in-out infinite;
  }
  @keyframes rec-pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }
  .st-scope .rec-buttons { display: flex; gap: 8px; justify-content: center; flex-wrap: wrap; }
  .st-scope .rec-btn {
    padding: 7px 16px; font-size: 12px; font-weight: 600;
    border-radius: 4px; cursor: pointer; border: 1px solid;
    transition: all 0.12s;
  }
  .st-scope .rec-btn-start { background: var(--st-red); color: #fff; border-color: var(--st-red); }
  .st-scope .rec-btn-start:hover { background: var(--st-red-dark, #8a0f0f); }
  .st-scope .rec-btn-stop  { background: #fff; color: var(--st-red); border-color: var(--st-red); }
  .st-scope .rec-btn-stop:hover { background: var(--st-red-pale); }
  .st-scope .rec-btn-pause { background: #fff; color: var(--st-text); border-color: var(--st-gray-mid); }
  .st-scope .rec-btn-pause:hover { background: var(--st-gray-light); }
  .st-scope .rec-info { font-size: 11px; color: var(--st-text-pale); margin-top: 4px; }
  .st-scope .rec-meter-wrap { width: 100%; max-width: 260px; margin: 4px auto; }
  .st-scope .rec-meter-bar {
    height: 6px; background: var(--st-gray-bg); border-radius: 3px;
    overflow: hidden; position: relative;
  }
  .st-scope .rec-meter-fill {
    height: 100%; border-radius: 3px; transition: width 80ms linear;
    background: linear-gradient(90deg, var(--st-green, #22c55e) 0%, #facc15 60%, var(--st-red) 90%);
  }
  .st-scope .rec-gain-wrap {
    display: flex; align-items: center; gap: 6px; width: 100%;
    max-width: 220px; margin: 2px auto;
  }
  .st-scope .rec-gain-label { font-size: 10px; color: var(--st-text-pale); white-space: nowrap; }
  .st-scope .rec-gain-slider {
    -webkit-appearance: none; appearance: none; flex: 1;
    height: 4px; background: var(--st-gray-mid); border-radius: 2px;
    outline: none; cursor: pointer;
  }
  .st-scope .rec-gain-slider::-webkit-slider-thumb {
    -webkit-appearance: none; width: 14px; height: 14px;
    border-radius: 50%; background: var(--st-red); cursor: pointer;
    border: 2px solid #fff; box-shadow: 0 1px 3px rgba(0,0,0,0.2);
  }
  .st-scope .rec-gain-slider::-moz-range-thumb {
    width: 14px; height: 14px; border-radius: 50%;
    background: var(--st-red); cursor: pointer;
    border: 2px solid #fff; box-shadow: 0 1px 3px rgba(0,0,0,0.2);
  }
  .st-scope .upload-warn {
    padding: 8px 12px; background: #fef3c7; border: 1px solid #fbbf24;
    border-radius: 4px; color: #92400e; font-size: 12px; line-height: 1.5;
  }

  .st-scope .dropzone {
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
  .st-scope .dropzone:hover { border-color: var(--st-red); background: var(--st-red-pale); }
  .st-scope .dropzone.drag { border-color: var(--st-red-mid); background: var(--st-red-pale); }
  .st-scope .dropzone.filled { border-style: solid; border-color: var(--st-red); background: var(--st-red-pale); }
  .st-scope .dropzone input[type=file] { position: absolute; inset: 0; opacity: 0; cursor: pointer; width: 100%; height: 100%; }
  .st-scope .dz-icon { font-size: 24px; margin-bottom: 8px; opacity: 0.45; }
  .st-scope .dz-label { font-size: 13px; font-weight: 600; color: var(--st-text-mid); margin: 0; }
  .st-scope .dz-hint { font-size: 12px; color: var(--st-text-pale); margin-top: 4px; font-weight: 300; }
  .st-scope .dz-file {
    display: flex; align-items: center; gap: 8px;
    font-size: 13px; font-weight: 600; color: var(--st-red-mid);
  }
  .st-scope .dz-remove {
    background: none; border: none; cursor: pointer;
    color: var(--st-text-pale); font-size: 18px;
    padding: 0; line-height: 1;
  }
  .st-scope .dz-remove:hover { color: var(--st-red); }

  /* ── OR DIVIDER ── */
  .st-scope .or-row {
    display: flex; align-items: center; gap: 12px;
    font-size: 11px; font-weight: 600; letter-spacing: 0.08em;
    text-transform: uppercase; color: var(--st-text-pale);
    margin: 0;
  }
  .st-scope .or-row::before,
  .st-scope .or-row::after {
    content: ''; flex: 1; height: 1px; background: var(--st-gray-mid);
  }

  /* ── FIELDS ── */
  .st-scope .field-label {
    font-size: 11px; font-weight: 700;
    color: var(--st-text-mid); margin-bottom: 6px; margin-top: 0;
    display: block; letter-spacing: 0.04em;
    text-transform: uppercase;
  }
  .st-scope .field-note {
    font-size: 12px; color: var(--st-text-pale);
    font-weight: 300; font-style: italic; margin-top: 6px;
  }
  .st-scope textarea {
    width: 100%; border: 1px solid var(--st-gray-mid);
    border-radius: 4px; padding: 12px 14px;
    font-size: 14px; line-height: 1.6;
    color: var(--st-text); background: var(--st-cream);
    resize: vertical; margin: 0;
    transition: border-color 0.15s, box-shadow 0.15s;
  }
  .st-scope textarea:focus {
    outline: none; border-color: var(--st-red);
    box-shadow: 0 0 0 3px rgba(139,26,26,0.08);
    background: white;
  }

  /* ── PROMPT EDITOR ── */
  .st-scope .prompt-box { border: 1px solid var(--st-gray-mid); border-radius: 4px; overflow: hidden; }
  .st-scope .prompt-bar {
    display: flex; align-items: center; justify-content: space-between;
    padding: 10px 14px; background: var(--st-gray-light);
    border-bottom: 1px solid var(--st-gray-mid);
  }
  .st-scope .prompt-bar-label {
    font-size: 11px; font-weight: 600; letter-spacing: 0.06em;
    text-transform: uppercase; color: var(--st-text-soft); margin: 0;
  }
  .st-scope .btn-xs {
    font-size: 12px; padding: 4px 10px; border-radius: 3px;
    border: 1px solid var(--st-gray-border); background: white;
    color: var(--st-text-mid); cursor: pointer; font-weight: 500;
    transition: border-color 0.12s;
  }
  .st-scope .btn-xs:hover { border-color: var(--st-red); }
  .st-scope .prompt-box textarea { border: none; border-radius: 0; background: white; font-size: 13px; }
  .st-scope .prompt-box textarea:focus { box-shadow: none; }

  /* ── DIAGNOSE TAGS ── */
  .st-scope .tag-wrap { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
  .st-scope .tag {
    display: inline-flex; align-items: center; gap: 5px;
    background: var(--st-red-pale); border: 1px solid rgba(139,26,26,0.20);
    border-radius: 3px; padding: 4px 10px;
    font-size: 13px; font-weight: 600; color: var(--st-red-mid);
  }
  .st-scope .tag-x {
    background: none; border: none; cursor: pointer;
    color: rgba(139,26,26,0.45); font-size: 15px;
    padding: 0; line-height: 1; font-weight: 400;
  }
  .st-scope .tag-x:hover { color: var(--st-red); }
  .st-scope .tag-input {
    border: 1px dashed var(--st-gray-border); border-radius: 3px;
    padding: 4px 10px; font-size: 13px;
    color: var(--st-text); background: transparent;
    outline: none; width: 190px;
  }
  .st-scope .tag-input:focus { border-color: var(--st-red); background: white; }

  /* ── INFO NOTE ── */
  .st-scope .info-note {
    border-left: 3px solid var(--st-red);
    background: var(--st-red-pale);
    padding: 10px 14px; border-radius: 0 4px 4px 0;
    font-size: 13px; color: var(--st-text-mid); font-weight: 400;
    line-height: 1.55; margin: 0;
  }

  /* ── ACTION BAR ── */
  .st-scope .action-bar {
    display: flex; justify-content: flex-end;
    padding-top: 16px; border-top: 1px solid var(--st-gray-mid);
    padding-bottom: 0; padding-left: 0; padding-right: 0;
  }

  /* ── BUTTONS ── */
  .st-scope .btn-primary {
    background: var(--st-red); color: white;
    border: none; border-radius: 3px;
    padding: 10px 24px; font-size: 14px; font-weight: 600;
    cursor: pointer; letter-spacing: 0.01em;
    transition: background 0.15s, box-shadow 0.15s;
    display: inline-flex; align-items: center; gap: 8px;
    margin: 0;
  }
  .st-scope .btn-primary:hover:not(:disabled) {
    background: var(--st-red-hover);
    box-shadow: 0 2px 10px rgba(139,26,26,0.28);
  }
  .st-scope .btn-primary:disabled { opacity: 0.40; cursor: not-allowed; }

  .st-scope .btn-secondary {
    background: white; color: var(--st-text-mid);
    border: 1px solid var(--st-gray-border); border-radius: 3px;
    padding: 10px 20px; font-size: 14px; font-weight: 600;
    cursor: pointer; letter-spacing: 0.01em;
    transition: background 0.15s, border-color 0.15s;
    display: inline-flex; align-items: center; gap: 6px;
  }
  .st-scope .btn-secondary:hover { background: var(--st-gray-light); border-color: var(--st-text-soft); }

  /* ── OUTPUT ── */
  .st-scope .output-card {
    background: white; border: 1px solid var(--st-gray-mid);
    border-radius: 5px; overflow: hidden; margin: 0;
  }
  .st-scope .output-head {
    display: flex; align-items: center; justify-content: space-between;
    padding: 14px 20px; background: var(--st-sidebar);
  }
  .st-scope .output-title {
    font-size: 15px; font-weight: 600; color: white; margin: 0;
  }
  .st-scope .output-btns { display: flex; gap: 8px; }
  .st-scope .btn-out {
    background: rgba(255,255,255,0.12);
    border: 1px solid rgba(255,255,255,0.22);
    color: white; border-radius: 3px;
    padding: 5px 14px; font-size: 12px; font-weight: 600;
    cursor: pointer; letter-spacing: 0.04em; text-transform: uppercase;
    transition: background 0.12s;
  }
  .st-scope .btn-out:hover { background: rgba(255,255,255,0.22); }
  .st-scope .output-tabs { display: flex; background: var(--st-gray-light); border-bottom: 1px solid var(--st-gray-mid); }
  .st-scope .otab {
    padding: 10px 18px; font-size: 13px; font-weight: 600;
    cursor: pointer; border-bottom: 2px solid transparent;
    color: var(--st-text-soft); margin: 0;
    transition: all 0.12s;
  }
  .st-scope .otab.on { color: var(--st-red); border-bottom-color: var(--st-red); }
  .st-scope .output-text {
    padding: 24px; font-size: 14px; line-height: 1.8;
    color: var(--st-text-mid); min-height: 140px;
    white-space: pre-wrap; font-weight: 400; margin: 0;
  }
  .st-scope .output-text.empty { color: var(--st-text-pale); font-style: italic; font-size: 13px; }

  /* ── QUALITY CHECK PANEL (v19 Phase 1) ── */
  /* Wird unterhalb von .output-card eingeblendet wenn der Backend-QC
     Issues gefunden hat. Schwere bestimmt Rahmenfarbe (kritisch=rot,
     warnung=orange, info=grau).
     Sprint 2: read-only Anzeige. Sprint 4 (Phase C): wird zur Checkbox-
     Liste erweitert, plus User-Hint-Textarea und Repair-Button. */
  .st-scope .qc-panel {
    margin-top: 14px; background: white;
    border: 1px solid var(--st-gray-mid);
    border-left: 4px solid var(--st-gray-mid);
    border-radius: 5px; overflow: hidden;
  }
  .st-scope .qc-panel.qc-critical { border-left-color: var(--st-red, #b00); }
  .st-scope .qc-panel.qc-warning  { border-left-color: #d18722; }
  .st-scope .qc-panel.qc-info     { border-left-color: var(--st-text-soft); }
  .st-scope .qc-head {
    display: flex; align-items: center; justify-content: space-between;
    padding: 10px 16px; background: var(--st-gray-light);
    border-bottom: 1px solid var(--st-gray-mid);
  }
  .st-scope .qc-title { font-size: 13px; font-weight: 600; color: var(--st-text-mid); }
  .st-scope .qc-counts { display: flex; gap: 6px; }
  .st-scope .qc-badge {
    display: inline-block; padding: 2px 8px;
    font-size: 11px; font-weight: 600; border-radius: 9px;
    letter-spacing: 0.03em; text-transform: uppercase;
  }
  .st-scope .qc-badge.qc-critical { background: var(--st-red, #b00); color: white; }
  .st-scope .qc-badge.qc-warning  { background: #d18722; color: white; }
  .st-scope .qc-badge.qc-info     { background: var(--st-text-soft); color: white; }
  .st-scope .qc-list { list-style: none; margin: 0; padding: 6px 0; }
  .st-scope .qc-item {
    display: flex; align-items: flex-start; gap: 10px;
    padding: 8px 16px; font-size: 13px; line-height: 1.45;
    border-bottom: 1px solid var(--st-gray-light);
  }
  .st-scope .qc-item:last-child { border-bottom: none; }
  .st-scope .qc-item .qc-marker {
    flex-shrink: 0; width: 8px; height: 8px; margin-top: 6px;
    border-radius: 50%; background: var(--st-text-soft);
  }
  .st-scope .qc-item.qc-critical .qc-marker { background: var(--st-red, #b00); }
  .st-scope .qc-item.qc-warning  .qc-marker { background: #d18722; }
  .st-scope .qc-item.qc-info     .qc-marker { background: var(--st-text-soft); }
  .st-scope .qc-msg   { flex: 1; color: var(--st-text-mid); }
  .st-scope .qc-code  {
    flex-shrink: 0; font-family: ui-monospace, "SF Mono", "Cascadia Mono",
                  Menlo, Consolas, monospace;
    font-size: 11px; color: var(--st-text-soft);
    background: var(--st-gray-light); border-radius: 3px;
    padding: 1px 6px;
  }

  /* ── Sprint 4: interaktive Erweiterung des Panels ── */
  .st-scope .qc-checkbox-label {
    display: inline-flex; align-items: center; gap: 6px;
    flex-shrink: 0; cursor: pointer; margin-top: 2px;
  }
  .st-scope .qc-checkbox {
    width: 16px; height: 16px; cursor: pointer;
    accent-color: var(--st-red, #8b1a1a);
  }
  .st-scope .qc-checkbox:disabled { cursor: not-allowed; }
  .st-scope .qc-repair-progress {
    padding: 12px 16px; background: var(--st-gray-light);
    border-top: 1px solid var(--st-gray-mid);
  }
  .st-scope .qc-repair-progress-label {
    font-size: 12px; font-weight: 600; color: var(--st-text-mid);
  }
  .st-scope .qc-repair-form {
    padding: 12px 16px; background: var(--st-gray-light);
    border-top: 1px solid var(--st-gray-mid);
  }
  .st-scope .qc-hint-label { display: block; position: relative; }
  .st-scope .qc-hint-label-text {
    display: block; font-size: 12px; font-weight: 600;
    color: var(--st-text-mid); margin-bottom: 6px;
  }
  .st-scope .qc-hint-textarea {
    width: 100%; min-height: 60px; padding: 8px 10px;
    border: 1px solid var(--st-gray-mid); border-radius: 4px;
    font-family: inherit; font-size: 13px; line-height: 1.4;
    resize: vertical; box-sizing: border-box; background: white;
  }
  .st-scope .qc-hint-textarea:focus {
    outline: none; border-color: var(--st-red, #8b1a1a);
  }
  .st-scope .qc-hint-textarea:disabled { background: var(--st-gray-light); }
  .st-scope .qc-hint-counter {
    position: absolute; right: 6px; bottom: 6px;
    font-size: 10px; color: var(--st-text-pale); pointer-events: none;
  }
  .st-scope .qc-repair-error {
    margin-top: 8px; padding: 8px 12px; background: #fef2f2;
    border: 1px solid #fecaca; border-radius: 4px;
    color: var(--st-red, #b00); font-size: 12px;
  }
  .st-scope .qc-repair-actions {
    margin-top: 10px; display: flex; justify-content: flex-end;
  }
  .st-scope .qc-repair-btn {
    padding: 8px 18px; background: var(--st-red, #8b1a1a); color: white;
    border: none; border-radius: 4px; font-size: 13px; font-weight: 600;
    cursor: pointer; transition: background 0.15s;
  }
  .st-scope .qc-repair-btn:hover:not(:disabled) { background: #6b0f0f; }
  .st-scope .qc-repair-btn:disabled {
    background: var(--st-text-pale); cursor: not-allowed;
  }

  /* ── Sprint 4: RepairPreviewModal ── */
  .st-scope .qc-modal-backdrop {
    position: fixed; inset: 0; background: rgba(0, 0, 0, 0.5);
    display: flex; align-items: center; justify-content: center;
    z-index: 9999; padding: 20px;
  }
  .st-scope .qc-modal {
    background: white; border-radius: 6px;
    max-width: 800px; width: 100%; max-height: 90vh;
    display: flex; flex-direction: column;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
  }
  .st-scope .qc-modal-head {
    display: flex; align-items: center; justify-content: space-between;
    padding: 14px 20px; border-bottom: 1px solid var(--st-gray-mid);
  }
  .st-scope .qc-modal-title { font-size: 15px; font-weight: 600; color: var(--st-text-mid); }
  .st-scope .qc-modal-x {
    background: none; border: none; font-size: 22px; cursor: pointer;
    color: var(--st-text-soft); line-height: 1; padding: 0 4px;
  }
  .st-scope .qc-modal-x:hover { color: var(--st-text-mid); }
  .st-scope .qc-modal-body {
    padding: 16px 20px; overflow-y: auto; flex: 1;
  }
  .st-scope .qc-modal-hint {
    margin: 0 0 10px 0; font-size: 12px; color: var(--st-text-soft);
    line-height: 1.4;
  }
  .st-scope .qc-modal-hint code {
    background: var(--st-gray-light); padding: 1px 5px; border-radius: 3px;
    font-size: 11px;
  }
  .st-scope .qc-modal-textarea {
    width: 100%; min-height: 320px; padding: 10px;
    border: 1px solid var(--st-gray-mid); border-radius: 4px;
    font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
    font-size: 12px; line-height: 1.45; resize: vertical;
    box-sizing: border-box; background: var(--st-gray-light);
  }
  .st-scope .qc-modal-textarea:focus {
    outline: none; border-color: var(--st-red, #8b1a1a);
  }
  .st-scope .qc-modal-foot {
    display: flex; gap: 10px; justify-content: flex-end;
    padding: 14px 20px; border-top: 1px solid var(--st-gray-mid);
  }

  /* ── Sprint 4: ResultVersionsTabs ── */
  .st-scope .qc-versions-tabs {
    display: flex; gap: 0; margin-top: 14px; margin-bottom: -1px;
  }
  .st-scope .qc-versions-tab {
    background: var(--st-gray-light); border: 1px solid var(--st-gray-mid);
    border-bottom: none; border-radius: 5px 5px 0 0;
    padding: 7px 16px; font-size: 12px; font-weight: 600;
    color: var(--st-text-soft); cursor: pointer; transition: all 0.15s;
    margin-right: -1px;
  }
  .st-scope .qc-versions-tab:hover:not(.active):not(:disabled) {
    background: white; color: var(--st-text-mid);
  }
  .st-scope .qc-versions-tab.active {
    background: white; color: var(--st-red, #8b1a1a); cursor: default;
    position: relative; z-index: 1;
  }
  .st-scope .qc-versions-tab:disabled { cursor: not-allowed; opacity: 0.5; }

  /* ── SPINNER ── */
  @keyframes spin { to { transform: rotate(360deg); } }
  .st-scope .spin {
    width: 14px; height: 14px;
    border: 2px solid rgba(255,255,255,0.3);
    border-top-color: white; border-radius: 50%;
    animation: spin 0.7s linear infinite;
    display: inline-block;
  }

  /* ── TOAST ── */
  .st-scope .toast {
    position: fixed; bottom: 24px; right: 24px;
    background: var(--st-sidebar); color: white;
    padding: 12px 20px; border-radius: 4px;
    font-size: 13px; font-weight: 600;
    box-shadow: var(--shadow-md); z-index: 999;
    display: flex; align-items: center; gap: 10px;
  }
  .st-scope .toast-dot {
    width: 7px; height: 7px; border-radius: 50%;
    background: var(--st-red); flex-shrink: 0;
  }

  /* scrollbar */
  .st-scope ::-webkit-scrollbar { width: 5px; }
  .st-scope ::-webkit-scrollbar-thumb { background: var(--st-gray-mid); border-radius: 3px; }
`;

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

export { S, useHeadStyle };
