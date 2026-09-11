// ═══════════════════════════════════════════════════════════════════════════
// scriptTelios — RunPod Control Proxy (Cloudflare Worker)
// ───────────────────────────────────────────────────────────────────────────
// Rolle: EINZIGE Logik-Schicht fuer die Pod-Steuerung. Haelt die Secrets,
// spricht mit der RunPod-GraphQL-API, fuehrt das Statusprotokoll (KV) und
// laeuft die Zeitsteuerung (Cron). Das Confluence-Makro ist ein reiner
// UI-Layer, der ausschliesslich diese HTTP-API aufruft — keine Duplizierung.
//
// HTTP-Endpunkte (alle ausser "/" erfordern HMAC-Auth):
//   GET  /            → Ping (ohne Auth)                    "RunPod Proxy OK"
//   GET  /state       → { podId, desiredStatus, hasGpu, anyRunning, otherRunning[] }
//   GET  /pods        → alle Account-Pods (Doppelstart-Erkennung)
//   GET  /selfcheck   → Self-Check-Report (live vom Pod; "stopped" wenn Pod aus)
//   POST /start       → Pod starten (podResume)
//   POST /stop        → Pod stoppen  (podStop)
//   POST /setPodId    → { podId }  → mutable Pod-ID in KV schreiben
//   POST /recover     → { podId? } → optionaler ID-Swap + Resume (No-GPU-Fall)
//   GET  /logs        → Statusprotokoll (neueste zuerst, inkl. user)
//   GET  /debug       → Auth-Diagnose (leakt nur Metadaten)
//   POST /testrun     → Backend-Testlauf triggern
//   POST /notify      → Benachrichtigung ausloesen (Backend meldet Feedback)
//
//   v19.21 Pod-Lifecycle v2 (Terminate & Redeploy statt Stop & Resume):
//   GET  /spec         → gespeicherte Pod-Spec (state:podSpec) anzeigen
//   POST /spec/capture → Spec aus dem LAUFENDEN Pod lesen und speichern
//   POST /spec/set     → { spec } → Spec manuell setzen/korrigieren
//   POST /spec/clear   → Spec loeschen → Worker faellt auf Stop/Resume zurueck
//   POST /ensure       → idempotent: laeuft → ok; aus → Deploy (starting);
//                        keine GPU → 10-min-Retry (starting/no_server);
//                        23–05 Uhr → blocked_night. Basis fuer Start-on-Intent.
//   Env DRY_RUN=1      → podTerminate/podFindAndDeployOnDemand nur protokollieren
//
// Auth (spiegelt backend/app/core/auth.py):
//   Header: X-Systelios-User / X-Systelios-Timestamp / X-Systelios-Signature
//   sig = HMAC-SHA256(CONFLUENCE_SHARED_SECRET, "<user>:<timestamp>") hex
//   Replay-Schutz: |now - ts| <= AUTH_WINDOW_SEC
//
// Env / Bindings:
//   RUNPOD_API_KEY            (Secret)  — RunPod-API-Key
//   RUNPOD_POD_ID             (Secret)  — Seed/Fallback fuer die Pod-ID
//   CONFLUENCE_SHARED_SECRET  (Secret)  — HMAC-Shared-Secret (== Backend)
//   CONFLUENCE_ORIGIN         (Var)     — erlaubte CORS-Origin(s), kommagetrennt
//                                         z.B. "https://confluence.systelios.de"
//   TELEGRAM_BOT_TOKEN        (Secret, optional)
//   TELEGRAM_CHAT_ID          (Secret, optional)
//   LOGS                      (KV)      — Statusprotokoll + mutable Pod-ID
//
// Backend-Hostname: Konstante BACKEND_BASE (siehe unten) — nicht mehrfach
// hartcodiert. Muss zum Public Hostname des Named Tunnels passen.
//
// KV-Keys im LOGS-Namespace:
//   "<epoch_ms>"    → JSON-Log-Eintrag (Key = Date.now().toString())
//   "state:podId"   → aktuell gesetzte Pod-ID (mutabel, per /setPodId)
// ═══════════════════════════════════════════════════════════════════════════

// Basis-URL des Pod-Backends (Cloudflare Named Tunnel). EINZIGE Stelle -
// vorher stand der Hostname an vier Stellen hartcodiert, ein Hostname-Wechsel
// haette Selfcheck, Testrun und Health-Warteschleife stillschweigend gebrochen.
// Bei Umzug auf eine andere Subdomain nur hier aendern (und den Public
// Hostname des Tunnels im Zero-Trust-Dashboard nachziehen).
const BACKEND_BASE = "https://api.scriptelios.win";

const AUTH_WINDOW_SEC = 300;         // Replay-Fenster (== AUTH_TIMESTAMP_WINDOW_SEC)
const POD_ID_KEY = "state:podId";    // KV-Key fuer die mutable Pod-ID

// ═══════════════════════════════════════════════════════════════════════════
// POD-LIFECYCLE v2 (v19.21) — Terminate & Redeploy
// ───────────────────────────────────────────────────────────────────────────
// Problem: Ein GESTOPPTER Pod bleibt an seinen Host gebunden; beim Resume muss
// genau dessen GPU frei sein ("no GPU available", Migration von Hand).
// Loesung: Stopp = podTerminate, Start = podFindAndDeployOnDemand auf dem
// Network Volume — RunPod sucht dann RZ-weit nach einer freien RTX PRO 4500.
// Alles Zustandsbehaftete liegt auf /workspace (Modelle, .env mit Tunnel-Token,
// venv, HF-Cache); runpod-start.sh ist auf ephemere Container-Disk ausgelegt.
// Aktiv NUR wenn eine Spec in KV liegt (state:podSpec) — sonst altes Verhalten.
// Kein GPU-Fallback auf andere Architekturen (Entscheid 11.09.2026).
const SPEC_KEY         = "state:podSpec";
const WANT_RUNNING_KEY = "state:wantRunning";    // JSON {since, attempts, lastAttempt}
const NO_SERVER_KEY    = "state:noServerSince";  // epoch ms, wenn Retry-Fenster erschoepft
const NO_GPU_RETRY_WINDOW_MS = 10 * 60 * 1000;   // Entscheid F2: 10 Minuten
const NO_GPU_RETRY_MIN_GAP_MS = 60 * 1000;       // hoechstens ein Versuch pro Minute
const DEPLOY_ETA_S = 300;                        // UI-Erwartung: ~3–6 min Kaltstart
// Nur diese GPU — exakter RunPod-gpuTypeId; Spec-Capture uebernimmt den Ist-Wert.
const REQUIRED_GPU_TYPE_ID = "NVIDIA RTX PRO 4500 Blackwell";

// ═══════════════════════════════════════════════════════════════════════════
// ZEITSTEUERUNG (v19.10) — der Cron STOPPT nur, gestartet wird von Hand.
// ───────────────────────────────────────────────────────────────────────────
// Vorher: zwei feste UTC-Crons ("0 7 * * 1-5" / "0 18 * * 1-5"), gegen die
// getAction() per exaktem String verglichen hat. Wurden sie im Dashboard
// geaendert, lief scheduled() ins "return" — ohne Log, ohne Telegram. Genau
// das ist passiert (registriert war "0 19 * * 2-6").
//
// Jetzt: EIN einziger Cron "*/15 * * * *". Der ist zeitzonen-invariant, also
// immun gegen Sommer-/Winterzeit, und benutzt Cloudflares Wochentag-
// Nummerierung nicht (dort ist 1 = Sonntag, 2 = Montag — die Quelle des
// zweiten Missverstaendnisses). Die gesamte Zeitlogik steht hier im Code und
// rechnet in echter Lokalzeit.
//
// DASHBOARD-SOLL: genau ein Cron-Eintrag, "*/15 * * * *". Jeder andere
// Trigger loggt und alarmiert (siehe handleUnknownCron).
const TZ = "Europe/Berlin";
const EXPECTED_CRONS = ["*/15 * * * *"];

// Idle-Schwellen nach Lokalzeit. 120 min in der Kernzeit, weil eine Aufnahme
// laenger dauern kann als eine Stunde und ein Abbruch mitten in der Sitzung
// den Upload verlieren wuerde.
const IDLE_LONG_SEC  = 120 * 60;   // 08:00–18:00
const IDLE_SHORT_SEC =  30 * 60;   // 05:00–08:00 und 18:00–23:00
const HARD_FROM_MIN  = 23 * 60;    // ab 23:00 bedingungslos …
const HARD_TO_MIN    =  5 * 60;    // … bis 05:00

// Kulanz in der harten Phase (Variante b): laeuft nachweislich noch etwas,
// wird der Stopp aufgeschoben — aber hoechstens so lange. Danach bedingungs-
// los, damit ein in "running" haengengebliebener Job den Pod nicht durch die
// Nacht traegt.
const HARD_GRACE_MS = 30 * 60 * 1000;
const HARD_GRACE_IDLE_SEC = 120;   // Heartbeat juenger als 2 min ⇒ "aktiv"

const RECONCILE_KEY    = "state:reconcileStatus";
const HARD_GRACE_KEY   = "state:hardGraceSince";
const UNKNOWN_CRON_KEY = "state:lastUnknownCron";

