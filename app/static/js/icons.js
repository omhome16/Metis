/* Stroke-based inline SVG icons — currentColor, no emoji. */

const P = {
  mark: '<path d="M12 3v18"/><path d="M3 12h18"/><path d="M5.6 5.6l12.8 12.8"/><path d="M18.4 5.6L5.6 18.4"/>',
  vault: '<path d="M12 3l8.5 4.5L12 12 3.5 7.5z"/><path d="M3.5 12.5l8.5 4.5 8.5-4.5"/><path d="M3.5 17.5l8.5 4.5 8.5-4.5"/>',
  doc: '<path d="M7 3h7l4 4v14H7z"/><path d="M14 3v4h4"/>',
  pdf: '<path d="M7 3h7l4 4v14H7z"/><path d="M14 3v4h4"/><path d="M10 13.2h4.4"/><path d="M10 16h3"/>',
  txt: '<path d="M7 3h7l4 4v14H7z"/><path d="M14 3v4h4"/><path d="M10 12h4.6"/><path d="M10 15.2h4.6"/>',
  image: '<rect x="4" y="5" width="16" height="14" rx="2"/><circle cx="9.2" cy="10" r="1.5"/><path d="M4.5 17.2l4.6-4.6 3.4 3.4 2.7-2.7 4.3 4.3"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5 5l1.4 1.4M17.6 17.6L19 19M19 5l-1.4 1.4M6.4 17.6L5 19"/>',
  moon: '<path d="M20 14.2A8 8 0 1 1 9.8 4a6.4 6.4 0 0 0 10.2 10.2z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3M6.5 7l1 13h9l1-13"/><path d="M10 11v5M14 11v5"/>',
  send: '<path d="M4 12L20 4l-5 16-3.5-7z"/><path d="M11.5 13L20 4"/>',
  stop: '<rect x="7" y="7" width="10" height="10" rx="1.5"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>',
  check: '<path d="M4.5 12.5l5 5 10-11"/>',
  alert: '<path d="M12 3.5L22 20H2z"/><path d="M12 10v4.5"/><circle cx="12" cy="17.2" r="0.4"/>',
  "chev-r": '<path d="M9.5 6l6 6-6 6"/>',
  "chev-d": '<path d="M6 9.5l6 6 6-6"/>',
  upload: '<path d="M12 16V4.5M7 9l5-5 5 5"/><path d="M4 20h16"/>',
  network: '<circle cx="5.5" cy="6.5" r="2.5"/><circle cx="18.5" cy="8" r="2.5"/><circle cx="12" cy="18.5" r="2.5"/><path d="M7.7 7.7l8.3.4M7 8.6l3.7 7.9M17 10.2l-3.3 6.3"/>',
  layers: '<path d="M12 4l8 4.5L12 13 4 8.5z"/><path d="M4 13.5l8 4.5 8-4.5"/><path d="M4 18.5l8 4.5 8-4.5"/>',
  edit: '<path d="M4 20h4l11-11-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
  external: '<path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M18 13v6a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h6"/>',
  refresh: '<path d="M20 12a8 8 0 1 1-2.5-5.8"/><path d="M20 4v4h-4"/>',
  chat: '<path d="M4 5.5h16v10H9l-5 4z"/>',
  file: '<path d="M7 3h7l4 4v14H7z"/><path d="M14 3v4h4"/><path d="M10 12h4.5"/><path d="M10 15h4.5"/>',
  grid: '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
  zoomIn: '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/><path d="M11 8v6M8 11h6"/>',
  zoomOut: '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/><path d="M8 11h6"/>',
  expand: '<path d="M9 4H4v5M15 4h5v5M9 20H4v-5M15 20h5v-5"/>',
  compress: '<path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5"/>',
  dot: '<circle cx="12" cy="12" r="4"/>',
  sparkle: '<path d="M12 3.5l1.9 5.1L19 10.5l-5.1 1.9L12 17.5l-1.9-5.1L5 10.5l5.1-1.9z"/><path d="M18.5 15.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z"/>',
  route: '<circle cx="6" cy="19" r="2"/><circle cx="18" cy="5" r="2"/><path d="M8 19h6a4 4 0 0 0 0-8H9a4 4 0 0 1 0-8h7"/>',
  thumbup: '<path d="M7 11v9H4v-9z"/><path d="M7 11l4-7a2 2 0 0 1 2 2v3h5.5a2 2 0 0 1 2 2.4l-1.2 6a2 2 0 0 1-2 1.6H7"/>',
  thumbdown: '<path d="M17 13V4h3v9z"/><path d="M17 13l-4 7a2 2 0 0 1-2-2v-3H5.5a2 2 0 0 1-2-2.4l1.2-6a2 2 0 0 1 2-1.6H17"/>',
};

export function icon(name, size = 16, cls = "") {
  const body = P[name] || P.mark;
  return `<svg class="${cls}" viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
}

export function iconEl(name, size = 16, cls = "") {
  const wrap = document.createElement("span");
  wrap.innerHTML = icon(name, size, cls);
  return wrap.firstChild;
}
