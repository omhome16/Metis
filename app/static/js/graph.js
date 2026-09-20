/* Force-directed knowledge-graph renderer on canvas.

Interactions: drag nodes, pan the canvas, wheel zoom, click to select.
Selection is delegated to the host view via onSelect (entity panel, chat). */

export function createGraph(holder, { onSelect = null, onEmpty = null } = {}) {
  const canvas = document.createElement("canvas");
  holder.append(canvas);
  const ctx = canvas.getContext("2d");

  let nodes = [];
  let links = [];
  let width = 0;
  let height = 0;
  let view = { x: 0, y: 0, k: 1 }; // pan + zoom
  let selected = null;
  let raf = null;
  let alpha = 0;

  const drag = { node: null, panning: false, px: 0, py: 0, moved: false };

  function resize() {
    const r = holder.getBoundingClientRect();
    width = Math.max(r.width, 50);
    height = Math.max(r.height, 50);
    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    canvas.style.width = width + "px";
    canvas.style.height = height + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    draw();
  }

  function setData(data) {
    nodes = (data?.nodes || []).map((n, i) => ({
      ...n,
      x: width / 2 + Math.cos((i / Math.max(1, data.nodes.length)) * Math.PI * 2) * 120,
      y: height / 2 + Math.sin((i / Math.max(1, data.nodes.length)) * Math.PI * 2) * 120,
      vx: 0,
      vy: 0,
    }));
    const byId = new Map(nodes.map((n) => [n.id, n]));
    links = (data?.edges || [])
      .map((e) => ({ source: byId.get(e.source), target: byId.get(e.target), type: e.type, dashed: !!e.inferred }))
      .filter((l) => l.source && l.target);
    selected = null;
    alpha = 1;
    kick();
    if (!nodes.length && onEmpty) onEmpty();
  }

  function kick() {
    alpha = 1;
    if (!raf) raf = requestAnimationFrame(tick);
  }

  function tick() {
    step();
    draw();
    if (alpha > 0.005) raf = requestAnimationFrame(tick);
    else raf = null;
  }

  function step() {
    // repulsion (capped pair sampling for big graphs)
    const rep = 1400;
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) { d2 = 1; dx = 1; }
        if (d2 > 90000) continue; // ignore far pairs
        const f = rep / d2;
        const d = Math.sqrt(d2);
        a.vx -= (dx / d) * f;
        a.vy -= (dy / d) * f;
        b.vx += (dx / d) * f;
        b.vy += (dy / d) * f;
      }
    }
    // springs
    for (const l of links) {
      const dx = l.target.x - l.source.x;
      const dy = l.target.y - l.source.y;
      const d = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
      const f = (d - 110) * 0.015;
      l.source.vx += (dx / d) * f;
      l.source.vy += (dy / d) * f;
      l.target.vx -= (dx / d) * f;
      l.target.vy -= (dy / d) * f;
    }
    // centering + integrate
    for (const n of nodes) {
      if (n === drag.node) { n.vx = 0; n.vy = 0; continue; }
      n.vx += (width / 2 - n.x) * 0.002;
      n.vy += (height / 2 - n.y) * 0.002;
      n.x += Math.max(-8, Math.min(8, n.vx));
      n.y += Math.max(-8, Math.min(8, n.vy));
      n.vx *= 0.85;
      n.vy *= 0.85;
    }
    alpha *= 0.98;
  }

  const COL = {
    entity: "#D97757",
    document: "#6B7FBC",
    chunk: "#8A877F",
    image: "#B98A2E",
    edge: "rgba(138, 135, 127, 0.4)",
    edgeAccent: "rgba(217, 119, 87, 0.85)",
  };

  function draw() {
    ctx.clearRect(0, 0, width, height);
    ctx.save();
    ctx.translate(view.x, view.y);
    ctx.scale(view.k, view.k);

    // edges
    for (const l of links) {
      const hi = selected && (l.source === selected || l.target === selected);
      ctx.strokeStyle = hi ? COL.edgeAccent : COL.edge;
      ctx.lineWidth = hi ? 1.6 : 1;
      if (l.dashed && !hi) ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(l.source.x, l.source.y);
      ctx.lineTo(l.target.x, l.target.y);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // nodes
    for (const n of nodes) {
      const isSel = n === selected;
      const r = n.label === "Entity" ? (isSel ? 9 : 6.5) : isSel ? 8 : 5;
      if (isSel) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + 5, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(217, 119, 87, 0.18)";
        ctx.fill();
      }
      ctx.beginPath();
      if (n.label === "Document") {
        ctx.roundRect(n.x - r, n.y - r, r * 2, r * 2, 3);
        ctx.fillStyle = COL.document;
      } else {
        ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
        ctx.fillStyle = n.label === "Image" ? COL.image : COL.entity;
      }
      ctx.fill();
      if (view.k > 0.7 || isSel) {
        ctx.font = `${isSel ? 600 : 400} 11px -apple-system, 'Segoe UI', sans-serif`;
        ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--ink-2").trim() || "#555";
        ctx.textAlign = "center";
        const name = n.name || "";
        ctx.fillText(name.length > 26 ? name.slice(0, 24) + "…" : name, n.x, n.y + r + 12);
      }
    }
    ctx.restore();
  }

  /* ── interaction ─────────────────────────────────────────────────────── */

  function toWorld(ev) {
    const r = canvas.getBoundingClientRect();
    return {
      x: (ev.clientX - r.left - view.x) / view.k,
      y: (ev.clientY - r.top - view.y) / view.k,
    };
  }

  function pick(ev) {
    const p = toWorld(ev);
    let best = null;
    let bestD = 14;
    for (const n of nodes) {
      const d = Math.hypot(n.x - p.x, n.y - p.y);
      if (d < bestD) { best = n; bestD = d; }
    }
    return best;
  }

  canvas.addEventListener("pointerdown", (ev) => {
    canvas.setPointerCapture(ev.pointerId);
    const n = pick(ev);
    drag.moved = false;
    if (n) {
      drag.node = n;
      selected = n;
      onSelect?.(n);
      kick();
    } else {
      drag.panning = true;
      drag.px = ev.clientX - view.x;
      drag.py = ev.clientY - view.y;
    }
  });

  canvas.addEventListener("pointermove", (ev) => {
    if (drag.node) {
      drag.moved = true;
      const p = toWorld(ev);
      drag.node.x = p.x;
      drag.node.y = p.y;
      kick();
    } else if (drag.panning) {
      drag.moved = true;
      view.x = ev.clientX - drag.px;
      view.y = ev.clientY - drag.py;
      draw();
    }
  });

  canvas.addEventListener("pointerup", () => {
    drag.node = null;
    drag.panning = false;
  });

  canvas.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    const factor = ev.deltaY < 0 ? 1.1 : 0.9;
    const r = canvas.getBoundingClientRect();
    const mx = ev.clientX - r.left;
    const my = ev.clientY - r.top;
    view.x = mx - (mx - view.x) * factor;
    view.y = my - (my - view.y) * factor;
    view.k = Math.max(0.15, Math.min(4, view.k * factor));
    draw();
  }, { passive: false });

  canvas.addEventListener("dblclick", () => {
    // reset view
    view = { x: 0, y: 0, k: 1 };
    kick();
  });

  const ro = new ResizeObserver(resize);
  ro.observe(holder);
  resize();

  return {
    setData,
    selectByName(name) {
      const n = nodes.find((x) => x.name === name) || null;
      selected = n;
      onSelect?.(n);
      kick();
    },
    destroy() {
      ro.disconnect();
      if (raf) cancelAnimationFrame(raf);
      canvas.remove();
    },
  };
}
