# ADR 0015: Metrofy makes a metro map from code, for display, and refuses rather than draws half

## Status

Accepted

## Date

2026-11-11

## Context

The designer is for drawing a map by hand: a person places stations, routes lines, nudges labels.
Some maps are not drawn but *derived*. One example is a migration's process and the stories
moving through it, which change daily and live in another system. Redrawing them by hand
creates a second definition that drifts, and hand-placing a map an agent wrote is work nobody
should do.

Archify shows the shape that works for derived diagrams. The input names things and says how
they relate, never where they go. The tool places everything, renders, and checks what it made.
It fails loudly instead of shipping a half-drawn picture, and leaves a receipt saying which
checks passed.

A metro map has one feature a box-and-arrow diagram lacks: a traveller riding a route
(`scenarios`, ADR 0011). That is exactly how to show a what-if: two ways of doing the same
piece of work are two trips on one map.

## Decision

- **A new entry point, `metrofy`** (`metro_map_tool/metrofy/`, standard library only), beside
  the renderer, not inside it. It drives `metro_map.render`; the renderer and existing maps
  are unchanged, byte for byte.
- **The input names, never positions** (`metrofy/schema.json`, format 1):
  - `zones` are lanes, drawn as swimlanes;
  - `steps` are stations in process order, each in one zone; a parent is drawn as an
    interchange;
  - `lines` are work riding the process, with a status (`planned`, `building`, `live`,
    `dropped`, which are the renderer's planned, under-construction, live and
    out-of-service);
  - `trips` are scenarios played out;
  - `details` on any of them are shown when it is chosen.
- **Metrofy decides the layout.**
  - Steps keep their listed order, two columns apart, on the middle row of their zone's lane.
  - A line visits its steps in process order whatever order they were written in, so work
    never runs backwards.
  - A grey "The process" line runs through every step, unless `spine` is false, so a trip
    can always ride the process itself.
  - Colours come from the palette when none is given.
- **`finalize` is four gates, stopping at the first that fails:**
  1. `input`: the schema's rules, plus the cross-references a schema cannot say.
  2. `spec`: `validate_spec` on what was placed.
  3. `trips and lanes`: any `spec_warnings` that means something would not be drawn, which
     is fatal here.
  4. `render`: every step reached the SVG, and so did the trips.

  The receipt has every gate's result, the counts, and digests of the input and the page.
  The CLI exits 1 on failure and writes no page.
- **The page is one self-contained file:**
  - the map, light and dark;
  - the trips listed, one played at a time;
  - a details panel for a clicked station, line or trip. A station wins over a route drawn
    across it.

  No network. Input text is escaped, and the data block cannot close its script.
- **Display only.** The page changes nothing. To change the map, change the input.
- **A skill** (`plugin/skills/metrofy/`) tells an agent to write the input and run `finalize`,
  never to hand-place a map.

## Consequences

### Positive

- A map derived from a live system (the first user is OntoVibeShift's story-by-story
  migration) stays a view of that system, regenerated at will, with no layout to maintain.
- Scenarios are visible as what they are: alternative journeys over the same ground.
- The renderer gains a second user without a single change.

### Negative

- The layout is deliberately simple: one row per zone, one column pair per step. A process
  with many steps makes a wide map; a hand-tuned map will always look better.
- `roadmap` mode, reading the input from ArchiMate (ADR 0013), and an MCP tool are not in
  format 1. They are on the parking lot.

## Carried out in

- the commit adding `metro_map_tool/metrofy/`, `tests/test_metrofy.py` and this ADR
