# metro-map-tool

**v2.9.0** · MIT · [releases](https://github.com/ERP-LAB-5/metro-map-tool/releases)

Transit-map diagrams for landscapes, pipelines and migrations: a JSON spec of
**stations** on a grid, **lines** routed through them and **zones** banding
groups of them, rendered to a standalone, octilinear, light/dark-aware SVG.

![The designer showing how-this-tool-works: setup and startup on one trunk, the
browser and MCP ways of designing forking through the Design zone, meeting again
at the saved file](docs/how-this-tool-works.png)

That is the map the designer opens with, drawn by the tool itself. The retired
red stub is the terminal menu the browser UI replaced — dashed, faded, and
capped with a dead-end bar. The strip under the map is the **legend**, and the
small labels riding the tracks are **segment notes**.

## The web designer

```bash
git clone https://github.com/ERP-LAB-5/metro-map-tool.git && cd metro-map-tool
./run.sh                 # creates .venv on first run, then serves 127.0.0.1:8765
./run.sh --stop          # shut it down
run.cmd                  # Windows: same thing  (run.cmd -Stop to stop)
.\run.ps1                # Windows, if PowerShell scripts are allowed
```

No git? See [Installing without a clone](#installing-without-a-clone).

`run.cmd` is a plain batch file for machines where PowerShell execution is
blocked; `run.ps1` does the same job where it is allowed. All three replace an
instance already holding the port rather than failing on it, so re-running one
is how you restart. The red **Stop** button in the toolbar shuts the server down
from the browser.

It opens on **how-this-tool-works**, a map of the tool itself — setup, startup,
then the two ways of designing (the browser and the MCP tools) forking apart and
meeting again at the saved file. After that it reopens whatever you had last.

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
- **Legend** (Style tab) names every line on the drawing beside a swatch in its
  own colour and dash pattern, so a retired line reads as retired there too.
  Put it at the top, left, bottom or right, or `hide` it. It defaults to
  **bottom**, so a map drawn before v3 gains one the first time you render it.
- **Dead ends** (station editor) cap a stop that is the end of the road, at
  three volumes on the same terminus bar: *end of the line* stops there — a
  planned retirement; *smoke* adds drifting puffs — watch out; *burning
  platform* adds flames — get off this train. An out-of-service line still gets
  a plain bar automatically where it runs out.
- **Runs on past the map** (line editor) puts an arrowhead just beyond a line's
  first or last stop, saying it carries on beyond what the map shows — the
  opposite of a dead end, and useful at the two ends of a timeline where the
  work started before and continues after.
- **Joins** (Joins tab) draw a *stretched interchange*: one capsule covering
  several stops, the way a tube map marks a correspondance. Use it for a
  milestone that lands on several parallel lines at once — the capsule stretches
  across every lane it touches and replaces those stops' own markers. A label on
  the join replaces the labels of the stops it covers, so a milestone is named
  once rather than six times.
- **Phases** (Time tab) band the map with grey columns between two dates, named
  vertically — "Preparation", "Test", "Go to market". Roadmap only, since they
  are placed by date.
- **Dates top or bottom** (Time tab) puts the ruler under the map instead of
  over it.
- **Rides** (Rides tab) animate a traveller along a route from start to end.
  A ride is an ordered list of stops; it follows the drawn track and changes
  line wherever the journey does, so you can show "the migration path" across
  three branches as one moving dot. **A ride starts on its own** as soon as it
  has a route — the ⏸ and ↻ buttons in the Rides tab only pause and rewind it.
  The motion is written into the exported SVG, so a file you send someone
  animates in their browser — and honours their reduced-motion setting.
- **Notes between stops** (Lines tab, *Between stops*) put a short label on the
  track between two stations — "6 weeks", "nightly batch". They rotate to follow
  the track, never read upside down, and a vertical one reads top-to-bottom.
- **Insert space** (in the station editor) opens or closes a column and a row at
  a station: everything past it shifts by the grid x and y you give, in one undo
  step. Negative values close a gap. A checkbox moves the anchor station too, for
  when you mean "insert a column *here*" rather than "make room after this".
- **About** (top bar) shows the installed version, checks github.com for a newer
  one, and links to the repository. The check is a single request for a text
  file, sends nothing about you or your maps, is cached for six hours, and is
  skipped silently when there is no network — `--no-update-check` turns it off.
- **☰** (top bar, or Ctrl+\) folds the side panel away for a wider canvas, and
  the choice is remembered. Picking a station, line or zone opens its editor
  **inside the list, directly under the row you clicked**, so what you selected
  and the fields that change it stay together.
- **Theme** (top bar) switches the designer between *auto*, *light* and *dark*.
  It themes the workspace and its live preview only, and is remembered per
  browser — *auto*, the default, follows Windows or your desktop setting. An
  **exported SVG is never themed by it**: it keeps both palettes and adapts to
  whoever opens it, so the same file suits a light README and a dark slide.
- **Style** (cell size, route width, corner radius, track spacing, label size,
  zone padding) is saved with the map.
- Upgrading from before the folder split? Anything still in `maps/` is no longer
  read — the designer says so at startup. Move those files into `mymaps/`.
- Maps live in two folders — `mymaps/` for your own work, which git ignores, and
  `metro_map_tool/shared-maps/` for the maps that ship with the tool. Open groups them under
  *My maps* and *Shared*; **Save** writes back to the folder a map came from, so
  editing a shared map updates it instead of quietly forking a copy, and *Save
  as* lets you pick. Open, Save, Save as and Export SVG are in the top bar;
  Ctrl+Z / Ctrl+Shift+Z undo and redo, Ctrl+S saves.

Any stop shared by two or more lines is drawn as a white interchange ring while
the *interchanges* toggle is on.

## Installing without a clone

**A release tarball** — needs neither git nor pip. `curl` and `tar` both ship
with Windows 10 and later, and with every Linux and macOS:

```bash
curl -L https://github.com/ERP-LAB-5/metro-map-tool/archive/refs/tags/v2.9.0.tar.gz | tar xz
cd metro-map-tool-2.9.0
./run.sh                 # or run.cmd on Windows
```

**Or install it properly**, with no source tree to keep. This puts three
commands on your PATH in their own virtual environment:

```bash
pipx install git+https://github.com/ERP-LAB-5/metro-map-tool@v2.9.0
metro-map-designer                    # the browser designer
metro-map spec.json -o map.svg        # the renderer
metro-map-mcp                         # the MCP server, for agents
```

`pip install` works the same way if you would rather manage the environment
yourself. Installed like this, **`mymaps/` follows your working directory**, so
run `metro-map-designer` from wherever you keep your maps; the maps that ship
with the tool travel inside the package and are always available.

Upgrading is the same command with a later tag, or `pipx upgrade metro-map-tool`.

## Roadmap mode

Switch **mode** in the top bar from *Metro map* to *Roadmap* and the x axis
becomes a calendar. The **Timeline** tab sets a start date, an end date and what
one column is worth — a day, week, month, quarter or year — and the map is drawn
over a Gantt-style ruler: a light grey line on every period boundary, the period
name centred in the band between two lines, and the coarser period (the year, or
the month for weeks and days) named in a row above.

Grid x is still how you place a station, and it now means something: **column *k*
covers grid x from *k* to *k*+1**, so a whole number is the *start* of a period
and `2.5` sits mid-period. The station editor names the date under the grid
fields and offers a date picker that jumps the station to the column holding it.

```json
{"mode": "roadmap",
 "timeline": {"start": "2026-01-01", "end": "2027-07-01", "interval": "quarter"}}
```

A start date is snapped back to the period holding it, so 14 February with a
monthly interval starts the ruler on 1 February. The ruler spans the whole
declared range whether or not a station reaches the far end, and column names
thin out rather than overlap when a column is narrow. Everything else — lines,
zones, service states, interchanges — works exactly as in metro mode, and the
dates are kept if you switch back, so flipping modes costs nothing.

`metro_map_tool/shared-maps/roadmap-example.json` is a worked example, and
`project-tube-map.json` is the full treatment: six team lines, milestones drawn
as stretched interchanges across the lanes they touch, phase bands and a bottom
axis.

## The command line

The same specs render headlessly, so a map designed in the browser drops
straight into a build or a docs pipeline:

```bash
# installed (pipx / pip)
metro-map spec.json -o map.svg
metro-map spec.json --cell 140 -o big.svg
metro-map spec.json --legend hide -o plain.svg      # or top/left/right
cat spec.json | metro-map - > map.svg

# from a checkout, without installing
python3 -m metro_map_tool.metro_map spec.json -o map.svg
```

Flags override the `style` block saved in the spec. A spec that would not draw
is reported line by line and exits 2, so it fails loudly in CI.

Command-line renders always carry both palettes, so an SVG follows the reader's
own light or dark setting. The designer's theme switch changes only what you
see while drawing.

`metro_map_tool/metro_map.py` is standard library only; only the web designer
(`app.py`) needs Flask, and only the MCP server needs `mcp`.

## For agents

`metro_map_tool/mcp_server.py` exposes the designer over MCP (stdio), starting
it on demand.
`.mcp.json` registers it for this repo; point another agent at it with:

```json
{"mcpServers": {"metro-map": {
  "command": "/path/to/metro-map-tool/.venv/bin/python",
  "args": ["-m", "metro_map_tool.mcp_server"]}}}
```

Tools: `list_maps`, `read_map`, `save_map`, `delete_map`, `render_map`,
`validate_map`, `resolve_timeline`, `spec_reference`, `designer_url`,
`stop_designer`. The map tools take a `folder` of `mymaps` or `shared`; a save
with no folder updates the map where it already lives and puts a new one in
`mymaps`, so an agent cannot fork a shared map by accident. `resolve_timeline`
turns a roadmap's dates into the columns it will draw, so a milestone can be
placed on the right grid x without redoing the calendar arithmetic.

Agent and human work through one server and one maps directory, and the browser
watches the open map, so **an agent's save reaches the canvas within a couple of
seconds** with no reload. Unsaved edits of your own are never overwritten — you
get *Load theirs* / *Keep mine*. And a Save that would clobber a version you
never saw is refused by the server (HTTP 409), offering the same choice.

`.claude/skills/metro-map/SKILL.md` teaches an agent the spec, the design order
and the judgement calls. Link it into `~/.claude/skills/` to use it outside this
repo:

```bash
ln -s "$PWD/.claude/skills/metro-map" ~/.claude/skills/metro-map
```

## Spec shape

```json
{
  "mode": "metro",
  "stations": {
    "s4": {"label": "S/4HANA Private", "gx": 8, "gy": 3,
           "interchange": true, "label_at": "above-right"}
  },
  "lines": [
    {"name": "Option A · Azure", "color": "#0098d4",
     "stations": ["ecc", "conv", "sit", "azure", "s4"],
     "notes": [{"at": 0, "text": "6 weeks"}]},
    {"name": "Archive", "color": "#e1251b", "status": "out-of-service",
     "stations": ["ecc", "sara", "jivs"]}
  ],
  "legend": "bottom",
  "zones": [
    {"name": "Preferred", "color": "#00a4a7", "stations": ["azure", "s4"]}
  ],
  "style": {"cell": 120, "stroke": 10, "corner": 22, "bundle_gap": 13, "label_size": 16}
}
```

`mode` is `metro` (the default, and left out) or `roadmap`; a roadmap adds a
`timeline` of `{start, end, interval}` and its grid x becomes a calendar.
`dead_end` on a station is `buffer` (a terminus bar), `smoke` (watch out) or
`fire` (the burning platform); the stop has to be on a line, since the marker is
laid across the track arriving at it. `scenarios` are travellers'
routes — `{"name", "stations": [...], "color"?, "duration"?}` — animated along
the drawn track; every consecutive pair of stops needs a line between them.
`legend` is `bottom` (the default), `top`, `left`, `right` or `hide`. A line's
`notes` are `{"at": <hop index>, "text": str}` — hop `0` is the gap between the
first two stops, so the last legal `at` is two less than the number of stops.
`gx`/`gy` are grid cells, not pixels, and need not be whole numbers — `9.5`
sits half a cell along.
`continues` on a line is `start`, `end` or `both` and arrows that end onward.
`status` is one of `live` (the default, and left out), `out-of-service`,
`under-construction` or `planned`. `label_angle` is `0`, `45` or `90` degrees
counter-clockwise, on top of `label_at`. Segments are octilinear (0° / 45° / 90°),
lines sharing a corridor spread into parallel tracks, and labels are placed on
the first side no track leaves the station on — `label_at` overrides that.
