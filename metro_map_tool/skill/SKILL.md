---
name: metro-map
description: Draw transit-map style diagrams — stations on a grid, coloured lines routed through them, zones banding groups of stations, a legend naming the lines and short notes riding the track between two stops — and render them to a standalone light/dark SVG. Two modes: a metro map on an abstract grid, or a roadmap whose x axis is a dated Gantt-style calendar. Use for landscape, migration, architecture, pipeline, phase-plan or roadmap diagrams whenever the shape is "things connected by named paths": SAP landscapes (ECC → RISE → BTP), data flows, deployment topologies, delivery timelines. Covers the JSON spec, the design order (stations, then lines, then zones), roadmap timelines, line service states (out of service with dead ends, under construction, planned), label placement and rotation, the metro-map MCP tools, and the browser designer that ships with it. Triggers on "metro map", "transit map", "roadmap", "landscape diagram", "draw the landscape", "diagram the migration", "timeline diagram", "tube map", or an ask to edit an existing mymaps/*.json or shared-maps/*.json.
---

# Metro map

A diagram is one JSON spec: **stations** on a grid, **lines** routed through
them, **zones** banding groups of them. `metro_map_tool/metro_map.py` renders it to a
standalone SVG — octilinear (0° / 45° / 90°) segments, parallel tracks where
lines share a corridor, automatic label placement, light and dark grounds.

A rendered map carries **both** palettes and lets the reader's own setting pick,
so never ask for a themed file unless someone wants one pinned; the designer's
theme switch is a view setting for the person drawing, not part of the map.

Two modes. **metro** (the default) is an abstract grid where gx only means
"further right". **roadmap** gives gx a calendar and draws a Gantt-style ruler
over the diagram. Everything else is identical between them.

Two front ends over the same files: a browser designer (drag stations
on a live canvas) and the `metro-map` MCP tools. Both go through one server, so
a person and an agent can work on the same diagram — a `save_map` reaches their
canvas within a couple of seconds.

## Design order — do not skip it

1. **Stations first.** Nothing else can reference a station that does not exist.
   Place them on the grid — gx/gy in the spec are the layout, so read those back
   rather than the rendered SVG when checking a position.
2. **Then lines.** A line is an ordered route through station ids. Reusing an id
   in two lines makes that stop an interchange (drawn as a white ring).
3. **Then zones**, if the diagram groups things — "Upgrade", "Preferred".

## The spec

```json
{
  "mode": "roadmap",
  "timeline": {"start": "2026-01-01", "end": "2027-07-01", "interval": "quarter"},
  "legend": "bottom",
  "stations": {
    "s4": {"label": "S/4HANA Private", "gx": 8, "gy": 3,
           "interchange": true,
           "label_at": "above-right", "label_angle": 45}
  },
  "lines": [
    {"name": "Option A · Azure", "color": "#0098d4",
     "stations": ["ecc", "conv", "sit", "s4"],
     "notes": [{"at": 0, "text": "6 weeks"}]},
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
| `format` | The spec's shape, stamped by the server on save. **Never write or change it by hand.** A map claiming a format newer than the tool reads is refused; a map without one predates the stamp and is read as it always was. |
| `mode` | `metro` (default, leave it out) or `roadmap`. |
| `legend` | `bottom` (the default), `top`, `left`, `right` or `hide`. Names every line beside a swatch in its own colour and dash pattern. Leave it out unless the map wants it elsewhere or off. |
| `dead_end` | On a station, three volumes of the same terminus bar: `buffer` simply stops there (a planned retirement), `smoke` adds drifting puffs (watch out), `fire` adds flames (the burning platform — get off). The stop must be on a line, because the marker is laid across the track arriving at it. An out-of-service line still earns a plain bar automatically wherever it runs out. |
| `notes` | On a **line**, not a station: `[{"at": <hop>, "text": str, "flip": bool?}]`. `at` is the hop index — `0` is the gap between `stations[0]` and `stations[1]`, so the last legal value is `len(stations) - 2`. |
| `timeline` | Roadmap only: `{start, end, interval}` with ISO dates and an interval of `day`, `week`, `month`, `quarter` or `year`. |
| `gx` / `gy` | Grid cells, **not pixels**, and may be fractional — `0.5` steps put two stations half a cell apart. Keep whole numbers unless the layout needs the room. |
| `interchange` | White ring instead of a coloured tick. Leave it out and let `auto_interchange` (on by default) set it. |
| `label_at` | `above`, `below`, `left`, `right`, `above-left`, `above-right`, `below-left`, `below-right`. Omit it and the renderer picks the first side no track leaves on — usually right. Only override when a label collides. |
| `label_angle` | `0` (default), `45` or `90`, counter-clockwise. A tilted label is anchored at the marker edge and reads *outward*, so it never crosses its own station. Use it to fit long names into a tight column. |
| `status` | `live` (default, leave it out), `out-of-service`, `under-construction`, `planned`. |
| `continues` | On a line: `start`, `end` or `both` runs the line out to the edge of the map and caps it with an arrowhead. On a roadmap the edge is the start/end of the timeline, so a one-stop line with `both` draws right across the chart through that stop — which is how to show something that was already running and still is. Without a timeline the leftmost and rightmost stations stand in. The opposite of a `dead_end`, and drawn in the same place, so never put both on one stop. |
| `onward` | Beside `continues`, `{"start": str?, "end": str?}` — what is written past the arrow, the "to Cockfosters" of a transit map. Name where the line comes from or goes to: a date ("since 2019"), a destination, a phase. Only ends that actually run on may carry one. The `onward_reach` style, in cells, pushes the arrows further out from the ends when the labels need room. |
| zone `continues` / `onward` | A zone takes the same two fields a line does. A band that was already running before the map begins, or goes on after it ends, is drawn out to the edge and left **open** on that side rather than closed as if it stopped — with the `onward` text written just past the open edge. |
| `junctions` | A top-level `{"<id>": {"gx": num, "gy": num}}`. A junction is a bend in the track with **no platform**: a line routes through it, but nothing draws a marker or a label for it, and no zone can hold one. It is where a branch splits off or rejoins when there is no station at that spot. |
| `branches` | On a line: `[{"name": str?, "stations": [ids], "continues"?, "onward"?}]`. A branch is **the same line going two ways** — same colour, same service, one legend entry — not a second line of the same colour. Start it on a point the line's route already passes through and end it on one too, and the fork and the rejoin draw themselves. This is how to draw a Helsinki-style split. Notes stay on the trunk, addressed by its hop index. |
| `origin` | On a station, line, zone or interchange an importer made: `"<source>:<id>"` — `jira:ACME-231`, `github:issue/owner/repo#12`. **Leave it alone.** It is how a re-sync recognises what it made last time and keeps the position, colour and wording a human gave it. A spec that came from an importer also carries a top-level `source` recording which one, and with what options. Neither changes anything that is drawn, so neither raises the `format`. |
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

## Notes between stops

A note is a short label riding the track of one hop — "6 weeks", "nightly
batch", "after sign-off". It rotates to follow the track, is kept out of
upside-down territory automatically, and gets a paper halo so it stays readable
where it crosses something.

- **Address it by hop index, not by station id.** A route may visit the same
  station twice; an id would be ambiguous. `at: 0` is the first gap.
- **Editing a route strands notes.** Removing or reordering stops changes what
  each index means. The designer prunes them for you; when you edit a spec
  through MCP, re-check every `at` after you touch `stations`, or `save_map`
  refuses the spec.
- **Keep them to two or three words.** A note is a caption on a line, not a
  paragraph; long ones overrun the hop and collide with the stations at each end.
- `flip: true` moves a note to the other side of its track.

## Stretched interchanges

`interchanges` is a list of `{"stations": [ids], "label"?, "label_at"?,
"label_angle"?}`. Each draws **one capsule covering the stops listed**, replacing
their individual markers — the tube-map way of showing a correspondance.

In the designer, **+ Milestone…** on the Joins tab builds one in a single step:
it makes a stop per chosen line at the same x, routes each one, and joins them.

Reach for it when one event lands on several parallel lines at once: a milestone
across six team lanes is six stops (each line needs its own, or the line does not
stop there) joined into one capsule. Give the *join* the label and the member
stops' labels are not drawn, so the milestone is named once instead of six times.
Name the member stops something like "Kick-off · Alpha" anyway — the station list
in the designer still shows them.

## Phases and the axis — roadmap only

`phases` is `[{"name", "from", "to", "color"?}]`, grey columns behind everything
between two dates with the name set vertically. `timeline.axis` is `"top"`
(default) or `"bottom"`. Together with a legend and 45° labels these give the
"project tube map" look; `shared-maps/project-tube-map.json` is that map.

## Drawing a git history

A commit graph is already a metro map; it needs no mode and no new fields, only
the mapping:

| git | map |
|---|---|
| lane | `gy` — main on 0, each concurrent branch on its own row |
| commit | a station, `gx` = its position in the order |
| branch | a line through the commits on it |
| merge | **a stop two lines share** — it becomes an interchange ring by itself |
| tag | `interchange: true` and the tag as the label |
| history before the view | `continues: "start"` on the line |

A branch line runs from the commit it forked at, through its own commits, to the
merge commit — so it starts and ends on stops that main also has, and the fork
and the merge draw themselves.

- **Read the history, do not invent it.** `git log --reverse --pretty=%h|%s|%D`
  gives the order, subjects and refs in one go; `%P` gives parents when you need
  to work out lanes.
- **A linear history draws as a straight line, and that is the honest answer.**
  Say so rather than inventing branches to make it look busier.
- **Tags earn a label; ordinary commits mostly do not.** Twenty short SHAs in a
  row is noise. Label the releases, and use `label_angle: 45` when they crowd.
- **Keep it to a couple of dozen commits.** Beyond that the map is longer than
  it is useful; pick a range worth explaining.

## Rides — an animated traveller

A `scenarios` entry is a traveller's route: `{"name", "stations": [...],
"color"?, "duration"?}`. It is drawn as a dot riding the **existing** track from
the first stop to the last, changing line wherever the journey does, and the
animation is written into the SVG so an exported file moves too.

- **Every consecutive pair of stops must be joined by a line**, in either
  direction. A pair with no track between them is warned about and the whole
  ride is skipped — a traveller cannot walk across open ground.
- It is an ordered list of *stops*, not of lines. Name the stations the traveller
  passes through and the renderer works out which track carries each hop.
- **There is nothing to start.** A ride animates as soon as it has a valid
  route; the designer's buttons only pause and rewind it.
- `duration` is seconds end to end, 8 by default. Longer for a route with many
  stops, or the dot moves too fast to follow.
- One or two rides on a map. Three dots moving at once is a screensaver, not an
  explanation.

## Roadmap mode

Set `"mode": "roadmap"` and a `timeline`, and the x axis becomes a calendar:
**column *k* covers gx from *k* to *k*+1**, so a whole gx is the *start* of a
period and `2.5` sits mid-period. A light grey line falls on every boundary, the
period name sits centred in the band between two lines, and the coarser period
(the year, or the month for weeks and days) is named in a row above.

- A start date is **snapped back** to the period holding it: 14 Feb with a
  monthly interval starts the ruler on 1 Feb. The ruler spans the whole declared
  range whether or not a station reaches the far end.
- Call `resolve_timeline` before placing anything. It returns the snapped start,
  the column count, and the gx, date and name of every column — place a
  milestone by looking up its date there rather than counting periods yourself.
- A station whose gx falls outside `0..columns` is warned about: it draws beside
  the ruler rather than on it.
- Keep gy for what the metro mode uses it for — parallel workstreams, one row
  each. Time is the x axis and nothing else.
- Switching a map to `metro` keeps the `timeline` block but stops drawing it,
  so a mode flip is free.

## Importing a plan

A map is worth drawing once; keeping it current is what makes it worth keeping.
`list_sources` says what can be imported from and what each one needs;
`import_map(source, options)` builds a spec and hands it back **without saving**,
so read it, adjust it, then `save_map` it.

| import idea | how to draw it |
|---|---|
| a Jira epic, or a GitHub `area:` label | a line |
| an issue | a station at its due date |
| a fix version or milestone | an `interchanges` capsule across the lanes it lands on |
| a sprint | a `phases` band |

Pass `into` the name of a map that source imported before and it **re-syncs**:
what changed upstream comes in, and the layout, colours and wording already in
the map survive. Prefer that to importing again — a fresh import throws away
everything anyone arranged.

**Browse before you import.** `browse_source` walks one level at a time —
Jira starts at the project and goes down either the hierarchy (epic set, epic,
issue) or the boards (board, sprint, issue). Pass the ids you want as
`select`, rather than importing a whole project and discarding most of it.

Three rules worth carrying: something that vanished upstream is kept and
reported rather than deleted (`prune` is how you actually remove it);
credentials belong to the machine running the designer, so **never ask a human
to paste a token to you** — if a source is unconfigured, say so and point them
at Settings; and Jira is read-only, so a request to "update the ticket" is a
thing to decline and explain, not to attempt.

## Working through MCP

The `metro-map` server (`.mcp.json` in the repo) starts the designer on demand.

| Tool | Use it for |
|---|---|
| `list_maps` | what already exists, with each map's folder and mode |
| `read_map(name, folder="")` | the whole spec, to edit; empty folder searches mymaps then shared |
| `save_map(name, spec, folder="")` | write it back — validates first, refuses a spec that will not draw; an empty folder updates the map where it already lives |
| `validate_map(spec)` | check before saving; returns `errors` (fatal) and `warnings` (a line with one stop, an empty zone, a station on no line) |
| `render_map(name, out_path=…)` | write the SVG to a file; pass `out_path` rather than pulling markup through the transcript |
| `resolve_timeline(timeline)` | a roadmap's columns: snapped start, count, and the gx, date and name of each — use it to place milestones on dates |
| `spec_reference` | palette, label sides and angles, line states, modes, intervals, legend positions, style defaults |
| `designer_url` | hand the human a link to take over in the browser |
| `list_sources` | the importers available, their options, and which credentials each needs |
| `browse_source(source, path="", view="", query="")` | look before importing — one level at a time; `query` narrows it (`SAP*`); node ids feed `import_map`'s `select` |
| `import_map(source, options, into="")` | build a spec from git, GitHub or Jira; `into` re-syncs a map instead of starting over |
| `stop_designer` | shut the local server down |

Read → modify → `validate_map` → `save_map`. Never hand-write a spec and save it
unvalidated: an unknown station id in a route is the usual mistake, and
validation names it.

**Two folders.** `mymaps` is the user's own work and git-ignored; `shared` is
what the repo ships. `list_maps` says which folder each map is in. Saving with
no folder updates the map where it already lives, and creates a new one in
`mymaps` — which is what you want almost every time. Name a folder only to move
a map, or to put a new one in `shared` because it belongs to the repo.

**Read immediately before you write.** Someone may have the same map open in the
browser; `read_map` → edit → `save_map` keeps that window short. Saving a spec
you read minutes ago silently drops whatever they did in between.

## Without MCP

```bash
cd metro-map-tool          # or wherever you cloned it
./run.sh                    # designer on 127.0.0.1:8765 (restarts if already up)
./run.sh --stop             # or the red Stop button in the toolbar
run.cmd                     # Windows;  run.cmd -Stop   (no PowerShell needed)
.\run.ps1                   # Windows, where PowerShell scripts are allowed

# the renderer: installed as `metro-map`, or from a checkout as a module
metro-map spec.json -o map.svg
metro-map spec.json --cell 140 -o big.svg        # flags beat spec.style
metro-map spec.json --legend hide -o plain.svg
python3 -m metro_map_tool.metro_map spec.json -o map.svg
```

Installed without a clone with
`pipx install git+https://github.com/ERP-LAB-5/metro-map-tool`, the same three
commands exist as `metro-map`, `metro-map-designer` and `metro-map-mcp`. The
shipped maps live inside the package; `mymaps/` follows the working directory
the designer was started from.

A spec that will not draw is reported line by line and exits 2.

## Judgement

- **Grid, not pixels.** Two cells between stops on a busy corridor, one where
  things are tight. Reach for fractional steps only when whole cells will not
  fit — they cost legibility.
- **Let labels place themselves.** Set `label_at` only after seeing a collision,
  and `label_angle` only when a column is genuinely too narrow.
- **A branch that is being retired is its own line**, not a status on a shared
  one — that is what makes the dead-end bar land in the right place.
- **Save the burning platform for the branch that genuinely ends badly.** It is
  the loudest mark on the map; two of them on one diagram and neither reads as
  urgent. `buffer` is the quiet way to say "this stops here".
- **Making room is arithmetic, not a redraw.** To open a column, add the shift
  to the gx of every station past that point (and gy for a row) — do not move
  stations one at a time, and add the raw offset rather than re-snapping, or a
  station deliberately placed on a half cell jumps to the next whole one. The
  designer has an *Insert space* button that does exactly this in one undo step.
- **Name the lines well — they are now on the drawing.** The legend prints
  `name` verbatim, so "Option A · Azure" reads better there than "Line 1".
- **Check the render, do not assume it.** Reading gx/gy back catches a wrong
  grid position; for anything subtler, render to a file and look at it.
