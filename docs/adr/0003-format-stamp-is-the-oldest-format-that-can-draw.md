# ADR 0003: A map is stamped with the oldest spec format that can draw it; older maps are never converted

## Status

Accepted

## Date

2026-09-04 (documented retroactively 2026-09-15)

## Context

The spec grows fields release by release. The stamp is the one thing that cannot be added
retroactively: without it, a pile of files could each be any of several shapes with no way to
tell. Stamping every save with the newest number would lock ordinary maps out of older copies
of the tool for features they never use.

## Decision

- Every save stamps `format`: the **oldest** format that can hold that map — 1 by default,
  2 once it has junctions or branches, 3 once it has swimlanes or routed rides.
- Every field added is optional and reads through a default, so an older map means what it
  always meant. `migrate()` exists for the first change that is not like that; so far it
  only stamps.
- A map claiming a format newer than the tool reads is refused by name, never half-drawn.
- Unknown keys survive a round trip, so an older tool opening a newer map does not strip it.
- Every SVG says which version drew it and which format its map needs.

## Consequences

### Positive

- No conversion step, ever, for maps written so far.
- Upgrading one machine does not lock the others out of maps that use nothing new.

### Negative

- A new feature that changes meaning, rather than adding a field, needs a real migration and a
  format bump — the hook is there, but it will be the first one.

## Carried out in

- [`08a51f4`](https://github.com/ERP-LAB-5/metro-map-tool/commit/08a51f4)
- [`b8b5de8`](https://github.com/ERP-LAB-5/metro-map-tool/commit/b8b5de8)
- [`bdc5466`](https://github.com/ERP-LAB-5/metro-map-tool/commit/bdc5466)
- [`2b86fc0`](https://github.com/ERP-LAB-5/metro-map-tool/commit/2b86fc0)
