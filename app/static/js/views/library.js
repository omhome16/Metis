import { el, clear, debounce } from "../util.js";
import { icon } from "../icons.js";
import { api } from "../api.js";
import { state } from "../store.js";
import { libraryPath } from "../router.js";
import { ForceGraph } from "../graph.js";
import { toast } from "./toast.js";

const FALLBACK_COLORS = ["#3B5A79", "#96691F", "#7A5C8C", "#5E6B4E", "#B0613A", "#2E6B4E", "#8A4F2F", "#45688F"];

// Requests made from elsewhere (e.g. a Surprises card) land here before the view mounts.
export let pendingJourney = null;
export let pendingFocus = null;

// One theme listener per session — the view re-mounts on every navigation.
let _themeHandler = null;
export function requestJourney(from, to) {
  pendingJourney = { from, to };
  location.hash = libraryPath("graph");
}
export function requestEntityFocus(name) {
  pendingFocus = name;
  location.hash = libraryPath("graph");
}

export async function renderLibrary(container, tab) {
  clear(container);
  const inner = el("div", { class: "view-inner" });
  container.append(inner);

  const vaults = state.vaults;
  const palette = vaultColorMap(vaults);

  // ── header ───────────────────────────────────────────────
  const head = el("div", { class: "vault-head" });
  const main = el("div", { class: "vault-head-main" });
  main.append(
    el("div", { class: "vault-eyebrow" }, [el("span", { class: "swatch lib", html: icon("network", 12) }), el("span", { text: "Library · all vaults" })]),
    el("h1", { class: "vault-title", text: "The Whole Library" }),
    el("p", { class: "vault-desc", text: "Every vault, one graph. Entities that span vaults are bridges; dashed edges cross between libraries. Ask any two ideas to find the path between them." })
  );
  head.append(main);
  inner.append(head);

  const totals = await fetchTotals();
  const stats = el("div", { class: "vault-stats" });
  const stat = (n, label) => el("span", { class: "stat-chip" }, [el("span", { class: "stat-num", text: String(n) }), el("span", { class: "stat-label", text: label })]);
  stats.append(stat(vaults.length, "vaults"), stat(totals.docs, "documents"), stat(totals.chunks, "chunks"), stat(totals.entities, "entities"));
  inner.append(stats);

  const tabs = el("div", { class: "tabs" }, [
    el("button", { class: `tab${tab === "graph" ? " active" : ""}`, text: "Graph" }),
    el("button", { class: `tab${tab === "surprises" ? " active" : ""}`, text: "Surprises" }),
  ]);
  tabs.querySelectorAll("button")[0].addEventListener("click", () => { location.hash = libraryPath("graph"); });
  tabs.querySelectorAll("button")[1].addEventListener("click", () => { location.hash = libraryPath("surprises"); });
  inner.append(tabs);

  const body = el("div");
  inner.append(body);

  if (tab === "surprises") {
    await renderSurprises(body, palette);
    return;
  }
  await renderLibraryGraph(body, palette);

  // any pending request (from a Surprises card) runs once the graph is up
  // (only if the user is still on the library graph view)
  if (pendingFocus) {
    const name = pendingFocus;
    pendingFocus = null;
    setTimeout(() => {
      if (location.hash.includes(libraryPath("graph"))) focusEntity(name);
    }, 600);
  }
  if (pendingJourney) {
    const j = pendingJourney;
    pendingJourney = null;
    setTimeout(() => {
      if (location.hash.includes(libraryPath("graph"))) runJourney(j.from, j.to);
    }, 600);
  }
}

function vaultColorMap(vaults) {
  const map = {};
  vaults.forEach((v, i) => {
    map[v.name] = v.color || FALLBACK_COLORS[i % FALLBACK_COLORS.length];
  });
  return map;
}

async function fetchTotals() {
  let docs = 0, chunks = 0, entities = 0;
  for (const v of state.vaults) {
    docs += v.doc_count || 0;
    chunks += v.chunk_count || 0;
    entities += v.entity_count || 0;
  }
  return { docs, chunks, entities };
}

