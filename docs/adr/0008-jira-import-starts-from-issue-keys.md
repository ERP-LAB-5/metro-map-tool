# ADR 0008: The Jira import starts from issue keys, with a mapping chosen per level

## Status

Accepted

## Date

2026-09-11 (documented retroactively 2026-09-15)

## Context

The first Jira browser walked from a list of hundreds of projects through two spines with one
filter box, and was broken outright once Atlassian removed the search endpoint. Somebody who
wants initiatives on a roadmap already knows their keys, and one team's epic is a stream worth
a branch while another's is only a folder — no fixed mapping gets both right.

## Decision

- A three-step wizard: **keys** (each its own line), **filter** (type, open only, free-text
  labels), **map** (a role per level — station, junction, zone, track note, hide, don't
  import — overridable per issue, with a stand-in date where Jira has none, used only until
  Jira has one).
- Everything the wizard picks is an ordinary option (`roots`, `levels`, `roles`, `dates`, …),
  so the CLI, MCP and a re-sync repeat it.
- Branches pack onto rows narrowest first and turn at 45°; one swimlane per key (since 3.5).

## Consequences

### Positive

- The import is as coarse or as fine as each team's Jira needs.

### Negative

- The mapping is powerful and therefore has more to explain; the wizard's live count carries
  most of that.

## Carried out in

- [`2191191`](https://github.com/ERP-LAB-5/metro-map-tool/commit/2191191)
- [`bdc5466`](https://github.com/ERP-LAB-5/metro-map-tool/commit/bdc5466)
