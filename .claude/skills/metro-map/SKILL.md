---
name: metro-map
description: Draw transit-map style diagrams — stations on a grid, coloured lines routed through them, zones banding groups of stations — and render them to a standalone light/dark SVG. Use for landscape, migration, architecture, pipeline or roadmap diagrams whenever the shape is "things connected by named paths": SAP landscapes (ECC → RISE → BTP), data flows, deployment topologies, phase plans. Covers the JSON spec, the design order (stations, then lines, then zones), line service states (out of service with dead ends, under construction, planned), label placement and rotation, the metro-map MCP tools, and the browser designer at diagrams/. Triggers on "metro map", "transit map", "landscape diagram", "draw the landscape", "diagram the migration", "tube map", or an ask to edit an existing maps/*.json.
---

# Metro map

A diagram is one JSON spec: **stations** on a grid, **lines** routed through
them, **zones** banding groups of them. `metro_map.py` renders it to a
standalone SVG — octilinear (0° / 45° / 90°) segments, parallel tracks where
lines share a corridor, automatic label placement, light and dark grounds.

Two front ends over the same files: a browser designer (`app.py`, drag stations
on a live canvas) and the `metro-map` MCP tools. They share `maps/*.json`, so a
person and an agent can work on the same diagram.

## Design order — do not skip it

1. **Stations first.** Nothing else can reference a station that does not exist.
   Place them on the grid, then check the shape with `preview_grid`.
2. **Then lines.** A line is an ordered route through station ids. Reusing an id
   in two lines makes that stop an interchange (drawn as a white ring).
3. **Then zones**, if the diagram groups things — "Upgrade", "Preferred".

## The spec

```json
{
  "stations": {
    "s4": {"label": "S/4HANA Private", "gx": 8, "gy": 3,
           "interchange": true,
           "label_at": "above-right", "label_angle": 45}
  },
  "lines": [
    {"name": "Option A · Azure", "color": "#0098d4",
     "stations": ["ecc", "conv", "sit", "s4"]},
    {"name": "Archive", "color": "#e1251b", "status": "out-of-service",
     "stations": ["ecc", "sara", "jivs"]}
  ],
  "zones": [
    {"name": "Preferred", "color": "#00a4a7", "stations": ["azure", "s4"]}
  ],
  "style": {"cell": 120, "stroke": 10, "corner": 22, "bundle_gap": 13,
            "label_size": 16, "zone_pad": 34},
  "editor": {"snap": 0.5}
}
```

| Field | Notes |
|---|---|
| `gx` / `gy` | Grid cells, **not pixels**, and may be fractional — `0.5` steps put two stations half a cell apart. Keep whole numbers unless the layout needs the room. |
| `interchange` | White ring instead of a coloured tick. Leave it out and let `auto_interchange` (on by default) set it. |
| `label_at` | `above`, `below`, `left`, `right`, `above-left`, `above-right`, `below-left`, `below-right`. Omit it and the renderer picks the first side no track leaves on — usually right. Only override when a label collides. |
| `label_angle` | `0` (default), `45` or `90`, counter-clockwise. A tilted label is anchored at the marker edge and reads *outward*, so it never crosses its own station. Use it to fit long names into a tight column. |
| `status` | `live` (default, leave it out), `out-of-service`, `under-construction`, `planned`. |
| `color` | `#rrggbb`. `spec_reference` returns the ten-colour palette the designer offers; stay inside it unless the diagram has its own brand colours. |

**Line states.** `out-of-service` draws dashed and faded, and caps the route
with a buffer-stop bar wherever it ends on a stop no line in service reaches —
that bar is the *dead end*. `under-construction` is a long dash at near-full
strength; `planned` is dotted. Only stops that every line has retired from fade
with their line.

**Zones** are sized to hold their stations *and* the station labels, plus
`style.zone_pad`, and are named above the top-left corner. An empty zone is not
drawn. Zones may overlap; they are painted in list order, behind everything.

**Line order matters.** Lines sharing a corridor are spread into parallel
tracks in list order, so the first line sits innermost.

## Working through MCP

The `metro-map` server (`.mcp.json` in the repo) starts the designer on demand.

| Tool | Use it for |
|---|---|
| `list_maps` | what already exists |
| `read_map(name)` | the whole spec, to edit |
| `save_map(name, spec)` | write it back — validates first, refuses a spec that will not draw |
| `validate_map(spec)` | check before saving; returns `errors` (fatal) and `warnings` (a line with one stop, an empty zone, a station on no line) |
| `preview_grid(name)` | the layout as text — read this instead of the SVG |
| `render_map(name, out_path=…)` | write the SVG to a file; pass `out_path` rather than pulling markup through the transcript |
| `spec_reference` | palette, label sides and angles, line states, style defaults |
| `list_templates` | a SAP landscape and a pipeline to start from |
| `designer_url` | hand the human a link to take over in the browser |
| `stop_designer` | shut the local server down |

Read → modify → `validate_map` → `save_map`. Never hand-write a spec and save it
unvalidated: an unknown station id in a route is the usual mistake, and
validation names it.

## Without MCP

```bash
cd diagrams
./run.sh                    # designer on 127.0.0.1:8765 (restarts if already up)
./run.sh --stop             # or the red Stop button in the toolbar
.\run.ps1                   # same on Windows;  .\run.ps1 -Stop

python3 metro_map.py maps/sap_landscape.json -o landscape.svg
python3 metro_map.py maps/sap_landscape.json --ascii     # the grid as text
python3 metro_map.py spec.json --cell 140 -o big.svg     # flags beat spec.style
```

A spec that will not draw is reported line by line and exits 2.

## Judgement

- **Grid, not pixels.** Two cells between stops on a busy corridor, one where
  things are tight. Reach for fractional steps only when whole cells will not
  fit — they cost legibility.
- **Let labels place themselves.** Set `label_at` only after seeing a collision,
  and `label_angle` only when a column is genuinely too narrow.
- **A branch that is being retired is its own line**, not a status on a shared
  one — that is what makes the dead-end bar land in the right place.
- **Check the render, do not assume it.** `preview_grid` catches a wrong grid
  position; for anything subtler, render to a file and look at it.
