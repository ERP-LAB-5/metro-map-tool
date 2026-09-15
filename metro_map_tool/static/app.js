/* SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) 2026 ERP-LAB-5
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
  sideHidden: false,
  ridesPlaying: true,                      // the travellers run on their own
  showRides: true,                         // a view choice, never saved with the map
  rides: [],                               // each ride as the server routed it
  filters: {},                             // search text per list, kept across re-renders
  snap: 1,                                 // grid step when dragging or nudging

  sel: { kind: null, id: null },           // "station" | "junction" | "line" | ...
  branch: null,                            // index into the open line's branches,
                                           // or null while its trunk is being edited
  dirty: false,
  version: null,                           // content hash of the map as loaded
  ignoreVersion: null,                     // an external change we chose to keep out
  locked: null,                            // {holder, seconds_left} while an agent works
  sideBeforeLock: null,                    // the panel as it was when the lock came
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
const SIDE_KEY = "metro-map:side";

/* ---------------------------------------------------------- side panel -- */

/** Fold the panel away for a wider canvas. Remembered per browser. */
function applySide(hidden, { remember = true } = {}) {
  S.sideHidden = !!hidden;
  document.body.classList.toggle("side-hidden", S.sideHidden);
  // a lock folding the panel away is not the person's preference, so it is not
  // stored: a reload mid-lock must not leave the panel hidden for good
  if (remember) {
    try { localStorage.setItem(SIDE_KEY, S.sideHidden ? "1" : "0"); } catch (_) { /* none */ }
  }
  const btn = $("#btn-side");
  if (btn) btn.setAttribute("aria-expanded", String(!S.sideHidden));
}

function storedSide() {
  try { return localStorage.getItem(SIDE_KEY) === "1"; } catch (_) { return false; }
}

function resolvedTheme() {
  // The header's picker is core.js's, and it records its choice on <html>.
  // "auto" leaves no attribute, so the system preference decides.
  const chosen = document.documentElement.dataset.theme;
  if (chosen === "light" || chosen === "dark") return chosen;
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
  spec.swimlanes = spec.swimlanes || [];
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

/** While an agent holds the open map, say so instead of changing it. True if paused. */
function pausedForAgent() {
  if (!S.locked) return false;
  flashLive("An agent is updating this map — take it over to edit.");
  return true;
}

/** The one door every mutation goes through. */
function applyChange(fn, { undo = true } = {}) {
  if (pausedForAgent()) return;
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
  S.rides = data.rides || [];
  applyTransform();
  decorate();
  applyRideState();
  renderRideRoute();           // the resolved route changes with the map, not the editor
  navAfterRender();
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
  if (NAV.ride !== null && NAV.follow) return;     // the camera is following a ride
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
    // while an agent holds the map the canvas can still be panned, not edited
    if (hit && waypoint(hitId) && !S.locked) {
      const id = hitId;
      const g = gridAt(ev);
      const st = waypoint(id);
      if (!g) return;
      drag = { id, offGX: st.gx - g.gx, offGY: st.gy - g.gy, gx: st.gx, gy: st.gy, moved: false };
      pushUndo();                       // dropped again on pointerup if nothing moved
    } else {
      pan = { x0: ev.clientX, y0: ev.clientY, px: S.pan.x, py: S.pan.y };
      if (NAV.ride !== null) NAV.follow = false;   // looking around: stop steering the camera
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
    if (NAV.ride !== null) NAV.follow = false;
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
  if (isJunction && tab === "scenarios" && S.sel.kind === "scenario"
      && S.spec.scenarios[S.sel.id] && isRouted(S.spec.scenarios[S.sel.id])) {
    rideClick(S.spec.scenarios[S.sel.id], id);      // a junction picks a branch
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
  if (tab === "lanes" && S.sel.kind === "lane" && S.spec.swimlanes[S.sel.id]) {
    stretchLane(S.sel.id, S.spec.stations[id].gy);
    return;
  }
  if (tab === "scenarios" && S.sel.kind === "scenario" && S.spec.scenarios[S.sel.id]) {
    rideClick(S.spec.scenarios[S.sel.id], id);
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
  } else if (tab === "lanes" && S.sel.kind === "lane" && S.spec.swimlanes[S.sel.id]) {
    text = `Shaping “${S.spec.swimlanes[S.sel.id].name}” — click a station to stretch the lane over its row`;
  } else if (tab === "lanes") {
    text = "Pick a lane to set its rows, or add one for each key area";
  } else if (tab === "scenarios" && S.sel.kind === "scenario" && S.spec.scenarios[S.sel.id]) {
    const sc = S.spec.scenarios[S.sel.id];
    text = !isRouted(sc) ? `Routing “${sc.name}” — click stations in the order the traveller visits them`
      : sc.from === undefined ? `“${sc.name}” — click the station where the ride starts`
      : sc.to === undefined ? `“${sc.name}” — click the station where the ride ends`
      : `“${sc.name}” — click a station or junction to route the ride via it`;
  } else if (tab === "scenarios" && NAV.ride !== null) {
    text = "Following a ride — Space pauses · ← → previous and next stop · + − zoom · Esc stops";
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
  renderLanes();
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
  searchable(list, "stations");
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
  searchable($("#line-list"), "lines");
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
  searchable($("#l-pick"), "line-stops", { items: "button", min: 12 });
  searchable($("#l-pick-j"), "line-junctions", { items: "button", min: 12 });
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

/* -------------------------------------------------------------- search -- */

/* Maps grow, and a list of sixty stations to find one in is a chore. Every
   long list gets a box that filters it as you type. The text is kept per list
   in S.filters, because these lists are rebuilt after every edit and a search
   that emptied itself on each click would be worse than none. */

function searchable(list, key, { items = ".row", min = 8 } = {}) {
  if (!list) return;
  // the list was just rebuilt: its old box filters rows that no longer exist
  const old = list.previousElementSibling;
  const typing = old && old.classList.contains("search")
    && old.querySelector("input") === document.activeElement;
  if (old && old.classList.contains("search")) old.remove();
  const all = [...list.querySelectorAll(items)];
  if (all.length < min && !S.filters[key]) return;
  const box = document.createElement("div");
  box.className = "search";
  box.innerHTML = `<input type="search" placeholder="Search ${all.length}…"
      value="${esc(S.filters[key] || "")}" spellcheck="false" aria-label="Search this list">
    <span class="search-count"></span>`;
  list.before(box);
  const input = box.querySelector("input");
  const count = box.querySelector(".search-count");
  const apply = () => {
    const q = input.value.trim().toLowerCase();
    S.filters[key] = input.value;
    let shown = 0;
    for (const el of all) {
      const text = `${el.textContent} ${el.dataset.search || ""}`.toLowerCase();
      el.hidden = !!q && !text.includes(q);
      // an open editor sits in the row after its own, and goes with it
      const host = el.nextElementSibling;
      if (host && host.classList.contains("edit-host")) host.hidden = el.hidden;
      if (!el.hidden) shown += 1;
    }
    count.textContent = q ? `${shown} of ${all.length}` : "";
  };
  input.addEventListener("input", apply);
  if (typing) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") { input.value = ""; apply(); }
    if (ev.key === "Enter") {
      ev.preventDefault();
      const left = all.filter((el) => !el.hidden);
      if (left.length === 1) left[0].click();
    }
  });
  apply();
}

/* ----------------------------------------------------------- swimlanes -- */

function renderLanes() {
  const list = $("#lane-list");
  if (!list) return;
  const lanes = S.spec.swimlanes;
  if (!lanes.length) {
    list.innerHTML = `<li class="note">No lanes yet — press “+ Add” to band the rows of a key area.</li>`;
  } else {
    list.innerHTML = lanes.map((ln, i) => {
      const open = S.sel.kind === "lane" && S.sel.id === i;
      const [a, b] = ln.rows || [0, 0];
      const inside = Object.values(S.spec.stations)
        .filter((st) => st.gy >= a - 0.5 && st.gy <= b + 0.5).length;
      return `<li class="row ${open ? "is-on" : ""}" data-lane="${i}">
        <span class="swatch band" style="background:${esc(ln.color || "#8f9aa4")}"></span>
        <span class="grow">
          <span class="lbl">${esc(ln.name)}</span>
          <span class="at">rows ${a}–${b} · ${inside} station${inside === 1 ? "" : "s"}</span>
        </span>
        <span class="caret">${open ? "▾" : "▸"}</span>
      </li>` + (open ? `<li class="edit-host"><div id="lane-editor" class="editor"></div></li>` : "");
    }).join("");
    list.querySelectorAll("[data-lane]").forEach((row) =>
      row.addEventListener("click", () => select("lane", Number(row.dataset.lane))));
  }
  searchable(list, "lanes");
  renderLaneEditor();
}

function renderLaneEditor() {
  const box = $("#lane-editor");
  if (!box) return;
  if (S.sel.kind !== "lane" || !S.spec.swimlanes[S.sel.id]) { box.innerHTML = ""; return; }
  const i = S.sel.id;
  const ln = S.spec.swimlanes[i];
  const [a, b] = ln.rows || [0, 0];
  box.innerHTML = `<div class="card">
    <h3>Swimlane</h3>
    <label class="field"><span>Name</span><input type="text" id="sl-name" value="${esc(ln.name)}"></label>
    <div class="field"><span>Colour</span>
      <div class="swatches">
        <button type="button" data-color="" title="no tint — alternate light bands"
          class="${ln.color ? "" : "is-on"} swatch-none">∅</button>
        ${PALETTE.map((pl) =>
          `<button type="button" data-color="${esc(pl.color)}" title="${esc(pl.name)}"
             style="background:${esc(pl.color)}" class="${(ln.color || "").toLowerCase() === pl.color.toLowerCase() ? "is-on" : ""}"></button>`).join("")}
      </div>
    </div>
    <div class="pair">
      <label class="field"><span>First row</span>
        <input type="number" id="sl-from" step="0.5" value="${a}"></label>
      <label class="field"><span>Last row</span>
        <input type="number" id="sl-to" step="0.5" value="${b}"></label>
    </div>
    <h3>Fit to a line</h3>
    <div class="pick" id="sl-pick">
      ${S.spec.lines.map((line, li) => `<button type="button" data-fit="${li}"
         style="border-color:${esc(line.color)}">${esc(line.name)}</button>`).join("")
        || `<p class="note">No lines yet.</p>`}
    </div>
    <div><button id="sl-del" class="danger">Delete lane</button></div>
  </div>`;

  const name = $("#sl-name");
  name.addEventListener("focus", pushUndo);
  name.addEventListener("input", () => { ln.name = name.value; markDirty(); scheduleRender(); });
  name.addEventListener("change", refreshPanels);
  box.querySelectorAll("[data-color]").forEach((btn) =>
    btn.addEventListener("click", () => applyChange(() => {
      if (btn.dataset.color) ln.color = btn.dataset.color; else delete ln.color;
    })));
  const rows = () => {
    const from = Number($("#sl-from").value);
    const to = Number($("#sl-to").value);
    if (!Number.isFinite(from) || !Number.isFinite(to)) return;
    applyChange(() => { ln.rows = [Math.min(from, to), Math.max(from, to)]; });
  };
  $("#sl-from").addEventListener("change", rows);
  $("#sl-to").addEventListener("change", rows);
  box.querySelectorAll("[data-fit]").forEach((btn) => btn.addEventListener("click", () => {
    const line = S.spec.lines[Number(btn.dataset.fit)];
    const ys = strandsOf(line).flatMap((st) => st.ids)
      .map((id) => waypoint(id)).filter(Boolean).map((p) => p.gy);
    if (!ys.length) return;
    applyChange(() => { ln.rows = [Math.min(...ys), Math.max(...ys)]; });
  }));
  searchable($("#sl-pick"), "lane-fit", { items: "button", min: 12 });
  $("#sl-del").addEventListener("click", () => applyChange(() => {
    S.spec.swimlanes.splice(i, 1);
    S.sel = { kind: null, id: null };
  }));
  setHint();
}

/** Widen a lane so it takes in a row, leaving it as it is if it already does. */
function stretchLane(i, gy) {
  const ln = S.spec.swimlanes[i];
  if (!ln || !Number.isFinite(gy)) return;
  const [a, b] = ln.rows || [gy, gy];
  if (gy >= a && gy <= b) return;
  applyChange(() => { ln.rows = [Math.min(a, gy), Math.max(b, gy)]; });
}

function addLane() {
  const lanes = S.spec.swimlanes;
  const i = lanes.length;
  const ys = Object.values(S.spec.stations).map((st) => st.gy);
  const after = lanes.length ? Math.max(...lanes.map((ln) => ln.rows[1])) + 1
    : (ys.length ? Math.min(...ys) : 0);
  const selected = S.sel.kind === "station" && S.spec.stations[S.sel.id];
  const row = selected ? selected.gy : after;
  applyChange(() => {
    lanes.push({ name: `Lane ${i + 1}`, rows: [row, row] });
    S.sel = { kind: "lane", id: i };
  });
  document.querySelector('.tab[data-tab="lanes"]').click();
  const name = $("#sl-name");
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
  searchable($("#zone-list"), "zones");
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
  searchable($("#z-pick"), "zone-stations", { items: "button", min: 12 });
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
  searchable($("#join-list"), "joins");
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
  searchable($("#j-pick"), "join-stations", { items: "button", min: 12 });
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
  const svg = svgEl();
  if (svg) svg.classList.toggle("rides-off", !S.showRides);
  const eye = $("#btn-ride-show");
  if (eye) {
    eye.classList.toggle("is-off", !S.showRides);
    eye.title = S.showRides ? "hide every traveller on the canvas (not saved)"
      : "show the travellers again";
  }
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
    list.innerHTML = `<li class="note">No rides yet — press “+ Add”, then click where the traveller starts and where it ends.</li>`;
  } else {
    list.innerHTML = S.spec.scenarios.map((sc, i) => {
      const open = S.sel.kind === "scenario" && S.sel.id === i;
      const report = rideReport(i);
      const stops = report ? report.stops.filter((x) => !x.jump).length : 0;
      const where = sc.from !== undefined || sc.to !== undefined || isRouted(sc)
        ? `${pointLabel(sc.from) || "start?"} → ${pointLabel(sc.to) || "end?"}`
        : "hand-picked stops";
      const ok = report && report.d;
      return `<li class="row ${open ? "is-on" : ""} ${sc.hidden ? "is-hidden" : ""}" data-scenario="${i}"
             data-search="${esc([sc.name, pointLabel(sc.from), pointLabel(sc.to)].join(" "))}">
        <span class="swatch" style="background:${esc(sc.color || "#101820")}"></span>
        <span class="grow">
          <span class="lbl">${esc(sc.name)}</span>
          <span class="at">${esc(where)}${ok ? ` · ${stops} stop${stops === 1 ? "" : "s"}` : ""}</span>
        </span>
        <button type="button" class="ghost" data-eye="${i}"
          title="${sc.hidden ? "show this ride" : "hide this ride"}">${sc.hidden ? "◌" : "👁"}</button>
        <button type="button" class="ghost" data-nav="${i}" ${ok && !sc.hidden ? "" : "disabled"}
          title="follow this ride like a satnav">▶</button>
        <span class="caret">${open ? "▾" : "▸"}</span>
      </li>` + (open ? `<li class="edit-host"><div id="scenario-editor" class="editor"></div></li>` : "");
    }).join("");
    list.querySelectorAll("[data-scenario]").forEach((row) =>
      row.addEventListener("click", () => select("scenario", Number(row.dataset.scenario))));
    list.querySelectorAll("[data-eye]").forEach((btn) => btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const sc = S.spec.scenarios[Number(btn.dataset.eye)];
      applyChange(() => { if (sc.hidden) delete sc.hidden; else sc.hidden = true; });
    }));
    list.querySelectorAll("[data-nav]").forEach((btn) => btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      navStart(Number(btn.dataset.nav));
    }));
  }
  searchable(list, "rides");
  renderScenarioEditor();
}