/* ── Graph tab ─────────────────────────────────────────────── */

async function renderLibraryGraph(body, palette) {
  // journey bar
  const bar = el("div", { class: "journey-bar" });
  bar.append(
    el("span", { class: "journey-label", html: `${icon("route", 14)}<span>Idea journey</span>` }),
    entityField("from"),
    el("span", { class: "journey-arrow", text: "→" }),
    entityField("to"),
    el("button", { class: "btn btn-primary", text: "Find the path", style: "padding:7px 13px;font-size:12.5px" }),
    el("button", { class: "btn btn-ghost", html: icon("x", 13), title: "Clear journey", style: "padding:7px 9px" })
  );
  body.append(bar);

  // vault filter chips
  const chips = el("div", { class: "vault-chips" });
  const enabled = new Set(state.vaults.map((v) => v.name));
  const chipAll = el("button", { class: "chip chip-all active", text: "All vaults" });
  chips.append(chipAll);
  const chipByVault = {};
  for (const v of state.vaults) {
    const c = el("button", { class: "chip active", style: `--chip:${palette[v.name] || "#888"}` });
    c.append(el("span", { class: "chip-dot" }), el("span", { text: v.name }));
    c.addEventListener("click", () => {
      const nowOn = enabled.has(v.name);
      if (nowOn) { enabled.delete(v.name); c.classList.remove("active"); }
      else { enabled.add(v.name); c.classList.add("active"); }
      chipAll.classList.toggle("active", enabled.size === state.vaults.length);
      applyFilter();
    });
    chipByVault[v.name] = c;
    chips.append(c);
  }
  chipAll.addEventListener("click", () => {
    enabled.clear();
    state.vaults.forEach((v) => enabled.add(v.name));
    Object.values(chipByVault).forEach((c) => c.classList.add("active"));
    chipAll.classList.add("active");
    applyFilter();
  });
  body.append(chips);

  // stage
  const stage = el("div", { class: "graph-stage" });
  const canvas = el("canvas");
  stage.append(canvas);

  const chrome = el("div", { class: "graph-chrome" });
  const searchField = el("div", { class: "field graph-search" }, [
    el("span", { html: icon("search", 15) }),
    el("input", { placeholder: "Focus an entity", "aria-label": "Focus an entity" }),
  ]);
  const tools = el("div", { class: "graph-tools" });
  const mkTool = (name, title, fn) => {
    const b = el("button", { class: "tool-btn", title, html: icon(name, 15) });
    b.addEventListener("click", fn);
    return b;
  };
  tools.append(
    mkTool("zoomIn", "Zoom in", () => zoomBy(1.3)),
    mkTool("zoomOut", "Zoom out", () => zoomBy(1 / 1.3)),
    mkTool("compress", "Fit to view", () => graph.fit()),
    mkTool("refresh", "Reload graph", () => load())
  );
  chrome.append(searchField, tools);
  stage.append(chrome);

  const legend = el("div", { class: "graph-legend lib-legend" });
  function paintLegend() {
    clear(legend);
    legend.append(
      el("span", { class: "legend-item" }, [el("span", { class: "legend-dot", style: "background:var(--node-doc)" }), el("span", { text: "Document" })]),
      el("span", { class: "legend-item" }, [el("span", { class: "legend-dot bridge-dot", style: "background:var(--node-entity)" }), el("span", { text: "Bridge entity" })]),
      el("span", { class: "legend-item" }, [el("span", { class: "legend-dash" }), el("span", { text: "Cross-vault edge" })]),
      el("span", { class: "legend-item" }, [el("span", { class: "legend-dash accent" }), el("span", { text: "Journey path" })])
    );
    for (const v of state.vaults) {
      legend.append(
        el("span", { class: "legend-item" }, [el("span", { class: "legend-dot", style: `background:${palette[v.name] || "#888"}` }), el("span", { text: v.name })])
      );
    }
  }
  paintLegend();
  stage.append(legend);
  stage.append(el("div", { class: "graph-hint", text: "drag · scroll to zoom · click entity to expand" }));

  const empty = el("div", { class: "graph-empty hidden" }, [
    el("div", {}, [
      el("div", { class: "ge-title", text: "Nothing here yet" }),
      el("div", { class: "ge-copy", text: "Index documents in at least two vaults and their concepts will appear here, connected across libraries." }),
    ]),
  ]);
  stage.append(empty);

  // narrative panel
  const narrative = el("div", { class: "journey-narrative hidden" });
  body.append(narrative);

  const graph = new ForceGraph(canvas);
  graph.onExpand = async (node) => {
    try {
      const data = await api.graphExplore(node.name, 1, 40);
      return graph.expand(toGraph(data));
    } catch {
      return null;
    }
  };
  graph.onSelect = (node) => {
    if (node.label === "Document") window.dispatchEvent(new CustomEvent("metis:open-doc", { detail: { id: node.id } }));
  };

  let allNodes = [];
  let allEdges = [];

  function colorNodes(nodes) {
    return nodes.map((n) => {
      if (n.label === "Document") {
        const color = palette[n.corpus] || palette[n.corpora?.[0]] || null;
        return { ...n, color };
      }
      if (n.label === "Entity") {
        const primary = (n.corpora && n.corpora.length) ? n.corpora[0] : n.corpus;
        const color = palette[primary] || null;
        return { ...n, color };
      }
      return n;
    });
  }

  function applyFilter() {
    const visibleNodes = allNodes.filter((n) => {
      if (n.label === "Document" || n.label === "Image") {
        return !n.corpus || enabled.has(n.corpus) || (n.corpora || []).some((c) => enabled.has(c));
      }
      return (n.corpora || []).some((c) => enabled.has(c)) || !(n.corpora && n.corpora.length);
    });
    const visibleIds = new Set(visibleNodes.map((n) => n.id));
    const visibleEdges = allEdges.filter((e) => visibleIds.has(e.source) && visibleIds.has(e.target));
    graph.setData({ nodes: colorNodes(visibleNodes), edges: visibleEdges });
  }

  async function load() {
    empty.classList.remove("hidden");
    canvas.classList.add("hidden");
    try {
      const data = await api.libraryGraph();
      allNodes = data.nodes || [];
      allEdges = (data.edges || []).filter((e) => e.source !== e.target);
      if (!allNodes.length) {
        graph.setData({ nodes: [], edges: [] });
        return;
      }
      applyFilter();
      empty.classList.add("hidden");
      canvas.classList.remove("hidden");
    } catch (err) {
      empty.querySelector(".ge-copy").textContent = `Could not load the library graph: ${err.message || "unknown error"}`;
    }
  }

  function zoomBy(f) {
    const { view, w, h } = graph;
    view.scale = Math.min(3.5, Math.max(0.25, view.scale * f));
    const cx = w / 2, cy = h / 2;
    const wx = (cx - view.x) / view.scale;
    const wy = (cy - view.y) / view.scale;
    view.x = cx - wx * view.scale;
    view.y = cy - wy * view.scale;
    graph.draw();
  }

  const input = searchField.querySelector("input");
  let entityNames = [];
  function collectNames() {
    entityNames = allNodes.filter((n) => n.label === "Entity").map((n) => n.name);
  }
  input.addEventListener("input", () => {
    const q = input.value.trim().toLowerCase();
    if (!q) return;
    const match = entityNames.find((n) => n.toLowerCase().includes(q));
    if (match && match !== graph.selected) focusEntity(match);
  });
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && input.value.trim()) {
      const q = input.value.trim().toLowerCase();
      const match = entityNames.find((n) => n.toLowerCase() === q) || entityNames.find((n) => n.toLowerCase().includes(q));
      if (match) focusEntity(match);
    }
  });

  function focusEntity(name) {
    graph.focus(name);
    const node = graph.byId.get(name);
    if (node && node.label === "Entity" && graph.onExpand) graph.onExpand(node);
  }

  // ── journey ─────────────────────────────────────────────
  const [fromField, toField] = bar.querySelectorAll(".entity-field");
  bar.querySelectorAll("button")[0].addEventListener("click", () => {
    const from = fromField.dataset.value || fromField.value.trim();
    const to = toField.dataset.value || toField.value.trim();
    if (from && to) runJourney(from, to);
    else toast("Pick two entities to find the path between them.", "error");
  });
  bar.querySelectorAll("button")[1].addEventListener("click", () => {
    graph.clearJourney();
    narrative.classList.add("hidden");
    fromField.value = ""; delete fromField.dataset.value;
    toField.value = ""; delete toField.dataset.value;
  });

  async function runJourney(from, to) {
    narrative.classList.add("hidden");
    try {
      const j = await api.libraryJourney(from, to);
      if (!j.found) {
        toast(j.narrative || "No path found.", "error");
        return;
      }
      const names = j.nodes.map((n) => n.name);
      // make sure both endpoints are visible in the graph
      const anyHidden = names.some((n) => !graph.byId.has(n));
      if (anyHidden) applyFilter();
      graph.setJourney(names);
      graph.focus(names[0]);
      renderNarrative(j);
    } catch (err) {
      toast(err.message || "Journey failed.", "error");
    }
  }

  function renderNarrative(j) {
    clear(narrative);
    narrative.classList.remove("hidden");
    const chain = j.nodes.map((nd, i) => {
      const vault = nd.corpora && nd.corpora.length ? nd.corpora[0] : null;
      const tag = vault ? el("span", { class: "jn-vault", style: `--chip:${palette[vault] || "#888"}`, text: vault }) : null;
      return el("span", { class: "jn-step" }, [
        el("span", { class: "jn-name", text: nd.name }),
        tag,
        i < j.nodes.length - 1 ? el("span", { class: "jn-arrow", html: icon("chev-r", 13) }) : null,
      ]);
    });
    narrative.append(
      el("div", { class: "jn-head" }, [
        el("span", { class: "section-label", text: `${j.from} → ${j.to}`, style: "margin:0" }),
        el("span", { class: "jn-meta", text: `${j.nodes.length} ideas · ${j.rels.length} hops` }),
      ]),
      el("div", { class: "jn-chain" }, chain),
      el("p", { class: "jn-story", text: j.narrative })
    );
    narrative.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  body.append(stage);
  if (_themeHandler) document.removeEventListener("metis:theme", _themeHandler);
  _themeHandler = () => graph.refreshTheme();
  document.addEventListener("metis:theme", _themeHandler);

  await load();
  collectNames();

  // expose journey runner for cards that arrive after mount
  window.__metisRunJourney = runJourney;
}

