# ADR 0002: Designer and agents share one server, every save is version-checked, and an agent locks the map it works on

## Status

Accepted

## Date

2026-09-02 (documented retroactively 2026-09-15)

## Context

The designer (a person in a browser) and the MCP tools (an agent) edit the same maps. The
browser used to read a map only on Open, so an agent's save sat on disk unseen and a later
browser save replaced it without a word. Later the reverse turned up: the MCP tools never
said which version they had read, so an agent's save always won over a person's.

## Decision

- **One server, one maps folder.** The MCP server is a client of the designer's HTTP API,
  never a second implementation of it.
- **Optimistic concurrency.** A save carries the version (a content hash, not an mtime) it
  was built on; the server refuses a stale one with 409 and hands back what is on disk.
  The browser watches the open map and reloads or asks. The MCP server remembers what it
  read and sends it too, and refuses to replace a map it never read unless told to.
- **An agent lock.** Reading a map locks it for the agent until its save lands or two minutes
  pass. The designer folds its panel away, goes read-only, and offers Take over; a take-over
  refuses the agent's next save until it reads again. Single user, single machine: the lock
  is a dict in the server's memory.

## Consequences

### Positive

- Neither side can silently undo the other.
- Nothing extra to run or persist; a restart clears any lock.

### Negative

- An agent must read before it writes, and is told so when it forgets.
- The lock is advisory for anything that does not go through the server.

## Carried out in

- [`57cac3b`](https://github.com/ERP-LAB-5/metro-map-tool/commit/57cac3b)
- [`3f4c4ce`](https://github.com/ERP-LAB-5/metro-map-tool/commit/3f4c4ce)
- [`74fe87e`](https://github.com/ERP-LAB-5/metro-map-tool/commit/74fe87e)
- [`ed36588`](https://github.com/ERP-LAB-5/metro-map-tool/commit/ed36588)
