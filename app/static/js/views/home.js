import { el, clear, fmtBytes, fmtDate } from "../util.js";
import { icon } from "../icons.js";
import { api } from "../api.js";
import { state } from "../store.js";
import { vaultPath } from "../router.js";
import { openCreateVault } from "./sidebar.js";
import { renderSidebar } from "./sidebar.js";

export async function renderHome(view) {
  clear(view);
  const inner = el("div", { class: "view-inner" });
  view.append(inner);

  inner.append(
    el("h1", { class: "home-title", text: "Your vaults" }),
    el("p", { class: "home-sub", text: "Libraries of documents, each with its own knowledge graph and a grounded chat." })
  );

  const vaults = state.vaults.length ? state.vaults : await api.vaults().catch(() => []);
  if (state.vaults.length !== vaults.length) {
    state.vaults = vaults;
    renderSidebar();
  }

  if (!vaults.length) {
    const empty = el("div", { class: "empty" });
    empty.append(
      el("div", { class: "empty-glyph", html: icon("vault", 26) }),
      el("div", { class: "empty-title", text: "No vaults yet" }),
      el("p", { class: "empty-copy", text: "Create a vault and drop in your first documents — a PDF, markdown, plain text, or an image. Metis will index it, map the relationships, and answer questions grounded in what you gave it." }),
      el("button", { class: "btn btn-primary", text: "Create your first vault" })
    );
    empty.querySelector("button").addEventListener("click", () => openCreateVault(() => renderHome(view)));
    inner.append(empty);
    return;
  }

  const cards = el("div", { class: "vault-cards" });
  for (const v of vaults) {
    const card = el("div", { class: "vault-card" });
    card.append(
      el("div", { class: "vault-card-name" }, [
        el("span", { class: "swatch", style: `background:${v.color || "#2E6B4E"}` }),
        el("span", { text: v.name }),
      ]),
      el("div", { class: "vault-card-desc", text: v.description || "No description yet." }),
      el("div", { class: "vault-card-stats" }, [
        el("span", { text: `${v.doc_count} documents` }),
        el("span", { text: `${v.chunk_count} chunks` }),
        el("span", { text: `${v.entity_count} entities` }),
      ])
    );
    card.addEventListener("click", () => { location.hash = vaultPath(v.name, "documents"); });
    cards.append(card);
  }
  inner.append(el("div", { class: "section-label", text: "Vaults" }), cards);

  // recent documents
  const recent = await api.recentDocs().catch(() => []);
  if (recent.length) {
    inner.append(el("div", { class: "section-label", style: "margin-top:8px", text: "Recently added" }));
    const list = el("div", { class: "recent-list" });
    for (const d of recent) {
      const row = el("div", { class: "recent-row" });
      const vault = state.vaults.find((v) => v.name === d.corpus);
      row.append(
        el("span", { class: "doc-glyph", html: icon(d.format === "image" ? "image" : d.format === "pdf" ? "pdf" : d.format === "txt" ? "txt" : "doc", 15), style: "width:26px;height:26px" }),
        el("span", { class: "recent-title", text: d.title }),
        el("span", { class: "recent-meta", text: `${vault ? vault.name : d.corpus} · ${fmtBytes(d.size)} · ${fmtDate(d.ingested_at)}` })
      );
      row.addEventListener("click", () => { location.hash = vaultPath(d.corpus, "documents"); });
      list.append(row);
    }
    inner.append(list);
  }
}
