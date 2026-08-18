/* Settings — the self-organizing control panel (P8).

Tune graph behavior at runtime (env defaults are overridden in Postgres):
extraction tier, auto-reorg policy + debounce window. Also surfaces the
reorganization audit log and a "Run reorganization now" button for demos.
*/

import { el, clear } from "../util.js";
import { api } from "../api.js";
import { toast } from "./toast.js";

const EXTRACTION_TIERS = [
  { value: "t1", label: "t1 — local, whole document", hint: "Regex extraction over every parent chunk. Full-book entity coverage, zero LLM cost, co-occurrence edges." },
  { value: "t2", label: "t2 — t1 + LLM windows", hint: "Adds LLM typed relations on sampled start/middle/end windows. Best quality per LLM call." },
  { value: "t3", label: "t3 — LLM per parent", hint: "LLM extraction per parent chunk. Needs an API key; falls back to t1 without one." },
];

const REORG_POLICIES = [
  { value: "debounced", label: "Debounced per batch", hint: "Reorganize when ≥ min docs arrived since the last run, or after 24h." },
  { value: "batch", label: "Every batch", hint: "Always reorganize after an ingest batch (more LLM spend on summaries)." },
  { value: "nightly", label: "Nightly", hint: "Only when > 24h elapsed since the last run. Cheapest." },
];

