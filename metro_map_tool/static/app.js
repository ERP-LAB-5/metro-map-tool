/* SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) 2026 D-LAB-5
 *
 * Metro map designer.
 *
 * The browser owns the spec and the interaction; the server owns the geometry.
 * Every change funnels through applyChange() -> scheduleRender(), which POSTs
 * the spec to /api/render and swaps the returned SVG into the canvas. Station
 * hit-testing rides on the data-station / data-cell attributes that render()
 * writes, so the client never re-implements the layout maths.
 */

"use strict";

/* ------------------------------------------------------------- helpers -- */

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const clone = (o) => JSON.parse(JSON.stringify(o));

async function api(method, path, payload) {
  const opts = { method, headers: {} };
  if (payload !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(payload);
  }
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch (_) { /* empty body */ }
  if (!res.ok) {
    throw { status: res.status, data,
            errors: (data && data.errors) || [`${method} ${path} failed (${res.status})`] };
  }
  return data;
}

/* --------------------------------------------------------------- state -- */

const S = {
  name: null,                              // saved map name, null while untitled
  folder: null,                            // which folder it came from, null while untitled
  spec: { stations: {}, junctions: {}, lines: [], zones: [], scenarios: [], interchanges: [] },
  style: { cell: 120, stroke: 10, corner: 22, bundle_gap: 13, label_size: 16 },
  autoIx: true,
  theme: "auto",                           // designer chrome and preview only
  sideHidden: false,
  ridesPlaying: true,                      // the travellers run on their own
  snap: 1,                                 // grid step when dragging or nudging

  sel: { kind: null, id: null },           // "station" | "junction" | "line" | ...
  branch: null,                            // index into the open line's branches,
                                           // or null while its trunk is being edited
  dirty: false,
  version: null,                           // content hash of the map as loaded
  ignoreVersion: null,                     // an external change we chose to keep out
  undo: [],
  redo: [],
  zoom: 1,
  pan: { x: 0, y: 0 },
};

const UNDO_MAX = 50;
let DEFAULT_STYLE = { ...S.style };
let LABEL_SIDES = [];
let LINE_STATUSES = [{ value: "live", label: "In service" }];
let LABEL_ANGLES = [0, 45, 90];
let PALETTE = [];
let MODES = [{ value: "metro", label: "Metro map" }, { value: "roadmap", label: "Roadmap" }];
let INTERVALS = ["day", "week", "month", "quarter", "year"];
let FOLDERS = [{ value: "mymaps", label: "My maps" }, { value: "shared", label: "Shared" }];
let DEFAULT_FOLDER = "mymaps";
let LEGEND_POSITIONS = ["hide", "top", "left", "bottom", "right"];
let CONTINUES = [{ value: "none", label: "no" }, { value: "start", label: "at the start" },
                 { value: "end", label: "at the end" }, { value: "both", label: "at both ends" }];
let DEAD_ENDS = [{ value: "none", label: "none" },
                 { value: "buffer", label: "end of the line" },
                 { value: "fire", label: "burning platform" }];
let DEFAULT_LEGEND = "bottom";
let lastSVG = "";
let TIMELINE = null;                         // ruler the server resolved, or null

const GUIDE_MAP = "how-this-tool-works";     // the map that explains the tool
const LAST_MAP_KEY = "metro-map:last";
const THEME_KEY = "metro-map:theme";
const SIDE_KEY = "metro-map:side";

/* --------------------------------------------------------------- theme -- */

/* A per-browser preference, not part of any map: it themes the designer and
   its preview, never an exported file. An export has to keep both palettes,
   because it may be opened anywhere. */

function storedTheme() {
  try { return localStorage.getItem(THEME_KEY) || "auto"; } catch (_) { return "auto"; }
}

function applyTheme(theme) {
  S.theme = ["auto", "light", "dark"].includes(theme) ? theme : "auto";
  const root = document.documentElement;
  if (S.theme === "auto") delete root.dataset.theme;
  else root.dataset.theme = S.theme;
  try { localStorage.setItem(THEME_KEY, S.theme); } catch (_) { /* no storage */ }
  const select = $("#theme-select");
  if (select && select.value !== S.theme) select.value = S.theme;
}

/* ---------------------------------------------------------- side panel -- */

/** Fold the panel away for a wider canvas. Remembered per browser. */
function applySide(hidden) {
  S.sideHidden = !!hidden;
  document.body.classList.toggle("side-hidden", S.sideHidden);
  try { localStorage.setItem(SIDE_KEY, S.sideHidden ? "1" : "0"); } catch (_) { /* none */ }
  const btn = $("#btn-side");
  if (btn) btn.setAttribute("aria-expanded", String(!S.sideHidden));
}

function storedSide() {
  try { return localStorage.getItem(SIDE_KEY) === "1"; } catch (_) { return false; }
}

/** What the preview should actually be drawn as, resolving "auto" now. */
function resolvedTheme() {
  if (S.theme !== "auto") return S.theme;
  return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark" : "light";
}

/** Remember the map this browser had open. Storage can be blocked; never throw. */
function rememberMap(name, folder) {
  try { localStorage.setItem(LAST_MAP_KEY, `${folder || DEFAULT_FOLDER}/${name}`); }
  catch (_) { /* no storage */ }
}

function rememberedMap() {
  try {
    const raw = localStorage.getItem(LAST_MAP_KEY);
    if (!raw) return null;
    const cut = raw.indexOf("/");
    // pre-v2 entries are a bare name; let the folder search find them
    return cut < 0 ? { name: raw, folder: null }
                   : { folder: raw.slice(0, cut), name: raw.slice(cut + 1) };
  } catch (_) { return null; }
}

/* ------------------------------------------------------------ roadmap -- */

function specMode() { return S.spec.mode || "metro"; }
function legendAt() { return S.spec.legend || DEFAULT_LEGEND; }
function isRoadmap() { return specMode() === "roadmap"; }

/** A sensible first timeline: this year and the next, by quarter. */
function defaultTimeline() {
  const year = new Date().getFullYear();
  return { start: `${year}-01-01`, end: `${year + 2}-01-01`, interval: "quarter" };
}

function setMode(value) {
  applyChange(() => {
    if (value === "metro") {
      delete S.spec.mode;                  // metro is the default, kept implicit
    } else {
      S.spec.mode = value;
      // keep whatever dates were there before, so flipping back and forth is free
      if (!S.spec.timeline) S.spec.timeline = defaultTimeline();
    }
  });
  syncMode();
}

/** Show the Timeline tab only where it means something. */
function syncMode() {
  const select = $("#mode-select");
  if (select) select.value = specMode();
  const tab = document.querySelector('.tab[data-tab="timeline"]');
  if (!tab) return;
  tab.hidden = !isRoadmap();
  if (tab.hidden && tab.classList.contains("is-on")) {
    document.querySelector('.tab[data-tab="stations"]').click();
  }
}

/** The timeline column a grid position falls in, or null off a roadmap. */
function columnAt(gx) {
  const cols = (TIMELINE && TIMELINE.columns_at) || [];
  const k = Math.floor(gx);
  return k >= 0 && k < cols.length ? cols[k] : null;
}

function snapshot() {
  return { spec: clone(S.spec), style: { ...S.style }, sel: { ...S.sel } };
}

/** Round a grid coordinate onto the current snap step. */
function snapTo(v) {
  const step = S.snap || 1;
  return Math.round(Math.round(v / step) * step * 1000) / 1000;
}

/** Every spec the editor touches carries the keys the panels expect. */
function normalise(spec) {
  spec.stations = spec.stations || {};
  spec.junctions = spec.junctions || {};
  spec.lines = spec.lines || [];
  spec.zones = spec.zones || [];
  spec.scenarios = spec.scenarios || [];
  spec.interchanges = spec.interchanges || [];
  spec.phases = spec.phases || [];
  // a hand-written or agent-written roadmap may arrive with no dates at all —
  // a GET does not validate, only a PUT does. Repair it on the way in, where
  // the map is being replaced wholesale anyway, not mid-render.
  if (spec.mode === "roadmap" && !spec.timeline) spec.timeline = defaultTimeline();
  return spec;
}

function restore(snap) {
  S.spec = clone(snap.spec);
  S.style = { ...snap.style };
  S.sel = { ...snap.sel };
}

function pushUndo() {
  S.undo.push(snapshot());
  if (S.undo.length > UNDO_MAX) S.undo.shift();
  S.redo.length = 0;
  syncToolbar();
}

/** The one door every mutation goes through. */
function applyChange(fn, { undo = true } = {}) {
  if (undo) pushUndo();
  fn();
  markDirty();
  refreshPanels();
  scheduleRender();
}

function markDirty() {
  S.dirty = true;
  $("#dirty").hidden = false;
}

function markClean() {
  S.dirty = false;
  $("#dirty").hidden = true;
}

function syncToolbar() {
  $("#btn-undo").disabled = S.undo.length === 0;
  $("#btn-redo").disabled = S.redo.length === 0;
  $("#map-name").textContent = S.name || "untitled";
  $("#btn-save").disabled = false;
}

/* -------------------------------------------------------------- render -- */

let renderPending = false;
let renderRunning = false;

function scheduleRender() {
  renderPending = true;
  if (renderRunning) return;
  renderRunning = true;
  (async () => {
    while (renderPending) {
      renderPending = false;
      await doRender();
    }
    renderRunning = false;
  })();
}

async function doRender() {
  const canvas = $("#canvas");
  const nStations = Object.keys(S.spec.stations).length;
  $("#empty").classList.toggle("is-on", nStations === 0);
  // An empty metro map needs no round trip. An empty roadmap does: its dates
  // are real before its first station is, and the Timeline panel reads them
  // back off the render answer.
  if (nStations === 0 && !isRoadmap()) {
    canvas.innerHTML = "";
    lastSVG = "";
    showProblems([]);
    return;
  }
  let data;
  try {
    data = await api("POST", "/api/render", {
      spec: S.spec, style: S.style, auto_interchange: S.autoIx,
      theme: resolvedTheme(),
    });
  } catch (err) {
    showProblems(err.errors);
    TIMELINE = null;
    renderTimeline();
    return;
  }
  showProblems([], data.warnings);
  TIMELINE = data.timeline || null;
  renderTimeline();
  if (data.empty) { canvas.innerHTML = ""; return; }

  // The server may have re-flagged interchanges; mirror that back locally so
  // the station list and the canvas agree.
  if (data.stations) {
    let changed = false;
    for (const [sid, st] of Object.entries(data.stations)) {
      const mine = S.spec.stations[sid];
      if (!mine) continue;
      const want = !!st.interchange;
      if (!!mine.interchange !== want) {
        changed = true;
        if (want) mine.interchange = true; else delete mine.interchange;
      }
    }
    if (changed) refreshPanels();
  }

  lastSVG = data.svg;
  canvas.innerHTML = data.svg;
  applyTransform();
  decorate();
  applyRideState();
}

function svgEl() { return $("#canvas svg"); }

function showProblems(errors, warnings) {
  const box = $("#problems");
  const bad = errors || [];
  const soft = warnings || [];
  if (!bad.length && !soft.length) { box.hidden = true; box.innerHTML = ""; return; }
  const block = (items, word) => items.length
    ? `<strong>${items.length} ${word}${items.length > 1 ? "s" : ""}:</strong>
       <ul>${items.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>` : "";
  box.hidden = false;
  box.classList.toggle("soft", !bad.length);
  box.innerHTML = block(bad, "problem") + block(soft, "note");
}

/** Selection halo, line highlight and drag ghost — redrawn after every render. */
function decorate() {
  const svg = svgEl();
  if (!svg) return;

  svg.querySelectorAll(".sel-halo, .ghost").forEach((n) => n.remove());
  svg.querySelectorAll(".junction.is-sel").forEach((n) => n.classList.remove("is-sel"));
  svg.querySelectorAll("#routes .route").forEach((p) => (p.style.opacity = ""));
  svg.querySelectorAll("#zones .zone").forEach((g) => (g.style.opacity = ""));

  if (S.sel.kind === "station") {
    const marker = svg.querySelector(`circle[data-station="${cssEsc(S.sel.id)}"]`);
    if (marker) {
      const halo = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      halo.setAttribute("class", "sel-halo");
      halo.setAttribute("cx", marker.getAttribute("cx"));
      halo.setAttribute("cy", marker.getAttribute("cy"));
      halo.setAttribute("r", Number(marker.getAttribute("r")) + 7);
      svg.appendChild(halo);
    }
  } else if (S.sel.kind === "junction") {
    const handle = svg.querySelector(`.junction[data-junction="${cssEsc(S.sel.id)}"]`);
    if (handle) {
      handle.classList.add("is-sel");
      const halo = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      halo.setAttribute("class", "sel-halo");
      halo.setAttribute("cx", handle.getAttribute("cx"));
      halo.setAttribute("cy", handle.getAttribute("cy"));
      halo.setAttribute("r", Number(handle.getAttribute("r")) + 7);
      svg.appendChild(halo);
    }
  } else if (S.sel.kind === "line") {
    // a line draws one path per strand now, so the path's index is no longer
    // its line's — the l{i} class is what actually says which line it is
    svg.querySelectorAll("#routes .route").forEach((p) => {
      if (!p.classList.contains(`l${S.sel.id}`)) p.style.opacity = "0.22";
    });
  } else if (S.sel.kind === "zone") {
    const drawn = drawnZoneIndexes();
    svg.querySelectorAll("#zones .zone").forEach((g, k) => {
      g.style.opacity = drawn[k] === S.sel.id ? "1" : "0.3";
    });
  }

  if (drag && drag.moved) drawGhost(drag.gx, drag.gy);
}

