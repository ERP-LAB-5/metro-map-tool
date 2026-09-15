# Architecture decision records

A decision that changes behaviour, a file format, a dependency or the licence is recorded
here, in the same change that carries it out ([ADR 0001](0001-roadmap-as-adrs-commits-and-specs.md)).
The commit keeps the detail and the evidence; the ADR keeps the decision and why, and links
the commits that carried it out. 0002–0013 were backfilled from the commit history.

A decision is never edited away. When it changes, a new ADR supersedes it, and the old one's
status says so.

## Index

| ADR | Decision | Status | Date |
|---|---|---|---|
| [0001](0001-roadmap-as-adrs-commits-and-specs.md) | Keep the roadmap in the repository: ADRs and commits behind, specs ahead | Accepted | 2026-09-15 |
| [0002](0002-one-server-version-checked-saves-and-an-agent-lock.md) | Designer and agents share one server, every save is version-checked, and an agent locks the map it works on | Accepted | 2026-09-02 |
| [0003](0003-format-stamp-is-the-oldest-format-that-can-draw.md) | A map is stamped with the oldest spec format that can draw it; older maps are never converted | Accepted | 2026-09-04 |
| [0004](0004-gpl-3-0-or-later.md) | License under GPL-3.0-or-later from v2.13.0 | Accepted | 2026-09-04 |
| [0005](0005-importers-are-plugins.md) | Importers are plugins with a fetch/build split, options declared once, and credentials that are never options | Accepted | 2026-09-05 |
| [0006](0006-jira-client-read-only-standard-library.md) | The Jira client is read-only and uses only the standard library | Accepted | 2026-09-05 |
| [0007](0007-resync-keeps-the-authors-work.md) | A re-sync keeps the author's work: match by origin, then merge three ways on an upstream snapshot | Accepted | 2026-09-05 |
| [0008](0008-jira-import-starts-from-issue-keys.md) | The Jira import starts from issue keys, with a mapping chosen per level | Accepted | 2026-09-11 |
| [0009](0009-build-on-python-tool-template.md) | Build on python-tool-template, and take its core only through copier | Accepted | 2026-09-12 |
| [0010](0010-tool-errors-reach-the-agent-and-about-shows-state.md) | Tool errors reach the agent, and About shows what the tool is made of | Accepted | 2026-09-15 |
| [0011](0011-swimlanes-and-rides-routed-by-the-track.md) | Swimlanes are row bands, and rides are routed by the track from a start to an end | Accepted | 2026-09-15 |
| [0012](0012-navigation-is-a-designer-feature.md) | Navigation mode belongs to the designer, not to the SVG | Accepted | 2026-09-15 |
| [0013](0013-archimate-import.md) | Read an ArchiMate implementation-and-migration model as a roadmap | Accepted | 2026-09-12 |
| [0014](0014-a-cut-is-made-on-the-spec.md) | Exporting some swimlanes and phases cuts the spec, not the picture | Accepted | 2026-09-15 |

## Writing one

Copy the shape of any ADR here:

```markdown
# ADR NNNN: <the decision, as a sentence>

## Status

Proposed | Accepted | Superseded by ADR NNNN

## Date

YYYY-MM-DD

## Context

What forced a decision: the problem, the constraints, what was tried.

## Decision

What was decided, precisely enough to check the code against.

## Consequences

### Positive

### Negative

## Carried out in

- the commits and releases
```
