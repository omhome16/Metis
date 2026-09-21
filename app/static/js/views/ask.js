/* Vault chat: grounded, streaming, citation-backed conversation.

Extras over plain chat:
- document selection ("answer only from these docs") via /ask document_ids
- save any answer as a note (it becomes a citable source)
- contradiction alert banner when the scan finds disagreeing sources
- agent thinking log (deep lane) */

import { el, clear, fmtTime, mdToHtml } from "../util.js";
import { icon } from "../icons.js";
import { api, askStream } from "../api.js";
import { state } from "../store.js";
import { toast } from "./toast.js";
import { openModal, closeModal } from "./modal.js";

export async function renderAsk(view, vault) {
  clear(view);
  const wrap = el("div", { class: "ask-wrap" });
  view.append(wrap);

  let conversations = await api.conversations(vault.name).catch(() => []);
  let activeConv = conversations[0]?.id || null;
  let selection = []; // document ids ("chat with a selection")
  let suggestions = await api.suggestions(vault.name).catch(() => ({ questions: [] }));
  let aborter = null;

  /* ── head: new chat + selection picker ─────────────────────────────── */
  const head = el("div", { class: "ask-head" });
  const newBtn = el("button", { class: "btn btn-secondary btn-sm", text: "New conversation" });
  const pickBtn = el("button", { class: "btn btn-ghost btn-sm", html: `${icon("filter", 14)} Answer from…` });
  const laneInfo = el("span", { class: "chip", text: `vault: ${vault.name}` });
  head.append(newBtn, pickBtn, el("span", { class: "spacer" }), laneInfo);

  const convTabs = el("div", { class: "conv-tabs" });
  const scroll = el("div", { class: "ask-scroll" });
  const thread = el("div", { class: "ask-thread" });
  scroll.append(thread);

  const selectionBar = el("div", { class: "selection-bar hidden" });
  const composer = el("div", { class: "ask-composer" });
  const box = el("div", { class: "composer-box" });
  const ta = el("textarea", { rows: 1, placeholder: `Ask anything in ${vault.name}…` });
  const send = el("button", { class: "send-btn", title: "Send", "aria-label": "Send", html: icon("send", 16) });
  box.append(ta, send);
  composer.append(box);
  wrap.append(head, convTabs, scroll, selectionBar, composer);

  newBtn.addEventListener("click", () => {
    activeConv = null;
    paintThread([]);
    paintConvTabs();
    paintSuggestions();
    ta.focus();
  });

  pickBtn.addEventListener("click", openPicker);

  ta.addEventListener("input", () => {
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 160) + "px";
  });
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  });
  send.addEventListener("click", submit);

  /* ── document selection ────────────────────────────────────────────── */
  async function openPicker() {
    const docs = await api.documents(vault.name).catch(() => []);
    const picked = new Set(selection);
    const list = el("div", { class: "picker-list" });
    for (const d of docs) {
      const row = el("div", { class: `picker-row${picked.has(d.id) ? " picked" : ""}` });
      row.append(
        el("span", { class: "checkbox", html: picked.has(d.id) ? icon("check", 11) : "" }),
        el("span", { class: "grow", text: d.title, style: "flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }),
        el("span", { class: "muted small", text: `${d.chunk_count} chunks` })
      );
      row.addEventListener("click", () => {
        if (picked.has(d.id)) picked.delete(d.id);
        else picked.add(d.id);
        row.classList.toggle("picked", picked.has(d.id));
        row.querySelector(".checkbox").innerHTML = picked.has(d.id) ? icon("check", 11) : "";
      });
      list.append(row);
    }
    const apply = el("button", { class: "btn btn-primary btn-sm", text: "Apply selection" });
    const clearSel = el("button", { class: "btn btn-ghost btn-sm", text: "Clear" });
    apply.addEventListener("click", () => {
      selection = [...picked];
      paintSelectionBar();
      closeModal();
    });
    clearSel.addEventListener("click", () => {
      selection = [];
      paintSelectionBar();
      closeModal();
    });
    openModal({
      title: "Answer from specific documents",
      sub: "Restrict retrieval to the documents you pick — a scoped answer.",
      body: (b) => {
        if (!docs.length) b.append(el("p", { class: "muted", text: "No documents in this vault yet." }));
        b.append(list);
      },
      footer: [clearSel, apply],
    });
  }

  function paintSelectionBar() {
    clear(selectionBar);
    if (!selection.length) {
      selectionBar.classList.add("hidden");
      return;
    }
    selectionBar.classList.remove("hidden");
    selectionBar.append(
      el("span", { html: icon("filter", 13), style: "display:flex" }),
      el("span", { text: `Answering from ${selection.length} document${selection.length > 1 ? "s" : ""} only` })
    );
    const clearBtn = el("button", { class: "btn btn-ghost btn-sm", text: "Clear" });
    clearBtn.addEventListener("click", () => {
      selection = [];
      paintSelectionBar();
    });
    selectionBar.append(el("span", { class: "spacer", style: "flex:1" }), clearBtn);
  }

  /* ── conversations ─────────────────────────────────────────────────── */
  function paintConvTabs() {
    clear(convTabs);
    for (const c of conversations.slice(0, 12)) {
      const tab = el("button", { class: `conv-tab${c.id === activeConv ? " active" : ""}`, title: c.title });
      tab.append(el("span", { text: c.title }));
      const del = el("span", {
        html: icon("x", 10),
        style: "margin-left:7px;color:var(--ink-3);display:inline-flex",
        title: "Delete conversation",
      });
      del.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        if (!confirm("Delete this conversation?")) return;
        await api.deleteConversation(c.id).catch(() => {});
        conversations = conversations.filter((x) => x.id !== c.id);
        if (activeConv === c.id) {
          activeConv = conversations[0]?.id || null;
          await loadThread();
        }
        paintConvTabs();
      });
      tab.append(del);
      tab.addEventListener("click", async () => {
        activeConv = c.id;
        paintConvTabs();
        await loadThread();
      });
      convTabs.append(tab);
    }
  }

  async function loadThread() {
    if (!activeConv) {
      paintThread([]);
      return;
    }
    const detail = await api.conversation(activeConv).catch(() => null);
    paintThread(detail?.messages || []);
  }

  /* ── thread painting ───────────────────────────────────────────────── */
  function paintThread(messages) {
    clear(thread);
    if (!messages.length) {
      paintSuggestions();
      return;
    }
    for (const m of messages) thread.append(messageNode(m));
    scroll.scrollTop = scroll.scrollHeight;
  }

  function paintSuggestions() {
    const qs = suggestions.questions || [];
    if (!qs.length) return;
    const box = el("div", { class: "empty", style: "padding:40px 0" });
    box.append(
      el("div", { class: "glyph", html: icon("sparkle", 24) }),
      el("h3", { text: `Ask ${vault.name}` }),
      el("p", { text: "Start with one of these, or write your own question below." })
    );
    const grid = el("div", { style: "display:flex;flex-direction:column;gap:8px;max-width:520px;margin:0 auto" });
    for (const q of qs) {
      const b = el("button", { class: "btn btn-secondary", style: "justify-content:flex-start", text: q });
      b.addEventListener("click", () => {
        ta.value = q;
        submit();
      });
      grid.append(b);
    }
    box.append(grid);
    thread.append(box);
  }

  function messageNode(m) {
    const node = el("div", { class: `msg msg-${m.role}` });
    if (m.role === "user") {
      node.append(el("div", { class: "bubble", text: m.content }));
      return node;
    }
    const bubble = el("div", { class: "bubble" });
    const body = el("div", { class: "msg-body" });
    body.innerHTML = mdToHtml(m.content || "", citeFactory(m));
    bubble.append(body);

    if (m.error) bubble.append(el("p", { class: "form-error", text: m.error }));
    if (m.sources?.chunks?.length) bubble.append(sourcesNode(m.sources));
    if (m.citations?.mode === "global") bubble.append(globalSources(m.citations));

    const meta = el("div", { class: "msg-actions" });
    const when = el("span", { class: "lane-chip", text: fmtTime(m.created_at) + (m.cached ? " · cached" : "") });
    meta.append(when);
    if (m.id) {
      const noteBtn = el("button", { class: "icon-btn", title: "Save as note", html: icon("note", 14) });
      noteBtn.addEventListener("click", () => saveAsNote(m));
      const up = el("button", { class: "icon-btn", title: "Helpful", html: icon("thumbup", 14) });
      const down = el("button", { class: "icon-btn", title: "Not helpful", html: icon("thumbdown", 14) });
      up.addEventListener("click", () => sendFeedback(m.id, 1));
      down.addEventListener("click", () => sendFeedback(m.id, -1));
      meta.append(noteBtn, up, down);
    }
    node.append(bubble, meta);
    return node;
  }

  function citeFactory(m) {
    const chunks = m.sources?.chunks || [];
    return (n) => {
      const c = chunks[n - 1];
      if (!c) return `[${n}]`;
      const chip = el("sup", { class: "cite-chip", text: String(n), title: c.doc });
      chip.addEventListener("click", () => {
        toast(`${c.doc}: ${(c.text || "").slice(0, 140)}…`, "info", 6000);
      });
      return chip.outerHTML;
    };
  }

  function sourcesNode(sources) {
    const box = el("div", { class: "sources" });
    for (const c of sources.chunks) {
      const card = el("div", { class: "source-card panel" });
      const headRow = el("div", { class: "src-head" });
      headRow.append(
        el("span", { html: icon("file", 13), style: "display:flex;color:var(--ink-3)" }),
        el("span", { class: "src-doc", text: c.doc }),
        el("span", { class: "src-score", text: c.score != null ? `score ${Number(c.score).toFixed(2)}` : "" })
      );
      card.append(headRow, el("div", { class: "src-text", text: c.text }));
      box.append(card);
    }
    return box;
  }

  function globalSources(citations) {
    const box = el("div", { class: "sources" });
    for (const c of citations.citations || []) {
      const card = el("div", { class: "source-card panel" });
      card.append(
        el("div", { class: "src-head" }, [
          el("span", { class: "src-doc", text: `Community ${c.community_id}` }),
        ]),
        el("div", { class: "src-text", text: c.summary || "" })
      );
      box.append(card);
    }
    return box;
  }

  async function saveAsNote(m) {
    const titleInput = el("input", { class: "input", value: (m.content || "").slice(0, 80) || "Saved answer" });
    const save = el("button", { class: "btn btn-primary btn-sm", text: "Save note" });
    save.addEventListener("click", async () => {
      try {
        await api.saveNote(vault.name, {
          title: titleInput.value.trim() || "Saved answer",
          content: m.content || "",
          message_id: m.id || null,
        });
        closeModal();
        toast("Saved as a note — it will be indexed and citable.", "ok");
      } catch (err) {
        toast(err.message || "Could not save note.", "error");
      }
    });
    openModal({
      title: "Save answer as note",
      sub: "Notes are real documents: chunked, embedded, and citable in future answers.",
      body: (b) => b.append(el("div", { class: "field" }, [el("label", { text: "Title" }), titleInput])),
      footer: [save],
    });
  }

  async function sendFeedback(messageId, rating) {
    await api.feedback(messageId, { rating }).catch(() => {});
    toast(rating > 0 ? "Thanks — recorded." : "Sorry about that — noted.", "ok");
  }

  /* ── submit + stream ───────────────────────────────────────────────── */
  async function submit() {
    const q = ta.value.trim();
    if (!q || aborter) return;
    ta.value = "";
    ta.style.height = "auto";

    clear(thread);
    const userMsg = el("div", { class: "msg msg-user" }, [el("div", { class: "bubble", text: q })]);
    thread.append(userMsg);

    const aNode = el("div", { class: "msg msg-assistant" });
    const bubble = el("div", { class: "bubble" });
    const body = el("div", { class: "msg-body", html: `<span class="typing-dots"><span></span><span></span><span></span></span>` });
    bubble.append(body);
    aNode.append(bubble);
    thread.append(aNode);
    scroll.scrollTop = scroll.scrollHeight;

    let answer = "";
    let sources = null;
    let citations = null;
    let done = null;
    let alert = null;
    const thinkLog = el("div");

    aborter = new AbortController();
    send.innerHTML = icon("stop", 15);
    const finish = () => {
      aborter = null;
      send.innerHTML = icon("send", 16);
    };

    try {
      await askStream(
        {
          question: q,
          corpus: vault.name,
          conversation_id: activeConv || undefined,
          document_ids: selection.length ? selection : undefined,
        },
        (event, data) => {
          if (event === "tokens") {
            answer += data.text;
            body.innerHTML = mdToHtml(answer, () => "");
          } else if (event === "sources") {
            sources = data;
            if (data.chunks?.length && !data.agent) {
              const existing = bubble.querySelector(".sources");
              if (existing) existing.remove();
              bubble.append(sourcesNode(data));
            }
          } else if (event === "citations") {
            citations = data;
            body.innerHTML = mdToHtml(answer, citeFactory({ sources }));
          } else if (event === "thinking") {
            const line = el("div", { class: "thinking" });
            line.textContent = data.label || data.summary || "thinking…";
            thinkLog.append(line);
            if (!bubble.contains(thinkLog)) bubble.prepend(thinkLog);
          } else if (event === "contradiction") {
            alert = data;
          } else if (event === "meta") {
            if (data.filters?.tags?.length) {
              laneInfo.textContent = `filters: ${data.filters.tags.join(", ")}`;
            }
          } else if (event === "done") {
            done = data;
            if (data.conversation_id && data.conversation_id !== activeConv) {
              activeConv = data.conversation_id;
            }
          }
          scroll.scrollTop = scroll.scrollHeight;
        },
        aborter.signal
      );
    } catch (err) {
      if (err.name !== "AbortError") {
        answer = answer || `[error: ${err.message}]`;
        body.innerHTML = mdToHtml(answer, () => "");
      }
    } finally {
      finish();
    }

    if (alert) {
      const banner = el("div", { class: "alert-banner" });
      banner.append(
        el("span", { class: "icon", html: icon("alert", 15) }),
        el("span", { text: `Heads up — your sources disagree here. ${alert.reason || ""}` })
      );
      bubble.prepend(banner);
    }

    body.innerHTML = mdToHtml(answer, citeFactory({ sources }));
    const existing = bubble.querySelector(".sources");
    if (existing) existing.remove();
    if (sources?.chunks?.length) bubble.append(sourcesNode(sources));

    const meta = el("div", { class: "msg-actions" });
    const noteBtn = el("button", { class: "icon-btn", title: "Save as note", html: icon("note", 14) });
    noteBtn.addEventListener("click", () => saveAsNote({ content: answer, id: done?.message_id }));
    if (done?.usage) {
      meta.append(
        el("span", { class: "lane-chip", text: `${done.usage.lane || ""} · ${done.usage.in || 0}→${done.usage.out || 0} tok${done.cached ? " · cached" : ""}` })
      );
    }
    meta.append(noteBtn);
    aNode.append(meta);

    // refresh the conversation list (new conversation may have been created)
    conversations = await api.conversations(vault.name).catch(() => conversations);
    paintConvTabs();
    scroll.scrollTop = scroll.scrollHeight;
  }

  /* ── boot the view ─────────────────────────────────────────────────── */
  paintConvTabs();
  paintSelectionBar();
  await loadThread();
  if (!activeConv) paintSuggestions();
  ta.focus();
}
