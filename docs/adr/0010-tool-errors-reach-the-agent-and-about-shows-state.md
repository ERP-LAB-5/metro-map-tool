# ADR 0010: Tool errors reach the agent, and About shows what the tool is made of

## Status

Accepted

## Date

2026-09-15 (documented retroactively 2026-09-15)

## Context

The MCP SDK hides the text of any exception except its own ToolError, so every sentence written
for an agent — a refused save, a validation problem, an unconfigured importer — reached it as
"Error executing tool …". And nothing said which parts of a tool were actually working.

## Decision

- The template bridge raises `ToolError` (also a ValueError / ConnectionError), and this tool's
  own raises use it, so messages reach the model.
- About lists services with a dot each — green up, red down, grey not part of this install:
  web server, MCP server (from a heartbeat), update check, agent skill, workspace, plus this
  tool's maps folders and Jira/GitHub importers.
- About and every drawing say which template and tool version they come from.

## Consequences

### Positive

- An agent can act on a refusal instead of retrying blind.

### Negative

- The heartbeat is traffic every 20 s while an MCP server runs.

## Carried out in

- [`ed36588`](https://github.com/ERP-LAB-5/metro-map-tool/commit/ed36588)
- [`4f6d0ac`](https://github.com/ERP-LAB-5/metro-map-tool/commit/4f6d0ac)
