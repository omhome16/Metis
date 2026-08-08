import { el, clear } from "../util.js";
import { icon } from "../icons.js";

let currentModal = null;

export function closeModal() {
  if (!currentModal) return;
  const backdrop = currentModal;
  currentModal = null;
  backdrop.classList.add("closing");
  setTimeout(() => backdrop.remove(), 200);
}

/** Open a modal. bodyFn(modalEl) may return an array of footer buttons. */
export function openModal({ title, sub = null, body, footer = [], wide = false }) {
  closeModal();
  const backdrop = el("div", { class: "modal-backdrop" });
  backdrop.addEventListener("click", (ev) => {
    if (ev.target === backdrop) closeModal();
  });
  document.addEventListener("keydown", onEsc);

  const modal = el("div", { class: "modal", style: wide ? "width:min(860px,100%)" : "" });
  const head = el("div", { class: "modal-head" });
  const titleWrap = el("div");
  titleWrap.append(el("div", { class: "modal-title", text: title }));
  if (sub) titleWrap.append(el("div", { class: "modal-sub", html: sub }));
  const closeBtn = el("button", { class: "icon-btn", title: "Close", html: icon("x") });
  closeBtn.addEventListener("click", closeModal);
  head.append(titleWrap, closeBtn);
  modal.append(head);

  const bodyEl = el("div", { class: "modal-body" });
  body(bodyEl, modal);
  modal.append(bodyEl);

  if (footer.length) {
    const foot = el("div", { class: "modal-foot" });
    for (const btn of footer) foot.append(btn);
    modal.append(foot);
  }

  backdrop.append(modal);
  document.getElementById("modal-root").append(backdrop);
  currentModal = backdrop;
  return modal;

  function onEsc(ev) {
    if (ev.key === "Escape") {
      document.removeEventListener("keydown", onEsc);
      closeModal();
    }
  }
}

/** Rebuild the modal body (e.g. after switching tabs). */
export function setModalBody(modal, bodyFn, footer = []) {
  const bodyEl = modal.querySelector(".modal-body");
  clear(bodyEl);
  bodyFn(bodyEl, modal);
  const footEl = modal.querySelector(".modal-foot");
  if (footEl) footEl.remove();
  if (footer.length) {
    const foot = el("div", { class: "modal-foot" });
    for (const btn of footer) foot.append(btn);
    modal.append(foot);
  }
}

export function confirmDialog({ title, message, confirmLabel = "Delete", danger = true }) {
  return new Promise((resolve) => {
    const okBtn = el("button", { class: `btn ${danger ? "btn-danger" : "btn-primary"}`, text: confirmLabel });
    const cancelBtn = el("button", { class: "btn btn-ghost", text: "Cancel" });
    okBtn.addEventListener("click", () => { closeModal(); resolve(true); });
    cancelBtn.addEventListener("click", () => { closeModal(); resolve(false); });
    openModal({
      title,
      body: (b) => b.append(el("p", { text: message, style: "line-height:1.6" })),
      footer: [cancelBtn, okBtn],
    });
  });
}

export function button(label, cls, onClick) {
  const btn = el("button", { class: `btn ${cls}`, text: label });
  btn.addEventListener("click", onClick);
  return btn;
}
