# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ERP-LAB-5
"""
build.py — Metrofy's four steps: check, place, render, wrap.

    check(doc)          every problem with a Metrofy input, as sentences
    place(doc)          the metro-map spec: grid positions decided here
    page(doc, spec, svg) the standalone HTML page around the rendered map
    finalize(doc)       all of it, plus the checks on what came out, as a
                        receipt; nothing is returned as a page unless every
                        check passed

Standard library only, like the renderer it drives.
"""

from __future__ import annotations

import hashlib
import math
import html
import json
import re
from typing import Any, Dict, List, Tuple

from .. import metro_map as mm

FORMAT = 1
ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
STATUS = {                     # Metrofy's words for a line, and the renderer's
    "planned": "planned",
    "building": "under-construction",
    "live": "live",
    "dropped": "out-of-service",
}
STATUS_WORD = {"planned": "planned", "building": "being built", "live": "live", "dropped": "dropped"}
#: the process line's colour, and the lines' colours in turn when none is given
SPINE_COLOUR = "#8f9aa4"
LINE_COLOURS = [c for n, c in mm.PALETTE if n != "grey"]
ZONE_COLOURS = ["#0098d4", "#7b3fb5", "#ee7c0e", "#007d32", "#9b0058", "#00a4a7"]
#: grid: columns between steps, rows per zone, and the row a zone's steps sit on
STEP_GAP = 2
#: with phases, the grid is a compact staircase drawn with free angles (the
#: renderer's "angles": "free"): zones are LANE rows apart, and within a
#: phase each zone's station stands a little right of the one above, so a
#: line changing zones inside a phase runs at STEEPEST degrees, never
#: straight down. Between phases the next phase starts just far enough on
#: that even the steepest change (the bottom zone to the top one) is no
#: steeper than that: every line is horizontal or between about 20 and 60
#: degrees, which keeps the map far shorter than 45-degree routing can.
LANE = 1.25
STEEPEST = 60.0
#: the least room between two phases' bands, in columns, and the least step
#: from one phase to the next, so labels in one lane do not meet
PHASE_MARGIN = 1.0
PHASE_MIN = 2.6
#: how far a phase band reaches past its stations: slimmer than a zone's
PHASE_PAD = 12.0
LANE_ROWS = 3
SPINE_NAME = "The process"
#: a phase's band, when it names no colour: quiet, so the lanes stay readable
PHASE_COLOUR = "#8f9aa4"
#: the renderer's own colour for a trip that names none
TRIP_COLOUR = "#e1251b"
#: a tighter grid than the designer's default, so the text stays readable once
#: the map is scaled to the page: a generated map has no hand-tuned spacing
STYLE = {"cell": 80.0, "label_size": 15.0, "zone_label_size": 14.0, "legend_size": 14.0}


def _ids(items: Any, what: str, errors: List[str]) -> List[str]:
    out: List[str] = []
    if not isinstance(items, list):
        errors.append(f"{what} must be a list")
        return out
    for i, it in enumerate(items, 1):
        if not isinstance(it, dict):
            errors.append(f"{what} {i} must be an object")
            continue
        iid = it.get("id")
        if not isinstance(iid, str) or not ID.match(iid):
            errors.append(f"{what} {i}: id must be lowercase letters, digits, - or _ (got {iid!r})")
            continue
        if iid in out:
            errors.append(f"{what} {i}: id '{iid}' is used twice")
        out.append(iid)
    return out


def _text(it: dict, key: str, where: str, errors: List[str], required: bool = True) -> None:
    v = it.get(key)
    if v is None and not required:
        return
    if not isinstance(v, str) or not v.strip():
        errors.append(f"{where}: needs a non-empty {key}")


def _details(it: dict, where: str, errors: List[str]) -> None:
    d = it.get("details")
    if d is None:
        return
    if not isinstance(d, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in d.items()):
        errors.append(f"{where}: details must map names to text")


def _colour(it: dict, where: str, errors: List[str]) -> None:
    c = it.get("color")
    if c is not None and not (isinstance(c, str) and HEX.match(c)):
        errors.append(f"{where}: color must be #rrggbb")


