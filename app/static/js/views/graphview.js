import { el, clear } from "../util.js";
import { icon } from "../icons.js";
import { api } from "../api.js";
import { renderVaultShell } from "./shell.js";
import { ForceGraph } from "../graph.js";

export async function renderGraph(container, vault) {
  const body = renderVaultShell(container, vault, "graph");

  const stage = el("div", { class: "graph-stage" });
  const canvas = el("canvas");
  stage.append(canvas);

  // chrome
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

  const legend = el("div", { class: "graph-legend" }, [
    el("span", { class: "legend-item" }, [el("span", { class: "legend-dot", style: "background:var(--node-entity)" }), el("span", { text: "Entity" })]),
    el("span", { class: "legend-item" }, [el("span", { class: "legend-dot sq", style: "background:var(--node-doc)" }), el("span", { text: "Document" })]),
    el("span", { class: "legend-item" }, [el("span", { class: "legend-dot sq", style: "background:var(--node-image)" }), el("span", { text: "Image" })]),
  ]);
  stage.append(legend);
  stage.append(el("div", { class: "graph-hint", text: "drag nodes · scroll to zoom · click entity to expand" }));

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
    if (node.label === "Document") {
      openDocFromGraph(node);
    }
  };

  const empty = el("div", { class: "graph-empty hidden" }, [
    el("div", {}, [
      el("div", { class: "ge-title", text: "No graph yet" }),
      el("div", { class: "ge-copy", text: "Entities appear here once documents are indexed — Metis extracts concepts and the relationships between them." }),
    ]),
  ]);
  stage.append(empty);

  const input = searchField.querySelector("input");
  let entityNames = [];
  input.addEventListener("input", () => {
    const q = input.value.trim().toLowerCase();
    if (!q) return;
    const match = entityNames.find((n) => n.toLowerCase().includes(q));
    if (match && match !== graph.selected) {
      focusEntity(match);
    }
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
    if (node && node.label === "Entity" && graph.onExpand) {
      graph.onExpand(node);
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

  async function load() {
    empty.classList.remove("hidden");
    canvas.classList.add("hidden");
    try {
      const data = await api.graph(vault.name);
      entityNames = data.nodes.filter((n) => n.label === "Entity").map((n) => n.name);
      if (!data.nodes.length) {
        graph.setData({ nodes: [], edges: [] });
        return;
      }
      graph.setData(data);
      empty.classList.add("hidden");
      canvas.classList.remove("hidden");
    } catch (err) {
      empty.querySelector(".ge-copy").textContent = `Could not load the graph: ${err.message || "unknown error"}`;
    }
  }

  body.append(stage);

  // theme changes should recolor the canvas
  document.addEventListener("metis:theme", () => graph.refreshTheme());

  await load();
}

/** Convert /graph/explore output into ForceGraph data. */
function toGraph(data) {
  const nodes = [];
  const edges = [];
  for (const n of data.neighbors || []) {
    if (!["Entity", "Document", "Image"].includes(n.label)) continue;
    nodes.push({
      id: n.value,
      label: n.label === "Entity" ? "Entity" : n.label,
      name: n.value,
      type: n.label,
      degree: 1,
    });
    edges.push({
      source: data.entity,
      target: n.value,
      kind: "RELATED",
      label: (n.relationships && n.relationships[0]) || "RELATED_TO",
      weight: 1,
    });
  }
  return { nodes, edges };
}

function openDocFromGraph(node) {
  // navigate to the documents tab of the vault (document list shows the file)
  window.dispatchEvent(new CustomEvent("metis:open-doc", { detail: { id: node.id, title: node.name } }));
}
