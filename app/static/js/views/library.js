/* Cross-vault library: one graph over every vault + "surprises" (cross-vault
   connections) + the reorganization log. */

import { el, clear, fmtDate } from "../util.js";
import { icon } from "../icons.js";
import { api } from "../api.js";
import { createGraph } from "../graph.js";
import { vaultPath } from "../router.js";
import { toast } from "./toast.js";

export async function renderLibrary(view, tab) {
  clear(view);

  if (tab === "surprises") return renderSurprises(view);

  const wrap = el("div", { class: "graph-wrap" });
  view.append(wrap);
  const holder = el("div", { class: "graph-canvas-holder" });
  wrap.append(holder);

  const legend = el("div", { class: "graph-legend" });
  legend.append(
    el("span", {}, [el("span", { class: "dot", style: "background:#D97757" }), "Entity"]),
    el("span", {}, [el("span", { class: "dot", style: "background:#6B7FBC" }), "Document"]),
    el("span", {}, [el("span", { class: "dot", style: "background:var(--warn)" }), "Bridge (cross-vault)"])
  );
  holder.append(legend);

  const graph = createGraph(holder, {
    onSelect: async (node) => {
      if (!node || node.label !== "Entity") return;
      try {
        const entities = await api.libraryEntities(node.name, 5).catch(() => null);
        if (entities?.results?.length) {
          const hit = entities.results[0];
          toast(`${node.name} appears in ${hit.vaults?.join(", ") || "multiple vaults"}`, "info", 5000);
        }
      } catch { /* panel-free explorer */ }
    },
  });

  try {
    graph.setData(await api.libraryGraph());
  } catch {
    graph.setData({ nodes: [], edges: [] });
  }

  const side = el("div", { class: "entity-panel" });
  wrap.append(side);
  side.append(
    el("h2", { text: "Library graph" }),
    el("p", { class: "section-sub", style: "margin-top:4px", text: "Every vault, one map. Entities that appear in more than one vault are highlighted as bridges." })
  );

  const runs = await api.reorgRuns(5).catch(() => []);
  if (runs.length) {
    side.append(el("h3", { style: "margin:18px 0 8px", text: "Recent reorganizations" }));
    const list = el("div", { class: "recent-list" });
    for (const r of runs) {
      const row = el("div", { class: "recent-row", style: "cursor:default" });
      row.append(
        el("span", { class: "title", text: `${r.communities_after ?? "?"} communities · ${r.summaries_made ?? 0} summaries` }),
        el("span", { class: "meta", text: fmtDate(r.run_at) })
      );
      list.append(row);
    }
    side.append(list);
  }

  const runBtn = el("button", {
    class: "btn btn-secondary",
    style: "width:100%;margin-top:16px",
    html: `${icon("refresh", 14)} Reorganize now`,
  });
  runBtn.addEventListener("click", async () => {
    runBtn.disabled = true;
    try {
      const res = await api.runCommunities();
      toast(`Detected ${res.communities ?? 0} communities, wrote ${res.summaries ?? 0} summaries.`, "ok");
      graph.setData(await api.libraryGraph());
    } catch (err) {
      toast(err.message || "Reorganization failed.", "error");
    } finally {
      runBtn.disabled = false;
    }
  });
  side.append(runBtn);
}

async function renderSurprises(view) {
  const wrap = el("div", { class: "docs-wrap" });
  view.append(wrap);
  wrap.append(
    el("div", { class: "section-head" }, [
      el("div", {}, [
        el("h1", { text: "Surprises" }),
        el("p", { class: "section-sub", text: "Connections your documents didn't plan — shared entities and ideas across different vaults." }),
      ]),
    ])
  );

  const box = el("div");
  wrap.append(box);

  try {
    const data = await api.librarySurprises();
    const items = data?.surprises || [];
    if (!items.length) {
      box.append(
        el("div", { class: "empty" }, [
          el("div", { class: "glyph", html: icon("sparkle", 26) }),
          el("h3", { text: "No surprises yet" }),
          el("p", { text: "Surprises appear when two different vaults talk about the same entity — add documents in more than one vault." }),
        ])
      );
      return;
    }
    const grid = el("div", { class: "vault-grid" });
    for (const s of items) {
      const card = el("div", { class: "vault-card card" });
      card.append(
        el("div", { style: "display:flex;align-items:center;gap:8px;margin-bottom:8px" }, [
          el("span", { html: icon("sparkle", 15), style: "color:var(--accent);display:flex" }),
          el("span", { class: "chip chip-accent", text: s.kind || "connection" }),
        ]),
        el("h3", { text: s.entities?.map((e) => e.name).join(" ↔ ") || "Connection" }),
        el("p", { class: "desc", text: s.insight || s.summary || "" }),
        el("div", { class: "vault-stats" }, [
          el("span", { text: (s.vaults || []).join(" · ") }),
        ])
      );
      const first = s.vaults?.[0];
      if (first) {
        card.addEventListener("click", () => { location.hash = vaultPath(first, "graph"); });
        card.style.cursor = "pointer";
      }
      grid.append(card);
    }
    box.append(grid);
  } catch (err) {
    box.append(el("p", { class: "muted", text: err.message || "Surprises are unavailable." }));
  }
}
