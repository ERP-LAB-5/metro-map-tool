# ADR 0012: Navigation mode belongs to the designer, not to the SVG

## Status

Accepted

## Date

2026-09-15 (documented retroactively 2026-09-15)

## Context

Following one ride like a satnav — a camera, a board, speech — needs a viewport and a runtime
that an exported SVG does not have.

## Decision

- Navigation lives in the designer only: one ride at a time, a following camera, a board with
  next stop / line / date / change, a strip of stops, keyboard and optional voice.
- It draws over the map without changing it, so it runs while an agent holds the lock and
  survives re-renders.
- The route's stops speak in bubbles opened on the side the map gave their labels; other
  labels stay, and lines keep their colours.

## Consequences

### Positive

- The file stays a plain, portable drawing; the experience lives where it can.

### Negative

- Someone given only the SVG gets the animation, not the navigation.

## Carried out in

- [`bdc5466`](https://github.com/ERP-LAB-5/metro-map-tool/commit/bdc5466)
- [`25b768a`](https://github.com/ERP-LAB-5/metro-map-tool/commit/25b768a)