function cssEsc(s) {
  return window.CSS && CSS.escape ? CSS.escape(s) : String(s).replace(/["\\]/g, "\\$&");
}

function drawGhost(gx, gy) {
  const svg = svgEl();
  if (!svg) return;
  svg.querySelectorAll(".ghost").forEach((n) => n.remove());
  const cell = Number(svg.dataset.cell) || S.style.cell;
  const g = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  g.setAttribute("class", "ghost");
  g.setAttribute("cx", gx * cell);
  g.setAttribute("cy", gy * cell);
  g.setAttribute("r", 18);
  svg.appendChild(g);
}

/* ------------------------------------------------------- canvas: view -- */

function applyTransform() {
  const svg = svgEl();
  if (!svg) return;
  svg.style.transform = `translate(${S.pan.x}px, ${S.pan.y}px) scale(${S.zoom})`;
  $("#zoom-level").textContent = `${Math.round(S.zoom * 100)}%`;
}

function fitToView() {
  const svg = svgEl();
  const box = $("#canvas").getBoundingClientRect();
  if (!svg) return;
  const w = Number(svg.getAttribute("width"));
  const h = Number(svg.getAttribute("height"));
  if (!w || !h) return;
  S.zoom = Math.min((box.width - 40) / w, (box.height - 40) / h, 2);
  S.pan.x = (box.width - w * S.zoom) / 2;
  S.pan.y = (box.height - h * S.zoom) / 2;
  applyTransform();
}

function zoomBy(factor, anchor) {
  const box = $("#canvas").getBoundingClientRect();
  const ax = anchor ? anchor.x - box.left : box.width / 2;
  const ay = anchor ? anchor.y - box.top : box.height / 2;
  const next = Math.min(4, Math.max(0.1, S.zoom * factor));
  const k = next / S.zoom;
  S.pan.x = ax - (ax - S.pan.x) * k;
  S.pan.y = ay - (ay - S.pan.y) * k;
  S.zoom = next;
  applyTransform();
}

/** Pointer position in grid cells, via the SVG's own screen matrix. */
function gridAt(ev) {
  const svg = svgEl();
  if (!svg || !svg.getScreenCTM) return null;
  const ctm = svg.getScreenCTM();
  if (!ctm) return null;
  const pt = new DOMPoint(ev.clientX, ev.clientY).matrixTransform(ctm.inverse());
  const cell = Number(svg.dataset.cell) || S.style.cell;
  return { gx: pt.x / cell, gy: pt.y / cell };
}

/* ---------------------------------------------------- canvas: pointers -- */

let drag = null;
let pan = null;

function initCanvas() {
  const canvas = $("#canvas");

  canvas.addEventListener("pointerdown", (ev) => {
    if (ev.button !== 0) return;
    const hit = ev.target.closest("[data-station], [data-junction]");
    const hitId = hit && (hit.dataset.station || hit.dataset.junction);
    if (hit && waypoint(hitId)) {
      const id = hitId;
      const g = gridAt(ev);
      const st = waypoint(id);
      if (!g) return;
      drag = { id, offGX: st.gx - g.gx, offGY: st.gy - g.gy, gx: st.gx, gy: st.gy, moved: false };
      pushUndo();                       // dropped again on pointerup if nothing moved
    } else {
      pan = { x0: ev.clientX, y0: ev.clientY, px: S.pan.x, py: S.pan.y };
      canvas.classList.add("panning");
    }
    try { canvas.setPointerCapture(ev.pointerId); } catch (_) { /* stale pointer */ }
  });

  canvas.addEventListener("pointermove", (ev) => {
    if (drag) {
      const g = gridAt(ev);
      if (!g) return;
      const gx = snapTo(g.gx + drag.offGX);
      const gy = snapTo(g.gy + drag.offGY);
      drag.gx = gx; drag.gy = gy;
      drawGhost(gx, gy);
      const st = waypoint(drag.id);
      if (st.gx !== gx || st.gy !== gy) {
        st.gx = gx; st.gy = gy;
        drag.moved = true;
        markDirty();
        refreshPanels();
        scheduleRender();
      }
    } else if (pan) {
      S.pan.x = pan.px + (ev.clientX - pan.x0);
      S.pan.y = pan.py + (ev.clientY - pan.y0);
      applyTransform();
    }
  });

  const end = (ev) => {
    const canvas = $("#canvas");
    try {
      if (canvas.hasPointerCapture(ev.pointerId)) canvas.releasePointerCapture(ev.pointerId);
    } catch (_) { /* never captured */ }
    canvas.classList.remove("panning");
    if (drag) {
      const { id, moved } = drag;
      drag = null;
      if (moved) {
        decorate();
      } else {
        S.undo.pop();                   // it was a click, not a move
        syncToolbar();
        onStationClick(id);
      }
    }
    pan = null;
  };
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);

  canvas.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    zoomBy(ev.deltaY < 0 ? 1.12 : 1 / 1.12, { x: ev.clientX, y: ev.clientY });
  }, { passive: false });
}

/** A click on a station: append it to the open route, else select it. */
function onStationClick(id) {
  const tab = currentTab();
  const isJunction = !!(S.spec.junctions || {})[id];
  if (tab === "lines" && S.sel.kind === "line" && S.spec.lines[S.sel.id]) {
    applyChange(() => routeBeingEdited(S.spec.lines[S.sel.id]).push(id));
    return;
  }
  // a junction has no platform, so nothing that bands or visits stops can hold
  // one — say so rather than letting the click quietly do nothing
  if (isJunction) {
    if (tab !== "stations") {
      $("#hint").textContent =
        `“${id}” is a junction — only a line's route can run through it`;
      return;
    }
    select("junction", id);
    return;
  }
  if (tab === "zones" && S.sel.kind === "zone" && S.spec.zones[S.sel.id]) {
    // A capsule replaces the markers of every stop it covers and answers to
    // only the first of them, so clicking it can reach just one. For a zone
    // that is never what was meant: the capsule is one thing on the page, and
    // banding it should band all of it.
    toggleZoneMembers(S.sel.id, joinedWith(id));
    return;
  }
  if (tab === "joins" && S.sel.kind === "join" && S.spec.interchanges[S.sel.id]) {
    toggleJoinMember(S.sel.id, id);
    return;
  }
  if (tab === "scenarios" && S.sel.kind === "scenario" && S.spec.scenarios[S.sel.id]) {
    applyChange(() => {
      const sc = S.spec.scenarios[S.sel.id];
      sc.stations = sc.stations || [];
      sc.stations.push(id);
    });
    return;
  }
  select("station", id);
}

let lastRevealed = "";

function select(kind, id) {
  if (kind !== S.sel.kind || id !== S.sel.id) S.branch = null;
  S.sel = { kind, id };
  refreshPanels();
  decorate();
  setHint();
  revealEditor();
}

/** Scroll a freshly opened editor into view — never while it is being typed in. */
function revealEditor() {
  const key = `${S.sel.kind}:${S.sel.id}`;
  if (key === lastRevealed) return;
  lastRevealed = key;
  const host = document.querySelector(".pane.is-on .edit-host");
  if (host) host.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

/* ---------------------------------------------------------------- tabs -- */

function currentTab() { return $(".tab.is-on").dataset.tab; }

function initTabs() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("is-on", t === tab));
      document.querySelectorAll(".pane").forEach((p) =>
        p.classList.toggle("is-on", p.dataset.pane === tab.dataset.tab));
      setHint();
      decorate();
    });
  });
}

function setHint() {
  const tab = currentTab();
  let text = "";
  if (tab === "stations") {
    text = "Drag a station on the canvas to move it · arrows nudge · Delete removes";
  } else if (tab === "lines" && S.sel.kind === "line" && S.spec.lines[S.sel.id]) {
    const ln = S.spec.lines[S.sel.id];
    const br = (ln.branches || [])[S.branch];
    text = br
      ? `Adding to branch “${br.name || `${ln.name} branch ${S.branch + 1}`}” — click stops and junctions on the canvas in order`
      : `Adding to “${ln.name}” — click stations on the canvas in order`;
  } else if (tab === "lines") {
    text = "Pick a line to edit its route";
  } else if (tab === "zones" && S.sel.kind === "zone" && S.spec.zones[S.sel.id]) {
    text = `Filling “${S.spec.zones[S.sel.id].name}” — click stations on the canvas to add or remove them`;
  } else if (tab === "zones") {
    text = "Pick a zone to choose which stations sit in it";
  } else if (tab === "joins" && S.sel.kind === "join" && S.spec.interchanges[S.sel.id]) {
    text = "Filling this join — click the stops the capsule should cover";
  } else if (tab === "joins") {
    text = "Pick a join to choose the stops it covers, or add one";
  } else if (tab === "scenarios" && S.sel.kind === "scenario" && S.spec.scenarios[S.sel.id]) {
    text = `Routing “${S.spec.scenarios[S.sel.id].name}” — click stations on the canvas in the order the traveller visits them`;
  } else if (tab === "scenarios") {
    text = travellers().length
      ? "Rides are running — pause or rewind them above"
      : "Add a ride, then click stations on the canvas in order";
  } else if (tab === "timeline") {
    text = TIMELINE
      ? `${TIMELINE.columns} ${TIMELINE.interval} columns — a whole grid x is a period boundary`
      : "Set a start, an end and an interval";
  } else {
    text = "Style is saved with the map";
  }
  $("#hint").textContent = text;
}

/* -------------------------------------------------------------- panels -- */

function refreshPanels() {
  renderStations();
  renderLines();
  renderZones();
  renderJoins();
  renderScenarios();
  renderTimeline();
  renderStyle();
  syncToolbar();
}

function linesUsing(sid) {
  // a branch is the same line, so a stop it alone reaches still counts as on it
  return S.spec.lines.filter((ln) =>
    strandsOf(ln).some((st) => st.ids.includes(sid)));
}

function zonesHolding(sid) {
  return S.spec.zones.filter((zn) => (zn.stations || []).includes(sid));
}

/** Zones are skipped in the SVG when empty, so map DOM order back to spec indexes. */
function drawnZoneIndexes() {
  return S.spec.zones
    .map((zn, i) => ((zn.stations || []).some((sid) => S.spec.stations[sid]) ? i : -1))
    .filter((i) => i >= 0);
}

function renderStations() {
  const list = $("#station-list");
  const ids = Object.keys(S.spec.stations).sort((a, b) => {
    const A = S.spec.stations[a], B = S.spec.stations[b];
    return A.gy - B.gy || A.gx - B.gx || a.localeCompare(b);
  });
  if (!ids.length) {
    list.innerHTML = `<li class="note">No stations yet — press “+ Add”.</li>`;
  } else {
    list.innerHTML = ids.map((sid) => {
      const open = S.sel.kind === "station" && S.sel.id === sid;
      const st = S.spec.stations[sid];
      const used = linesUsing(sid);
      const chips = (used.length
        ? used.map((ln) => `<span class="chip" style="color:${esc(ln.color)}">${esc(ln.name)}</span>`).join("")
        : `<span class="chip unused">unused</span>`)
        + zonesHolding(sid).map((zn) =>
          `<span class="chip zone-chip" style="color:${esc(zn.color)};border-color:${esc(zn.color)}">${esc(zn.name)}</span>`).join("");
      return `<li class="row ${S.sel.kind === "station" && S.sel.id === sid ? "is-on" : ""}" data-sid="${esc(sid)}">
        <span class="dot ${st.interchange ? "ring" : ""}"></span>
        <span class="grow">
          <span class="lbl">${esc(st.label)}</span>
          <span class="id">${esc(sid)}</span> <span class="at">(${st.gx},${st.gy})</span>
          <span class="chips">${chips}</span>
        </span>
        <span class="caret">${open ? "▾" : "▸"}</span>
      </li>` + (open ? `<li class="edit-host"><div id="station-editor" class="editor"></div></li>` : "");
    }).join("");
    list.querySelectorAll("[data-sid]").forEach((row) =>
      row.addEventListener("click", () => select("station", row.dataset.sid)));
  }
  renderStationEditor();
  renderJunctions();
}

/** The junction list: id, where it sits, and which lines run through it. */
function renderJunctions() {
  const list = $("#junction-list");
  if (!list) return;
  const table = S.spec.junctions || {};
  const ids = Object.keys(table).sort((a, b) =>
    table[a].gy - table[b].gy || table[a].gx - table[b].gx || a.localeCompare(b));
  if (!ids.length) {
    list.innerHTML = `<li class="note">None. Add one where a line should split
      or rejoin between stops.</li>`;
    return;
  }
  list.innerHTML = ids.map((jid) => {
    const open = S.sel.kind === "junction" && S.sel.id === jid;
    const jn = table[jid];
    const used = linesUsing(jid);
    const chips = used.length
      ? used.map((ln) => `<span class="chip" style="color:${esc(ln.color)}">${esc(ln.name)}</span>`).join("")
      : `<span class="chip unused">on no line</span>`;
    return `<li class="row ${open ? "is-on" : ""}" data-jid="${esc(jid)}">
      <span class="dot junction-dot"></span>
      <span class="grow">
        <span class="lbl">${esc(jid)}</span>
        <span class="at">(${jn.gx},${jn.gy})</span>
        <span class="chips">${chips}</span>
      </span>
      <span class="caret">${open ? "▾" : "▸"}</span>
    </li>` + (open ? `<li class="edit-host"><div id="junction-editor" class="editor"></div></li>` : "");
  }).join("");
  list.querySelectorAll("[data-jid]").forEach((row) =>
    row.addEventListener("click", () => select("junction", row.dataset.jid)));
  renderJunctionEditor();
}

function renderJunctionEditor() {
  const box = $("#junction-editor");
  if (!box) return;
  const jid = S.sel.id;
  const jn = (S.spec.junctions || {})[jid];
  if (!jn) { box.innerHTML = ""; return; }
  const used = linesUsing(jid);
  box.innerHTML = `<div class="card">
    <div class="row-fields">
      <label class="field"><span>Grid x</span>
        <input type="number" id="j-gx" step="${S.snap}" value="${jn.gx}"></label>
      <label class="field"><span>Grid y</span>
        <input type="number" id="j-gy" step="${S.snap}" value="${jn.gy}"></label>
    </div>
    <p class="note">${used.length
      ? `On ${used.map((ln) => esc(ln.name)).join(", ")}.`
      : "On no line yet — add it to a route in the Lines tab, or click it on the canvas while a line is open."}</p>
    <div><button id="j-del" class="danger">Delete junction</button></div>
  </div>`;
  const move = (axis, input) => {
    input.addEventListener("focus", pushUndo);
    input.addEventListener("input", () => {
      const v = Number(input.value);
      if (!Number.isFinite(v)) return;
      jn[axis] = v;
      markDirty();
      scheduleRender();
    });
  };
  move("gx", $("#j-gx"));
  move("gy", $("#j-gy"));
  $("#j-del").addEventListener("click", () => applyChange(() => {
    // a route still listing it would fail validation on the next save
    for (const ln of S.spec.lines) {
      for (const st of strandsOf(ln)) {
        for (let k = st.ids.length - 1; k >= 0; k -= 1) {
          if (st.ids[k] === jid) st.ids.splice(k, 1);
        }
      }
      if (S.spec.lines.includes(ln)) pruneNotes(ln);
    }
    delete S.spec.junctions[jid];
    S.sel = { kind: null, id: null };
  }));
}

function addJunction() {
  const table = (S.spec.junctions = S.spec.junctions || {});
  let n = Object.keys(table).length + 1;
  while (table[`j${n}`]) n += 1;
  const jid = `j${n}`;
  // land it between the two stops of whatever route is open, which is almost
  // always where a junction is wanted
  const ln = S.sel.kind === "line" ? S.spec.lines[S.sel.id] : null;
  const route = ln ? routeBeingEdited(ln).filter((id) => waypoint(id)) : [];
  let gx = 0, gy = 0;
  if (route.length >= 2) {
    const a = waypoint(route[route.length - 2]), b = waypoint(route[route.length - 1]);
    gx = Math.round((a.gx + b.gx) / 2);
    gy = Math.round((a.gy + b.gy) / 2);
  } else {
    const all = Object.values(S.spec.stations);
    if (all.length) {
      gx = Math.round(all.reduce((t, st) => t + st.gx, 0) / all.length);
      gy = Math.round(all.reduce((t, st) => t + st.gy, 0) / all.length);
    }
  }
  applyChange(() => {
    table[jid] = { gx, gy };
    S.sel = { kind: "junction", id: jid };
  });
}

