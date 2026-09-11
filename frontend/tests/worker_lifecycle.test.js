/**
 * v19.21 Pod-Lifecycle v2 — reine Funktionen des Cloudflare-Workers.
 * Kein Netzwerk, kein KV: getestet werden Spec-Ableitung, Deploy-Payload,
 * Env-Sanitizing, No-GPU-Erkennung und das 10-min-Retry-Fenster.
 */
import {
  buildDeployInput, deployMutation, sanitizeEnv, specFromPod, isValidSpec,
  isNoGpuMessage, retryDecision, isNightBlocked, missingSpecFields,
} from "../../misc/cloudflareworker.js";

const POD = {
  id: "abc123def", name: "scriptTelios", imageName: "runpod/pytorch:2.4.0-py3.11-cuda12.4.1",
  dockerArgs: "bash /workspace/scriptTelios/backend/runpod-start.sh", ports: "8000/http,22/tcp",
  containerDiskInGb: 60, volumeMountPath: "/workspace", networkVolumeId: "vol_xyz",
  vcpuCount: 12, memoryInGb: 62,
  env: ["OLLAMA_MODELS=/workspace/ollama", "RUNPOD_POD_ID=abc123def", "CLOUDFLARE_TUNNEL_TOKEN=geheim",
        "LLM_NUM_CTX_CAP=32768", "PUBLIC_KEY=ssh-ed25519 AAAA", "DB_PASSWORD=pw"],
  machine: { gpuTypeId: "NVIDIA RTX PRO 4500 Blackwell", dataCenterId: "EU-RO-1", secureCloud: true },
};

describe("sanitizeEnv", () => {
  test("verwirft Secrets und RunPod-Auto-Variablen, behaelt Konfig", () => {
    const env = sanitizeEnv(POD.env);
    const keys = env.map(e => e.key);
    expect(keys).toEqual(["OLLAMA_MODELS", "LLM_NUM_CTX_CAP"]);
    expect(JSON.stringify(env)).not.toMatch(/geheim|pw|AAAA/);
  });
  test("akzeptiert Objektform", () => {
    expect(sanitizeEnv([{ key: "A", value: 1 }, { key: "API_KEY", value: "x" }])).toEqual([{ key: "A", value: "1" }]);
  });
});

describe("specFromPod / isValidSpec", () => {
  test("vollstaendige Spec aus Pod-Objekt", () => {
    const spec = specFromPod(POD);
    expect(isValidSpec(spec)).toBe(true);
    expect(missingSpecFields(spec)).toEqual([]);
    expect(spec.gpuTypeId).toBe("NVIDIA RTX PRO 4500 Blackwell");
    expect(spec.dataCenterId).toBe("EU-RO-1");
    expect(spec.cloudType).toBe("SECURE");
    expect(spec.env.find(e => e.key === "CLOUDFLARE_TUNNEL_TOKEN")).toBeUndefined();
  });
  test("fehlendes Volume → ungueltig, wird gemeldet", () => {
    const spec = specFromPod({ ...POD, networkVolumeId: null });
    expect(isValidSpec(spec)).toBe(false);
    expect(missingSpecFields(spec)).toEqual(["networkVolumeId"]);
    // extra-Volume aus der Volume-Liste rettet es
    expect(isValidSpec(specFromPod({ ...POD, networkVolumeId: null }, { networkVolumeId: "vol_2" }))).toBe(true);
  });
});

describe("buildDeployInput / deployMutation", () => {
  const spec = specFromPod(POD);
  test("Payload: genau eine GPU, kein Fallback-Typ, Volume gemountet", () => {
    const inp = buildDeployInput(spec);
    expect(inp.gpuCount).toBe(1);
    expect(inp.gpuTypeId).toBe("NVIDIA RTX PRO 4500 Blackwell");
    expect(inp.networkVolumeId).toBe("vol_xyz");
    expect(inp.volumeMountPath).toBe("/workspace");
    expect(inp.volumeInGb).toBe(0);
    expect(inp.dataCenterId).toBe("EU-RO-1");
    expect(inp).not.toHaveProperty("gpuTypeIdList");
  });
  test("Mutation: cloudType als Enum (ohne Quotes), Strings gequotet", () => {
    const m = deployMutation(buildDeployInput(spec));
    expect(m).toMatch(/podFindAndDeployOnDemand\(input: \{/);
    expect(m).toMatch(/cloudType: SECURE[,}]/);
    expect(m).toMatch(/gpuTypeId: "NVIDIA RTX PRO 4500 Blackwell"/);
    expect(m).toMatch(/env: \[\{key: "OLLAMA_MODELS", value: "\/workspace\/ollama"\}/);
    expect(m).not.toMatch(/geheim/);
  });
});

describe("isNoGpuMessage", () => {
  test.each([
    ["There are no longer any instances available with the requested specifications.", true],
    ["No GPU available on host", true],
    ["Out of stock: NVIDIA RTX PRO 4500", true],
    ["Unauthorized", false],
    ["Pod not found", false],
  ])("%s → %s", (msg, exp) => expect(isNoGpuMessage(msg)).toBe(exp));
});

describe("retryDecision (10-min-Fenster, 1 Versuch/min)", () => {
  const t0 = 1_700_000_000_000;
  test("erster Versuch", () => {
    expect(retryDecision(null, t0)).toEqual({ action: "attempt", since: t0, attempts: 1 });
  });
  test("innerhalb 60 s nach letztem Versuch → warten", () => {
    const d = retryDecision({ since: t0, attempts: 1, lastAttempt: t0 }, t0 + 20_000);
    expect(d.action).toBe("wait");
    expect(d.retryInS).toBe(40);
  });
  test("nach 60 s → naechster Versuch, Zaehler +1", () => {
    const d = retryDecision({ since: t0, attempts: 3, lastAttempt: t0 + 180_000 }, t0 + 241_000);
    expect(d).toEqual({ action: "attempt", since: t0, attempts: 4 });
  });
  test("nach 10 min → aufgeben", () => {
    const d = retryDecision({ since: t0, attempts: 9, lastAttempt: t0 + 540_000 }, t0 + 601_000);
    expect(d.action).toBe("give_up");
  });
});

describe("isNightBlocked (23–05 Uhr Lokalzeit)", () => {
  test.each([[23 * 60, true], [2 * 60, true], [4 * 60 + 59, true], [5 * 60, false], [12 * 60, false], [22 * 60 + 59, false]])
    ("Minute %i → %s", (m, exp) => expect(isNightBlocked(m)).toBe(exp));
});