export default {
  async scheduled(event, env, ctx) {
    try {
      const action = getAction(event.cron);
      if (action !== "tick") { await handleUnknownCron(event.cron, env); return; }

      // Ein Report fuer beides — Selfcheck und Reconciler teilen sich den
      // Backend-Call, sonst wuerde der Pod pro Tick zweimal befragt.
      const secrets = await getSecrets(env);
      const report = await getSelfcheckReport(env, secrets);
      await cronSelfcheck(env, ctx, report, secrets);
      await reconcile(env, ctx, secrets, report);
      await retryWantRunning(env, ctx, secrets, report);   // v19.21 (B4)
    } catch (err) {
      console.log("Scheduled error:", err);
      try {
        const secrets = await getSecrets(env);
        await notify(secrets, `❌ Scheduled crash: ${err.message}`);
      } catch {}
    }
  },

  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    originCache.set(req, await resolveOrigin(env));   // Origin (Var oder Secret) aufloesen + cachen
    const cors = corsHeaders(env, req);

    // CORS-Preflight immer beantworten
    if (req.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors });
    }

    // Oeffentlicher Ping (ohne Auth) — kein Secret involviert
    if (url.pathname === "/" || url.pathname === "") {
      if (url.searchParams.get("diag")) {
        // Diagnose OHNE Auth: NUR Vorhandensein/Lesbarkeit der Bindings — niemals Werte.
        const present = async (name) => {
          const b = env[name];
          if (!b) return false;
          if (typeof b === "string") return b.trim().length > 0;
          if (typeof b.get === "function") { try { const v = await b.get(); return !!(v && v.trim()); } catch { return false; } }
          return false;
        };
        const diag = {
          CONFLUENCE_SHARED_SECRET: await present("CONFLUENCE_SHARED_SECRET"),
          CONFLUENCE_ORIGIN: await present("CONFLUENCE_ORIGIN"),
          RUNPOD_API_KEY: await present("RUNPOD_API_KEY"),
          RUNPOD_POD_ID: await present("RUNPOD_POD_ID"),
          TELEGRAM_BOT_TOKEN: await present("TELEGRAM_BOT_TOKEN"),
          LOGS: !!env.LOGS,
        };
        // Backend-Self-Check AUS WORKER-SICHT — genau das, was fetchBackendSelfcheck sieht.
        let backend;
        try {
          const br = await fetch(`${BACKEND_BASE}/api/selfcheck`,
            { signal: AbortSignal.timeout(15000), headers: { "Accept": "application/json" } });
          const bt = await br.text();
          let parsed = null; try { parsed = JSON.parse(bt); } catch {}
          backend = {
            httpStatus: br.status,
            contentType: br.headers.get("content-type") || "",
            jsonOk: !!parsed,
            parsedStatus: parsed ? parsed.status : null,
            bodySnippet: (bt || "").replace(/\s+/g, " ").trim().slice(0, 220),
          };
        } catch (e) {
          backend = { error: String(e && e.message || e) };
        }
        return new Response(JSON.stringify({ ok: true, bindings: diag, backend }, null, 2),
          { status: 200, headers: { ...cors, "Content-Type": "application/json" } });
      }
      return new Response("RunPod Proxy OK", { status: 200, headers: cors });
    }

    try {
      const secrets = await getSecrets(env);

      // Ab hier: HMAC-Pflicht fuer alle Endpunkte
      const auth = await verifyHmac(req, secrets);
      if (!auth.ok) {
        return json({ error: "unauthorized", reason: auth.reason }, { status: 401, env, req });
      }
      const user = auth.user;

      if (url.pathname === "/state")    return await handleState(env, secrets, req);
      if (url.pathname === "/pods")     return await handlePods(env, secrets, req);
      if (url.pathname === "/selfcheck") return await handleSelfcheck(env, secrets, req);
      if (url.pathname === "/start")    return await execute("start", env, ctx, user, req);
      if (url.pathname === "/stop")     return await execute("stop", env, ctx, user, req);
      if (url.pathname === "/setPodId") return await handleSetPodId(req, env, user);
      // v19.21 Lifecycle v2
      if (url.pathname === "/spec" && req.method === "GET") return await handleSpecGet(env, req);
      if (url.pathname === "/spec/capture") return await handleSpecCapture(env, secrets, user, req);
      if (url.pathname === "/spec/set")     return await handleSpecSet(req, env, user);
      if (url.pathname === "/spec/clear")   return await handleSpecClear(env, user, req);
      if (url.pathname === "/ensure")       return await handleEnsure(env, ctx, secrets, user, req);
      if (url.pathname === "/recover")  return await handleRecover(req, env, ctx, user);
      if (url.pathname === "/logs")     return await getLogs(env, req);
      if (url.pathname === "/debug")    return await debugAuth(env, req);
      if (url.pathname === "/testrun")  return await triggerTestRun(env, ctx, req);
      if (url.pathname === "/notify")   return await handleNotify(req, env, secrets, user);

      return json({ error: "not_found", path: url.pathname }, { status: 404, env, req });
    } catch (err) {
      console.log("Fetch error:", err);
      return json({ error: err.message }, { status: 500, env, req });
    }
  }
};

// ─────────────────────────────────────────────
// SECRETS (Cloudflare Secrets Store — async .get() — oder plain string)
// ─────────────────────────────────────────────

async function getSecrets(env) {
  const resolve = async (name) => {
    const binding = env[name];
    if (!binding) return null;
    if (typeof binding.get === "function") return await binding.get();
    if (typeof binding === "string") return binding;
    return null;
  };

  const [runpodKey, podId, telegramToken, telegramChatId, confluenceSecret] = await Promise.all([
    resolve("RUNPOD_API_KEY"),
    resolve("RUNPOD_POD_ID"),
    resolve("TELEGRAM_BOT_TOKEN"),
    resolve("TELEGRAM_CHAT_ID"),
    resolve("CONFLUENCE_SHARED_SECRET"),
  ]);

  return {
    runpodKey: runpodKey?.trim() ?? null,
    podId: podId?.trim() ?? null,
    telegramToken: telegramToken?.trim() ?? null,
    telegramChatId: telegramChatId?.trim() ?? null,
    confluenceSecret: confluenceSecret?.trim() ?? null,
  };
}

// ─────────────────────────────────────────────
// AUTH (HMAC-SHA256, spiegelt core/auth.py)
// ─────────────────────────────────────────────

async function hmacHex(secret, msg) {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw", enc.encode(secret),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]
  );
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode(msg));
  return [...new Uint8Array(sig)].map(b => b.toString(16).padStart(2, "0")).join("");
}