function renderStationEditor() {
  const box = $("#station-editor");
  if (!box) return;                    // nothing selected, so no host in the list
  if (S.sel.kind !== "station" || !S.spec.stations[S.sel.id]) { box.innerHTML = ""; return; }
  const sid = S.sel.id;
  const st = S.spec.stations[sid];
  const sides = ["auto", ...LABEL_SIDES];
  box.innerHTML = `<div class="card">
    <h3>Station</h3>
    <label class="field"><span>Label</span><input type="text" id="f-label" value="${esc(st.label)}"></label>
    <label class="field"><span>Id (used by routes)</span><input type="text" id="f-id" value="${esc(sid)}"></label>
    <div class="pair">
      <label class="field"><span>Grid x</span><input type="number" id="f-gx" step="${S.snap}" value="${st.gx}"></label>
      <label class="field"><span>Grid y</span><input type="number" id="f-gy" step="${S.snap}" value="${st.gy}"></label>
    </div>
    ${isRoadmap() ? `<label class="field"><span>Date — <b id="f-when">${esc(whenLabel(st.gx))}</b></span>
      <input type="date" id="f-date" value="${esc(dateForGX(st.gx))}"
             title="jump this station to the column holding a date"></label>` : ""}
    <div class="pair">
      <label class="field"><span>Label side</span><select id="f-side">
        ${sides.map((s) => `<option value="${esc(s)}" ${(st.label_at || "auto") === s ? "selected" : ""}>${esc(s)}</option>`).join("")}
      </select></label>
      <label class="field"><span>Label angle</span><select id="f-angle">
        ${LABEL_ANGLES.map((a) => `<option value="${a}" ${(st.label_angle || 0) === a ? "selected" : ""}>${a}°</option>`).join("")}
      </select></label>
    </div>
    <label class="field"><span>Dead end</span><select id="f-end">
      ${DEAD_ENDS.map((e) => `<option value="${esc(e.value)}" ${(st.dead_end || "none") === e.value ? "selected" : ""}>${esc(e.label)}</option>`).join("")}
    </select></label>
    <div class="row-btns">
      <button id="f-insert">Insert space…</button>
      <button id="f-del" class="danger">Delete station</button>
    </div>
  </div>`;

  const live = (el, fn) => {
    el.addEventListener("focus", pushUndo);
    el.addEventListener("input", () => { fn(); markDirty(); scheduleRender(); });
  };

  const label = $("#f-label");
  live(label, () => {
    st.label = label.value;
    document.querySelectorAll(`#station-list [data-sid="${cssEsc(sid)}"] .lbl`)
      .forEach((n) => (n.textContent = st.label));
  });

  for (const [id, axis] of [["#f-gx", "gx"], ["#f-gy", "gy"]]) {
    const input = $(id);
    live(input, () => {
      const v = Number(input.value);
      if (Number.isFinite(v)) st[axis] = v;
      const when = $("#f-when");
      if (when && axis === "gx") when.textContent = whenLabel(st.gx);
    });
    input.addEventListener("change", refreshPanels);
  }

  const when = $("#f-date");
  if (when) {
    when.addEventListener("change", (ev) => {
      const gx = gxForDate(ev.target.value);
      if (gx === null) { ev.target.value = dateForGX(st.gx); return; }
      applyChange(() => { st.gx = gx; });
    });
  }

  $("#f-side").addEventListener("change", (ev) => applyChange(() => {
    if (ev.target.value === "auto") delete st.label_at;
    else st.label_at = ev.target.value;
  }));

  $("#f-angle").addEventListener("change", (ev) => applyChange(() => {
    const a = Number(ev.target.value);
    if (a) st.label_angle = a; else delete st.label_angle;   // 0 is the default
  }));
  $("#f-end").addEventListener("change", (ev) => applyChange(() => {
    if (ev.target.value === "none") delete st.dead_end;   // the default stays implicit
    else st.dead_end = ev.target.value;
  }));
  $("#f-id").addEventListener("change", (ev) => renameStation(sid, ev.target.value.trim()));
  $("#f-insert").addEventListener("click", () => insertSpaceDialog(sid));
  $("#f-del").addEventListener("click", () => deleteStation(sid));
}

/** How a grid x reads as a date, for the station editor. */
function whenLabel(gx) {
  const col = columnAt(gx);
  if (!col) return "off the timeline";
  const part = gx - Math.floor(gx);
  return part ? `inside ${col.full}` : `start of ${col.full}`;
}

function dateForGX(gx) {
  const col = columnAt(gx);
  return col ? col.date : "";
}

/** The column holding a date, as a whole grid x — null when it is off the ruler. */
function gxForDate(iso) {
  const cols = (TIMELINE && TIMELINE.columns_at) || [];
  if (!iso || !cols.length) return null;
  const last = cols[cols.length - 1];
  // ISO dates compare correctly as strings; both ends have to be checked, or a
  // date past the ruler quietly resolves to its final column
  if (iso < cols[0].date || iso > last.ends) return null;
  let found = null;
  for (const col of cols) {
    if (col.date <= iso) found = col.gx; else break;
  }
  return found;
}

function renameStation(oldId, newId) {
  if (!newId || newId === oldId) { refreshPanels(); return; }
  if (S.spec.stations[newId]) { alert(`Id “${newId}” is already used.`); refreshPanels(); return; }
  applyChange(() => {
    const rebuilt = {};
    for (const [k, v] of Object.entries(S.spec.stations)) rebuilt[k === oldId ? newId : k] = v;
    S.spec.stations = rebuilt;
    for (const ln of S.spec.lines) ln.stations = ln.stations.map((s) => (s === oldId ? newId : s));
    S.sel = { kind: "station", id: newId };
  });
}

function deleteStation(sid) {
  applyChange(() => {
    delete S.spec.stations[sid];
    for (const ln of S.spec.lines) {
      ln.stations = ln.stations.filter((s) => s !== sid);
      pruneNotes(ln);
    }
    for (const zn of S.spec.zones) {
      if (zn.stations) zn.stations = zn.stations.filter((s) => s !== sid);
    }
    S.sel = { kind: null, id: null };
  });
}

function addStation() {
  const ids = Object.keys(S.spec.stations);
  let n = ids.length + 1;
  while (S.spec.stations[`s${n}`]) n += 1;
  const sid = `s${n}`;
  const taken = new Set(ids.map((k) => `${S.spec.stations[k].gx},${S.spec.stations[k].gy}`));
  let gx = 0, gy = 0;
  if (ids.length) {
    gx = Math.round(ids.reduce((a, k) => a + S.spec.stations[k].gx, 0) / ids.length);
    gy = Math.round(ids.reduce((a, k) => a + S.spec.stations[k].gy, 0) / ids.length);
    while (taken.has(`${gx},${gy}`)) gx += 1;
  }
  applyChange(() => {
    S.spec.stations[sid] = { label: `Station ${n}`, gx, gy };
    S.sel = { kind: "station", id: sid };
  });
  const label = $("#f-label");
  if (label) { label.focus(); label.select(); }
}

/* --------------------------------------------------------------- lines -- */

function renderLines() {
  const list = $("#line-list");
  if (!S.spec.lines.length) {
    list.innerHTML = `<li class="note">No lines yet — place a few stations, then press “+ Add”.</li>`;
  } else {
    list.innerHTML = S.spec.lines.map((ln, i) => {
      const open = S.sel.kind === "line" && S.sel.id === i;
      const state = ln.status && ln.status !== "live" ? ln.status : "";
      const badge = state
        ? `<span class="chip state ${esc(state)}">${esc(statusLabel(state))}</span>` : "";
      return `
      <li class="row ${S.sel.kind === "line" && S.sel.id === i ? "is-on" : ""}" data-line="${i}">
        <span class="swatch ${esc(state)}" style="background:${esc(ln.color)}"></span>
        <span class="grow">
          <span class="lbl">${esc(ln.name)} ${badge}</span>
          <span class="at">${ln.stations.length ? esc(ln.stations.join(" › ")) : "empty route"}</span>
        </span>
        <button class="ghost" data-move="${i}" data-dir="-1" title="earlier track" ${i === 0 ? "disabled" : ""}>↑</button>
        <button class="ghost" data-move="${i}" data-dir="1" title="later track" ${i === S.spec.lines.length - 1 ? "disabled" : ""}>↓</button>
      </li>` + (open ? `<li class="edit-host"><div id="line-editor" class="editor"></div></li>` : "");
    }).join("");
    list.querySelectorAll("[data-line]").forEach((row) =>
      row.addEventListener("click", (ev) => {
        if (ev.target.dataset.move !== undefined) return;
        select("line", Number(row.dataset.line));
      }));
    list.querySelectorAll("[data-move]").forEach((btn) =>
      btn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        moveLine(Number(btn.dataset.move), Number(btn.dataset.dir));
      }));
  }
  renderLineEditor();
}

/** A stop's label for the notes editor, falling back to its id. */
/** The route a click on the canvas appends to: the trunk, or the open branch. */
function routeBeingEdited(ln) {
  const br = (ln.branches || [])[S.branch];
  if (!br) return ln.stations;
  br.stations = br.stations || [];
  return br.stations;
}

/** A point on the grid the author can move: a station, or a junction. */
function waypoint(id) {
  return (S.spec.stations || {})[id] || (S.spec.junctions || {})[id] || null;
}

/** Every stop a line reaches, down its trunk and all its branches. */
function strandsOf(ln) {
  const out = [{ ids: ln.stations || [], owner: ln, name: ln.name || "" }];
  (ln.branches || []).forEach((br, i) => out.push({
    ids: br.stations || [], owner: br,
    name: br.name || `${ln.name || ""} branch ${i + 1}`,
  }));
  return out;
}

/** A readable name for a route point, station or junction. */
function pointName(id) {
  const st = (S.spec.stations || {})[id];
  return st ? (st.label || id) : id;
}

function stopName(sid) {
  return (S.spec.stations[sid] || {}).label || sid;
}

/** What a line says it runs on to, at one end of the map. */
function saysAt(ln, which) {
  const said = ln.onward;
  return (said && typeof said === "object" && typeof said[which] === "string")
    ? said[which] : "";
}

/** Set or clear one end's onward label, leaving no empty object behind. */
function setSays(ln, which, text) {
  const said = text.trim();
  if (!said) {
    if (ln.onward) delete ln.onward[which];
  } else {
    if (!ln.onward || typeof ln.onward !== "object") ln.onward = {};
    ln.onward[which] = said;
  }
  if (ln.onward && !Object.keys(ln.onward).length) delete ln.onward;
}

/** Drop labels for an end that no longer runs on past the map. */
function pruneSays(ln) {
  if (!ln.onward) return;
  const at = ln.continues || "none";
  for (const which of ["start", "end"]) {
    if (at !== which && at !== "both") delete ln.onward[which];
  }
  if (!Object.keys(ln.onward).length) delete ln.onward;
}

function noteAt(ln, hop) {
  const found = (ln.notes || []).find((n) => n && n.at === hop);
  return found ? found.text : "";
}

/** Drop notes whose hop no longer exists — a shorter route has fewer gaps.

    Without this, removing a stop leaves a note pointing past the end of the
    route and the next save is refused by validation for something the user
    cannot see. */
function pruneNotes(ln) {
  if (!ln.notes) return;
  const hops = (ln.stations || []).length - 1;
  const kept = ln.notes.filter((n) => n && n.at >= 0 && n.at < hops);
  if (kept.length) ln.notes = kept; else delete ln.notes;
}

/** Write one hop's note, dropping the entry entirely when it is cleared. */
function setNote(ln, hop, text) {
  const notes = ln.notes || [];
  const at = notes.findIndex((n) => n && n.at === hop);
  if (!text.trim()) {
    if (at >= 0) notes.splice(at, 1);
    // an empty notes array is noise in the saved file
    if (notes.length) ln.notes = notes; else delete ln.notes;
    return;
  }
  if (at >= 0) notes[at].text = text;
  else notes.push({ at: hop, text });
  notes.sort((a, b) => a.at - b.at);
  ln.notes = notes;
}

function statusLabel(value) {
  const found = LINE_STATUSES.find((st) => st.value === value);
  return found ? found.label.replace(" — dead end", "") : value;
}

function moveLine(i, dir) {
  const j = i + dir;
  if (j < 0 || j >= S.spec.lines.length) return;
  applyChange(() => {
    const [ln] = S.spec.lines.splice(i, 1);
    S.spec.lines.splice(j, 0, ln);
    if (S.sel.kind === "line" && S.sel.id === i) S.sel.id = j;
  });
}

