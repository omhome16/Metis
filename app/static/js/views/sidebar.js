import { el, clear } from "../util.js";
import { icon } from "../icons.js";
import { api } from "../api.js";
import { state } from "../store.js";
import { vaultPath, libraryPath, VAULT_TABS } from "../router.js";
import { openModal, closeModal } from "./modal.js";
import { toast } from "./toast.js";

const SWATCHES = ["#D97757", "#7A9E7E", "#6B7FBC", "#B98A2E", "#8C6A9E", "#5E8A8B"];

const NAV_ITEMS = [
  { id: "graph", label: "Library graph", icon: "network" },
  { id: "surprises", label: "Surprises", icon: "sparkle" },
  { id: "settings", label: "Settings", icon: "gear" },
];

export function renderSidebar() {
  renderLibraryNav();
  renderUserFoot();
  const list = document.getElementById("vaultList");
  if (!list) return;
  clear(list);
  const route = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);

  for (const v of state.vaults) {
    const active = route[0] === "v" && decodeURIComponent(route[1] || "") === v.name;
    const item = el("button", { class: `rail-item${active ? " active" : ""}`, "data-name": v.name });
    item.append(
      el("span", { class: "swatch", style: `background:${v.color || SWATCHES[0]};width:9px;height:9px;border-radius:3px;flex:none;display:inline-block` }),
      el("span", { class: "grow", text: v.name }),
      el("span", { class: "count", text: String(v.doc_count || 0) })
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
  for (const item of NAV_ITEMS) {
    const btn = el("button", {
      class: `rail-item${activeTab === item.id ? " active" : ""}`,
      "data-name": item.label,
    });
    btn.append(
      el("span", { class: "icon", html: icon(item.icon, 15) }),
      el("span", { class: "grow", text: item.label })
    );
    btn.addEventListener("click", () => {
      location.hash = item.id === "settings" ? "#/settings" : libraryPath(item.id);
    });
    list.append(btn);
  }
}

function renderUserFoot() {
  const foot = document.querySelector(".rail-foot");
  if (!foot) return;
  clear(foot);
  if (state.user) {
    const who = el("div", { class: "rail-item", style: "cursor:default" });
    who.append(
      el("span", { class: "icon", html: icon("user", 15) }),
      el("span", { class: "grow", text: state.user.email || "signed in" })
    );
    const out = el("button", {
      class: "icon-btn",
      title: "Sign out",
      "aria-label": "Sign out",
      html: icon("logout", 15),
    });
    out.addEventListener("click", signOut);
    who.append(out);
    foot.append(who);
  } else {
    foot.append(el("div", { class: "rail-caption", text: "A corpus is a library. A vault is how you keep it." }));
  }
}

export function signOut() {
  import("../api.js").then(({ setJwt }) => {
    setJwt("");
    location.hash = "#/login";
    location.reload();
  });
}

function currentTab(name) {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (parts[0] === "v" && decodeURIComponent(parts[1] || "") === name && VAULT_TABS.includes(parts[2])) {
    return parts[2];
  }
  return "documents";
}

export function openCreateVault(onCreated) {
  const nameInput = el("input", { class: "input", placeholder: "e.g. Research, Novels, Self-Development", maxlength: 128 });
  const descInput = el("textarea", { class: "input", placeholder: "What lives in this vault? (optional)", rows: 2 });
  const colorRow = el("div", { style: "display:flex;gap:8px" });
  let color = SWATCHES[0];
  for (const c of SWATCHES) {
    const dot = el("button", {
      type: "button",
      class: `color-dot${c === color ? " selected" : ""}`,
      style: `background:${c};width:26px;height:26px;border-radius:50%;border:2px solid ${c === color ? "var(--ink)" : "transparent"};cursor:pointer`,
    });
    dot.addEventListener("click", () => {
      color = c;
      colorRow.querySelectorAll(".color-dot").forEach((d) => {
        d.style.borderColor = d === dot ? "var(--ink)" : "transparent";
      });
    });
    colorRow.append(dot);
  }

  const createBtn = el("button", { class: "btn btn-primary", text: "Create vault" });
  createBtn.addEventListener("click", async () => {
    const vname = nameInput.value.trim();
    if (!vname) { toast("Give the vault a name first.", "error"); return; }
    createBtn.disabled = true;
    createBtn.textContent = "Creating…";
    try {
      const vault = await api.createVault({ name: vname, description: descInput.value.trim() || null, color });
      state.vaults.push(vault);
      state.vaults.sort((a, b) => a.name.localeCompare(b.name));
      renderSidebar();
      closeModal();
      toast(`Vault "${vname}" created.`, "ok");
      if (onCreated) onCreated(vault);
    } catch (err) {
      toast(err.message || "Could not create vault.", "error");
      createBtn.disabled = false;
      createBtn.textContent = "Create vault";
    }
  });

  openModal({
    title: "New vault",
    sub: "A vault is a library of documents, its knowledge graph, and a chat over both.",
    body: (b) => {
      b.append(
        el("div", { class: "field" }, [el("label", { text: "Name" }), nameInput]),
        el("div", { class: "field" }, [el("label", { text: "Description" }), descInput]),
        el("div", { class: "field" }, [el("label", { text: "Accent" }), colorRow])
      );
      nameInput.focus();
    },
    footer: [
      el("button", { class: "btn btn-ghost", text: "Cancel", onclick: () => closeModal() }),
      createBtn,
    ],
  });
}
