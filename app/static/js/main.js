import { initTheme, toggleTheme, currentTheme } from "./theme.js";
import { icon } from "./icons.js";
import { api, jwt, apiToken } from "./api.js";
import { state } from "./store.js";
import { parseHash, vaultPath } from "./router.js";
import { renderSidebar } from "./views/sidebar.js";
import { renderAuth } from "./views/auth.js";
import { renderHome } from "./views/home.js";
import { renderDocuments } from "./views/documents.js";
import { renderGraph } from "./views/graphview.js";
import { renderAsk } from "./views/ask.js";
import { renderContradictions } from "./views/contradictions.js";
import { renderLibrary } from "./views/library.js";
import { renderSettings } from "./views/settings.js";

let authKnown = false; // have we resolved the auth probe?

async function boot() {
  initTheme();

  document.getElementById("brandMark").innerHTML = icon("mark", 22);
  const themeBtn = document.getElementById("themeToggle");
  const paintThemeIcon = () => {
    themeBtn.innerHTML = icon(currentTheme() === "dark" ? "sun" : "moon", 17);
  };
  paintThemeIcon();
  themeBtn.addEventListener("click", toggleTheme);
  document.addEventListener("metis:theme", paintThemeIcon);

  document.getElementById("newVaultBtn").innerHTML = icon("plus", 15);
  document.getElementById("newVaultBtn").addEventListener("click", async () => {
    const { openCreateVault } = await import("./views/sidebar.js");
    openCreateVault(() => { location.hash = "#/"; });
  });

  // Resolve the session before painting anything user-owned.
  if (jwt() || apiToken()) {
    try {
      state.user = await api.me();
    } catch {
      state.user = null;
    }
  }

  window.addEventListener("metis:auth-required", () => {
    authKnown = true;
    renderAuth(document.getElementById("view"));
  });

  window.addEventListener("hashchange", render);
  await render();

  if (!state.needsAuth) {
    await refreshVaults();
    initStatusbar();
  }
  authKnown = true;
}

async function refreshVaults() {
  try {
    state.vaults = await api.vaults();
  } catch {
    state.vaults = [];
  }
  renderSidebar();
}

async function render() {
  const route = parseHash();
  const view = document.getElementById("view");
  paintTopbar(route);

  // users mode with no session: everything becomes the auth screen.
  if (!authKnown || state.needsAuth || route.name === "login") {
    try {
      state.user = await api.me();
      state.needsAuth = false;
      document.body.classList.remove("authing");
    } catch {
      renderAuth(view);
      return;
    }
  } else {
    document.body.classList.remove("authing");
  }

  if (route.name === "login") {
    location.hash = "#/";
    return;
  }
  if (route.name === "home") {
    await renderHome(view);
    return;
  }
  if (route.name === "library") {
    await renderLibrary(view, route.tab);
    return;
  }
  if (route.name === "settings") {
    await renderSettings(view);
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
  else if (route.tab === "contradictions") await renderContradictions(view, vault);
  else await renderDocuments(view, vault);
}

function paintTopbar(route) {
  const ctx = document.getElementById("topbarContext");
  ctx.innerHTML = "";
  if (document.body.classList.contains("authing")) return;

  if (route.name === "home") {
    ctx.append(crumb("Overview", true));
  } else if (route.name === "settings") {
    ctx.append(
      crumb("Overview", false, () => { location.hash = "#/"; }),
      elSep(),
      crumb("Settings", true)
    );
  } else if (route.name === "library") {
    ctx.append(
      crumb("Library", false, () => { location.hash = "#/"; }),
      elSep(),
      crumb(route.tab === "surprises" ? "Surprises" : "Graph", true)
    );
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
  chips.model.querySelector(".chip-label").textContent = "model · bge-m3 / reranker";

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