function renderLineEditor() {
  const box = $("#line-editor");
  if (!box) return;                    // nothing selected, so no host in the list
  if (S.sel.kind !== "line" || !S.spec.lines[S.sel.id]) { box.innerHTML = ""; return; }
  const i = S.sel.id;
  const ln = S.spec.lines[i];
  const byPlace = (t) => (a, b) => (t[a].gy - t[b].gy) || (t[a].gx - t[b].gx);
  const stationIds = Object.keys(S.spec.stations).sort(byPlace(S.spec.stations));
  const junctionIds = Object.keys(S.spec.junctions || {})
    .sort(byPlace(S.spec.junctions || {}));
  if (S.branch !== null && !(ln.branches || [])[S.branch]) S.branch = null;
  const route = routeBeingEdited(ln);

  box.innerHTML = `<div class="card">
    <h3>Line</h3>
    <label class="field"><span>Name</span><input type="text" id="l-name" value="${esc(ln.name)}"></label>
    <div class="field"><span>Colour</span>
      <div class="swatches">${PALETTE.map((p) =>
        `<button type="button" data-color="${esc(p.color)}" title="${esc(p.name)}"
           style="background:${esc(p.color)}" class="${p.color.toLowerCase() === ln.color.toLowerCase() ? "is-on" : ""}"></button>`).join("")}
      </div>
      <input type="text" id="l-color" value="${esc(ln.color)}" spellcheck="false">
    </div>

    <label class="field"><span>Service</span><select id="l-status">
      ${LINE_STATUSES.map((st) =>
        `<option value="${esc(st.value)}" ${(ln.status || "live") === st.value ? "selected" : ""}>${esc(st.label)}</option>`).join("")}
    </select></label>
    <label class="field"><span>Runs on past the map</span><select id="l-onward">
      ${CONTINUES.map((c) => `<option value="${esc(c.value)}" ${(ln.continues || "none") === c.value ? "selected" : ""}>${esc(c.label)}</option>`).join("")}
    </select></label>
    ${(ln.continues && ln.continues !== "none") ? `
    <div class="hops" id="l-says">
      <p class="note">Where it goes once it is off the page — “since 2019”, “to Cockfosters”. Written beyond the arrow.</p>
      ${["start", "end"].filter((w) => ln.continues === w || ln.continues === "both").map((w) => `
        <label class="field hop">
          <span>${w === "start" ? "Off the left" : "Off the right"}</span>
          <input type="text" data-says="${w}" value="${esc(saysAt(ln, w))}"
                 placeholder="nothing yet">
        </label>`).join("")}
    </div>` : ""}

    ${(ln.status || "live") === "out-of-service"
      ? `<p class="note">Drawn dashed and faded, with a dead-end bar wherever the route ends on a stop no line in service reaches.</p>` : ""}

    <h3>Route</h3>
    <p class="note">A branch is the same line going two ways — same colour, one
      legend entry. Start it on a stop or junction the route already passes
      through, and end it on one too, and it forks and rejoins by itself.</p>
    <div class="strands" id="l-strands">
      ${strandsOf(ln).map((st, k) => `
        <button type="button" data-strand="${k - 1}"
          class="${(k - 1) === (S.branch === null ? -1 : S.branch) ? "is-on" : ""}"
        >${k === 0 ? "Trunk" : esc(st.name)} <span class="id">${st.ids.length}</span></button>`).join("")}
      <button type="button" id="l-branch-add" class="ghost">+ Branch</button>
    </div>
    ${S.branch !== null && (ln.branches || [])[S.branch] ? `
    <label class="field"><span>Branch name</span>
      <input type="text" id="l-branch-name"
             value="${esc((ln.branches[S.branch].name) || "")}"
             placeholder="${esc(ln.name)} branch ${S.branch + 1}"></label>
    <label class="field"><span>Runs on past the map</span><select id="l-branch-onward">
      ${CONTINUES.map((c) => `<option value="${esc(c.value)}" ${(ln.branches[S.branch].continues || "none") === c.value ? "selected" : ""}>${esc(c.label)}</option>`).join("")}
    </select></label>` : ""}

    <h3>${S.branch === null ? "Stops" : "Branch stops"} — ${route.length} point${route.length === 1 ? "" : "s"}</h3>
    ${route.length < 2 ? `<p class="warn">${S.branch === null
        ? "A line needs at least two stops to render."
        : "A branch needs at least two points — where it leaves the line, and where it goes."}</p>` : ""}
    <div class="route" id="l-route">
      ${route.map((sid, k) => `
        <div class="stop-row" draggable="true" data-pos="${k}">
          <span class="n">${k + 1}</span>
          <span class="grow">${esc(pointName(sid))}
            <span class="id">${esc(sid)}</span></span>
          <button class="ghost" data-drop="${k}" title="remove from route">×</button>
        </div>`).join("") || `<p class="note">Empty — pick points below, or click them on the canvas.</p>`}
    </div>
    ${S.branch !== null ? `<div><button id="l-branch-del" class="danger">Delete branch</button></div>` : ""}

    ${S.branch === null && ln.stations.length > 1 ? `<h3>Between stops</h3>
    <p class="note">A short label riding the track — “6 weeks”, “nightly batch”. Leave one blank to remove it.</p>
    <div class="hops" id="l-notes">
      ${ln.stations.slice(0, -1).map((sid, k) => `
        <label class="field hop">
          <span>${esc(stopName(sid))} → ${esc(stopName(ln.stations[k + 1]))}</span>
          <input type="text" data-note="${k}" value="${esc(noteAt(ln, k))}"
                 placeholder="nothing yet">
        </label>`).join("")}
    </div>` : ""}

    <h3>Available stations</h3>
    <div class="pick" id="l-pick">
      ${stationIds.length ? stationIds.map((sid) =>
        `<button type="button" data-add="${esc(sid)}"
           class="${route.includes(sid) ? "on-route" : ""}"
           title="${route.includes(sid) ? "already on this route — adding again makes it a repeat stop" : "append to the route"}"
         >${esc(S.spec.stations[sid].label)}</button>`).join("")
        : `<p class="note">No stations yet — add them in the Stations tab first.</p>`}
    </div>
    ${junctionIds.length ? `<h3>Junctions</h3>
    <p class="note">Bends with no platform — where a branch splits or rejoins.</p>
    <div class="pick" id="l-pick-j">
      ${junctionIds.map((jid) =>
        `<button type="button" data-add="${esc(jid)}"
           class="junction-pick ${route.includes(jid) ? "on-route" : ""}"
         >${esc(jid)}</button>`).join("")}
    </div>` : ""}

    <div><button id="l-del" class="danger">Delete line</button></div>
  </div>`;

  const name = $("#l-name");
  name.addEventListener("focus", pushUndo);
  name.addEventListener("input", () => { ln.name = name.value; markDirty(); scheduleRender(); });
  name.addEventListener("change", refreshPanels);

  const color = $("#l-color");
  color.addEventListener("change", () => {
    if (/^#[0-9a-fA-F]{6}$/.test(color.value.trim())) {
      applyChange(() => { ln.color = color.value.trim().toLowerCase(); });
    } else {
      color.value = ln.color;
    }
  });
  box.querySelectorAll("[data-color]").forEach((btn) =>
    btn.addEventListener("click", () => applyChange(() => { ln.color = btn.dataset.color; })));

  $("#l-status").addEventListener("change", (ev) => applyChange(() => {
    if (ev.target.value === "live") delete ln.status;      // the default stays implicit
    else ln.status = ev.target.value;
  }));
  $("#l-onward").addEventListener("change", (ev) => applyChange(() => {
    if (ev.target.value === "none") { delete ln.continues; delete ln.onward; }
    else {                            // the default stays implicit
      ln.continues = ev.target.value;
      pruneSays(ln);                  // an end that no longer runs on keeps no label
    }
  }));
  box.querySelectorAll("[data-says]").forEach((input) => {
    input.addEventListener("focus", pushUndo);
    input.addEventListener("input", () => {
      setSays(ln, input.dataset.says, input.value);
      markDirty();
      scheduleRender();
    });
  });
  box.querySelectorAll("[data-add]").forEach((btn) =>
    btn.addEventListener("click", () => applyChange(() =>
      routeBeingEdited(ln).push(btn.dataset.add))));
  box.querySelectorAll("[data-drop]").forEach((btn) =>
    btn.addEventListener("click", () => applyChange(() => {
      routeBeingEdited(ln).splice(Number(btn.dataset.drop), 1);
      if (S.branch === null) pruneNotes(ln);   // notes ride the trunk's hops
    })));

  box.querySelectorAll("[data-strand]").forEach((btn) =>
    btn.addEventListener("click", () => {
      const k = Number(btn.dataset.strand);
      S.branch = k < 0 ? null : k;
      refreshPanels();
      setHint();
    }));
  const addBranch = $("#l-branch-add");
  if (addBranch) addBranch.addEventListener("click", () => applyChange(() => {
    ln.branches = ln.branches || [];
    ln.branches.push({ name: `${ln.name} branch ${ln.branches.length + 1}`,
                       stations: [] });
    S.branch = ln.branches.length - 1;
  }));
  const brName = $("#l-branch-name");
  if (brName) {
    brName.addEventListener("focus", pushUndo);
    brName.addEventListener("input", () => {
      const br = ln.branches[S.branch];
      const said = brName.value.trim();
      if (said) br.name = said; else delete br.name;   // the default stays implicit
      markDirty();
      scheduleRender();
    });
    brName.addEventListener("change", refreshPanels);
  }
  const brOnward = $("#l-branch-onward");
  if (brOnward) brOnward.addEventListener("change", (ev) => applyChange(() => {
    const br = ln.branches[S.branch];
    if (ev.target.value === "none") { delete br.continues; delete br.onward; }
    else { br.continues = ev.target.value; pruneSays(br); }
  }));
  const brDel = $("#l-branch-del");
  if (brDel) brDel.addEventListener("click", () => applyChange(() => {
    ln.branches.splice(S.branch, 1);
    if (!ln.branches.length) delete ln.branches;
    S.branch = null;
  }));

  box.querySelectorAll("[data-note]").forEach((input) => {
    input.addEventListener("focus", pushUndo);
    input.addEventListener("input", () => {
      setNote(ln, Number(input.dataset.note), input.value);
      markDirty();
      scheduleRender();
    });
  });

  initRouteDnD(ln);
  $("#l-del").addEventListener("click", () => applyChange(() => {
    S.spec.lines.splice(i, 1);
    S.sel = { kind: null, id: null };
  }));
  setHint();
}

/** Drag a stop up or down inside the route. */
function initRouteDnD(ln) {
  let from = null;
  $("#l-route").querySelectorAll(".stop-row").forEach((row) => {
    row.addEventListener("dragstart", () => { from = Number(row.dataset.pos); });
    row.addEventListener("dragover", (ev) => { ev.preventDefault(); row.classList.add("drag-over"); });
    row.addEventListener("dragleave", () => row.classList.remove("drag-over"));
    row.addEventListener("drop", (ev) => {
      ev.preventDefault();
      row.classList.remove("drag-over");
      const to = Number(row.dataset.pos);
      if (from === null || from === to) return;
      applyChange(() => {
        const route = routeBeingEdited(ln);
        const [sid] = route.splice(from, 1);
        route.splice(to, 0, sid);
        if (S.branch === null) pruneNotes(ln);
      });
      from = null;
    });
  });
}

function addLine() {
  const i = S.spec.lines.length;
  const p = PALETTE[i % PALETTE.length] || { color: "#0098d4" };
  applyChange(() => {
    S.spec.lines.push({ name: `Line ${i + 1}`, color: p.color, stations: [] });
    S.sel = { kind: "line", id: i };
  });
  const name = $("#l-name");
  if (name) { name.focus(); name.select(); }
}

/* --------------------------------------------------------------- zones -- */

function renderZones() {
  const list = $("#zone-list");
  if (!S.spec.zones.length) {
    list.innerHTML = `<li class="note">No zones yet — press “+ Add” to band a group of stations.</li>`;
  } else {
    list.innerHTML = S.spec.zones.map((zn, i) => {
      const open = S.sel.kind === "zone" && S.sel.id === i;
      const n = (zn.stations || []).length;
      return `<li class="row ${open ? "is-on" : ""}" data-zone="${i}">
        <span class="swatch band" style="background:${esc(zn.color)}"></span>
        <span class="grow">
          <span class="lbl">${esc(zn.name)}</span>
          <span class="at">${n ? `${n} station${n === 1 ? "" : "s"}` : "empty"}</span>
        </span>
        <span class="caret">${open ? "▾" : "▸"}</span>
      </li>` + (open ? `<li class="edit-host"><div id="zone-editor" class="editor"></div></li>` : "");
    }).join("");
    list.querySelectorAll("[data-zone]").forEach((row) =>
      row.addEventListener("click", () => select("zone", Number(row.dataset.zone))));
  }
  renderZoneEditor();
}

function renderZoneEditor() {
  const box = $("#zone-editor");
  if (!box) return;                    // nothing selected, so no host in the list
  if (S.sel.kind !== "zone" || !S.spec.zones[S.sel.id]) { box.innerHTML = ""; return; }
  const i = S.sel.id;
  const zn = S.spec.zones[i];
  const members = zn.stations || [];
  const stationIds = Object.keys(S.spec.stations).sort((a, b) => {
    const A = S.spec.stations[a], B = S.spec.stations[b];
    return A.gy - B.gy || A.gx - B.gx;
  });

  box.innerHTML = `<div class="card">
    <h3>Zone</h3>
    <label class="field"><span>Name</span><input type="text" id="z-name" value="${esc(zn.name)}"></label>
    <div class="field"><span>Colour</span>
      <div class="swatches">${PALETTE.map((pl) =>
        `<button type="button" data-color="${esc(pl.color)}" title="${esc(pl.name)}"
           style="background:${esc(pl.color)}" class="${pl.color.toLowerCase() === zn.color.toLowerCase() ? "is-on" : ""}"></button>`).join("")}
      </div>
      <input type="text" id="z-color" value="${esc(zn.color)}" spellcheck="false">
    </div>

    <h3>Stations in this zone — ${members.length}</h3>
    ${members.length ? "" : `<p class="note">Empty zones are not drawn. Click stations below, or on the canvas.</p>`}
    <div class="pick" id="z-pick">
      ${stationIds.length ? stationIds.map((sid) =>
        `<button type="button" data-toggle="${esc(sid)}"
           class="${members.includes(sid) ? "in-zone" : ""}"
           title="${members.includes(sid) ? "click to take it out of the zone" : "click to put it in the zone"}"
         >${esc(S.spec.stations[sid].label)}</button>`).join("")
        : `<p class="note">No stations yet — add them in the Stations tab first.</p>`}
    </div>

    <div><button id="z-del" class="danger">Delete zone</button></div>
  </div>`;

  const name = $("#z-name");
  name.addEventListener("focus", pushUndo);
  name.addEventListener("input", () => { zn.name = name.value; markDirty(); scheduleRender(); });
  name.addEventListener("change", refreshPanels);

  const color = $("#z-color");
  color.addEventListener("change", () => {
    if (/^#[0-9a-fA-F]{6}$/.test(color.value.trim())) {
      applyChange(() => { zn.color = color.value.trim().toLowerCase(); });
    } else {
      color.value = zn.color;
    }
  });
  box.querySelectorAll("[data-color]").forEach((btn) =>
    btn.addEventListener("click", () => applyChange(() => { zn.color = btn.dataset.color; })));
  box.querySelectorAll("[data-toggle]").forEach((btn) =>
    btn.addEventListener("click", () => toggleZoneMember(i, btn.dataset.toggle)));
  $("#z-del").addEventListener("click", () => applyChange(() => {
    S.spec.zones.splice(i, 1);
    S.sel = { kind: null, id: null };
  }));
  setHint();
}

/** Every stop drawn by the same capsule as this one, or just this one. */
function joinedWith(sid) {
  const group = (S.spec.interchanges || []).find(
    (ix) => ix && (ix.stations || []).includes(sid));
  return group ? group.stations.slice() : [sid];
}

function toggleZoneMember(zoneIndex, sid) {
  toggleZoneMembers(zoneIndex, joinedWith(sid));
}

/** Put a set of stops in a zone, or take them all out if they are already in. */
function toggleZoneMembers(zoneIndex, sids) {
  const zn = S.spec.zones[zoneIndex];
  if (!zn || !sids.length) return;
  applyChange(() => {
    zn.stations = zn.stations || [];
    const allIn = sids.every((s) => zn.stations.includes(s));
    for (const sid of sids) {
      const at = zn.stations.indexOf(sid);
      if (allIn && at >= 0) zn.stations.splice(at, 1);
      else if (!allIn && at < 0) zn.stations.push(sid);
    }
  });
}

function addZone() {
  const i = S.spec.zones.length;
  const used = new Set([...S.spec.lines, ...S.spec.zones].map((x) => x.color));
  const pick = PALETTE.find((pl) => !used.has(pl.color)) || PALETTE[i % PALETTE.length]
    || { color: "#8f9aa4" };
  applyChange(() => {
    S.spec.zones.push({ name: `Zone ${i + 1}`, color: pick.color, stations: [] });
    S.sel = { kind: "zone", id: i };
  });
  const name = $("#z-name");
  if (name) { name.focus(); name.select(); }
}

/* ------------------------------------------------------------ timeline -- */

function renderTimeline() {
  const box = $("#timeline-editor");
  if (!box) return;
  if (!isRoadmap()) { box.innerHTML = ""; box.dataset.built = ""; return; }
  // never install a default here: writing to the spec from inside a render pass
  // would persist dates nobody chose, with no undo step and no dirty mark.
  // normalise() puts one on a roadmap that arrives without one.
  const tl = S.spec.timeline;
  if (!tl) { box.innerHTML = ""; box.dataset.built = ""; return; }

  // rebuilt only when the values changed under it — otherwise a date input
  // would lose focus on every keystroke that triggers a re-render
  const stamp = `${tl.start}|${tl.end}|${tl.interval}|${tl.axis || "top"}|${JSON.stringify(S.spec.phases || [])}`;
  if (box.dataset.built === stamp) { renderTimelineReadout(); return; }

  box.innerHTML = `<div class="card">
    <div class="pair">
      <label class="field"><span>Start</span>
        <input type="date" id="t-start" value="${esc(tl.start)}"></label>
      <label class="field"><span>End</span>
        <input type="date" id="t-end" value="${esc(tl.end)}"></label>
    </div>
    <div class="pair">
      <label class="field"><span>One column is</span><select id="t-interval">
        ${INTERVALS.map((iv) =>
          `<option value="${esc(iv)}" ${tl.interval === iv ? "selected" : ""}>${esc(iv)}</option>`).join("")}
      </select></label>
      <label class="field"><span>Dates go</span><select id="t-axis">
        ${["top", "bottom"].map((v) =>
          `<option value="${v}" ${(tl.axis || "top") === v ? "selected" : ""}>${v}</option>`).join("")}
      </select></label>
    </div>
    <p class="note" id="t-readout"></p>
  </div>

  <div class="card">
    <h3>Phases</h3>
    <p class="note">Grey columns behind everything, banded by date — “Preparation”, “Test”, “Go to market”.</p>
    <div class="hops" id="t-phases">
      ${(S.spec.phases || []).map((ph, k) => `
        <div class="phase-row" data-phase="${k}">
          <input type="text" data-pf="name" data-k="${k}" value="${esc(ph.name || "")}" placeholder="name">
          <input type="date" data-pf="from" data-k="${k}" value="${esc(ph.from || "")}">
          <input type="date" data-pf="to" data-k="${k}" value="${esc(ph.to || "")}">
          <button class="ghost" data-pdel="${k}" title="remove">×</button>
        </div>`).join("")}
    </div>
    <div><button id="t-add-phase">+ Add phase</button></div>
  </div>`;
  box.dataset.built = stamp;

  // A start date is snapped back to the period holding it, so 14 Feb with a
  // monthly interval becomes 1 Feb — write the snapped value back so the field
  // shows what the ruler actually starts on.
  const write = (key, value) => applyChange(() => {
    S.spec.timeline = { ...S.spec.timeline, [key]: value };
  });
  $("#t-start").addEventListener("change", (ev) => write("start", ev.target.value));
  $("#t-end").addEventListener("change", (ev) => write("end", ev.target.value));
  $("#t-interval").addEventListener("change", (ev) => write("interval", ev.target.value));
  $("#t-axis").addEventListener("change", (ev) => write("axis", ev.target.value));

  box.querySelectorAll("[data-pf]").forEach((input) => {
    input.addEventListener("focus", pushUndo);
    input.addEventListener("input", () => {
      const ph = S.spec.phases[Number(input.dataset.k)];
      if (!ph) return;
      ph[input.dataset.pf] = input.value;
      markDirty();
      scheduleRender();
    });
  });
  box.querySelectorAll("[data-pdel]").forEach((btn) =>
    btn.addEventListener("click", () => applyChange(() =>
      S.spec.phases.splice(Number(btn.dataset.pdel), 1))));
  $("#t-add-phase").addEventListener("click", () => applyChange(() => {
    // default to the span the ruler already covers, so it draws immediately
    const first = TIMELINE && TIMELINE.columns_at[0];
    const last = TIMELINE && TIMELINE.columns_at[TIMELINE.columns_at.length - 1];
    S.spec.phases.push({ name: `Phase ${S.spec.phases.length + 1}`,
                         from: first ? first.date : "",
                         to: last ? last.ends : "" });
  }));
  renderTimelineReadout();
}

