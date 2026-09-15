# ADR 0007: A re-sync keeps the author's work: match by origin, then merge three ways on an upstream snapshot

## Status

Accepted

## Date

2026-09-05 (documented retroactively 2026-09-15)

## Context

Importing twice must not undo an afternoon's arranging — that is the reason a map is worth
keeping rather than regenerating. But "never overwrite" meant a changed due date in Jira never
moved its stop either.

## Decision

- Everything an importer makes carries an `origin`; a re-sync matches by it and keeps the
  author's ids, so routes, zones and rides that name them keep working.
- Elements also carry an `upstream` snapshot of what the importer decided (label, date,
  position; a name for lines, branches, zones and lanes). A re-sync compares three ways:
  changed only upstream → applied; changed only on the map → kept silently; changed on both →
  the map's kept and the clash reported.
- Something gone upstream is kept and reported; `prune` removes it.
- Maps from before snapshots keep their values once and merge three ways from then on.

## Consequences

### Positive

- A stop nobody dragged follows its new date; one somebody dragged stays.
- The designer, MCP `import_map(into=…)` and `--model` all behave the same, being one merge.

### Negative

- A snapshot is extra data in the map; it must be left alone by hand editors and agents
  (the skill says so).

## Carried out in

- [`0b2ab3c`](https://github.com/ERP-LAB-5/metro-map-tool/commit/0b2ab3c)
- [`2191191`](https://github.com/ERP-LAB-5/metro-map-tool/commit/2191191)
- [`bdc5466`](https://github.com/ERP-LAB-5/metro-map-tool/commit/bdc5466)