def check(doc: Any) -> List[str]:
    """Every problem with a Metrofy input, as sentences a person can act on."""
    if not isinstance(doc, dict):
        return ["a Metrofy input is a JSON object"]
    errors: List[str] = []
    if doc.get("metrofy") != FORMAT:
        errors.append(f'"metrofy" must be {FORMAT}, the input format this version reads')
    _text(doc, "title", "the map", errors)
    allowed = {"metrofy", "title", "subtitle", "zones", "phases", "steps", "lines", "trips", "spine", "legend",
               "details"}
    for k in sorted(set(doc) - allowed):
        errors.append(f'unknown field "{k}": a Metrofy input has only ' + ", ".join(sorted(allowed)))
    _details(doc, "the map", errors)
    if "spine" in doc and not isinstance(doc["spine"], bool):
        errors.append("spine must be true or false")
    if "legend" in doc and not isinstance(doc["legend"], bool):
        errors.append("legend must be true or false")

    zones = _ids(doc.get("zones"), "zone", errors)
    for i, z in enumerate(doc.get("zones") or [], 1):
        if isinstance(z, dict):
            _text(z, "name", f"zone {i}", errors)
            _colour(z, f"zone {i}", errors)
    if not zones:
        errors.append("at least one zone is needed: every step sits in one")

    phases = _ids(doc["phases"], "phase", errors) if "phases" in doc else []
    for i, ph in enumerate(doc.get("phases") or [], 1):
        if isinstance(ph, dict):
            _text(ph, "name", f"phase {i}", errors)
            _colour(ph, f"phase {i}", errors)
            _details(ph, f"phase {i}", errors)

    steps = _ids(doc.get("steps"), "step", errors)
    seen: List[str] = []
    cells: Dict[Tuple[Any, Any], str] = {}
    for i, s in enumerate(doc.get("steps") or [], 1):
        if not isinstance(s, dict):
            continue
        where = f"step {i} ('{s.get('id')}')"
        _text(s, "label", where, errors)
        _details(s, where, errors)
        if s.get("zone") not in zones:
            errors.append(f"{where}: zone must be one of the zones' ids (got {s.get('zone')!r})")
        if "phases" in doc:
            if s.get("phase") not in phases:
                errors.append(f"{where}: phase must be one of the phases' ids (got {s.get('phase')!r})")
            elif (s.get("phase"), s.get("zone")) in cells:
                errors.append(f"{where}: shares phase '{s.get('phase')}' and zone '{s.get('zone')}' with step "
                              f"'{cells[(s.get('phase'), s.get('zone'))]}'; one station per phase and zone")
            else:
                cells[(s.get("phase"), s.get("zone"))] = s.get("id")
        elif s.get("phase") is not None:
            errors.append(f"{where}: has a phase, but the map lists no phases")
        parent = s.get("parent")
        if parent is not None and parent not in seen:
            errors.append(f"{where}: parent '{parent}' must be a step listed before it")
        seen.append(s.get("id"))
    if len(steps) < 2:
        errors.append("at least two steps are needed to draw a line between them")

    lines = _ids(doc.get("lines"), "line", errors)
    for i, ln in enumerate(doc.get("lines") or [], 1):
        if not isinstance(ln, dict):
            continue
        where = f"line {i} ('{ln.get('id')}')"
        _text(ln, "name", where, errors)
        _colour(ln, where, errors)
        _details(ln, where, errors)
        if ln.get("status", "planned") not in STATUS:
            errors.append(f"{where}: status must be one of " + ", ".join(STATUS))
        st = ln.get("steps")
        if not isinstance(st, list) or not all(isinstance(x, str) for x in st):
            errors.append(f"{where}: steps must be a list of step ids")
            continue
        for x in st:
            if x not in steps:
                errors.append(f"{where}: unknown step '{x}'")
        if len(set(st)) != len(st):
            errors.append(f"{where}: a step is listed twice")
        if len(set(st)) < 2:
            errors.append(f"{where}: a line needs at least two steps; a story touching one step "
                          "rides the process line instead")
    names = [ln.get("name") for ln in doc.get("lines") or [] if isinstance(ln, dict)]
    if doc.get("spine", True) and SPINE_NAME in names:
        errors.append(f'"{SPINE_NAME}" is the process line\'s name; give the story another')
    if len(set(names)) != len(names):
        errors.append("two lines share a name; the legend tells lines apart by name")

    _ids(doc.get("trips") or [], "trip", errors)
    for i, tr in enumerate(doc.get("trips") or [], 1):
        if not isinstance(tr, dict):
            continue
        where = f"trip {i} ('{tr.get('id')}')"
        _text(tr, "name", where, errors)
        _colour(tr, where, errors)
        _details(tr, where, errors)
        if tr.get("line") is not None and tr["line"] not in lines:
            errors.append(f"{where}: line must be one of the lines' ids")
        d = tr.get("duration")
        if d is not None and (isinstance(d, bool) or not isinstance(d, (int, float)) or not 0 < d <= 600):
            errors.append(f"{where}: duration must be seconds between 0 and 600")
        stops = tr.get("stops")
        if not isinstance(stops, list) or len(stops) < 2 or not all(isinstance(x, str) for x in stops):
            errors.append(f"{where}: stops must list at least two step ids")
            continue
        for x in stops:
            if x not in steps:
                errors.append(f"{where}: unknown step '{x}'")
    return errors