function renderTimelineReadout() {
  const out = $("#t-readout");
  if (!out) return;
  if (!TIMELINE) { out.textContent = "Not drawing yet — check the dates above."; return; }
  const first = TIMELINE.columns_at[0];
  const last = TIMELINE.columns_at[TIMELINE.columns_at.length - 1];
  out.textContent = `${TIMELINE.columns} columns, ${first.full} to ${last.full}`
    + ` · ruler starts ${TIMELINE.start}, closes ${TIMELINE.end}`;
  // a start is snapped back to the period holding it, so show what the ruler
  // actually begins on rather than leaving 14 Feb in a field that means 1 Feb
  const startField = $("#t-start");
  if (startField && document.activeElement !== startField
      && startField.value !== TIMELINE.start) {
    startField.value = TIMELINE.start;
  }
  setHint();
}

/* --------------------------------------------------------------- joins -- */

function renderJoins() {
  const list = $("#join-list");
  if (!list) return;
  if (!S.spec.interchanges.length) {
    list.innerHTML = `<li class="note">No joins yet — press “+ Add”, then click the stops the capsule should cover.</li>`;
  } else {
    list.innerHTML = S.spec.interchanges.map((ix, i) => {
      const open = S.sel.kind === "join" && S.sel.id === i;
      const n = (ix.stations || []).length;
      return `<li class="row ${open ? "is-on" : ""}" data-join="${i}">
        <span class="swatch band" style="background:var(--ink)"></span>
        <span class="grow">
          <span class="lbl">${esc(ix.label || "(unnamed)")}</span>
          <span class="at">${n ? `${n} stop${n === 1 ? "" : "s"}` : "empty"}</span>
        </span>
        <span class="caret">${open ? "▾" : "▸"}</span>
      </li>` + (open ? `<li class="edit-host"><div id="join-editor" class="editor"></div></li>` : "");
    }).join("");
    list.querySelectorAll("[data-join]").forEach((row) =>
      row.addEventListener("click", () => select("join", Number(row.dataset.join))));
  }
  renderJoinEditor();
}

function renderJoinEditor() {
  const box = $("#join-editor");
  if (!box) return;
  if (S.sel.kind !== "join" || !S.spec.interchanges[S.sel.id]) { box.innerHTML = ""; return; }
  const i = S.sel.id;
  const ix = S.spec.interchanges[i];
  const members = ix.stations || [];
  const ids = Object.keys(S.spec.stations).sort((a, b) => {
    const A = S.spec.stations[a], B = S.spec.stations[b];
    return A.gx - B.gx || A.gy - B.gy;
  });
  const sides = ["auto", ...LABEL_SIDES];

  box.innerHTML = `<div class="card">
    <h3>Join</h3>
    <label class="field"><span>Label — replaces the labels of the stops it covers</span>
      <input type="text" id="j-label" value="${esc(ix.label || "")}" placeholder="leave blank to keep each stop's own"></label>
    <div class="pair">
      <label class="field"><span>Label side</span><select id="j-side">
        ${sides.map((v) => `<option value="${esc(v)}" ${(ix.label_at || "auto") === v ? "selected" : ""}>${esc(v)}</option>`).join("")}
      </select></label>
      <label class="field"><span>Label angle</span><select id="j-angle">
        ${LABEL_ANGLES.map((a) => `<option value="${a}" ${(ix.label_angle || 0) === a ? "selected" : ""}>${a}°</option>`).join("")}
      </select></label>
    </div>

    <h3>Stops covered — ${members.length}</h3>
    ${members.length < 2 ? `<p class="warn">A join needs two stops to stretch between.</p>` : ""}
    <div class="pick" id="j-pick">
      ${ids.length ? ids.map((sid) =>
        `<button type="button" data-toggle="${esc(sid)}"
           class="${members.includes(sid) ? "in-zone" : ""}"
         >${esc(S.spec.stations[sid].label)}</button>`).join("")
        : `<p class="note">No stations yet.</p>`}
    </div>

    <div><button id="j-del" class="danger">Delete join</button></div>
  </div>`;

  const label = $("#j-label");
  label.addEventListener("focus", pushUndo);
  label.addEventListener("input", () => {
    if (label.value.trim()) ix.label = label.value; else delete ix.label;
    markDirty();
    scheduleRender();
  });
  label.addEventListener("change", refreshPanels);
  $("#j-side").addEventListener("change", (ev) => applyChange(() => {
    if (ev.target.value === "auto") delete ix.label_at; else ix.label_at = ev.target.value;
  }));
  $("#j-angle").addEventListener("change", (ev) => applyChange(() => {
    const a = Number(ev.target.value);
    if (a) ix.label_angle = a; else delete ix.label_angle;
  }));
  box.querySelectorAll("[data-toggle]").forEach((btn) =>
    btn.addEventListener("click", () => toggleJoinMember(i, btn.dataset.toggle)));
  $("#j-del").addEventListener("click", () => applyChange(() => {
    S.spec.interchanges.splice(i, 1);
    S.sel = { kind: null, id: null };
  }));
  setHint();
}

function toggleJoinMember(index, sid) {
  const ix = S.spec.interchanges[index];
  if (!ix) return;
  applyChange(() => {
    ix.stations = ix.stations || [];
    const at = ix.stations.indexOf(sid);
    if (at >= 0) ix.stations.splice(at, 1); else ix.stations.push(sid);
  });
}

/* A milestone that lands on several lines at once is one capsule but many
   stops — one per line, because each line has to stop there for its route to be
   right. Placing six of those by hand and joining them is the tedious half of
   the tube-map look, so this does the whole thing in one action. */

/** The row a line runs on near a given x — the gy of its closest stop. */
function laneOf(line, gx) {
  const stops = (line.stations || []).map((s) => S.spec.stations[s]).filter(Boolean);
  if (!stops.length) return 0;
  return stops.reduce((best, st) =>
    Math.abs(st.gx - gx) < Math.abs(best.gx - gx) ? st : best, stops[0]).gy;
}

/** Put a stop into a route where its x belongs, keeping the line left to right. */
function insertByGx(line, sid, gx) {
  const route = line.stations || (line.stations = []);
  let at = route.findIndex((s) => {
    const st = S.spec.stations[s];
    return st && st.gx > gx;
  });
  if (at < 0) at = route.length;
  route.splice(at, 0, sid);
  // notes are addressed by hop index, and a stop inserted before them moves
  // every later hop along by one; without this they would quietly retarget
  for (const note of line.notes || []) {
    if (note && typeof note.at === "number" && note.at >= at) note.at += 1;
  }
  return at;
}

function freeId(base) {
  const root = (base || "s").toLowerCase().replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "").slice(0, 24) || "stop";
  if (!S.spec.stations[root]) return root;
  let n = 2;
  while (S.spec.stations[`${root}-${n}`]) n += 1;
  return `${root}-${n}`;
}

function milestoneDialog() {
  const usable = S.spec.lines
    .map((ln, i) => ({ ln, i }))
    .filter(({ ln }) => (ln.stations || []).length);
  if (!usable.length) {
    showProblems(["No line has any stops yet — route at least one line first, "
      + "so a milestone knows which row each line runs on."]);
    return;
  }
  const suggested = Math.max(...Object.values(S.spec.stations).map((s) => s.gx), -1) + 1;

  dialog("Milestone across lines", `
    <p class="note">One stop on each line you pick, all at the same grid x, joined
      into a single capsule. The label goes on the join, so it is written once.</p>
    <label class="field"><span>Label</span>
      <input type="text" id="m-label" value="Milestone" autofocus></label>
    <div class="pair">
      <label class="field"><span>Grid x</span>
        <input type="number" id="m-gx" step="${S.snap}" value="${suggested}"></label>
      <label class="field"><span>Label angle</span><select id="m-angle">
        ${LABEL_ANGLES.map((a) => `<option value="${a}" ${a === 45 ? "selected" : ""}>${a}°</option>`).join("")}
      </select></label>
    </div>
    ${isRoadmap() ? `<label class="field"><span>Or pick a date</span>
      <input type="date" id="m-date"></label>` : ""}
    <h3>Lines it lands on</h3>
    <div class="hops" id="m-lines">
      ${usable.map(({ ln, i }) => `
        <label class="toggle"><input type="checkbox" data-line="${i}" checked>
          <span class="swatch" style="background:${esc(ln.color)}"></span>
          ${esc(ln.name)}</label>`).join("")}
    </div>
    <div class="actions"><button value="cancel">Cancel</button>
      <button type="button" class="primary" id="m-ok">Add milestone</button></div>`,
  (form, dlg) => {
    const dateField = form.querySelector("#m-date");
    if (dateField) {
      dateField.addEventListener("change", (ev) => {
        const gx = gxForDate(ev.target.value);
        if (gx === null) { ev.target.value = ""; return; }
        form.querySelector("#m-gx").value = gx;
      });
    }
    form.querySelector("#m-ok").addEventListener("click", () => {
      const label = form.querySelector("#m-label").value.trim() || "Milestone";
      const gx = Number(form.querySelector("#m-gx").value);
      const angle = Number(form.querySelector("#m-angle").value);
      const picked = [...form.querySelectorAll("[data-line]:checked")]
        .map((b) => Number(b.dataset.line));
      dlg.close();
      if (!Number.isFinite(gx) || !picked.length) {
        showProblems(["A milestone needs a grid x and at least one line."]);
        return;
      }
      addMilestone(label, gx, angle, picked);
    });
  });
}

function addMilestone(label, gx, angle, lineIndexes) {
  applyChange(() => {
    const members = [];
    for (const li of lineIndexes) {
      const line = S.spec.lines[li];
      if (!line) continue;
      const sid = freeId(`${label}-${line.name}`);
      S.spec.stations[sid] = { label: `${label} · ${line.name}`,
                               gx, gy: laneOf(line, gx) };
      insertByGx(line, sid, gx);
      members.push(sid);
    }
    S.spec.interchanges.push({ label, stations: members,
                               label_at: "above", label_angle: angle || undefined });
    S.sel = { kind: "join", id: S.spec.interchanges.length - 1 };
  });
}

function addJoin() {
  const i = S.spec.interchanges.length;
  applyChange(() => {
    S.spec.interchanges.push({ stations: [] });
    S.sel = { kind: "join", id: i };
  });
  const label = $("#j-label");
  if (label) label.focus();
}

/* --------------------------------------------------------------- rides -- */

/* A ride animates the moment it has a route — there is nothing to start. The
   controls exist because that is not obvious, and because watching a loop you
   cannot stop is worse than one you can. Play state is re-applied after every
   render, since each render swaps in a fresh SVG that starts out running. */

function travellers() {
  return document.querySelectorAll("#canvas svg .traveller");
}

function applyRideState() {
  travellers().forEach((t) => {
    t.style.animationPlayState = S.ridesPlaying ? "running" : "paused";
  });
  const btn = $("#btn-ride-play");
  if (btn) {
    btn.textContent = S.ridesPlaying ? "⏸ Pause" : "▶ Play";
    btn.title = S.ridesPlaying ? "pause the travellers" : "resume the travellers";
  }
  const none = !travellers().length;
  for (const id of ["#btn-ride-play", "#btn-ride-restart"]) {
    const b = $(id);
    if (b) b.disabled = none;
  }
}

function toggleRides() {
  S.ridesPlaying = !S.ridesPlaying;
  applyRideState();
}

/** Send every traveller back to its first stop. */
function restartRides() {
  travellers().forEach((t) => {
    t.style.animation = "none";
    void t.getBoundingClientRect();          // force a reflow, or the restart is a no-op
    t.style.animation = "";
  });
  applyRideState();
}

function renderScenarios() {
  const list = $("#scenario-list");
  if (!list) return;
  if (!S.spec.scenarios.length) {
    list.innerHTML = `<li class="note">No rides yet — press “+ Add”, then click stations on the canvas in the order the traveller visits them.</li>`;
  } else {
    list.innerHTML = S.spec.scenarios.map((sc, i) => {
      const open = S.sel.kind === "scenario" && S.sel.id === i;
      const n = (sc.stations || []).length;
      return `<li class="row ${open ? "is-on" : ""}" data-scenario="${i}">
        <span class="swatch" style="background:${esc(sc.color || "#101820")}"></span>
        <span class="grow">
          <span class="lbl">${esc(sc.name)}</span>
          <span class="at">${n ? `${n} stop${n === 1 ? "" : "s"} · ${sc.duration || 8}s` : "no route yet"}</span>
        </span>
        <span class="caret">${open ? "▾" : "▸"}</span>
      </li>` + (open ? `<li class="edit-host"><div id="scenario-editor" class="editor"></div></li>` : "");
    }).join("");
    list.querySelectorAll("[data-scenario]").forEach((row) =>
      row.addEventListener("click", () => select("scenario", Number(row.dataset.scenario))));
  }
  renderScenarioEditor();
}

function renderScenarioEditor() {
  const box = $("#scenario-editor");
  if (!box) return;
  if (S.sel.kind !== "scenario" || !S.spec.scenarios[S.sel.id]) { box.innerHTML = ""; return; }
  const i = S.sel.id;
  const sc = S.spec.scenarios[i];
  const route = sc.stations || [];
  const ids = Object.keys(S.spec.stations).sort((a, b) => {
    const A = S.spec.stations[a], B = S.spec.stations[b];
    return A.gy - B.gy || A.gx - B.gx;
  });

  box.innerHTML = `<div class="card">
    <h3>Ride</h3>
    <label class="field"><span>Name</span><input type="text" id="r-name" value="${esc(sc.name)}"></label>
    <div class="field"><span>Colour</span>
      <div class="swatches">${PALETTE.map((pl) =>
        `<button type="button" data-color="${esc(pl.color)}" title="${esc(pl.name)}"
           style="background:${esc(pl.color)}" class="${(sc.color || "").toLowerCase() === pl.color.toLowerCase() ? "is-on" : ""}"></button>`).join("")}
      </div>
    </div>
    <label class="field"><span>Seconds end to end — <b id="r-secs">${sc.duration || 8}</b></span>
      <input type="range" id="r-dur" min="2" max="40" step="1" value="${sc.duration || 8}"></label>

    <h3>Route — ${route.length} stop${route.length === 1 ? "" : "s"}</h3>
    ${route.length < 2 ? `<p class="warn">A ride needs at least two stops.</p>` : ""}
    <div class="route" id="r-route">
      ${route.map((sid, k) => `
        <div class="stop-row" data-pos="${k}">
          <span class="n">${k + 1}</span>
          <span class="grow">${esc(stopName(sid))}</span>
          <button class="ghost" data-drop="${k}" title="remove from the route">×</button>
        </div>`).join("") || `<p class="note">Empty — click stations on the canvas in order.</p>`}
    </div>

    <h3>Available stations</h3>
    <div class="pick" id="r-pick">
      ${ids.length ? ids.map((sid) =>
        `<button type="button" data-add="${esc(sid)}">${esc(S.spec.stations[sid].label)}</button>`).join("")
        : `<p class="note">No stations yet.</p>`}
    </div>

    <div><button id="r-del" class="danger">Delete ride</button></div>
  </div>`;

  const name = $("#r-name");
  name.addEventListener("focus", pushUndo);
  name.addEventListener("input", () => { sc.name = name.value; markDirty(); scheduleRender(); });
  name.addEventListener("change", refreshPanels);

  const dur = $("#r-dur");
  dur.addEventListener("pointerdown", pushUndo);
  dur.addEventListener("input", () => {
    sc.duration = Number(dur.value);
    $("#r-secs").textContent = dur.value;
    markDirty();
    scheduleRender();
  });

  box.querySelectorAll("[data-color]").forEach((btn) =>
    btn.addEventListener("click", () => applyChange(() => { sc.color = btn.dataset.color; })));
  box.querySelectorAll("[data-add]").forEach((btn) =>
    btn.addEventListener("click", () => applyChange(() => {
      sc.stations = sc.stations || [];
      sc.stations.push(btn.dataset.add);
    })));
  box.querySelectorAll("[data-drop]").forEach((btn) =>
    btn.addEventListener("click", () => applyChange(() =>
      sc.stations.splice(Number(btn.dataset.drop), 1))));
  $("#r-del").addEventListener("click", () => applyChange(() => {
    S.spec.scenarios.splice(i, 1);
    S.sel = { kind: null, id: null };
  }));
  setHint();
}

