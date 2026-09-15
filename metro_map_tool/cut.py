# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ERP-LAB-5
"""Cut a map down to some of its swimlanes and phases, for export.

A busy map is often shared one key area or one period at a time. The cut is
made on a copy of the spec rather than on the picture (ADR 0014): what is left
is an ordinary map, so it is laid out, labelled and stamped like any other, and
nothing is half-hidden behind a clip.

- A station or junction stays when its row falls in a chosen lane and its
  column in a chosen phase. Lanes not chosen close up, so the chosen ones stack.
- The ruler shrinks to the chosen phases, and every column shifts with it.
- Lines, branches, zones, interchanges, notes and rides keep what is left of
  them. A line that lost stops beyond one end runs on past the map there.

Standard library only, like the renderer.
"""

from __future__ import annotations

import copy
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

from . import metro_map as mm

EPS = 1e-6


def names(items: object) -> List[str]:
    return [(it.get("name") or "").strip() for it in (items or [])
            if isinstance(it, dict)]


def problems(spec: dict, lanes: Optional[Sequence[str]],
             phases: Optional[Sequence[str]]) -> List[str]:
    """What is wrong with a cut, in sentences. Empty when it can be made."""
    out = []
    if lanes is not None:
        have = names(spec.get("swimlanes"))
        if not have:
            out.append("this map has no swimlanes to choose from")
        for n in lanes:
            if have and n not in have:
                out.append(f"no swimlane is called '{n}' — there are: " + ", ".join(have))
        if have and not lanes:
            out.append("choose at least one swimlane")
    if phases is not None:
        have = names(spec.get("phases"))
        if not mm.spec_timeline(spec):
            out.append("phases are dates on a roadmap — this map has no timeline to cut")
        elif not have:
            out.append("this map has no phases to choose from")
        for n in phases:
            if have and n not in have:
                out.append(f"no phase is called '{n}' — there are: " + ", ".join(have))
        if have and not phases:
            out.append("choose at least one phase")
    return out


def cut(spec: dict, lanes: Optional[Sequence[str]] = None,
        phases: Optional[Sequence[str]] = None) -> dict:
    """A copy of spec holding only the named lanes and phases (None means all).

    Raises ValueError with the problems when the cut cannot be made.
    """
    said = problems(spec, lanes, phases)
    if said:
        raise ValueError("; ".join(said))
    out = copy.deepcopy(spec)
    everything_lanes = lanes is None or set(lanes) >= set(names(spec.get("swimlanes")))
    everything_phases = phases is None or set(phases) >= set(names(spec.get("phases")))
    if everything_lanes and everything_phases:
        return out

    keep_y = shift_y = None
    if not everything_lanes:
        keep_y, shift_y = _lane_rows(out, set(lanes))
    keep_x = None
    shift_x = 0
    if not everything_phases:
        keep_x, shift_x = _phase_columns(out, set(phases))

    def kept(point: dict) -> bool:
        gx, gy = point.get("gx"), point.get("gy")
        if not isinstance(gx, (int, float)) or not isinstance(gy, (int, float)):
            return False
        return (keep_y is None or keep_y(gy)) and (keep_x is None or keep_x(gx))

    alive = set()
    for kind in ("stations", "junctions"):
        points = out.get(kind) or {}
        for pid in list(points):
            if kept(points[pid]):
                alive.add(pid)
            else:
                del points[pid]
        for p in points.values():
            if shift_y:
                p["gy"] = round(p["gy"] - shift_y(p["gy"]), 6)
            if shift_x:
                p["gx"] = round(p["gx"] - shift_x, 6)
    stations = set(out.get("stations") or {})

    lines = []
    for line in out.get("lines") or []:
        if not isinstance(line, dict):
            continue
        if not _trim_route(line, alive, stations):
            continue
        branches = [br for br in line.get("branches") or []
                    if isinstance(br, dict) and _trim_route(br, alive, stations)]
        if "branches" in line:
            line["branches"] = branches
        lines.append(line)
    out["lines"] = lines
    kept_lines = {ln.get("name") for ln in lines}

    zones = []
    for zone in out.get("zones") or []:
        if isinstance(zone, dict):
            zone["stations"] = [s for s in zone.get("stations") or [] if s in alive]
            if zone["stations"]:
                zones.append(zone)
    if "zones" in out:
        out["zones"] = zones

    if "interchanges" in out:
        out["interchanges"] = [
            dict(ix, stations=[s for s in ix.get("stations") or [] if s in alive])
            for ix in out["interchanges"] or [] if isinstance(ix, dict)
            and sum(1 for s in ix.get("stations") or [] if s in alive) >= 2]

    if "scenarios" in out:
        out["scenarios"] = [sc for sc in out["scenarios"] or []
                            if isinstance(sc, dict) and _trim_ride(sc, alive, kept_lines)]

    if not everything_lanes:
        out["swimlanes"] = [ln for ln in out.get("swimlanes") or []
                            if isinstance(ln, dict) and (ln.get("name") or "").strip() in lanes]
        for ln in out["swimlanes"]:
            a, b = ln["rows"]
            ln["rows"] = [round(a - shift_y(a), 6), round(b - shift_y(b), 6)]
    if not everything_phases:
        out["phases"] = [ph for ph in out.get("phases") or []
                         if isinstance(ph, dict) and (ph.get("name") or "").strip() in phases]
    return out