def _column(doc: dict) -> Dict[str, int]:
    """Each step's column: its phase's place when there are phases, else its own."""
    if doc.get("phases"):
        at = {ph["id"]: k for k, ph in enumerate(doc["phases"])}
        return {s["id"]: at[s["phase"]] for s in doc["steps"]}
    return {s["id"]: i for i, s in enumerate(doc["steps"])}


def _order(doc: dict) -> Dict[str, Tuple[int, int]]:
    """Process order: by column, then by zone (top to bottom), then as listed."""
    col = _column(doc)
    lane = {z["id"]: k for k, z in enumerate(doc["zones"])}
    return {s["id"]: (col[s["id"]], lane[s["zone"]], i) for i, s in enumerate(doc["steps"])}


def place(doc: dict) -> dict:
    """The metro-map spec for a checked input. Every position is decided here.

    Steps keep the order they are listed in, which is the process order, one
    column pair apart; each sits on the middle row of its zone's swimlane.
    With phases, the column is the phase's: the steps of one phase stand one
    above the other, one per zone, and the phase is a named band around them.
    Lines visit their steps in that order whatever order they were listed in,
    so a story can never run backwards through the process.
    """
    order = _order(doc)
    col = _column(doc)
    zones = doc["zones"]
    phased = bool(doc.get("phases"))
    lane_rows = LANE if phased else LANE_ROWS
    stair = LANE / math.tan(math.radians(STEEPEST))           # columns per zone inside a phase
    row = {z["id"]: k * lane_rows + 1 for k, z in enumerate(zones)}
    lane_at = {z["id"]: k for k, z in enumerate(zones)}
    half = (lane_rows - 0.1) / 2 if phased else 1
    # a phase is (zones - 1) stairs wide; the next starts when the steepest
    # change from its last stair to the next phase's first is still 45 degrees
    span = stair * (len(zones) - 1)                             # a phase's own width
    drop = LANE * (len(zones) - 1)                              # the steepest change between phases
    phase_step = max(span + drop / math.tan(math.radians(STEEPEST)) + PHASE_MARGIN * (span > 0), PHASE_MIN)
    parents = {s.get("parent") for s in doc["steps"] if s.get("parent")}
    stations = {}
    for i, s in enumerate(doc["steps"]):
        if phased:
            gx = round(col[s["id"]] * phase_step + lane_at[s["zone"]] * stair, 3)
        else:
            gx = col[s["id"]] * STEP_GAP
        st = {"label": s["label"], "gx": gx, "gy": row[s["zone"]]}
        if s["id"] in parents:
            st["interchange"] = True
        stations[s["id"]] = st
    lines = []
    if doc.get("spine", True):
        lines.append({"name": SPINE_NAME, "color": SPINE_COLOUR, "status": "live",
                      "stations": sorted((s["id"] for s in doc["steps"]), key=order.__getitem__)})
    for k, ln in enumerate(doc.get("lines") or []):
        lines.append({"name": ln["name"], "color": ln.get("color") or LINE_COLOURS[k % len(LINE_COLOURS)],
                      "status": STATUS[ln.get("status", "planned")],
                      "stations": sorted(dict.fromkeys(ln["steps"]), key=order.__getitem__)})
    for ln in lines:
        if ln["status"] == "live":
            del ln["status"]
    scenarios = []
    colour_of = {ln["id"]: lines[k + (1 if doc.get("spine", True) else 0)]["color"]
                 for k, ln in enumerate(doc.get("lines") or [])}
    for tr in doc.get("trips") or []:
        sc = {"name": tr["name"], "stations": list(tr["stops"])}
        colour = tr.get("color") or colour_of.get(tr.get("line"))
        if colour:
            sc["color"] = colour
        if tr.get("duration"):
            sc["duration"] = tr["duration"]
        scenarios.append(sc)
    spec = {
        "stations": stations,
        "lines": lines,
        # lanes may not touch (the renderer reads touching as overlapping):
        # phased lanes leave a tenth of a row between them
        "swimlanes": [{"name": z["name"], "rows": [row[z["id"]] - half, row[z["id"]] + half],
                       "color": z.get("color") or ZONE_COLOURS[k % len(ZONE_COLOURS)]}
                      for k, z in enumerate(zones)],
        "legend": "bottom" if doc.get("legend", True) else "hide",
        "style": dict(STYLE),
    }
    if doc.get("phases"):
        spec["style"]["zone_pad"] = PHASE_PAD
        spec["angles"] = "free"
        spec["zones"] = [{"name": ph["name"], "color": ph.get("color") or PHASE_COLOUR,
                          "stations": [s["id"] for s in doc["steps"] if s["phase"] == ph["id"]]}
                         for ph in doc["phases"] if any(s["phase"] == ph["id"] for s in doc["steps"])]
    if scenarios:
        spec["scenarios"] = scenarios
    spec["format"] = mm.needs_format(spec)
    return spec