export async function renderSettings(view) {
  clear(view);
  const wrap = el("div", { class: "settings-view" });
  wrap.append(el("h1", { class: "page-title", text: "Settings" }));
  wrap.append(el("p", { class: "page-sub", text: "How the library organizes itself — extraction tier, auto-reorg policy, and the reorganization log." }));

  let data;
  try {
    data = await api.settings();
  } catch (err) {
    wrap.append(el("p", { class: "empty-note", text: `Could not load settings: ${err.message}` }));
    view.append(wrap);
    return;
  }
  const s = data.settings || {};
  const providers = data.providers || {};
  const hasLLM = providers.groq || providers.gemini || providers.ollama;

  const grid = el("div", { class: "form-grid" });

  // ── extraction tier ────────────────────────────────────────────────────
  const tierSelect = selectField(EXTRACTION_TIERS, s["graph.extraction_mode"], "Extraction tier");
  const tierHint = el("p", { class: "field-hint" });
  const updateTierHint = () => {
    const opt = EXTRACTION_TIERS.find((t) => t.value === tierSelect.value);
    tierHint.textContent = opt ? opt.hint : "";
    if (tierSelect.value === "t3" && !hasLLM) {
      tierHint.textContent = "t3 needs an API key (GROQ_API_KEY / GEMINI_API_KEY or an ollama model) — it falls back to t1 without one.";
    }
  };
  tierSelect.addEventListener("change", updateTierHint);
  updateTierHint();

  const windowsInput = el("input", {
    type: "number", min: 1, max: 8, value: String(s["graph.extract_windows"] ?? 3),
  });
  windowsInput.disabled = s["graph.extraction_mode"] !== "t2";

  const autoReorg = el("input", { type: "checkbox" });
  autoReorg.checked = s["graph.reorg_auto"] !== false;

  const policySelect = selectField(REORG_POLICIES, s["graph.reorg_policy"], "Reorg trigger");
  const minDocsInput = el("input", {
    type: "number", min: 1, max: 100, value: String(s["graph.reorg_min_docs"] ?? 3),
  });
  minDocsInput.disabled = s["graph.reorg_policy"] !== "debounced";

  const syncDisabled = () => {
    windowsInput.disabled = tierSelect.value !== "t2";
    minDocsInput.disabled = policySelect.value !== "debounced";
  };
  tierSelect.addEventListener("change", syncDisabled);
  policySelect.addEventListener("change", syncDisabled);

  grid.append(
    field("Extraction tier", tierSelect, tierHint),
    field("LLM windows (t2)", windowsInput, el("p", { class: "field-hint", text: "How many sampled windows get LLM typed relations." })),
    field("Auto-reorganize", autoReorg, el("p", { class: "field-hint", text: "Run community detection + summary refresh after ingest batches." })),
    field("Reorg trigger", policySelect),
    field("Min docs (debounced)", minDocsInput, el("p", { class: "field-hint", text: "Ingested docs since the last run before a debounced reorg fires." })),
    field("Providers", el("span", { class: "provider-row" }, [
      providerChip("groq", providers.groq),
      providerChip("gemini", providers.gemini),
      providerChip("ollama", providers.ollama),
    ]), el("p", { class: "field-hint", text: "Read-only: which LLM routes are configured. t3 needs at least one." })),
  );

  const saveBtn = el("button", { class: "btn btn-primary", text: "Save settings" });
  saveBtn.addEventListener("click", async () => {
    const payload = {
      "graph.extraction_mode": tierSelect.value,
      "graph.extract_windows": parseInt(windowsInput.value, 10) || 3,
      "graph.reorg_auto": autoReorg.checked,
      "graph.reorg_policy": policySelect.value,
      "graph.reorg_min_docs": parseInt(minDocsInput.value, 10) || 3,
    };
    saveBtn.disabled = true;
    saveBtn.textContent = "Saving…";
    try {
      await api.saveSettings(payload);
      toast("Settings saved — the next ingest batch picks them up.", "ok");
    } catch (err) {
      toast(err.message || "Could not save settings.", "error");
    } finally {
      saveBtn.disabled = false;
      saveBtn.textContent = "Save settings";
    }
  });

  // ── reorganization log ──────────────────────────────────────────────────
  const reorgBox = el("div", { class: "panel" });
  reorgBox.append(el("h2", { class: "panel-title", text: "Reorganizations" }));
  const runBtn = el("button", { class: "btn btn-ghost", text: "Run reorganization now" });
  runBtn.addEventListener("click", async () => {
    runBtn.disabled = true;
    runBtn.textContent = "Detecting communities…";
    try {
      const res = await api.runCommunities();
      toast(`Reorganization done: ${res.communities ?? 0} communities, ${res.summaries ?? 0} summaries.`, "ok");
    } catch (err) {
      toast(err.message || "Reorganization failed.", "error");
    } finally {
      runBtn.disabled = false;
      runBtn.textContent = "Run reorganization now";
      renderRuns(reorgBox, runBtn);
    }
  });

  const renderRuns = async (box, btn) => {
    let runs = [];
    try {
      runs = (await api.reorgRuns()).runs || [];
    } catch { /* log stays empty */ }
    const list = box.querySelector(".reorg-list");
    if (list) list.remove();
    const ul = el("div", { class: "reorg-list" });
    if (!runs.length) {
      ul.append(el("p", { class: "empty-note", text: "No reorganizations yet — ingest a document batch or run one now." }));
    }
    for (const r of runs) {
      const when = r.run_at ? new Date(r.run_at).toLocaleString() : "?";
      const delta = (r.communities_after ?? 0) - (r.communities_before ?? 0);
      const deltaTxt = delta > 0 ? `+${delta}` : String(delta);
      const trigger = r.triggered_by === "manual" ? "manual" : `auto · ${r.docs_since_last ?? 0} docs`;
      ul.append(el("div", { class: "reorg-row" }, [
        el("div", { class: "reorg-when", text: when }),
        el("div", { class: "reorg-what", text: `${r.communities_before ?? 0} → ${r.communities_after ?? 0} communities (${deltaTxt}) · ${r.summaries_made ?? 0} summaries` }),
        el("div", { class: "reorg-trigger", text: trigger }),
      ]));
    }
    box.append(ul);
    if (btn) box.append(btn);
  };

  reorgBox.append(el("p", { class: "field-hint", text: "Every automatic or manual reorganization is recorded here — the same log auto-reorgs write after ingest batches." }));
  await renderRuns(reorgBox, runBtn);

  const actions = el("div", { class: "form-actions" });
  actions.append(saveBtn);

  wrap.append(grid, actions, reorgBox);
  view.append(wrap);
}

/* ── tiny form helpers ──────────────────────────────────────────────────── */

function field(labelText, control, hint) {
  const box = el("div", { class: "form-field" });
  box.append(el("label", { text: labelText }), control);
  if (hint) box.append(hint);
  return box;
}

function selectField(options, value, _label) {
  const sel = el("select");
  for (const opt of options) {
    const o = el("option", { value: opt.value, text: opt.label });
    if (opt.value === value) o.selected = true;
    sel.append(o);
  }
  return sel;
}

function providerChip(name, on) {
  return el("span", { class: `provider-chip${on ? " on" : ""}`, text: `${name}${on ? " ✓" : " —"}` });
}
