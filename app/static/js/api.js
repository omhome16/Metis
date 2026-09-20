/* Thin API client for the Metis backend (JWT bearer + legacy token probe). */

const enc = encodeURIComponent;
const JWT_KEY = "metis.jwt";
const TOKEN_KEY = "metis.apiToken";

export function jwt() {
  try { return localStorage.getItem(JWT_KEY) || ""; } catch { return ""; }
}

export function setJwt(token) {
  try {
    if (token) localStorage.setItem(JWT_KEY, token);
    else localStorage.removeItem(JWT_KEY);
  } catch { /* storage unavailable */ }
}

/** Legacy shared-secret token (METIS_AUTH_MODE=token deployments). */
export function apiToken() {
  try { return localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; }
}

export function setApiToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch { /* storage unavailable */ }
}

function authHeaders(base = {}) {
  const h = { ...base };
  const t = jwt() || apiToken();
  if (t) h.Authorization = `Bearer ${t}`;
  return h;
}

/** Append the token for browser navigations that cannot set headers. */
function withToken(url) {
  const t = apiToken();
  if (!t) return url;
  return `${url}${url.includes("?") ? "&" : "?"}token=${enc(t)}`;
}

async function request(method, url, { json, form } = {}) {
  const opts = { method };
  if (json !== undefined) {
    opts.headers = { "Content-Type": "application/json" };
    opts.body = JSON.stringify(json);
  } else if (form !== undefined) {
    opts.body = form; // FormData — browser sets the multipart boundary
  }
  opts.headers = authHeaders(opts.headers || {});
  const res = await fetch(url, opts);
  if (res.status === 401 && typeof window !== "undefined") {
    // Signed out (or token missing): let the auth view take over.
    window.dispatchEvent(new CustomEvent("metis:auth-required"));
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail ?? detail;
    } catch { /* non-JSON error body */ }
    const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    err.status = res.status;
    throw err;
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res.text();
}

/** Fetch a binary (file download / export) with auth, and trigger a save. */
async function download(url, filename) {
  const res = await fetch(url, { headers: authHeaders() });
  if (res.status === 401) window.dispatchEvent(new CustomEvent("metis:auth-required"));
  if (!res.ok) throw new Error(`download failed (${res.status})`);
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}

export const api = {
  // auth
  register: (data) => request("POST", "/api/v1/auth/register", { json: data }),
  login: (data) => request("POST", "/api/v1/auth/login", { json: data }),
  me: () => request("GET", "/api/v1/auth/me"),

  // vaults
  vaults: () => request("GET", "/api/v1/vaults"),
  createVault: (data) => request("POST", "/api/v1/vaults", { json: data }),
  updateVault: (name, data) => request("PATCH", `/api/v1/vaults/${enc(name)}`, { json: data }),
  deleteVault: (name) => request("DELETE", `/api/v1/vaults/${enc(name)}`),
  vaultDetail: (name) => request("GET", `/api/v1/vaults/${enc(name)}`),

  // documents
  documents: (name) => request("GET", `/api/v1/vaults/${enc(name)}/documents`),
  recentDocs: () => request("GET", "/api/v1/documents/recent"),
  doc: (id) => request("GET", `/api/v1/documents/${id}`),
  docContent: (id) => request("GET", `/api/v1/documents/${id}/content`),
  docChunks: (id) => request("GET", `/api/v1/documents/${id}/chunks`),
  docFile: (id, filename) => download(`/api/v1/documents/${id}/file`, filename || "document"),
  docFileUrl: (id) => withToken(`/api/v1/documents/${id}/file`),
  deleteDoc: (id) => request("DELETE", `/api/v1/documents/${id}`),

  // ingest
  ingest: (corpus, files) => {
    const fd = new FormData();
    fd.append("corpus", corpus);
    for (const f of files) fd.append("files", f, f.name);
    return request("POST", "/api/v1/ingest", { form: fd });
  },
  job: (id) => request("GET", `/api/v1/ingest/${id}`),

  // imports (URL / Readwise / Zotero)
  importUrl: (data) => request("POST", "/api/v1/imports/url", { json: data }),
  importReadwise: (data) => request("POST", "/api/v1/imports/readwise", { json: data }),
  importZotero: (data) => request("POST", "/api/v1/imports/zotero", { json: data }),

  // notes
  saveNote: (name, data) => request("POST", `/api/v1/vaults/${enc(name)}/notes`, { json: data }),

  // contradiction report
  contradictionReport: (name) => request("POST", `/api/v1/vaults/${enc(name)}/contradiction-report`),

  // export
  exportVault: (name) => download(`/api/v1/vaults/${enc(name)}/export`, `${name}-metis-export.zip`),

  // graph
  graph: (name) => request("GET", `/api/v1/vaults/${enc(name)}/graph`),
  suggestions: (name) => request("GET", `/api/v1/vaults/${enc(name)}/suggestions`),
  graphExplore: (entity, depth = 1, limit = 40) =>
    request("GET", `/api/v1/graph/explore?entity=${enc(entity)}&depth=${depth}&limit=${limit}`),

  // cross-vault library
  libraryGraph: () => request("GET", "/api/v1/library/graph"),
  libraryEntities: (q, limit = 12) => request("GET", `/api/v1/library/entities?q=${enc(q)}&limit=${limit}`),
  librarySurprises: () => request("GET", "/api/v1/library/surprises"),
  libraryJourney: (from, to) => request("GET", `/api/v1/library/journey?from=${enc(from)}&to=${enc(to)}`),
  reorgRuns: (limit = 8) => request("GET", `/api/v1/library/reorganizations?limit=${limit}`),

  // settings
  settings: () => request("GET", "/api/v1/settings"),
  saveSettings: (data) => request("PUT", "/api/v1/settings", { json: { settings: data } }),
  runCommunities: () => request("POST", "/api/v1/graph/communities"),

  // conversations
  conversations: (name) => request("GET", `/api/v1/vaults/${enc(name)}/conversations`),
  conversation: (id) => request("GET", `/api/v1/conversations/${id}`),
  deleteConversation: (id) => request("DELETE", `/api/v1/conversations/${id}`),
  feedback: (messageId, data) => request("POST", `/api/v1/ask/${enc(messageId)}/feedback`, { json: data }),

  health: () => request("GET", "/healthz"),
  cacheStats: () => request("GET", "/api/v1/cache/stats"),
};

/**
 * Stream the /ask SSE endpoint via fetch. `onEvent(event, data)` is called
 * for every parsed frame; throws on non-2xx responses.
 */
export async function askStream(payload, onEvent, signal) {
  const res = await fetch("/api/v1/ask", {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
    signal,
  });
  if (res.status === 401) window.dispatchEvent(new CustomEvent("metis:auth-required"));
  if (!res.ok || !res.body) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail ?? detail;
    } catch { /* ignore */ }
    throw new Error(detail);
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  let event = "message";
  let data = "";

  const flush = () => {
    if (data !== "") {
      try {
        onEvent(event, JSON.parse(data));
      } catch {
        onEvent("parse-error", { raw: data });
      }
    }
    event = "message";
    data = "";
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    // sse-starlette sends \r\n frames — split on either, stripping the CR
    let idx;
    while ((idx = buf.search(/\r?\n/)) !== -1) {
      const nl = buf[idx] === "\r" ? 2 : 1;
      const line = buf.slice(0, idx);
      buf = buf.slice(idx + nl);
      if (line === "") flush();
      else if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data += line.slice(5).trim();
    }
  }
  flush();
}