function addScenario() {
  const i = S.spec.scenarios.length;
  const used = new Set(S.spec.scenarios.map((x) => x.color));
  const pick = PALETTE.find((pl) => !used.has(pl.color)) || PALETTE[i % PALETTE.length]
    || { color: "#7b3fb5" };
  applyChange(() => {
    S.spec.scenarios.push({ name: `Ride ${i + 1}`, color: pick.color,
                            duration: 8, stations: [] });
    S.sel = { kind: "scenario", id: i };
  });
  const name = $("#r-name");
  if (name) { name.focus(); name.select(); }
}

/* --------------------------------------------------------------- style -- */

const STYLE_FIELDS = [
  ["cell", "Pixels per grid cell", 40, 240, 5],
  ["stroke", "Route width", 2, 26, 1],
  ["corner", "Corner radius", 0, 60, 1],
  ["bundle_gap", "Gap between parallel tracks", 4, 40, 1],
  ["label_size", "Label size", 8, 40, 1],
  ["zone_pad", "Zone band padding", 8, 90, 2],
  ["onward_reach", "How far a continuing line runs past the ends", 0, 4, 0.25],
];

const SNAP_STEPS = [[1, "1 cell"], [0.5, "½ cell"], [1 / 3, "⅓ cell"], [0.25, "¼ cell"]];

function renderStyle() {
  const box = $("#style-editor");
  if (box.dataset.built === "1") {
    const legend = box.querySelector("#f-legend");
    if (legend && legend.value !== legendAt()) legend.value = legendAt();
    const snap = box.querySelector("#f-snap");
    if (snap && Math.abs(Number(snap.value) - S.snap) > 1e-6) {
      snap.value = SNAP_STEPS.reduce((best, [v]) =>
        Math.abs(v - S.snap) < Math.abs(best - S.snap) ? v : best, 1);
    }
    for (const [key] of STYLE_FIELDS) {
      const input = box.querySelector(`[data-style="${key}"]`);
      if (input && Number(input.value) !== S.style[key]) input.value = S.style[key];
      const out = box.querySelector(`[data-style-out="${key}"]`);
      if (out) out.textContent = S.style[key];
    }
    return;
  }
  box.innerHTML = `<div class="card">
    <label class="field"><span>Legend — where the line names go</span>
      <select id="f-legend">${LEGEND_POSITIONS.map((v) =>
        `<option value="${v}" ${legendAt() === v ? "selected" : ""}>${v}</option>`).join("")}
      </select></label>
    <label class="field"><span>Grid snap — how close two stations may sit</span>
      <select id="f-snap">${SNAP_STEPS.map(([v, label]) =>
        `<option value="${v}" ${Math.abs(v - S.snap) < 1e-6 ? "selected" : ""}>${label}</option>`).join("")}
      </select></label>
    ${STYLE_FIELDS.map(([key, label, min, max, step]) => `
    <label class="field"><span>${esc(label)} — <b data-style-out="${key}">${S.style[key]}</b></span>
      <input type="range" data-style="${key}" min="${min}" max="${max}" step="${step}" value="${S.style[key]}">
    </label>`).join("")}</div>`;
  box.dataset.built = "1";
  $("#f-legend").addEventListener("change", (ev) => applyChange(() => {
    // bottom is the default, so it stays out of the spec the way "live" does
    if (ev.target.value === DEFAULT_LEGEND) delete S.spec.legend;
    else S.spec.legend = ev.target.value;
  }));
  $("#f-snap").addEventListener("change", (ev) => {
    S.snap = Number(ev.target.value);
    markDirty();
    setHint();
    refreshPanels();
  });
  box.querySelectorAll("[data-style]").forEach((input) => {
    input.addEventListener("pointerdown", pushUndo);
    input.addEventListener("input", () => {
      S.style[input.dataset.style] = Number(input.value);
      box.querySelector(`[data-style-out="${input.dataset.style}"]`).textContent = input.value;
      markDirty();
      scheduleRender();
    });
  });
}

/* ------------------------------------------------------------- dialogs -- */

/* ------------------------------------------------------------ settings -- */

/** Connect an importer: the one place a credential is ever typed. */
async function settingsDialog(only) {
  let info;
  try { info = await api("GET", "/api/sources"); }
  catch (err) { showProblems(err.errors); return; }
  const wanting = info.sources.filter((s) => (s.env || []).length);
  if (!wanting.length) {
    showProblems([], ["No importer needs credentials."]);
    return;
  }
  let chosen = only && wanting.some((s) => s.name === only)
    ? only : wanting[0].name;
  let state = null;

  const load = async () => { state = await api("GET", `/api/settings/${chosen}`); };
  try { await load(); } catch (err) { showProblems(err.errors); return; }

  const body = () => `
    <label class="field"><span>Importer</span>
      <select id="set-source">${wanting.map((s) =>
        `<option value="${esc(s.name)}" ${s.name === chosen ? "selected" : ""}
         >${esc(s.title)}</option>`).join("")}</select></label>
    ${state.fields.map((f) => `
      <label class="field" title="${esc(f.help)}">
        <span>${esc(f.key)}${f.required ? " *" : ""}</span>
        <input type="${f.secret ? "password" : "text"}" data-set="${esc(f.key)}"
               placeholder="${esc(f.secret && f.present
                 ? "•••••••• saved — type to replace"
                 : (f.placeholder || f.help))}"
               autocomplete="off" spellcheck="false"
               value="${esc(f.secret ? "" : (f.present && f.from === "settings" ? "" : ""))}">
        <span class="note">${f.present
          ? `set from ${esc(f.from)}${f.from === "environment"
              ? " — the environment wins, so this box will not change it" : ""}`
          : "not set"}</span>
      </label>`).join("")}
    <p class="note">Saved to <code>${esc(state.path)}</code>, readable only by
      you. A secret is never sent back to this page, and never reaches a map.</p>
    <p class="note" id="set-said"></p>
    <div class="actions">
      <button value="cancel">Close</button>
      <button type="button" id="set-test">Test connection</button>
      <button type="button" class="primary" id="set-save">Save</button>
    </div>`;

  dialog("Settings", body(), (form, dlg) => {
    const wire = () => {
      form.querySelector("#set-source").addEventListener("change", async (ev) => {
        chosen = ev.target.value;
        try { await load(); } catch (err) { showProblems(err.errors); return; }
        form.innerHTML = `<h2>Settings</h2>${body()}`;
        wire();
      });
      const said = form.querySelector("#set-said");
      const collect = () => {
        const values = {};
        form.querySelectorAll("[data-set]").forEach((el) => {
          // an untouched box means "leave it alone", not "clear it" — otherwise
          // opening Settings and pressing Save would wipe a saved token
          if (el.value !== "") values[el.dataset.set] = el.value;
        });
        return values;
      };
      form.querySelector("#set-save").addEventListener("click", async () => {
        const values = collect();
        if (!Object.keys(values).length) { said.textContent = "Nothing to save."; return; }
        said.textContent = "Saving…";
        try { state = await api("PUT", `/api/settings/${chosen}`, { values }); }
        catch (err) { said.textContent = (err.errors || ["could not save"])[0]; return; }
        form.innerHTML = `<h2>Settings</h2>${body()}`;
        wire();
        form.querySelector("#set-said").textContent = "Saved.";
      });
      form.querySelector("#set-test").addEventListener("click", async () => {
        said.textContent = "Testing…";
        let res;
        try { res = await api("POST", `/api/settings/${chosen}/test`); }
        catch (err) { said.textContent = (err.errors || ["could not test"])[0]; return; }
        said.textContent = (res.ok ? "✓ " : "! ") + res.said;
      });
    };
    wire();
  });
}

/* -------------------------------------------------------------- import -- */

/** One form field for one declared source option, chosen by its kind. */
/** A field's starting value: what the browser picked, else its declared default. */
function optValue(o, filled) {
  if (filled && filled[o.name] !== undefined && filled[o.name] !== "") {
    return filled[o.name];
  }
  return o.default === null || o.default === undefined ? "" : String(o.default);
}

function optField(o, filled) {
  const id = `imp-${o.name}`;
  const common = `id="${id}" data-opt="${esc(o.name)}"`;
  const value = optValue(o, filled);
  if (o.kind === "bool") {
    return `<label class="field"><span>${esc(o.name)}</span>
      <span class="note"><input type="checkbox" ${common} ${o.default ? "checked" : ""}>
      ${esc(o.help)}</span></label>`;
  }
  if (o.kind === "choice") {
    return `<label class="field" title="${esc(o.help)}"><span>${esc(o.name)}</span>
      <select ${common}>${(o.choices || []).map((c) =>
        `<option value="${esc(c)}" ${c === o.default ? "selected" : ""}>${esc(c || "auto")}</option>`).join("")}
      </select></label>`;
  }
  const type = { int: "number", date: "date" }[o.kind] || "text";
  return `<label class="field" title="${esc(o.help)}">
    <span>${esc(o.name)}${o.required ? " *" : ""}</span>
    <input type="${type}" ${common} value="${esc(value)}"
           placeholder="${esc(o.placeholder || "")}"></label>`;
}

/** Walk a source's tree and tick what to import. */
async function browseDialog(src, onPicked) {
  const view = { name: (src.views[0] || {}).name || "" };
  const columns = [];                 // [{path, nodes}] — one per level opened
  const picked = new Map();           // id -> label, in the order ticked

  const fetchLevel = async (path) => {
    const q = new URLSearchParams({ path: path.join("/"), view: view.name });
    return api("GET", `/api/browse/${src.name}?${q}`);
  };

  const body = () => `
    ${src.views.length > 1 ? `<div class="strands" id="brw-views">
      ${src.views.map((v) => `<button type="button" data-view="${esc(v.name)}"
        class="${v.name === view.name ? "is-on" : ""}"
        title="${esc(v.help || "")}">${esc(v.title)}</button>`).join("")}
    </div>` : ""}
    <div class="browse" id="brw-cols">
      ${columns.map((col, depth) => `
        <div class="browse-col" data-depth="${depth}">
          ${col.nodes.length ? col.nodes.map((n) => `
            <div class="browse-row ${picked.has(n.id) ? "is-on" : ""}"
                 data-node="${esc(n.id)}" data-depth="${depth}">
              ${n.selectable ? `<input type="checkbox" data-pick="${esc(n.id)}"
                 ${picked.has(n.id) ? "checked" : ""}>` : ""}
              <span class="grow">${esc(n.label)}
                ${n.hint ? `<span class="id">${esc(n.hint)}</span>` : ""}</span>
              ${n.expandable ? `<span class="caret">▸</span>` : ""}
            </div>`).join("")
            : `<p class="note">Nothing here.</p>`}
        </div>`).join("")}
    </div>
    <p class="note" id="brw-said">${picked.size
      ? `${picked.size} selected — ${[...picked.values()].slice(0, 3).map(esc).join(", ")}${picked.size > 3 ? "…" : ""}`
      : "Pick a project, then tick what to import. Nothing ticked imports everything in the project."}</p>
    <div class="actions">
      <button value="cancel">Cancel</button>
      <button type="button" class="primary" id="brw-go">Import</button>
    </div>`;

  let redraw = () => {};
  const open = async (path, depth) => {
    const said = document.querySelector("#brw-said");
    if (said) said.textContent = "Loading…";
    let res;
    try { res = await fetchLevel(path); }
    catch (err) { showProblems(err.errors); return; }
    columns.length = depth;
    columns.push({ path, nodes: res.nodes });
    redraw();
  };

  dialog(`Browse ${src.title}`, body(), (form, dlg) => {
    redraw = () => {
      form.innerHTML = `<h2>Browse ${esc(src.title)}</h2>${body()}`;
      wire();
    };
    const wire = () => {
      form.querySelectorAll("[data-view]").forEach((b) =>
        b.addEventListener("click", async () => {
          view.name = b.dataset.view;
          columns.length = 0;
          picked.clear();
          await open([], 0);
        }));
      form.querySelectorAll("[data-node]").forEach((row) =>
        row.addEventListener("click", async (ev) => {
          if (ev.target.matches("[data-pick]")) return;   // ticking is not opening
          const depth = Number(row.dataset.depth);
          const path = columns[depth].path.concat(row.dataset.node);
          await open(path, depth + 1);
        }));
      form.querySelectorAll("[data-pick]").forEach((box) =>
        box.addEventListener("change", () => {
          const id = box.dataset.pick;
          if (box.checked) {
            // the node's own label, not the row's text — the row also carries
            // the hint, and "Cutover In Progress · due 2026-03-31" is not a name
            const depth = Number(box.closest(".browse-row").dataset.depth);
            const node = (columns[depth].nodes || []).find((n) => n.id === id);
            picked.set(id, (node && node.label) || id);
          } else {
            picked.delete(id);
          }
          const said = form.querySelector("#brw-said");
          said.textContent = picked.size
            ? `${picked.size} selected — ${[...picked.values()].slice(0, 3).join(", ")}${picked.size > 3 ? "…" : ""}`
            : "Nothing ticked imports everything in the project.";
          box.closest(".browse-row").classList.toggle("is-on", box.checked);
        }));
      form.querySelector("#brw-go").addEventListener("click", () => {
        // the first column is projects, so whatever is open there is the scope
        const project = (columns[0] || {}).path !== undefined && columns[1]
          ? columns[1].path[0] : "";
        dlg.close();
        onPicked({ project, select: [...picked.keys()] });
      });
    };
    wire();
    open([], 0);
  });
}

