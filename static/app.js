/* Metro map designer.
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
  spec: { stations: {}, lines: [], zones: [] },
  style: { cell: 120, stroke: 10, corner: 22, bundle_gap: 13, label_size: 16 },
  autoIx: true,
  snap: 1,                                 // grid step when dragging or nudging

  sel: { kind: null, id: null },           // "station" | "line"
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
let lastSVG = "";
let TIMELINE = null;                         // ruler the server resolved, or null

const GUIDE_MAP = "how-this-tool-works";     // the map that explains the tool
const LAST_MAP_KEY = "metro-map:last";

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
  spec.lines = spec.lines || [];
  spec.zones = spec.zones || [];
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
  if (nStations === 0) {
    canvas.innerHTML = "";
    lastSVG = "";
    showProblems([]);
    return;
  }
  let data;
  try {
    data = await api("POST", "/api/render", {
      spec: S.spec, style: S.style, auto_interchange: S.autoIx,
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
  } else if (S.sel.kind === "line") {
    const routes = svg.querySelectorAll("#routes .route");
    routes.forEach((p, i) => { if (i !== S.sel.id) p.style.opacity = "0.22"; });
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
    const hit = ev.target.closest("[data-station]");
    if (hit && S.spec.stations[hit.dataset.station]) {
      const id = hit.dataset.station;
      const g = gridAt(ev);
      const st = S.spec.stations[id];
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
      const st = S.spec.stations[drag.id];
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
  if (tab === "lines" && S.sel.kind === "line" && S.spec.lines[S.sel.id]) {
    applyChange(() => S.spec.lines[S.sel.id].stations.push(id));
    return;
  }
  if (tab === "zones" && S.sel.kind === "zone" && S.spec.zones[S.sel.id]) {
    toggleZoneMember(S.sel.id, id);
    return;
  }
  select("station", id);
}

function select(kind, id) {
  S.sel = { kind, id };
  refreshPanels();
  decorate();
  setHint();
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
    text = `Adding to “${S.spec.lines[S.sel.id].name}” — click stations on the canvas in order`;
  } else if (tab === "lines") {
    text = "Pick a line to edit its route";
  } else if (tab === "zones" && S.sel.kind === "zone" && S.spec.zones[S.sel.id]) {
    text = `Filling “${S.spec.zones[S.sel.id].name}” — click stations on the canvas to add or remove them`;
  } else if (tab === "zones") {
    text = "Pick a zone to choose which stations sit in it";
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
  renderTimeline();
  renderStyle();
  syncToolbar();
}

function linesUsing(sid) {
  return S.spec.lines.filter((ln) => ln.stations.includes(sid));
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
      </li>`;
    }).join("");
    list.querySelectorAll("[data-sid]").forEach((row) =>
      row.addEventListener("click", () => select("station", row.dataset.sid)));
  }
  renderStationEditor();
}

function renderStationEditor() {
  const box = $("#station-editor");
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
  if (!iso || !cols.length || iso < cols[0].date) return null;
  let found = null;
  for (const col of cols) {              // ISO dates compare correctly as strings
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
    for (const ln of S.spec.lines) ln.stations = ln.stations.filter((s) => s !== sid);
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
      </li>`;
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
  if (S.sel.kind !== "line" || !S.spec.lines[S.sel.id]) { box.innerHTML = ""; return; }
  const i = S.sel.id;
  const ln = S.spec.lines[i];
  const stationIds = Object.keys(S.spec.stations).sort((a, b) => {
    const A = S.spec.stations[a], B = S.spec.stations[b];
    return A.gy - B.gy || A.gx - B.gx;
  });

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
    ${(ln.status || "live") === "out-of-service"
      ? `<p class="note">Drawn dashed and faded, with a dead-end bar wherever the route ends on a stop no line in service reaches.</p>` : ""}

    <h3>Route — ${ln.stations.length} stop${ln.stations.length === 1 ? "" : "s"}</h3>
    ${ln.stations.length < 2 ? `<p class="warn">A line needs at least two stops to render.</p>` : ""}
    <div class="route" id="l-route">
      ${ln.stations.map((sid, k) => `
        <div class="stop-row" draggable="true" data-pos="${k}">
          <span class="n">${k + 1}</span>
          <span class="grow">${esc((S.spec.stations[sid] || {}).label || sid)}
            <span class="id">${esc(sid)}</span></span>
          <button class="ghost" data-drop="${k}" title="remove from route">×</button>
        </div>`).join("") || `<p class="note">Empty — pick stations below, or click them on the canvas.</p>`}
    </div>

    <h3>Available stations</h3>
    <div class="pick" id="l-pick">
      ${stationIds.length ? stationIds.map((sid) =>
        `<button type="button" data-add="${esc(sid)}"
           class="${ln.stations.includes(sid) ? "on-route" : ""}"
           title="${ln.stations.includes(sid) ? "already on this route — adding again makes it a repeat stop" : "append to the route"}"
         >${esc(S.spec.stations[sid].label)}</button>`).join("")
        : `<p class="note">No stations yet — add them in the Stations tab first.</p>`}
    </div>

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
  box.querySelectorAll("[data-add]").forEach((btn) =>
    btn.addEventListener("click", () => applyChange(() => ln.stations.push(btn.dataset.add))));
  box.querySelectorAll("[data-drop]").forEach((btn) =>
    btn.addEventListener("click", () => applyChange(() => ln.stations.splice(Number(btn.dataset.drop), 1))));

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
        const [sid] = ln.stations.splice(from, 1);
        ln.stations.splice(to, 0, sid);
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
      const n = (zn.stations || []).length;
      return `<li class="row ${S.sel.kind === "zone" && S.sel.id === i ? "is-on" : ""}" data-zone="${i}">
        <span class="swatch band" style="background:${esc(zn.color)}"></span>
        <span class="grow">
          <span class="lbl">${esc(zn.name)}</span>
          <span class="at">${n ? `${n} station${n === 1 ? "" : "s"}` : "empty"}</span>
        </span>
      </li>`;
    }).join("");
    list.querySelectorAll("[data-zone]").forEach((row) =>
      row.addEventListener("click", () => select("zone", Number(row.dataset.zone))));
  }
  renderZoneEditor();
}

function renderZoneEditor() {
  const box = $("#zone-editor");
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

function toggleZoneMember(zoneIndex, sid) {
  const zn = S.spec.zones[zoneIndex];
  if (!zn) return;
  applyChange(() => {
    zn.stations = zn.stations || [];
    const at = zn.stations.indexOf(sid);
    if (at >= 0) zn.stations.splice(at, 1); else zn.stations.push(sid);
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
  const tl = S.spec.timeline || (S.spec.timeline = defaultTimeline());

  // rebuilt only when the values changed under it — otherwise a date input
  // would lose focus on every keystroke that triggers a re-render
  const stamp = `${tl.start}|${tl.end}|${tl.interval}`;
  if (box.dataset.built === stamp) { renderTimelineReadout(); return; }

  box.innerHTML = `<div class="card">
    <div class="pair">
      <label class="field"><span>Start</span>
        <input type="date" id="t-start" value="${esc(tl.start)}"></label>
      <label class="field"><span>End</span>
        <input type="date" id="t-end" value="${esc(tl.end)}"></label>
    </div>
    <label class="field"><span>One column is</span><select id="t-interval">
      ${INTERVALS.map((iv) =>
        `<option value="${esc(iv)}" ${tl.interval === iv ? "selected" : ""}>${esc(iv)}</option>`).join("")}
    </select></label>
    <p class="note" id="t-readout"></p>
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
  setHint();
}

/* --------------------------------------------------------------- style -- */