// Laengengleicher Vergleich (kein Early-Exit) — Hex-Strings.
function timingSafeEqualHex(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

async function verifyHmac(req, secrets) {
  const user = req.headers.get("X-Systelios-User") || "";
  const ts = req.headers.get("X-Systelios-Timestamp") || "";
  const sig = req.headers.get("X-Systelios-Signature") || "";

  if (!user || !ts || !sig) return { ok: false, reason: "missing_headers" };
  if (!secrets.confluenceSecret) return { ok: false, reason: "server_secret_missing" };

  const tsNum = parseInt(ts, 10);
  if (!Number.isFinite(tsNum)) return { ok: false, reason: "bad_timestamp" };

  const age = Math.abs(Date.now() / 1000 - tsNum);
  if (age > AUTH_WINDOW_SEC) return { ok: false, reason: "expired" };

  const expected = await hmacHex(secrets.confluenceSecret, `${user}:${ts}`);
  if (!timingSafeEqualHex(expected, sig.toLowerCase())) return { ok: false, reason: "bad_signature" };

  return { ok: true, user };
}

// ─────────────────────────────────────────────
// CORS + JSON-Helper
// ─────────────────────────────────────────────

// CONFLUENCE_ORIGIN kann plain Var (String) ODER Secrets-Store-Binding (.get()) sein.
// Async aufgeloest und request-scoped gecacht, damit das synchrone corsHeaders() drankommt.
const originCache = new WeakMap();

async function resolveOrigin(env) {
  const b = env.CONFLUENCE_ORIGIN;
  if (!b) return "";
  if (typeof b === "string") return b;
  if (typeof b.get === "function") { try { return (await b.get()) || ""; } catch { return ""; } }
  return "";
}

function corsHeaders(env, req) {
  const reqOrigin = req ? (req.headers.get("Origin") || "") : "";
  // Vorab aufgeloeste Origin (Secrets Store oder Var) request-scoped aus dem Cache;
  // Fallback fuer Cron/kein-req: nur wenn plain String.
  const cached = req ? originCache.get(req) : undefined;
  const rawOrigin = typeof cached === "string"
    ? cached
    : (typeof env.CONFLUENCE_ORIGIN === "string" ? env.CONFLUENCE_ORIGIN : "");
  const configured = rawOrigin
    .split(",").map(s => s.trim()).filter(Boolean);

  let allowOrigin;
  if (configured.length === 0) {
    allowOrigin = reqOrigin || "*";           // Dev-Fallback: Request-Origin spiegeln
  } else if (configured.includes(reqOrigin)) {
    allowOrigin = reqOrigin;
  } else {
    allowOrigin = configured[0];              // unbekannte Origin: erste erlaubte
  }

  return {
    "Access-Control-Allow-Origin": allowOrigin,
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers":
      "Content-Type, X-Systelios-User, X-Systelios-Timestamp, X-Systelios-Signature",
    "Access-Control-Max-Age": "86400",
    "Vary": "Origin",
  };
}

function json(data, { status = 200, env, req } = {}) {
  return new Response(JSON.stringify(data, null, 2), {
    status,
    headers: { "content-type": "application/json", ...corsHeaders(env, req) },
  });
}

// ─────────────────────────────────────────────
// POD-ID (mutabel: KV, Fallback Secret)
// ─────────────────────────────────────────────

async function getPodId(env, secrets) {
  if (env.LOGS) {
    try {
      const v = await env.LOGS.get(POD_ID_KEY);
      if (v && v.trim()) return v.trim();
    } catch (e) {
      console.log("getPodId KV error:", e);
    }
  }
  return secrets.podId;   // Seed/Fallback aus Secret
}

function isValidPodId(id) {
  return typeof id === "string" && /^[a-z0-9]{6,}$/i.test(id);
}

// ─────────────────────────────────────────────
// CRON
// ─────────────────────────────────────────────

// Cloudflare liefert den Ausdruck gelegentlich mit abweichendem Whitespace
// (im Log war ein Trailing Space zu sehen). Exakter String-Vergleich ohne
// Normalisierung ist deshalb fragil.
function normalizeCron(cron) {
  return String(cron ?? "").trim().replace(/\s+/g, " ");
}

function getAction(cron) {
  const c = normalizeCron(cron);
  if (c === "*/15 * * * *") return "tick";
  return null;
}

// Frueher fuehrte ein unbekannter Cron zu einem stillen "return": kein Log,
// kein Telegram, wallTimeMs 0 — die Stoerung lief wochenlang unbemerkt.
// Jetzt laut, aber dedupliziert (sonst alle 15 min dieselbe Meldung).
async function handleUnknownCron(cron, env) {
  const c = normalizeCron(cron);

  await log(env, {
    action: "cron", status: "UNKNOWN_CRON", user: "cron",
    cron: c, expected: EXPECTED_CRONS,
  });

  let prev = null;
  if (env.LOGS) {
    try { prev = await env.LOGS.get(UNKNOWN_CRON_KEY); } catch {}
  }
  if (prev === c) return;
  if (env.LOGS) {
    try { await env.LOGS.put(UNKNOWN_CRON_KEY, c); } catch {}
  }

  const secrets = await getSecrets(env);
  await notify(secrets,
    `🚨 Unbekannter Cron-Trigger: "${c}"\n` +
    `Erwartet: ${EXPECTED_CRONS.join(", ")}\n` +
    `Der Worker hat nichts getan. Eintrag im Cloudflare-Dashboard loeschen.`);
}

// ─────────────────────────────────────────────
// MAIN EXECUTION (start/stop)
// ─────────────────────────────────────────────

// opts.quiet: unterdrueckt NUR die Abschlussmeldung. Der Reconciler meldet
// selbst und dedupliziert dabei — sonst haette ein wiederholt schei-
// ternder Auto-Stopp alle 15 Minuten dasselbe Telegram erzeugt.
async function execute(action, env, ctx, user, req, opts = {}) {
  const secrets = await getSecrets(env);
  const podId = await getPodId(env, secrets);

  // ── Doppelstart-Schutz ──────────────────────────────────────────────
  // Vor jedem Start ALLE Account-Pods pruefen (nicht nur die getrackte ID).
  // Faengt den Fall ab, dass von Hand ein Pod mit ANDERER ID gestartet wurde
  // und Cron/UI sonst einen zweiten Pod resuemieren wuerden (doppelte GPU-Kosten).
  if (action === "start") {
    const podsRes = await listPods(secrets);
    if (podsRes.ok) {
      const running = podsRes.pods.filter(
        p => String(p.desiredStatus).toUpperCase() === "RUNNING"
      );
      const foreignRunning = running.filter(p => p.id !== podId);
      const trackedRunning = running.find(p => p.id === podId);

      if (foreignRunning.length > 0) {
        const entry = {
          time: new Date().toISOString(),
          action, status: "OTHER_RUNNING", blocked: true, user, podId,
          runningPods: foreignRunning.map(p => ({
            id: p.id, name: p.name, desiredStatus: p.desiredStatus, hasGpu: p.hasGpu,
          })),
        };
        await log(env, entry);
        await notify(secrets,
          `⚠️ START abgebrochen (${user}): es laeuft bereits ein anderer Pod — ` +
          foreignRunning.map(p => `${p.name || "?"} (${p.id})`).join(", ") +
          `. Kein zweiter Pod gestartet. Ggf. Pod-ID uebernehmen.`
        );
        return json(entry, { env, req });
      }

      if (trackedRunning) {
        await notify(secrets, "ℹ️ Server is already running, nothing to do.");
        const e = { action, status: "ALREADY_RUNNING", user, podId };
        await log(env, e);
        return json(e, { env, req });
      }
    }
    // listPods fehlgeschlagen → Best-Effort: unten greift der getState-Kurzschluss.
  }

  const { status: stateBefore } = await getState(secrets, podId);

  // Kurzschluss, wenn bereits im Zielzustand
  if (action === "start" && stateBefore === "RUNNING") {
    await notify(secrets, "ℹ️ Server is already running, nothing to do.");
    const e = { action, status: "ALREADY_RUNNING", user, podId };
    await log(env, e);
    return json(e, { env, req });
  }
  if (action === "stop" && (stateBefore === "EXITED" || stateBefore === "NO_STATUS")) {
    // NO_STATUS = Pod existiert nicht mehr (nach Terminate) → nichts zu tun.
    if (!opts.quiet) await notify(secrets, "ℹ️ Server is already stopped, nothing to do.");
    const e = { action, status: "ALREADY_STOPPED", user, podId };
    await log(env, e);
    return json(e, { env, req });
  }

  // v19.21: Mit Spec → Terminate/Deploy; ohne Spec → Stop/Resume (Altverhalten).
  const spec = await getSpec(env);
  const dryRun = isDryRun(env);
  let result, effectivePodId = podId;
  if (spec && action === "start") {
    result = await deployPod(secrets, spec, dryRun);
    if (result?.ok && result?.newPodId) {
      effectivePodId = result.newPodId;
      if (env.LOGS && !dryRun) { try { await env.LOGS.put(POD_ID_KEY, effectivePodId); } catch {} }
    }
  } else if (spec && action === "stop") {
    result = await terminatePod(secrets, podId, dryRun);
  } else {
    result = await runPod(action, secrets, podId);
  }
  const { status: stateAfterRaw } = dryRun && spec
    ? { status: action === "start" ? "RUNNING" : "NO_STATUS" }
    : await getState(secrets, effectivePodId);
  // Nach Terminate liefert RunPod fuer die alte ID keinen Pod mehr → als EXITED werten.
  const stateAfter = (action === "stop" && stateAfterRaw === "NO_STATUS") ? "EXITED" : stateAfterRaw;
  const lifecycleMode = spec ? "redeploy" : "resume";

  if (result?.status === 401) {
    const entry = {
      time: new Date().toISOString(),
      action, status: "AUTH_FAILED", user, podId,
      stateBefore, stateAfter, runpod: result,
    };
    await log(env, entry);
    await notify(secrets, `🚨 ${action.toUpperCase()} AUTH_FAILED\nCheck RUNPOD_API_KEY in Secrets Store`);
    return json(entry, { status: 401, env, req });
  }

  const isHardFailure =
    !result?.ok ||
    result?.status >= 400 ||
    result?.data?.errors ||
    result?.data?.fetchError ||
    result?.data?.parseError;

  const success =
    (action === "start" && stateAfter === "RUNNING") ||
    (action === "stop"  && stateAfter === "EXITED");

  const status = isHardFailure ? "HARD_ERROR" : success ? "SUCCESS" : "SOFT_FAIL";

  // No-GPU fuer die UI ableiten: RunPod-Fehler ODER Start blieb EXITED.
  const noGpu = !!result?.noGpu || (action === "start" && stateAfter === "EXITED");

  const entry = {
    time: new Date().toISOString(),
    action, status: dryRun && spec ? `DRY_RUN_${status}` : status, user,
    podId: effectivePodId, previousPodId: effectivePodId !== podId ? podId : undefined,
    lifecycle: lifecycleMode, dryRun: !!(dryRun && spec),
    stateBefore, stateAfter, noGpu, runpod: result,
  };

  await log(env, entry);

  // Startzeit fuer die Startup-Erkennung tracken (getSelfcheckReport liest sie).
  if (env.LOGS && status === "SUCCESS" && !(dryRun && spec)) {
    try {
      if (action === "start") { sinceMemo = Date.now(); await env.LOGS.put("state:podRunningSince", String(sinceMemo)); }
      if (action === "stop")  { sinceMemo = 0; await env.LOGS.put("state:podRunningSince", "0"); }
    } catch {}
  }

  if (status === "SUCCESS" && action === "start" && !(dryRun && spec)) {
    // Fire-and-forget Health-Poll — laeuft dank ctx auch bei manuellem Start.
    ctx?.waitUntil?.(waitForBackend(secrets, result));
    if (spec) await notify(secrets, `🚀 Deploy OK (${user}): neuer Pod ${effectivePodId}` +
      (entry.previousPodId ? ` (vorher ${entry.previousPodId})` : ""));
  } else if (!opts.quiet) {
    await notify(secrets,
      status === "SUCCESS"
        ? `✅ ${action.toUpperCase()} OK (${user})\n${stateBefore} → ${stateAfter}`
        : `❌ ${action.toUpperCase()} ${status} (${user})\n${JSON.stringify(result?.data)}`
    );
  }

  return json(entry, { env, req });
}

// ─────────────────────────────────────────────
// STATE-ENDPUNKT
// ─────────────────────────────────────────────

async function handleState(env, secrets, req) {
  const podId = await getPodId(env, secrets);
  const podsRes = await listPods(secrets);

  if (podsRes.ok) {
    const tracked = podsRes.pods.find(p => p.id === podId);
    const running = podsRes.pods.filter(
      p => String(p.desiredStatus).toUpperCase() === "RUNNING"
    );
    const otherRunning = running.filter(p => p.id !== podId);
    const trackedRunning = String(tracked?.desiredStatus).toUpperCase() === "RUNNING";
    return json({
      ok: true,
      podId,
      desiredStatus: tracked?.desiredStatus ?? "NOT_FOUND",
      hasGpu: !!tracked?.hasGpu,
      trackedRunning,             // laeuft der GETRACKTE Pod?
      anyRunning: running.length > 0,   // laeuft IRGENDEIN Pod? (UI: "Server laeuft")
      otherRunning,               // laufende Pods mit ANDERER ID → Doppelstart-Warnung
      podCount: podsRes.pods.length,
      lifecycle: await lifecycleState(env),   // v19.21
    }, { env, req });
  }

  // Fallback: Einzel-Query, falls die Pod-Liste nicht abrufbar ist.
  const st = await getState(secrets, podId);
  const trackedRunningFb = String(st.status).toUpperCase() === "RUNNING";
  return json({
    ok: true,
    podId,
    desiredStatus: st.status,
    hasGpu: !!st.hasGpu,
    trackedRunning: trackedRunningFb,
    anyRunning: trackedRunningFb,     // ohne Pod-Liste nur getrackter Pod bekannt
    otherRunning: [],
    podsError: podsRes.error,
  }, { env, req });
}

// ─────────────────────────────────────────────
// SET POD ID
// ─────────────────────────────────────────────

async function handleSetPodId(req, env, user) {
  let body;
  try { body = await req.json(); } catch { body = {}; }
  const newId = (body?.podId || "").trim();

  if (!isValidPodId(newId)) {
    return json({ error: "invalid_pod_id", podId: newId }, { status: 400, env, req });
  }
  if (!env.LOGS) {
    return json(
      { error: "kv_unavailable", detail: "LOGS KV binding required to store mutable pod id" },
      { status: 500, env, req }
    );
  }

  const previous = await env.LOGS.get(POD_ID_KEY);
  await env.LOGS.put(POD_ID_KEY, newId);

  await log(env, {
    time: new Date().toISOString(),
    action: "setPodId", status: "SUCCESS", user,
    previous: previous || null, podId: newId,
  });

  const secrets = await getSecrets(env);
  await notify(secrets, `🆔 Pod-ID gesetzt (${user}): ${previous || "—"} → ${newId}`);

  return json({ ok: true, podId: newId, previous: previous || null }, { env, req });
}

// ─────────────────────────────────────────────
// RECOVER (Variante i: optionaler ID-Swap + Resume)
// ─────────────────────────────────────────────

async function handleRecover(req, env, ctx, user) {
  let body;
  try { body = await req.json(); } catch { body = {}; }
  const newId = (body?.podId || "").trim();

  const secrets = await getSecrets(env);

  // Optionaler Pod-ID-Swap vor dem Resume
  if (newId) {
    if (!isValidPodId(newId)) {
      return json({ error: "invalid_pod_id", podId: newId }, { status: 400, env, req });
    }
    if (env.LOGS) await env.LOGS.put(POD_ID_KEY, newId);
  }

  const podId = await getPodId(env, secrets);
  const result = await runPod("start", secrets, podId);
  const { status: stateAfter } = await getState(secrets, podId);
  const noGpu = !!result?.noGpu || stateAfter === "EXITED";

  const entry = {
    time: new Date().toISOString(),
    action: "recover",
    status: noGpu ? "NO_GPU" : "SUCCESS",
    user, podId, swapped: !!newId,
    stateAfter, runpod: result,
  };
  await log(env, entry);

  if (!noGpu) {
    sinceMemo = Date.now();
    if (env.LOGS) { try { await env.LOGS.put("state:podRunningSince", String(sinceMemo)); } catch {} }
    ctx?.waitUntil?.(waitForBackend(secrets, result));
    await notify(secrets, `🔁 Recover (${user}): Pod ${podId} → resume (${stateAfter})`);
  } else {
    await notify(secrets, `🚫 Recover (${user}): weiterhin keine GPU fuer Pod ${podId}.`);
  }

  // noGpu explizit fuer die UI mitliefern
  return json({ ...entry, noGpu }, { env, req });
}

// ─────────────────────────────────────────────
// LIFECYCLE v2 — Spec, Deploy, Terminate, Ensure (v19.21)
// ─────────────────────────────────────────────

function isDryRun(env) {
  const v = env?.DRY_RUN;
  return v === "1" || v === "true" || v === true;
}

async function getSpec(env) {
  if (!env.LOGS) return null;
  try {
    const raw = await env.LOGS.get(SPEC_KEY);
    if (!raw) return null;
    const spec = JSON.parse(raw);
    return isValidSpec(spec) ? spec : null;
  } catch (e) {
    console.log("getSpec KV error:", e);
    return null;
  }
}

function isValidSpec(spec) {
  return !!(spec && typeof spec === "object" &&
    typeof spec.imageName === "string" && spec.imageName.trim() &&
    typeof spec.networkVolumeId === "string" && spec.networkVolumeId.trim() &&
    typeof spec.gpuTypeId === "string" && spec.gpuTypeId.trim());
}

// Secrets NIE ins KV: Werte von Keys mit typischen Secret-Namen und RunPod-
// Auto-Variablen werden verworfen; die Env kommt beim Boot aus /workspace/.env.
const SECRET_KEY_RE = /(TOKEN|SECRET|KEY|PASS|PWD|CREDENTIAL|PRIVATE)/i;
const RUNPOD_AUTO_ENV_RE = /^(RUNPOD_|PUBLIC_KEY$|CUDA_VERSION$|PYTORCH_VERSION$|PWD$|HOME$|PATH$|HOSTNAME$)/;

function sanitizeEnv(envList) {
  const out = [];
  for (const item of envList || []) {
    let key, value;
    if (typeof item === "string") {
      const i = item.indexOf("=");
      if (i <= 0) continue;
      key = item.slice(0, i); value = item.slice(i + 1);
    } else if (item && typeof item === "object") {
      key = item.key; value = item.value;
    } else continue;
    if (!key || RUNPOD_AUTO_ENV_RE.test(key) || SECRET_KEY_RE.test(key)) continue;
    out.push({ key, value: String(value ?? "") });
  }
  return out;
}

// Spec aus dem RunPod-Pod-Objekt ableiten. Unbekannte/fehlende Felder bleiben
// leer und muessen ueber /spec/set nachgetragen werden (UI zeigt die Spec).
function specFromPod(pod, extra = {}) {
  const gpuTypeId = pod?.machine?.gpuTypeId || REQUIRED_GPU_TYPE_ID;
  return {
    version: 1,
    capturedAt: new Date().toISOString(),
    capturedFromPodId: pod?.id || null,
    name: pod?.name || "scriptTelios",
    imageName: pod?.imageName || "",
    dockerArgs: pod?.dockerArgs || "",
    ports: pod?.ports || "",
    containerDiskInGb: Number(pod?.containerDiskInGb) || 50,
    volumeMountPath: pod?.volumeMountPath || "/workspace",
    networkVolumeId: pod?.networkVolumeId || extra.networkVolumeId || "",
    dataCenterId: pod?.machine?.dataCenterId || extra.dataCenterId || "",
    gpuTypeId,
    cloudType: pod?.machine?.secureCloud === false ? "COMMUNITY" : "SECURE",
    minVcpuCount: Number(pod?.vcpuCount) || 8,
    minMemoryInGb: Number(pod?.memoryInGb) || 30,
    env: sanitizeEnv(pod?.env),
  };
}

// Deploy-Input fuer podFindAndDeployOnDemand — reine Funktion (Jest-getestet).
function buildDeployInput(spec) {
  const input = {
    cloudType: spec.cloudType || "SECURE",
    gpuCount: 1,
    gpuTypeId: spec.gpuTypeId,
    name: spec.name || "scriptTelios",
    imageName: spec.imageName,
    containerDiskInGb: spec.containerDiskInGb || 50,
    volumeInGb: 0,                       // kein zusaetzliches Pod-Volume: alles auf dem Network Volume
    volumeMountPath: spec.volumeMountPath || "/workspace",
    networkVolumeId: spec.networkVolumeId,
    minVcpuCount: spec.minVcpuCount || 8,
    minMemoryInGb: spec.minMemoryInGb || 30,
    startSsh: true,
    env: (spec.env || []).map(e => ({ key: e.key, value: e.value })),
  };
  if (spec.dockerArgs) input.dockerArgs = spec.dockerArgs;
  if (spec.ports) input.ports = spec.ports;
  if (spec.dataCenterId) input.dataCenterId = spec.dataCenterId;
  return input;
}

function gqlValue(v) {
  if (v === null || v === undefined) return "null";
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (Array.isArray(v)) return "[" + v.map(gqlValue).join(", ") + "]";
  if (typeof v === "object") return "{" + Object.entries(v).map(([k, x]) => `${k}: ${gqlValue(x)}`).join(", ") + "}";
  return JSON.stringify(String(v));
}

// GraphQL-Enums (cloudType) duerfen NICHT in Anfuehrungszeichen stehen.
function deployMutation(input) {
  const body = Object.entries(input).map(([k, v]) =>
    k === "cloudType" ? `${k}: ${v}` : `${k}: ${gqlValue(v)}`).join(", ");
  return `mutation { podFindAndDeployOnDemand(input: {${body}}) { id imageName desiredStatus machine { podHostId gpuTypeId } } }`;
}

function isNoGpuMessage(msg) {
  return /no.*(gpu|avail|capacity|resource|instance)|not.*available|out of stock|zero gpu/i.test(msg || "");
}

async function runpodGraphql(secrets, query) {
  let res, text;
  try {
    res = await fetch(`https://api.runpod.io/graphql?api_key=${secrets.runpodKey}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify({ query }),
    });
    text = await res.text();
  } catch (err) {
    return { ok: false, status: 0, data: { fetchError: err.message } };
  }
  let parsed;
  try { parsed = JSON.parse(text); } catch { parsed = { parseError: true, raw: text }; }
  return { ok: res.ok && !parsed?.errors, status: res.status, data: parsed };
}

async function capturePodSpec(secrets, podId) {
  const q = `query { pod(input: { podId: "${podId}" }) {
      id name imageName dockerArgs ports containerDiskInGb volumeInGb volumeMountPath env
      vcpuCount memoryInGb networkVolumeId
      machine { gpuTypeId dataCenterId secureCloud }
    } }`;
  const r = await runpodGraphql(secrets, q);
  if (!r.ok || !r.data?.data?.pod) {
    // Tolerant: einzelne Felder koennen je nach API-Version fehlen → zweiter,
    // schlanker Versuch mit den sicheren Feldern.
    const q2 = `query { pod(input: { podId: "${podId}" }) {
        id name imageName dockerArgs ports containerDiskInGb volumeMountPath env
        machine { gpuTypeId } } }`;
    const r2 = await runpodGraphql(secrets, q2);
    if (!r2.ok || !r2.data?.data?.pod) return { ok: false, error: r2.data || r.data };
    return { ok: true, pod: r2.data.data.pod, partial: true };
  }
  return { ok: true, pod: r.data.data.pod };
}

async function listNetworkVolumes(secrets) {
  const r = await runpodGraphql(secrets, `query { myself { networkVolumes { id name dataCenterId size } } }`);
  return r.ok ? (r.data?.data?.myself?.networkVolumes || []) : [];
}

async function deployPod(secrets, spec, dryRun) {
  const input = buildDeployInput(spec);
  const mutation = deployMutation(input);
  if (dryRun) {
    return { ok: true, dryRun: true, status: 200, newPodId: null,
             data: { dryRun: true, input, mutation } };
  }
  const r = await runpodGraphql(secrets, mutation);
  if (r.data?.errors) {
    const noGpu = r.data.errors.some(e => isNoGpuMessage(e.message));
    return { ok: false, status: r.status, data: r.data, noGpu };
  }
  const pod = r.data?.data?.podFindAndDeployOnDemand;
  if (!pod?.id) return { ok: false, status: r.status, data: r.data };
  return { ok: true, status: r.status, newPodId: pod.id, data: r.data };
}

async function terminatePod(secrets, podId, dryRun) {
  const mutation = `mutation { podTerminate(input: { podId: "${podId}" }) }`;
  if (dryRun) return { ok: true, dryRun: true, status: 200, data: { dryRun: true, mutation } };
  const r = await runpodGraphql(secrets, mutation);
  return { ok: r.ok, status: r.status, data: r.data };
}

async function lifecycleState(env) {
  const spec = await getSpec(env);
  let want = null, noServerSince = null;
  if (env.LOGS) {
    try { const w = await env.LOGS.get(WANT_RUNNING_KEY); want = w ? JSON.parse(w) : null; } catch {}
    try { const n = await env.LOGS.get(NO_SERVER_KEY); noServerSince = n ? Number(n) : null; } catch {}
  }
  return {
    mode: spec ? "redeploy" : "resume",
    dryRun: isDryRun(env),
    gpuTypeId: spec?.gpuTypeId || null,
    specCapturedAt: spec?.capturedAt || null,
    wantRunning: want,
    noServerSince,
  };
}

// ── /spec-Endpunkte ──
async function handleSpecGet(env, req) {
  const spec = await getSpec(env);
  let raw = null;
  if (!spec && env.LOGS) { try { raw = await env.LOGS.get(SPEC_KEY); } catch {} }
  return json({ ok: true, spec, invalidRaw: raw ? true : false, dryRun: isDryRun(env) }, { env, req });
}

async function handleSpecCapture(env, secrets, user, req) {
  if (!env.LOGS) return json({ error: "kv_unavailable" }, { status: 500, env, req });
  const podId = await getPodId(env, secrets);
  const cap = await capturePodSpec(secrets, podId);
  if (!cap.ok) return json({ error: "capture_failed", podId, detail: cap.error }, { status: 502, env, req });
  const extra = {};
  if (!cap.pod.networkVolumeId || !cap.pod.machine?.dataCenterId) {
    const vols = await listNetworkVolumes(secrets);
    if (vols.length === 1) { extra.networkVolumeId = vols[0].id; extra.dataCenterId = vols[0].dataCenterId; }
    else if (cap.pod.networkVolumeId) {
      const v = vols.find(x => x.id === cap.pod.networkVolumeId);
      if (v) extra.dataCenterId = v.dataCenterId;
    }
  }
  const spec = specFromPod(cap.pod, extra);
  const valid = isValidSpec(spec);
  await env.LOGS.put(SPEC_KEY, JSON.stringify(spec));
  await log(env, { time: new Date().toISOString(), action: "spec_capture", status: valid ? "OK" : "INCOMPLETE",
                   user, podId, partial: !!cap.partial, missing: missingSpecFields(spec) });
  await notify(secrets, valid
    ? `📋 Pod-Spec erfasst (${user}) aus ${podId}: ${spec.gpuTypeId}, Volume ${spec.networkVolumeId}. Lifecycle v2 (Terminate/Redeploy) ist ab jetzt AKTIV.`
    : `⚠️ Pod-Spec unvollstaendig (${user}): fehlt ${missingSpecFields(spec).join(", ")} — per /spec/set nachtragen. Lifecycle v2 bleibt INAKTIV.`);
  return json({ ok: true, valid, spec, partial: !!cap.partial, missing: missingSpecFields(spec) }, { env, req });
}

function missingSpecFields(spec) {
  const m = [];
  if (!spec?.imageName) m.push("imageName");
  if (!spec?.networkVolumeId) m.push("networkVolumeId");
  if (!spec?.gpuTypeId) m.push("gpuTypeId");
  return m;
}

async function handleSpecSet(req, env, user) {
  if (!env.LOGS) return json({ error: "kv_unavailable" }, { status: 500, env, req });
  let body; try { body = await req.json(); } catch { body = {}; }
  const incoming = body?.spec;
  if (!incoming || typeof incoming !== "object") return json({ error: "spec_required" }, { status: 400, env, req });
  const current = (await getSpec(env)) || {};
  const spec = { ...current, ...incoming, env: sanitizeEnv(incoming.env ?? current.env ?? []), updatedAt: new Date().toISOString() };
  const valid = isValidSpec(spec);
  await env.LOGS.put(SPEC_KEY, JSON.stringify(spec));
  await log(env, { time: new Date().toISOString(), action: "spec_set", status: valid ? "OK" : "INCOMPLETE", user, missing: missingSpecFields(spec) });
  return json({ ok: true, valid, spec, missing: missingSpecFields(spec) }, { env, req });
}

async function handleSpecClear(env, user, req) {
  if (env.LOGS) { try { await env.LOGS.delete(SPEC_KEY); } catch {} }
  await log(env, { time: new Date().toISOString(), action: "spec_clear", status: "OK", user });
  return json({ ok: true, mode: "resume" }, { env, req });
}

// ── /ensure — Start-on-Intent (B10) ──
function isNightBlocked(minutes) {
  return minutes >= HARD_FROM_MIN || minutes < HARD_TO_MIN;
}

// Retry-Fenster-Logik als reine Funktion (Jest-getestet).
//   want: {since, attempts, lastAttempt} | null   now: epoch ms
//   → { action: "attempt" | "wait" | "give_up", ... }
function retryDecision(want, now) {
  if (!want) return { action: "attempt", since: now, attempts: 1 };
  const elapsed = now - Number(want.since || now);
  if (elapsed > NO_GPU_RETRY_WINDOW_MS) return { action: "give_up", elapsed };
  if (now - Number(want.lastAttempt || 0) < NO_GPU_RETRY_MIN_GAP_MS) {
    return { action: "wait", retryInS: Math.ceil((NO_GPU_RETRY_MIN_GAP_MS - (now - Number(want.lastAttempt || 0))) / 1000) };
  }
  return { action: "attempt", since: Number(want.since), attempts: Number(want.attempts || 0) + 1 };
}

async function setWantRunning(env, want) {
  if (!env.LOGS) return;
  try {
    if (want) await env.LOGS.put(WANT_RUNNING_KEY, JSON.stringify(want));
    else await env.LOGS.delete(WANT_RUNNING_KEY);
  } catch {}
}

async function handleEnsure(env, ctx, secrets, user, req) {
  const now = Date.now();
  const minutes = localMinutes(now);
  const podId = await getPodId(env, secrets);

  // Laeuft schon etwas?
  const podsRes = await listPods(secrets);
  const running = podsRes.ok ? podsRes.pods.filter(p => String(p.desiredStatus).toUpperCase() === "RUNNING") : [];
  if (running.length > 0) {
    await setWantRunning(env, null);
    if (env.LOGS) { try { await env.LOGS.delete(NO_SERVER_KEY); } catch {} }
    // Boot-Phase erkennen: Startzeit juenger als DEPLOY_ETA_S → "starting"
    let since = 0; try { since = Number(await env.LOGS?.get("state:podRunningSince")) || 0; } catch {}
    const booting = since && (now - since) < DEPLOY_ETA_S * 1000;
    return json({ status: booting ? "starting" : "ok", podId: running[0].id, eta_s: booting ? Math.max(30, DEPLOY_ETA_S - Math.round((now - since) / 1000)) : 0 }, { env, req });
  }

  if (isNightBlocked(minutes)) {
    return json({ status: "blocked_night", until: "05:00" }, { env, req });
  }

  // Retry-Fenster
  let want = null;
  try { const w = await env.LOGS?.get(WANT_RUNNING_KEY); want = w ? JSON.parse(w) : null; } catch {}
  const decision = retryDecision(want, now);
  if (decision.action === "wait") {
    return json({ status: "starting", reason: "retry_pending", retry_in_s: decision.retryInS, eta_s: DEPLOY_ETA_S }, { env, req });
  }
  if (decision.action === "give_up") {
    await setWantRunning(env, null);
    if (env.LOGS) { try { await env.LOGS.put(NO_SERVER_KEY, String(now)); } catch {} }
    await log(env, { time: new Date().toISOString(), action: "ensure", status: "NO_SERVER", user, podId, elapsed_s: Math.round(decision.elapsed / 1000) });
    await notify(secrets, `🚫 Kein Server verfuegbar: ${Math.round(NO_GPU_RETRY_WINDOW_MS / 60000)} min lang keine ${REQUIRED_GPU_TYPE_ID} frei. Retry-Fenster beendet (${user}).`);
    return json({ status: "no_server", retry_allowed: true }, { env, req });
  }

  // Versuch
  const res = await execute("start", env, ctx, user, null, { quiet: true });
  let entry = null; try { entry = await res.json(); } catch {}
  const st = entry?.status || "UNKNOWN";
  if (st === "SUCCESS" || st === "ALREADY_RUNNING" || st.startsWith("DRY_RUN")) {
    await setWantRunning(env, null);
    if (env.LOGS) { try { await env.LOGS.delete(NO_SERVER_KEY); } catch {} }
    return json({ status: "starting", podId: entry?.podId || podId, eta_s: DEPLOY_ETA_S, dryRun: !!entry?.dryRun }, { env, req });
  }
  if (entry?.noGpu) {
    const w = { since: decision.since, attempts: decision.attempts, lastAttempt: now };
    await setWantRunning(env, w);
    if (w.attempts === 1) await notify(secrets, `⏳ Keine ${REQUIRED_GPU_TYPE_ID} frei — versuche bis zu ${Math.round(NO_GPU_RETRY_WINDOW_MS / 60000)} min weiter (${user}).`);
    return json({ status: "starting", reason: "no_gpu", attempt: w.attempts, retry_in_s: Math.round(NO_GPU_RETRY_MIN_GAP_MS / 1000), eta_s: DEPLOY_ETA_S }, { env, req });
  }
  if (st === "OTHER_RUNNING") return json({ status: "ok", podId: entry?.runningPods?.[0]?.id, note: "other_pod_running" }, { env, req });
  return json({ status: "error", detail: st }, { status: 502, env, req });
}

// Cron-Tick: laufendes Retry-Fenster weiterfuehren (falls die App nicht mehr pollt).
async function retryWantRunning(env, ctx, secrets, report) {
  if (!env.LOGS) return;
  let want = null;
  try { const w = await env.LOGS.get(WANT_RUNNING_KEY); want = w ? JSON.parse(w) : null; } catch {}
  if (!want) return;
  if (report && report.status !== "stopped") { await setWantRunning(env, null); return; }
  const fakeReq = null;
  await handleEnsure(env, ctx, secrets, "cron-retry", fakeReq);
}

// ─────────────────────────────────────────────
// RUNPOD CALL
// ─────────────────────────────────────────────

async function runPod(action, secrets, podId) {
  const { runpodKey } = secrets;

  const query =
    action === "start"
      ? `
        mutation {
          podResume(input: { podId: "${podId}", gpuCount: 1 }) {
            id
            desiredStatus
          }
        }
      `
      : `
        mutation {
          podStop(input: { podId: "${podId}" }) {
            id
            desiredStatus
          }
        }
      `;

  let res, text;
  try {
    res = await fetch(`https://api.runpod.io/graphql?api_key=${runpodKey}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Accept": "application/json"
      },
      body: JSON.stringify({ query: query.trim() })
    });
    text = await res.text();
  } catch (err) {
    console.log("RUNPOD FETCH ERROR:", err);
    return { ok: false, status: 0, data: { fetchError: err.message } };
  }

  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    parsed = { parseError: true, raw: text };
  }

  console.log("RUNPOD STATUS:", res.status);
  console.log("RUNPOD RESPONSE:", parsed);

  if (parsed?.errors) {
    console.log("GRAPHQL ERRORS:", parsed.errors);
    // No-GPU-Fehler von RunPod erkennen
    const gpuError = parsed.errors.some(e =>
      /no.*(gpu|avail|capacity|resource)/i.test(e.message ?? "")
    );
    if (gpuError) {
      return { ok: false, status: res.status, data: parsed, noGpu: true };
    }
  }

  return {
    ok: res.ok,
    status: res.status,
    data: parsed
  };
}

