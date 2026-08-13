/* Thin API client for the Metis backend. */

const enc = encodeURIComponent;

async function request(method, url, { json, form } = {}) {
  const opts = { method, signal: undefined };
  if (json !== undefined) {
    opts.headers = { "Content-Type": "application/json" };
    opts.body = JSON.stringify(json);
  } else if (form !== undefined) {
    opts.body = form; // FormData — browser sets the multipart boundary
  }
  const res = await fetch(url, opts);
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

export const api = {
  vaults: () => request("GET", "/api/v1/vaults"),
  createVault: (data) => request("POST", "/api/v1/vaults", { json: data }),
  updateVault: (name, data) => request("PATCH", `/api/v1/vaults/${enc(name)}`, { json: data }),
  deleteVault: (name) => request("DELETE", `/api/v1/vaults/${enc(name)}`),
  vaultDetail: (name) => request("GET", `/api/v1/vaults/${enc(name)}`),

  documents: (name) => request("GET", `/api/v1/vaults/${enc(name)}/documents`),
  recentDocs: () => request("GET", "/api/v1/documents/recent"),
  graph: (name) => request("GET", `/api/v1/vaults/${enc(name)}/graph`),
  suggestions: (name) => request("GET", `/api/v1/vaults/${enc(name)}/suggestions`),
  graphExplore: (entity, depth = 1, limit = 40) =>
    request("GET", `/api/v1/graph/explore?entity=${enc(entity)}&depth=${depth}&limit=${limit}`),

  libraryGraph: () => request("GET", "/api/v1/library/graph"),
  libraryEntities: (q, limit = 12) => request("GET", `/api/v1/library/entities?q=${enc(q)}&limit=${limit}`),
  librarySurprises: () => request("GET", "/api/v1/library/surprises"),
  libraryJourney: (from, to) => request("GET", `/api/v1/library/journey?from=${enc(from)}&to=${enc(to)}`),

  doc: (id) => request("GET", `/api/v1/documents/${id}`),
  docContent: (id) => request("GET", `/api/v1/documents/${id}/content`),
  docChunks: (id) => request("GET", `/api/v1/documents/${id}/chunks`),
  docFileUrl: (id) => `/api/v1/documents/${id}/file`,
  deleteDoc: (id) => request("DELETE", `/api/v1/documents/${id}`),

  conversations: (name) => request("GET", `/api/v1/vaults/${enc(name)}/conversations`),
  conversation: (id) => request("GET", `/api/v1/conversations/${id}`),
  deleteConversation: (id) => request("DELETE", `/api/v1/conversations/${id}`),
  feedback: (messageId, data) => request("POST", `/api/v1/ask/${enc(messageId)}/feedback`, { json: data }),

  ingest: (corpus, files) => {
    const fd = new FormData();
    fd.append("corpus", corpus);
    for (const f of files) fd.append("files", f, f.name);
    return request("POST", "/api/v1/ingest", { form: fd });
  },
  job: (id) => request("GET", `/api/v1/ingest/${id}`),

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
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
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
