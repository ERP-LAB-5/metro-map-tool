# metro-map-tool

**v3.5.2** · GPL-3.0 · [releases](https://github.com/ERP-LAB-5/metro-map-tool/releases)

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
- **Runs on past the map** (line editor) draws a line out to the edge and caps
  it with an arrowhead, saying it carries on beyond what the map shows — the
  opposite of a dead end. On a roadmap the edge is the start or end of the
  timeline, so a system that was already running and has one milestone on it
  draws as a full-width line through that stop. Without a timeline the leftmost
  and rightmost stations stand in for the edges.
- **+ Milestone…** (Joins tab) does the tedious half in one action: pick a
  label, an x (or a date), and which lines it lands on, and it drops one stop on
  each of those lines at that x, routes them in the right order, and joins them
  into a single capsule. The label goes on the join, so it is written once
  rather than once per lane.
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
- **Swimlanes** (Lanes tab) band the map into named rows, one per key area,
  with the name in a gutter on the left. A station sits in the lane its row
  falls in; click a station to stretch a lane over it, or fit a lane to a line.
- **Rides** (Rides tab) animate a traveller from a **start** to an **end** — a
  station, a junction, or where a line runs **past the map**. The track decides
  the way, changing line where it has to; add a **via** to send it down a
  particular branch, and mark stops to **jump** so it rides straight through.
  It waits at every other stop. The 👁 on a ride hides it (and leaves it out of
  the export); the 👁 in the tab's header hides every traveller on the canvas.
  The motion, pauses included, is written into the exported SVG, so a file you
  send someone animates in their browser — and honours their reduced-motion
  setting.
- **▶ Navigate** follows one ride like a satnav: the camera stays with the
  traveller, a board shows the next stop, the date on a roadmap and where to
  change, and a strip shows the whole journey. The side panel folds away while
  you ride and comes back when you stop. Space pauses, ← → go to the previous
  and next stop, + − (or the mouse wheel, or the zoom slider) zoom without losing
  the traveller, M shrinks the board to one line and back, Esc stops; drag the
  map to look around and **Re-centre** to follow again. Spoken announcements
  are one button away. Only one ride is followed at a time; the others keep
  moving.
- **Export SVG** on a map with swimlanes or phases asks which of them go in the
  file — all, by default. Untick some and the file holds only the rest: stations
  outside are left out, the other lanes close up, the ruler covers just the
  chosen phases, and a line cut short runs on past the edge. The map you are
  editing is not changed.
- **Search** sits above every long list — stations, lines, zones, rides and the
  pick lists in their editors — and filters as you type.
- **Notes between stops** (Lines tab, *Between stops*) put a short label on the
  track between two stations — "6 weeks", "nightly batch". They rotate to follow
  the track, never read upside down, and a vertical one reads top-to-bottom.
- **Insert space** (in the station editor) opens or closes a column and a row at
  a station: everything past it shifts by the grid x and y you give, in one undo
  step. Negative values close a gap. A checkbox moves the anchor station too, for
  when you mean "insert a column *here*" rather than "make room after this".
- **About** (top bar) shows the installed version, checks github.com for a newer
  one, and links to the repository. On a pip or pipx install it can **update
  itself**: it runs pip against the latest tag, shows you what pip said, and
  restarts into the new version only if that succeeded. From a source checkout
  it tells you to `git pull` instead, because pip cannot update what it did not
  install. The check is a single request for a text
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
- **Every SVG says what drew it**: the root element carries
  `data-generator="metro-map <version>"` and `data-format`, the oldest spec format
  that can hold the map, and a comment at the top says the same for a person
  opening the file. Older maps need no converting — every field added since the
  first release is optional, and a map is only saved as a newer format once it
  uses something that format brought (format 3: swimlanes and start/end rides).
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

## Roadmap and decisions

- [Roadmap](docs/roadmap/README.md): this tool, python-tool-template and
  sap-di-tools on one map, drawn by this tool.
- [Decisions](docs/adr/README.md): why it is built the way it is, with the
  commits that did it.
- [Next station](docs/specs/PARKING-LOT.md): what is parked, and how a release
  gets scoped ([specs](docs/specs/README.md)).

## Licence and credits

GPL-3.0-or-later. © 2026 D-LAB-5 — *Twin. Experiment. Automate.*

You may use, study, change and share it; if you distribute a changed version,
it has to carry the same freedoms and ship its source. See [LICENSE](LICENSE).

**Releases up to and including v2.12.0 were published under the MIT licence and
remain available on those terms** — a licence already given cannot be withdrawn.
The change applies from v2.13.0 onward.

If it saves you an afternoon, [buy me a coffee](https://www.buymeacoffee.com/dlab5).

## Installing without a clone

**A release tarball** — needs neither git nor pip. `curl` and `tar` both ship
with Windows 10 and later, and with every Linux and macOS:

```bash
curl -L https://github.com/ERP-LAB-5/metro-map-tool/archive/refs/tags/v3.5.2.tar.gz | tar xz
cd metro-map-tool-3.5.2
./run.sh                 # or run.cmd on Windows
```

**Or install it properly**, with no source tree to keep. This puts three
commands on your PATH in their own virtual environment:

```bash
pipx install git+https://github.com/ERP-LAB-5/metro-map-tool@v3.5.2
metro-map-designer                    # the browser designer
metro-map spec.json -o map.svg        # the renderer
metro-map-mcp                         # the MCP server, for agents
metro-map-skill --install             # the agent skill, into ~/.claude/skills
```

`pip install` works the same way if you would rather manage the environment
yourself. Installed like this, **`mymaps/` follows your working directory**, so
run `metro-map-designer` from wherever you keep your maps; the maps that ship
with the tool travel inside the package and are always available.

### If the commands are not on your PATH

`pip install --user` puts them somewhere your shell may not look. Every command
has a `python -m` twin that needs no PATH at all, and runs exactly the same
code — the named commands are only generated wrappers:

| command | the same thing as a module |
|---|---|
| `metro-map-designer` | `python3 -m metro_map_tool.app` |
| `metro-map` | `python3 -m metro_map_tool.metro_map` |
| `metro-map-mcp` | `python3 -m metro_map_tool.mcp_server` |
| `metro-map-skill` | `python3 -m metro_map_tool.core.skill_install` |

Flags are identical either way — `python3 -m metro_map_tool.app --port 9000`.

From a clone, use the virtual environment's own interpreter, since that is
where Flask lives:

```bash
cd metro-map-tool && .venv/bin/python -m metro_map_tool.app
```

There is a **[cheat sheet](docs/cheatsheet.md)** with the commands, the spec
fields and the keyboard shortcuts on one page.

## Updating an install

**From the designer:** *About → Update and restart*. It runs pip against the
latest published tag, shows you what pip said whatever the outcome, and restarts
into the new version only if the install actually succeeded.

**From the command line:**

```bash
# name the new tag — the dependable form
pip install --upgrade git+https://github.com/ERP-LAB-5/metro-map-tool@v3.5.2

# or drop the tag to track the default branch and always get the newest
pip install --upgrade git+https://github.com/ERP-LAB-5/metro-map-tool
```

`--upgrade` on its own will not move you if you pinned a tag: `@v3.5.2` means
that commit for good, so re-running it reinstalls the same version. Change the
tag, or leave it off.

With pipx, `pipx upgrade` re-runs whatever spec you first installed and has the
same pinned-tag problem, so name the tag and force it:

```bash
pipx install --force git+https://github.com/ERP-LAB-5/metro-map-tool@v3.5.2
```

**After any update**, if you use the agent skill, refresh your copy — it is a
copy under `~/.claude/skills`, so upgrading the package does not touch it:

```bash
metro-map --version                    # what you have now
metro-map-skill --install --force      # refresh the skill
```

## Drawing a git history

A commit graph is already a metro map: lanes are grid y, commits are stations, a
branch is a line through the commits on it, and a merge is a stop two lines
share — so it becomes an interchange ring on its own. No mode and no extra
fields; [the skill](metro_map_tool/skill/SKILL.md) has the mapping and the
judgement calls, so an agent carrying it can draw one on request.

`--from-git` does it without an agent — from a path, or a URL it clones:

```bash
metro-map --from-git .                              -o history.svg
metro-map --from-git https://github.com/o/r         -o history.svg
metro-map --from-git . --branches main,stage,beta   -o history.svg
metro-map --from-git . --subjects --commits 40      -o history.svg
```

A branch is a lane, a commit is a station, and a merge is a stop two lanes share
— so forks and rejoins draw themselves. Commits are placed in **topological**
order, not by date: dates come from whoever's machine made the commit, run
backwards across a rebase and tie when a script commits twice in a second, while
topological order is the one thing that guarantees a commit sits to the right of
what it was built on.

### The model comes first

A branch model can be drawn before there is a repository to look at.
[`branch-model.json`](metro_map_tool/shared-maps/branch-model.json) is one:
prod, preprod, beta, alpha and bug-fix as lanes, with the shape work takes
through them. Each line says which git branch it stands for:

```json
{"name": "preprod", "branch": "stage", "color": "#9b0058", "stations": [...]}
```

Point `--model` at it and the real commits fill that shape in, keeping the lane
order, names, colours, legend and style you chose:

```bash
metro-map --from-git . --model branch-model.json --write-spec synced.json -o synced.svg
```

The map is the intent; the repository is only the evidence. Notes are dropped on
a sync — they are addressed by hop index, and after the commits change those
indices point somewhere else entirely.

## Importing a plan

A map is worth drawing once. Keeping it worth looking at means re-drawing it
every time the plan moves, which nobody does by hand — so the plan comes from
where it already lives.

```bash
metro-map --sources                       # what can be imported from
metro-map --from jira --describe          # and what that one needs

metro-map --from github    --opt repo=owner/name        -o plan.svg
metro-map --from jira      --opt project=ABCD           -o plan.svg
metro-map --from archimate --opt file=roadmap.ttl       -o plan.svg
```

Or **Import…** in the designer, which asks the same questions as a form.

| planning idea | how it is drawn |
|---|---|
| a Jira epic, or a GitHub `area:` label | a **line** |
| an issue | a **station**, placed at its due date |
| a fix version, or a milestone | a **capsule** across every lane it lands on |
| a sprint | a **band** behind the map |
| an epic where nothing has started | the line drawn as **planned** |

### From an ArchiMate model

`--from archimate` reads an implementation-and-migration model in Turtle, so a
roadmap that is already a model does not have to be retyped as a drawing:

| ArchiMate | how it is drawn |
|---|---|
| a `WorkPackage` | a **line**; one nested inside another rides its parent's line |
| a `Deliverable` | a **station** — it has no date of its own, so it takes the date of the work that produces it |
| an `ImplementationEvent` | a **station** at its own date |
| a `Plateau` | a **capsule** across the lanes, at the date the work realising it finishes |
| a `Gap` | said in the notes, never silently dropped |
| the `status` property | `done` draws the line **live**, `planned` draws it **planned** |

**A plateau carries no date**, and this importer does not invent one: a state is
reached when the work bringing it about finishes, so the date is computed. That
is the rule the model itself is built on, and copying it here is what stops the
two disagreeing.

Turtle is read with `rdflib` when it is installed, and otherwise by a small
built-in reader that handles what model generators emit. Nothing else in the
tool needs RDF, so it stays an optional extra:

```bash
pip install rdflib        # optional; only --from archimate benefits
```

### Connecting

**Settings** in the designer is the shortest way: pick the importer, fill in the
boxes, press **Test connection**. It saves to a file only you can read
(`~/.config/metro-map/jira.conf`, mode `0600`), and a secret is never sent back
to the page once saved.

Environment variables still work and still win, so CI and one-off overrides are
unchanged — `GITHUB_TOKEN`, or `JIRA_BASE_URL` / `JIRA_EMAIL` /
`JIRA_API_TOKEN`. Already keeping those in a config file for your own scripts? Point
`METRO_MAP_JIRA_CONFIG` at it and leave it where it is — no copying a token
from one file to another. It reads the usual `[jira]` ini section and
understands the common spellings, so `url`/`base_url`, `token`/`api_token` and
`user`/`email` all work.

A credential is never an option, so it cannot reach a map, an API response or
an error message. `metro-map --from NAME --describe` says what a source wants
and whether it is set.

### Starting from issue keys

A roadmap is usually a few initiatives and what hangs off them, not a whole
project, so the Jira import starts from keys — **Import… → Start from issue
keys…** — in three steps:

1. **Keys.** Type one, **+ Add key** for more. Each key becomes a line of its
   own. A key that does not exist (or your account cannot see), a duplicate, or
   a key already beneath another one in the list is said right under its box.
2. **Filter** by **issue type**, **All or Only open**, and **labels** — typed
   freely, comma separated, with the labels found beneath your keys offered to
   click. The trees are loaded once and filtered where they are, so nothing is
   re-fetched while you narrow. Filtering keeps the structure: pick only Bugs
   and whatever they hang from stays, as what they sit in.
3. **Map.** Say what each level beneath a key becomes, and override single
   issues where their level is not right for them:

   | role | on the map |
   |---|---|
   | Station | a stop at its date |
   | Junction | a branch of the line, carrying what is beneath it — it turns off before its first stop and rejoins after its last, and branches can branch |
   | Zone | a band around the stops beneath it |
   | Track note | its name on the track beside the stops beneath it (on a line's main route) |
   | Hide | not drawn; what is beneath it still comes |
   | Don't import | neither it nor anything beneath it |

   An issue with no due date and no quarter label gets a date box: a date typed
   there places it until Jira has a date of its own, and then Jira's wins. The
   count at the bottom says what you will get — stations, branches, zones and
   notes on how many lines, and what will be left out for want of a date.

The wizard only fills in ordinary options, so the same import works on the
command line and through MCP, and a re-sync repeats the same mapping:

```bash
metro-map --from jira --opt roots=ABCD-123,ABCD-456 --opt open_only=true \
          --opt levels=junction,station --opt roles=ABCD-130=zone \
          --opt dates=ABCD-140=2026-10-01
```

`project=` still takes a whole project the old way — epics as lines — and
`jql=` any search you like.

And you no longer have to know your instance's field numbers: the plugin asks
Jira which fields are called Sprint, Start date and Epic Link, and remembers
the answer. `--opt sprint_field=` still overrides it.

An issue with no due date but a quarter label like `FY25Q1` is placed in that
quarter rather than left out — `--opt quarter_labels=false` turns that off.

**Jira is read-only.** The client refuses anything but `GET` and says so.
Changing tickets from a map is a separate feature with its own confirmation,
and it is not built yet.

### Re-syncing keeps what you did

This is the point of importing rather than exporting. Every stop an import
creates remembers where it came from, so the next sync recognises it:

```bash
metro-map --from jira --opt project=ABCD --model plan.json --write-spec plan.json
```

**The import owns what exists upstream; you own where it sits, what colour it
is and what the map calls it.** Move a stop, recolour a line, rename something
into words your team actually uses — a re-sync brings in what changed and
leaves all of that alone.

What a Jira import makes remembers what Jira said for it — the label, the date,
the position — so a re-sync can tell the two kinds of change apart:

| since the last sync… | the re-sync |
|---|---|
| Jira changed it, the map did not | takes Jira's — a new due date moves the stop, a new summary renames it |
| the map changed it, Jira did not | keeps the map's, without a word |
| both changed it | keeps the map's, and says what Jira has now |

So a stop you never touched follows its date, and one you dragged stays where
you put it. The same happens from the designer (the **Re-sync into** checkbox,
which also fills the form from how the map was imported) and from MCP
`import_map(into=…)`. A map imported before this existed keeps its values on
the first re-sync and is updated like this from then on.

Something that vanishes upstream is **kept**, with a note, because a sync that
deletes is triggered by innocuous things — a narrower date window, a rename, a
permissions change — and what it deletes is an afternoon of somebody's
arranging. `--opt prune=true` is how you actually remove it.

`--opt refresh=label,gx` asks for the upstream value on fields you would rather
not maintain by hand.

### Working offline, and adding your own source

```bash
metro-map --from jira --opt project=ABCD --opt to_file=payload.json   # record
metro-map --from jira --opt project=ABCD --opt from_file=payload.json # replay
```

Fetching and drawing are separate, so a recorded payload replays identically
with no network and no credentials — useful on a train, and it is how the
importers are tested.

Somebody else's importer joins the tool by declaring the entry-point group
`metro_map_tool.sources` in their own `pyproject.toml`, pointing at a `Source`.
It then appears in `--sources`, in the designer's Import dialog and to an agent,
with no change here. One that fails to load costs you that importer and nothing
else.

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
metro-map plan.json --lane Delivery --phase "Wave 1" -o part.svg   # some lanes/phases only

# from a checkout, without installing
python3 -m metro_map_tool.metro_map spec.json -o map.svg
```

Flags override the `style` block saved in the spec. `--lane` and `--phase`
(each repeatable) cut the drawing down to those swimlanes and phases, the way
the designer's export does. A spec that would not draw
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

**While an agent works on a map, the map is locked.** `read_map` takes the lock
and the agent's `save_map` gives it back; it also lapses by itself after two
minutes without a save. Meanwhile the designer folds the panel away (and brings
it back afterwards if it was open), the panel button shows **agent lock**, and
editing and Save are paused while the agent's changes keep arriving on the
canvas. **Take over** ends the lock at once; the agent's next save is then
refused with a message telling it so, and nothing either side did is lost.

The agent's saves are checked the same way yours are: `save_map` only replaces
the version the agent last read, and refuses otherwise with a sentence the
agent can act on — re-read the map and re-apply its change.

**About** lists what the tool is made of, with a dot each — green up, red down,
grey not part of this install: the web server, the MCP server (it reports in
every 20 seconds while an agent has it open), the update check, the agent
skill, the maps folders, and the Jira and GitHub importers once configured.

[**The skill**](metro_map_tool/skill/SKILL.md) teaches an agent the spec, the
design order and the judgement calls. **It ships inside the package**, so an
install already has it — one command puts it where Claude Code looks:

```bash
metro-map-skill --install        # copies it to ~/.claude/skills/metro-map
metro-map-skill                  # just print where the packaged copy lives
metro-map-skill --print          # write it to stdout
```

Working from a checkout instead? A second, identical copy sits at
[`.claude/skills/metro-map/SKILL.md`](.claude/skills/metro-map/SKILL.md), so an
agent working in this repository picks it up with no setup. It is a real file
rather than a symlink because a symlink does not survive a clone on Windows — it
arrives as a text file holding the target path, and the skill quietly becomes one
line of nonsense. To check the pair has not drifted:

```bash
metro-map-skill --check .claude/skills/metro-map/SKILL.md
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

`format` is the shape of the spec, stamped on save. Leave it alone: a map
claiming a format newer than your copy understands is refused with a message
rather than half-drawn, and one without it simply predates the stamp and is read
as it always was.
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
`junctions` is a top-level `{"<id>": {"gx", "gy"}}` — a bend in the track with
no platform, drawn as nothing, that a line can be routed through. A line's
`branches` are `[{"name"?, "stations": [ids]}]`: the same line going two ways,
in one colour under one legend entry. Start a branch on a point the route
already passes through and end it on one too, and the fork and the rejoin draw
themselves — which is how to get a Helsinki-style split. A zone takes the same
`continues` and `onward` a line does, and is left open on the side it runs off.
`continues` on a line is `start`, `end` or `both` and arrows that end onward;
`onward` is `{"start": str?, "end": str?}` — the "to Cockfosters" written past
that arrow, and the `onward_reach` style (in cells) runs the ends further out.
`status` is one of `live` (the default, and left out), `out-of-service`,
`under-construction` or `planned`. `label_angle` is `0`, `45` or `90` degrees
counter-clockwise, on top of `label_at`. Segments are octilinear (0° / 45° / 90°),
lines sharing a corridor spread into parallel tracks, and labels are placed on
the first side no track leaves the station on — `label_at` overrides that.