function isRouted(sc) {
  return ["from", "to", "via", "pass", "dwell", "hidden"].some((k) => k in sc);
}

function rideReport(i) {
  return (S.rides || []).find((r) => r.index === i) || null;
}

/** Where a ride starts or ends, in words. */
function pointLabel(point) {
  if (point === undefined || point === null) return "";
  if (typeof point === "string") {
    if (S.spec.stations[point]) return S.spec.stations[point].label || point;
    if ((S.spec.junctions || {})[point]) return `junction ${point}`;
    return point;
  }
  return `${point.edge === "start" ? "←" : "→"} ${point.line} past the map`;
}

/** Everything a ride may start at, end at or pass through, for a picker. */
function ridePoints() {
  const out = [];
  S.spec.lines.forEach((ln) => {
    strandsOf(ln).forEach((st) => {
      const at = st.owner.continues || "none";
      if (["start", "both"].includes(at) && st.ids.length) {
        out.push({ value: { line: ln.name, edge: "start" }, label: `← ${ln.name} past the map`, kind: "edge" });
      }
      if (["end", "both"].includes(at) && st.ids.length) {
        out.push({ value: { line: ln.name, edge: "end" }, label: `→ ${ln.name} past the map`, kind: "edge" });
      }
    });
  });
  const seen = new Set();
  const uniq = out.filter((p) => {
    const k = JSON.stringify(p.value);
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
  const ids = Object.keys(S.spec.stations).sort((a, b) =>
    (S.spec.stations[a].label || a).localeCompare(S.spec.stations[b].label || b));
  return uniq
    .concat(ids.map((id) => ({ value: id, label: S.spec.stations[id].label || id, kind: "station", search: id })))
    .concat(Object.keys(S.spec.junctions || {}).map((id) => ({ value: id, label: `junction ${id}`, kind: "junction", search: id })));
}

/** A small searchable menu of ride points, opened under a button. */
function pickPoint(anchor, onPick) {
  document.querySelectorAll(".point-menu").forEach((m) => m.remove());
  const menu = document.createElement("div");
  menu.className = "point-menu";
  const points = ridePoints();
  menu.innerHTML = `<div class="pick point-list">${points.map((p, k) =>
    `<button type="button" data-k="${k}" class="kind-${p.kind}" data-search="${esc(p.search || "")}">${esc(p.label)}</button>`).join("")
    || `<p class="note">No stations yet.</p>`}</div>`;
  anchor.after(menu);
  const list = menu.querySelector(".point-list");
  S.filters["ride-point"] = "";
  searchable(list, "ride-point", { items: "button", min: 0 });
  const input = menu.querySelector("input");
  if (input) input.focus();
  list.querySelectorAll("[data-k]").forEach((btn) => btn.addEventListener("click", () => {
    menu.remove();
    onPick(points[Number(btn.dataset.k)].value);
  }));
  const close = (ev) => {
    if (!menu.contains(ev.target) && ev.target !== anchor) {
      menu.remove();
      document.removeEventListener("pointerdown", close, true);
    }
  };
  document.addEventListener("pointerdown", close, true);
}

function renderScenarioEditor() {
  const box = $("#scenario-editor");
  if (!box) return;
  if (S.sel.kind !== "scenario" || !S.spec.scenarios[S.sel.id]) { box.innerHTML = ""; return; }
  const i = S.sel.id;
  const sc = S.spec.scenarios[i];
  const routed = isRouted(sc);

  box.innerHTML = `<div class="card">
    <h3>Ride</h3>
    <label class="field"><span>Name</span><input type="text" id="r-name" value="${esc(sc.name)}"></label>
    <div class="field"><span>Colour</span>
      <div class="swatches">${PALETTE.map((pl) =>
        `<button type="button" data-color="${esc(pl.color)}" title="${esc(pl.name)}"
           style="background:${esc(pl.color)}" class="${(sc.color || "").toLowerCase() === pl.color.toLowerCase() ? "is-on" : ""}"></button>`).join("")}
      </div>
    </div>
    ${routed ? `
    <div class="field"><span>Start</span>
      <button type="button" class="point-btn" id="r-from">${esc(pointLabel(sc.from) || "choose where it starts…")}</button></div>
    <div class="field"><span>Via — in order, to send it down a particular branch</span>
      <div class="chips via-chips">${(sc.via || []).map((v, k) =>
        `<span class="chip">${esc(pointLabel(v))}<button type="button" class="ghost" data-unvia="${k}" title="remove">×</button></span>`).join("")}
        <button type="button" class="ghost" id="r-via">+ via</button></div></div>
    <div class="field"><span>End</span>
      <button type="button" class="point-btn" id="r-to">${esc(pointLabel(sc.to) || "choose where it ends…")}</button></div>
    <label class="field"><span>Wait at each stop — <b id="r-dwell-v">${sc.dwell ?? 1.5}</b> s</span>
      <input type="range" id="r-dwell" min="0" max="10" step="0.5" value="${sc.dwell ?? 1.5}"></label>
    <label class="field"><span>Travel time end to end — <b id="r-secs">${sc.duration || 12}</b> s</span>
      <input type="range" id="r-dur" min="2" max="120" step="1" value="${sc.duration || 12}"></label>
    <h3>Route</h3>
    <div id="r-route" class="route"></div>` : `
    <p class="note">This ride lists its stops by hand, the way rides used to be made.
      Convert it to start at its first stop and end at its last — the stops between become vias.</p>
    <p><button type="button" id="r-convert" class="primary">Convert to Start / End</button></p>
    <div id="r-route" class="route"></div>`}
    <label class="field"><span><input type="checkbox" id="r-hidden" ${sc.hidden ? "checked" : ""}>
      Hide this ride — left out of the map and the exported SVG</span></label>
    <div class="row-btns">
      <button type="button" id="r-nav" ${rideReport(i)?.d && !sc.hidden ? "" : "disabled"}>▶ Navigate</button>
      <button id="r-del" class="danger">Delete ride</button>
    </div>
  </div>`;

  const name = $("#r-name");
  name.addEventListener("focus", pushUndo);
  name.addEventListener("input", () => { sc.name = name.value; markDirty(); scheduleRender(); });
  name.addEventListener("change", refreshPanels);
  box.querySelectorAll("[data-color]").forEach((btn) =>
    btn.addEventListener("click", () => applyChange(() => { sc.color = btn.dataset.color; })));
  $("#r-hidden").addEventListener("change", (ev) => applyChange(() => {
    if (ev.target.checked) sc.hidden = true; else delete sc.hidden;
  }));
  $("#r-nav").addEventListener("click", () => navStart(i));
  $("#r-del").addEventListener("click", () => applyChange(() => {
    if (NAV.ride === i) navStop();
    S.spec.scenarios.splice(i, 1);
    S.sel = { kind: null, id: null };
  }));

  if (routed) {
    $("#r-from").addEventListener("click", (ev) =>
      pickPoint(ev.currentTarget, (v) => applyChange(() => { sc.from = v; })));
    $("#r-to").addEventListener("click", (ev) =>
      pickPoint(ev.currentTarget, (v) => applyChange(() => { sc.to = v; })));
    $("#r-via").addEventListener("click", (ev) =>
      pickPoint(ev.currentTarget, (v) => applyChange(() => { sc.via = (sc.via || []).concat([v]); })));
    box.querySelectorAll("[data-unvia]").forEach((btn) => btn.addEventListener("click", () =>
      applyChange(() => {
        sc.via.splice(Number(btn.dataset.unvia), 1);
        if (!sc.via.length) delete sc.via;
      })));
    const dwell = $("#r-dwell");
    dwell.addEventListener("pointerdown", pushUndo);
    dwell.addEventListener("input", () => {
      sc.dwell = Number(dwell.value);
      $("#r-dwell-v").textContent = dwell.value;
      markDirty();
      scheduleRender();
    });
    const dur = $("#r-dur");
    dur.addEventListener("pointerdown", pushUndo);
    dur.addEventListener("input", () => {
      sc.duration = Number(dur.value);
      $("#r-secs").textContent = dur.value;
      markDirty();
      scheduleRender();
    });
  } else {
    $("#r-convert").addEventListener("click", () => applyChange(() => {
      const stops = sc.stations || [];
      if (stops.length) sc.from = stops[0];
      if (stops.length > 1) sc.to = stops[stops.length - 1];
      if (stops.length > 2) sc.via = stops.slice(1, -1);
      sc.dwell = 1.5;
      sc.duration = Math.max(2, Number(sc.duration) || 12);
      delete sc.stations;
    }));
  }
  renderRideRoute();
  setHint();
}

/** The route the server worked out for the open ride, with a stop/jump toggle each. */
function renderRideRoute() {
  const host = $("#r-route");
  if (!host || S.sel.kind !== "scenario") return;
  const i = S.sel.id;
  const sc = S.spec.scenarios[i];
  const report = rideReport(i);
  if (!sc) return;
  if (!report) { host.innerHTML = `<p class="note">Working out the route…</p>`; return; }
  const problems = (report.problems || []).map((p) => `<p class="warn">${esc(p)}</p>`).join("");
  if (!report.stops.length) { host.innerHTML = problems || `<p class="note">No route yet.</p>`; return; }
  const routed = isRouted(sc);
  host.innerHTML = problems + report.stops.map((st, k) => `
    <div class="stop-row ${st.jump ? "is-jump" : ""}">
      <span class="n">${k + 1}</span>
      <span class="grow">${esc(st.label)}${st.change ? ` <span class="chip">change for ${esc(st.change)}</span>` : ""}</span>
      ${routed && k > 0 && k < report.stops.length - 1 ? `<button type="button" class="ghost"
        data-jump="${esc(st.id)}" title="${st.jump ? "stop here" : "ride straight through"}"
        >${st.jump ? "jump" : "stop"}</button>` : ""}
    </div>`).join("");
  host.querySelectorAll("[data-jump]").forEach((btn) => btn.addEventListener("click", () =>
    applyChange(() => {
      const id = btn.dataset.jump;
      const pass = new Set(sc.pass || []);
      if (pass.has(id)) pass.delete(id); else pass.add(id);
      if (pass.size) sc.pass = [...pass]; else delete sc.pass;
    })));
}

/** A station or junction clicked on the canvas while a ride is open. */
function rideClick(sc, id) {
  if (!isRouted(sc)) {
    applyChange(() => { sc.stations = sc.stations || []; sc.stations.push(id); });
    return;
  }
  applyChange(() => {
    if (sc.from === undefined) sc.from = id;
    else if (sc.to === undefined) sc.to = id;
    else sc.via = (sc.via || []).concat([id]);
  });
}

function addScenario() {
  const i = S.spec.scenarios.length;
  const used = new Set(S.spec.scenarios.map((x) => x.color));
  const pick = PALETTE.find((pl) => !used.has(pl.color)) || PALETTE[i % PALETTE.length]
    || { color: "#7b3fb5" };
  applyChange(() => {
    S.spec.scenarios.push({ name: `Ride ${i + 1}`, color: pick.color,
                            dwell: 1.5, duration: 12 });
    S.sel = { kind: "scenario", id: i };
  });
  const name = $("#r-name");
  if (name) { name.focus(); name.select(); }
}

/** Show or hide every traveller on the canvas. A view choice, not saved. */
function toggleShowRides() {
  S.showRides = !S.showRides;
  applyRideState();
}

/* ---------------------------------------------------------- navigation -- */

/* Follow one ride like a satnav: the camera stays with the traveller, it waits
   at each stop and rides through the ones marked to pass, and a board says
   what is next. The other rides keep animating as they are; only one ride can
   be followed at a time.

   The route and its stops come from the server (S.rides), so this is only
   motion: a path laid over the map, a dot moved along it, and the camera. It
   changes nothing in the map, so it runs just as well while an agent holds it. */

const NAV = { ride: null, zoomPref: 2 };
const NAV_NS = "http://www.w3.org/2000/svg";
const NAV_VOICE_KEY = "metro-map.nav-voice";
const reducedMotion = () => window.matchMedia
  && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

function navVoiceStored() {
  try { return localStorage.getItem(NAV_VOICE_KEY) === "1"; } catch (_) { return false; }
}

function speak(text) {
  if (!NAV.voice || !text || !window.speechSynthesis) return;
  try {
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(new SpeechSynthesisUtterance(text));
  } catch (_) { /* no voice here: the board still says it */ }
}

/** The points the traveller waits at: stops it does not ride through, plus the ends. */
function navAnchors(report) {
  const out = [];
  report.stops.forEach((st, k) => { if (!st.jump) out.push({ at: st.at, stop: k }); });
  if (!out.length || out[0].at > 0.0005) out.unshift({ at: 0, stop: null });
  if (out[out.length - 1].at < 0.9995) out.push({ at: 1, stop: null });
  return out;
}

function navStart(i) {
  const report = rideReport(i);
  if (!report || !report.d) return;
  navStop({ quiet: true });
  Object.assign(NAV, {
    ride: i, name: report.name, report, anchors: navAnchors(report),
    playing: true, speed: 1, zoom: NAV.zoomPref, follow: true, voice: navVoiceStored(),
    dwellOverride: null, k: 0, p: 0, wait: 0, frac: 0, done: false, last: null,
    boardKey: "",
  });
  NAV.phase = NAV.anchors[0].stop !== null ? "dwell" : "move";
  if (!navBuild()) { NAV.ride = null; return; }
  renderNavBoard(true);
  const first = report.stops[0];
  speak(first ? `${report.name}. Starting at ${first.label}.` : report.name);
  NAV.raf = requestAnimationFrame(navTick);
  setHint();
}

function navStop({ quiet = false } = {}) {
  if (NAV.ride === null) return;
  cancelAnimationFrame(NAV.raf);
  const svg = svgEl();
  if (svg) {
    svg.querySelector("#nav-overlay")?.remove();
    svg.querySelectorAll(".traveller").forEach((t) => { t.style.visibility = ""; });
    svg.classList.remove("navigating");
  }
  NAV.ride = null;
  $("#navboard").hidden = true;
  $("#navboard").innerHTML = "";
  if (window.speechSynthesis) window.speechSynthesis.cancel();
  if (!quiet) setHint();
}

/** Lay the followed route over the map. False when there is no map to lay it on. */
function navBuild() {
  const svg = svgEl();
  const r = NAV.report;
  if (!svg || !r) return false;
  svg.querySelector("#nav-overlay")?.remove();
  const vb = svg.viewBox.baseVal;
  const w = S.style.stroke || 10;
  const g = document.createElementNS(NAV_NS, "g");
  g.id = "nav-overlay";
  g.innerHTML = `<rect class="nav-veil" x="${vb.x}" y="${vb.y}" width="${vb.width}" height="${vb.height}"/>
    <path class="nav-route" d="${r.d}" stroke="${esc(r.color)}" stroke-width="${w * 1.1}"/>
    <path class="nav-trail" d="${r.d}" stroke="${esc(r.color)}" stroke-width="${w * 1.1}"/>
    <path class="nav-ahead" d="${r.d}" stroke="${esc(r.color)}" stroke-width="${w * 1.6}"/>
    <g class="nav-stops"></g>
    <circle class="nav-traveller" r="${w * 1.25}" fill="${esc(r.color)}"/>`;
  svg.appendChild(g);
  svg.classList.add("navigating");
  NAV.path = g.querySelector(".nav-route");
  NAV.len = NAV.path.getTotalLength() || 1;
  const stops = g.querySelector(".nav-stops");
  stops.innerHTML = r.stops.map((st) => {
    const pt = NAV.path.getPointAtLength(st.at * NAV.len);
    return `<g class="nav-stop ${st.jump ? "jump" : ""}">
      <circle cx="${pt.x}" cy="${pt.y}" r="${w * 0.9}" stroke="${esc(r.color)}" stroke-width="${w * 0.45}"/>
      <text x="${pt.x}" y="${pt.y - w * 2.2}" text-anchor="middle">${esc(st.label)}</text></g>`;
  }).join("");
  // the ride's own traveller gives way to the one being steered
  svg.querySelectorAll(`.traveller.t${NAV.ride}`).forEach((t) => { t.style.visibility = "hidden"; });
  navDraw();
  return true;
}

/** After a re-render: lay the route again, or stop if the ride has gone. */
function navAfterRender() {
  if (NAV.ride === null) return;
  const report = rideReport(NAV.ride);
  const sc = S.spec.scenarios[NAV.ride];
  if (!report || !report.d || !sc || sc.hidden || report.name !== NAV.name) {
    navStop();
    flashLive("The ride you were following changed — navigation stopped.");
    return;
  }
  const frac = NAV.frac;
  NAV.report = report;
  NAV.anchors = navAnchors(report);
  navSeek(frac, { keepPhase: true });
  navBuild();
  renderNavBoard(true);
}

/** Put the traveller at a fraction of the route, waiting there if it is a stop. */
function navSeek(frac, { keepPhase = false } = {}) {
  const a = NAV.anchors;
  let k = 0;
  while (k < a.length - 1 && a[k + 1].at <= frac + 1e-6) k += 1;
  NAV.k = k;
  NAV.frac = frac;
  NAV.done = k >= a.length - 1;
  if (NAV.done) { NAV.phase = "dwell"; NAV.p = 0; return; }
  const onAnchor = Math.abs(a[k].at - frac) < 1e-6;
  if (onAnchor && (!keepPhase || NAV.phase === "dwell") && a[k].stop !== null) {
    NAV.phase = "dwell"; NAV.wait = keepPhase ? NAV.wait : 0; NAV.p = 0;
  } else {
    NAV.phase = "move";
    const span = a[k + 1].at - a[k].at || 1;
    NAV.p = Math.max(0, Math.min(1, (frac - a[k].at) / span));
  }
}

function navDwell() {
  return NAV.dwellOverride ?? NAV.report.dwell ?? 1.5;
}

function navSegSeconds(k) {
  const a = NAV.anchors;
  const travel = NAV.report.travel || 12;
  return Math.max(0.35, travel * (a[k + 1].at - a[k].at));
}

function navTick(ts) {
  if (NAV.ride === null) return;
  const dt = NAV.last === null ? 0 : Math.min(0.1, (ts - NAV.last) / 1000);
  NAV.last = ts;
  const a = NAV.anchors;
  if (NAV.playing && !NAV.done) {
    if (NAV.phase === "dwell") {
      NAV.wait += dt * NAV.speed;
      if (NAV.wait >= navDwell()) {
        NAV.phase = "move"; NAV.p = 0; NAV.wait = 0;
        const next = navNextStop();
        if (next) speak(`Next stop: ${next.label}.`);
      }
    } else {
      NAV.p += dt * NAV.speed / navSegSeconds(NAV.k);
      if (NAV.p >= 1) {
        NAV.k += 1; NAV.p = 0;
        NAV.frac = a[NAV.k].at;
        const here = a[NAV.k].stop !== null ? NAV.report.stops[a[NAV.k].stop] : null;
        if (NAV.k >= a.length - 1) {
          NAV.done = true; NAV.phase = "dwell";
          speak(here ? `${here.label}. This ride ends here.` : "This ride ends here.");
        } else if (here) {
          NAV.phase = "dwell"; NAV.wait = 0;
          speak(here.change ? `${here.label}. Change here for ${here.change}.` : here.label);
        }
      }
    }
    if (NAV.phase === "move" && !NAV.done) {
      NAV.frac = a[NAV.k].at + (a[NAV.k + 1].at - a[NAV.k].at) * easeInOut(NAV.p);
    }
  }
  navDraw(dt);
  renderNavBoard();
  NAV.raf = requestAnimationFrame(navTick);
}

/** Move the dot, the trail and the look-ahead, and bring the camera along. */
function navDraw(dt = 0) {
  const svg = svgEl();
  const g = svg && svg.querySelector("#nav-overlay");
  if (!g || !NAV.path) return;
  const at = NAV.frac * NAV.len;
  const pt = NAV.path.getPointAtLength(at);
  const dot = g.querySelector(".nav-traveller");
  dot.setAttribute("cx", pt.x);
  dot.setAttribute("cy", pt.y);
  g.querySelector(".nav-trail").style.strokeDasharray = `${at} ${NAV.len * 2}`;
  const next = NAV.anchors[Math.min(NAV.k + 1, NAV.anchors.length - 1)];
  const ahead = Math.max(0, next.at * NAV.len - at);
  const aheadPath = g.querySelector(".nav-ahead");
  aheadPath.style.strokeDasharray = `0 ${at} ${ahead} ${NAV.len * 2}`;
  g.querySelectorAll(".nav-stop").forEach((el, k) => {
    el.classList.toggle("passed", NAV.report.stops[k].at <= NAV.frac + 1e-4);
  });

  if (!NAV.follow) return;
  const box = $("#canvas").getBoundingClientRect();
  const x0 = Number(svg.dataset.x0) || 0;
  const y0 = Number(svg.dataset.y0) || 0;
  const k = reducedMotion() || dt === 0 ? 1 : 1 - Math.exp(-dt * 4);
  S.zoom += (NAV.zoom - S.zoom) * k;
  const wantX = box.width / 2 - (pt.x - x0) * S.zoom;
  const wantY = box.height * 0.55 - (pt.y - y0) * S.zoom;
  S.pan.x += (wantX - S.pan.x) * k;
  S.pan.y += (wantY - S.pan.y) * k;
  applyTransform();
}

/** The next stop the traveller will wait at, or null at the end. */
function navNextStop() {
  const a = NAV.anchors;
  const from = NAV.phase === "dwell" ? NAV.k + 1 : NAV.k + 1;
  for (let k = from; k < a.length; k += 1) {
    if (a[k].stop !== null) return NAV.report.stops[a[k].stop];
  }
  return null;
}

function navSecondsTo(stop) {
  if (!stop) return 0;
  const a = NAV.anchors;
  let secs = NAV.phase === "dwell" ? Math.max(0, navDwell() - NAV.wait) : 0;
  for (let k = NAV.k; k < a.length - 1; k += 1) {
    secs += navSegSeconds(k) * (k === NAV.k && NAV.phase === "move" ? 1 - NAV.p : 1);
    if (a[k + 1].stop !== null && NAV.report.stops[a[k + 1].stop] === stop) break;
    if (a[k + 1].stop !== null) secs += navDwell();
  }
  return secs / NAV.speed;
}

function stopDate(stop) {
  if (!stop) return "";
  if (stop.date) return stop.date;
  const st = S.spec.stations[stop.id];
  return st && isRoadmap() ? dateForGX(st.gx) : "";
}

function renderNavBoard(full = false) {
  const board = $("#navboard");
  if (NAV.ride === null) return;
  const r = NAV.report;
  const here = NAV.phase === "dwell" && NAV.anchors[NAV.k].stop !== null
    ? r.stops[NAV.anchors[NAV.k].stop] : null;
  const next = navNextStop();
  const key = [NAV.k, NAV.phase, NAV.done, NAV.playing, NAV.follow, NAV.voice, NAV.speed,
               Math.ceil(navSecondsTo(next))].join("|");
  if (!full && key === NAV.boardKey) return;
  NAV.boardKey = key;
  // the line being ridden: what the last stop passed left on, not where the next
  // one changes to
  const passed = r.stops.filter((st) => st.at <= NAV.frac + 1e-4);
  const line = (here || passed[passed.length - 1] || r.stops[0] || {}).line || "";
  const change = here && here.change ? here.change : "";
  const when = stopDate(NAV.done ? here : next);
  if (full || !board.firstChild) {
    board.hidden = false;
    board.innerHTML = `
      <div class="nav-head">
        <span class="swatch" style="background:${esc(r.color)}"></span>
        <b class="grow">${esc(r.name)}</b>
        <button type="button" class="ghost" id="nav-exit" title="stop navigating (Esc)">✕</button>
      </div>
      <div class="nav-now">
        <span class="nav-kicker" id="nav-kicker"></span>
        <span class="nav-stop" id="nav-stop"></span>
        <span class="nav-meta" id="nav-meta"></span>
        <span class="nav-change" id="nav-change" hidden></span>
      </div>
      <div class="nav-strip" id="nav-strip">${r.stops.map((st, k) =>
        `<button type="button" class="nav-dot ${st.jump ? "jump" : ""}" data-stop="${k}"
           title="${esc(st.label)}${st.jump ? " — rides through" : ""}"></button>`).join("")}</div>
      <div class="nav-ctrl">
        <button type="button" id="nav-prev" title="previous stop (←)">⏮</button>
        <button type="button" id="nav-play" title="pause or play (Space)"></button>
        <button type="button" id="nav-next" title="next stop (→)">⏭</button>
        <select id="nav-speed" title="speed">${[0.5, 1, 2, 4].map((v) =>
          `<option value="${v}" ${v === NAV.speed ? "selected" : ""}>${v}×</option>`).join("")}</select>
        <label title="zoom (+ −)">🔍<input type="range" id="nav-zoom" min="1.5" max="6" step="0.25" value="${NAV.zoom}"></label>
        <label title="seconds at each stop">⏱<input type="range" id="nav-wait" min="0" max="10" step="0.5" value="${navDwell()}"></label>
        <button type="button" id="nav-voice" title="spoken announcements"></button>
        <button type="button" id="nav-follow" hidden title="follow the traveller again">◎ Re-centre</button>
      </div>`;
    $("#nav-exit").addEventListener("click", () => navStop());
    $("#nav-prev").addEventListener("click", () => navStep(-1));
    $("#nav-next").addEventListener("click", () => navStep(1));
    $("#nav-play").addEventListener("click", navTogglePlay);
    $("#nav-speed").addEventListener("change", (ev) => { NAV.speed = Number(ev.target.value); });
    $("#nav-zoom").addEventListener("input", (ev) => {
      NAV.zoom = NAV.zoomPref = Number(ev.target.value); NAV.follow = true;
    });
    $("#nav-wait").addEventListener("input", (ev) => { NAV.dwellOverride = Number(ev.target.value); });
    $("#nav-voice").addEventListener("click", () => {
      NAV.voice = !NAV.voice;
      try { localStorage.setItem(NAV_VOICE_KEY, NAV.voice ? "1" : "0"); } catch (_) { /* none */ }
      if (!NAV.voice && window.speechSynthesis) window.speechSynthesis.cancel();
      renderNavBoard(true);
    });
    $("#nav-follow").addEventListener("click", () => { NAV.follow = true; renderNavBoard(true); });
    board.querySelectorAll("[data-stop]").forEach((btn) => btn.addEventListener("click", () => {
      const st = r.stops[Number(btn.dataset.stop)];
      navSeek(st.at);
      NAV.playing = true;
      navDraw();
      renderNavBoard(true);
    }));
  }
  $("#nav-kicker").textContent = NAV.done ? "Arrived" : here ? "Now at" : next ? "Next stop" : "Riding on";
  $("#nav-stop").textContent = NAV.done ? (here ? here.label : "the end of the line")
    : here ? here.label : next ? next.label : "past the edge of the map";
  const secs = Math.ceil(navSecondsTo(next));
  $("#nav-meta").textContent = [line && `on ${line}`, when, !NAV.done && next && !here ? `in ${secs} s` : "",
    here && next ? `then ${next.label}` : ""].filter(Boolean).join(" · ");
  const ch = $("#nav-change");
  ch.hidden = !change;
  ch.textContent = change ? `Change here for ${change}` : "";
  $("#nav-play").textContent = NAV.done ? "↻" : NAV.playing ? "⏸" : "▶";
  $("#nav-voice").textContent = NAV.voice ? "🔈" : "🔇";
  $("#nav-follow").hidden = NAV.follow;
  board.querySelectorAll("[data-stop]").forEach((btn, k) => {
    const st = r.stops[k];
    btn.classList.toggle("passed", st.at <= NAV.frac + 1e-4);
    btn.classList.toggle("current", !!here && st === here);
  });
}

function navTogglePlay() {
  if (NAV.ride === null) return;
  if (NAV.done) { navSeek(0); NAV.playing = true; navDraw(); renderNavBoard(true); return; }
  NAV.playing = !NAV.playing;
  renderNavBoard(true);
}

/** To the previous or next stop the traveller waits at. */
function navStep(dir) {
  if (NAV.ride === null) return;
  const a = NAV.anchors;
  let k = NAV.k;
  if (dir > 0) {
    k += 1;
    while (k < a.length - 1 && a[k].stop === null) k += 1;
  } else {
    if (NAV.phase === "dwell" || NAV.p < 0.15) k -= 1;
    while (k > 0 && a[k].stop === null) k -= 1;
  }
  k = Math.max(0, Math.min(a.length - 1, k));
  navSeek(a[k].at);
  NAV.playing = true;
  navDraw();
  renderNavBoard(true);
  const here = a[k].stop !== null ? NAV.report.stops[a[k].stop] : null;
  if (here) speak(here.label);
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

/** What the source's browser picked, said in a line, carried in hidden fields.
 *
 * Those options are written by the wizard, not typed: a raw "levels" box
 * inviting "junction,station" by hand is the wizard's job done worse. */
function browseSummary(opts, filled) {
  const val = (name) => (filled && filled[name] !== undefined ? filled[name] : "");
  const list = (name) => String(val(name) || "").split(",").map((x) => x.trim()).filter(Boolean);
  const roots = list("roots");
  const bits = [];
  if (roots.length) {
    bits.push(`<strong>${esc(roots.join(", "))}</strong>`);
    if (list("types").length) bits.push(`only ${esc(list("types").join(", "))}`);
    if (val("open_only") === true || val("open_only") === "true") bits.push("only open");
    if (list("labels").length) bits.push(`labels ${esc(list("labels").join(", "))}`);
    if (list("levels").length) bits.push(`levels ${esc(list("levels").join(" → "))}`);
    if (list("roles").length) bits.push(`${list("roles").length} issue(s) mapped on their own`);
    if (list("dates").length) bits.push(`${list("dates").length} date(s) of your own`);
  }
  return `<div class="card browse-summary">
      ${roots.length ? `<p>${bits.join(" · ")}</p>` : `<p class="note">Pick issue keys,
        narrow what is beneath them, and map each level onto the map.</p>`}
      <p><button type="button" id="imp-browse" class="${roots.length ? "ghost" : "primary"}"
        >${roots.length ? "Change…" : "Start from issue keys…"}</button></p>
      ${opts.map((o) => `<input type="hidden" data-opt="${esc(o.name)}"
        value="${esc(String(val(o.name) ?? ""))}">`).join("")}
    </div>`;
}

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
    // what was filled in wins, false included — only an absent value falls
    // back to the default
    const on = filled && filled[o.name] !== undefined && filled[o.name] !== ""
      ? filled[o.name] === true || filled[o.name] === "true" : !!o.default;
    return `<label class="field"><span>${esc(o.name)}</span>
      <span class="note"><input type="checkbox" ${common} ${on ? "checked" : ""}>
      ${esc(o.help)}</span></label>`;
  }
  if (o.kind === "choice") {
    return `<label class="field" title="${esc(o.help)}"><span>${esc(o.name)}</span>
      <select ${common}>${(o.choices || []).map((c) =>
        `<option value="${esc(c)}" ${c === value ? "selected" : ""}>${esc(c || "auto")}</option>`).join("")}
      </select></label>`;
  }
  const type = { int: "number", date: "date" }[o.kind] || "text";
  return `<label class="field" title="${esc(o.help)}">
    <span>${esc(o.name)}${o.required ? " *" : ""}</span>
    <input type="${type}" ${common} value="${esc(value)}"
           placeholder="${esc(o.placeholder || "")}"></label>`;
}

/** Start from issue keys, narrow what is beneath them, and say what each level
 * becomes on the map.
 *
 * Three steps, because they are three different questions: *which* pieces of
 * the plan (keys — asked for, checked, each tree loaded once), *how much of
 * them* (filters), and *as what* (the mapping). The last two are answered
 * locally on what is already loaded, so changing one costs no round trip and
 * nothing races back out of order. The rules copied here only count; the
 * import itself is built by the server, from the same options.
 *
 * `start` carries whatever the form already says, so re-opening the wizard on
 * a map that was imported before picks up where that import left off. */
async function treeWizard(src, onPicked, start = {}) {
  const KEY = /^[A-Z][A-Z0-9_]+-\d+$/;
  const QUARTER = /^[A-Z]{0,4}\d{2}Q[1-4]$/i;
  const ROLES = [["station", "Station"], ["junction", "Junction — a branch"],
                 ["zone", "Zone"], ["note", "Track note"],
                 ["hide", "Hide, keep what is beneath"], ["skip", "Don't import"]];
  const example = ((src.options || []).find((o) => o.name === "roots") || {})
    .placeholder || "ABCD-123";
  const pairs = (list) => Object.fromEntries((list || []).map((p) => {
    const at = p.indexOf("=");
    return [p.slice(0, at).trim().toUpperCase(), p.slice(at + 1).trim()];
  }).filter(([k, v]) => k && v));

  let step = 1;
  let keys = (start.roots || []).length ? [...start.roots] : [""];
  let problems = {};                  // key row -> sentence
  let trees = [];                     // [{key, nodes}], in key order
  let types = [];                     // [[name, count]] across every tree
  let seenLabels = [];
  const chosen = {
    types: new Set(start.types || []),
    narrowed: (start.types || []).length > 0,
    open_only: !!start.open_only,
    labels: (start.labels || []).join(", "),
  };
  const map = { levels: [...(start.levels || [])], roles: pairs(start.roles),
                dates: pairs(start.dates) };

  // ---- the rules, copied just far enough to count --------------------------
  const labelsWanted = () => chosen.labels.split(",").map((s) => s.trim().toLowerCase())
    .filter(Boolean);
  const keptOf = (nodes) => {
    const out = new Set([nodes[0].id]);
    const wanted = labelsWanted();
    for (const n of nodes.slice(1)) {
      const f = n.facets || {};
      if (chosen.narrowed && !chosen.types.has(f.type)) continue;
      if (chosen.open_only && !f.open) continue;
      if (wanted.length && !(f.labels || []).some((l) => wanted.includes(l.toLowerCase()))) continue;
      out.add(n.id);
    }
    const parent = new Map(nodes.map((n) => [n.id, n.parent]));
    for (const id of [...out]) {
      for (let up = parent.get(id); up && !out.has(up); up = parent.get(up)) out.add(up);
    }
    return out;
  };
  const kidsOf = (nodes, kept) => {
    const kids = new Map();
    nodes.slice(1).filter((n) => kept.has(n.id)).forEach((n) => {
      if (!kids.has(n.parent)) kids.set(n.parent, []);
      kids.get(n.parent).push(n);
    });
    return kids;
  };
  const depthOf = () => Math.max(0, ...trees.flatMap((t) => t.nodes.map((n) => n.depth || 0)));
  // worked out once per draw: it walks every tree, and the tally asks for it
  // on every row
  let defaultOne = null;
  const levelDefault = (depth) => {
    if (depth !== 1) return "station";
    if (defaultOne) return defaultOne;
    return (defaultOne = trees.some((t) => {
      const kept = keptOf(t.nodes);
      const kids = kidsOf(t.nodes, kept);
      return t.nodes.some((n) => n.depth === 1 && kept.has(n.id) && (kids.get(n.id) || []).length);
    }) ? "junction" : "station");
  };
  const dated = (n) => {
    const f = n.facets || {};
    return !!(f.due || (f.labels || []).some((l) => QUARTER.test(l)));
  };
  /** Every kept issue's role, as the server will work it out, and the count. */
  const resolve = () => {
    const role = new Map();
    const tally = { stations: 0, branches: 0, zones: 0, notes: 0, lines: 0, undated: 0 };
    for (const t of trees) {
      const kept = keptOf(t.nodes);
      const kids = kidsOf(t.nodes, kept);
      // returns how many stops the subtree placed, so a junction, zone or
      // note with nothing beneath it is not counted — it will not be drawn
      const visit = (id) => {
        let placed = 0;
        for (const n of kids.get(id) || []) {
          let r = map.roles[n.id] || map.levels[n.depth - 1] || levelDefault(n.depth);
          if (r === "skip") continue;
          if (r === "junction" && !(kids.get(n.id) || []).length) r = "station";
          role.set(n.id, r);
          let here = 0;
          if (r === "station") {
            if (dated(n) || map.dates[n.id]) { here = 1; tally.stations += 1; }
            else tally.undated += 1;
          }
          const below = visit(n.id);
          if (below && r === "junction") tally.branches += 1;
          // a junction with nothing dated beneath is still a stop if it is dated
          if (!below && r === "junction" && (dated(n) || map.dates[n.id])) {
            here = 1; tally.stations += 1;
          }
          if (below && r === "zone") tally.zones += 1;
          if (below && r === "note") tally.notes += 1;
          placed += here + below;
        }
        return placed;
      };
      if (visit(t.nodes[0].id)) tally.lines += 1;
    }
    return { role, tally };
  };

  // ---- markup -------------------------------------------------------------
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const chips = (list, attr, isOn) => `<div class="strands">${list.map(([v, n]) =>
    `<button type="button" data-${attr}="${esc(v)}" class="${isOn(v) ? "is-on" : ""}"
      >${esc(v)}<span class="count">${n}</span></button>`).join("")}</div>`;
  const row = (n, extra = "") => `
    <div class="tree-row ${n.expandable ? "is-line" : ""}" data-row="${esc(n.id)}"
         style="--depth:${Number(n.depth) || 0}">
      <span class="key">${esc(n.id)}</span>
      <span class="grow">${esc(n.label)}</span>
      <span class="note">${esc(n.hint || "")}</span>${extra}
    </div>`;

  const stepOne = () => `
    <p class="note">Each key is imported as a line of its own, with everything
      beneath it. An initiative, an epic or a story will do.</p>
    ${keys.map((k, i) => `
      <div class="wiz-key">
        <input type="text" data-key="${i}" value="${esc(k)}" autocomplete="off"
               spellcheck="false" placeholder="${esc(example.split(",")[0])}">
        ${keys.length > 1 ? `<button type="button" class="ghost" data-drop="${i}"
          title="Remove this key">×</button>` : ""}
      </div>
      ${problems[i] ? `<p class="warn">${esc(problems[i])}</p>` : ""}`).join("")}
    <p><button type="button" class="ghost" id="wiz-add">+ Add key</button></p>
    <div class="actions">
      <button value="cancel">Cancel</button>
      <button type="button" class="primary" id="wiz-next">Next</button>
    </div>`;

  const stepTwo = () => `
    <p class="note">Narrow what comes in. Whatever a kept issue hangs from stays,
      as the structure it sits in.</p>
    <h3 class="group">Issue types</h3>
    ${chips(types, "type", (v) => !chosen.narrowed || chosen.types.has(v))}
    <h3 class="group">Status</h3>
    <div class="strands">
      <button type="button" data-open="0" class="${chosen.open_only ? "" : "is-on"}">All</button>
      <button type="button" data-open="1" class="${chosen.open_only ? "is-on" : ""}">Only open</button>
    </div>
    <label class="field"><span>Labels — any of these, comma separated; empty takes all</span>
      <input type="text" id="wiz-labels" value="${esc(chosen.labels)}" autocomplete="off"
             spellcheck="false" placeholder="25Q1, cutover"></label>
    ${seenLabels.length ? `<p class="note">Found beneath these keys:</p>
      ${chips(seenLabels, "label", (v) => labelsWanted().includes(v.toLowerCase()))}` : ""}
    ${trees.map((t) => `<details class="tree" open>
      <summary>${esc(t.nodes[0].id)} · ${esc(t.nodes[0].label)}</summary>
      ${t.nodes.slice(1).map((n) => row(n)).join("")}
      ${(t.nodes[0].facets || {}).truncated ? `<p class="warn">Only the first
        ${t.nodes.length - 1} issues are shown.</p>` : ""}
    </details>`).join("")}
    <p class="note" id="wiz-said"></p>
    <div class="actions">
      <button type="button" id="wiz-back">Back</button>
      <button value="cancel">Cancel</button>
      <button type="button" class="primary" id="wiz-next">Next</button>
    </div>`;

  const stepThree = () => {
    const deepest = depthOf();
    const levelRows = [];
    for (let d = 1; d <= deepest; d++) {
      const at = new Map();
      let branches = false;
      for (const t of trees) {
        const kept = keptOf(t.nodes);
        const kids = kidsOf(t.nodes, kept);
        t.nodes.filter((n) => n.depth === d && kept.has(n.id)).forEach((n) => {
          at.set(n.facets.type, (at.get(n.facets.type) || 0) + 1);
          if ((kids.get(n.id) || []).length) branches = true;
        });
      }
      if (!at.size) continue;
      const value = map.levels[d - 1] || levelDefault(d);
      levelRows.push(`<tr>
        <td>Level ${d}</td>
        <td class="note">${[...at].map(([ty, c]) => `${esc(ty)} ×${c}`).join(", ")}</td>
        <td><select data-level="${d}">${ROLES.map(([v, label]) =>
          `<option value="${v}" ${v === value ? "selected" : ""}
            ${v === "junction" && !branches ? "disabled" : ""}>${esc(label)}</option>`).join("")}
        </select></td></tr>`);
    }
    return `
    <p class="note">Each key is a line. Say what each level beneath it becomes —
      and change single issues below where their level is not right for them.</p>
    <div class="wiz-levels"><table>${levelRows.join("")}</table></div>
    ${trees.map((t) => {
      const kept = keptOf(t.nodes);
      return `<details class="tree" open>
        <summary>${esc(t.nodes[0].id)} · ${esc(t.nodes[0].label)} — a line</summary>
        ${t.nodes.slice(1).filter((n) => kept.has(n.id)).map((n) => row(n, `
          <select data-role="${esc(n.id)}" title="What this issue becomes">
            <option value="">as its level</option>
            ${ROLES.map(([v, label]) => `<option value="${v}"
              ${map.roles[n.id] === v ? "selected" : ""}>${esc(label)}</option>`).join("")}
          </select>
          ${dated(n) ? "" : `<input type="date" data-date="${esc(n.id)}"
            value="${esc(map.dates[n.id] || "")}" title="Jira has no date for this — give it one">`}`)).join("")}
      </details>`;
    }).join("")}
    <p class="note" id="wiz-said"></p>
    <div class="actions">
      <button type="button" id="wiz-back">Back</button>
      <button value="cancel">Cancel</button>
      <button type="button" class="primary" id="wiz-go">Continue</button>
    </div>`;
  };

  dialog(`Import from ${src.title}`, stepOne(), (form, dlg) => {
    const draw = () => {
      defaultOne = null;
      const body = step === 1 ? stepOne() : step === 2 ? stepTwo() : stepThree();
      form.innerHTML = `<h2>Import from ${esc(src.title)}</h2>${body}`;
      [null, wireOne, wireTwo, wireThree][step]();
    };
    const back = () => {
      const b = form.querySelector("#wiz-back");
      if (b) b.addEventListener("click", () => { step -= 1; draw(); });
    };

    const wireOne = () => {
      const read = () => { keys = [...form.querySelectorAll("[data-key]")].map((b) => b.value); };
      form.querySelector("#wiz-add").addEventListener("click", () => {
        read(); keys.push(""); problems = {}; draw();
        const boxes = form.querySelectorAll("[data-key]");
        boxes[boxes.length - 1].focus();
      });
      form.querySelectorAll("[data-drop]").forEach((b) => b.addEventListener("click", () => {
        read(); keys.splice(Number(b.dataset.drop), 1); problems = {}; draw();
      }));
      const next = form.querySelector("#wiz-next");
      const go = async () => {
        read();
        problems = {};
        const wanted = keys.map((k) => k.trim().toUpperCase());
        const seen = new Map();
        wanted.forEach((k, i) => {
          if (!k) return;
          if (!KEY.test(k)) problems[i] = `“${keys[i].trim()}” is not an issue key — it looks like ${example.split(",")[0]}.`;
          else if (seen.has(k)) problems[i] = `${k} is already in the list.`;
          else seen.set(k, i);
        });
        if (!seen.size && !Object.keys(problems).length) problems[0] = "Give at least one key.";
        if (Object.keys(problems).length) { draw(); return; }
        next.disabled = true;
        next.textContent = "Looking…";
        const found = await Promise.all([...seen].map(async ([k, i]) => {
          try {
            const res = await api("GET", `/api/browse/${src.name}?${new URLSearchParams({ path: k })}`);
            return { key: k, i, nodes: res.nodes };
          } catch (err) {
            // said beside the key it is about: the fix is a typo in that box
            problems[i] = (err.errors || []).join(" ");
            return null;
          }
        }));
        const loaded = found.filter(Boolean);
        // a key inside another key's tree would be imported twice
        for (const t of loaded) {
          const holder = loaded.find((o) => o !== t && o.nodes.some((n) => n.id === t.key));
          if (holder) problems[t.i] = `${t.key} is already beneath ${holder.key}, so it would come in twice.`;
        }
        if (Object.keys(problems).length) { draw(); return; }
        const fresh = trees.map((t) => t.key).join() !== loaded.map((t) => t.key).join();
        trees = loaded.map(({ key, nodes }) => ({ key, nodes }));
        keys = trees.map((t) => t.key);
        const tally = (pick) => {
          const m = new Map();
          trees.forEach((t) => t.nodes.slice(1).forEach((n) =>
            pick(n.facets || {}).forEach((v) => v && m.set(v, (m.get(v) || 0) + 1))));
          return [...m].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
        };
        types = tally((f) => [f.type]);
        seenLabels = tally((f) => f.labels || []);
        if (fresh && !(start.types || []).length) { chosen.narrowed = false; chosen.types = new Set(); }
        step = 2;
        draw();
      };
      next.addEventListener("click", go);
      form.querySelectorAll("[data-key]").forEach((box) => box.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") { ev.preventDefault(); go(); }   // not submit-and-close
      }));
      const empty = [...form.querySelectorAll("[data-key]")].find((b) => !b.value);
      (empty || form.querySelector("[data-key]")).focus();
    };

    // Filtering changes classes, not markup, so a long tree keeps its scroll
    // position while boxes are ticked and letters typed.
    const refreshTwo = () => {
      form.querySelectorAll("[data-type]").forEach((b) =>
        b.classList.toggle("is-on", !chosen.narrowed || chosen.types.has(b.dataset.type)));
      form.querySelectorAll("[data-open]").forEach((b) =>
        b.classList.toggle("is-on", (b.dataset.open === "1") === chosen.open_only));
      form.querySelectorAll("[data-label]").forEach((b) =>
        b.classList.toggle("is-on", labelsWanted().includes(b.dataset.label.toLowerCase())));
      let kept = 0, all = 0;
      for (const t of trees) {
        const keep = keptOf(t.nodes);
        kept += keep.size - 1;
        all += t.nodes.length - 1;
        t.nodes.slice(1).forEach((n) => {
          const el = form.querySelector(`[data-row="${CSS.escape(n.id)}"]`);
          if (el) el.classList.toggle("is-out", !keep.has(n.id));
        });
      }
      const said = form.querySelector("#wiz-said");
      const none = chosen.narrowed && !chosen.types.size;
      said.textContent = none ? "Pick at least one issue type."
        : `${kept} of ${plural(all, "issue")} beneath ${plural(trees.length, "key")}`;
      said.classList.toggle("warn", none);
      form.querySelector("#wiz-next").disabled = none;
    };

    const wireTwo = () => {
      back();
      form.querySelectorAll("[data-type]").forEach((b) => b.addEventListener("click", () => {
        if (!chosen.narrowed) { chosen.narrowed = true; chosen.types = new Set(types.map(([v]) => v)); }
        const v = b.dataset.type;
        chosen.types.has(v) ? chosen.types.delete(v) : chosen.types.add(v);
        if (chosen.types.size === types.length) chosen.narrowed = false;
        refreshTwo();
      }));
      form.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () => {
        chosen.open_only = b.dataset.open === "1"; refreshTwo();
      }));
      const box = form.querySelector("#wiz-labels");
      box.addEventListener("input", () => { chosen.labels = box.value; refreshTwo(); });
      form.querySelectorAll("[data-label]").forEach((b) => b.addEventListener("click", () => {
        const v = b.dataset.label;
        const list = chosen.labels.split(",").map((s) => s.trim()).filter(Boolean);
        const at = list.findIndex((l) => l.toLowerCase() === v.toLowerCase());
        if (at >= 0) list.splice(at, 1); else list.push(v);
        chosen.labels = list.join(", ");
        box.value = chosen.labels;
        refreshTwo();
      }));
      form.querySelector("#wiz-next").addEventListener("click", () => { step = 3; draw(); });
      refreshTwo();
    };

    const refreshThree = () => {
      const { role, tally } = resolve();
      form.querySelectorAll("[data-row]").forEach((el) => {
        const r = role.get(el.dataset.row);
        el.classList.toggle("is-out", !r || r === "hide");
        const date = el.querySelector("[data-date]");
        if (date) date.hidden = r !== "station";
      });
      const said = form.querySelector("#wiz-said");
      const bits = [plural(tally.stations, "station")];
      if (tally.branches) bits.push(plural(tally.branches, "branch").replace("branchs", "branches"));
      if (tally.zones) bits.push(plural(tally.zones, "zone"));
      if (tally.notes) bits.push(plural(tally.notes, "track note"));
      said.textContent = tally.stations
        ? `${bits.join(" · ")} on ${plural(tally.lines, "line")}`
          + (tally.undated ? ` · ${tally.undated} left out for want of a date` : "")
        : "Nothing would be placed — give the undated issues a date, or map a level to Station.";
      said.classList.toggle("warn", !tally.stations);
      form.querySelector("#wiz-go").disabled = !tally.stations;
    };

    const wireThree = () => {
      back();
      form.querySelectorAll("[data-level]").forEach((sel) => sel.addEventListener("change", () => {
        const d = Number(sel.dataset.level);
        while (map.levels.length < d) map.levels.push("");
        map.levels[d - 1] = sel.value;
        refreshThree();
      }));
      form.querySelectorAll("[data-role]").forEach((sel) => sel.addEventListener("change", () => {
        if (sel.value) map.roles[sel.dataset.role] = sel.value;
        else delete map.roles[sel.dataset.role];
        refreshThree();
      }));
      form.querySelectorAll("[data-date]").forEach((box) => box.addEventListener("change", () => {
        if (box.value) map.dates[box.dataset.date] = box.value;
        else delete map.dates[box.dataset.date];
        refreshThree();
      }));
      form.querySelector("#wiz-go").addEventListener("click", () => {
        // the levels as shown, defaults written out, so a re-sync does not
        // quietly change its mind when the tree beneath grows a level
        const levels = [];
        for (let d = 1; d <= depthOf(); d++) levels.push(map.levels[d - 1] || levelDefault(d));
        const known = new Set(trees.flatMap((t) => t.nodes.map((n) => n.id)));
        const undated = new Set(trees.flatMap((t) => t.nodes.filter((n) => !dated(n)).map((n) => n.id)));
        dlg.close();
        onPicked({
          roots: trees.map((t) => t.key),
          types: chosen.narrowed ? [...chosen.types] : [],
          open_only: chosen.open_only,
          labels: chosen.labels.split(",").map((s) => s.trim()).filter(Boolean),
          levels,
          roles: Object.entries(map.roles).filter(([k]) => known.has(k)).map(([k, v]) => `${k}=${v}`),
          dates: Object.entries(map.dates).filter(([k]) => undated.has(k)).map(([k, v]) => `${k}=${v}`),
        });
      });
      refreshThree();
    };

    wireOne();
  });
}

