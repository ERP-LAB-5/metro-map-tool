# ADR 0011: Swimlanes are row bands, and rides are routed by the track from a start to an end

## Status

Accepted

## Date

2026-09-15 (documented retroactively 2026-09-15)

## Context

Busy maps had nothing separating key areas, and a ride had to be typed stop by stop along
direct pairs of a line, could not arrive from or leave past the map, and could not be hidden.

## Decision

- **Swimlanes** are named bands of rows across the whole map (`swimlanes: [{name, rows,
  color?}]`), the horizontal twin of phases; a station belongs to the lane its row falls in.
- **Rides** say `from`, `to`, `via`, `pass`, `dwell`, `hidden`. The renderer finds the way —
  shortest, with a penalty for changing line and for doubling back after a via — over the drawn
  hops, junctions included, and a line's open end past the map is a node too.
- Waiting at stops is written into the SVG's keyframes; a hidden ride is left out.
- Rides in the older `stations` shape draw byte for byte as before. Maps using either new
  feature are format 3 (see ADR 0003).

## Consequences

### Positive

- A ride is two clicks; a lane per key area makes a busy map readable.
- Shipped and existing maps are unchanged.

### Negative

- Routing is a guess at "the obvious way"; `via` is the escape hatch when it is not the one
  meant.

## Carried out in

- [`bdc5466`](https://github.com/ERP-LAB-5/metro-map-tool/commit/bdc5466)
- [`25b768a`](https://github.com/ERP-LAB-5/metro-map-tool/commit/25b768a)