/* ── entity picker ─────────────────────────────────────────── */

function entityField(which) {
  const wrap = el("div", { class: "entity-field", "data-which": which });
  const input = el("input", { placeholder: which === "from" ? "from an idea…" : "to an idea…", autocomplete: "off", "aria-label": which === "from" ? "Journey start" : "Journey destination" });
  const list = el("div", { class: "entity-suggest hidden" });
  wrap.append(input, list);
  let items = [];

  const fetchSuggestions = debounce(async (q) => {
    if (!q) { list.classList.add("hidden"); return; }
    try {
      const res = await api.libraryEntities(q, 8);
      items = (res.entities || []).map((e) => e.name);
      if (!items.length) { list.classList.add("hidden"); return; }
      clear(list);
      items.forEach((name) => {
        const row = el("button", { class: "es-item", type: "button", text: name });
        row.addEventListener("click", () => select(name));
        list.append(row);
      });
      list.classList.remove("hidden");
    } catch { /* quiet */ }
  }, 200);

  function select(name) {
    input.value = name;
    wrap.dataset.value = name;
    list.classList.add("hidden");
  }

  input.addEventListener("input", () => {
    delete wrap.dataset.value;
    fetchSuggestions(input.value.trim());
  });
  input.addEventListener("focus", () => { if (items.length) list.classList.remove("hidden"); });
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      if (items.length && !wrap.dataset.value) select(items[0]);
      else wrap.dataset.value = input.value.trim();
      list.classList.add("hidden");
    } else if (ev.key === "Escape") {
      list.classList.add("hidden");
    }
  });
  document.addEventListener("click", (ev) => {
    if (!wrap.contains(ev.target)) list.classList.add("hidden");
  });
  return wrap;
}