// ─────────────────────────────────────────────
// POD-LISTE (Account-weit) — Doppelstart-Erkennung
// ─────────────────────────────────────────────

async function listPods(secrets) {
  const { runpodKey } = secrets;

  const query = `
    query {
      myself {
        pods {
          id
          name
          desiredStatus
          runtime { gpus { id } }
        }
      }
    }
  `;

  let res, text;
  try {
    res = await fetch(`https://api.runpod.io/graphql?api_key=${runpodKey}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: query.trim() })
    });
    text = await res.text();
  } catch (err) {
    console.log("LISTPODS FETCH ERROR:", err);
    return { ok: false, error: `fetch: ${err.message}`, pods: [] };
  }

  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { ok: false, error: "parse_error", pods: [] };
  }

  if (parsed?.errors) {
    console.log("LISTPODS GRAPHQL ERRORS:", parsed.errors);
    return { ok: false, error: "graphql_error", pods: [] };
  }

  const rawPods = parsed?.data?.myself?.pods ?? [];
  const pods = rawPods.map(p => ({
    id: p.id,
    name: p.name ?? null,
    desiredStatus: p.desiredStatus ?? "NO_STATUS",
    hasGpu: (p.runtime?.gpus?.length ?? 0) > 0,
  }));

  return { ok: true, pods };
}

async function handlePods(env, secrets, req) {
  const podId = await getPodId(env, secrets);
  const podsRes = await listPods(secrets);
  if (!podsRes.ok) {
    return json(
      { ok: false, error: podsRes.error, trackedPodId: podId, pods: [] },
      { status: 502, env, req }
    );
  }
  return json({ ok: true, trackedPodId: podId, pods: podsRes.pods }, { env, req });
}

// ─────────────────────────────────────────────
// SELF-CHECK (Backend /api/selfcheck proxien + Cron-Reporting)
// ─────────────────────────────────────────────

async function fetchBackendSelfcheck() {
  try {
    const res = await fetch(`${BACKEND_BASE}/api/selfcheck`,
      { signal: AbortSignal.timeout(15000), headers: { "Accept": "application/json" } });
    const text = await res.text();
    try {
      return JSON.parse(text);
    } catch {
      const snippet = (text || "").replace(/\s+/g, " ").trim().slice(0, 160);
      return { status: "down", detail: `ungueltige Antwort (HTTP ${res.status}): ${snippet || "leer"}` };
    }
  } catch (e) {
    return { status: "down", detail: "Backend nicht erreichbar: " + e.message };
  }
}

// UI: liefert den Live-Report. Pod aus → "stopped" (kein Fehlalarm).
// Boot-Fenster: Zeit nach Pod-Start, in der ein (noch) nicht bereites Backend als
// "starting" statt "down" gilt (uvicorn + Ollama-Modelle laden). Grosszuegig gewaehlt.
const STARTUP_GRACE_MS = 240000;  // 4 min

// KV ist eventually consistent: ein get() direkt nach put() kann noch den alten Wert
// liefern. Ohne dieses Isolate-Memo wuerde podRunningSince bei jedem Poll neu auf
// "jetzt" gesetzt → das Grace-Fenster liefe nie ab ("Startup (0s)" fuer immer).
let sinceMemo = 0;

async function getRunningSince(env) {
  let since = 0;
  if (env.LOGS) { try { since = parseInt(await env.LOGS.get("state:podRunningSince") || "0", 10) || 0; } catch {} }
  if (!since && sinceMemo) since = sinceMemo;          // stale KV-Lesung → Memo gewinnt
  if (!since) {
    since = Date.now();                                // erstmals laufend gesehen
    if (env.LOGS) { try { await env.LOGS.put("state:podRunningSince", String(since)); } catch {} }
  }
  sinceMemo = since;
  return since;
}

// Zentraler Self-Check-Report inkl. Startup-Erkennung. Von UI (/selfcheck) und Cron genutzt.
async function getSelfcheckReport(env, secrets) {
  const podId = await getPodId(env, secrets);
  const { status: podStatus } = await getState(secrets, podId);

  if (String(podStatus).toUpperCase() !== "RUNNING") {
    sinceMemo = 0;
    if (env.LOGS) { try { await env.LOGS.put("state:podRunningSince", "0"); } catch {} }
    return { status: "stopped", podStatus, detail: "Pod laeuft nicht" };
  }

  const report = await fetchBackendSelfcheck();
  const hasReal = !!(report && report.checks && Object.keys(report.checks).length > 0);

  // Startup-Signal: bevorzugt die echte Backend-Uptime (zuverlaessig), sonst der
  // KV-Zeitstempel (noetig, solange das Backend gar nicht antwortet).
  let elapsed, source;
  if (report && typeof report.uptime_sec === "number") {
    elapsed = report.uptime_sec * 1000;
    source = "uptime";
    sinceMemo = Date.now() - elapsed;                  // Memo mit der echten Uptime synchronisieren
  } else {
    elapsed = Date.now() - (await getRunningSince(env));
    source = "kv";
  }
  const withinGrace = elapsed < STARTUP_GRACE_MS;

  // Im Boot-Fenster: Backend nicht erreichbar ODER noch nicht "ok" → "starting" statt Stoerung.
  if (withinGrace && (!hasReal || report.status !== "ok")) {
    return {
      status: "starting",
      detail: "Backend startet\u2026 (" + Math.round(elapsed / 1000) + "s)",
      elapsedSec: Math.round(elapsed / 1000),
      graceSource: source,
      checks: hasReal ? report.checks : undefined,
    };
  }
  return report;
}

// UI: liefert den Report (inkl. "starting"/"stopped").
async function handleSelfcheck(env, secrets, req) {
  const report = await getSelfcheckReport(env, secrets);
  return json(report, { env, req });
}

// Cron: nur wenn Pod laeuft; Telegram nur bei Statuswechsel (De-Dup ueber KV).
// report/secrets werden vom Tick durchgereicht, damit Selfcheck und Reconciler
// sich EINEN Backend-Call teilen. Ohne Argumente weiterhin eigenstaendig lauffaehig.
async function cronSelfcheck(env, ctx, report = null, secretsIn = null) {
  const secrets = secretsIn || await getSecrets(env);
  const rep = report || await getSelfcheckReport(env, secrets);
  const newStatus = rep.status || "down";

  if (newStatus === "stopped") {
    if (env.LOGS) await env.LOGS.put("state:selfcheckStatus", "stopped");
    return;   // Pod bewusst aus → kein Alarm
  }

  let prev = null;
  if (env.LOGS) {
    try { prev = await env.LOGS.get("state:selfcheckStatus"); } catch {}
    await env.LOGS.put("state:selfcheckStatus", newStatus);
    await env.LOGS.put("state:selfcheck", JSON.stringify(rep));
  }

  // Telegram nur bei echtem Statuswechsel; "starting"/"stopped" sind erwartet → kein Alarm.
  if (prev && prev !== newStatus && prev !== "stopped" && prev !== "starting" && newStatus !== "starting") {
    const emoji = newStatus === "ok" ? "✅" : (newStatus === "degraded" ? "⚠️" : "🚨");
    let detail = "";
    if (rep.checks) {
      const bad = Object.keys(rep.checks).filter(k => rep.checks[k] && rep.checks[k].ok === false);
      if (bad.length) detail = "\nBetroffen: " + bad.join(", ");
    }
    await notify(secrets, `${emoji} Self-Check: ${prev} → ${newStatus}${detail}`);
  }
}

// ─────────────────────────────────────────────
// RECONCILER — Idle-Auto-Stopp (v19.10)
// ─────────────────────────────────────────────
//
// Level-triggered: jeder Tick vergleicht Soll gegen Ist und handelt nur bei
// Divergenz. Der Reconciler STARTET nie — der Start erfolgt manuell ueber das
// Pod-Start-Makro. Damit kann eine Fehlentscheidung hier nie GPU-Kosten
// erzeugen, nur beenden.

// Intl loest Sommer-/Winterzeit selbst auf — deshalb steht hier keine einzige
// UTC-Offset-Rechnung. workerd bringt volles ICU mit.
const _localFmt = new Intl.DateTimeFormat("en-GB", {
  timeZone: TZ, hour12: false, hour: "2-digit", minute: "2-digit",
});

function localMinutes(nowMs) {
  const parts = _localFmt.formatToParts(new Date(nowMs));
  const pick = (t) => parseInt(parts.find(p => p.type === t)?.value ?? "0", 10);
  let h = pick("hour");
  if (h === 24) h = 0;            // en-GB liefert Mitternacht je nach ICU als "24"
  return h * 60 + pick("minute");
}

// "hard" = bedingungslos stoppen; sonst Idle-Schwelle in Sekunden.
function policyFor(minutes) {
  if (minutes >= HARD_FROM_MIN || minutes < HARD_TO_MIN) return "hard";
  if (minutes >= 8 * 60 && minutes < 18 * 60) return IDLE_LONG_SEC;
  return IDLE_SHORT_SEC;
}

// KV ist eventually consistent — dasselbe Problem wie bei podRunningSince:
// ein get() direkt nach put() kann den alten Wert liefern und die Kulanz
// wuerde bei jedem Tick neu beginnen, also nie ablaufen.
let hardGraceMemo = 0;

async function hardGraceSince(env) {
  let since = 0;
  if (env.LOGS) {
    try { since = parseInt(await env.LOGS.get(HARD_GRACE_KEY) || "0", 10) || 0; } catch {}
  }
  if (!since && hardGraceMemo) since = hardGraceMemo;
  if (!since) {
    since = Date.now();
    if (env.LOGS) { try { await env.LOGS.put(HARD_GRACE_KEY, String(since)); } catch {} }
  }
  hardGraceMemo = since;
  return since;
}

async function clearHardGrace(env) {
  hardGraceMemo = 0;
  if (!env.LOGS) return;
  // Bewusst KEIN Kurzschluss ueber hardGraceMemo: in einem frischen Isolate
  // ist das Memo immer 0, ein in KV stehengebliebener Zeitstempel wuerde dann
  // nie geloescht. Beim naechsten Eintritt in die harte Phase gaelte die
  // Kulanz sofort als abgelaufen — Variante (b) waere still zu (a) degradiert.
  // Stattdessen lesen (billig) und nur bei Bedarf schreiben.
  try {
    const cur = await env.LOGS.get(HARD_GRACE_KEY);
    if (cur && cur !== "0") await env.LOGS.put(HARD_GRACE_KEY, "0");
  } catch {}
}

async function stopViaReconciler(env, ctx, secrets, reason) {
  const res = await execute("stop", env, ctx, "auto", null, { quiet: true });

  let entry = null;
  try { entry = await res.json(); } catch {}
  const status = entry?.status || "UNKNOWN";
  const ok = status === "SUCCESS" || status === "ALREADY_STOPPED";

  let prev = null;
  if (env.LOGS) {
    try { prev = await env.LOGS.get(RECONCILE_KEY); } catch {}
    try { await env.LOGS.put(RECONCILE_KEY, ok ? "OK" : status); } catch {}
  }

  if (ok) {
    // Erfolg meldet immer — passiert hoechstens einmal pro Tag, danach ist
    // der Pod EXITED und der Reconciler steigt sofort wieder aus.
    if (status === "SUCCESS") {
      await notify(secrets, `🌙 Auto-Stopp (${entry?.lifecycle === "redeploy" ? "terminate" : "stop"}): ${reason}\nRUNNING → ${entry?.stateAfter || "?"}`);
    }
  } else if (prev !== status) {
    // Fehlschlag nur bei Statuswechsel — sonst alle 15 Minuten dasselbe.
    await notify(secrets, `🚨 Auto-Stopp fehlgeschlagen (${status})\nGrund des Stopps: ${reason}`);
  }
}

async function reconcile(env, ctx, secrets, report) {
  // Pod aus → nichts zu tun. Auch die Kulanz wird zurueckgesetzt, damit sie
  // beim naechsten Lauf frisch beginnt.
  if (!report || report.status === "stopped") {
    await clearHardGrace(env);
    return;
  }

  const policy = policyFor(localMinutes(Date.now()));
  const act = report.activity;
  const haveAct = !!(act && act.ok === true);
  const idle = haveAct && typeof act.idle_sec === "number" ? act.idle_sec : null;

  if (policy === "hard") {
    // "starting" zaehlt als aktiv: der Pod wurde gerade erst hochgefahren
    // (Backend-Uptime < 4 min). Ihn binnen 15 Minuten wieder abzuschiessen,
    // bevor er ueberhaupt nutzbar ist, waere absurd.
    const busy =
      report.status === "starting" ||
      (haveAct && (act.active_jobs > 0 || (idle !== null && idle < HARD_GRACE_IDLE_SEC)));

    if (busy && Date.now() - (await hardGraceSince(env)) < HARD_GRACE_MS) return;

    await clearHardGrace(env);
    await stopViaReconciler(env, ctx, secrets, "Nachtabschaltung (23:00–05:00)");
    return;
  }

  await clearHardGrace(env);

  // Degradations-Fallback: ohne verlaessliche Telemetrie kein Idle-Stopp.
  // Die harte Nachtphase oben greift trotzdem — das Modell faellt damit auf
  // das alte Verhalten zurueck, nie auf "laeuft unbemerkt durch".
  if (!haveAct || idle === null) return;
  if (act.active_jobs > 0) return;
  if (idle < policy) return;

  await stopViaReconciler(env, ctx, secrets,
    `idle ${Math.round(idle / 60)} min (Schwelle ${Math.round(policy / 60)} min)`);
}

// ─────────────────────────────────────────────
// STATE
// ─────────────────────────────────────────────

async function getState(secrets, podId) {
  const { runpodKey } = secrets;

  const query = `
    query {
      pod(input: { podId: "${podId}" }) {
        id
        desiredStatus
        runtime {
          gpus {
            id
          }
        }
      }
    }
  `;

  let res, text;
  try {
    res = await fetch(`https://api.runpod.io/graphql?api_key=${runpodKey}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: query.trim() })
    });
    text = await res.text();
  } catch (err) {
    console.log("GETSTATE FETCH ERROR:", err);
    return { status: "FETCH_ERROR", hasGpu: false };
  }

  try {
    const json = JSON.parse(text);
    if (json?.errors) return { status: "ERROR", hasGpu: false };
    const pod = json?.data?.pod;
    return {
      status: pod?.desiredStatus || "NO_STATUS",
      hasGpu: (pod?.runtime?.gpus?.length ?? 0) > 0,
    };
  } catch {
    return { status: "PARSE_ERROR", hasGpu: false };
  }
}

