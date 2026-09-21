import { el, clear, fmtBytes, fmtDate } from "../util.js";
import { icon } from "../icons.js";
import { api } from "../api.js";
import { state } from "../store.js";
import { vaultPath } from "../router.js";
import { openCreateVault, renderSidebar } from "./sidebar.js";

export async function renderHome(view) {
  clear(view);
  const inner = el("div", { class: "home-wrap" });
  view.append(inner);

  const user = state.user;
  inner.append(
    el("div", { class: "home-hero" }, [
      el("h1", { text: user ? `Good to see you, ${firstName(user)}` : "Your library" }),
      el("p", {
        text:
          "Drop in documents — Metis indexes them, maps how the ideas connect, flags where your sources disagree, and answers questions with citations.",
      }),
    ])
  );

  const vaults = state.vaults.length ? state.vaults : await api.vaults().catch(() => []);
  if (state.vaults.length !== vaults.length) {
    state.vaults = vaults;
    renderSidebar();
  }

  if (!vaults.length) {
    const empty = el("div", { class: "empty" });
    empty.append(
      el("div", { class: "glyph", html: icon("vault", 30) }),
      el("h3", { text: "No vaults yet" }),
      el("p", {
        text:
          "Create a vault and add your first documents — PDF, EPUB, markdown, plain text, a web article, or your Readwise highlights.",
      }),
      el("button", { class: "btn btn-primary", text: "Create your first vault" })
    );
    empty.querySelector("button").addEventListener("click", () => openCreateVault(() => renderHome(view)));
    inner.append(empty);
    return;
  }

  const cards = el("div", { class: "vault-grid" });
  for (const v of vaults) {
    const card = el("div", { class: "vault-card card" });
    card.append(
      el("h3", {}, [
        el("span", { style: `display:inline-block;width:9px;height:9px;border-radius:3px;background:${v.color || "#D97757"};margin-right:8px;vertical-align:2px` }),
        el("span", { text: v.name }),
      ]),
      el("p", { class: "desc", text: v.description || "No description yet." }),
      el("div", { class: "vault-stats" }, [
        el("span", { text: `${v.doc_count} docs` }),
        el("span", { text: `${v.chunk_count} chunks` }),
        el("span", { text: `${v.entity_count} entities` }),
      ])
    );
    card.addEventListener("click", () => { location.hash = vaultPath(v.name, "documents"); });
    cards.append(card);
  }
  inner.append(
    el("div", { class: "section-head" }, [
      el("h2", { text: "Vaults" }),
      el("button", { class: "btn btn-secondary btn-sm", text: "New vault" }),
    ]),
    cards
  );
  inner.querySelector(".section-head button").addEventListener("click", () => openCreateVault(() => renderHome(view)));

  const recent = await api.recentDocs().catch(() => []);
  if (recent.length) {
    inner.append(el("h2", { style: "margin:26px 0 12px", text: "Recently added" }));
    const list = el("div", { class: "recent-list" });
    for (const d of recent) {
      const row = el("div", { class: "recent-row" });
      row.append(
        el("span", { html: icon(d.format === "image" ? "image" : d.format === "pdf" ? "pdf" : "file", 15), style: "color:var(--ink-3);display:flex" }),
        el("span", { class: "title", text: d.title }),
        el("span", { class: "meta", text: `${d.corpus} · ${fmtBytes(d.size)} · ${fmtDate(d.ingested_at)}` })
      );
      row.addEventListener("click", () => { location.hash = vaultPath(d.corpus, "documents"); });
      list.append(row);
    }
    inner.append(list);
  }
}

function firstName(user) {
  const n = user.display_name || user.email || "";
  return n.split("@")[0].split(/[.\s_-]/)[0].replace(/^./, (c) => c.toUpperCase()) || "there";
}
