/* Vault knowledge-graph explorer: canvas + entity side panel. */

import { el, clear } from "../util.js";
import { icon } from "../icons.js";
import { api } from "../api.js";
import { createGraph } from "../graph.js";
import { vaultPath } from "../router.js";

export async function renderGraph(view, vault) {
  clear(view);
  const wrap = el("div", { class: "graph-wrap" });
  view.append(wrap);

  const holder = el("div", { class: "graph-canvas-holder" });
  wrap.append(holder);

  let panel = null;

  const legend = el("div", { class: "graph-legend" });
  legend.append(
    legendDot("entity", "Entity"),
    legendDot("document", "Document")
  );
  holder.append(legend);

  const search = el("input", {
    class: "input",
    placeholder: "Jump to an entity…",
    style: "position:absolute;top:14px;left:14px;width:220px;box-shadow:var(--shadow-1)",
  });
  search.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      graph.selectByName(search.value.trim());
      search.blur();
    }
  });
  holder.append(search);

  const graph = createGraph(holder, {
    onSelect: (node) => showPanel(node),
    onEmpty: () => {
      holder.append(
        el("div", { class: "empty", style: "position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;background:var(--bg)" }, [
          el("div", { class: "glyph", html: icon("network", 28) }),
          el("h3", { text: "The graph is empty" }),
          el("p", { text: "Entities and relations are extracted while documents are indexed. Add documents to this vault and the map will draw itself." }),
        ])
      );
    },
  });

  try {
    graph.setData(await api.graph(vault.name));
  } catch {
    graph.setData({ nodes: [], edges: [] });
  }

  function legendDot(kind, label) {
    const s = el("span", { class: "dot", style: `background:${kind === "entity" ? "#D97757" : "#6B7FBC"}` });
    const item = el("span", {}, [s, label]);
    return item;
  }

  async function showPanel(node) {
    if (panel) panel.remove();
    if (!node) return;

    panel = el("div", { class: "entity-panel" });
    wrap.append(panel);

    panel.append(
      el("button", {
        class: "icon-btn",
        style: "float:right",
        title: "Close panel",
        html: icon("x", 14),
      }),
      el("h3", { text: node.name || node.id?.slice(0, 10) || "Node" }),
      el("div", { class: "entity-kind", text: node.label || "Node" })
    );
    panel.querySelector(".icon-btn").addEventListener("click", () => {
      panel.remove();
      panel = null;
    });

    if (node.label === "Entity") {
      const chatBtn = el("button", {
        class: "btn btn-primary",
        style: "width:100%;margin-bottom:16px",
        html: `${icon("chat", 15)} Chat about this`,
      });
      chatBtn.addEventListener("click", () => {
        // hand the question to the chat view; deep lane boosts with the graph
        sessionStorage.setItem("metis.askPrefill", `What is ${node.name}, and how does it connect to the rest of this vault?`);
        location.hash = vaultPath(vault.name, "ask");
      });
      panel.append(chatBtn);

      const listWrap = el("div");
      listWrap.append(el("div", { class: "section-sub", style: "margin-bottom:6px", text: "Connected" }));
      const list = el("div", { class: "entity-neighbor-list" });
      listWrap.append(list);
      panel.append(listWrap);
      list.append(el("div", { class: "muted small", text: "Expanding…" }));

      try {
        const res = await api.graphExplore(node.name, 1, 40);
        clear(list);
        const neighbors = (res.neighbors?.nodes || [])
          .filter((n) => n.name !== node.name)
          .slice(0, 18);
        const edgeType = (id) => {
          const e = (res.neighbors?.edges || []).find(
            (x) => (x.source === node.id && x.target === id) || (x.target === node.id && x.source === id)
          );
          return e ? e.type || "related" : "";
        };
        if (!neighbors.length) {
          list.append(el("div", { class: "muted small", text: "No direct neighbors found." }));
        }
        for (const n of neighbors) {
          const btn = el("button", { class: "entity-neighbor" });
          btn.append(
            el("span", {
              class: "dot",
              style: `display:inline-block;width:7px;height:7px;border-radius:50%;flex:none;background:${n.label === "Document" ? "#6B7FBC" : "#D97757"}`,
            }),
            el("span", { class: "grow", text: n.name || n.id?.slice(0, 8), style: "flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }),
            el("span", { class: "rel", text: edgeType(n.id) })
          );
          btn.addEventListener("click", () => {
            if (n.label === "Entity") {
              graph.selectByName(n.name);
              search.value = n.name;
            } else {
              location.hash = vaultPath(vault.name, "documents");
            }
          });
          list.append(btn);
        }
      } catch {
        clear(list);
        list.append(el("div", { class: "muted small", text: "Graph service unreachable." }));
      }
    } else if (node.label === "Document") {
      const open = el("button", {
        class: "btn btn-secondary",
        style: "width:100%;margin-bottom:8px",
        html: `${icon("file", 15)} Open document`,
      });
      open.addEventListener("click", () => {
        location.hash = vaultPath(vault.name, "documents");
      });
      panel.append(open);
    }
  }
}