// ─────────────────────────────────────────────
// TEST RUN
// ─────────────────────────────────────────────

async function triggerTestRun(env, ctx, req) {
  const secrets = await getSecrets(env);
  const podId = await getPodId(env, secrets);

  // Erst pruefen, ob der Pod wirklich laeuft
  const { status } = await getState(secrets, podId);
  if (status !== "RUNNING") {
    const msg = `⚠️ Cannot run tests — server is ${status}, not RUNNING.`;
    await notify(secrets, msg);
    return json({ error: msg }, { status: 409, env, req });
  }

  await notify(secrets, "🧪 Test run triggered, waiting for results...");

  // Fire and forget — HTTP-Antwort kommt sofort zurueck
  ctx?.waitUntil?.(runTests(secrets));

  return json({ status: "TEST_STARTED" }, { env, req });
}

async function runTests(secrets) {
  const TEST_URL = `${BACKEND_BASE}/api/testrun`;
  const TIMEOUT_MS = 300_000; // max 5 Minuten fuer die Test-Suite

  let res, text;
  try {
    res = await fetch(TEST_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: AbortSignal.timeout(TIMEOUT_MS)
    });
    text = await res.text();
  } catch (err) {
    await notify(secrets, `❌ Test run failed — could not reach backend:\n${err.message}`);
    return;
  }

  let result;
  try {
    result = JSON.parse(text);
  } catch {
    result = { output: text };
  }

  const output = result?.output ?? text;

  // pytest-Summary parsen, z.B. "5 failed, 95 passed in 12.3s"
  const summaryMatch = output.match(/={3,}\s*(.+?)\s*={3,}\s*$/m);
  const summaryLine = summaryMatch?.[1] ?? "";

  const totalPassed = parseInt(summaryLine.match(/(\d+)\s+passed/)?.[1] ?? 0);
  const totalFailed = parseInt(summaryLine.match(/(\d+)\s+failed/)?.[1] ?? 0);
  const total = (totalPassed + totalFailed) || "?";

  const failedNames = [...output.matchAll(/^FAILED\s+\S+::(\S+)/gm)].map(m => m[1]);

  let msg;
  if (totalFailed === 0) {
    msg = `✅ All green! 0 of ${total} tests failed.`;
  } else {
    const names = failedNames.length ? `\n(${failedNames.join(", ")})` : "";
    msg = `❌ Ouch! ${totalFailed} of ${total} tests failed.${names}`;
  }

  await notify(secrets, msg);
}

