# ADR 0009: Build on python-tool-template, and take its core only through copier

## Status

Accepted

## Date

2026-09-12 (documented retroactively 2026-09-15)

## Context

About, self-update, restart, the loopback guard and the MCP bridge were invented here, then
extracted into python-tool-template (with sap-di-tools). Keeping the hand-written originals
meant the tool that had all of it was the one tool not maintained by it.

## Decision

- metro-map-tool is an instance of python-tool-template. `core/` (server, bridge, services,
  agent panel, skill installer, identity) is template-owned and changes only through
  `copier update`; the tool keeps its own app, MCP tools, renderer and importers.
- A fix to core is made in the template, released there, and taken here — never edited in place.
- The tool shows which template version it is built on (About, `--version`, the startup lines).

## Consequences

### Positive

- A core fix reaches every tool; this tool is no longer the odd one out.

### Negative

- A core change is two releases (template, then tool), and a tool-owned file the template also
  touches (`mcp_server.py`) needs a hand-resolved merge now and then.

## Carried out in

- [`de43643`](https://github.com/ERP-LAB-5/metro-map-tool/commit/de43643)
- [`4a7b701`](https://github.com/ERP-LAB-5/metro-map-tool/commit/4a7b701)
- [`c032d1d`](https://github.com/ERP-LAB-5/metro-map-tool/commit/c032d1d)
