import { createReadStream, existsSync, statSync } from "node:fs";
import { readFile } from "node:fs/promises";
import http from "node:http";
import https from "node:https";
import path from "node:path";
import { Readable } from "node:stream";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const clientRoot = path.join(root, "dist", "client");
const workerPath = path.join(root, "dist", "server", "index.js");
const host = "127.0.0.1";
const port = Number(process.env.PORT || 3000);
const contentTypes = {
  ".css":"text/css; charset=utf-8", ".js":"application/javascript; charset=utf-8",
  ".json":"application/json; charset=utf-8", ".svg":"image/svg+xml",
  ".png":"image/png", ".jpg":"image/jpeg", ".jpeg":"image/jpeg",
  ".webp":"image/webp", ".ico":"image/x-icon", ".woff":"font/woff",
  ".woff2":"font/woff2", ".ttf":"font/ttf",
};

function resolveStatic(urlPath) {
  let pathname;
  try { pathname = decodeURIComponent(new URL(urlPath, "http://localhost").pathname); }
  catch { return null; }
  const candidate = path.resolve(clientRoot, `.${pathname}`);
  if (candidate !== clientRoot && !candidate.startsWith(`${clientRoot}${path.sep}`)) return null;
  if (!existsSync(candidate) || !statSync(candidate).isFile()) return null;
  return candidate;
}

function staticHeaders(filePath) {
  const stat = statSync(filePath);
  return {
    "content-type": contentTypes[path.extname(filePath).toLowerCase()] || "application/octet-stream",
    "content-length": String(stat.size),
    "cache-control": filePath.includes(`${path.sep}assets${path.sep}`)
      ? "public, max-age=31536000, immutable" : "public, max-age=3600",
  };
}

async function assetFetch(input) {
  const source = typeof input === "string" ? input : input.url;
  const filePath = resolveStatic(source);
  if (!filePath) return new Response("Not found", { status: 404 });
  return new Response(await readFile(filePath), { headers: staticHeaders(filePath) });
}

async function toRequest(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const body = chunks.length ? Buffer.concat(chunks) : undefined;
  const headers = new Headers();
  for (const [name, value] of Object.entries(req.headers)) {
    if (Array.isArray(value)) value.forEach(item => headers.append(name, item));
    else if (value !== undefined) headers.set(name, value);
  }
  return new Request(`http://${req.headers.host || `${host}:${port}`}${req.url || "/"}`, {
    method: req.method, headers, body,
  });
}

const hopHeaders = new Set([
  "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
  "te", "trailer", "transfer-encoding", "upgrade", "proxy-connection",
]);

function endToEndHeaders(source) {
  const blocked = new Set(hopHeaders);
  for (const token of String(source.connection || "").split(",")) blocked.add(token.trim().toLowerCase());
  return Object.fromEntries(Object.entries(source).filter(([name]) => !blocked.has(name.toLowerCase())));
}

export function localBackendUrl(value = process.env.NATURE_API_URL ?? "http://127.0.0.1:8000") {
  const target = new URL(value);
  if (!["http:", "https:"].includes(target.protocol) ||
      !["127.0.0.1", "localhost", "[::1]"].includes(target.hostname) ||
      target.username || target.password || target.pathname !== "/" || target.search || target.hash) {
    throw new Error("NATURE_API_URL must be a loopback HTTP(S) origin without credentials or a path.");
  }
  // Resolve localhost without DNS; no configured host can resolve off this machine.
  if (target.hostname === "localhost") target.hostname = "127.0.0.1";
  return target;
}

export function createApiProxyHandler({ backendUrl, timeoutMs = 120_000 } = {}) {
  const backend = localBackendUrl(backendUrl);
  return (req, res) => {
    const headers = endToEndHeaders(req.headers);
    delete headers.host;
    const transport = backend.protocol === "https:" ? https : http;
    // Use an explicit fixed origin and a path, never resolve user input as a URL.
    const suffix = (req.url || "/api").slice(4);
    const upstream = transport.request(backend, {
      method: req.method, path: suffix.startsWith("/") ? suffix : `/${suffix}`, headers,
    });
    const fail = (status, detail) => {
      req.unpipe(upstream);
      req.resume();
      if (res.destroyed || res.writableEnded) return;
      if (res.headersSent) { res.destroy(); return; }
      res.writeHead(status, { "content-type": "application/json; charset=utf-8" });
      res.end(JSON.stringify({ detail }));
    };
    upstream.setTimeout(timeoutMs, () => {
      fail(504, "本地分析服务响应超时");
      upstream.destroy();
    });
    upstream.on("error", () => fail(503, "本地分析服务尚未启动或连接中断"));
    req.on("aborted", () => upstream.destroy());
    req.on("error", () => upstream.destroy());
    res.on("close", () => upstream.destroy());
    upstream.on("response", response => {
      if (res.destroyed || res.writableEnded) { response.destroy(); return; }
      res.writeHead(response.statusCode || 502, endToEndHeaders(response.headers));
      response.on("error", () => res.destroy());
      response.pipe(res);
      res.once("finish", () => {
        req.unpipe(upstream);
        if (!req.complete) req.resume();
      });
    });
    req.pipe(upstream);
  };
}

export function createLocalServer({ worker, backendUrl, timeoutMs } = {}) {
  const proxyApi = createApiProxyHandler({ backendUrl, timeoutMs });
  return http.createServer(async (req, res) => {
  try {
    // Multipart API uploads must bypass vinext's progressive Server Action parser,
    // which applies its 1 MiB action limit before matching the API route.
    if (/^\/api(?:\/|\?|$)/.test(req.url || "")) {
      proxyApi(req, res);
      return;
    }
    const filePath = resolveStatic(req.url || "/");
    if (filePath) {
      res.writeHead(200, staticHeaders(filePath));
      createReadStream(filePath).pipe(res);
      return;
    }
    const request = await toRequest(req);
    const response = await worker.fetch(
      request,
      { ASSETS: { fetch: assetFetch } },
      { waitUntil() {}, passThroughOnException() {} },
    );
    const headers = {};
    response.headers.forEach((value, name) => { headers[name] = value; });
    const cookies = response.headers.getSetCookie?.() || [];
    if (cookies.length) headers["set-cookie"] = cookies;
    res.writeHead(response.status, headers);
    if (!response.body) res.end();
    else Readable.fromWeb(response.body).pipe(res);
  } catch (error) {
    console.error(error);
    if (!res.headersSent) res.writeHead(500, { "content-type":"text/plain; charset=utf-8" });
    res.end("Local server error");
  }
  });
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  if (!existsSync(workerPath) || !existsSync(clientRoot)) {
    throw new Error("Production build is missing. Run npm run build first.");
  }
  const worker = (await import(pathToFileURL(workerPath).href)).default;
  const server = createLocalServer({ worker });
  server.listen(port, host, () => {
    console.log(`星钥科研智能工作台 running at http://${host}:${port}`);
  });
  server.on("error", error => { console.error(error); process.exitCode = 1; });
  for (const signal of ["SIGINT", "SIGTERM"]) {
    process.on(signal, () => server.close(() => process.exit(0)));
  }
}