const STYLE_FIELDS = [
  ["cell", "Pixels per grid cell", 40, 240, 5],
  ["stroke", "Route width", 2, 26, 1],
  ["corner", "Corner radius", 0, 60, 1],
  ["bundle_gap", "Gap between parallel tracks", 4, 40, 1],
  ["label_size", "Label size", 8, 40, 1],
  ["zone_pad", "Zone band padding", 8, 90, 2],
];

const SNAP_STEPS = [[1, "1 cell"], [0.5, "½ cell"], [1 / 3, "⅓ cell"], [0.25, "¼ cell"]];

function renderStyle() {
  const box = $("#style-editor");
  if (box.dataset.built === "1") {
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
    <label class="field"><span>Grid snap — how close two stations may sit</span>
      <select id="f-snap">${SNAP_STEPS.map(([v, label]) =>
        `<option value="${v}" ${Math.abs(v - S.snap) < 1e-6 ? "selected" : ""}>${label}</option>`).join("")}
      </select></label>
    ${STYLE_FIELDS.map(([key, label, min, max, step]) => `
    <label class="field"><span>${esc(label)} — <b data-style-out="${key}">${S.style[key]}</b></span>
      <input type="range" data-style="${key}" min="${min}" max="${max}" step="${step}" value="${S.style[key]}">
    </label>`).join("")}</div>`;
  box.dataset.built = "1";
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
  applyChange(() => {
    for (const st of Object.values(S.spec.stations)) {
      if (dx && past(st.gx, ax)) st.gx = snapTo(st.gx + dx);
      if (dy && past(st.gy, ay)) st.gy = snapTo(st.gy + dy);
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

function exportSVG() {
  if (!lastSVG) { showProblems(["nothing rendered to export yet"]); return; }
  const blob = new Blob([lastSVG], { type: "image/svg+xml;charset=utf-8" });
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

function stopServer() {
  const warning = S.dirty
    ? "This map has unsaved changes that will be lost.\n\nStop the designer anyway?"
    : "Stop the designer? This shuts down the local server.";
  if (!confirm(warning)) return;
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
    PALETTE = palette;
    S.style = { ...defaults.style };
  } catch (err) {
    showProblems(err.errors || ["could not reach the server"]);
  }

  $("#mode-select").innerHTML = MODES.map((m) =>
    `<option value="${esc(m.value)}">${esc(m.label)}</option>`).join("");
  $("#mode-select").addEventListener("change", (ev) => setMode(ev.target.value));

  initTabs();
  initCanvas();
  initKeys();
  syncMode();

  $("#btn-add-station").addEventListener("click", addStation);
  $("#btn-add-line").addEventListener("click", addLine);
  $("#btn-add-zone").addEventListener("click", addZone);
  $("#btn-new").addEventListener("click", newMap);
  $("#btn-open").addEventListener("click", openDialog);
  $("#btn-save").addEventListener("click", () => saveMap());
  $("#btn-saveas").addEventListener("click", saveAsDialog);
  $("#btn-export").addEventListener("click", exportSVG);
  $("#btn-stop").addEventListener("click", stopServer);
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
