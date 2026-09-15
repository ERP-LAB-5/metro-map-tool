#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
layout.py — issue trees, mapped level by level, as a roadmap.

Each key is a line. What hangs beneath it becomes whatever its level was mapped
to: a stop, a branch of the line, a zone around stops, a note on the track — or
nothing, with or without what is beneath it. That mapping is the user's, which
is the point: one team's epic is a stream of work worth its own branch, another
team's is a folder, and no fixed rule gets both right.

Everything made here carries an `upstream` snapshot of what the import decided
— a label, a date, a position — so a re-sync can tell "Jira changed this" from
"somebody changed this on the map" and update only the first. merge.py does
that; this module only has to say what it decided.

Pure: a payload in, a spec out. No network, which is what lets the tests
exercise every role from a stand-in tree.
"""

from __future__ import annotations

import dataclasses
import math
from typing import Dict, List, Optional, Tuple

from ... import metro_map as mm
from .. import SourceError
from .. import plan
from . import tree

ORIGIN = "jira"
# Half the run of a branch's turn: a fork sits a whole column before the first
# stop on its branch, which one row down on square cells is a 45° turn, the way
# a metro map draws one.
TURN = 0.5


@dataclasses.dataclass
class Strand:
    """One run of track: a line's trunk, or a branch off a trunk or a branch."""
    key: str
    name: str
    parent: Optional["Strand"] = None
    stops: List[str] = dataclasses.field(default_factory=list)      # station ids
    forks: List["Strand"] = dataclasses.field(default_factory=list)  # branches off it
    due: Optional[str] = None           # a junction's own date, where it has one
    row: float = 0.0
    fork_id: str = ""
    join_id: str = ""


