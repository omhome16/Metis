import { el, clear } from "../util.js";
import { icon } from "../icons.js";
import { api } from "../api.js";
import { state } from "../store.js";
import { vaultPath, libraryPath } from "../router.js";
import { openModal, closeModal } from "./modal.js";
import { toast } from "./toast.js";

const SWATCHES = ["#2E6B4E", "#B0613A", "#3B5A79", "#7A5C8C", "#96691F", "#5E6B4E"];

const LIBRARY_ITEMS = [
  { id: "graph", label: "Library graph", icon: "network" },
  { id: "surprises", label: "Surprises", icon: "sparkle" },
  { id: "settings", label: "Settings", icon: "gear" },
];

export function renderSidebar() {
  renderLibraryNav();
  const list = document.getElementById("vaultList");
  clear(list);
  const route = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);

  for (const v of state.vaults) {
    const active = route[0] === "v" && decodeURIComponent(route[1] || "") === v.name;
    const item = el("button", {
      class: `vault-item${active ? " active" : ""}`,
      "data-name": v.name,
    });
    item.append(
      el("span", { class: "vault-swatch", style: `background:${v.color || SWATCHES[0]}` }),
      el("span", { class: "vault-name", text: v.name }),
      el("span", { class: "vault-count", text: String(v.doc_count || 0) })
    );
    item.addEventListener("click", () => {
      location.hash = vaultPath(v.name, active ? currentTab(v.name) : "documents");
    });
    list.append(item);
  }
}

function renderLibraryNav() {
  const list = document.getElementById("libraryList");
  if (!list) return;
  clear(list);
  const route = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  const activeTab = route[0] === "library" ? route[1] : route[0] === "settings" ? "settings" : null;
  for (const item of LIBRARY_ITEMS) {
    const btn = el("button", {
      class: `vault-item library-item${activeTab === item.id ? " active" : ""}`,
      "data-name": item.label,
    });
    btn.append(
      el("span", { class: "vault-swatch lib", html: icon(item.icon, 12) }),
      el("span", { class: "vault-name", text: item.label })
    );
    btn.addEventListener("click", () => {
      location.hash = item.id === "settings" ? "#/settings" : libraryPath(item.id);
    });
    list.append(btn);
  }
}

function currentTab(name) {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (parts[0] === "v" && decodeURIComponent(parts[1] || "") === name && ["documents", "graph", "ask"].includes(parts[2])) {
    return parts[2];
  }
  return "documents";
}

export function openCreateVault(onCreated) {
  const nameInput = el("input", { placeholder: "e.g. Research, Legal, Product Docs", maxlength: 128 });
  const descInput = el("textarea", { placeholder: "What lives in this vault? (optional)", rows: 2 });
  const colorRow = el("div", { class: "color-row" });
  let color = SWATCHES[0];
  for (const c of SWATCHES) {
    const dot = el("button", { type: "button", class: `color-dot${c === color ? " selected" : ""}`, style: `background:${c}` });
    dot.addEventListener("click", () => {
      color = c;
      colorRow.querySelectorAll(".color-dot").forEach((d) => d.classList.toggle("selected", d.style.background === c));
    });
    colorRow.append(dot);
  }

  const createBtn = el("button", { class: "btn btn-primary", text: "Create vault" });
  createBtn.addEventListener("click", async () => {
    const name = nameInput.value.trim();
    if (!name) { toast("Give the vault a name first.", "error"); return; }
    createBtn.disabled = true;
    createBtn.textContent = "Creating…";
    try {
      const vault = await api.createVault({ name, description: descInput.value.trim() || null, color });
      state.vaults.push(vault);
      state.vaults.sort((a, b) => a.name.localeCompare(b.name));
      renderSidebar();
      closeModal();
      toast(`Vault "${name}" created.`, "ok");
      if (onCreated) onCreated(vault);
    } catch (err) {
      toast(err.message || "Could not create vault.", "error");
      createBtn.disabled = false;
      createBtn.textContent = "Create vault";
    }
  });

  openModal({
    title: "New vault",
    sub: "A vault is a library of documents, their knowledge graph, and a chat over both.",
    body: (b) => {
      const grid = el("div", { class: "form-grid" });
      grid.append(
        el("div", { class: "form-field" }, [el("label", { text: "Name" }), nameInput]),
        el("div", { class: "form-field" }, [el("label", { text: "Description" }), descInput]),
        el("div", { class: "form-field" }, [el("label", { text: "Accent" }), colorRow])
      );
      b.append(grid);
      nameInput.focus();
    },
    footer: [el("button", { class: "btn btn-ghost", text: "Cancel", onclick: () => closeModal() }), createBtn],
  });
}