# ---------------------------------------------------------------- the page --

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="Metrofy {version}">
<title>{title}</title>
<style>
:root {{ --bg: #ffffff; --fg: #1d2329; --muted: #5b6670; --panel: #f4f6f8; --rule: #dde2e6; --accent: #0098d4; }}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{ --bg: #14181c; --fg: #e8ecef; --muted: #9aa5ae; --panel: #1e2328; --rule: #2e353b; --accent: #4fc3f7; }}
}}
:root[data-theme="dark"] {{ --bg: #14181c; --fg: #e8ecef; --muted: #9aa5ae; --panel: #1e2328; --rule: #2e353b; --accent: #4fc3f7; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--fg);
  font: 15px/1.45 "Hanken Grotesk", "Helvetica Neue", Helvetica, Arial, sans-serif; }}
header, main {{ max-width: 1400px; margin: 0 auto; padding: 16px; }}
h1 {{ font-size: 22px; margin: 8px 0 2px; }}
.sub {{ color: var(--muted); margin: 0; }}
.layout {{ display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 16px; align-items: start; }}
@media (max-width: 860px) {{ .layout {{ grid-template-columns: 1fr; }} }}
.map {{ overflow: auto; border: 1px solid var(--rule); border-radius: 10px; padding: 8px; }}
.map svg {{ display: block; width: 100%; height: auto; }}
.map [data-station], .map path[class*="route"] {{ cursor: pointer; }}
aside {{ background: var(--panel); border-radius: 10px; padding: 12px 14px; }}
aside h2 {{ font-size: 15px; margin: 4px 0 8px; }}
.trips button {{ display: block; width: 100%; text-align: left; margin: 0 0 6px; padding: 8px 10px;
  border: 1px solid var(--rule); border-radius: 8px; background: var(--bg); color: var(--fg); font: inherit; cursor: pointer; }}
.trips button:hover, .trips button[aria-pressed="true"] {{ border-color: var(--accent); }}
.swatch {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; vertical-align: middle; }}
dl {{ margin: 0; }} dt {{ color: var(--muted); font-size: 13px; margin-top: 6px; }} dd {{ margin: 0; }}
.hint {{ color: var(--muted); font-size: 13px; }}
</style>
</head>
<body>
<header>
  <h1>{title}</h1>
  {subtitle}
