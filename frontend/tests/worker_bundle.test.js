/**
 * @jest-environment node
 */
/**
 * v19.32 Bundle-Auslieferung ueber den Worker (KV statt Confluence-Anhang):
 * Upload mit Secret + SHA-Pruefung, GET mit ETag/304, Meta, Rollback.
 * KV ist ein Map-Mock; crypto.subtle kommt aus Node.
 */
import { handleBundle, berlinStamp } from "../../misc/cloudflareworker.js";

function kv() {
  const m = new Map();
  return {
    async get(k, opt) { const v = m.has(k) ? m.get(k) : null; if (v == null) return null; return opt && opt.type === "json" ? JSON.parse(v) : v; },
    async put(k, v) { m.set(k, v); },
    _m: m,
  };
}
const CORS = { "Access-Control-Allow-Origin": "*" };
const BODY = "// bundle\n" + "x".repeat(2000);
const BODY2 = "// bundle v2\n" + "y".repeat(2000);

function req(path, { method = "GET", headers = {}, body } = {}) {
  return new Request("https://w.example" + path, { method, headers, body });
}
function env(extra = {}) {
  return { LOGS: kv(), CONFLUENCE_SHARED_SECRET: "shared", ...extra };
}
async function call(e, r) {
  return handleBundle(r, e, new URL(r.url), CORS);
}

describe("Worker Bundle", () => {
  test("ohne Bundle: GET 404 als JS-Kommentar, meta 404", async () => {
    const e = env();
    const r = await call(e, req("/systelios.js"));
    expect(r.status).toBe(404);
    expect(r.headers.get("content-type")).toMatch(/javascript/);
    expect((await call(e, req("/systelios.js/meta"))).status).toBe(404);
  });

  test("Upload ohne/mit falschem Secret -> 401; Fallback auf CONFLUENCE_SHARED_SECRET", async () => {
    const e = env();
    expect((await call(e, req("/systelios.js", { method: "POST", body: BODY }))).status).toBe(401);
    expect((await call(e, req("/systelios.js", { method: "POST", body: BODY, headers: { "X-Bundle-Secret": "nope" } }))).status).toBe(401);
    const ok = await call(e, req("/systelios.js", { method: "POST", body: BODY, headers: { "X-Bundle-Secret": "shared" } }));
    expect(ok.status).toBe(200);
  });

  test("Upload, SHA-Pruefung, GET mit ETag/304, Meta mit lesbarem Zeitstempel", async () => {
    const e = env({ BUNDLE_UPLOAD_SECRET: "up" });
    const bad = await call(e, req("/systelios.js", { method: "POST", body: BODY, headers: { "X-Bundle-Secret": "up", "X-Bundle-Sha256": "deadbeef" } }));
    expect(bad.status).toBe(422);
    const up = await call(e, req("/systelios.js", { method: "POST", body: BODY, headers: { "X-Bundle-Secret": "up", "X-Bundle-Version": "v19.32", "X-Bundle-User": "carsten" } }));
    expect(up.status).toBe(200);
    const d = await up.json();
    expect(d.ok).toBe(true);
    expect(d.current.version).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2} · v19\.32 · [0-9a-f]{7}$/);
    expect(d.current.uploadedBy).toBe("carsten");
    expect(d.previous).toBeNull();

    const g = await call(e, req("/systelios.js"));
    expect(g.status).toBe(200);
    expect(await g.text()).toBe(BODY);
    expect(g.headers.get("cache-control")).toBe("no-cache");
    expect(g.headers.get("access-control-allow-origin")).toBe("*");
    const etag = g.headers.get("etag");
    expect(etag).toBe(`"${d.current.sha256}"`);
    const notMod = await call(e, req("/systelios.js", { headers: { "If-None-Match": etag } }));
    expect(notMod.status).toBe(304);

    const meta = await (await call(e, req("/systelios.js/meta"))).json();
    expect(meta.sha256).toBe(d.current.sha256);
    expect(meta.size).toBe(BODY.length);

    // gleicher Inhalt nochmal -> unchanged, previous bleibt leer
    const again = await (await call(e, req("/systelios.js", { method: "POST", body: BODY, headers: { "X-Bundle-Secret": "up" } }))).json();
    expect(again.unchanged).toBe(true);
    expect(e.LOGS._m.has("bundle:previous")).toBe(false);
  });

  test("zweiter Upload verschiebt current -> previous; Rollback tauscht zurueck", async () => {
    const e = env({ BUNDLE_UPLOAD_SECRET: "up" });
    const h = { "X-Bundle-Secret": "up" };
    const a = await (await call(e, req("/systelios.js", { method: "POST", body: BODY, headers: h }))).json();
    const b = await (await call(e, req("/systelios.js", { method: "POST", body: BODY2, headers: h }))).json();
    expect(b.previous.sha256).toBe(a.current.sha256);
    expect(await (await call(e, req("/systelios.js"))).text()).toBe(BODY2);
    const meta = await (await call(e, req("/systelios.js/meta"))).json();
    expect(meta.previous.sha256).toBe(a.current.sha256);

    const rb = await call(e, req("/systelios.js/rollback", { method: "POST", headers: h }));
    expect(rb.status).toBe(200);
    expect(await (await call(e, req("/systelios.js"))).text()).toBe(BODY);
    const meta2 = await (await call(e, req("/systelios.js/meta"))).json();
    expect(meta2.sha256).toBe(a.current.sha256);
    expect(meta2.rolledBackAt).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/);
    expect(meta2.previous.sha256).toBe(b.current.sha256);
  });

  test("zu kleiner Body 422; unbekannte Methode 405; ohne KV 500", async () => {
    const e = env({ BUNDLE_UPLOAD_SECRET: "up" });
    expect((await call(e, req("/systelios.js", { method: "POST", body: "tiny", headers: { "X-Bundle-Secret": "up" } }))).status).toBe(422);
    expect((await call(e, req("/systelios.js", { method: "PUT", body: BODY }))).status).toBe(405);
    expect((await call({ CONFLUENCE_SHARED_SECRET: "x" }, req("/systelios.js"))).status).toBe(500);
  });

  test("berlinStamp ist lesbar und in Europe/Berlin", () => {
    expect(berlinStamp(new Date("2026-09-23T19:40:00Z"))).toBe("2026-09-23 21:40");
    expect(berlinStamp(new Date("2026-01-15T08:05:00Z"))).toBe("2026-01-15 09:05");
  });
});
