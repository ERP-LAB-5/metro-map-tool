# ADR 0005: Importers are plugins with a fetch/build split, options declared once, and credentials that are never options

## Status

Accepted

## Date

2026-09-05 (documented retroactively 2026-09-15)

## Context

A map is worth drawing once; keeping it worth looking at means re-drawing it whenever the plan
moves. The git-history import proved the shape; GitHub and Jira needed the same thing without
three copies of the plumbing, and without a token ever being able to leak into a map.

## Decision

- A **Source** declares its options once, and that declaration drives the command line, the MCP
  catalogue and the designer's Import dialog.
- **`fetch`** does all the network work and returns plain data; **`build`** is pure. A recorded
  payload (`to_file` / `from_file`) replays with no network — which is how importers are tested.
- **Credentials are not options.** They come from the environment or a per-source config file
  (created 0600), so a token is never a value the option machinery holds, and cannot reach a
  spec, a response or an error message. A config file someone already keeps elsewhere can be
  pointed at, with the usual key spellings understood.
- Sources may also declare a browser (Nodes and Views) and option groups for the Import screen.
- Third-party sources join through the `metro_map_tool.sources` entry point.

## Consequences

### Positive

- A new source gets a CLI, an MCP tool and a form for free.
- Offline and testable by construction.

### Negative

- A source's options are flat values; structured choices (like the Jira mapping) are encoded as
  `KEY=value` lists.

## Carried out in

- [`0b2ab3c`](https://github.com/ERP-LAB-5/metro-map-tool/commit/0b2ab3c)
- [`6f15be8`](https://github.com/ERP-LAB-5/metro-map-tool/commit/6f15be8)
- [`c963034`](https://github.com/ERP-LAB-5/metro-map-tool/commit/c963034)
