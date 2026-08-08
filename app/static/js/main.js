import { initTheme, toggleTheme, currentTheme } from "./theme.js";
import { icon } from "./icons.js";
import { api } from "./api.js";
import { state } from "./store.js";
import { parseHash, vaultPath } from "./router.js";
import { renderSidebar, openCreateVault } from "./views/sidebar.js";
import { renderHome } from "./views/home.js";
import { renderDocuments } from "./views/documents.js";
import { renderGraph } from "./views/graphview.js";
import { renderAsk } from "./views/ask.js";

async function boot() {
  initTheme();

  // brand + topbar
  document.getElementById("brandMark").innerHTML = icon("mark", 22);
  const themeBtn = document.getElementById("themeToggle");
  const paintThemeIcon = () => {
    themeBtn.innerHTML = icon(currentTheme() === "dark" ? "sun" : "moon", 17);
  };
  paintThemeIcon();
  themeBtn.addEventListener("click", toggleTheme);
  document.addEventListener("metis:theme", paintThemeIcon);

  document.getElementById("newVaultBtn").innerHTML = icon("plus", 15);
  document.getElementById("newVaultBtn").addEventListener("click", () =>
    openCreateVault(() => { location.hash = "#/"; })
  );

  await refreshVaults();
  initStatusbar();

  const render = async () => {
    const route = parseHash();
    const view = document.getElementById("view");
    paintTopbar(route);

    if (route.name === "home") {
      await renderHome(view);
      return;
    }

    let vault = state.vaults.find((v) => v.name === route.vault);
    if (!vault) {
      try {
        vault = await api.vaultDetail(route.vault);
        state.vaults.push(vault);
        state.vaults.sort((a, b) => a.name.localeCompare(b.name));
        renderSidebar();
      } catch {
        location.hash = "#/";
        return;
      }
    }
    state.current = vault;

    if (route.tab === "graph") await renderGraph(view, vault);
    else if (route.tab === "ask") await renderAsk(view, vault);
    else await renderDocuments(view, vault);
  };

  window.addEventListener("hashchange", render);

  // A document node clicked in the graph jumps to the vault's document library.
  window.addEventListener("metis:open-doc", () => {
    if (state.current && parseHash().name === "vault") {
      const route = parseHash();
      if (route.tab !== "documents") location.hash = vaultPath(route.vault, "documents");
    }
  });

  // No hash → land on the overview so all vaults are visible first.
  await render();
}

async function refreshVaults() {
  try {
    state.vaults = await api.vaults();
  } catch {
    state.vaults = [];
  }
  renderSidebar();
}

function paintTopbar(route) {
  const ctx = document.getElementById("topbarContext");
  ctx.innerHTML = "";
  if (route.name === "home") {
    ctx.append(crumb("Overview", true));
  } else {
    ctx.append(
      crumb(route.vault, false, () => { location.hash = "#/"; }),
      elSep(),
      crumb(route.tab[0].toUpperCase() + route.tab.slice(1), true)
    );
  }
}

function crumb(text, here, onClick) {
  const s = document.createElement("span");
  s.className = `crumb${here ? " here" : ""}`;
  s.textContent = text;
  if (onClick) {
    s.style.cursor = "pointer";
    s.addEventListener("click", onClick);
  }
  return s;
}
function elSep() {
  const s = document.createElement("span");
  s.className = "crumb";
  s.textContent = "/";
  return s;
}

/* ── status bar ───────────────────────────────────────────── */

function initStatusbar() {
  const chips = {
    db: document.querySelector('[data-svc="db"]'),
    redis: document.querySelector('[data-svc="redis"]'),
    graph: document.querySelector('[data-svc="graph"]'),
    cache: document.querySelector('[data-svc="cache"]'),
    model: document.querySelector('[data-svc="model"]'),
  };
  chips.model.querySelector(".chip-label").textContent = "model · bge-m3 / bge-reranker / clip";

  const setState = (key, ok) => {
    chips[key].classList.remove("ok", "down");
    chips[key].classList.add(ok ? "ok" : "down");
  };

  const poll = async () => {
    try {
      const h = await api.health();
      setState("db", h.services?.db === "up");
      setState("redis", h.services?.redis === "up");
      setState("graph", h.services?.graph === "up");
      const stats = await api.cacheStats().catch(() => null);
      if (stats) {
        const q = (stats.hits || 0) + (stats.misses || 0);
        chips.cache.querySelector(".chip-label").textContent = `cache ${stats.entries ?? 0}e`;
        setState("cache", true);
      } else {
        setState("cache", false);
      }
    } catch {
      setState("db", false);
      setState("redis", false);
      setState("graph", false);
      setState("cache", false);
    }
  };
  poll();
  setInterval(poll, 10000);
}

boot();
