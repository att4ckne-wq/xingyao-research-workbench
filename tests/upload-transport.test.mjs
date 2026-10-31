import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import http from "node:http";
import { once } from "node:events";
import { test } from "node:test";
import { createLocalServer, localBackendUrl } from "../scripts/local-server.mjs";

async function listen(server) {
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  return `http://127.0.0.1:${server.address().port}`;
}

async function close(server) {
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
}

async function fixture(t, handler, options = {}) {
  const backend = http.createServer(handler);
  const backendUrl = await listen(backend);
  const frontend = createLocalServer({
    worker: { fetch: () => new Response("worker fallback") }, backendUrl, ...options,
  });
  const url = await listen(frontend);
  t.after(async () => { await close(frontend); await close(backend); });
  return { url, backend, frontend };
}

function request(url, { method = "GET", headers = {}, body } = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request(url, { method, headers }, res => {
      const chunks = [];
      res.on("data", chunk => chunks.push(chunk));
      res.on("error", reject);
      res.on("end", () => resolve({ status: res.statusCode, headers: res.headers, body: Buffer.concat(chunks) }));
    });
    req.on("error", reject);
    req.end(body);
  });
}

test("18.8 MB multipart crosses the local HTTP boundary with exact bytes and session headers", async t => {
  const payload = Buffer.concat([
    Buffer.from('--transport-test\r\nContent-Disposition: form-data; name="file"; filename="synthetic.docx"\r\nContent-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document\r\n\r\n'),
    Buffer.alloc(18_800_000, 0x71), Buffer.from("\r\n--transport-test--\r\n"),
  ]);
  const expected = createHash("sha256").update(payload).digest("hex");
  let observed;
  const { url } = await fixture(t, async (req, res) => {
    const hash = createHash("sha256");
    let bytes = 0;
    for await (const chunk of req) { hash.update(chunk); bytes += chunk.length; }
    observed = { bytes, hash: hash.digest("hex"), path: req.url, cookie: req.headers.cookie,
      contentType: req.headers["content-type"], hop: req.headers["x-request-hop"] };
    res.writeHead(201, { "content-type": "application/json", "set-cookie": ["session=synthetic; HttpOnly; Path=/", "other=synthetic; Path=/"],
      connection: "x-response-hop", "x-response-hop": "private" });
    res.end('{"accepted":true}');
  });
  const result = await request(`${url}/api/projects?mode=synthetic`, { method: "POST", body: payload,
    headers: { "content-type": "multipart/form-data; boundary=transport-test", "content-length": payload.length,
      cookie: "session=synthetic", connection: "x-request-hop", "x-request-hop": "private" } });
  assert.deepEqual(observed, { bytes: payload.length, hash: expected, path: "/projects?mode=synthetic",
    cookie: "session=synthetic", contentType: "multipart/form-data; boundary=transport-test", hop: undefined });
  assert.equal(result.status, 201);
  assert.equal(result.headers["set-cookie"].length, 2);
  assert.equal(result.headers["x-response-hop"], undefined);
  assert.equal(result.body.toString(), '{"accepted":true}');
});

test("chunked upload reaches upstream before client finishes", async t => {
  let signal;
  const firstChunk = new Promise(resolve => { signal = resolve; });
  const { url } = await fixture(t, async (req, res) => {
    for await (const chunk of req) { assert.ok(chunk.length); signal(); }
    res.end("streamed");
  });
  const result = new Promise((resolve, reject) => {
    const req = http.request(`${url}/api/stream`, { method: "POST", headers: { "content-type": "multipart/form-data; boundary=synthetic" } }, res => {
      res.resume(); res.on("end", () => resolve(res.statusCode));
    });
    req.on("error", reject);
    req.write(Buffer.alloc(64 * 1024, 0x72));
    const timer = setTimeout(() => req.destroy(new Error("proxy buffered the upload")), 2000);
    firstChunk.then(() => { clearTimeout(timer); req.end("last"); });
  });
  assert.equal(await result, 200);
});

test("backend rejection, download headers and redirect survive without following redirects", async t => {
  const { url } = await fixture(t, (req, res) => {
    if (req.url === "/reject") { res.writeHead(413, { "content-type": "application/json" }); res.end('{"detail":"backend limit"}'); }
    else if (req.url === "/download") { res.writeHead(200, { "content-disposition": 'attachment; filename="synthetic.zip"', "content-type": "application/zip" }); res.end("zip bytes"); }
    else { res.writeHead(302, { location: "https://example.invalid/never-follow" }); res.end(); }
  });
  const rejected = await request(`${url}/api/reject`, { method: "POST", body: "synthetic" });
  assert.equal(rejected.status, 413);
  assert.equal(rejected.body.toString(), '{"detail":"backend limit"}');
  const download = await request(`${url}/api/download`);
  assert.equal(download.headers["content-disposition"], 'attachment; filename="synthetic.zip"');
  assert.equal(download.body.toString(), "zip bytes");
  const redirect = await request(`${url}/api/redirect`);
  assert.equal(redirect.status, 302);
  assert.equal(redirect.headers.location, "https://example.invalid/never-follow");
});

test("API routing leaves pages with the worker and preserves all HTTP methods", async t => {
  const { url } = await fixture(t, (req, res) => { res.end(`${req.method} ${req.url}`); });
  for (const method of ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]) {
    const result = await request(`${url}/api/items?x=1`, { method });
    assert.equal(result.body.toString(), `${method} /items?x=1`);
  }
  assert.equal((await request(`${url}/api?x=1`)).body.toString(), "GET /?x=1");
  assert.equal((await request(`${url}/api-other`)).body.toString(), "worker fallback");
});

test("upstream timeout and connection failure produce bounded JSON errors", async t => {
  const { url, backend } = await fixture(t, () => {}, { timeoutMs: 40 });
  const timeout = await request(`${url}/api/timeout`);
  assert.equal(timeout.status, 504);
  assert.equal(typeof JSON.parse(timeout.body).detail, "string");
  await close(backend);
  const unavailable = await request(`${url}/api/unavailable`);
  assert.equal(unavailable.status, 503);
  assert.equal(typeof JSON.parse(unavailable.body).detail, "string");
});

test("aborting a client upload cancels its upstream socket", { timeout: 3000 }, async t => {
  let received, aborted;
  const firstChunk = new Promise(resolve => { received = resolve; });
  const cancelled = new Promise(resolve => { aborted = resolve; });
  const { url } = await fixture(t, (req, res) => {
    if (req.url === "/health") { res.end("ok"); return; }
    req.on("data", () => received());
    req.on("aborted", () => aborted());
    req.on("error", () => {});
  });
  const client = http.request(`${url}/api/abort`, { method: "POST" });
  client.on("error", () => {});
  client.write(Buffer.alloc(64 * 1024, 0x73));
  await firstChunk;
  client.destroy();
  await cancelled;
  assert.equal((await request(`${url}/api/health`)).body.toString(), "ok");
});

test("backend configuration cannot select a remote origin or credentials", () => {
  for (const value of ["https://example.com", "http://127.0.0.1.evil.test", "file:///tmp/file", "http://user:pass@127.0.0.1", "http://127.0.0.1/path", "http://127.0.0.1/?x=1"]) {
    assert.throws(() => localBackendUrl(value));
  }
  assert.equal(localBackendUrl("http://localhost:8501").hostname, "127.0.0.1");
  assert.equal(localBackendUrl("http://[::1]:8501").hostname, "[::1]");
});
