# Specs: the next station

What is behind this tool lives in the [ADRs](../adr/README.md) and the commits. What is ahead
lives here ([ADR 0001](../adr/0001-roadmap-as-adrs-commits-and-specs.md)).

## Lifecycle

1. **Parked.** An idea, a known gap or a loose end goes on [PARKING-LOT.md](PARKING-LOT.md)
   with what it is, why it is parked, and what we would need to know to pick it up. Nothing
   there is scheduled.
2. **Scoped.** When a release is decided, its items move from the parking lot into
   `vX.Y.Z.md`, written from [TEMPLATE.md](TEMPLATE.md). Status *Draft*, then *In progress*.
   It is a living document: questions get answered in it, scope changes are made in it.
3. **Frozen.** At release the spec is marked *Frozen* and not edited again; what actually
   shipped is in the release commit and its tag. Decisions made along the way have ADRs.
4. **On the map.** The release gets its station on the [roadmap](../roadmap/README.md).

Only one release spec is open at a time. Whatever is not finished goes back on the parking lot,
not into a spec for the release after.

## Now

3.6.2 is released ([3.6.0](v3.6.0.md), [3.6.1](v3.6.1.md), [3.6.2](v3.6.2.md), all frozen). No release is scoped after it. **3.7** is the parking lot: the tool is being used as it is, and what
comes next will be decided from that use.
