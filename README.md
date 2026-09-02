# diagrams

Transit-map diagrams for landscapes, pipelines and migrations: a JSON spec of
**stations** on a grid and **lines** through them, rendered to a standalone,
octilinear, light/dark-aware SVG.

## The web designer

```bash
./run.sh                 # creates .venv on first run, then serves 127.0.0.1:8765
./run.sh --stop          # shut it down
.\run.ps1                # Windows: same thing  (.\run.ps1 -Stop to stop)
```

Both scripts replace an instance already holding the port rather than failing on
it, so re-running one is how you restart. The red **Stop** button in the toolbar
shuts the server down from the browser.

- **Stations first.** Add a station, then drag its dot on the canvas — it snaps
  to the grid and the map re-routes live. Arrow keys nudge, Delete removes.
- **Then lines.** Pick a line and the editor lists every station placed so far;
  click one to append it to the route, or click stations straight on the canvas
  in the order you want them visited. Drag stops to reorder, `×` to drop one.
  Line order matters: lines sharing a corridor are drawn as parallel tracks in
  list order.
- **Service state.** A line is in service unless you say otherwise. *Out of
  service* draws it dashed and faded and caps it with a buffer-stop bar wherever
  the route ends on a stop nothing in service still reaches — the dead end.
  *Under construction* is a long dash at near-full strength; *planned* is dotted.
- **Zones** band a group of stations — "Upgrade", "Preferred", whatever the
  diagram needs. Pick a zone and click stations in the picker or on the canvas
  to put them in or take them out; the band is drawn behind everything, sized to
  hold those stations and their labels, and named above its top-left corner.
- **Grid snap** (Style tab) sets how close two stations may sit: a whole cell by
  default, or a half, third or quarter of one. It applies to dragging, the arrow
  keys and the grid-x/y fields, and is saved with the map.
- **Labels** place themselves on the first free side of a station; override the
  side per station, and tilt the text 0°, 45° or 90° when a column is tight —
  a tilted label reads outward from its stop.
- **Style** (cell size, route width, corner radius, track spacing, label size,
  zone padding) is saved with the map.
- Maps live in `maps/*.json` — Open, Save, Save as, Export SVG in the top bar.
  Ctrl+Z / Ctrl+Shift+Z undo and redo; Ctrl+S saves.

Any stop shared by two or more lines is drawn as a white interchange ring while
the *interchanges* toggle is on.

## The command line

The same specs render headlessly, so a map designed in the browser drops
straight into a build or a docs pipeline:

```bash
python3 metro_map.py maps/sap_landscape.json -o landscape.svg
python3 metro_map.py maps/sap_landscape.json --cell 140 --bundle-gap 14 -o big.svg
python3 metro_map.py maps/sap_landscape.json --ascii      # rough grid in the terminal
cat spec.json | python3 metro_map.py - > map.svg
```

Flags override the `style` block saved in the spec. A spec that would not draw
is reported line by line and exits 2, so it fails loudly in CI.

`metro_map.py` is standard library only; only the web designer (`app.py`) needs
Flask.

## For agents

`mcp_server.py` exposes the designer over MCP (stdio), starting it on demand.
`.mcp.json` registers it for this repo; point another agent at it with:

```json
{"mcpServers": {"metro-map": {
  "command": "/path/to/diagrams/.venv/bin/python",
  "args": ["/path/to/diagrams/mcp_server.py"]}}}
```

Tools: `list_maps`, `read_map`, `save_map`, `delete_map`, `render_map`,
`validate_map`, `preview_grid`, `list_templates`, `spec_reference`,
`designer_url`, `stop_designer`. Agent and human share `maps/`, so a save on
one side is visible on the other.

`.claude/skills/metro-map/SKILL.md` teaches an agent the spec, the design order
and the judgement calls. Link it into `~/.claude/skills/` to use it outside this
repo:

```bash
ln -s "$PWD/.claude/skills/metro-map" ~/.claude/skills/metro-map
```

## Spec shape

```json
{
  "stations": {
    "s4": {"label": "S/4HANA Private", "gx": 8, "gy": 3,
           "interchange": true, "label_at": "above-right"}
  },
  "lines": [
    {"name": "Option A · Azure", "color": "#0098d4",
     "stations": ["ecc", "conv", "sit", "azure", "s4"]},
    {"name": "Archive", "color": "#e1251b", "status": "out-of-service",
     "stations": ["ecc", "sara", "jivs"]}
  ],
  "zones": [
    {"name": "Preferred", "color": "#00a4a7", "stations": ["azure", "s4"]}
  ],
  "style": {"cell": 120, "stroke": 10, "corner": 22, "bundle_gap": 13, "label_size": 16}
}
```

`gx`/`gy` are grid cells, not pixels, and need not be whole numbers — `9.5`
sits half a cell along.
`status` is one of `live` (the default, and left out), `out-of-service`,
`under-construction` or `planned`. `label_angle` is `0`, `45` or `90` degrees
counter-clockwise, on top of `label_at`. Segments are octilinear (0° / 45° / 90°),
lines sharing a corridor spread into parallel tracks, and labels are placed on
the first side no track leaves the station on — `label_at` overrides that.