async function importDialog(want, filled) {
  let info;
  try { info = await api("GET", "/api/sources"); }
  catch (err) { showProblems(err.errors); return; }
  // fetched now rather than at boot: whether a credential is set can change
  // while the designer is running, and a stale "not set" reads as a bug
  let chosen = info.sources.some((s) => s.name === want)
    ? want : (info.sources.length ? info.sources[0].name : "");

  const body = () => {
    const src = info.sources.find((s) => s.name === chosen) || {};
    const opts = (src.options || []).filter(
      (o) => !(info.local_only || []).includes(o.name));
    const missing = (src.env || []).filter((e) => e.required && !e.present);
    return `
      <label class="field"><span>Import from</span>
        <select id="imp-source">${info.sources.map((s) =>
          `<option value="${esc(s.name)}" ${s.name === chosen ? "selected" : ""}
           >${esc(s.title)}</option>`).join("")}</select></label>
      <p class="note">${esc(src.summary || "")}</p>
      ${(src.env || []).map((e) => `<p class="note">${e.present ? "✓" : "!"}
        <code>${esc(e.name)}</code> — ${e.present ? `set from ${esc(e.from || "settings")}`
          : `not set. ${esc(e.help)}`}</p>`).join("")}
      ${missing.length ? `<p class="warn">Not connected yet —
        <button type="button" id="imp-settings" class="ghost">open Settings</button></p>` : ""}
      ${src.browsable && !missing.length ? `<p class="note">
        <button type="button" id="imp-browse" class="ghost">Browse ${esc(src.title)}…</button>
        — pick a project and tick what you want, instead of typing keys.</p>` : ""}
      ${opts.map((o) => optField(o, filled)).join("")}
      ${S.name ? `<label class="field"><span>
        <input type="checkbox" id="imp-into" ${(S.spec.source || {}).name === chosen ? "checked" : ""}>
        Re-sync into “${esc(S.name)}”</span>
        <span class="note">Keeps the positions, colours and wording already in
        this map; only what changed upstream comes in.</span></label>` : ""}
      ${(info.broken || []).map((b) =>
        `<p class="warn">A plugin failed to load — ${esc(b)}</p>`).join("")}
      <div class="actions"><button value="cancel">Cancel</button>
        <button type="button" class="primary" id="imp-go"
          ${missing.length ? "disabled" : ""}>Import</button></div>`;
  };

  dialog("Import a plan", body(), (form, dlg) => {
    const wire = () => {
      form.querySelector("#imp-source").addEventListener("change", (ev) => {
        chosen = ev.target.value;
        form.innerHTML = `<h2>Import a plan</h2>${body()}`;
        wire();
      });
      const openSettings = form.querySelector("#imp-settings");
      if (openSettings) openSettings.addEventListener("click", () => {
        document.querySelector("#dialog").close();
        settingsDialog(chosen);
      });
      const openBrowse = form.querySelector("#imp-browse");
      if (openBrowse) openBrowse.addEventListener("click", () => {
        const src = info.sources.find((s) => s.name === chosen);
        document.querySelector("#dialog").close();
        browseDialog(src, (choice) => {
          // straight back to the *same* source's form with the choice filled
          // in, so what the browser picked is visible and editable rather than
          // hidden — and so a Jira selection cannot land on git's form
          importDialog(src.name, {project: choice.project,
                                  select: choice.select.join(",")});
        });
      });
      form.querySelector("#imp-go").addEventListener("click", async () => {
        const options = {};
        form.querySelectorAll("[data-opt]").forEach((el) => {
          options[el.dataset.opt] = el.type === "checkbox" ? el.checked : el.value;
        });
        const into = form.querySelector("#imp-into");
        const payload = { source: chosen, options };
        if (into && into.checked) payload.into = { name: S.name, folder: S.folder };
        const go = form.querySelector("#imp-go");
        go.disabled = true;
        go.textContent = "Importing…";
        let res;
        try { res = await api("POST", "/api/import", payload); }
        catch (err) {
          go.disabled = false;
          go.textContent = "Import";
          showProblems(err.errors);
          return;
        }
        dlg.close();
        // An import is an edit, not a save: it lands dirty so that keeping it
        // stays a deliberate act. And unless it was a re-sync *into* the open
        // map, it is a different map — leaving the old name attached would put
        // the next Ctrl+S straight through whatever was open before.
        const resync = !!(payload.into);
        applyChange(() => {
          S.spec = normalise(res.spec);
          if (!resync) {
            S.name = null;
            S.folder = null;
            S.version = null;
          }
        });
        showProblems(res.errors, (res.notes || []).concat(res.warnings || []));
      });
    };
    wire();
  });
}

function dialog(title, bodyHTML, onOpen) {
  const dlg = $("#dialog");
  const form = $("#dialog-form");
  form.innerHTML = `<h2>${esc(title)}</h2>${bodyHTML}`;
  dlg.showModal();
  if (onOpen) onOpen(form, dlg);
  return dlg;
}

async function openDialog() {
  let maps;
  try { maps = await api("GET", "/api/maps"); }
  catch (err) { showProblems(err.errors); return; }

  // grouped by folder, so it is obvious which maps belong to the repo
  const groups = FOLDERS.map(({ value, label }) => {
    const mine = maps.filter((m) => m.folder === value);
    if (!mine.length) return "";
    return `<h3 class="group">${esc(label)}</h3>` + mine.map((m) => `
      <div class="row" data-open="${esc(m.name)}" data-folder="${esc(m.folder)}">
        <span class="grow"><span class="lbl">${esc(m.name)}</span>
          <span class="meta">${m.error ? esc(m.error)
            : `${m.stations} stations · ${m.lines} lines${m.mode === "roadmap" ? " · roadmap" : ""}`}</span></span>
        <button type="button" class="ghost danger" data-del="${esc(m.name)}"
                data-del-folder="${esc(m.folder)}" title="delete">×</button>
      </div>`).join("");
  }).join("");
  const rows = groups || `<p class="note">No maps saved yet.</p>`;

  dialog("Open a map", `<div class="pick-list">${rows}</div>
    <div class="actions"><button value="cancel">Cancel</button></div>`, (form, dlg) => {
    form.querySelectorAll("[data-open]").forEach((row) =>
      row.addEventListener("click", (ev) => {
        if (ev.target.dataset.del !== undefined) return;
        dlg.close();
        loadMap(row.dataset.open, { folder: row.dataset.folder });
      }));
    form.querySelectorAll("[data-del]").forEach((btn) =>
      btn.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        const name = btn.dataset.del;
        const folder = btn.dataset.delFolder;
        const where = (FOLDERS.find((f) => f.value === folder) || {}).label || folder;
        if (!confirm(`Delete “${name}” from ${where}?`)) return;
        try {
          await api("DELETE",
            `/api/maps/${encodeURIComponent(name)}?folder=${encodeURIComponent(folder)}`);
        } catch (err) { showProblems(err.errors); return; }
        dlg.close();
        if (S.name === name && S.folder === folder) {
          S.name = null; S.folder = null; markDirty(); syncToolbar();
        }
        openDialog();
      }));
  });
}

/** Open (or close) a column and a row at a station, shifting what follows. */
function insertSpaceDialog(sid) {
  const anchor = S.spec.stations[sid];
  if (!anchor) return;
  dialog(`Insert space at “${anchor.label}”`, `
    <p class="note">Everything past this station moves. Negative values close a
      gap instead of opening one.</p>
    <div class="pair">
      <label class="field"><span>Shift right by (grid x)</span>
        <input type="number" id="i-dx" step="${S.snap}" value="1"></label>
      <label class="field"><span>Shift down by (grid y)</span>
        <input type="number" id="i-dy" step="${S.snap}" value="0"></label>
    </div>
    <label class="toggle"><input type="checkbox" id="i-self"> move “${esc(anchor.label)}” too</label>
    <div class="actions"><button value="cancel">Cancel</button>
      <button type="button" class="primary" id="i-ok">Insert</button></div>`, (form, dlg) => {
    const go = () => {
      const dx = Number(form.querySelector("#i-dx").value) || 0;
      const dy = Number(form.querySelector("#i-dy").value) || 0;
      dlg.close();
      insertSpace(sid, dx, dy, form.querySelector("#i-self").checked);
    };
    form.querySelector("#i-ok").addEventListener("click", go);
    form.querySelectorAll("input[type=number]").forEach((input) =>
      input.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") { ev.preventDefault(); go(); }
      }));
    form.querySelector("#i-dx").select();
  });
}

function insertSpace(sid, dx, dy, withAnchor) {
  const anchor = S.spec.stations[sid];
  if (!anchor || (!dx && !dy)) return;
  // read the anchor's position before the loop — it may be one of the movers
  const ax = anchor.gx, ay = anchor.gy;
  const past = (v, at) => (withAnchor ? v >= at - 1e-9 : v > at + 1e-9);
  // A rigid shift, not a re-snap: rounding onto S.snap would drag a station
  // deliberately placed on a half cell onto the next whole one, collapsing the
  // spacing the author chose. Round only to keep float noise out of the spec.
  const shift = (v, by) => Math.round((v + by) * 1000) / 1000;
  applyChange(() => {
    // junctions move with the stations: they are points on the same grid, and
    // leaving them behind would drag every route through them out of shape
    const movers = Object.values(S.spec.stations)
      .concat(Object.values(S.spec.junctions || {}));
    for (const st of movers) {
      if (dx && past(st.gx, ax)) st.gx = shift(st.gx, dx);
      if (dy && past(st.gy, ay)) st.gy = shift(st.gy, dy);
    }
  });
}

function saveAsDialog() {
  const into = S.folder || DEFAULT_FOLDER;
  dialog("Save map as", `
    <label class="field"><span>Name (letters, digits, space, - and _)</span>
      <input type="text" id="d-name" value="${esc(S.name || "untitled")}" autofocus></label>
    <label class="field"><span>Into</span><select id="d-folder">
      ${FOLDERS.map((f) =>
        `<option value="${esc(f.value)}" ${f.value === into ? "selected" : ""}>${esc(f.label)}</option>`).join("")}
    </select></label>
    <p class="note">Shared maps belong to the repo; My maps is ignored by git.</p>
    <div class="actions"><button value="cancel">Cancel</button>
      <button type="button" class="primary" id="d-ok">Save</button></div>`, (form, dlg) => {
    const input = form.querySelector("#d-name");
    const go = () => {
      dlg.close();
      saveMap(input.value.trim(), form.querySelector("#d-folder").value);
    };
    form.querySelector("#d-ok").addEventListener("click", go);
    input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); go(); } });
    input.select();
  });
}

/** Version, licence, where this came from, and how to move it forward. */
async function aboutDialog() {
  const repo = "https://github.com/ERP-LAB-5/metro-map-tool";
  dialog("About metro map", `
    <div class="about">
      <img class="about-logo" src="/static/dlab5.png" alt="D-LAB-5"
           width="88" height="88">
      <p class="about-lead">A transit-map drawing tool: stations on a grid, lines
        routed through them, zones banding groups of them.</p>
      <dl class="about-grid">
        <dt>Installed</dt><dd id="a-installed">…</dd>
        <dt>Latest</dt><dd id="a-latest">checking…</dd>
        <dt>Repository</dt>
        <dd><a href="${repo}" target="_blank" rel="noopener noreferrer">${repo.replace("https://", "")}</a></dd>
        <dt>Licence</dt><dd><a href="https://www.gnu.org/licenses/gpl-3.0.html" target="_blank" rel="noopener noreferrer">GPL-3.0-or-later</a></dd>
      </dl>
      <p class="note" id="a-note"></p>
      <pre class="about-log" id="a-log" hidden></pre>
      <p class="about-foot">© 2026 D-LAB-5 — Twin. Experiment. Automate.<br>
        <a href="https://www.buymeacoffee.com/dlab5" target="_blank"
           rel="noopener noreferrer">☕ Buy me a coffee</a></p>
    </div>
    <div class="actions">
      <button type="button" id="a-update" hidden>Update and restart</button>
      <button value="cancel" class="primary">Close</button>
    </div>`);

  let info;
  try { info = await api("GET", "/api/version"); }
  catch (_) { info = null; }
  const installed = $("#a-installed");
  if (!installed) return;                       // dialog closed while we asked
  const latest = $("#a-latest");
  const note = $("#a-note");

  if (!info) {
    installed.textContent = "unknown";
    latest.textContent = "—";
    note.textContent = "The designer did not answer.";
    return;
  }
  installed.textContent = info.installed;
  if (info.disabled) {
    latest.textContent = "not checked";
    note.textContent = "The update check is off (--no-update-check).";
  } else if (info.offline || !info.latest) {
    latest.textContent = "unknown";
    note.textContent = "Could not reach github.com — offline, or behind a proxy.";
  } else if (info.update_available) {
    latest.innerHTML = `<strong>${esc(info.latest)}</strong> — `
      + `<a href="${esc(info.releases)}" target="_blank" rel="noopener noreferrer">release notes</a>`;
    // pip can only upgrade what pip installed; a checkout is git's business
    if (info.install === "installed") {
      note.textContent = `You are on ${info.installed}. Updating runs pip, then restarts.`;
      const btn = $("#a-update");
      btn.hidden = false;
      btn.className = "warn-btn";
      btn.addEventListener("click", () => runUpdate(btn));
    } else {
      note.textContent = `You are on ${info.installed}, running from a checkout — `
        + "update it with git pull, then press Restart.";
    }
  } else {
    latest.textContent = info.latest;
    note.textContent = "Up to date.";
  }
}

/** Ask the server to pip-upgrade itself, then offer the restart that lands it. */
async function runUpdate(btn) {
  const note = $("#a-note");
  const log = $("#a-log");
  btn.disabled = true;
  btn.textContent = "Updating…";
  note.textContent = "Running pip. This can take a minute.";
  let out;
  try { out = await api("POST", "/api/update"); }
  catch (err) { out = (err.data && err.data.output) ? err.data : { ok: false, output: err.errors.join("\n") }; }
  if (log) {
    log.hidden = false;
    log.textContent = out.output || "";
  }
  if (!out.ok) {
    btn.disabled = false;
    btn.textContent = "Try again";
    note.textContent = "The update did not go through — nothing has changed.";
    return;
  }
  // the files are new; only a restart is running them
  note.textContent = "Updated. Restarting to run the new version.";
  btn.textContent = "Restarting…";
  restartTheServer();
}

/** Start over: an empty grid, unnamed until it is saved. */
function newMap() {
  if (!confirmDiscard()) return;
  S.name = null;
  S.folder = null;
  S.version = null;
  S.ignoreVersion = null;
  TIMELINE = null;
  hideLive();
  S.spec = normalise({});
  S.style = { ...DEFAULT_STYLE };
  S.snap = 1;
  S.sel = { kind: null, id: null };
  S.undo.length = 0;
  S.redo.length = 0;
  S.zoom = 1;
  S.pan = { x: 0, y: 0 };
  $("#style-editor").dataset.built = "";
  $("#timeline-editor").dataset.built = "";
  markDirty();
  syncMode();
  refreshPanels();
  scheduleRender();
  currentTab() === "stations" || document.querySelector('.tab[data-tab="stations"]').click();
  setHint();
}

/* ------------------------------------------------------------------ io -- */

function confirmDiscard() {
  return !S.dirty || confirm("This map has unsaved changes. Discard them?");
}

async function loadMap(name, { force = false, folder = null } = {}) {
  if (!force && !confirmDiscard()) return;
  const where = folder ? `?folder=${encodeURIComponent(folder)}` : "";
  let data;
  try { data = await api("GET", `/api/maps/${encodeURIComponent(name)}${where}`); }
  catch (err) { showProblems(err.errors); return; }
  S.name = data.name;
  S.folder = data.folder || DEFAULT_FOLDER;
  S.version = data.version || null;
  S.ignoreVersion = null;
  S.spec = normalise(data.spec);
  S.style = { ...DEFAULT_STYLE, ...(data.spec.style || {}) };
  S.snap = Number((data.spec.editor || {}).snap) || 1;
  S.sel = { kind: null, id: null };
  S.undo.length = 0; S.redo.length = 0;
  $("#timeline-editor").dataset.built = "";
  rememberMap(S.name, S.folder);
  markClean();
  hideLive();
  syncMode();
  refreshPanels();
  scheduleRender();
  setTimeout(fitToView, 250);
  startWatching();
}