// ─────────────────────────────────────────────
// DEBUG (leakt nur Metadaten; hinter Auth)
// ─────────────────────────────────────────────

async function debugAuth(env, req) {
  const results = {};

  let secrets;
  try {
    secrets = await getSecrets(env);
  } catch (err) {
    return json({ secretsError: err.message }, { status: 500, env, req });
  }

  const { runpodKey, telegramToken, telegramChatId, confluenceSecret } = secrets;
  const podId = await getPodId(env, secrets);

  results.env = {
    RUNPOD_API_KEY: runpodKey
      ? `set (${runpodKey.length} chars, starts: ${runpodKey.slice(0, 6)}...)`
      : "MISSING",
    RUNPOD_POD_ID_effective: podId || "MISSING",
    CONFLUENCE_SHARED_SECRET: confluenceSecret ? `set (${confluenceSecret.length} chars)` : "MISSING",
    CONFLUENCE_ORIGIN: env.CONFLUENCE_ORIGIN || "MISSING",
    TELEGRAM_BOT_TOKEN: telegramToken ? `set (${telegramToken.length} chars)` : "MISSING",
    TELEGRAM_CHAT_ID: telegramChatId || "MISSING",
    LOGS_KV: env.LOGS ? "bound" : "MISSING",
  };

  try {
    const res = await fetch(`https://api.runpod.io/graphql?api_key=${runpodKey}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: `{ __typename }` })
    });
    results.queryParam = { status: res.status, raw: await res.text() };
  } catch (err) {
    results.queryParam = { fetchError: err.message };
  }

  try {
    const res = await fetch(`https://api.runpod.io/graphql?api_key=${runpodKey}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query: `query { pod(input: { podId: "${podId}" }) { id desiredStatus } }`
      })
    });
    results.podQuery = { status: res.status, raw: await res.text() };
  } catch (err) {
    results.podQuery = { fetchError: err.message };
  }

  return json(results, { env, req });
}

