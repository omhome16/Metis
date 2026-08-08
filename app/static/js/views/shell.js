import { el, clear } from "../util.js";
import { icon } from "../icons.js";
import { api } from "../api.js";
import { state } from "../store.js";
import { vaultPath } from "../router.js";
import { openModal, closeModal, confirmDialog } from "./modal.js";
import { toast } from "./toast.js";
import { renderSidebar } from "./sidebar.js";

export const TABS = [
  { id: "documents", label: "Documents" },
  { id: "graph", label: "Graph" },
  { id: "ask", label: "Ask" },
];

/** Renders the vault header + tabs into `container`, returns the body element. */
export function renderVaultShell(container, vault, activeTab) {
  clear(container);
  const inner = el("div", { class: "view-inner" });
  container.append(inner);

  const head = el("div", { class: "vault-head" });
  const main = el("div", { class: "vault-head-main" });
  main.append(
    el("div", { class: "vault-eyebrow" }, [
      el("span", { class: "swatch", style: `background:${vault.color || "#2E6B4E"}` }),
      el("span", { text: "Vault" }),
    ]),
    el("h1", { class: "vault-title", text: vault.name }),
    vault.description ? el("p", { class: "vault-desc", text: vault.description }) : null
  );
  const actions = el("div", { class: "vault-actions" });
  actions.append(
    el("button", { class: "btn btn-ghost", html: `${icon("edit", 15)}<span>Edit</span>` }),
    el("button", { class: "btn btn-ghost", html: `${icon("trash", 15)}<span>Delete</span>`, style: "color:var(--err)" })
  );
  actions.querySelectorAll("button")[0].addEventListener("click", () => openEditVault(vault));
  actions.querySelectorAll("button")[1].addEventListener("click", () => deleteVaultFlow(vault));
  head.append(main, actions);
  inner.append(head);

  const stats = el("div", { class: "vault-stats" });
  const stat = (n, label) => el("span", { class: "stat-chip" }, [
    el("span", { class: "stat-num", text: String(n) }),
    el("span", { class: "stat-label", text: label }),
  ]);
  stats.append(
    stat(vault.doc_count ?? 0, "documents"),
    stat(vault.chunk_count ?? 0, "chunks"),
    stat(vault.entity_count ?? 0, "entities"),
    stat(vault.image_count ?? 0, "images")
  );
  inner.append(stats);

  const tabs = el("div", { class: "tabs" });
  for (const t of TABS) {
    const tab = el("button", { class: `tab${t.id === activeTab ? " active" : ""}`, text: t.label });
    tab.addEventListener("click", () => { location.hash = vaultPath(vault.name, t.id); });
    tabs.append(tab);
  }
  inner.append(tabs);

  const body = el("div");
  inner.append(body);
  return body;
}

async function refreshVault(vault) {
  try {
    const fresh = await api.vaultDetail(vault.name);
    const idx = state.vaults.findIndex((v) => v.name === vault.name);
    if (idx >= 0) state.vaults[idx] = fresh;
    return fresh;
  } catch {
    return vault;
  }
}

function openEditVault(vault) {
  const descInput = el("textarea", { rows: 3, text: vault.description || "" });
  const colorRow = el("div", { class: "color-row" });
  const SWATCHES = ["#2E6B4E", "#B0613A", "#3B5A79", "#7A5C8C", "#96691F", "#5E6B4E"];
  let color = vault.color || SWATCHES[0];
  for (const c of SWATCHES) {
    const dot = el("button", { type: "button", class: `color-dot${c === color ? " selected" : ""}`, style: `background:${c}` });
    dot.addEventListener("click", () => {
      color = c;
      colorRow.querySelectorAll(".color-dot").forEach((d) => d.classList.toggle("selected", d.style.background === c));
    });
    colorRow.append(dot);
  }
  const save = el("button", { class: "btn btn-primary", text: "Save changes" });
  save.addEventListener("click", async () => {
    try {
      await api.updateVault(vault.name, { description: descInput.value.trim() || null, color });
      closeModal();
      toast("Vault updated.", "ok");
      const fresh = await refreshVault(vault);
      renderSidebar();
      document.getElementById("view").dispatchEvent(new CustomEvent("metis:vault-updated", { detail: fresh }));
    } catch (err) {
      toast(err.message || "Could not update vault.", "error");
    }
  });
  openModal({
    title: `Edit ${vault.name}`,
    body: (b) => {
      const grid = el("div", { class: "form-grid" });
      grid.append(
        el("div", { class: "form-field" }, [el("label", { text: "Description" }), descInput]),
        el("div", { class: "form-field" }, [el("label", { text: "Accent" }), colorRow])
      );
      b.append(grid);
    },
    footer: [el("button", { class: "btn btn-ghost", text: "Cancel", onclick: () => closeModal() }), save],
  });
}

async function deleteVaultFlow(vault) {
  const ok = await confirmDialog({
    title: `Delete "${vault.name}"?`,
    message: `This permanently removes the vault, its ${vault.doc_count || 0} documents, chunks, and graph nodes. This cannot be undone.`,
    confirmLabel: "Delete vault",
  });
  if (!ok) return;
  try {
    await api.deleteVault(vault.name);
    state.vaults = state.vaults.filter((v) => v.name !== vault.name);
    renderSidebar();
    toast(`Vault "${vault.name}" deleted.`, "ok");
    location.hash = "#/";
  } catch (err) {
    toast(err.message || "Could not delete vault.", "error");
  }
}
