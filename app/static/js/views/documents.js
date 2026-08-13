import { el, clear, fmtBytes, fmtDate, debounce } from "../util.js";
import { icon } from "../icons.js";
import { api } from "../api.js";
import { renderVaultShell } from "./shell.js";
import { openModal, closeModal, confirmDialog, setModalBody } from "./modal.js";
import { toast } from "./toast.js";

export async function renderDocuments(container, vault) {
  const body = renderVaultShell(container, vault, "documents");
  body.append(el("div", { class: "doc-sink" }));

  const sink = body.querySelector(".doc-sink");
  const toolbar = el("div", { class: "toolbar" });
  const searchField = el("div", { class: "field" }, [
    el("span", { html: icon("search", 15) }),
    el("input", { placeholder: "Filter documents", "aria-label": "Filter documents" }),
  ]);
  const addBtn = el("button", { class: "btn btn-primary", html: `${icon("upload", 15)}<span>Add documents</span>` });
  addBtn.addEventListener("click", () => openUploadModal(vault, () => load()));
  toolbar.append(searchField, addBtn);
  sink.append(toolbar);

  const grid = el("div", { class: "doc-grid" });
  sink.append(grid);

  const input = searchField.querySelector("input");
  input.addEventListener("input", debounce(() => applyFilter(input.value), 120));

  let docs = [];
  async function load() {
    grid.classList.add("loading");
    grid.innerHTML = '<div class="empty" style="grid-column:1/-1;padding:40px"><div class="empty-copy" style="margin:0">Loading documents…</div></div>';
    try {
      docs = await api.documents(vault.name);
    } catch (err) {
      grid.innerHTML = "";
      grid.append(emptyState("Could not load documents.", err.message || "", () => load()));
      return;
    }
    grid.classList.remove("loading");
    render(docs);
  }

  function applyFilter(q) {
    const needle = q.trim().toLowerCase();
    render(needle ? docs.filter((d) => d.title.toLowerCase().includes(needle)) : docs);
  }

  function render(list) {
    clear(grid);
    if (!list.length) {
      grid.append(emptyState(
        "No documents here yet",
        "Drop in a PDF, markdown file, plain text, or an image. Metis will chunk it, embed it, and map its relationships.",
        () => openUploadModal(vault, () => load())
      ));
      return;
    }
    for (const d of list) {
      const glyph = d.format === "image" ? "image" : d.format === "pdf" ? "pdf" : d.format === "txt" ? "txt" : "doc";
      const meta = [fmtBytes(d.size), d.chunk_count ? `${d.chunk_count} chunks` : null, d.image_count ? `${d.image_count} image` : null, fmtDate(d.ingested_at)]
        .filter(Boolean)
        .join("  ·  ");
      const card = el("div", { class: "doc-card", tabindex: "0" });
      card.append(
        el("div", { class: "doc-card-top" }, [
          el("span", { class: `doc-glyph ${d.format}`, html: icon(glyph, 16) }),
          el("span", { class: `status-badge ${d.status}`, text: d.status }),
          ...(d.extraction_status && d.extraction_status !== "ok"
            ? [el("span", { class: `status-badge ${d.extraction_status}`, text: d.extraction_status === "ocr" ? "OCR" : "no text", title: d.extraction_status === "ocr" ? "Text recovered via OCR" : "No extractable text found" })]
            : []),
        ]),
        el("div", { class: "doc-card-title", text: d.title }),
        el("div", { class: "doc-card-meta", text: meta }),
        el("div", { class: "doc-card-actions" }, [
          el("button", { class: "btn btn-ghost", html: `${icon("file", 13)}<span>View</span>`, style: "padding:5px 9px;font-size:12px" }),
          el("button", { class: "btn btn-ghost", html: `${icon("trash", 13)}`, style: "padding:5px 8px;color:var(--err)" }),
        ])
      );
      const [viewBtn, delBtn] = card.querySelectorAll(".doc-card-actions button");
      viewBtn.addEventListener("click", (ev) => { ev.stopPropagation(); openDocModal(vault, d, () => load()); });
      delBtn.addEventListener("click", (ev) => { ev.stopPropagation(); deleteDoc(d); });
      card.addEventListener("click", () => openDocModal(vault, d, () => load()));
      card.addEventListener("keydown", (ev) => { if (ev.key === "Enter") openDocModal(vault, d, () => load()); });
      grid.append(card);
    }
  }

  function emptyState(title, copy, action) {
    const empty = el("div", { class: "empty", style: "grid-column:1/-1" });
    empty.append(
      el("div", { class: "empty-glyph", html: icon("layers", 26) }),
      el("div", { class: "empty-title", text: title }),
      el("p", { class: "empty-copy", text: copy }),
      el("button", { class: "btn btn-primary", text: "Add documents" })
    );
    empty.querySelector("button").addEventListener("click", action);
    return empty;
  }

  async function deleteDoc(d) {
    const ok = await confirmDialog({
      title: `Remove "${d.title}"?`,
      message: "The document, its chunks, and its graph nodes will be removed from the vault.",
      confirmLabel: "Remove document",
    });
    if (!ok) return;
    try {
      await api.deleteDoc(d.id);
      toast("Document removed.", "ok");
      load();
    } catch (err) {
      toast(err.message || "Could not remove document.", "error");
    }
  }

  await load();
}