async function importDialog(want, filled) {
  let info;
  try { info = await api("GET", "/api/sources"); }
  catch (err) { showProblems(err.errors); return; }
  // fetched now rather than at boot: whether a credential is set can change
  // while the designer is running, and a stale "not set" reads as a bug
  const stamped = (S.spec && S.spec.source) || {};
  if (typeof want !== "string") want = stamped.name;
  let chosen = info.sources.some((s) => s.name === want)
    ? want : (info.sources.length ? info.sources[0].name : "");
  // A map this source made before already says how it was imported. Starting
  // from that makes bringing it up to date Import → Import, with nothing to
  // remember or retype.
  if (!filled && S.name && stamped.name === chosen) {
    filled = Object.fromEntries(Object.entries(stamped.options || {}).map(
      ([k, v]) => [k, Array.isArray(v) ? v.join(",") : v]));
  }

  const body = () => {
    const src = info.sources.find((s) => s.name === chosen) || {};
    // The screen shows what a person filling it in needs. Every option is
    // still taken by the command line and MCP; the groups only decide where,
    // or whether, each one appears here.
    const opts = (src.options || []).filter(
      (o) => !(info.local_only || []).includes(o.name) && o.group !== "cli");
    const missing = (src.env || []).filter((e) => e.required && !e.present);
    const browsed = src.browsable ? opts.filter((o) => o.group === "browse") : [];
    const main = opts.filter((o) => !browsed.includes(o) && o.group !== "advanced");
    const more = opts.filter((o) => o.group === "advanced");
    const given = (o) => filled && filled[o.name] !== undefined && filled[o.name] !== ""
      && String(filled[o.name]) !== String(o.default ?? "");
    const from = [...new Set((src.env || []).map((e) => e.from).filter(Boolean))];
    return `
      <label class="field"><span>Import from</span>
        <select id="imp-source">${info.sources.map((s) =>
          `<option value="${esc(s.name)}" ${s.name === chosen ? "selected" : ""}
           >${esc(s.title)}</option>`).join("")}</select></label>
      <p class="note">${esc(src.summary || "")}</p>
      ${(src.env || []).length && !missing.length ? `<p class="note">✓ Connected —
        credentials from ${esc(from.join(" and ") || "settings")}</p>` : ""}
      ${missing.map((e) => `<p class="note">! <code>${esc(e.name)}</code> — not set.
        ${esc(e.help)}</p>`).join("")}
      ${missing.length ? `<p class="warn">Not connected yet —
        <button type="button" id="imp-settings" class="ghost">open Settings</button></p>` : ""}
      ${browsed.length && !missing.length ? browseSummary(browsed, filled) : ""}
      ${main.map((o) => optField(o, filled)).join("")}
      ${more.length ? `<details class="more" ${more.some(given) ? "open" : ""}>
        <summary>More options</summary>
        ${more.map((o) => optField(o, filled)).join("")}
      </details>` : ""}
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
        // the wizard starts from what the form says now, so a mapping made
        // last time — or typed in by hand — is where it picks up
        const now = {};
        form.querySelectorAll("[data-opt]").forEach((el) => {
          now[el.dataset.opt] = el.type === "checkbox" ? el.checked : el.value;
        });
        const csv = (v) => String(v || "").split(",").map((x) => x.trim()).filter(Boolean);
        document.querySelector("#dialog").close();
        treeWizard(src, (choice) => {
          // straight back to the *same* source's form with the choice filled
          // in, so what the wizard picked is visible and editable rather than
          // hidden — and so a Jira selection cannot land on git's form
          importDialog(src.name, {
            ...now,
            roots: choice.roots.join(","), types: choice.types.join(","),
            open_only: choice.open_only, labels: choice.labels.join(","),
            levels: choice.levels.join(","), roles: choice.roles.join(","),
            dates: choice.dates.join(",") });
        }, { roots: csv(now.roots), types: csv(now.types),
             open_only: now.open_only === true || now.open_only === "true",
             labels: csv(now.labels), levels: String(now.levels || "").split(",").map((x) => x.trim()),
             roles: csv(now.roles), dates: csv(now.dates) });
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
  setLock(data.lock || null);
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
  if (sameMap && pausedForAgent()) return;
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
  setLock((mine && mine.lock) || null);
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

/* An agent that reads the open map locks it until its save lands. The page
   follows along read-only — its saves still arrive as live reloads — and
   "Take over" ends the lock at once. The server then refuses the agent's next
   save until it reads the map again, so nothing either side did is lost. */
function setLock(lock) {
  const was = !!S.locked;
  S.locked = lock;
  document.body.classList.toggle("is-locked", !!lock);
  $("#side-lock").hidden = !lock;
  const bar = $("#lockbar");
  if (!lock) {
    bar.hidden = true;
    if (was) {
      restoreSideAfterLock();
      flashLive(`The agent is done with “${S.name}” — editing is back on.`);
    }
    return;
  }
  if (!was) {
    // nothing in the panel can be used while the agent works, so fold it away
    // and give the canvas the room — and put it back as it was afterwards
    S.sideBeforeLock = S.sideHidden;
    applySide(true, { remember: false });
  }
  if (was && !bar.hidden) {                // already showing: only the countdown moves
    const left = bar.querySelector(".left");
    if (left) left.textContent = lockLeft(lock);
    return;
  }
  bar.hidden = false;
  bar.innerHTML = `<span class="grow"><b>An agent is updating “${esc(S.name)}”.</b>
    Editing is paused and its changes appear here as they are saved${S.dirty
      ? " — your unsaved edits are kept, but cannot be saved until you take over" : ""}.
    <span class="left note">${esc(lockLeft(lock))}</span></span>`;
  const btn = document.createElement("button");
  btn.textContent = "Take over";
  btn.className = "primary";
  btn.title = "end the agent's lock and edit the map yourself";
  btn.addEventListener("click", takeOver);
  bar.appendChild(btn);
}

function restoreSideAfterLock() {
  if (S.sideBeforeLock === null) return;
  applySide(S.sideBeforeLock, { remember: false });
  S.sideBeforeLock = null;
}

function lockLeft(lock) {
  return `The lock ends by itself in ${Math.max(0, lock.seconds_left)} s if the agent stops.`;
}

async function takeOver() {
  const where = S.folder ? `?folder=${encodeURIComponent(S.folder)}` : "";
  try { await api("DELETE", `/api/maps/${encodeURIComponent(S.name)}/lock${where}`); }
  catch (err) { showProblems(err.errors); return; }
  S.locked = null;                          // quietly: this was the person's own doing
  document.body.classList.remove("is-locked");
  $("#lockbar").hidden = true;
  $("#side-lock").hidden = true;
  restoreSideAfterLock();
  flashLive(`You have “${S.name}” — the agent will be told if it tries to save.`);
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
  if (pausedForAgent() || !S.undo.length) return;
  S.redo.push(snapshot());
  restore(S.undo.pop());
  markDirty();
  syncMode();                    // mode and timeline ride on the spec
  refreshPanels();
  scheduleRender();
}

function redo() {
  if (pausedForAgent() || !S.redo.length) return;
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

    // while a ride is being followed, the arrows steer the ride, not a station
    if (NAV.ride !== null && !typing && !mod) {
      const act = {
        " ": navTogglePlay, ArrowLeft: () => navStep(-1), ArrowRight: () => navStep(1),
        Escape: () => navStop(),
        "+": () => { NAV.zoom = NAV.zoomPref = Math.min(6, NAV.zoom + 0.5); NAV.follow = true; },
        "=": () => { NAV.zoom = NAV.zoomPref = Math.min(6, NAV.zoom + 0.5); NAV.follow = true; },
        "-": () => { NAV.zoom = NAV.zoomPref = Math.max(1.5, NAV.zoom - 0.5); NAV.follow = true; },
      }[ev.key];
      if (act) { ev.preventDefault(); act(); return; }
    }

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
  // The preview is drawn in the theme rather than styled into it, so a change
  // of theme means a re-render. core.js announces its own.
  document.addEventListener("core:theme", scheduleRender)

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
  $("#btn-add-lane").addEventListener("click", addLane);
  $("#btn-add-scenario").addEventListener("click", addScenario);
  $("#btn-add-join").addEventListener("click", addJoin);
  $("#btn-milestone").addEventListener("click", milestoneDialog);
  $("#btn-ride-play").addEventListener("click", toggleRides);
  $("#btn-ride-restart").addEventListener("click", restartRides);
  $("#btn-ride-show").addEventListener("click", toggleShowRides);
  $("#btn-new").addEventListener("click", newMap);
  $("#btn-open").addEventListener("click", openDialog);
  $("#btn-save").addEventListener("click", () => saveMap());
  $("#btn-saveas").addEventListener("click", saveAsDialog);
  $("#btn-export").addEventListener("click", exportSVG);
  // Stop, Restart, About and the theme picker belong to the header that
  // core/_base.html draws, and core.js wires them. What it cannot know is that
  // a map lives in this browser until it is saved, so the guard stays here.
  core.setLeavingGuard(confirmLeaving);
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
