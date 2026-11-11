# ADR 0016: A map may ask for free angles; octilinear stays the default

## Status

Accepted

## Date

2026-11-11

## Context

Every hop is drawn octilinear (0°, 45° or 90°): a pair of stations that is not on one of
those lines gets a straight leg and a 45° leg. That is the metro-map look, and a map drawn by
hand is placed for it.

A generated map is not (ADR 0015). Metrofy places the stages of a process as columns and the
alternatives of a stage one above the other. Its first user, a ten-stage migration with three
lanes, had to spread its columns until every change of lane could be a 45° diagonal. The map
came out twice as wide as it needed to be. Lines at 30 to 60 degrees read just as well, and let
the columns close up.

## Decision

- A spec may say `"angles": "free"`. Each hop is then drawn as one straight leg, at whatever
  angle its two stations make. Everything else is unchanged: bundling into parallel tracks,
  rounded corners, notes, rides.
- `"angles": "octilinear"`, or no field, is the default. Every existing map draws exactly as
  before.
- `"free"` needs format 4. An older renderer would route the hops through octilinear bends and
  draw a different shape without saying so, which is what the stamp is for (ADR 0003).
- **Corners are joined with a miter limit.** Two offset legs meeting at a corner are joined
  where they cross. With free angles, two nearly parallel legs on different tracks cross far
  off the map. A crossing more than three gaps from where the legs end falls back to the
  midpoint. Octilinear legs turn by 45° or more, which keeps every crossing within 1.5 gaps,
  so this never changes an octilinear map.
- Metrofy uses free angles for maps with phases. A change of lane inside a phase runs at 60°.
  The next phase starts just far enough on that no change between phases is steeper.

## Consequences

### Positive

- The migration map is about 40% shorter, and a whole process fits a screen.
- A hand-drawn map never sees the difference.

### Negative

- A free-angle map loses the strict metro look; it is for generated maps that would rather be
  compact.
- The designer offers no switch for it yet: the field is kept on save like any other, but set
  in the spec.

## Carried out in

- the commit adding `angles`, the miter limit, `tests/test_angles.py` and this ADR
