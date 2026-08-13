import { el, clear, mdToHtml, esc, fmtDate } from "../util.js";
import { icon } from "../icons.js";
import { api, askStream } from "../api.js";
import { renderVaultShell } from "./shell.js";
import { toast } from "./toast.js";

const TOOL_LABEL = {
  search_vault: "searching vault",
  graph_lookup: "expanding graph",
  wikipedia: "wikipedia",
};

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
  toolbar.querySelector("button").addEventListener("click", startNew);
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

  const convPanel = el("div", { class: "ask-panel" });
  const convHead = el("div", { class: "conv-toolbar" }, [
    el("div", { class: "ask-panel-label", text: "Conversations", style: "margin:0" }),
    el("button", { class: "btn btn-ghost", html: icon("plus", 12), style: "padding:4px 9px;font-size:11px", title: "New conversation" }),
  ]);
  const convListEl = el("div", { class: "conv-list" });
  convPanel.append(convHead, convListEl);
  convHead.querySelector("button").addEventListener("click", startNew);

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
      el("p", { class: "ask-panel-empty", text: "Metis reasons with tools — searching the vault, expanding the knowledge graph, and checking background references before answering." }),
      el("p", { class: "ask-panel-empty", text: "Each conversation is stored server-side, so history survives reloads and follow-up questions keep full context." }),
      el("p", { class: "ask-panel-empty", text: "Citations are verified against the retrieved sources; a contradiction scan flags when sources disagree." }),
    ])
  );
  rail.append(convPanel, about, how);
  layout.append(chat, rail);

  // ── state ────────────────────────────────────────────────
  let history = []; // [{role, text, sources, citations, contradiction, cached, usage, error, thinking}]
  let activeConvId = null;
  let convList = [];
  let currentAbort = null;
  let live = null; // {textEl, thinkingEl, logEl, sourcesCard, metaRow, bannerEl, assistant, streaming}
  let streaming = false;
  let firstTokenSeen = false;

  loadConversations();
  renderThread();

  // ── conversations ─────────────────────────────────────────
  async function loadConversations() {
    try {
      convList = await api.conversations(vault.name);
    } catch {
      convList = [];
    }
    renderConvList();
  }

  function renderConvList() {
    clear(convListEl);
    if (!convList.length) {
      convListEl.append(el("p", { class: "conv-empty", text: "No conversations yet. Ask something to begin." }));
      return;
    }
    for (const c of convList) {
      const item = el("button", { class: `conv-item${c.id === activeConvId ? " active" : ""}`, title: c.title });
      const del = el("span", { class: "conv-del", html: icon("x", 11), title: "Delete conversation" });
      item.append(
        el("span", { class: "conv-title", text: c.title }),
        el("span", { class: "conv-meta" }, [
          el("span", { text: `${c.message_count} msgs` }),
          el("span", { text: fmtDate(c.updated_at) }),
        ]),
        del
      );
      del.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        await api.deleteConversation(c.id).catch(() => null);
        if (activeConvId === c.id) startNew();
        else loadConversations();
      });
      item.addEventListener("click", () => {
        if (c.id !== activeConvId) loadConversation(c.id);
      });
      convListEl.append(item);
    }
  }

  async function loadConversation(id) {
    try {
      const detail = await api.conversation(id);
      activeConvId = detail.id;
      history = (detail.messages || []).map((m) => ({
        role: m.role,
        text: m.content,
        sources: m.sources,
        citations: m.citations,
        usage: m.usage,
        cached: m.cached,
        error: m.error,
        contradiction: null,
      }));
    } catch {
      activeConvId = null;
      history = [];
      toast("Could not load that conversation.", "error");
    }
    renderThread();
    scrollBottom();
    renderConvList();
  }

  function startNew() {
    activeConvId = null;
    history = [];
    renderThread();
    renderConvList();
  }

  // ── thread rendering ──────────────────────────────────────
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
    textEl.hidden = true;
    bodyEl.append(textEl);
    msg.append(el("div", { class: "msg-avatar", text: "M" }), bodyEl);
    thread.append(msg);

    const slot = { textEl, thinkingEl: null, logEl: null, sourcesCard: null, metaRow: null, bannerEl: null, assistant: m, streaming };
    if (streaming) live = slot;
    if (!m.text && streaming) showThinking(slot);
    updateText(slot, m, streaming);

    if (m.sources && (m.sources.chunks?.length || m.sources.images?.length || m.sources.communities?.length)) {
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

  function showThinking(slot) {
    if (slot.thinkingEl) return;
    const wrap = el("div", { class: "thinking" });
    wrap.append(
      el("span", { class: "thinking-dots" }, [el("i"), el("i"), el("i")]),
      el("span", { class: "thinking-step", text: "thinking" })
    );
    const logEl = el("div", { class: "thinking-log" });
    slot.thinkingEl = wrap;
    slot.logEl = logEl;
    slot.textEl.before(wrap);
    wrap.after(logEl);
    scrollBottom();
  }

  function addThinking(slot, data) {
    if (!slot || !slot.logEl) return;
    const tool = TOOL_LABEL[data.tool] || data.tool || "gathering evidence";
    const args = data.args ? prettyArgs(data.args) : "";
    const item = el("div", { class: "thinking-log-item" }, [
      el("span", { class: "tl-tool", text: tool }),
      el("span", { class: "tl-args", text: args }),
      data.result ? el("span", { class: "tl-result", text: data.result }) : null,
    ]);
    slot.logEl.append(item);
    if (slot.thinkingEl) slot.thinkingEl.querySelector(".thinking-step").textContent = tool;
    scrollBottom();
  }

  function prettyArgs(args) {
    try {
      const v = typeof args === "string" ? JSON.parse(args) : args;
      return Object.entries(v || {}).map(([k, val]) => `${k}: ${String(val).slice(0, 60)}`).join(" · ");
    } catch {
      return String(args || "").slice(0, 80);
    }
  }

  function updateText(slot, m, streaming) {
    if (streaming) {
      if (!m.text) {
        if (!slot.thinkingEl) showThinking(slot);
        return;
      }
      // first real tokens → swap thinking indicator for the streaming answer
      if (slot.thinkingEl) {
        slot.thinkingEl.remove();
        slot.thinkingEl = null;
        if (slot.logEl) { slot.logEl.classList.add("hidden"); }
        firstTokenSeen = true;
      }
      slot.textEl.hidden = false;
    } else {
      if (slot.thinkingEl) { slot.thinkingEl.remove(); slot.thinkingEl = null; }
      if (slot.logEl) slot.logEl.classList.add("hidden");
      slot.textEl.hidden = !m.text;
    }
    const cite = (n) => `<span class="cite-chip" data-n="${n}" title="Jump to source ${n}">${n}</span>`;
    slot.textEl.innerHTML = mdToHtml(m.text, cite) + (streaming && m.text ? '<span class="caret"></span>' : "");
  }

  function buildMetaRow(m) {
    const meta = el("div", { class: "msg-meta" });
    if (m.sources?.communities?.length) meta.append(el("span", { class: "tag cached", html: `${icon("network", 10)}<span>global</span>` }));
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
    const communities = m.sources.communities || [];
    const total = chunks.length + images.length + communities.length;
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
    communities.forEach((c, i) => {
      const item = el("div", { class: "source-item", "data-n": i + 1 });
      item.append(
        el("div", { class: "source-item-top" }, [
          el("span", { class: "source-doc", text: "Community" }),
          el("span", { class: "source-kind", text: `${c.entity_count} entities` }),
        ]),
        el("div", { class: "source-text", text: c.summary }),
        el("div", { class: "source-tags" }, (c.members || []).slice(0, 6).map((m) => el("span", { class: "tag", text: m })))
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

  // ── submit / stream ───────────────────────────────────────
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
    const assistantMsg = { role: "assistant", text: "", sources: null, citations: null, contradiction: null, cached: false, usage: null, error: null, thinking: [] };
    history.push(userMsg, assistantMsg);
    streaming = true;
    firstTokenSeen = false;
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
        { question: userMsg.text, corpus: vault.name, stream: true, image, conversation_id: activeConvId || undefined },
        (event, data) => {
          if (event === "thinking") {
            assistantMsg.thinking.push(data);
            addThinking(live, data);
          } else if (event === "sources") {
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
            if (data.conversation_id && !activeConvId) {
              activeConvId = data.conversation_id;
              loadConversations();
            }
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
      scrollBottom();
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
