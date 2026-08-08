/* ForceGraph — bespoke canvas knowledge-graph engine.
 *
 * Responsibilities: force layout (repulsion + springs + gravity), pan/zoom,
 * node drag, hover highlight with neighbor dimming, edge labels on hover,
 * click-to-expand, double-click focus, live theme colors.
 */

const FONT_UI = '"Inter", system-ui, sans-serif';
const FONT_MONO = '"IBM Plex Mono", monospace';

export class ForceGraph {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.nodes = [];
    this.edges = [];
    this.byId = new Map();
    this.view = { x: 0, y: 0, scale: 1 };
    this.hover = null; // node id
    this.hoverEdge = null; // {source, target, kind}
    this.selected = null; // node id
    this.pinned = new Set();
    this.onExpand = null; // (node) => Promise — fetch neighbors
    this.onSelect = null; // (node) => void
    this._raf = null;
    this._settle = 0;
    this._drag = null; // {id, dx, dy}
    this._pan = null; // {sx, sy, vx, vy}
    this._downAt = null;
    this._moved = false;
    this._warm = 0;
    this._theme = {};
    this._readTheme();

    const resize = () => this.resize();
    new ResizeObserver(resize).observe(canvas.parentElement);
    this._bind();
    this.resize();
  }

  /* ── theme ─────────────────────────────────────────────── */
  _readTheme() {
    const cs = getComputedStyle(document.documentElement);
    const v = (n) => cs.getPropertyValue(n).trim();
    this._theme = {
      entity: v("--node-entity") || "#2E6B4E",
      doc: v("--node-doc") || "#B0613A",
      image: v("--node-image") || "#4E6078",
      ring: v("--node-ring") || "#F3F1EA",
      ink: v("--ink") || "#22261F",
      soft: v("--ink-soft") || "#545A50",
      faint: v("--ink-faint") || "#79806F",
      faintest: v("--ink-faintest") || "#A2A795",
      hairline: v("--hairline") || "#E0DCCF",
      accent: v("--accent") || "#2E6B4E",
      glow: v("--glow-entity") || "rgba(46,107,78,0.35)",
    };
  }

  refreshTheme() {
    this._readTheme();
    this.draw();
  }

  /* ── data ──────────────────────────────────────────────── */
  setData(graph) {
    const nodes = (graph?.nodes || []).map((n) => this._makeNode(n, true));
    const edges = (graph?.edges || []).filter((e) => e.source !== e.target);
    this.nodes = nodes;
    this.edges = edges;
    this.byId = new Map(nodes.map((n) => [n.id, n]));
    this.selected = null;
    this.pinned.clear();
    this.journey = new Set();
    this.journeyEdges = new Set();
    this._seedPositions();
    this._warm = 140;
    this.fit(true);
    this.start();
  }

  _makeNode(raw, fresh = false) {
    const degree = raw.degree || 1;
    const label = raw.label || "Entity";
    const name = raw.name || raw.id;
    const r =
      label === "Entity"
        ? 5.5 + 3.4 * Math.log2(1 + degree)
        : label === "Document"
          ? 11
          : 9;
    return {
      id: raw.id,
      label,
      name,
      type: raw.type || label,
      degree,
      r,
      color: raw.color || null, // per-node override (library vault coloring)
      corpus: raw.corpus || null,
      corpora: raw.corpora || [],
      bridge: raw.corpora && raw.corpora.length > 1,
      x: 0,
      y: 0,
      vx: 0,
      vy: 0,
      fresh,
    };
  }

  /* ── journey highlight ─────────────────────────────────── */
  setJourney(nodeIds) {
    this.journey = new Set(nodeIds || []);
    this.journeyEdges = new Set();
    const ids = this.journey;
    for (const e of this.edges) {
      if (ids.has(e.source) && ids.has(e.target)) this.journeyEdges.add(`${e.source}|${e.target}`);
    }
    this.draw();
  }

  clearJourney() {
    this.journey = new Set();
    this.journeyEdges = new Set();
    this.draw();
  }

  /** Incremental expansion: merge new nodes/edges, animate pop-in. */
  expand(graph) {
    const added = [];
    for (const n of graph?.nodes || []) {
      if (!this.byId.has(n.id)) {
        const node = this._makeNode(n, true);
        node.x = this.nodes.length ? this.nodes[0].x : this.view.x;
        node.y = this.nodes.length ? this.nodes[0].y : this.view.y;
        this.nodes.push(node);
        this.byId.set(node.id, node);
        added.push(node);
      } else {
        this.byId.get(n.id).degree = Math.max(this.byId.get(n.id).degree, n.degree || 1);
      }
    }
    for (const e of graph?.edges || []) {
      if (e.source === e.target) continue;
      if (!this.edges.some((x) => x.source === e.source && x.target === e.target && x.kind === e.kind)) {
        this.edges.push(e);
      }
    }
    // warm up newly added nodes only
    this._warm = 90;
    this.start();
    return added;
  }

  /* ── physics ───────────────────────────────────────────── */
  start() {
    if (this._raf) return;
    const step = () => {
      if (this._step()) this._raf = requestAnimationFrame(step);
      else this._raf = null;
    };
    this._raf = requestAnimationFrame(step);
  }

  _step() {
    const n = this.nodes;
    if (!n.length) return false;

    const cx = (this.view.x - this.view.x); // gravity target is view center in world coords
    const gx = 0, gy = 0;

    // pair repulsion
    for (let i = 0; i < n.length; i++) {
      const a = n[i];
      for (let j = i + 1; j < n.length; j++) {
        const b = n[j];
        let dx = a.x - b.x;
        let dy = a.y - b.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 0.01) { dx = (Math.random() - 0.5); dy = (Math.random() - 0.5); d2 = dx * dx + dy * dy; }
        const d = Math.sqrt(d2);
        const f = (9000 / (d * d + 60)) * (d < 220 ? 1 : 0.25);
        const fx = (dx / d) * f;
        const fy = (dy / d) * f;
        a.vx += fx; a.vy += fy;
        b.vx -= fx; b.vy -= fy;
      }
    }

    // springs
    for (const e of this.edges) {
      const a = this.byId.get(e.source);
      const b = this.byId.get(e.target);
      if (!a || !b) continue;
      let dx = b.x - a.x;
      let dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const ideal = 96 + Math.min(a.r + b.r, 60);
      const f = (d - ideal) * 0.055;
      const fx = (dx / d) * f;
      const fy = (dy / d) * f;
      a.vx += fx; a.vy += fy;
      b.vx -= fx; b.vy -= fy;
    }

    // gravity to origin + damping + integrate
    let maxSpeed = 0;
    for (const a of n) {
      a.vx += (-a.x) * 0.012;
      a.vy += (-a.y) * 0.012;
      a.vx *= 0.86;
      a.vy *= 0.86;
      const sp = Math.hypot(a.vx, a.vy);
      if (sp > 14) { a.vx *= 14 / sp; a.vy *= 14 / sp; }
      if (!this.pinned.has(a.id)) {
        a.x += a.vx;
        a.y += a.vy;
      }
      maxSpeed = Math.max(maxSpeed, sp);
    }

    this._warm--;
    const settled = maxSpeed < 0.06 && this._warm <= 0;
    this.draw();
    return !settled;
  }

  /* ── layout helpers ────────────────────────────────────── */
  _seedPositions() {
    const n = this.nodes.length;
    this.nodes.forEach((node, i) => {
      if (node.label === "Entity") {
        const ang = (i / Math.max(n, 1)) * Math.PI * 2;
        const rad = 130 + Math.sqrt(i) * 46;
        node.x = Math.cos(ang) * rad;
        node.y = Math.sin(ang) * rad;
      } else {
        const ang = Math.random() * Math.PI * 2;
        node.x = Math.cos(ang) * 160;
        node.y = Math.sin(ang) * 160;
      }
      node.vx = 0;
      node.vy = 0;
    });
  }

  /* ── viewport ──────────────────────────────────────────── */
  resize() {
    const parent = this.canvas.parentElement;
    const w = parent.clientWidth;
    const h = parent.clientHeight;
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.max(1, w * dpr);
    this.canvas.height = Math.max(1, h * dpr);
    this.canvas.style.width = `${w}px`;
    this.canvas.style.height = `${h}px`;
    this.dpr = dpr;
    this.w = w;
    this.h = h;
    this.draw();
  }

  fit(animate = false) {
    if (!this.nodes.length) {
      this.view = { x: 0, y: 0, scale: 1 };
      this.draw();
      return;
    }
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const n of this.nodes) {
      minX = Math.min(minX, n.x); maxX = Math.max(maxX, n.x);
      minY = Math.min(minY, n.y); maxY = Math.max(maxY, n.y);
    }
    const bw = Math.max(1, maxX - minX);
    const bh = Math.max(1, maxY - minY);
    const scale = Math.min(this.w / (bw + 160), this.h / (bh + 160), 1.6);
    this.view = {
      scale,
      x: -((minX + maxX) / 2) * scale + this.w / 2,
      y: -((minY + maxY) / 2) * scale + this.h / 2,
    };
    this.draw();
  }

  focus(id) {
    const n = this.byId.get(id);
    if (!n) return;
    this.selected = id;
    this.view = {
      x: -n.x * this.view.scale + this.w / 2,
      y: -n.y * this.view.scale + this.h / 2,
      scale: Math.max(this.view.scale, 1.5),
    };
    this.draw();
  }

  /* ── rendering ─────────────────────────────────────────── */
  draw() {
    const ctx = this.ctx;
    const { w, h, dpr } = this;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    if (!this.nodes.length) return;

    const { view } = this;
    const T = (p) => [p.x * view.scale + view.x, p.y * view.scale + view.y];

    const highlight = this.hover || this.selected;
    const neighbors = new Set();
    if (highlight) {
      neighbors.add(highlight);
      for (const e of this.edges) {
        if (e.source === highlight) neighbors.add(e.target);
        if (e.target === highlight) neighbors.add(e.source);
      }
    }

    ctx.save();
    ctx.translate(view.x, view.y);
    ctx.scale(view.scale, view.scale);

    const journeyKey = (e) => `${e.source}|${e.target}`;
    // edges
    for (const e of this.edges) {
      const a = this.byId.get(e.source);
      const b = this.byId.get(e.target);
      if (!a || !b) continue;
      const onJourney = this.journey && this.journey.has(e.source) && this.journey.has(e.target);
      const active = (highlight && (e.source === highlight || e.target === highlight)) || onJourney;
      const dim = highlight && !active;
      ctx.globalAlpha = dim ? 0.12 : 0.28 + Math.min(e.weight || 1, 4) * 0.14;
      if (onJourney) {
        ctx.globalAlpha = 1;
        ctx.strokeStyle = this._theme.accent;
        ctx.lineWidth = 2.6;
        ctx.setLineDash([5, 3]);
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.globalAlpha = 0.22;
        ctx.lineWidth = 6;
        ctx.stroke();
        ctx.globalAlpha = 1;
        continue;
      }
      ctx.strokeStyle = e.kind === "RELATED" ? this._theme.faint : this._theme.faintest;
      ctx.lineWidth = e.kind === "RELATED" ? 1 + Math.min(e.weight || 1, 4) * 0.35 : 0.8;
      if (e.cross) ctx.setLineDash([3, 4]);
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
      ctx.setLineDash([]);
      if (active && this.hoverEdge && this.hoverEdge.source === e.source && this.hoverEdge.target === e.target) {
        ctx.globalAlpha = 1;
        ctx.strokeStyle = this._theme.accent;
        ctx.stroke();
      }
    }
    ctx.globalAlpha = 1;

    // nodes
    for (const n of this.nodes) {
      const active = highlight && (n.id === highlight || neighbors.has(n.id));
      const dim = highlight && !active;
      const isHighlight = n.id === highlight;
      ctx.globalAlpha = dim ? 0.25 : 1;
      this._drawNode(n, isHighlight);
    }
    ctx.globalAlpha = 1;
    ctx.restore();
  }

  _drawNode(n, isHighlight) {
    const ctx = this.ctx;
    const t = this._theme;
    let color = n.color || t.entity;
    if (!n.color) {
      if (n.label === "Document") color = t.doc;
      else if (n.label === "Image") color = t.image;
    }

    const onJourney = this.journey && this.journey.has(n.id);
    const growing = n.fresh;
    const r = growing ? n.r * 0.35 : n.r;

    ctx.save();
    if (isHighlight || n.id === this.selected || onJourney) {
      ctx.shadowColor = onJourney ? t.accent : t.glow;
      ctx.shadowBlur = onJourney ? 22 : 18;
    }
    ctx.fillStyle = color;
    ctx.strokeStyle = onJourney ? t.accent : t.ring;
    ctx.lineWidth = onJourney ? 2.4 : 1.2;

    if (n.label === "Entity") {
      ctx.beginPath();
      ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
      // bridge: multi-vault entity gets an inner dot marker
      if (n.bridge) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, 2, 0, Math.PI * 2);
        ctx.fillStyle = t.ring;
        ctx.fill();
      }
      if (isHighlight || n.id === this.selected || onJourney) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + 4, 0, Math.PI * 2);
        ctx.strokeStyle = color;
        ctx.globalAlpha *= 0.55;
        ctx.stroke();
      }
    } else if (n.label === "Document") {
      const w = 24, hgt = 28;
      const x = n.x - w / 2, y = n.y - hgt / 2;
      ctx.beginPath();
      ctx.roundRect(x, y, w, hgt, 4);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = t.ring;
      ctx.globalAlpha *= 0.85;
      ctx.beginPath();
      ctx.roundRect(x + 5, y + 5, w - 10, 2, 1);
      ctx.fill();
      ctx.beginPath();
      ctx.roundRect(x + 5, y + 10, w - 10, 2, 1);
      ctx.fill();
      ctx.beginPath();
      ctx.roundRect(x + 5, y + 15, w - 14, 2, 1);
      ctx.fill();
    } else {
      // Image — diamond
      ctx.translate(n.x, n.y);
      ctx.rotate(Math.PI / 4);
      const s = 13;
      ctx.beginPath();
      ctx.roundRect(-s / 2, -s / 2, s, s, 3);
      ctx.fill();
      ctx.stroke();
    }
    ctx.restore();

    // label
    const label = n.label === "Entity" ? n.name : n.name.length > 22 ? n.name.slice(0, 21) + "…" : n.name;
    ctx.save();
    ctx.font = n.label === "Entity" ? `400 10.5px ${FONT_UI}` : `400 9.5px ${FONT_MONO}`;
    ctx.fillStyle = n.label === "Entity" ? t.soft : t.faint;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.fillText(label, n.x, n.y + r + 5);
    ctx.restore();

    n.fresh = false;
  }

  /* ── hit testing & interaction ─────────────────────────── */
  _toWorld(px, py) {
    const { view } = this;
    return {
      x: (px - view.x) / view.scale,
      y: (py - view.y) / view.scale,
    };
  }

  _hitNode(px, py) {
    const p = this._toWorld(px, py);
    let best = null;
    let bestD = Infinity;
    for (const n of this.nodes) {
      const d = Math.hypot(n.x - p.x, n.y - p.y);
      if (d <= n.r + 8 && d < bestD) { best = n; bestD = d; }
    }
    return best;
  }

  _hitEdge(px, py) {
    const p = this._toWorld(px, py);
    let best = null;
    let bestD = Infinity;
    for (const e of this.edges) {
      const a = this.byId.get(e.source);
      const b = this.byId.get(e.target);
      if (!a || !b) continue;
      const mx = (a.x + b.x) / 2;
      const my = (a.y + b.y) / 2;
      const d = Math.hypot(p.x - mx, p.y - my);
      if (d < 22 && d < bestD) { best = e; bestD = d; }
    }
    return best;
  }

  _bind() {
    const c = this.canvas;

    c.addEventListener("pointerdown", (ev) => {
      c.setPointerCapture(ev.pointerId);
      this._downAt = { x: ev.clientX, y: ev.clientY };
      this._moved = false;
      const node = this._hitNode(ev.offsetX, ev.offsetY);
      if (node) {
        this._drag = { id: node.id, dx: node.x - this._toWorld(ev.offsetX, ev.offsetY).x, dy: node.y - this._toWorld(ev.offsetX, ev.offsetY).y };
        this.pinned.add(node.id);
        this.selected = node.id;
      } else {
        this._pan = { sx: ev.offsetX, sy: ev.offsetY, vx: this.view.x, vy: this.view.y };
      }
      this.start();
    });

    c.addEventListener("pointermove", (ev) => {
      if (this._downAt) {
        const dx = Math.abs(ev.clientX - this._downAt.x);
        const dy = Math.abs(ev.clientY - this._downAt.y);
        if (dx + dy > 4) this._moved = true;
      }
      if (this._drag) {
        const p = this._toWorld(ev.offsetX, ev.offsetY);
        const n = this.byId.get(this._drag.id);
        if (n) { n.x = p.x + this._drag.dx; n.y = p.y + this._drag.dy; }
        this.start();
      } else if (this._pan) {
        this.view.x = this._pan.vx + (ev.offsetX - this._pan.sx);
        this.view.y = this._pan.vy + (ev.offsetY - this._pan.sy);
        this.draw();
      } else {
        const node = this._hitNode(ev.offsetX, ev.offsetY);
        const edge = this._hitEdge(ev.offsetX, ev.offsetY);
        const nextHover = node ? node.id : null;
        const nextEdge = node ? null : edge;
        if (nextHover !== this.hover || (nextEdge !== this.hoverEdge)) {
          this.hover = nextHover;
          this.hoverEdge = nextEdge;
          this.canvas.style.cursor = node ? "pointer" : "grab";
          this.draw();
        }
      }
    });

    const end = (ev) => {
      this._drag = null;
      this._pan = null;
      this._downAt = null;
      try { c.releasePointerCapture(ev.pointerId); } catch { /* ignore */ }
    };
    c.addEventListener("pointerup", end);
    c.addEventListener("pointercancel", end);

    c.addEventListener("click", (ev) => {
      if (this._moved) return;
      const node = this._hitNode(ev.offsetX, ev.offsetY);
      if (node) {
        this.selected = node.id;
        if (this.onSelect) this.onSelect(node);
        if (node.label === "Entity" && this.onExpand) {
          this.onExpand(node).then((added) => {
            if (added && added.length) this.focus(node.id);
          });
        }
        this.start();
      }
    });

    c.addEventListener("dblclick", (ev) => {
      const node = this._hitNode(ev.offsetX, ev.offsetY);
      if (node) this.focus(node.id);
    });

    c.addEventListener("wheel", (ev) => {
      ev.preventDefault();
      const factor = ev.deltaY < 0 ? 1.12 : 1 / 1.12;
      const prevScale = this.view.scale;
      const newScale = Math.min(3.5, Math.max(0.25, prevScale * factor));
      const wx = (ev.offsetX - this.view.x) / prevScale;
      const wy = (ev.offsetY - this.view.y) / prevScale;
      this.view.scale = newScale;
      this.view.x = ev.offsetX - wx * newScale;
      this.view.y = ev.offsetY - wy * newScale;
      this.draw();
    }, { passive: false });
  }
}
