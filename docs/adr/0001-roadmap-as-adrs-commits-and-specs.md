# ADR 0001: Keep the roadmap in the repository: ADRs and commits behind, specs ahead

## Status

Accepted

## Date

2026-09-15

## Context

metro-map-tool went from a script to a designer with importers, an MCP server and a
template underneath it in two weeks — 35 tags. Every decision along the way is written
down, but only in long commit bodies, and what comes next lives in conversations. The tool
now depends on python-tool-template, which was extracted from it, and sap-di-tools depends
on the same template; nothing shows how those three move together.

The DHC repositories already settled a convention for this (`docs/adr`, a spec per release,
a parking lot), and it has held up.

## Decision

The roadmap is kept in this repository, in three parts:

- **Behind: ADRs and commits.** A decision that changes behaviour, a file format, a
  dependency or the licence gets an ADR in `docs/adr/NNNN-*.md`, in the same change that
  carries it out. The commit keeps the detail and the evidence; the ADR keeps the decision
  and why, and names the commits.
- **Ahead: specs.** Ideas not yet scheduled go on `docs/specs/PARKING-LOT.md`. When a release
  is scoped, its items move into `docs/specs/vX.Y.Z.md`, a living document until the
  release, frozen after it.
- **Between: a roadmap drawn with this tool.** `docs/roadmap/roadmap.json` shows the tool,
  the template and sap-di-tools as lines, releases as stations, and a join wherever a tool
  took a template version. Each release adds its station.

The decisions made before this ADR are backfilled as 0002–0013 from their commits.

## Consequences

### Positive

- One place each for "why is it like this", "what shipped" and "what is next".
- The dependency on the template is visible, not remembered.
- The roadmap is itself a map this tool draws, so it doubles as a real-world test.

### Negative

- A release has one more step: the roadmap station. `tests/test_docs.py` makes forgetting
  it fail rather than drift.
- Backfilled ADRs are reconstructions from commits, not records made at the time.

## Carried out in

- this change