def build(roots: List[dict], opts: dict, model: Optional[dict] = None,
          versions: Optional[List[dict]] = None,
          phases: Optional[List[dict]] = None) -> Tuple[dict, List[str]]:
    """A roadmap spec from mapped trees. Returns (spec, notes).

    `roots` is the payload's list of {root, issues, truncated}; `opts` the
    coerced source options.
    """
    levels = tree.levels_of(opts.get("levels") or [])
    overrides = tree.roles_of(opts.get("roles") or [])
    stand_in = tree.dates_of(opts.get("dates") or [])
    use_quarters = opts.get("quarter_labels", True)
    done_mark = str(opts.get("done_mark") or "")
    notes: List[str] = []

    stations: Dict[str, dict] = {}      # id -> station, gx filled in later
    dates: Dict[str, str] = {}          # station id -> iso date
    fix: Dict[str, List[str]] = {}      # version id -> station ids
    trunks: List[Strand] = []
    zones: List[dict] = []
    track_notes: List[Tuple[Strand, str, dict, List[str]]] = []
    undated: List[str] = []
    shadowed: List[str] = []
    reach: List[str] = []               # dates the ruler must hold besides stops
    unbranched: List[str] = []          # junctions with nothing dated beneath

    for n, entry in enumerate(roots):
        root = entry.get("root") or {}
        rkey = root.get("key") or ""
        if not rkey:
            continue
        issues = entry.get("issues") or []
        kept = tree.keep(issues, rkey, opts.get("types"),
                         bool(opts.get("open_only")), opts.get("labels"))
        role, said = tree.roles(issues, rkey, kept, levels, overrides)
        notes.extend(said)
        kids: Dict[str, List[dict]] = {}
        for issue in issues:
            if issue.get("key") in role:
                kids.setdefault(issue.get("parent") or "", []).append(issue)
        placed: List[str] = []          # station ids in the order they were made

        def date_of(issue: dict) -> Optional[str]:
            fields = issue.get("fields") or {}
            key = issue.get("key") or ""
            said = (fields.get("duedate") or "")[:10]
            if said:
                if key in stand_in:
                    shadowed.append(key)
                return said
            if use_quarters:
                for label in fields.get("labels") or []:
                    _, end = _quarter(str(label))
                    if end:
                        return end
            return stand_in.get(key)

        def stop(issue: dict, strand: Strand) -> None:
            key = issue.get("key") or ""
            fields = issue.get("fields") or {}
            when = date_of(issue)
            if key.lower() in stations:
                # two keys whose trees overlap: one stop, not two
                notes.append(f"{key} is beneath more than one key — placed once, "
                             "under the first")
                return
            if not when:
                undated.append(key)
                return
            sid = key.lower()
            label = fields.get("summary") or key
            if _state(issue) == "done" and done_mark:
                label = f"{done_mark} {label}"
            stations[sid] = {"label": label, "origin": f"{ORIGIN}:{key}",
                             "date": when, "_state": _state(issue)}
            dates[sid] = when
            strand.stops.append(sid)
            placed.append(sid)
            for fv in fields.get("fixVersions") or []:
                if fv.get("id"):
                    fix.setdefault(str(fv["id"]), []).append(sid)

        def visit(parent_key: str, strand: Strand) -> None:
            for issue in sorted(kids.get(parent_key, []),
                                key=lambda i: (date_of(i) or "9999", i.get("key"))):
                key = issue.get("key") or ""
                fields = issue.get("fields") or {}
                summary = fields.get("summary") or key
                what = role[key]
                if what == "station":
                    stop(issue, strand)
                    visit(key, strand)
                elif what == "hide":
                    visit(key, strand)
                elif what == "junction":
                    branch = Strand(key=key, name=summary, parent=strand,
                                    due=(fields.get("duedate") or "")[:10] or None)
                    if branch.due:
                        reach.append(branch.due)
                    visit(key, branch)
                    if branch.stops or branch.forks:
                        strand.forks.append(branch)
                    elif date_of(issue):
                        # no branch to draw, but the issue itself is dated:
                        # better a stop than losing it
                        unbranched.append(key)
                        stop(issue, strand)
                    else:
                        unbranched.append(key)
                elif what in ("zone", "note"):
                    start = len(placed)
                    visit(key, strand)
                    members = placed[start:]
                    if not members:
                        notes.append(f"{key}: no stops beneath it, so no {what}")
                    elif what == "zone":
                        zones.append({"name": summary, "stations": list(members),
                                      "origin": f"{ORIGIN}:{key}"})
                    else:
                        track_notes.append((strand, key, {"text": summary}, list(members)))

        trunk = Strand(key=rkey, name=(root.get("fields") or {}).get("summary") or rkey)
        visit(rkey, trunk)
        if entry.get("truncated"):
            notes.append(f"{rkey}: the tree stopped at the import limit — raise "
                          "'limit' to take the rest")
        if not (trunk.stops or trunk.forks):
            notes.append(f"{rkey}: nothing beneath it to place, so no line")
            continue
        trunk.row = n                   # provisional; rows are dealt out below
        trunks.append(trunk)

    if unbranched:
        notes.append(f"{_few(unbranched)}: nothing dated beneath, so no branch — "
                     "placed as a stop where it has a date of its own")
    if undated:
        notes.append(f"{len(undated)} issue(s) have no due date, quarter label or "
                     f"date of your own, and were left out: {_few(undated, 5)}")
    if shadowed:
        notes.append("Jira has its own date for " + ", ".join(sorted(set(shadowed)))
                     + " now, so the date given for it is no longer used")
    if not stations:
        raise SourceError("nothing to place — no issue beneath those keys has a "
                          "due date, a quarter label or a date of your own")

    # --------------------------------------------------------------- ruler --
    timeline = _ruler(sorted(list(dates.values()) + reach), opts, model)
    tl = mm.build_timeline(timeline)
    at = lambda iso: round(tl.gx_of(mm.parse_date(iso)), 3)
    for sid, when in dates.items():
        stations[sid]["gx"] = at(when)

    # ---------------------------------------------------------------- rows --
    # Each line starts below everything the one before it used. Its branches
    # go one row beneath what they leave, sharing that row where their reach
    # does not overlap. The narrowest take the nearest rows, so that a wider
    # branch forced further down wraps around them rather than crossing them.
    floor = 0
    for trunk in trunks:
        trunk.row = floor
        taken: Dict[float, List[Tuple[float, float]]] = {}
        for strand in _nested(trunk, lambda st: _width(st, stations, at))[1:]:
            lo, hi = _reach(strand, stations, at)
            want = (lo - TURN, hi + TURN)
            row = strand.parent.row + 1
            while any(a < want[1] and want[0] < b for a, b in taken.get(row, [])):
                row += 1
            taken.setdefault(row, []).append(want)
            strand.row = row
        floor = max(s.row for s in _walk(trunk)) + 1

    junctions: Dict[str, dict] = {}
    for trunk in trunks:
        for strand in _walk(trunk):
            for sid in strand.stops:
                stations[sid]["gy"] = strand.row
        _span(trunk, stations, junctions, at)

    # A fork turning off before the first column, or a join after the last,
    # needs the ruler to hold it. Whole columns are added, so every position
    # moves by exactly that many and nothing drawn changes relative to the rest.
    if junctions:
        low = min(j["gx"] for j in junctions.values())
        high = max(j["gx"] for j in junctions.values())
        before = max(0, math.ceil(-low - 1e-9))
        after = max(0, math.ceil(high - tl.columns - 1e-9))
        if before or after:
            timeline = dict(timeline,
                            start=mm.step(tl.start, tl.interval, -before).isoformat(),
                            end=mm.step(tl.end, tl.interval, after).isoformat())
            tl = mm.build_timeline(timeline)
            for point in list(stations.values()) + list(junctions.values()):
                point["gx"] = round(point["gx"] + before, 3)

    # --------------------------------------------------------- fix versions --
    capsules: List[dict] = []
    capsule_stops: Dict[int, List[str]] = {}
    line_of = {sid: t for t in trunks for s in _walk(t) for sid in s.stops}
    for version in versions or []:
        vid = str(version.get("id") or "")
        when = (version.get("releaseDate") or "")[:10]
        on = []
        for sid in fix.get(vid, []):
            if line_of.get(sid) and line_of[sid] not in on:
                on.append(line_of[sid])
        if not when or len(on) < 2:
            continue
        members = []
        for trunk in on:
            mark = f"v-{vid}-r{int(trunk.row)}"
            stations[mark] = {"label": version.get("name") or vid, "gx": at(when),
                              "gy": trunk.row, "date": when,
                              "origin": f"{ORIGIN}:version/{vid}#{int(trunk.row)}"}
            capsule_stops.setdefault(id(trunk), []).append(mark)
            members.append(mark)
        capsules.append({"stations": members, "label": version.get("name") or vid,
                         "origin": f"{ORIGIN}:version/{vid}", "label_angle": 45})

    # --------------------------------------------------------------- routes --
    _nudge(stations, junctions)
    lines: List[dict] = []
    for n, trunk in enumerate(trunks):
        route = _route(trunk, stations, junctions, extra=capsule_stops.get(id(trunk)))
        line = {"name": trunk.name,
                "color": plan.LANE_COLOURS[n % len(plan.LANE_COLOURS)],
                "stations": route, "origin": f"{ORIGIN}:{trunk.key}"}
        branches = []
        for strand in _walk(trunk)[1:]:
            branches.append({"name": strand.name, "origin": f"{ORIGIN}:{strand.key}",
                             "stations": [strand.fork_id]
                             + _route(strand, stations, junctions)
                             + [strand.join_id],
                             "upstream": {"name": strand.name}})
        if branches:
            line["branches"] = branches
        states = {stations[s].get("_state") for st in _walk(trunk) for s in st.stops}
        status = plan.STATUS["done"] if states == {"done"} else (
            plan.STATUS["todo"] if states == {"todo"} else None)
        if status:
            line["status"] = status
        line["upstream"] = {"name": trunk.name, "status": status}
        lines.append(line)

    for strand, key, note, members in track_notes:
        owner = next((ln for ln, t in zip(lines, trunks) if t is strand), None)
        if owner is None:
            notes.append(f"{key}: a track note can only ride a line's main route, "
                         "not a branch — left out")
            continue
        route = owner["stations"]
        first = next((s for s in route if s in members), None)
        if first is None or len(route) < 2:
            notes.append(f"{key}: none of its stops is on the line's main route, "
                         "so there is no track to put the note on")
            continue
        hop = min(route.index(first), len(route) - 2)
        owner.setdefault("notes", []).append(
            {"at": hop, "text": note["text"], "origin": f"{ORIGIN}:{key}",
             "upstream": {"text": note["text"]}})

    palette = plan.LANE_COLOURS
    for n, zone in enumerate(zones):
        zone["color"] = palette[(n + len(trunks)) % len(palette)]
        zone["upstream"] = {"name": zone["name"]}

    crowded = _crowded(stations)
    for sid, st in stations.items():
        st.pop("_state", None)
        if sid in crowded:
            # a plan bunches up around its deadlines, and level labels a few
            # days apart overprint one another; tilted, each can be read
            st["label_at"] = "above-right"
            st["label_angle"] = 45
        else:
            st["label_at"] = "above" if int(st["gy"]) % 2 == 0 else "below"
        st["upstream"] = {"label": st["label"], "date": st.get("date"),
                          "gx": st["gx"], "gy": st["gy"]}
    for jid, jn in junctions.items():
        jn["upstream"] = {"gx": jn["gx"], "gy": jn["gy"]}

    # one swimlane per key, around its line and every branch off it
    swimlanes = []
    for trunk, line in zip(trunks, lines):
        rows = [float(st.row) for st in _walk(trunk)]
        lane_rows = [min(rows), max(rows)]
        swimlanes.append({"name": trunk.name, "rows": lane_rows,
                          "origin": f"{ORIGIN}:{trunk.key}#lane",
                          "upstream": {"name": trunk.name, "rows": lane_rows}})

    spec: dict = {"mode": "roadmap", "timeline": timeline, "stations": stations,
                  "lines": lines, "legend": "bottom", "swimlanes": swimlanes,
                  "style": {"cell": 120, "stroke": 9, "label_size": 13}}
    if junctions:
        spec["junctions"] = junctions
    if zones:
        spec["zones"] = zones
    if capsules:
        spec["interchanges"] = capsules
    if phases:
        spec["phases"] = phases
    return spec, notes


