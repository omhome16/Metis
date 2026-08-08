/* Shared helpers: DOM, formatting, tiny markdown renderer. */

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c) node.append(c);
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

export function fmtBytes(n) {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v >= 100 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

export function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const now = new Date();
  const sameYear = d.getFullYear() === now.getFullYear();
  return d.toLocaleDateString(undefined, sameYear
    ? { month: "short", day: "numeric" }
    : { year: "numeric", month: "short", day: "numeric" });
}

export function fmtTime(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

export function debounce(fn, ms) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

/* ── markdown-lite ────────────────────────────────────────── */

/**
 * Render answer markdown to HTML. `cite(n)` returns the HTML string
 * for a citation chip (or null to render the plain "[n]" text).
 */
export function mdToHtml(md, cite) {
  const lines = String(md ?? "").split("\n");
  const blocks = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // fenced code
    if (/^```/.test(line.trim())) {
      const buf = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i].trim())) buf.push(lines[i]), i++;
      i++;
      blocks.push({ type: "code", text: buf.join("\n") });
      continue;
    }

    // heading
    const h = /^(#{1,3})\s+(.*)$/.exec(line);
    if (h) {
      blocks.push({ type: "h", level: h[1].length, html: inline(h[2], cite) });
      i++;
      continue;
    }

    // lists
    if (/^\s*[-*+]\s+/.test(line) || /^\s*\d+[.)]\s+/.test(line)) {
      const ordered = /^\s*\d+/.test(line);
      const items = [];
      while (i < lines.length && (/^\s*[-*+]\s+/.test(lines[i]) || /^\s*\d+[.)]\s+/.test(lines[i]))) {
        items.push(inline(lines[i].replace(/^\s*[-*+]\s+/, "").replace(/^\s*\d+[.)]\s+/, ""), cite));
        i++;
      }
      blocks.push({ type: ordered ? "ol" : "ul", items });
      continue;
    }

    // paragraph (collect until blank or another block start)
    const buf = [];
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !/^(#{1,3})\s/.test(lines[i]) &&
      !/^```/.test(lines[i].trim()) &&
      !/^\s*[-*+]\s+/.test(lines[i]) &&
      !/^\s*\d+[.)]\s+/.test(lines[i])
    ) {
      buf.push(lines[i]);
      i++;
    }
    if (buf.length) blocks.push({ type: "p", html: inline(buf.join(" "), cite) });
    else i++;
  }

  return blocks
    .map((b) => {
      switch (b.type) {
        case "h": return `<h${b.level}>${b.html}</h${b.level}>`;
        case "ul": return `<ul>${b.items.map((x) => `<li>${x}</li>`).join("")}</ul>`;
        case "ol": return `<ol>${b.items.map((x) => `<li>${x}</li>`).join("")}</ol>`;
        case "code": return `<pre><code>${esc(b.text)}</code></pre>`;
        default: return `<p>${b.html}</p>`;
      }
    })
    .join("");
}

function inline(src, cite) {
  let s = esc(src);

  // code spans
  s = s.replace(/`([^`]+)`/g, (_, c) => `<code>${c}</code>`);

  // citation chips — must come before bold so [1] inside ** doesn't break
  if (cite) {
    s = s.replace(/\[(\d+(?:\s*,\s*\d+)*)\]/g, (m, nums) =>
      nums
        .split(",")
        .map((n) => n.trim())
        .filter((n) => /^\d+$/.test(n))
        .map((n) => cite(Number(n)))
        .join("") || m
    );
  }

  // links (safe: http/https or same-origin paths only)
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+|\/[^\s)]+)\)/g, (_, t, u) => `<a href="${u}" target="_blank" rel="noopener">${t}</a>`);

  // bold / italic
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  return s;
}
