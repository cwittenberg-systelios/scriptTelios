# Sprintplan v19.34 – Vorrang laufender Interviews vor neuen Jobs

Basis: v19.33. Gilt nur für `GPU_PROFILE=single`. Bei `dual` und bei
`INTERVIEW_PRIORITY=false` wirkt nichts.

Entscheidungen (24.09.): Ablauf nach 5 min ohne Aktivität, kein Job wartet
länger als 10 min, P0-Transkriptionen warten ebenfalls.

| # | Schritt | Test |
|---|---|---|
| S1 | `services/interview_lease.py`: Reservierung je Session (anlegen, verlängern, freigeben), Ablauf nach `INTERVIEW_LEASE_IDLE_S`, `wait_until_free()` mit Obergrenze `INTERVIEW_MAX_JOB_WAIT_S` und Abbruch | Unit: Ablauf, Verlängerung, Obergrenze, Freigabe, Abbruch |
| S2 | `run_job`: vor dem Start warten, solange ein Interview läuft. Fortschrittstext „Wartet auf ein laufendes Interview (höchstens noch n Min)“, `interview_wait_s` in `performance.log`. Ein laufender Job wird nicht unterbrochen. Wartende Jobs zählen nicht als „laufend“. | Unit mit JobQueue |
| S3 | P0-Worker wartet ebenfalls (höchstens 10 min) | Code-Review |
| S4 | Endpoints: Turn, Diktat (`session_id`), Abschluss und Chat verlängern die Reservierung. Der Chat gibt bei fertig frei und meldet vorab `status.jobs_running`. Neu: `POST /interview/lease` (touch/release) | Unit |
| S5 | Frontend: `useInterviewLease` gibt frei, wenn das Gespräch endet, verworfen wird, die Komponente verschwindet oder der Tab geschlossen wird (keepalive). Aufnahme-Start verlängert. Hinweis „Ein Auftrag läuft gerade noch …“ bis zum ersten Wort. Gilt für Dialog und Fragenkarten. | Jest |
| S6 | CHANGELOG, `.env.example`, Bundle | alle Gates |
