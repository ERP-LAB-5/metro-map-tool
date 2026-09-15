# Parking lot — the next station (3.7)

> **Status:** Parked. Nothing here is scheduled.
> **Since:** 3.5.2, 2026-09-15 (3.6.0 took nothing from here)

The tool is being used as it is for a while; what comes next is decided from that use. When a
release is scoped, the items it takes move from here into `vX.Y.Z.md`
(see [README](README.md)). New ideas and loose ends are added here, not built straight away.

Each item says **what** it is, **why it is parked**, and **what we would need to know** to pick
it up.

---

## Bring the ArchiMate importer up to 3.5

**What.** `--from archimate` ([ADR 0013](../adr/0013-archimate-import.md)) predates swimlanes and
upstream snapshots. Candidates:
- a swimlane per top-level WorkPackage, as Jira has one per key;
- `upstream` snapshots, so re-importing a changed model updates what changed in the model and
  keeps what was arranged on the map ([ADR 0007](../adr/0007-resync-keeps-the-authors-work.md));
- perhaps rides from one Plateau to the next, as the migration path.

**Why parked.** Left as is by choice, until it is clear how much the ArchiMate route is used.

**What we would need to know.** Whether models are re-imported after hand-arranging (snapshots
matter) or regenerated each time (they do not); which WorkPackage level is the useful lane.

## Branch names are not drawn on the map

**What.** A branch's `name` shows in the designer only; on the drawing a branch is an unnamed
stretch of its line.

**Why parked.** Track notes can already label it by hand; no map has needed more yet.

**What we would need to know.** Where a name would sit without crowding stops — probably a track
note placed automatically on the branch's first hop.

## Overlapping Jira branches can cross

**What.** Branches whose date ranges overlap are packed onto rows narrowest first; when they
still overlap, tracks cross. Crossings avoid stops, and the author can drag.

**Why parked.** Rare in the maps seen so far, and dragging fixes it.

**What we would need to know.** Real imports where it happens often enough to matter.

## Dragged stops can drift on a re-sync

**What.** A stop someone dragged keeps its column. If a re-sync moves the timeline's start
earlier, the columns shift under it and the dragged stop no longer sits on the date it had.

**Why parked.** Needs the timeline start to move, which is unusual once a plan is under way.

**What we would need to know.** Whether to store a dragged position as a date instead of a
column in roadmap mode — a format question (ADR 0003).

## Long pick groups do not collapse

**What.** "Show all" for pick groups over twelve buttons was planned for 3.5 and not built.

**Why parked.** The search box on every pick group covers it.

**What we would need to know.** Whether anyone scrolls long groups rather than typing.

## Not yet verified in 3.5

**What.**
- Swimlanes per Jira key have not been run against a live Jira site (tests use recorded
  payloads).
- The navigation voice has not been heard (it was checked in a headless browser).

**Why parked.** Waiting for real use.

**What we would need to know.** A first live Jira import with several keys; one ride followed
with voice on.

## sap-di-tools is on template v0.3.0

**What.** sap-di-tools was built on python-tool-template v0.3.0. v0.4.0 (service status in About)
and v0.4.1 (template version shown) are not in it.

**Why parked.** Not required; nothing in sap-di-tools needs them yet.

**What we would need to know.** Nothing; take it the next time sap-di-tools changes for another
reason. It moves the sap-di-tools join on the roadmap.

## The roadmap is updated by hand at release

**What.** Adding a release's station to `docs/roadmap/roadmap.json` and re-rendering is a manual
step; `tests/test_docs.py` only catches an SVG that no longer matches its spec.

**Why parked.** Two minutes per release; the release script is core-owned, so automating it is a
template change (ADR 0009), and other tools on the template do not keep a roadmap map.

**What we would need to know.** Whether other template tools want a roadmap too — then it
belongs in the template.

## Save a cut as a map of its own

**What.** Export can cut a map down to some swimlanes and phases
([ADR 0014](../adr/0014-a-cut-is-made-on-the-spec.md)), but only as an SVG. *Save as… a cut* would
keep the part as a map to edit.

**Why parked.** An open question in the 3.6.0 spec, left out of the release.

**What we would need to know.** Whether a saved cut should stay linked to the whole map (and
re-cut when the whole changes) or become a separate copy.