/* ── upload modal ─────────────────────────────────────────── */

export function openUploadModal(vault, onDone) {
  const dz = el("div", { class: "dropzone" });
  dz.innerHTML = `${icon("upload", 30)}<div class="dropzone-copy">Drop files here, or <strong>browse</strong><div class="hint">pdf · md · txt · png · jpg · webp — up to 50 MB each</div></div>`;
  const fileInput = el("input", { type: "file", multiple: true, accept: ".pdf,.md,.markdown,.txt,.png,.jpg,.jpeg,.webp", class: "sr-only" });
  const uploadList = el("div", { class: "upload-list" });
  const progress = el("div", { class: "progress-track hidden" });
  const fill = el("div", { class: "progress-fill", style: "width:0%" });
  progress.append(fill);

  let files = [];
  const uploadBtn = el("button", { class: "btn btn-primary", text: "Upload to vault" });
  uploadBtn.disabled = true;

  dz.addEventListener("click", () => fileInput.click());
  dz.addEventListener("dragover", (ev) => { ev.preventDefault(); dz.classList.add("dragover"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("dragover"));
  dz.addEventListener("drop", (ev) => {
    ev.preventDefault();
    dz.classList.remove("dragover");
    addFiles([...ev.dataTransfer.files]);
  });
  fileInput.addEventListener("change", () => addFiles([...fileInput.files]));

  function addFiles(list) {
    files = files.concat(list.filter((f) => !files.some((x) => x.name === f.name && x.size === f.size)));
    renderList();
    uploadBtn.disabled = !files.length;
  }

  function renderList() {
    clear(uploadList);
    for (const f of files) {
      const row = el("div", { class: "upload-row" });
      const stateEl = el("span", { class: "up-state", text: fmtBytes(f.size) });
      const nameEl = el("span", { class: "up-name", text: f.name });
      const rm = el("button", { class: "icon-btn", title: "Remove", html: icon("x", 13), style: "width:24px;height:24px" });
      rm.addEventListener("click", () => {
        files = files.filter((x) => x !== f);
        renderList();
        uploadBtn.disabled = !files.length;
      });
      row.append(nameEl, stateEl, rm);
      uploadList.append(row);
    }
  }

  uploadBtn.addEventListener("click", async () => {
    if (!files.length) return;
    uploadBtn.disabled = true;
    uploadBtn.textContent = "Uploading…";
    progress.classList.remove("hidden");
    try {
      const res = await api.ingest(vault.name, files);
      fill.style.width = "12%";
      await pollJob(res.job_id, fill);
      toast(`${res.files_added} document${res.files_added === 1 ? "" : "s"} added to "${vault.name}".`, "ok");
      closeModal();
      if (onDone) onDone();
    } catch (err) {
      toast(err.message || "Upload failed.", "error");
      uploadBtn.disabled = false;
      uploadBtn.textContent = "Upload to vault";
    }
  });

  openModal({
    title: `Add documents to ${vault.name}`,
    sub: "Files are indexed in the background — chunking, embeddings, and graph extraction.",
    body: (b) => {
      b.append(dz, fileInput, uploadList, progress);
      if (!files.length) uploadBtn.disabled = true;
    },
    footer: [el("button", { class: "btn btn-ghost", text: "Cancel", onclick: () => closeModal() }), uploadBtn],
  });
}

function pollJob(jobId, fill) {
  return new Promise((resolve, reject) => {
    const tick = async () => {
      try {
        const job = await api.job(jobId);
        fill.style.width = `${Math.max(parseFloat(fill.style.width || "0"), job.progress)}%`;
        if (job.status === "done" || job.status === "failed") {
          if (job.status === "failed" && Object.keys(job.per_file_errors || {}).length) {
            reject(new Error(`Some files failed to index: ${Object.values(job.per_file_errors)[0]}`));
            return;
          }
          resolve();
          return;
        }
        setTimeout(tick, 1400);
      } catch (err) {
        reject(err);
      }
    };
    tick();
  });
}

/* ── document detail modal ────────────────────────────────── */

async function openDocModal(vault, doc, onDeleted) {
  const modal = openModal({
    title: doc.title,
    sub: `${doc.corpus}  ·  ${doc.format.toUpperCase()}  ·  ${fmtBytes(doc.size)}  ·  ${fmtDate(doc.ingested_at)}`,
    body: (b) => b.append(el("div", { class: "empty-copy", text: "Loading…", style: "margin:0" })),
    footer: [el("button", { class: "btn btn-ghost", text: "Close", onclick: () => closeModal() })],
    wide: true,
  });

  let content = "";
  let chunks = [];
  try {
    const [c, ch] = await Promise.all([api.docContent(doc.id), api.docChunks(doc.id)]);
    content = c.text || "";
    chunks = ch;
  } catch { /* content optional */ }

  const tabsEl = el("div", { class: "modal-tabs" });
  const tabContent = el("div", { style: "padding:16px 22px 20px" });
  const delBtn = el("button", { class: "btn btn-danger", html: `${icon("trash", 14)}<span>Remove</span>` });
  delBtn.addEventListener("click", async () => {
    const ok = await confirmDialog({
      title: `Remove "${doc.title}"?`,
      message: "This removes the document and its chunks from the vault.",
      confirmLabel: "Remove document",
    });
    if (!ok) return;
    try {
      await api.deleteDoc(doc.id);
      toast("Document removed.", "ok");
      closeModal();
      if (onDeleted) onDeleted();
    } catch (err) {
      toast(err.message || "Could not remove document.", "error");
    }
  });
  const foot = modal.querySelector(".modal-foot");
  foot.prepend(delBtn);

  const contentTab = el("button", { class: "tab active", text: "Content" });
  const chunksTab = el("button", { class: "tab", text: `Chunks (${chunks.length})` });
  contentTab.addEventListener("click", () => { contentTab.classList.add("active"); chunksTab.classList.remove("active"); renderContent(); });
  chunksTab.addEventListener("click", () => { chunksTab.classList.add("active"); contentTab.classList.remove("active"); renderChunks(); });
  tabsEl.append(contentTab, chunksTab);

  function renderContent() {
    clear(tabContent);
    if (doc.format === "image") {
      tabContent.append(el("img", { class: "doc-image", src: api.docFileUrl(doc.id), alt: doc.title }));
    } else if (content) {
      tabContent.append(el("div", { class: "doc-content", text: content }));
    } else {
      tabContent.append(el("p", { class: "ask-panel-empty", text: "No extractable text for this document." }));
    }
  }

  function renderChunks() {
    clear(tabContent);
    if (!chunks.length) {
      tabContent.append(el("p", { class: "ask-panel-empty", text: "No chunks yet — indexing may still be running." }));
      return;
    }
    for (const c of chunks) {
      tabContent.append(el("div", { class: "chunk-item" }, [
        el("div", { class: "chunk-item-head" }, [
          el("span", { text: `#${c.index}` }),
          el("span", { text: `${c.tokens} tokens` }),
        ]),
        el("div", { class: "chunk-item-text", text: c.text }),
      ]));
    }
  }

  const bodyEl = modal.querySelector(".modal-body");
  bodyEl.append(tabsEl, tabContent);
  renderContent();
}