/** Ask before a Save-as lands on a map that already exists. True to go ahead. */
async function confirmOverwrite(name, folder) {
  let maps;
  try { maps = await api("GET", "/api/maps"); }
  catch (_) { return true; }         // cannot check; the save itself will report
  const hit = maps.find((m) => m.name === name && m.folder === folder);
  if (!hit) return true;
  const where = (FOLDERS.find((f) => f.value === folder) || {}).label || folder;
  return confirm(`“${name}” already exists in ${where}`
    + ` (${hit.stations} stations, ${hit.lines} lines).\n\nReplace it?`);
}

async function saveMap(name, folder) {
  name = name || S.name;
  if (!name) { saveAsDialog(); return; }
  // no folder given means "back where it came from", so editing a shared map
  // updates it rather than quietly forking a copy into mymaps
  folder = folder || S.folder || DEFAULT_FOLDER;
  const spec = clone(S.spec);
  spec.style = { ...S.style };
  spec.editor = { snap: S.snap };
  const body = { spec, auto_interchange: S.autoIx, folder };
  const sameMap = name === S.name && folder === S.folder;
  if (sameMap && S.version) body.base_version = S.version;
  // Saving under a different name or into the other folder carries no base
  // version, so the server's concurrency check cannot fire — nothing else
  // stands between "Save as" and a map of the same name already sitting there.
  if (!sameMap && !(await confirmOverwrite(name, folder))) return;
  try {
    const data = await api("PUT", `/api/maps/${encodeURIComponent(name)}`, body);
    S.name = data.name;
    S.folder = data.folder || folder;
    S.version = data.version || null;
    S.ignoreVersion = null;
    S.spec = normalise(data.spec);
    delete S.spec.style;               // the editor keeps style and snap outside the spec
    delete S.spec.editor;
    rememberMap(S.name, S.folder);
    markClean();
    showProblems([]);
    refreshPanels();
    scheduleRender();
  } catch (err) {
    if (err.status === 409) {
      // someone saved in between; let the human decide, never merge silently
      showLive(`“${name}” was saved by someone else while you were editing.`, [
        { label: "Load theirs (lose mine)",
          fn: async () => {
            S.dirty = false;
            await loadMap(name, { force: true, folder });
          } },
        { label: "Overwrite theirs", cls: "primary",
          fn: async () => {
            S.version = (err.data && err.data.version) || null;   // adopt, then win
            S.folder = folder;                     // so the retry counts as the same map
            hideLive();
            await saveMap(name, folder);
          } },
      ]);
      return;
    }
    showProblems(err.errors);
  }
}

async function exportSVG() {
  if (!Object.keys(S.spec.stations).length) {
    showProblems(["nothing rendered to export yet"]);
    return;
  }
  // Re-render rather than reuse the preview: the preview is baked to whatever
  // theme the toolbar is showing, and a file that leaves here has to keep both
  // palettes so it suits whoever opens it.
  let svg;
  try {
    const data = await api("POST", "/api/render", {
      spec: S.spec, style: S.style, auto_interchange: S.autoIx, theme: "auto",
    });
    svg = data.svg;
  } catch (err) { showProblems(err.errors); return; }
  if (!svg) { showProblems(["nothing rendered to export yet"]); return; }
  const blob = new Blob([svg], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${S.name || "map"}.svg`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function guardUnload(ev) {
  if (S.dirty) { ev.preventDefault(); ev.returnValue = ""; }
}

/* Stopping and restarting both take the server away, and the map lives in this
   browser until it is written to disk. So both ask, and both offer to save
   rather than only warning — a confirm() can say "lose them or cancel" and
   nothing else, which is why these are dialogs. */

/** Ask before an action that would take unsaved work with it. */
function confirmLeaving(verb, go) {
  const Verb = verb[0].toUpperCase() + verb.slice(1);
  if (!S.dirty) {
    dialog(`${Verb} the designer?`, `
      <p class="note">${verb === "restart"
        ? "The server starts again on the same port and the page reloads."
        : "This shuts down the local server."} Everything you have saved stays
        in your maps folder.</p>
      <div class="actions">
        <button value="cancel">Cancel</button>
        <button type="button" class="${verb === "restart" ? "primary" : "danger"}" id="q-go">${Verb}</button>
      </div>`, (form, dlg) =>
      form.querySelector("#q-go").addEventListener("click", () => { dlg.close(); go(); }));
    return;
  }

  const named = !!S.name;
  dialog(`${Verb} the designer?`, `
    <p class="note"><b>“${esc(S.name || "untitled")}” has unsaved changes.</b>
      They are only in this browser — ${verb === "restart"
        ? "restarting reloads the page and loses them"
        : "stopping the server loses them for good"}.</p>
    <div class="actions">
      <button value="cancel">Cancel</button>
      <button type="button" class="danger" id="q-discard">${Verb} without saving</button>
      <button type="button" class="primary" id="q-save">${named ? `Save and ${verb}` : "Name it and save…"}</button>
    </div>`, (form, dlg) => {
    form.querySelector("#q-discard").addEventListener("click", () => { dlg.close(); go(); });
    form.querySelector("#q-save").addEventListener("click", async () => {
      dlg.close();
      await saveMap();
      // saveMap leaves us dirty when it could not finish: a name still to give,
      // a validation error, or someone else's save in the way. Never go then.
      if (S.dirty) {
        showProblems([named
          ? `Did not ${verb} — the save did not go through. Deal with that, then try again.`
          : `Did not ${verb} — give the map a name and save it, then try again.`]);
        return;
      }
      go();
    });
  });
}

function stopServer() { confirmLeaving("stop", shutDownServer); }
function restartServer() { confirmLeaving("restart", restartTheServer); }

function shutDownServer() {
  // the server answers, then exits — a failed fetch here is the expected ending
  fetch("/api/shutdown", { method: "POST", headers: { "Content-Type": "application/json" } })
    .catch(() => {})
    .finally(() => {
      window.removeEventListener("beforeunload", guardUnload);
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
      document.body.innerHTML =
        `<div class="stopped"><h1>Designer stopped</h1>
         <p>The local server has shut down. Run <code>./run.sh</code>
         (or <code>run.cmd</code> / <code>run.ps1</code> on Windows) to start it
         again.</p></div>`;
    });
}

/** Ask the server to replace itself, then reload once it answers again. */
async function restartTheServer() {
  window.removeEventListener("beforeunload", guardUnload);
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  document.body.innerHTML =
    `<div class="stopped"><h1>Restarting…</h1>
     <p id="restart-note">Waiting for the designer to come back.</p></div>`;
  fetch("/api/restart", { method: "POST", headers: { "Content-Type": "application/json" } })
    .catch(() => {});   // the server may die mid-answer, which is the point

  // it has to go away and come back; polling from the start could catch the old
  // one still answering, so wait past the moment it replaces itself
  await new Promise((r) => setTimeout(r, 900));
  for (let tries = 0; tries < 40; tries += 1) {
    try {
      const res = await fetch("/api/maps", { cache: "no-store" });
      if (res.ok) { location.reload(); return; }
    } catch (_) { /* still down */ }
    await new Promise((r) => setTimeout(r, 400));
  }
  const note = document.getElementById("restart-note");
  if (note) {
    note.textContent = "It did not come back. Start it again with ./run.sh "
      + "(or run.cmd on Windows).";
  }
}

/* ----------------------------------------------------------------- live -- */

const POLL_MS = 2000;
let pollTimer = null;

/** Watch the open map for saves made elsewhere — another tab, or an agent. */
function startWatching() {
  if (pollTimer) return;
  pollTimer = setInterval(checkForExternalSave, POLL_MS);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) checkForExternalSave();      // catch up on refocus
  });
}

async function checkForExternalSave() {
  if (!S.name || document.hidden) return;
  let maps;
  try { maps = await api("GET", "/api/maps"); }
  catch (_) { return; }                    // server stopped or restarting; try later
  const mine = maps.find((m) => m.name === S.name && m.folder === S.folder);
  if (!mine || !mine.version) return;
  if (mine.version === S.version || mine.version === S.ignoreVersion) return;

  if (!S.dirty) {
    await loadMap(S.name, { force: true, folder: S.folder });
    flashLive(`“${S.name}” was updated elsewhere — reloaded.`);
    return;
  }
  showLive(`“${S.name}” changed on disk while you have unsaved edits.`, [
    { label: "Load theirs", cls: "primary",
      fn: async () => {
        S.dirty = false;
        await loadMap(S.name, { force: true, folder: S.folder });
      } },
    { label: "Keep mine", fn: () => { S.ignoreVersion = mine.version; hideLive(); } },
  ]);
}

function showLive(message, actions) {
  const bar = $("#live");
  bar.hidden = false;
  bar.innerHTML = `<span class="grow">${esc(message)}</span>`;
  for (const a of actions || []) {
    const btn = document.createElement("button");
    btn.textContent = a.label;
    if (a.cls) btn.className = a.cls;
    btn.addEventListener("click", a.fn);
    bar.appendChild(btn);
  }
}

function hideLive() {
  const bar = $("#live");
  bar.hidden = true;
  bar.innerHTML = "";
}

function flashLive(message) {
  showLive(message, [{ label: "Dismiss", fn: hideLive }]);
  setTimeout(() => { if ($("#live").textContent.startsWith(message.slice(0, 12))) hideLive(); }, 6000);
}

/* ------------------------------------------------------------ undo/redo -- */

function undo() {
  if (!S.undo.length) return;
  S.redo.push(snapshot());
  restore(S.undo.pop());
  markDirty();
  syncMode();                    // mode and timeline ride on the spec
  refreshPanels();
  scheduleRender();
}

function redo() {
  if (!S.redo.length) return;
  S.undo.push(snapshot());
  restore(S.redo.pop());
  markDirty();
  syncMode();
  refreshPanels();
  scheduleRender();
}

/* ------------------------------------------------------------- keyboard -- */

function initKeys() {
  document.addEventListener("keydown", (ev) => {
    const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(ev.target.tagName);
    const mod = ev.ctrlKey || ev.metaKey;

    if (mod && ev.key.toLowerCase() === "z") {
      ev.preventDefault();
      ev.shiftKey ? redo() : undo();
      return;
    }
    if (mod && ev.key.toLowerCase() === "y") { ev.preventDefault(); redo(); return; }
    if (mod && ev.key.toLowerCase() === "s") { ev.preventDefault(); saveMap(); return; }
    if (mod && ev.key === "\\") { ev.preventDefault(); applySide(!S.sideHidden); return; }
    if (typing) return;

    if (S.sel.kind === "station" && S.spec.stations[S.sel.id]) {
      const st = S.spec.stations[S.sel.id];
      const nudge = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[ev.key];
      if (nudge) {
        ev.preventDefault();
        applyChange(() => {
          st.gx = snapTo(st.gx + nudge[0] * (S.snap || 1));
          st.gy = snapTo(st.gy + nudge[1] * (S.snap || 1));
        });
        return;
      }
      if (ev.key === "Delete" || ev.key === "Backspace") {
        ev.preventDefault();
        deleteStation(S.sel.id);
        return;
      }
    }
    if (ev.key === "Escape") select(null, null);
  });
}

/* ---------------------------------------------------------------- boot -- */

async function boot() {
  applyTheme(storedTheme());          // before anything paints, to avoid a flash
  applySide(storedSide());
  try {
    const [defaults, palette] = await Promise.all([
      api("GET", "/api/defaults"), api("GET", "/api/palette"),
    ]);
    DEFAULT_STYLE = defaults.style;
    LABEL_SIDES = defaults.label_sides;
    LINE_STATUSES = defaults.line_statuses || LINE_STATUSES;
    LABEL_ANGLES = defaults.label_angles || LABEL_ANGLES;
    MODES = defaults.modes || MODES;
    INTERVALS = defaults.intervals || INTERVALS;
    FOLDERS = defaults.folders || FOLDERS;
    DEFAULT_FOLDER = defaults.default_folder || DEFAULT_FOLDER;
    LEGEND_POSITIONS = defaults.legend_positions || LEGEND_POSITIONS;
    CONTINUES = defaults.continues || CONTINUES;
    DEAD_ENDS = defaults.dead_ends || DEAD_ENDS;
    DEFAULT_LEGEND = defaults.default_legend || DEFAULT_LEGEND;
    PALETTE = palette;
    S.style = { ...defaults.style };
  } catch (err) {
    showProblems(err.errors || ["could not reach the server"]);
  }

  $("#mode-select").innerHTML = MODES.map((m) =>
    `<option value="${esc(m.value)}">${esc(m.label)}</option>`).join("");
  $("#mode-select").addEventListener("change", (ev) => setMode(ev.target.value));
  $("#theme-select").addEventListener("change", (ev) => {
    applyTheme(ev.target.value);
    scheduleRender();                 // the preview is drawn in the theme, not styled into it
  });
  // while on auto, follow the system if it changes under us
  if (window.matchMedia) {
    window.matchMedia("(prefers-color-scheme: dark)")
      .addEventListener("change", () => { if (S.theme === "auto") scheduleRender(); });
  }

  initTabs();
  initCanvas();
  initKeys();
  syncMode();

  $("#btn-add-station").addEventListener("click", addStation);
  $("#btn-add-junction").addEventListener("click", addJunction);
  $("#btn-import").addEventListener("click", importDialog);
  $("#btn-settings").addEventListener("click", () => settingsDialog());
  $("#btn-add-line").addEventListener("click", addLine);
  $("#btn-add-zone").addEventListener("click", addZone);
  $("#btn-add-scenario").addEventListener("click", addScenario);
  $("#btn-add-join").addEventListener("click", addJoin);
  $("#btn-milestone").addEventListener("click", milestoneDialog);
  $("#btn-ride-play").addEventListener("click", toggleRides);
  $("#btn-ride-restart").addEventListener("click", restartRides);
  $("#btn-new").addEventListener("click", newMap);
  $("#btn-open").addEventListener("click", openDialog);
  $("#btn-save").addEventListener("click", () => saveMap());
  $("#btn-saveas").addEventListener("click", saveAsDialog);
  $("#btn-export").addEventListener("click", exportSVG);
  $("#btn-stop").addEventListener("click", stopServer);
  $("#btn-restart").addEventListener("click", restartServer);
  $("#btn-about").addEventListener("click", aboutDialog);
  $("#btn-side").addEventListener("click", () => applySide(!S.sideHidden));
  $("#btn-undo").addEventListener("click", undo);
  $("#btn-redo").addEventListener("click", redo);
  $("#btn-style-reset").addEventListener("click", () => {
    pushUndo();
    S.style = { ...DEFAULT_STYLE };
    $("#style-editor").dataset.built = "";
    markDirty();
    refreshPanels();
    scheduleRender();
  });
  $("#auto-ix").addEventListener("change", (ev) => {
    S.autoIx = ev.target.checked;
    scheduleRender();
  });
  $("#zoom-in").addEventListener("click", () => zoomBy(1.2));
  $("#zoom-out").addEventListener("click", () => zoomBy(1 / 1.2));
  $("#zoom-fit").addEventListener("click", fitToView);

  window.addEventListener("beforeunload", guardUnload);

  refreshPanels();
  setHint();

  // Start on whatever this browser had open, else the map that explains the
  // tool, else the most recently touched one — never on a blank canvas.
  try {
    const maps = await api("GET", "/api/maps");
    if (maps.length) {
      const last = rememberedMap();
      const pick = (last && maps.find((m) => m.name === last.name
                     && (!last.folder || m.folder === last.folder)))
        || maps.find((m) => m.name === GUIDE_MAP)
        || maps.slice().sort((a, b) => b.mtime - a.mtime)[0];
      await loadMap(pick.name, { folder: pick.folder });
      return;
    }
  } catch (_) { /* fall through to an empty workspace */ }
  scheduleRender();
}

boot();
