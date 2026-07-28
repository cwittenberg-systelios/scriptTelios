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
const BACKEND_BASE = "https://scriptelios.win";

const AUTH_WINDOW_SEC = 300;         // Replay-Fenster (== AUTH_TIMESTAMP_WINDOW_SEC)
const POD_ID_KEY = "state:podId";    // KV-Key fuer die mutable Pod-ID

export default {
  async scheduled(event, env, ctx) {
    try {
      const action = getAction(event.cron);
      if (action === "selfcheck") { await cronSelfcheck(env, ctx); return; }
      if (!action) return;
      // Cron laeuft intern — keine Auth, User = "cron", kein Request-Objekt.
      await execute(action, env, ctx, "cron", null);
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

function getAction(cron) {
  if (cron === "0 7 * * 1-5") return "start";
  if (cron === "0 18 * * 1-5") return "stop";
  if (cron === "*/15 * * * *") return "selfcheck";
  return null;
}

// ─────────────────────────────────────────────
// MAIN EXECUTION (start/stop)
// ─────────────────────────────────────────────

async function execute(action, env, ctx, user, req) {
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
  if (action === "stop" && stateBefore === "EXITED") {
    await notify(secrets, "ℹ️ Server is already stopped, nothing to do.");
    const e = { action, status: "ALREADY_STOPPED", user, podId };
    await log(env, e);
    return json(e, { env, req });
  }

  const result = await runPod(action, secrets, podId);
  const { status: stateAfter } = await getState(secrets, podId);

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
    action, status, user, podId,
    stateBefore, stateAfter, noGpu, runpod: result,
  };

  await log(env, entry);

  // Startzeit fuer die Startup-Erkennung tracken (getSelfcheckReport liest sie).
  if (env.LOGS && status === "SUCCESS") {
    try {
      if (action === "start") { sinceMemo = Date.now(); await env.LOGS.put("state:podRunningSince", String(sinceMemo)); }
      if (action === "stop")  { sinceMemo = 0; await env.LOGS.put("state:podRunningSince", "0"); }
    } catch {}
  }

  if (status === "SUCCESS" && action === "start") {
    // Fire-and-forget Health-Poll — laeuft dank ctx auch bei manuellem Start.
    ctx?.waitUntil?.(waitForBackend(secrets, result));
  } else {
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
async function cronSelfcheck(env, ctx) {
  const secrets = await getSecrets(env);
  const rep = await getSelfcheckReport(env, secrets);
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
