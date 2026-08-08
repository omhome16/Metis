import { el } from "../util.js";
import { icon } from "../icons.js";

export function toast(message, kind = "info", ms = 3400) {
  const root = document.getElementById("toast-root");
  const node = el("div", { class: `toast ${kind}`, html: icon(kind === "ok" ? "check" : kind === "error" ? "alert" : "info") });
  node.append(el("span", { text: message }));
  root.append(node);
  setTimeout(() => {
    node.classList.add("leaving");
    setTimeout(() => node.remove(), 200);
  }, ms);
}
