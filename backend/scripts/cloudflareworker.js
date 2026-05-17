export default {
  async scheduled(event, env, ctx) {
    try {
      const action = getAction(event.cron);
      if (!action) return;

      await execute(action, env, ctx);
    } catch (err) {
      console.log("Scheduled error:", err);
      try {
        const secrets = await getSecrets(env);
        await notify(secrets, `❌ Scheduled crash: ${err.message}`);
      } catch {}
    }
  },

  async fetch(req, env) {
    try {
      const url = new URL(req.url);

      if (url.pathname === "/start") return await execute("start", env, ctx);
      if (url.pathname === "/stop") return await execute("stop", env, ctx);
      if (url.pathname === "/logs") return await getLogs(env);
      if (url.pathname === "/debug") return await debugAuth(env);
      if (url.pathname === "/testrun") return await triggerTestRun(env, ctx);

      return new Response("RunPod Scheduler OK", { status: 200 });
    } catch (err) {
      console.log("Fetch error:", err);
      return new Response(err.message, { status: 500 });
    }
  }
};

// ─────────────────────────────────────────────
// SECRETS (Cloudflare Secrets Store — async .get())
// ─────────────────────────────────────────────

async function getSecrets(env) {
  const resolve = async (name) => {
    const binding = env[name];
    if (!binding) return null;
    if (typeof binding.get === "function") return await binding.get();
    if (typeof binding === "string") return binding;
    return null;
  };

  const [runpodKey, podId, telegramToken, telegramChatId] = await Promise.all([
    resolve("RUNPOD_API_KEY"),
    resolve("RUNPOD_POD_ID"),
    resolve("TELEGRAM_BOT_TOKEN"),
    resolve("TELEGRAM_CHAT_ID"),
  ]);

  return {
    runpodKey: runpodKey?.trim() ?? null,
    podId: podId?.trim() ?? null,
    telegramToken: telegramToken?.trim() ?? null,
    telegramChatId: telegramChatId?.trim() ?? null,
  };
}

// ─────────────────────────────────────────────
// CRON
// ─────────────────────────────────────────────

function getAction(cron) {
  if (cron === "0 7 * * 1-5") return "start";
  if (cron === "0 18 * * 1-5") return "stop";
  return null;
}

// ─────────────────────────────────────────────
// MAIN EXECUTION
// ─────────────────────────────────────────────