def _lane_rows(spec: dict, chosen: set):
    """Which rows stay, and how far each kept row moves up to close the gaps."""
    bands: List[Tuple[float, float, bool]] = []
    for ln in spec.get("swimlanes") or []:
        if not isinstance(ln, dict) or not isinstance(ln.get("rows"), list) \
                or len(ln["rows"]) != 2:
            continue
        a, b = sorted(float(r) for r in ln["rows"])
        bands.append((a - 0.5, b + 0.5, (ln.get("name") or "").strip() in chosen))
    kept = [(a, b) for a, b, yes in bands if yes]
    # a gap is a band nobody chose that no chosen band overlaps; it closes up
    gaps = [(a, b) for a, b, yes in bands if not yes
            and not any(a < kb and ka < b for ka, kb in kept)]

    def keep(gy: float) -> bool:
        return any(a - EPS <= gy <= b + EPS for a, b in kept)

    def shift(gy: float) -> float:
        return sum(b - a for a, b in gaps if b <= gy + EPS)

    return keep, shift


def _phase_columns(spec: dict, chosen: set):
    """Which columns stay, and how many whole columns the ruler loses in front."""
    tl = mm.spec_timeline(spec)
    spans: List[Tuple[date, date]] = []
    for ph in spec.get("phases") or []:
        if isinstance(ph, dict) and (ph.get("name") or "").strip() in chosen:
            try:
                spans.append((mm.parse_date(ph.get("from")), mm.parse_date(ph.get("to"))))
            except ValueError:
                continue
    ranges = [(tl.gx_of(a), tl.gx_of(b)) for a, b in spans]

    def keep(gx: float) -> bool:
        return any(a - EPS <= gx <= b + EPS for a, b in ranges)

    if not spans:
        return keep, 0
    begins = max(min(a for a, _ in spans), tl.start)
    finish = min(max(b for _, b in spans), tl.end)
    start = mm.period_start(begins, tl.interval)
    shift = 0
    while tl.boundary(shift) < start:
        shift += 1
    block: Dict[str, object] = spec["timeline"]
    block["start"] = start.isoformat()
    if finish > start:
        block["end"] = finish.isoformat()
    return keep, shift


def _trim_route(route: dict, alive: set, stations: set) -> bool:
    """Keep a line's or branch's surviving stops. False when nothing worth drawing is left."""
    ids = route.get("stations") or []
    left = [s for s in ids if s in alive]
    if not any(s in stations for s in left):
        return False
    if len(left) == len(ids):
        return True
    notes = []
    for note in route.get("notes") or []:
        at = note.get("at") if isinstance(note, dict) else None
        if not isinstance(at, int) or not 0 <= at < len(ids) - 1:
            continue
        a, b = ids[at], ids[at + 1]
        if a in alive and b in alive:
            notes.append(dict(note, at=left.index(a)))
    if "notes" in route:
        route["notes"] = notes
    first = ids.index(left[0])
    last = ids.index(left[-1])
    ends = set()
    was = mm.line_continues(route)
    if was in ("start", "both") or first > 0:
        ends.add("start")
    if was in ("end", "both") or last < len(ids) - 1:
        ends.add("end")
    route["continues"] = "both" if len(ends) == 2 else (ends.pop() if ends else "none")
    route["stations"] = left
    return True


def _trim_ride(ride: dict, alive: set, lines: set) -> bool:
    """Keep a ride whose ends still exist. False when it has nowhere to go."""
    if not mm.is_routed_ride(ride):
        ride["stations"] = [s for s in ride.get("stations") or [] if s in alive]
        return len(ride["stations"]) >= 2

    def there(point: object) -> bool:
        if isinstance(point, str):
            return point in alive
        if isinstance(point, dict):
            return point.get("line") in lines
        return point is None

    if not there(ride.get("from")) or not there(ride.get("to")):
        return False
    if not all(there(v) for v in ride.get("via") or []):
        return False
    if "pass" in ride:
        ride["pass"] = [s for s in ride.get("pass") or [] if s in alive]
    return True
