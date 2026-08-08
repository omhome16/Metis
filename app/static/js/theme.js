/* Theme management: 'light' | 'dark', persisted, system-aware. */

const KEY = "metis-theme";

export function initTheme() {
  const saved = localStorage.getItem(KEY);
  const system = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  applyTheme(saved || system);
}

export function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem(KEY, theme);
  document.dispatchEvent(new CustomEvent("metis:theme", { detail: theme }));
}

export function toggleTheme() {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  applyTheme(next);
}

export function currentTheme() {
  return document.documentElement.dataset.theme || "light";
}
