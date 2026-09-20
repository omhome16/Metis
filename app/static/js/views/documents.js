/* Vault documents: upload, connectors (URL/Readwise/Zotero), export, inspect. */

import { el, clear, fmtBytes, fmtDate } from "../util.js";
import { icon } from "../icons.js";
import { api } from "../api.js";
import { toast } from "./toast.js";
import { openModal, closeModal, confirmDialog } from "./modal.js";

const FMT_ICON = { pdf: "pdf", epub: "book", md: "file", txt: "txt", image: "image" };

export async function renderDocuments(view, vault) {
  clear(view);
  const wrap = el("div", { class: "docs-wrap" });
  view.append(wrap);

  wrap.append(
    el("div", { class: "section-head" }, [
      el("div", {}, [
        el("h1", { text: vault.name }),
        el("p", { class: "section-sub", text: vault.description || "Documents, imports, and the library this vault indexes." }),
      ]),
    ])
  );

  const toolbar = el("div", { class: "docs-toolbar" });
  const uploadBtn = el("button", { class: "btn btn-primary", html: `${icon("upload", 15)} Upload files` });
  const importBtn = el("button", { class: "btn btn-secondary", html: `${icon("link", 15)} Import` });
  const exportBtn = el("button", { class: "btn btn-secondary", html: `${icon("download", 15)} Export` });
  toolbar.append(uploadBtn, importBtn, el("span", { class: "spacer" }), exportBtn);
  wrap.append(toolbar);

  const dropzone = el("div", { class: "dropzone" });
  dropzone.append(
    el("div", { html: icon("upload", 26), style: "display:flex;justify-content:center;margin-bottom:8px;color:var(--accent)" }),
    el("div", { text: "Drop files here, or click to browse" }),
    el("div", { class: "small", style: "margin-top:3px", text: "PDF · EPUB · Markdown · plain text · images" })
  );
  wrap.append(dropzone);

  const fileInput = el("input", { type: "file", multiple: true, class: "hidden", accept: ".pdf,.epub,.md,.markdown,.txt,.png,.jpg,.jpeg,.webp" });
  wrap.append(fileInput);

  const list = el("div", { class: "doc-list" });
  wrap.append(list);

  let pollTimer = null;

  uploadBtn.addEventListener("click", () => fileInput.click());
  dropzone.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", async () => {
    if (fileInput.files.length) await doUpload([...fileInput.files]);
    fileInput.value = "";
  });
  dropzone.addEventListener("dragover", (e) => { e.preventDefault(); dropzone.classList.add("over"); });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("over"));
  dropzone.addEventListener("drop", async (e) => {
    e.preventDefault();
    dropzone.classList.remove("over");
    if (e.dataTransfer.files.length) await doUpload([...e.dataTransfer.files]);
  });

  importBtn.addEventListener("click", openImportModal);
  exportBtn.addEventListener("click", async () => {
    toast("Building your export…", "info");
    try {
      await api.exportVault(vault.name);
      toast("Export downloaded — unzip into Obsidian or any markdown vault.", "ok");
    } catch (err) {
      toast(err.message || "Export failed.", "error");
    }
  });

  async function doUpload(files) {
    uploadBtn.disabled = true;
    dropzone.textContent = "Uploading…";
    try {
      const res = await api.ingest(vault.name, files);
      toast(`${res.files_added} file${res.files_added === 1 ? "" : "s"} queued for indexing.`, "ok");
      await loadDocs();
      pollJob(res.job_id);
    } catch (err) {
      toast(err.message || "Upload failed.", "error");
    } finally {
      uploadBtn.disabled = false;
      dropzone.innerHTML = "";
      dropzone.append(
        el("div", { html: icon("upload", 26), style: "display:flex;justify-content:center;margin-bottom:8px;color:var(--accent)" }),
        el("div", { text: "Drop files here, or click to browse" }),
        el("div", { class: "small", style: "margin-top:3px", text: "PDF · EPUB · Markdown · plain text · images" })
      );
    }
  }

  function pollJob(jobId) {
    if (!jobId || pollTimer) return;
    let checks = 0;
    pollTimer = setInterval(async () => {
      try {
        const job = await api.job(jobId);
        if (job.status === "done" || job.status === "failed" || ++checks > 240) {
          clearInterval(pollTimer);
          pollTimer = null;
          if (job.status === "failed" && Object.keys(job.per_file_errors || {}).length) {
            toast("Some files failed to index — see the document list.", "error");
          }
          await loadDocs();
        } else if (checks % 3 === 0) {
          await loadDocs(); // refresh statuses while the worker runs
        }
      } catch {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    }, 2500);
  }

  async function loadDocs() {
    const docs = await api.documents(vault.name).catch(() => []);
    clear(list);
    if (!docs.length) {
      list.append(
        el("div", { class: "empty" }, [
          el("div", { class: "glyph", html: icon("book", 26) }),
          el("h3", { text: "Nothing here yet" }),
          el("p", { text: "Upload files, import from the web, Readwise, or Zotero. Everything you add gets indexed into this vault's knowledge graph." }),
        ])
      );
      return;
    }
    for (const d of docs) list.append(docRow(d));
  }

  function docRow(d) {
    const statusClass = d.status === "indexed" ? "ok" : d.status === "error" ? "error" : "pending";
    const statusText = d.status === "indexed" ? "indexed" : d.status === "error" ? "error" : "indexing…";
    const row = el("div", { class: "doc-row card" });
    row.append(
      el("span", { class: "icon", html: icon(FMT_ICON[d.format] || "file", 18) }),
      el("div", { class: "main" }, [
        el("div", { class: "title", text: d.title }),
        el("div", { class: "sub" }, [
          el("span", { text: fmtDate(d.ingested_at) }),
          el("span", { text: `${d.chunk_count} chunks` }),
          d.image_count ? el("span", { text: `${d.image_count} images` }) : "",
          d.size ? el("span", { text: fmtBytes(d.size) }) : "",
        ].filter(Boolean)),
      ]),
      el("span", { class: `doc-status ${statusClass}`, text: statusText }),
      el("div", { class: "rail-actions" }, [
        el("button", { class: "icon-btn", title: "Download original", html: icon("download", 15) }),
        el("button", { class: "icon-btn", title: "Delete", html: icon("trash", 15) }),
      ])
    );
    const [dl, del] = row.querySelectorAll(".rail-actions .icon-btn");
    dl.addEventListener("click", (ev) => {
      ev.stopPropagation();
      api.docFile(d.id, d.title).catch((err) => toast(err.message, "error"));
    });
    del.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      if (!(await confirmDialog({ title: "Delete document", message: `"${d.title}" and its index entries will be removed.` }))) return;
      await api.deleteDoc(d.id).catch((err) => toast(err.message, "error"));
      loadDocs();
    });
    row.addEventListener("click", () => openDocModal(d));
    return row;
  }

  async function openDocModal(d) {
    openModal({
      title: d.title,
      sub: `${d.corpus} · ${d.format} · ${d.status}`,
      wide: true,
      body: async (b) => {
        const tabs = el("div", { class: "seg", style: "margin-bottom:14px" });
        const contentBtn = el("button", { class: "active", text: "Content" });
        const chunkBtn = el("button", { text: `Chunks (${d.chunk_count})` });
        tabs.append(contentBtn, chunkBtn);
        const bodyBox = el("div", { class: "doc-content", style: "white-space:pre-wrap;font-size:13.5px;line-height:1.7;color:var(--ink-2);max-height:52vh;overflow-y:auto" });
        b.append(tabs, bodyBox);
        const loadContent = async () => {
          contentBtn.classList.add("active");
          chunkBtn.classList.remove("active");
          const c = await api.docContent(d.id).catch(() => ({ text: "(unavailable)" }));
          clear(bodyBox);
          bodyBox.textContent = c.text || "(no extracted text)";
        };
        const loadChunks = async () => {
          chunkBtn.classList.add("active");
          contentBtn.classList.remove("active");
          const chunks = await api.docChunks(d.id).catch(() => []);
          clear(bodyBox);
          bodyBox.style.whiteSpace = "normal";
          for (const ch of chunks) {
            bodyBox.append(
              el("div", { class: "panel", style: "padding:10px 14px;margin-bottom:8px" }, [
                el("div", { class: "muted mono small", text: `chunk ${ch.index} · ${ch.tokens} tokens` }),
                el("div", { style: "font-size:12.5px;margin-top:4px;color:var(--ink-2)", text: ch.text }),
              ])
            );
          }
          bodyBox.style.whiteSpace = "pre-wrap";
        };
        contentBtn.addEventListener("click", loadContent);
        chunkBtn.addEventListener("click", loadChunks);
        await loadContent();
      },
      footer: [
        el("button", {
          class: "btn btn-secondary btn-sm",
          html: `${icon("download", 14)} Original file`,
          onclick: () => api.docFile(d.id, d.title).catch((err) => toast(err.message, "error")),
        }),
        el("button", { class: "btn btn-ghost btn-sm", text: "Close", onclick: closeModal }),
      ],
    });
  }

  function openImportModal() {
    const tabs = el("div", { class: "seg", style: "margin-bottom:16px" });
    const urlBtn = el("button", { class: "active", text: "Web page" });
    const rwBtn = el("button", { text: "Readwise" });
    const zoBtn = el("button", { text: "Zotero" });
    tabs.append(urlBtn, rwBtn, zoBtn);
    const box = el("div");

    const field = (label, input) => el("div", { class: "field" }, [el("label", { text: label }), input]);
    const urlInput = el("input", { class: "input", placeholder: "https://example.com/article" });
    const rwToken = el("input", { class: "input", placeholder: "Readwise access token", type: "password" });
    const zoKey = el("input", { class: "input", placeholder: "Zotero API key", type: "password" });
    const zoUser = el("input", { class: "input", placeholder: "Zotero user ID" });

    const go = el("button", { class: "btn btn-primary", text: "Import" });
    const hint = el("p", { class: "form-hint" });
    go.addEventListener("click", async () => {
      go.disabled = true;
      go.textContent = "Importing…";
      try {
        let res;
        if (urlBtn.classList.contains("active")) {
          res = await api.importUrl({ corpus: vault.name, url: urlInput.value.trim() });
        } else if (rwBtn.classList.contains("active")) {
          res = await api.importReadwise({ corpus: vault.name, token: rwToken.value.trim() });
        } else {
          res = await api.importZotero({ corpus: vault.name, api_key: zoKey.value.trim(), user_id: zoUser.value.trim() });
        }
        toast(`${res.files_added} item${res.files_added === 1 ? "" : "s"} queued for indexing.`, "ok");
        closeModal();
        loadDocs();
      } catch (err) {
        hint.textContent = err.message || "Import failed.";
        hint.style.color = "var(--danger)";
        go.disabled = false;
        go.textContent = "Import";
      }
    });

    const paintUrl = () => {
      clear(box);
      box.append(field("Page URL", urlInput), el("p", { class: "form-hint", text: "The article's main text is extracted and indexed." }));
    };
    const paintRw = () => {
      clear(box);
      box.append(
        field("Readwise access token", rwToken),
        el("p", { class: "form-hint", text: "Create one at readwise.io/access_token. Books with highlights become documents. The token is used for this import only — never stored." })
      );
    };
    const paintZo = () => {
      clear(box);
      box.append(
        field("Zotero API key", zoKey),
        field("Zotero user ID", zoUser),
        el("p", { class: "form-hint", text: "Create a key at zotero.org/settings/keys (read access). Item titles and abstracts become documents." })
      );
    };
    urlBtn.addEventListener("click", () => { [urlBtn, rwBtn, zoBtn].forEach((b) => b.classList.remove("active")); urlBtn.classList.add("active"); paintUrl(); });
    rwBtn.addEventListener("click", () => { [urlBtn, rwBtn, zoBtn].forEach((b) => b.classList.remove("active")); rwBtn.classList.add("active"); paintRw(); });
    zoBtn.addEventListener("click", () => { [urlBtn, rwBtn, zoBtn].forEach((b) => b.classList.remove("active")); zoBtn.classList.add("active"); paintZo(); });

    openModal({
      title: "Import into this vault",
      sub: "Bring sources in from where they already live.",
      body: (b) => {
        b.append(tabs, box, hint);
        paintUrl();
      },
      footer: [go],
    });
  }

  await loadDocs();
}