// ─────────────────────────────────────────────
// HEALTH CHECK / WAIT FOR BACKEND
// ─────────────────────────────────────────────

async function waitForBackend(secrets, runpodResult) {
  const HEALTH_URL = `${BACKEND_BASE}/api/health`;
  const INITIAL_WAIT_MS = 60_000;   // 60s initialer Boot-Wait
  const POLL_INTERVAL_MS = 30_000;  // 30s zwischen Versuchen
  const MAX_ATTEMPTS = 14;          // 14 × 30s = 7min Polling = 8min gesamt

  // No-GPU sofort aus der Mutation-Antwort erkennen
  if (runpodResult?.noGpu) {
    await notify(secrets, "🚫 No GPU available — RunPod could not allocate a GPU for the pod. Try again later.");
    return false;
  }

  await notify(secrets, "⏳ Server starting up, waiting for backend...");

  await sleep(INITIAL_WAIT_MS);

  // Nach dem initialen Wait Pod-State pruefen — noch EXITED ⇒ keine GPU bekommen
  const podState = await getState(secrets, await podIdFromSecrets(secrets));
  console.log(`Pod state after initial wait: ${JSON.stringify(podState)}`);

  if (podState.status === "EXITED") {
    await notify(secrets,
      "🚫 No GPU available — pod is still EXITED after 60s.\n" +
      "RunPod could not allocate a GPU. Try again later or check RunPod GPU availability."
    );
    return false;
  }

  for (let i = 1; i <= MAX_ATTEMPTS; i++) {
    const elapsed = 60 + (i - 1) * 30;
    console.log(`Health check attempt ${i}/${MAX_ATTEMPTS} (after ${elapsed}s)...`);

    try {
      const res = await fetch(HEALTH_URL, { signal: AbortSignal.timeout(15_000) });
      const text = await res.text();

      if (res.ok && text.includes('"status":"ok"')) {
        console.log(`Backend healthy after ~${elapsed}s`);
        await notify(secrets, `✅ Server ready! Backend responded after ~${elapsed}s`);
        return true;
      }
    } catch (err) {
      console.log(`Health check ${i} failed: ${err.message}`);
    }

    // Alle 3 Versuche Pod-State nachpruefen (spaete GPU-Fehler abfangen)
    if (i % 3 === 0) {
      const midState = await getState(secrets, await podIdFromSecrets(secrets));
      if (midState.status === "EXITED") {
        await notify(secrets, "🚫 Pod returned to EXITED during startup — likely no GPU was available or pod crashed.");
        return false;
      }
    }

    if (i < MAX_ATTEMPTS) {
      await sleep(POLL_INTERVAL_MS);
    }
  }

  await notify(secrets,
    "⚠️ Backend did not respond within 8 minutes.\n" +
    "Possible causes:\n" +
    "• No GPU available on RunPod — check GPU availability\n" +
    "• Model still loading (grosse Modelle brauchen laenger unter Last)\n" +
    "• runpod-start.sh failed → check /workspace/backend.log\n" +
    "• Cloudflare tunnel not started → check /workspace/cloudflared.log"
  );
  return false;
}