</header>
<main class="layout">
  <div class="map" id="map">{svg}</div>
  <aside>
    <section class="trips" aria-label="Trips">
      <h2>Trips</h2>
      {trips}
    </section>
    <section aria-live="polite">
      <h2 id="d-title">Details</h2>
      <div id="d-body"><p class="hint">Choose a station, a line or a trip.</p></div>
    </section>
  </aside>
</main>
<script id="metrofy-data" type="application/json">{data}</script>
<script>
(() => {{
  const data = JSON.parse(document.getElementById("metrofy-data").textContent);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}}[c]));
  const show = (title, rows) => {{
    document.getElementById("d-title").textContent = title;
    document.getElementById("d-body").innerHTML = rows.length
      ? "<dl>" + rows.map(([k, v]) => `<dt>${{esc(k)}}</dt><dd>${{esc(v)}}</dd>`).join("") + "</dl>"
      : '<p class="hint">Nothing more is recorded.</p>';
  }};
  const svg = document.querySelector("#map svg");
  const trips = svg ? [...svg.querySelectorAll("#scenarios > *")] : [];
  const play = (k) => {{
    // One trip at a time: the others are hidden, and the chosen one restarts.
    trips.forEach((g, i) => {{ g.style.display = k === null || i === k ? "" : "none"; }});
    document.querySelectorAll(".trips button").forEach((b, i) => b.setAttribute("aria-pressed", String(i === k)));
    if (svg && svg.setCurrentTime) svg.setCurrentTime(0);
  }};
  document.querySelectorAll(".trips button").forEach((b, k) => b.addEventListener("click", () => {{
    play(k);
    const t = data.trips[k];
    show(t.name, [["stops", t.stops.join(" → ")], ...(t.line ? [["on", t.line]] : []), ...Object.entries(t.details || {{}})]);
  }}));
  document.getElementById("map").addEventListener("click", (e) => {{
    // A station wins over a route drawn across it: what is under the pointer
    // is asked, not only the topmost element.
    const st = document.elementsFromPoint(e.clientX, e.clientY).map((el) => el.closest("[data-station]")).find(Boolean);
    if (st) {{
      const s = data.steps[st.getAttribute("data-station")];
      if (s) show(s.label, [["zone", s.zone], ...Object.entries(s.details || {{}})]);
      return;
    }}
    const route = e.target.closest('path[class*="route"]');
    const m = route && /\\bl(\\d+)\\b/.exec(route.getAttribute("class"));
    if (m) {{
      const ln = data.lines[Number(m[1])];
      if (ln) show(ln.name, [["status", ln.status], ["steps", ln.steps.join(" → ")], ...Object.entries(ln.details || {{}})]);
    }}
  }});
  if (trips.length > 1) play(0);
}})();
</script>
</body>
</html>
"""


def data(doc: dict, spec: dict) -> dict:
    """What an app needs beside the SVG to make the map its own.

    Keyed the way the SVG marks things, so a click can be answered without
    reading the drawing: `steps` by the `data-station` id, `lines` by the
    index in a route's `l<i>` class (the process line, when drawn, is 0), and
    `trips` in the order of the children of the SVG's `#scenarios` group.
    """
    zone_name = {z["id"]: z["name"] for z in doc["zones"]}
    phase_name = {ph["id"]: ph["name"] for ph in doc.get("phases") or []}
    label = {s["id"]: s["label"] for s in doc["steps"]}
    spine = [{"name": SPINE_NAME, "status": "live", "steps": [label[x] for x in spec["lines"][0]["stations"]],
              "details": {"what": "every step, in process order"}}] if doc.get("spine", True) else []
    out = {
        "title": doc["title"],
        "subtitle": doc.get("subtitle") or "",
        "steps": {s["id"]: {"label": s["label"], "zone": zone_name[s["zone"]], "parent": s.get("parent"),
                            "phase": phase_name.get(s.get("phase")), "details": s.get("details") or {}}
                  for s in doc["steps"]},
        "phases": [{"id": ph["id"], "name": ph["name"], "details": ph.get("details") or {}}
                   for ph in doc.get("phases") or []],
        "lines": spine + [{"id": ln["id"], "name": ln["name"], "status": STATUS_WORD[ln.get("status", "planned")],
                           "stations": list(spec["lines"][k + len(spine)]["stations"]),
                           "steps": [label[x] for x in spec["lines"][k + len(spine)]["stations"]],
                           "details": ln.get("details") or {}}
                          for k, ln in enumerate(doc.get("lines") or [])],
        "trips": [{"id": t["id"], "name": t["name"], "stops": [label[x] for x in t["stops"]],
                   "stations": list(t["stops"]),
                   "line": next((ln["name"] for ln in doc.get("lines") or [] if ln["id"] == t.get("line")), None),
                   "color": sc.get("color") or TRIP_COLOUR, "details": t.get("details") or {}}
                  for t, sc in zip(doc.get("trips") or [], spec.get("scenarios") or [])],
    }
    for ln, drawn in zip(out["lines"], spec["lines"]):
        ln["color"] = drawn["color"]
    return out


def page(doc: dict, spec: dict, svg: str, version: str) -> str:
    """The standalone page: the map, its trips, and a details panel. No network."""
    d = data(doc, spec)
    colours = [t["color"] for t in d["trips"]]
    trips = "\n      ".join(
        f'<button type="button" aria-pressed="false"><span class="swatch" style="background:{html.escape(c)}">'
        f'</span>{html.escape(t["name"])}</button>' for t, c in zip(doc.get("trips") or [], colours)) \
        or '<p class="hint">No trips on this map.</p>'
    sub = f'<p class="sub">{html.escape(doc["subtitle"])}</p>' if doc.get("subtitle") else ""
    blob = json.dumps({k: d[k] for k in ("steps", "lines", "trips")}, ensure_ascii=False, sort_keys=True).replace("</", "<\\/")
    return PAGE.format(title=html.escape(doc["title"]), subtitle=sub, svg=svg, trips=trips,
                       data=blob, version=html.escape(version))


# ------------------------------------------------------------ finalize --

def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def finalize(doc: Any, version: str = "") -> Tuple[Dict[str, Any], str]:
    """(receipt, page). The page is empty unless the receipt says ok."""
    from .. import __version__
    receipt, made = parts(doc)
    if not receipt["ok"]:
        return receipt, ""
    out = page(doc, receipt["spec"], made["svg"], version or __version__)
    receipt["output"] = _sha(out)
    return receipt, out


def parts(doc: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """(receipt, {svg, data}): the map for an app to show in its own layout.

    The same gates as `finalize`, which is built on this. The SVG is the
    renderer's, light and dark, with its trips; `data` is `data()`. Both are
    empty unless the receipt says ok.
    """
    receipt: Dict[str, Any] = {"ok": False, "format": FORMAT, "gates": [], "errors": [], "warnings": []}

    def gate(name: str, problems: List[str]) -> bool:
        receipt["gates"].append({"gate": name, "ok": not problems})
        receipt["errors"].extend(f"{name}: {p}" for p in problems)
        return not problems

    if not gate("input", check(doc)):
        return receipt, {}
    receipt["input"] = _sha(json.dumps(doc, ensure_ascii=False, sort_keys=True))
    spec = place(doc)
    if not gate("spec", mm.validate_spec(spec)):
        return receipt, {}
    warnings = mm.spec_warnings(spec)
    # A trip the renderer would skip, or a step outside its lane, is a broken
    # map, not a cosmetic note: it fails here instead of shipping half drawn.
    fatal = [w for w in warnings if "not drawn" in w or "outside every swimlane" in w or "overlap" in w]
    if not gate("trips and lanes", fatal):
        return receipt, {}
    receipt["warnings"] = [w for w in warnings if w not in fatal]
    svg = mm.render(spec, mm.style_from(spec["style"]), "auto")
    drawn = set(re.findall(r'data-station="([^"]+)"', svg))
    missing = [f"step '{s}' was not drawn" for s in spec["stations"] if s not in drawn]
    if spec.get("scenarios") and 'id="scenarios"' not in svg:
        missing.append("the trips were not drawn")
    if not gate("render", missing):
        return receipt, {}
    receipt.update(ok=True, svg=_sha(svg), counts={
        "zones": len(doc["zones"]), "steps": len(doc["steps"]), "lines": len(doc.get("lines") or []),
        "trips": len(doc.get("trips") or [])}, spec=spec)
    return receipt, {"svg": svg, "data": data(doc, spec)}