/* ── Surprises tab ─────────────────────────────────────────── */

async function renderSurprises(body, palette) {
  const wrap = el("div", { class: "surprises" });
  wrap.append(
    el("p", { class: "home-sub", text: "Connections the library itself noticed — concepts shared across vaults, and links that cross from one library into another." })
  );
  const grid = el("div", { class: "surprise-grid" });
  wrap.append(grid);
  body.append(wrap);

  const note = el("p", { class: "ask-panel-empty", style: "margin-top:14px" });
  wrap.append(note);

  try {
    const res = await api.librarySurprises();
    const cards = res.cards || [];
    if (!cards.length) {
      note.textContent = res.note || "No surprises yet — index more vaults.";
      return;
    }
    cards.forEach((c) => {
      const card = el("div", { class: "surprise-card" });
      const isShared = c.kind === "shared";
      card.append(
        el("div", { class: "surprise-top" }, [
          el("span", { class: "surprise-kind", text: isShared ? "Shared concept" : "Cross-vault link" }),
          el("span", { class: "surprise-metric", text: isShared ? `${c.degree || 0} connections` : `weight ${(c.weight || 1).toFixed(1)}` }),
        ]),
        isShared
          ? el("div", { class: "surprise-entities" }, [
              el("span", { class: "se-name", text: c.entity }),
              el("span", { class: "se-type", text: c.type || "Concept" }),
            ])
          : el("div", { class: "surprise-entities" }, [
              el("span", { class: "se-name", text: c.source }),
              el("span", { class: "se-link", html: icon("network", 12) }),
              el("span", { class: "se-name", text: c.target }),
            ]),
        el("div", { class: "surprise-vaults" }, vaultChips(c, palette)),
        el("p", { class: "surprise-insight", text: c.insight }),
        el("div", { class: "surprise-actions" }, [
          el("button", { class: "btn btn-soft", html: `${icon("route", 13)}<span>${isShared ? "Show in graph" : "Explore path"}</span>`, style: "padding:6px 11px;font-size:12px" }),
        ])
      );
      card.querySelector(".surprise-actions button").addEventListener("click", () => {
        if (isShared) requestEntityFocus(c.entity);
        else requestJourney(c.source, c.target);
      });
      grid.append(card);
    });
  } catch (err) {
    note.textContent = `Could not load surprises: ${err.message || "unknown error"}`;
  }
}

function vaultChips(c, palette) {
  const names = c.kind === "shared" ? (c.vaults || []) : [c.vault_a, c.vault_b].filter(Boolean);
  return names.map((n) => el("span", { class: "chip chip-mini active", style: `--chip:${palette[n] || "#888"}` }, [
    el("span", { class: "chip-dot" }),
    el("span", { text: n }),
  ]));
}

/** Convert /graph/explore output into ForceGraph data. */
function toGraph(data) {
  const nodes = [];
  const edges = [];
  for (const n of data.neighbors || []) {
    if (!["Entity", "Document", "Image"].includes(n.label)) continue;
    nodes.push({ id: n.value, label: n.label, name: n.value, type: n.label, degree: 1 });
    edges.push({ source: data.entity, target: n.value, kind: "RELATED", label: (n.relationships && n.relationships[0]) || "RELATED_TO", weight: 1 });
  }
  return { nodes, edges };
}