async function execute(action, env, ctx) {
  const secrets = await getSecrets(env);

  const { status: stateBefore } = await getState(secrets);

  // Short-circuit if already in the desired state
  if (action === "start" && stateBefore === "RUNNING") {
    await notify(secrets, "ℹ️ Server is already running, nothing to do.");
    return new Response(JSON.stringify({ action, status: "ALREADY_RUNNING" }, null, 2), {
      headers: { "content-type": "application/json" }
    });
  }
  if (action === "stop" && stateBefore === "EXITED") {
    await notify(secrets, "ℹ️ Server is already stopped, nothing to do.");
    return new Response(JSON.stringify({ action, status: "ALREADY_STOPPED" }, null, 2), {
      headers: { "content-type": "application/json" }
    });
  }

  const result = await runPod(action, secrets);
  const { status: stateAfter } = await getState(secrets);

  if (result?.status === 401) {
    const entry = {
      time: new Date().toISOString(),
      action,
      status: "AUTH_FAILED",
      stateBefore,
      stateAfter,
      runpod: result
    };
    await log(env, entry);
    await notify(secrets, `🚨 ${action.toUpperCase()} AUTH_FAILED\nCheck RUNPOD_API_KEY in Secrets Store`);
    return new Response(JSON.stringify(entry, null, 2), {
      status: 401,
      headers: { "content-type": "application/json" }
    });
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

  const entry = {
    time: new Date().toISOString(),
    action,
    status,
    stateBefore,
    stateAfter,
    runpod: result
  };

  await log(env, entry);

  if (status === "SUCCESS" && action === "start") {
    // Fire-and-forget health poll — runs async so HTTP response returns immediately
    ctx?.waitUntil?.(waitForBackend(secrets, result));
  } else {
    await notify(secrets,
      status === "SUCCESS"
        ? `✅ ${action.toUpperCase()} OK\n${stateBefore} → ${stateAfter}`
        : `❌ ${action.toUpperCase()} ${status}\n${JSON.stringify(result?.data)}`
    );
  }

  return new Response(JSON.stringify(entry, null, 2), {
    headers: { "content-type": "application/json" }
  });
}

// ─────────────────────────────────────────────
// RUNPOD CALL
// ─────────────────────────────────────────────

async function runPod(action, secrets) {
  const { runpodKey, podId } = secrets;

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
    // Detect GPU availability errors from RunPod
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
// STATE
// ─────────────────────────────────────────────

async function getState(secrets) {
  const { runpodKey, podId } = secrets;

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
    return "FETCH_ERROR";
  }

  try {
    const json = JSON.parse(text);
    if (json?.errors) return { status: "ERROR" };
    const pod = json?.data?.pod;
    return {
      status: pod?.desiredStatus || "NO_STATUS",
      hasGpu: (pod?.runtime?.gpus?.length ?? 0) > 0,
    };
  } catch {
    return { status: "PARSE_ERROR" };
  }
}

// ─────────────────────────────────────────────
// TEST RUN
// ─────────────────────────────────────────────

async function triggerTestRun(env, ctx) {
  const secrets = await getSecrets(env);

  // Check pod is actually running first
  const { status } = await getState(secrets);
  if (status !== "RUNNING") {
    const msg = `⚠️ Cannot run tests — server is ${status}, not RUNNING.`;
    await notify(secrets, msg);
    return new Response(JSON.stringify({ error: msg }, null, 2), {
      status: 409,
      headers: { "content-type": "application/json" }
    });
  }

  await notify(secrets, "🧪 Test run triggered, waiting for results...");

  // Fire and forget so HTTP response returns immediately
  ctx?.waitUntil?.(runTests(secrets));

  return new Response(JSON.stringify({ status: "TEST_STARTED" }, null, 2), {
    headers: { "content-type": "application/json" }
  });
}

async function runTests(secrets) {
  const TEST_URL = "https://scriptelios.win/api/testrun";
  const TIMEOUT_MS = 300_000; // 5 minutes max for test suite

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
  const exitCode = result?.exitCode ?? (res.ok ? 0 : 1);

  // Parse pytest summary line e.g. "5 failed, 95 passed in 12.3s"
  const summaryMatch = output.match(/={3,}\s*(.+?)\s*={3,}\s*$/m);
  const summaryLine = summaryMatch?.[1] ?? "";

  const totalPassed = parseInt(summaryLine.match(/(\d+)\s+passed/)?.[1] ?? 0);
  const totalFailed = parseInt(summaryLine.match(/(\d+)\s+failed/)?.[1] ?? 0);
  const total = (totalPassed + totalFailed) || "?";

  // Collect failed test names from "FAILED path::test_name" lines
  const failedNames = [...output.matchAll(/^FAILED\s+\S+::(\S+)/gm)]
    .map(m => m[1]);

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
// DEBUG
// ─────────────────────────────────────────────

async function debugAuth(env) {
  const results = {};

  let secrets;
  try {
    secrets = await getSecrets(env);
  } catch (err) {
    return new Response(JSON.stringify({ secretsError: err.message }, null, 2), {
      status: 500,
      headers: { "content-type": "application/json" }
    });
  }

  const { runpodKey, podId, telegramToken, telegramChatId } = secrets;

  results.env = {
    RUNPOD_API_KEY: runpodKey
      ? `set (${runpodKey.length} chars, starts: ${runpodKey.slice(0, 6)}...)`
      : "MISSING",
    RUNPOD_POD_ID: podId || "MISSING",
    TELEGRAM_BOT_TOKEN: telegramToken ? `set (${telegramToken.length} chars)` : "MISSING",
    TELEGRAM_CHAT_ID: telegramChatId || "MISSING",
  };

  try {
    const res = await fetch(`https://api.runpod.io/graphql?api_key=${runpodKey}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: `{ __typename }` })
    });
    const raw = await res.text();
    results.queryParam = { status: res.status, raw };
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
    const raw = await res.text();
    results.podQuery = { status: res.status, raw };
  } catch (err) {
    results.podQuery = { fetchError: err.message };
  }

  return new Response(JSON.stringify(results, null, 2), {
    headers: { "content-type": "application/json" }
  });
}

// ─────────────────────────────────────────────
// HEALTH CHECK / WAIT FOR BACKEND
// ─────────────────────────────────────────────

async function waitForBackend(secrets, runpodResult) {
  const HEALTH_URL = "https://scriptelios.win/api/health";
  const INITIAL_WAIT_MS = 60_000;   // 60s initial boot wait
  const POLL_INTERVAL_MS = 30_000;  // 30s between attempts
  const MAX_ATTEMPTS = 14;          // 14 × 30s = 7min polling = 8min total

  // Detect GPU error immediately from mutation response
  if (runpodResult?.noGpu) {
    await notify(secrets, "🚫 No GPU available — RunPod could not allocate a GPU for the pod. Try again later.");
    return false;
  }

  await notify(secrets, "⏳ Server starting up, waiting for backend...");

  // Wait for initial boot
  await sleep(INITIAL_WAIT_MS);

  // After initial wait, check pod state — if still EXITED, RunPod never got a GPU
  const podState = await getState(secrets);
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

    // Every 3 attempts, re-check pod state to catch late GPU failures
    if (i % 3 === 0) {
      const midState = await getState(secrets);
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
    "• Model still loading (Qwen3:32b ~90s, longer under load)\n" +
    "• runpod-start.sh failed → check /workspace/backend.log\n" +
    "• Cloudflare tunnel not started → check /workspace/cloudflared.log"
  );
  return false;
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
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
        body: JSON.stringify({
          chat_id: telegramChatId,
          text
        })
      }
    );
  } catch (e) {
    console.log("Telegram error:", e);
  }
}

// ─────────────────────────────────────────────
// LOGGING (KV)
// ─────────────────────────────────────────────

async function log(env, entry) {
  if (!env.LOGS) return;

  try {
    await env.LOGS.put(Date.now().toString(), JSON.stringify(entry));
  } catch (e) {
    console.log("Log error:", e);
  }
}

async function getLogs(env) {
  if (!env.LOGS) {
    return new Response("KV not configured", { status: 500 });
  }

  const list = await env.LOGS.list({ limit: 20 });

  const logs = await Promise.all(
    list.keys.map(k => env.LOGS.get(k.name).then(JSON.parse))
  );

  return new Response(JSON.stringify(logs, null, 2), {
    headers: { "content-type": "application/json" }
  });
}
