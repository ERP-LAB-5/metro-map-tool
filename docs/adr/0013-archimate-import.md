# ADR 0013: Read an ArchiMate implementation-and-migration model as a roadmap

## Status

Accepted

## Date

2026-09-12 (documented retroactively 2026-09-15)

## Context

Some plans already exist as an ArchiMate 3.2 model in Turtle (work packages, deliverables,
plateaus, gaps). Redrawing them by hand would create a second definition that drifts.

## Decision

- `--from archimate` maps the model with ArchiMate's own vocabulary: a WorkPackage is a line
  (a nested one rides its parent's), Deliverables and ImplementationEvents are stations, a
  Plateau is a capsule where lanes meet, a Gap is reported in the notes.
- A plateau carries no date: a state is reached when the work realising it finishes, so the date
  is computed — the model's own rule. A deliverable takes its producing work's date likewise.
- Turtle is parsed by rdflib when installed, otherwise by a small built-in reader; a test asserts
  they agree.
- It has not yet been brought up to 3.5 (lanes, snapshots) — parked, see
  `docs/specs/PARKING-LOT.md`.

## Consequences

### Positive

- A model-backed roadmap stays in step with its model.

### Negative

- Only the implementation-and-migration subset is read.

## Carried out in

- [`2c8431e`](https://github.com/ERP-LAB-5/metro-map-tool/commit/2c8431e)
