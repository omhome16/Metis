/* Vault contradiction report: "where do my sources disagree?" */

import { el, clear } from "../util.js";
import { icon } from "../icons.js";
import { api } from "../api.js";
import { toast } from "./toast.js";
import { vaultPath } from "../router.js";

export async function renderContradictions(view, vault) {
  clear(view);
  const wrap = el("div", { class: "docs-wrap" });
  view.append(wrap);

  const head = el("div", { class: "report-head" }, [
    el("div", {}, [
      el("h1", { text: "Where sources disagree" }),
      el("p", {
        class: "section-sub",
        text: "A vault-wide scan: similar passages are compared, and genuine contradictions are flagged with both quotes side by side.",
      }),
    ]),
    el("button", { class: "btn btn-primary", html: `${icon("zap", 15)} Run scan` }),
  ]);
  wrap.append(head);

  const results = el("div");
  wrap.append(results);

  const runBtn = head.querySelector("button");

  async function run() {
    runBtn.disabled = true;
    runBtn.innerHTML = `<span class="spin">${icon("refresh", 15)}</span> Scanning…`;
    clear(results);
    results.append(
      el("div", { class: "panel card-pad muted", text: "Reading the vault, screening similar passages, judging candidate pairs…" })
    );
    try {
      const report = await api.contradictionReport(vault.name);
      paint(report);
    } catch (err) {
      clear(results);
      results.append(el("div", { class: "alert-banner" }, [
        el("span", { class: "icon", html: icon("alert", 15) }),
        el("span", { text: err.message || "The scan could not run." }),
      ]));
    } finally {
      runBtn.disabled = false;
      runBtn.innerHTML = `${icon("zap", 15)} Run scan`;
    }
  }

  runBtn.addEventListener("click", run);

  function paint(report) {
    clear(results);
    const meta = el("p", {
      class: "section-sub",
      style: "margin-bottom:16px",
      text: `${report.checked_chunks} passages screened · ${report.candidate_pairs} candidate pairs judged · ${report.flagged.length} contradiction${report.flagged.length === 1 ? "" : "s"} found`,
    });
    results.append(meta);

    if (!report.checked_chunks) {
      results.append(
        el("div", { class: "empty" }, [
          el("div", { class: "glyph", html: icon("shield", 26) }),
          el("h3", { text: "Nothing to scan yet" }),
          el("p", { text: "Add documents to this vault first — the scan compares passages across your whole library." }),
        ])
      );
      return;
    }

    if (!report.flagged.length) {
      results.append(
        el("div", { class: "empty" }, [
          el("div", { class: "glyph", html: icon("check", 26) }),
          el("h3", { text: "No contradictions found" }),
          el("p", { text: "Every similar passage pair agrees (or covers different subjects). Your sources are consistent — as far as this scan can tell." }),
        ])
      );
      return;
    }

    for (const pair of report.flagged) results.append(pairCard(pair));
  }

  function pairCard(pair) {
    const prob = pair.probability;
    const card = el("div", { class: "pair-card card" });
    const headRow = el("div", { class: "pair-head" });
    headRow.append(
      el("span", { class: "chip chip-danger", html: `${icon("alert", 12)} contradiction` })
    );
    if (prob != null) {
      headRow.append(
        el("span", { class: "mono small muted", text: `p = ${prob.toFixed(2)}` }),
        el("div", { class: "prob-track" }, [
          el("div", { class: "prob-fill", style: `width:${Math.round((prob ?? 1) * 100)}%` }),
        ])
      );
    }
    card.append(headRow);

    for (const side of [pair.a, pair.b]) {
      card.append(
        el("div", { class: "pair-doc" }, [
          el("span", { html: icon("file", 12), style: "display:flex" }),
          el("span", { text: side.doc || "unknown document" }),
        ]),
        el("div", { class: "pair-quote", text: side.text })
      );
      if (side === pair.a) card.append(el("div", { class: "pair-vs", text: "DISAGREES WITH" }));
    }

    if (pair.reason) {
      card.append(el("p", { class: "small muted", style: "margin:10px 0 0", text: pair.reason }));
    }
    return card;
  }

  // auto-run on first open — the report is the point of the tab
  run();
}
