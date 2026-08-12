# Sprintplan G6 — KI-Transparenzhinweis (AI Act Art. 50)

**Base:** `v19_QA_v02` @ `b9deb0f` (frischer Clone 12.08.2026)

## Rechtliche Ableitung (Kurzfassung, Details im Chat)
- Art. 50(1): Systemebene-Hinweis für Nutzer:innen genügt → **UI-Hinweis, permanent sichtbar**.
- Art. 50(4): Kennzeichnungspflicht nur für *veröffentlichte* Texte zur Information der Öffentlichkeit, entfällt zudem bei menschlicher Prüfung. Krankenakte + Kassenanträge = keine Veröffentlichung → **keine Fußzeile pro Dokument**. Redundanz-Bedenken bestätigt.

## Schritte

**S1 — Sidebar-Transparenzhinweis** *(einzige Codeänderung)*
`frontend/klinische-dokumentation.jsx`: permanenter Hinweis im Sidebar-Footer oberhalb der Versionszeile:
„KI-gestütztes System — alle Entwürfe erfordern fachliche Prüfung und Freigabe durch die behandelnde Person."
Testbar: Hinweis in jedem Workflow sichtbar, unabhängig von Backend-Status.

**S2 — Build & Bundle-Verifikation**
esbuild-Syntaxcheck + `vite build`, Bundle-Marker-Prüfung (Hinweistext im Bundle vorhanden), Auslieferung `backend__static__systelios.js`.

**S3 — Tests**
Frontend-Jest-Suite (api/shared unverändert → keine Regression erwartet); Backend-Suite nicht betroffen (kein Backend-Touch), wird zur Sicherheit mitlaufen gelassen sofern Abhängigkeiten installierbar.

**Bewusst NICHT im Sprint:** Fußzeile in generierten Dokumenten (P1–P6), Kennzeichnung in Anträgen (P2b/P3/P3b). Begründung: keine Rechtspflicht, Redundanz in der Akte, bei Kassenanträgen potenziell irritierend; interne Nachvollziehbarkeit besteht bereits über `job_id` in prompts.log/feedback.log.
