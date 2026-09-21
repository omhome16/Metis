/* Sign in / register. Shown whenever the API says accounts are on and the
   browser has no valid session. Exchanges the JWT for a page reload. */

import { el, clear } from "../util.js";
import { icon } from "../icons.js";
import { api, setJwt, setApiToken } from "../api.js";
import { state } from "../store.js";

export function renderAuth(view) {
  clear(view);
  document.body.classList.add("authing");
  state.user = null;
  state.needsAuth = true;

  const wrap = el("div", { class: "auth-wrap" });
  const card = el("div", { class: "auth-card card" });

  let mode = "login";

  const email = el("input", { class: "input", type: "email", placeholder: "you@example.com", autocomplete: "email" });
  const password = el("input", { class: "input", type: "password", placeholder: "••••••••", autocomplete: "current-password" });
  const name = el("input", { class: "input", type: "text", placeholder: "How should we greet you? (optional)" });
  const error = el("p", { class: "form-error hidden" });
  const title = el("h1");
  const sub = el("p", { class: "auth-sub" });
  const submit = el("button", { class: "btn btn-primary", style: "width:100%" });
  const switchLine = el("p", { class: "auth-switch" });
  const nameField = el("div", { class: "field hidden" }, [el("label", { text: "Display name" }), name]);

  const paint = () => {
    title.textContent = mode === "login" ? "Welcome back" : "Create your library";
    sub.textContent =
      mode === "login"
        ? "Sign in to your knowledge vaults."
        : "One account. Every vault you build stays yours.";
    submit.textContent = mode === "login" ? "Sign in" : "Create account";
    nameField.classList.toggle("hidden", mode === "login");
    error.classList.add("hidden");
    const btn = el("button", { text: mode === "login" ? "Need an account? Register" : "Already have an account? Sign in" });
    btn.addEventListener("click", () => {
      mode = mode === "login" ? "register" : "login";
      paint();
    });
    clear(switchLine);
    switchLine.append(btn);
  };

  submit.addEventListener("click", async () => {
    error.classList.add("hidden");
    submit.disabled = true;
    submit.textContent = mode === "login" ? "Signing in…" : "Creating…";
    try {
      setApiToken(""); // users mode supersedes any legacy shared token
      const fn = mode === "login" ? api.login : api.register;
      const body = { email: email.value.trim(), password: password.value };
      if (mode === "register" && name.value.trim()) body.display_name = name.value.trim();
      const res = await fn(body);
      setJwt(res.token);
      state.needsAuth = false;
      document.body.classList.remove("authing");
      location.hash = "#/";
      location.reload(); // simplest full re-entry with the new session
    } catch (err) {
      error.textContent = err.message || "Something went wrong.";
      error.classList.remove("hidden");
      submit.disabled = false;
      paint();
    }
  });

  password.addEventListener("keydown", (e) => {
    if (e.key === "Enter") submit.click();
  });
  email.addEventListener("keydown", (e) => {
    if (e.key === "Enter") password.focus();
  });

  card.append(
    el("div", { class: "auth-mark", html: icon("mark", 30) }),
    title,
    sub,
    el("div", { class: "field" }, [el("label", { text: "Email" }), email]),
    el("div", { class: "field" }, [el("label", { text: "Password" }), password]),
    nameField,
    error,
    submit,
    switchLine
  );
  wrap.append(card);
  view.append(wrap);
  paint();
  email.focus();
}
