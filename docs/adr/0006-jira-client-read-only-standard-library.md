# ADR 0006: The Jira client is read-only and uses only the standard library

## Status

Accepted

## Date

2026-09-05 (documented retroactively 2026-09-15)

## Context

The importer needs four or five REST reads. Taking the `jira` package and its dependencies for
that would be a poor trade, and a tool that draws diagrams has no business changing tickets by
accident.

## Decision

- The client is urllib over one `request()` method that refuses anything but GET, with a
  sentence saying so. Changing tickets from a map would be a separate feature with its own
  confirmation.
- Credentials go in the Authorization header, never a URL; errors name the status, never a body.
- When Atlassian removed `/rest/api/3/search`, the client moved to `/search/jql` with token
  paging, behind the same method.

## Consequences

### Positive

- No dependency to track; read-only is a property of the code, not of care.

### Negative

- Jira API changes land on us directly (the 410 on `/search` was one).

## Carried out in

- [`6f15be8`](https://github.com/ERP-LAB-5/metro-map-tool/commit/6f15be8)
- [`2191191`](https://github.com/ERP-LAB-5/metro-map-tool/commit/2191191)