// waitForBackend haelt kein env — Pod-ID hier nur aus dem Secret ableitbar.
// (Der Health-Poll ist unkritisch gegenueber einem frischen KV-Swap.)
async function podIdFromSecrets(secrets) {
  return secrets.podId;
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// ─────────────────────────────────────────────
// NOTIFY — Benachrichtigungen im Auftrag des Backends (Sprint F2)
// ─────────────────────────────────────────────
//
// Warum ueber den Worker statt direkt vom Pod: die Telegram-Credentials
// bleiben ausschliesslich hier im Secrets Store. Der Pod - die Maschine mit
// den Klientendaten - haelt kein Telegram-Secret; er signiert nur mit dem
// ohnehin vorhandenen CONFLUENCE_SHARED_SECRET.
//
// SICHERHEIT - bewusst KEIN Freitext-Relay:
// Das Confluence-Makro signiert im Browser, das Shared Secret steht also im
// Seitenquelltext. Wer es liest, kann jeden Endpunkt hier aufrufen (gilt
// ebenso fuer /start und /stop - das Bedrohungsmodell aendert sich nicht).
// Damit daraus kein beliebiger Nachrichtenkanal in den Admin-Chat wird,
// baut der Worker den Text SELBST aus strukturierten, validierten Feldern.
// Ein "text"-Feld im Body wird ignoriert.
//
// DSGVO: der Feedback-Freitext verlaesst den Pod nie - er steht nur in
// feedback.log auf EU-Infrastruktur. Hier kommen ausschliesslich Rating,
// Workflow, Job-ID und Login an.

async function handleNotify(req, env, secrets, user) {
  if (req.method !== "POST") {
    return json({ error: "method_not_allowed" }, { status: 405, env, req });
  }

  let body;
  try {
    body = await req.json();
  } catch {
    return json({ error: "invalid_json" }, { status: 400, env, req });
  }

  const kind = String(body?.kind || "");
  if (kind !== "feedback") {
    return json({ error: "unknown_kind", kind }, { status: 400, env, req });
  }

  const rating = Number(body?.rating);
  if (!Number.isInteger(rating) || rating < 1 || rating > 5) {
    return json({ error: "invalid_rating" }, { status: 400, env, req });
  }

  // Freitextfelder werden NICHT uebernommen; nur kurze Kennungen, hart
  // begrenzt und auf unverdaechtige Zeichen reduziert.
  const clean = (v, max) => String(v ?? "").replace(/[^\w.\-:@]/g, "").slice(0, max);
  const workflow = clean(body?.workflow, 40) || "?";
  const jobId    = clean(body?.jobId, 8) || "?";
  const from     = clean(body?.user, 64) || clean(user, 64) || "?";

  const stars = "\u2b50".repeat(rating);
  await notify(secrets,
    `${stars} ${rating}/5 Feedback\nWorkflow: ${workflow}\nJob: ${jobId}\nVon: ${from}`);

  await log(env, { action: "notify", status: "SUCCESS", user,
                   detail: `feedback ${rating}/5 (${workflow})` });

  return json({ ok: true }, { env, req });
}

// ─────────────────────────────────────────────
// TELEGRAM
// ─────────────────────────────────────────────

async function notify(secrets, text) {
  const { telegramToken, telegramChatId } = secrets;
  if (!telegramToken || !telegramChatId) return;

  try {
    await fetch(
      `https://api.telegram.org/bot${telegramToken}/sendMessage`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: telegramChatId, text })
      }
    );
  } catch (e) {
    console.log("Telegram error:", e);
  }
}

// ─────────────────────────────────────────────
// LOGGING (KV) — Statusprotokoll, Single Source
// ─────────────────────────────────────────────

const LOG_KEY = "state:log";
const LOG_MAX = 100;

async function log(env, entry) {
  if (!env.LOGS) return;
  try {
    if (!entry.time) entry.time = new Date().toISOString();
    let arr = [];
    try { const raw = await env.LOGS.get(LOG_KEY); if (raw) arr = JSON.parse(raw) || []; } catch {}
    if (!Array.isArray(arr)) arr = [];
    arr.unshift(entry);                              // neueste zuerst
    if (arr.length > LOG_MAX) arr = arr.slice(0, LOG_MAX);
    await env.LOGS.put(LOG_KEY, JSON.stringify(arr));
  } catch (e) {
    console.log("Log error:", e);
  }
}

async function getLogs(env, req) {
  if (!env.LOGS) {
    return json({ error: "kv_unavailable" }, { status: 500, env, req });
  }
  let arr = [];
  try { const raw = await env.LOGS.get(LOG_KEY); if (raw) arr = JSON.parse(raw) || []; } catch {}
  if (!Array.isArray(arr)) arr = [];
  return json(arr.slice(0, 50), { env, req });   // neueste 50 (bereits neueste zuerst)
}

// v19.21: reine Funktionen fuer Unit-Tests (frontend/tests/worker_lifecycle.test.js)
export { buildDeployInput, deployMutation, sanitizeEnv, specFromPod, isValidSpec, isNoGpuMessage, retryDecision, isNightBlocked, missingSpecFields };