# --------------------------------------------------------------- helpers --

def _crowded(stations: Dict[str, dict]) -> set:
    """Stops with a neighbour on their own row less than a column away."""
    rows: Dict[float, List[Tuple[float, str]]] = {}
    for sid, st in stations.items():
        rows.setdefault(st["gy"], []).append((st["gx"], sid))
    out = set()
    for points in rows.values():
        points.sort()
        for (ax, a), (bx, b) in zip(points, points[1:]):
            if bx - ax < 1.0:
                out.update((a, b))
    return out


def _few(keys: List[str], shown: int = 3) -> str:
    """A list of keys short enough to read in one note."""
    more = len(keys) - shown
    return ", ".join(keys[:shown]) + (f" and {more} more" if more > 0 else "")


def _quarter(label: str):
    from . import quarter_dates           # late: the package imports this module
    return quarter_dates(label)


def _state(issue: dict) -> str:
    cat = (((issue.get("fields") or {}).get("status") or {})
           .get("statusCategory") or {}).get("key")
    return {"done": "done", "indeterminate": "active"}.get(cat, "todo")


def _walk(strand: Strand) -> List[Strand]:
    """A strand and every branch beneath it, depth first — the row order."""
    out = [strand]
    for fork in strand.forks:
        out.extend(_walk(fork))
    return out


