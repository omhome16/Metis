/* Hash router: #/ (home), #/login, #/v/<vault>/<tab>, #/library/<tab>, #/settings */

export const VAULT_TABS = ["documents", "graph", "ask", "contradictions"];

export function parseHash() {
  const raw = location.hash.replace(/^#\/?/, "");
  const parts = raw.split("/").filter((p) => p !== "");
  if (parts[0] === "login") return { name: "login" };
  if (parts.length === 0 || parts[0] === "overview") return { name: "home" };
  if (parts[0] === "v") {
    return {
      name: "vault",
      vault: decodeURIComponent(parts[1] || ""),
      tab: VAULT_TABS.includes(parts[2]) ? parts[2] : "documents",
    };
  }
  if (parts[0] === "library") {
    return {
      name: "library",
      tab: ["graph", "surprises"].includes(parts[1]) ? parts[1] : "graph",
    };
  }
  if (parts[0] === "settings") return { name: "settings" };
  return { name: "home" };
}

export function vaultPath(name, tab = "documents") {
  return `#/v/${encodeURIComponent(name)}/${tab}`;
}

export function libraryPath(tab = "graph") {
  return `#/library/${tab}`;
}

export function homePath() {
  return "#/";
}
