#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
plan.py — turn dated work items into a roadmap spec.

GitHub and Jira disagree about almost everything except the shape of the thing
being planned: some streams of work, some items with dates on them, and some
moments where several streams have to land together. That shape is what this
module maps, so each source only has to say what its own JSON means.

Nothing here talks to a network. Given a list of items it is a pure function,
which is what makes both sources testable from a saved payload.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .. import metro_map as mm

LANE_COLOURS = [c for _, c in mm.PALETTE]

# A stream's status, in the vocabulary the renderer already has.
STATUS = {"done": "live", "active": "under-construction", "todo": "planned"}


def pick_interval(first: str, last: str) -> str:
    """A column width that gives a readable number of them.

    Roughly a dozen to thirty columns: fewer and the chart says nothing about
    when, more and the labels collide.
    """
    span = (mm.parse_date(last) - mm.parse_date(first)).days
    if span <= 70:
        return "week"
    if span <= 420:
        return "month"
    if span <= 1500:
        return "quarter"
    return "year"


def build(items: List[dict], groups: List[dict], *, source: str,
          interval: str = "", done_mark: str = "", catch_all: str = "",
          lane_order: Optional[List[str]] = None) -> Tuple[dict, List[str]]:
    """A roadmap spec from work items.

    Each item is {id, label, lane, date, origin, state?}; each group is
    {name, date, origin, members: [item id]} — a release, a fix version, a
    milestone: the moment several lanes have to land together.
    """
    notes: List[str] = []
    dated = [it for it in items if it.get("date")]
    if len(dated) != len(items):
        notes.append(f"{len(items) - len(dated)} item(s) have no date and were "
                     "left out — a roadmap places things by when they are due")
    if not dated:
        raise ValueError("nothing with a date to place")

    first = min(it["date"] for it in dated)
    last = max(it["date"] for it in dated)
    interval = interval or pick_interval(first, last)
    # A small project can have everything in one milestone, which is a ruler of
    # no width — and a calendar has to span at least the period it is drawn in.
    if first >= last:
        last = mm.step(mm.parse_date(first), interval, 1).isoformat()
    timeline = {"start": first, "end": last, "interval": interval}
    tl = mm.build_timeline(timeline)
    at_of = lambda d: round(tl.gx_of(mm.parse_date(d)), 3)

    # lanes in the order asked for, then whatever else turned up, earliest first
    seen = {it["lane"] for it in dated}
    order = [ln for ln in (lane_order or []) if ln in seen]
    # a catch-all lane goes to the bottom whatever its dates say: it is where
    # things landed for want of a label, not a stream anybody planned
    # The name breaks the tie, and it has to: two lanes starting on the same day
    # would otherwise come out in set-iteration order, which differs run to run,
    # so the same input would import as a different map each time.
    rest = sorted(seen - set(order),
                  key=lambda name: (name == catch_all,
                                    min(it["date"] for it in dated
                                        if it["lane"] == name),
                                    name))
    lanes = order + rest
    row_of = {name: row for row, name in enumerate(lanes)}

    # Which lanes each release touches, worked out before anything is placed,
    # because a release needs a column of its own to stand in.
    rows_of_group: Dict[int, List[int]] = {}
    reserved: set = set()
    for i, grp in enumerate(groups):
        if not grp.get("date"):
            continue
        rows = sorted({row_of[it["lane"]] for it in dated
                       if it["id"] in set(grp.get("members") or [])})
        if len(rows) < 2:
            continue                       # a capsule needs two lanes to cross
        rows_of_group[i] = rows
        reserved.add(at_of(grp["date"]))

    stations: Dict[str, dict] = {}
    routes: Dict[str, List[Tuple[float, str]]] = {name: [] for name in lanes}
    for name in lanes:
        row = row_of[name]
        taken: Dict[float, int] = {}
        for it in sorted((i for i in dated if i["lane"] == name),
                         key=lambda i: (i["date"], i["id"])):
            gx = at_of(it["date"])
            # Two items due the same week would land on one another, so the
            # later ones step along a third of a cell. A column a release stands
            # in is skipped over entirely: the release bar owns that spot, and
            # a stop hidden under a capsule loses its label.
            nth = taken.get(gx, 1 if gx in reserved else 0)
            taken[gx] = nth + 1
            place = round(gx + nth / 3.0, 3)
            label = it["label"]
            if it.get("state") == "done" and done_mark:
                label = f"{done_mark} {label}"
            stations[it["id"]] = {
                "label": label, "gx": place, "gy": row,
                "label_at": "above" if row % 2 == 0 else "below",
                "origin": it["origin"],
                # The date is the fact; gx is a drawing of it, and the two are
                # deliberately different numbers — items due the same week are
                # nudged apart so both labels can be read. Reading a date back
                # out of gx would turn that nudge into a real change of date,
                # so the date is kept as itself.
                "date": it["date"]}
            routes[name].append((place, it["id"]))

    # A release is a moment, not a set of things: what makes it worth drawing is
    # the vertical line it puts through every lane that has to be ready. So it
    # gets one stop per lane at its own date, and the capsule joins those.
    capsules = []
    for i, grp in enumerate(groups):
        rows = rows_of_group.get(i)
        if not rows:
            continue
        gx = at_of(grp["date"])
        at: List[str] = []
        for row in rows:
            mark = grp["origin"].replace(":", "-").replace("/", "-") + f"-r{row}"
            stations[mark] = {"label": grp["name"], "gx": gx, "gy": row,
                              "origin": f"{grp['origin']}#{row}"}
            routes[lanes[row]].append((gx, mark))
            at.append(mark)
        capsules.append({"stations": at, "label": grp["name"],
                         "origin": grp["origin"], "label_angle": 45})

    # Nudging a stop along can push it past the final column, which draws it
    # off the end of the ruler. The chart is what has to accommodate the work,
    # not the other way round, so the calendar grows to cover it.
    furthest = max((gx for gx, _ in (p for r in routes.values() for p in r)),
                   default=0.0)
    if furthest > tl.columns:
        import math
        grown = tl.columns + math.ceil(furthest - tl.columns)
        timeline = dict(timeline, end=tl.boundary(grown).isoformat())
        notes.append(f"the calendar was extended to {timeline['end']} to leave "
                     "room for what lands in the last column")

    lines = []
    for name in lanes:
        line = {"name": name,
                "color": LANE_COLOURS[row_of[name] % len(LANE_COLOURS)],
                "stations": [sid for _, sid in sorted(routes[name])],
                "origin": f"{source}:lane/{name}"}
        states = {it.get("state") for it in dated if it["lane"] == name}
        if states == {"done"}:
            line["status"] = STATUS["done"]
        elif states == {"todo"}:
            line["status"] = STATUS["todo"]
        lines.append(line)

    spec: dict = {"mode": "roadmap", "timeline": timeline,
                  "stations": stations, "lines": lines, "legend": "bottom",
                  "style": {"cell": 120, "stroke": 9, "label_size": 13}}
    if capsules:
        spec["interchanges"] = capsules
    return spec, notes
