/* Hash router: #/ (home) and #/v/<vault>/<tab> */

export function parseHash() {
  const raw = location.hash.replace(/^#\/?/, "");
  const parts = raw.split("/").filter((p) => p !== "");
  if (parts.length === 0 || parts[0] === "overview") return { name: "home" };
  if (parts[0] === "v") {
    return {
      name: "vault",
      vault: decodeURIComponent(parts[1] || ""),
      tab: ["documents", "graph", "ask"].includes(parts[2]) ? parts[2] : "documents",
    };
  }
  return { name: "home" };
}

export function vaultPath(name, tab = "documents") {
  return `#/v/${encodeURIComponent(name)}/${tab}`;
}

export function homePath() {
  return "#/";
}