def _ruler(found: List[str], opts: dict, model: Optional[dict]) -> dict:
    """The timeline: the dates placed, widened to the map's own when re-syncing.

    Using the map's own ruler matters beyond looks: a position is only
    comparable with the one recorded last time if both were measured on the
    same ruler, and that comparison is how a re-sync tells a stop somebody
    dragged from one nobody touched.
    """
    first, last = found[0], found[-1]
    had = (model or {}).get("timeline") if (model or {}).get("mode") == "roadmap" else None
    interval = str(opts.get("interval") or "") or (had or {}).get("interval") \
        or plan.pick_interval(first, last)
    start = mm.parse_date(first)
    end = mm.parse_date(last)
    if isinstance(had, dict):
        try:
            start = min(start, mm.parse_date(had["start"]))
            end = max(end, mm.parse_date(had["end"]))
            interval = had.get("interval") or interval
        except (KeyError, ValueError, TypeError):
            pass
    if start >= end:
        end = mm.step(start, interval, 1)
    return {"start": start.isoformat(), "end": end.isoformat(), "interval": interval}


def _nested(strand: Strand, width) -> List[Strand]:
    """Like _walk, but the branches off each strand narrowest first."""
    out = [strand]
    for fork in sorted(strand.forks, key=width):
        out.extend(_nested(fork, width))
    return out


