import { el, clear, mdToHtml, esc } from "../util.js";
import { icon } from "../icons.js";
import { api, askStream } from "../api.js";
import { renderVaultShell } from "./shell.js";
import { toast } from "./toast.js";

const HISTORY_KEY = (name) => `metis-chat-${name}`;

export async function renderAsk(container, vault) {
  const body = renderVaultShell(container, vault, "ask");
  const layout = el("div", { class: "ask-layout" });
  body.append(layout);

  // ── chat column ──────────────────────────────────────────
  const chat = el("div", { class: "chat" });
  const toolbar = el("div", { class: "toolbar" }, [
    el("span", { class: "section-label", text: `Conversation`, style: "margin:0" }),
    el("button", { class: "btn btn-ghost", html: `${icon("refresh", 14)}<span>New conversation</span>`, style: "padding:6px 11px;font-size:12px" }),
  ]);
  toolbar.querySelector("button").addEventListener("click", () => {
    localStorage.removeItem(HISTORY_KEY(vault.name));
    history = [];
    renderThread();
    renderSuggestions();
  });
  chat.append(toolbar);

  const thread = el("div", { class: "thread" });
  chat.append(thread);

  // composer
  const composer = el("div", { class: "composer" });
  const ta = el("textarea", { placeholder: `Ask about ${vault.name}…`, rows: 1, "aria-label": "Ask a question" });
  const attachRow = el("div");
  const foot = el("div", { class: "composer-foot" });
  const hints = el("span", { class: "composer-hints", text: "enter to send · shift+enter for a new line" });
  const actions = el("div", { style: "display:flex;gap:6px" });
  const attachBtn = el("button", { class: "icon-btn", title: "Attach an image", html: icon("image", 16) });
  const sendBtn = el("button", { class: "btn btn-primary", html: icon("send", 15), style: "padding:8px 12px" });
  actions.append(attachBtn, sendBtn);
  foot.append(hints, actions);
  composer.append(ta, attachRow, foot);
  chat.append(composer);

  // image attach
  const fileInput = el("input", { type: "file", accept: "image/*", class: "sr-only" });
  document.body.append(fileInput);
  let attachedImage = null; // data URL
  attachBtn.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", async () => {
    const f = fileInput.files[0];
    fileInput.value = "";
    if (!f) return;
    try {
      attachedImage = await fileToDataUrl(f);
      renderAttach();
    } catch {
      toast("Could not read that image.", "error");
    }
  });

  function renderAttach() {
    clear(attachRow);
    if (!attachedImage) return;
    const thumb = el("span", { class: "attach-thumb" });
    thumb.append(
      el("img", { src: attachedImage, alt: "Attached image" }),
      el("span", { text: "attached" }),
      el("button", { class: "icon-btn", html: icon("x", 12), style: "width:18px;height:18px", title: "Remove image" })
    );
    thumb.querySelector("button").addEventListener("click", () => { attachedImage = null; renderAttach(); });
    attachRow.append(thumb);
  }

  // autogrow
  ta.addEventListener("input", () => {
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 200)}px`;
  });
  ta.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) {
      ev.preventDefault();
      submit();
    }
  });
  sendBtn.addEventListener("click", submit);

  // ── right rail ───────────────────────────────────────────
  const rail = el("div", { class: "ask-rail" });
  const about = el("div", { class: "ask-panel" });
  about.append(
    el("div", { class: "ask-panel-label", text: "Vault context" }),
    el("p", { class: "ask-panel-empty", text: vault.description || "No description." }),
    el("p", { class: "ask-panel-empty", style: "margin-top:8px", text: `${vault.doc_count} documents · ${vault.chunk_count} chunks · ${vault.entity_count} entities` })
  );
  const how = el("div", { class: "ask-panel" });
  how.append(
    el("div", { class: "ask-panel-label", text: "How answers are grounded" }),
    el("div", { class: "ask-panel-list" }, [
      el("p", { class: "ask-panel-empty", text: "Your question is rewritten, then matched against the vault's chunks with hybrid vector + keyword retrieval and a local reranker." }),
      el("p", { class: "ask-panel-empty", text: "The knowledge graph expands retrieval to related entities, so answers can draw on connections, not just keyword hits." }),
      el("p", { class: "ask-panel-empty", text: "Citations are verified against the retrieved sources; a contradiction scan flags when sources disagree." }),
    ])
  );
  rail.append(about, how);
  layout.append(chat, rail);

  // ── state ────────────────────────────────────────────────
  let history = loadHistory(vault.name);
  let currentAbort = null;
  let live = null; // {textEl, sourcesCard, metaRow, bannerEl, assistant}
  let streaming = false;

  renderThread();

  function renderThread() {
    clear(thread);
    live = null;
    history.forEach((m, i) => {
      const isStreaming = streaming && m.role === "assistant" && i === history.length - 1;
      if (m.role === "user") {
        const msg = el("div", { class: "msg user" });
        msg.append(
          el("div", { class: "msg-avatar", text: "Y" }),
          el("div", { class: "msg-body" }, [el("div", { text: m.text })])
        );
        thread.append(msg);
      } else {
        appendAssistant(m, isStreaming);
      }
    });
    if (!history.length) renderSuggestions();
    scrollBottom();
  }

  function appendAssistant(m, streaming) {
    const msg = el("div", { class: "msg assistant" });
    const bodyEl = el("div", { class: "msg-body" });
    const textEl = el("div", { class: "msg-assistant-text" });
    bodyEl.append(textEl);
    msg.append(el("div", { class: "msg-avatar", text: "M" }), bodyEl);
    thread.append(msg);

    const slot = { textEl, sourcesCard: null, metaRow: null, bannerEl: null, assistant: m };
    if (streaming) live = slot;
    updateText(slot, m, streaming);

    if (m.sources && m.sources.chunks?.length || m.sources?.images?.length) {
      slot.sourcesCard = buildSourcesCard(m);
      bodyEl.append(slot.sourcesCard);
    }
    if (m.contradiction) {
      slot.bannerEl = el("div", { class: "contradiction", html: `${icon("alert", 16)}<span>${esc(m.contradiction.reason || "The retrieved sources disagree on this point.")}</span>` });
      bodyEl.append(slot.bannerEl);
    }
    if (!streaming) {
      slot.metaRow = buildMetaRow(m);
      bodyEl.append(slot.metaRow);
    }
    return msg;
  }

  function updateText(slot, m, streaming) {
    const cite = (n) => `<span class="cite-chip" data-n="${n}" title="Jump to source ${n}">${n}</span>`;
    slot.textEl.innerHTML = mdToHtml(m.text, cite) + (streaming ? '<span class="caret"></span>' : "");
  }

  function buildMetaRow(m) {
    const meta = el("div", { class: "msg-meta" });
    if (m.cached) meta.append(el("span", { class: "tag cached", html: `${icon("check", 10)}<span>served from cache</span>` }));
    if (m.usage) {
      const { in: tin = 0, out = 0 } = m.usage;
      meta.append(el("span", { class: "tag usage", text: `${tin} in · ${out} out` }));
      if (m.usage.cost_usd) meta.append(el("span", { class: "tag usage", text: `$${m.usage.cost_usd.toFixed(5)}` }));
    }
    if (m.error) meta.append(el("span", { class: "tag usage", style: "color:var(--err)", text: m.error }));
    return meta;
  }

  function buildSourcesCard(m) {
    const chunks = m.sources.chunks || [];
    const images = m.sources.images || [];
    const total = chunks.length + images.length;
    const card = el("div", { class: "sources" });
    const head = el("button", { class: "sources-head", html: `<span>Sources · ${total}</span><span class="chev">${icon("chev-r", 13)}</span>` });
    const bodyEl = el("div", { class: "sources-body" });
    card.append(head, bodyEl);
    head.addEventListener("click", () => card.classList.toggle("open"));

    chunks.forEach((c, i) => {
      const item = el("div", { class: "source-item", "data-n": i + 1 });
      item.append(
        el("div", { class: "source-item-top" }, [
          el("span", { class: "source-doc", text: c.doc }),
          el("span", { class: "source-score", text: c.score != null ? c.score.toFixed(3) : "" }),
        ]),
        el("div", { class: "source-bar" }, [el("div", { class: "fill", style: `width:${Math.min(100, Math.max(8, (c.score || 0) * 100))}%` })]),
        el("div", { class: "source-text", text: c.text })
      );
      item.addEventListener("click", () => card.classList.add("open"));
      bodyEl.append(item);
    });
    images.forEach((img) => {
      const item = el("div", { class: "source-item" });
      item.append(
        el("div", { class: "source-item-top" }, [
          el("span", { class: "source-doc", text: img.doc }),
          el("span", { class: "source-kind", text: "image" }),
        ]),
        el("div", { class: "source-text", text: img.caption })
      );
      bodyEl.append(item);
    });
    if (!total) card.classList.add("hidden");
    return card;
  }

  function renderSuggestions() {
    const existing = thread.querySelector(".suggest-grid");
    if (existing) existing.remove();
    const grid = el("div", { class: "suggest-grid" });
    api.suggestions(vault.name)
      .then((s) => {
        for (const q of s.questions || []) {
          const b = el("button", { class: "suggest-item", text: q });
          b.addEventListener("click", () => submit(q));
          grid.append(b);
        }
        thread.append(grid);
      })
      .catch(() => { /* no suggestions */ });
  }

  function submit(textOverride) {
    if (currentAbort) return;
    const text = (textOverride ?? ta.value).trim();
    if (!text) return;
    const image = attachedImage || undefined;
    attachedImage = null;
    renderAttach();
    ta.value = "";
    ta.style.height = "auto";

    const userMsg = { role: "user", text };
    const assistantMsg = { role: "assistant", text: "", sources: null, citations: null, contradiction: null, cached: false, usage: null, error: null };
    history.push(userMsg, assistantMsg);
    persist();
    streaming = true;
    renderThread();
    scrollBottom();
    send(userMsg, assistantMsg, image);
  }

  async function send(userMsg, assistantMsg, image) {
    const ac = new AbortController();
    currentAbort = ac;
    sendBtn.innerHTML = icon("stop", 15);
    sendBtn.classList.add("btn-danger");
    sendBtn.classList.remove("btn-primary");
    let buffer = "";
    try {
      await askStream(
        { question: userMsg.text, corpus: vault.name, stream: true, image },
        (event, data) => {
          if (event === "sources") {
            assistantMsg.sources = data;
            if (live) {
              if (live.sourcesCard) live.sourcesCard.remove();
              live.sourcesCard = buildSourcesCard(assistantMsg);
              live.textEl.closest(".msg-body").append(live.sourcesCard);
            }
          } else if (event === "tokens") {
            buffer += data.text;
            assistantMsg.text = buffer;
            if (live) updateText(live, assistantMsg, true);
          } else if (event === "citations") {
            assistantMsg.citations = data.citations || [];
          } else if (event === "contradiction") {
            assistantMsg.contradiction = data;
            if (live && !live.bannerEl) {
              live.bannerEl = el("div", { class: "contradiction", html: `${icon("alert", 16)}<span>${esc(data.reason || "The retrieved sources disagree on this point.")}</span>` });
              live.textEl.closest(".msg-body").append(live.bannerEl);
            }
          } else if (event === "done") {
            assistantMsg.usage = data;
            assistantMsg.cached = !!data.cached;
          }
        },
        ac.signal
      );
    } catch (err) {
      if (err.name !== "AbortError") {
        assistantMsg.error = err.message || "Request failed";
      }
    } finally {
      currentAbort = null;
      sendBtn.innerHTML = icon("send", 15);
      sendBtn.classList.remove("btn-danger");
      sendBtn.classList.add("btn-primary");
      streaming = false;
      if (live) {
        const slot = live;
        live = null;
        updateText(slot, assistantMsg, false);
        slot.metaRow = buildMetaRow(assistantMsg);
        slot.textEl.closest(".msg-body").append(slot.metaRow);
      }
      persist();
      scrollBottom();
    }
  }

  function persist() {
    const slim = history.slice(-40).map(({ role, text, sources, contradiction, cached, usage, error }) => ({ role, text, sources, contradiction, cached, usage, error }));
    localStorage.setItem(HISTORY_KEY(vault.name), JSON.stringify(slim));
  }

  function loadHistory(name) {
    try {
      const raw = localStorage.getItem(HISTORY_KEY(name));
      if (!raw) return [];
      const arr = JSON.parse(raw);
      return Array.isArray(arr) ? arr.filter((m) => m && typeof m.text === "string") : [];
    } catch {
      return [];
    }
  }

  // citation chips → scroll to the source
  thread.addEventListener("click", (ev) => {
    const chip = ev.target.closest(".cite-chip");
    if (!chip) return;
    const card = chip.closest(".msg").querySelector(".sources");
    if (card) {
      card.classList.add("open");
      const item = card.querySelector(`.source-item[data-n="${chip.dataset.n}"]`);
      if (item) {
        item.style.outline = "2px solid var(--accent)";
        item.scrollIntoView({ block: "nearest" });
        setTimeout(() => { item.style.outline = ""; }, 1600);
      }
    }
  });

  function scrollBottom() {
    const view = document.getElementById("view");
    requestAnimationFrame(() => { view.scrollTop = view.scrollHeight; });
  }

  // open documents from graph view
  window.addEventListener("metis:open-doc", () => { /* handled by router if needed */ });
}

/* ── helpers ──────────────────────────────────────────────── */

function fileToDataUrl(file, maxSide = 1024) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      const scale = Math.min(1, maxSide / Math.max(img.width, img.height));
      const c = document.createElement("canvas");
      c.width = Math.max(1, Math.round(img.width * scale));
      c.height = Math.max(1, Math.round(img.height * scale));
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(url);
      resolve(c.toDataURL("image/jpeg", 0.85));
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("bad image")); };
    img.src = url;
  });
}
