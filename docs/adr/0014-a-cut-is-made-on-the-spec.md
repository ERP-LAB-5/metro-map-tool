# ADR 0014: Exporting some swimlanes and phases cuts the spec, not the picture

## Status

Accepted

## Date

2026-09-15

## Context

Maps with swimlanes and phases get busy, and they are often shared one key area or one period
at a time. There are two ways to export only part of a map:

- **Clip the rendered SVG** to the chosen bands. Labels, arrows and the legend would be cut
  mid-way. The unchosen lanes would stay as empty space, and the ruler would still span the
  whole plan.
- **Cut a copy of the spec** and render that.

## Decision

- A cut is made on a copy of the spec, in `metro_map_tool/cut.py`, before rendering.
  - Stations and junctions stay only when their row is in a chosen swimlane and their column in a
    chosen phase.
  - Swimlanes that were not chosen close up.
  - The timeline starts at the first chosen phase and ends with the last, and columns shift
    with it.
  - Lines, branches, zones, interchanges, notes and rides keep what is left.
  - A line that lost stops beyond one end is marked as continuing there.
  - A ride whose start, end or via is gone is left out.
- No choice means all. Choosing everything draws exactly the uncut map.
- One function serves the designer's Export dialog (`/api/render` with `cut`), the command
  line (`--lane`, `--phase`) and the MCP `render_map(swimlanes, phases)`.
- The map being edited, and the file on disk, are never changed by a cut.

## Consequences

### Positive

- A cut is an ordinary map: labels are placed, the legend lists the lines still drawn, and the
  format stamp and version are there as for any other file.
- There is one definition of a cut, tested once.

### Negative

- Labels are placed afresh for the cut, so a part can look slightly different from the same
  area of the whole map.
- A cut cannot be saved as a map of its own yet (an open question in the 3.6.0 spec).

## Carried out in

- [`cc37eb0`](https://github.com/ERP-LAB-5/metro-map-tool/commit/cc37eb0), released in 3.6.0
