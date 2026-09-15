# metro-map-tool vX.Y.Z — [Release name]

> **Status:** Draft | In progress | Frozen
> **Target date:** YYYY-MM-DD
> **Previous release:** vA.B.C
> **Template:** python-tool-template vA.B.C (unchanged | take vA.B.C)

## Goals

What this release achieves, in 2–5 bullets, for someone drawing maps.

## Big picture

How the features fit together, what changes for the person in the designer, the agent using the
MCP tools, and the command line.

## Features

### [Feature name] (designer | renderer | MCP | importer: jira/github/git/archimate | all)

**From the parking lot:** the item this came from, if any.

**What:** one paragraph.

**Details:**
- Spec format: new optional fields? does `needs_format()` change? (ADR 0003)
- Existing and shipped maps: still byte-identical?
- Designer / MCP / CLI: which of them get it?

**Acceptance criteria:**
- [ ] Criterion
- [ ] Tests that pin it

## Open questions

- [ ] Question

## Dependencies and risks

- Template changes needed first (a core change is a template release, then `copier update` —
  ADR 0009)
- Other tools on the template (sap-di-tools)

## Decisions

ADRs written for this release:

- ADR NNNN — …

## Release checklist

- [ ] `./test.sh` passes (full, not `--quick`)
- [ ] Existing maps render as before, or the change is intended and said
- [ ] README and the agent skill say what is new
- [ ] Station added to `docs/roadmap/roadmap.json` and the SVG re-rendered
- [ ] Unfinished items moved back to the parking lot
- [ ] `scripts/release.py X.Y.Z`, pushed, GitHub release created
- [ ] This spec marked Frozen