def _width(strand: Strand, stations: Dict[str, dict], at) -> float:
    lo, hi = _reach(strand, stations, at)
    return hi - lo


def _reach(strand: Strand, stations: Dict[str, dict], at) -> Tuple[float, float]:
    """How far along the axis a strand and everything off it runs, by its stops."""
    xs = [stations[s]["gx"] for st in _walk(strand) for s in st.stops]
    xs += [at(st.due) for st in _walk(strand) if st.due]
    return (min(xs), max(xs)) if xs else (0.0, 0.0)


def _span(strand: Strand, stations: Dict[str, dict], junctions: Dict[str, dict],
          at) -> Tuple[float, float]:
    """Place a strand's forks, deepest first, and return the reach of its points.

    A branch dropping n rows turns off n columns early and rejoins n columns
    late: with square cells that is a clean 45° each way, and a wider branch
    below a narrower one wraps around it instead of cutting through it.
    """
    xs = [stations[s]["gx"] for s in strand.stops]
    for fork in strand.forks:
        lo, hi = _span(fork, stations, junctions, at)
        drop = max(1, fork.row - strand.row)
        low = lo - drop * 2 * TURN
        high = hi + drop * 2 * TURN
        if fork.due:
            high = max(high, at(fork.due))
        base = fork.key.lower()
        fork.fork_id, fork.join_id = f"j-{base}-fork", f"j-{base}-join"
        junctions[fork.fork_id] = {"gx": round(low, 3), "gy": strand.row,
                                   "origin": f"{ORIGIN}:{fork.key}#fork"}
        junctions[fork.join_id] = {"gx": round(high, 3), "gy": strand.row,
                                   "origin": f"{ORIGIN}:{fork.key}#join"}
        xs.extend([low, high])
    return (min(xs), max(xs)) if xs else (0.0, 0.0)


def _nudge(stations: Dict[str, dict], junctions: Dict[str, dict]) -> None:
    """Two points due at the same spot on one row step apart by a third of a column.

    A junction takes its turn like a stop: a fork sitting exactly on a station
    would bend the track through the station's marker.
    """
    taken: Dict[Tuple[float, float], int] = {}
    points = [(p["gx"], pid, p) for pid, p in list(stations.items())
              + list(junctions.items())]
    for gx, pid, point in sorted(points, key=lambda t: (t[0], t[1])):
        spot = (gx, point["gy"])
        nth = taken.get(spot, 0)
        taken[spot] = nth + 1
        if nth:
            point["gx"] = round(gx + nth / 3.0, 3)


def _route(strand: Strand, stations: Dict[str, dict], junctions: Dict[str, dict],
           extra: Optional[List[str]] = None) -> List[str]:
    """A strand's own points in order along the axis: its stops, and the fork
    and join of every branch leaving it."""
    points = [(stations[s]["gx"], s) for s in strand.stops + list(extra or [])]
    for fork in strand.forks:
        points.append((junctions[fork.fork_id]["gx"], fork.fork_id))
        points.append((junctions[fork.join_id]["gx"], fork.join_id))
    return [pid for _, pid in sorted(points)]
