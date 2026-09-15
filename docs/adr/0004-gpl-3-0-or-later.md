# ADR 0004: License under GPL-3.0-or-later from v2.13.0

## Status

Accepted

## Date

2026-09-04 (documented retroactively 2026-09-15)

## Context

The project started under MIT and nothing obliged a change. No dependency brings copyleft in:
Flask and Werkzeug are BSD-3, mcp is MIT.

## Decision

From v2.13.0 the tool is GPL-3.0-or-later: use it, study it, change it, share it — and a
distributed changed version carries the same freedoms and ships its source. Source files carry
an SPDX identifier and a copyright line rather than the long boilerplate.

v1.0.0 to v2.12.0 were published under MIT and stay MIT for anyone holding them; a licence
already granted cannot be withdrawn, and the README says so.

## Consequences

### Positive

- Improvements that are distributed come back as source.

### Negative

- Two licences in the history, explained once in the README.

## Carried out in

- [`c7ed9e8`](https://github.com/ERP-LAB-5/metro-map-tool/commit/c7ed9e8)
