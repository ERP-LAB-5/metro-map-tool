#!/usr/bin/env python3
"""
metro_map.py — render a transit-map spec to a standalone SVG.

Input JSON
----------
{
  "stations": {
    "<id>": {"label": str, "gx": number, "gy": number,
             "interchange": bool?,          # white ring instead of a coloured tick
             "label_at": "above"|"below"|"left"|"right"
                        |"above-left"|"above-right"
                        |"below-left"|"below-right",   # optional override
             "label_angle": 0|45|90,                   # counter-clockwise tilt
             "dead_end": "buffer"|"smoke"|"fire"}      # stops here / watch out / get off
  },
  "lines": [ {"name": str, "color": "#rrggbb", "stations": ["<id>", ...],
              "status": "live"|"out-of-service"|"under-construction"|"planned"?} ],
  "zones": [ {"name": str, "color": "#rrggbb", "stations": ["<id>", ...]} ]?,
  "mode": "metro"|"roadmap"?,                    # metro is the default
  "timeline": {"start": "yyyy-mm-dd", "end": "yyyy-mm-dd",
               "interval": "day"|"week"|"month"|"quarter"|"year"}?,  # roadmap only
  "legend": "hide"|"top"|"left"|"bottom"|"right"?,  # bottom is the default
  "scenarios": [ {"name": str, "stations": ["<id>", ...],
                  "color": "#rrggbb"?, "duration": <seconds>?} ]?
}

A line may also carry "notes": [{"at": <hop index>, "text": str, "flip": bool?}],
a short label riding the track between two of its stops — hop 0 is the gap
between stations[0] and stations[1].

Geometry rules
--------------
1. gx/gy are grid cells, not pixels, and need not be whole: a half or quarter
   cell puts two stations closer together. One cell = --cell px.
2. Every segment is octilinear (0° / 45° / 90°). A station pair that is neither
   axis-aligned nor a perfect diagonal is routed straight-then-diagonal, with
   the straight leg on the longer axis.
3. Lines that share a corridor — same infinite line, overlapping span — are
   spread into parallel tracks. Corners are re-joined by intersecting the two
   offset legs, so an offset never leaves a gap at a bend.
4. Corners are rounded with a quadratic Bézier, radius clamped to half the
   shorter adjacent leg.
5. Labels are placed in the first compass direction that no track leaves the
   station on, preferring orthogonal over diagonal and outward over inward.
   "label_at" overrides this per station.
   "label_angle" tilts the text; a tilted label is anchored at the marker edge
   and reads outward, so it never sweeps back across its own station.
6a. A stop with "dead_end" is capped by the terminus bar, at one of three
   volumes: "buffer" simply stops, "smoke" adds drifting puffs — watch out — and
   "fire" adds flames, the burning platform you should get off. The bar is
   oriented by the track arriving at the stop, so it works at any angle; the
   smoke and fire always rise up the page, because that is what makes them read
   as smoke and fire rather than as a jet.
6. A line is in service unless "status" says otherwise: out-of-service is dashed
   and faded and gets a buffer-stop bar wherever it ends on a stop no line in
   service reaches — the dead end; under-construction is a long dash; planned is
   dotted. Only stops every line has retired from are faded with them.
7. A zone is a tinted, dashed band behind the network, sized to hold its
   stations and their labels plus --zone-pad, and named above its top-left
   corner. Zones are drawn in list order; an empty one is skipped.
8. In "roadmap" mode the x axis is a calendar. Column k covers gx in [k, k+1),
   so a light grey boundary line falls on every whole gx and the period's name
   is centred in the band between two lines, the way a Gantt chart reads. The
   ruler spans the whole declared start..end, whether or not a station reaches
   the far end, and column names thin out rather than overlap when a column is
   narrow. A timeline left on a metro map is kept but not drawn.
9. The legend names every line beside a swatch drawn in its own colour and dash
   pattern, laid outside everything else — outside the roadmap header too. On a
   horizontal edge the entries wrap into rows rather than running off the side.
10. A scenario is a traveller's route: an ordered list of stops, animated as a
   dot riding the drawn track from the first to the last. Consecutive stops must
   be joined by some line, in either direction; the journey changes line
   wherever it needs to. The motion is written into the SVG and honours the
   reader's reduced-motion setting.
11. A note rides the middle of one hop, measured along the track after bundling
   so it follows its own line out of a shared corridor, and is rotated to the
   track. The rotation is kept within (-90, 90], so a note never reads upside
   down and a vertical one reads top to bottom.

An optional "style" object (cell, stroke, corner, bundle_gap, label_size) may sit
beside them; the web designer writes it, and command-line flags override it.

Usage
-----
    metro-map-designer                         # browser designer on :8765
    metro-map spec.json -o map.svg
    metro-map spec.json --cell 140 --bundle-gap 14 -o map.svg
    python3 -m metro_map_tool.metro_map spec.json -o map.svg

Any stop used by two or more lines can be flagged as an interchange automatically
(--auto-interchange, and the toggle in the designer's toolbar).

This module is standard library only; app.py adds Flask.
"""

from __future__ import annotations

import argparse
import colorsys
import json
import math
import re
import sys
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

Point = Tuple[float, float]
EPS = 1e-9

from . import REPO_URL, __version__          # noqa: F401  (re-exported)


# ---------------------------------------------------------------- style ----

@dataclass
class Style:
    cell: float = 120.0          # pixels per grid cell
    stroke: float = 10.0         # route width
    corner: float = 22.0         # corner radius
    bundle_gap: float = 13.0     # spacing between parallel tracks
    stop_r: float = 7.5          # ordinary stop radius
    stop_ring: float = 4.0       # ring width on an ordinary stop
    inter_r: float = 11.0        # interchange radius
    label_size: float = 16.0
    label_gap: float = 10.0      # clearance between marker edge and label
    margin: float = 26.0         # padding around the drawing bounds
    zone_pad: float = 34.0       # how far a zone band reaches past its stations
    zone_radius: float = 26.0    # zone corner radius
    zone_fill: float = 0.10      # zone tint opacity
    zone_label_size: float = 13.0
    status_fade: float = 0.5     # how far an out-of-service route falls back
    buffer_len: float = 1.15     # dead-end bar half-length, in stroke widths
    tl_row_h: float = 22.0       # height of one roadmap header row
    tl_label_size: float = 12.0  # period name under the header
    tl_major_size: float = 13.0  # the coarser period above it
    legend_size: float = 13.0    # line name in the legend
    legend_row_h: float = 22.0   # height of one legend entry
    legend_swatch: float = 26.0  # length of the colour stroke beside a name
    legend_gap: float = 10.0     # swatch to name, and note to track
    note_size: float = 12.0      # a note riding a track between two stations
    flame_scale: float = 1.0     # size of the burning-platform fire
    font: str = '"Hanken Grotesk","Helvetica Neue",Helvetica,Arial,sans-serif'


# ------------------------------------------------------------ geometry ----

def add(a: Point, b: Point) -> Point:
    return (a[0] + b[0], a[1] + b[1])


def sub(a: Point, b: Point) -> Point:
    return (a[0] - b[0], a[1] - b[1])


def scale(a: Point, k: float) -> Point:
    return (a[0] * k, a[1] * k)


def norm(a: Point) -> Point:
    n = math.hypot(*a)
    return (0.0, 0.0) if n < EPS else (a[0] / n, a[1] / n)


def perp(a: Point) -> Point:
    return (-a[1], a[0])


def dist(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def sign(x: float) -> float:
    return (x > 0) - (x < 0)


def octilinear(a: Point, b: Point) -> List[Point]:
    """Waypoints from a to b, excluding a. Straight, 45°, or straight+diagonal."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    if abs(dx) < EPS or abs(dy) < EPS or abs(abs(dx) - abs(dy)) < EPS:
        return [b]                                   # already octilinear
    if abs(dx) > abs(dy):                            # straight along x, then 45°
        return [(a[0] + sign(dx) * (abs(dx) - abs(dy)), a[1]), b]
    return [(a[0], a[1] + sign(dy) * (abs(dy) - abs(dx))), b]  # straight along y


def intersect(p1: Point, d1: Point, p2: Point, d2: Point) -> Optional[Point]:
    """Intersection of the infinite lines p1+t*d1 and p2+s*d2."""
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-7:
        return None
    t = ((p2[0] - p1[0]) * d2[1] - (p2[1] - p1[1]) * d2[0]) / den
    return (p1[0] + d1[0] * t, p1[1] + d1[1] * t)


def drop_collinear(pts: Sequence[Point]) -> List[Point]:
    """Remove pass-through vertices so no corner is rounded where there is no bend."""
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        a, b = norm(sub(pts[i], out[-1])), norm(sub(pts[i + 1], pts[i]))
        if abs(a[0] * b[1] - a[1] * b[0]) > 1e-6:
            out.append(pts[i])
    out.append(pts[-1])
    return out


def join_legs(legs: Sequence[Tuple[Point, Point]]) -> List[Point]:
    """Points through a run of offset legs, re-joined at their intersections.

    Two legs that have been pushed apart into parallel tracks no longer meet;
    extending each to where they cross closes the corner rather than leaving a
    step in it. Parallel legs never cross, so those fall back to the midpoint.
    """
    if not legs:
        return []
    pts = [legs[0][0]]
    for a, b in zip(legs, legs[1:]):
        x = intersect(a[0], sub(a[1], a[0]), b[0], sub(b[1], b[0]))
        pts.append(x or scale(add(a[1], b[0]), 0.5))
    pts.append(legs[-1][1])
    out: List[Point] = [pts[0]]
    for p in pts[1:]:
        if dist(p, out[-1]) > 0.5:
            out.append(p)
    return drop_collinear(out)


def rounded_path(pts: Sequence[Point], radius: float) -> str:
    """SVG path data through pts with rounded corners."""
    f = lambda p: f"{p[0]:.2f} {p[1]:.2f}"
    if len(pts) < 2:
        return ""
    out = [f"M {f(pts[0])}"]
    for i in range(1, len(pts) - 1):
        prev, cur, nxt = pts[i - 1], pts[i], pts[i + 1]
        r = min(radius, dist(prev, cur) / 2, dist(cur, nxt) / 2)
        if r < 0.5:
            out.append(f"L {f(cur)}")
            continue
        a = add(cur, scale(norm(sub(prev, cur)), r))
        b = add(cur, scale(norm(sub(nxt, cur)), r))
        out.append(f"L {f(a)} Q {f(cur)} {f(b)}")
    out.append(f"L {f(pts[-1])}")
    return " ".join(out)


# ------------------------------------------------------------- bundling ----

def corridor_key(p: Point, q: Point) -> Tuple[Point, float]:
    """Canonical identity of the infinite line through p and q."""
    u = norm(sub(q, p))
    if u[0] < -EPS or (abs(u[0]) < EPS and u[1] < 0):   # canonical direction
        u = (-u[0], -u[1])
    c = u[0] * p[1] - u[1] * p[0]                        # signed perpendicular offset
    return ((round(u[0], 6), round(u[1], 6)), round(c, 3))


def assign_offsets(segments: List[dict], gap: float) -> None:
    """Give every segment a perpendicular displacement so shared corridors split
    into parallel tracks. Only segments that actually overlap share a bundle."""
    corridors: Dict[tuple, List[dict]] = {}
    for seg in segments:
        u, c = corridor_key(seg["p"], seg["q"])
        seg["u"] = u
        t0 = u[0] * seg["p"][0] + u[1] * seg["p"][1]
        t1 = u[0] * seg["q"][0] + u[1] * seg["q"][1]
        seg["t0"], seg["t1"] = min(t0, t1), max(t0, t1)
        corridors.setdefault((u, c), []).append(seg)

    for group in corridors.values():
        group.sort(key=lambda s: s["t0"])
        cluster, end = [], -math.inf          # sweep overlapping spans together
        clusters = []
        for seg in group:
            if cluster and seg["t0"] > end - 0.5:
                clusters.append(cluster)
                cluster = []
            cluster.append(seg)
            end = max(end, seg["t1"]) if cluster[:-1] else seg["t1"]
        if cluster:
            clusters.append(cluster)

        for cl in clusters:
            ranks = sorted({s["line"] for s in cl})
            n = len(ranks)
            for seg in cl:
                k = ranks.index(seg["line"])
                seg["offset"] = (k - (n - 1) / 2) * gap
                seg["shift"] = scale(perp(seg["u"]), seg["offset"])


# ---------------------------------------------------------------- model ----

class Map:
    def __init__(self, spec: dict, style: Style):
        self.style = style
        self.stations = spec["stations"]
        self.lines = spec["lines"]
        self.pos: Dict[str, Point] = {
            sid: (st["gx"] * style.cell, st["gy"] * style.cell)
            for sid, st in self.stations.items()
        }
        self.segments: List[dict] = []
        self.stop_seg: Dict[Tuple[int, str], dict] = {}   # (line index, station) -> touching segment
        self._build_segments()
        assign_offsets(self.segments, style.bundle_gap)

    def _build_segments(self) -> None:
        for li, line in enumerate(self.lines):
            ids = line["stations"]
            # "hop" is the index of the station pair a leg belongs to, so a note
            # written against hop k can find the legs that carry it — one leg
            # when the pair is octilinear already, two when it bends
            for hop, (a, b) in enumerate(zip(ids, ids[1:])):
                pa = self.pos[a]
                first = None
                for q in octilinear(pa, self.pos[b]):
                    seg = {"line": li, "hop": hop, "p": pa, "q": q,
                           "offset": 0.0, "shift": (0.0, 0.0)}
                    self.segments.append(seg)
                    first = first or seg
                    pa = q
                self.stop_seg[(li, a)] = first                     # segment leaving a
                self.stop_seg.setdefault((li, b), self.segments[-1])
            if len(ids) >= 2:
                self.stop_seg[(li, ids[-1])] = self.segments[-1]   # terminus

    # -- routes ------------------------------------------------------------

    def polyline(self, line_index: int) -> List[Point]:
        """Offset legs of one line, re-joined at their intersections."""
        segs = [s for s in self.segments if s["line"] == line_index]
        return join_legs([(add(s["p"], s["shift"]), add(s["q"], s["shift"]))
                          for s in segs])

    def hop_legs(self, a: str, b: str) -> Optional[List[Tuple[Point, Point]]]:
        """The offset legs of a single hop between two stops, running a to b.

        Any line that runs the pair directly will do, in either direction — a
        traveller does not care which line's timetable it is, only that there is
        track. Returns None when nothing connects them, which is a scenario
        asking to walk across open ground.
        """
        for li, line in enumerate(self.lines):
            ids = line["stations"]
            for k in range(len(ids) - 1):
                pair = (ids[k], ids[k + 1])
                if pair not in ((a, b), (b, a)):
                    continue
                segs = [g for g in self.segments
                        if g["line"] == li and g["hop"] == k]
                legs = [(add(g["p"], g["shift"]), add(g["q"], g["shift"]))
                        for g in segs]
                if pair == (a, b):
                    return legs
                return [(q, p) for p, q in reversed(legs)]
        return None

    def journey(self, stations: Sequence[str]) -> Optional[List[Point]]:
        """The path a traveller takes through a list of stops, or None.

        Built from the same offset legs the lines are drawn from, so the
        traveller sits on the track rather than near it — including the jog
        across to the other rail when the journey changes line at an
        interchange.
        """
        legs: List[Tuple[Point, Point]] = []
        for a, b in zip(stations, stations[1:]):
            hop = self.hop_legs(a, b)
            if hop is None:
                return None
            legs.extend(hop)
        return join_legs(legs) if legs else None

    # -- stations ----------------------------------------------------------

    def end_segment(self, sid: str) -> Optional[dict]:
        """A segment touching this stop, for orienting a terminus marker.

        Prefers a line that ends here, so the bar sits across the track the
        route actually arrives on rather than one merely passing through.
        """
        touching = [self.stop_seg.get((li, sid)) for li in self.lines_through(sid)]
        ending = [self.stop_seg.get((li, sid)) for li in self.lines_through(sid)
                  if self.lines[li]["stations"][-1] == sid
                  or self.lines[li]["stations"][0] == sid]
        for seg in ending + touching:
            if seg:
                return seg
        return None

    def hop_midpoint(self, line_index: int, hop: int) -> Optional[Tuple[Point, Point]]:
        """The middle of one station-to-station hop, and the way the track runs.

        Measured along the legs *after* bundling, so a note on a line sharing a
        corridor rides that line's own track rather than the corridor centre.
        A hop that bends is walked by arc length, so the midpoint of an
        L-shaped run lands where the eye expects it rather than at the corner.
        """
        legs = [(add(g["p"], g["shift"]), add(g["q"], g["shift"]))
                for g in self.segments
                if g["line"] == line_index and g["hop"] == hop]
        if not legs:
            return None
        spans = [dist(a, b) for a, b in legs]
        half = sum(spans) / 2
        for (a, b), span in zip(legs, spans):
            if half <= span or span == spans[-1]:
                t = (half / span) if span > EPS else 0.0
                return add(a, scale(sub(b, a), min(t, 1.0))), norm(sub(b, a))
            half -= span
        return None

    def lines_through(self, sid: str) -> List[int]:
        return [i for i, ln in enumerate(self.lines) if sid in ln["stations"]]

    def directions_at(self, sid: str) -> List[Point]:
        """Unit vectors of every track leaving this station."""
        dirs = []
        for seg in self.segments:
            if dist(seg["p"], self.pos[sid]) < 0.5:
                dirs.append(norm(sub(seg["q"], seg["p"])))
            elif dist(seg["q"], self.pos[sid]) < 0.5:
                dirs.append(norm(sub(seg["p"], seg["q"])))
        return dirs

    def marker_radius(self, sid: str) -> float:
        st = self.stations[sid]
        if not st.get("interchange"):
            return self.style.stop_r + self.style.stop_ring / 2
        # an interchange ring must cover the whole bundle passing through it
        touching = [self.stop_seg.get((li, sid)) for li in self.lines_through(sid)]
        spread = max((abs(seg["offset"]) for seg in touching if seg), default=0.0)
        return max(self.style.inter_r, spread + self.style.stroke / 2 + 1)


COMPASS: Dict[str, Point] = {
    "above": (0, -1), "below": (0, 1), "left": (-1, 0), "right": (1, 0),
    "above-left": (-0.7071, -0.7071), "above-right": (0.7071, -0.7071),
    "below-left": (-0.7071, 0.7071), "below-right": (0.7071, 0.7071),
}
# vertical sides read best and cost the least canvas width, then horizontal,
# then diagonals as the last resort for a station with tracks on all four sides.
TIER = {"above": 0, "below": 0, "left": 1, "right": 1}


def choose_label_side(occupied: Sequence[Point], outward: Point) -> str:
    """First free compass direction: by tier, then pointing away from the map centre."""
    def free(d: Point) -> bool:
        return all(d[0] * o[0] + d[1] * o[1] < 0.85 for o in occupied)

    ranked = sorted(
        COMPASS.items(),
        key=lambda kv: (TIER.get(kv[0], 2),
                        -(kv[1][0] * outward[0] + kv[1][1] * outward[1])),
    )
    for name, d in ranked:
        if free(d):
            return name
    return "above"


LABEL_ANGLES = (0, 45, 90)          # counter-clockwise tilt, in degrees


def label_geometry(side: str, center: Point, r: float, style: Style,
                   angle: float = 0) -> Tuple[Point, str]:
    """Anchor point and text-anchor for a label on the given side.

    A tilted label is anchored at the marker edge and reads outward from the
    station, so the text never sweeps back across the stop it names.
    """
    if angle:
        d = COMPASS[side]
        gap = r + style.label_gap
        rad = math.radians(angle)
        adv = (math.cos(rad), -math.sin(rad))       # text advance, screen axes
        drop = scale(perp(adv), style.label_size * 0.36)   # baseline -> x-height
        at = add(add(center, scale(d, gap)), drop)
        return at, ("start" if adv[0] * d[0] + adv[1] * d[1] >= 0 else "end")
    gap = r + style.label_gap
    cx, cy = center
    half = style.label_size * 0.36
    if side == "above":
        return (cx, cy - gap), "middle"
    if side == "below":
        return (cx, cy + gap + style.label_size * 0.8), "middle"
    if side == "left":
        return (cx - gap, cy + half), "end"
    if side == "right":
        return (cx + gap, cy + half), "start"
    d = COMPASS[side]
    x = cx + d[0] * gap * 0.85
    y = cy + d[1] * gap * 0.85 + (style.label_size * 0.62 if d[1] > 0 else 0)
    return (x, y), ("end" if d[0] < 0 else "start")


# ------------------------------------------------------------ timeline ----
#
# In roadmap mode the x axis is a calendar. Column k spans gx in [k, k+1), so a
# boundary line falls on every whole gx and the period's name is centred in the
# band between two lines — the way a Gantt chart reads. Everything here is
# stdlib date arithmetic; there is no dateutil to lean on.

INTERVALS = ("day", "week", "month", "quarter", "year")
MAX_COLUMNS = 400          # a day interval over ten years is not a diagram

# Spelled out rather than taken from strftime: %b follows the machine's locale,
# and a map must render the same on every machine that opens it.
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def parse_date(value: object) -> date:
    """An ISO yyyy-mm-dd date, or ValueError naming what was wrong with it."""
    if not isinstance(value, str):
        raise ValueError("must be a date as yyyy-mm-dd")
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(f"'{value}' is not a date as yyyy-mm-dd") from None


def add_months(d: date, n: int) -> date:
    """Shift by whole months, clamping the day into the month it lands in."""
    total = d.year * 12 + (d.month - 1) + n
    year, month = divmod(total, 12)
    month += 1
    last = (date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(d.day, last))


def period_start(d: date, interval: str) -> date:
    """Snap a date back to the first day of the period holding it."""
    if interval == "day":
        return d
    if interval == "week":
        return d - timedelta(days=d.weekday())          # back to Monday
    if interval == "month":
        return date(d.year, d.month, 1)
    if interval == "quarter":
        return date(d.year, (d.month - 1) // 3 * 3 + 1, 1)
    return date(d.year, 1, 1)                            # year


def step(d: date, interval: str, n: int = 1) -> date:
    """Advance n whole periods from a period start."""
    if interval == "day":
        return d + timedelta(days=n)
    if interval == "week":
        return d + timedelta(weeks=n)
    if interval == "month":
        return add_months(d, n)
    if interval == "quarter":
        return add_months(d, n * 3)
    return date(d.year + n, d.month, d.day)              # year


def minor_label(d: date, interval: str) -> str:
    """The name of one column."""
    if interval == "day":
        return f"{d.day} {MONTHS[d.month - 1]}"
    if interval == "week":
        return f"W{d.isocalendar()[1]:02d}"
    if interval == "month":
        return MONTHS[d.month - 1]
    if interval == "quarter":
        return f"Q{(d.month - 1) // 3 + 1}"
    return str(d.year)


def major_label(d: date, interval: str) -> str:
    """The coarser period a column belongs to; empty when there is none."""
    if interval in ("day", "week"):
        return f"{MONTHS[d.month - 1]} {d.year}"
    if interval in ("month", "quarter"):
        return str(d.year)
    return ""                                            # year needs no parent


@dataclass
class Timeline:
    start: date              # snapped back to the period holding spec start
    end: date                # the boundary closing the last column
    interval: str
    columns: int

    def boundary(self, k: int) -> date:
        """The date column k opens on; k == columns is the closing boundary."""
        return step(self.start, self.interval, k)


def build_timeline(tl: object) -> Timeline:
    """A Timeline from a spec's timeline block, or ValueError saying why not."""
    if not isinstance(tl, dict):
        raise ValueError("timeline must be an object")
    interval = tl.get("interval") or "month"
    if interval not in INTERVALS:
        raise ValueError("timeline.interval must be one of " + ", ".join(INTERVALS))
    begins = parse_date(tl.get("start"))
    finish = parse_date(tl.get("end"))
    # compare before snapping: a start of 15 Jan snaps back to 1 Jan, and an end
    # of 10 Jan would then look like a valid one-column January
    if finish <= begins:
        raise ValueError("timeline.end must fall after timeline.start")
    start = period_start(begins, interval)
    columns = 1
    while step(start, interval, columns) < finish:
        columns += 1
        if columns > MAX_COLUMNS:
            raise ValueError(
                f"timeline covers more than {MAX_COLUMNS} {interval} columns — "
                "use a coarser interval or a shorter span")
    return Timeline(start, step(start, interval, columns), interval, columns)


def spec_timeline(spec: dict) -> Optional[Timeline]:
    """The timeline a spec draws with, or None when it is not a roadmap.

    A timeline left on a metro map is kept but ignored, so switching modes back
    and forth in the designer never throws the dates away.
    """
    if (spec.get("mode") or "metro") != "roadmap":
        return None
    try:
        return build_timeline(spec.get("timeline"))
    except ValueError:
        return None            # validate_spec reports it; render must not blow up


def timeline_svg(tl: Timeline, style: Style, top: float, bottom: float
                 ) -> Tuple[str, float]:
    """The ruler group, plus the header height it claims above `top`.

    Boundaries run the full height so the header cells line up with the columns
    below them. Labels thin out rather than overlap when a column is narrow.
    """
    s, cell = style, style.cell
    rows = 2 if major_label(tl.start, tl.interval) else 1
    header = s.tl_row_h * rows
    head_top = top - header
    minor_top = head_top + s.tl_row_h * (rows - 1)

    def fits(text: str, width: float, size: float) -> bool:
        return len(text) * size * 0.58 + 8 <= width

    parts: List[str] = []

    # alternating tint, so the eye can carry a row across the diagram
    for k in range(tl.columns):
        if k % 2:
            parts.append(f'    <rect class="tl-band" x="{k * cell:.1f}" y="{top:.1f}" '
                         f'width="{cell:.1f}" height="{bottom - top:.1f}"/>')

    # every boundary gets a line; the header sits on a rule of its own
    for k in range(tl.columns + 1):
        x = k * cell
        parts.append(f'    <line class="tl-line" x1="{x:.1f}" y1="{head_top:.1f}" '
                     f'x2="{x:.1f}" y2="{bottom:.1f}"/>')
    parts.append(f'    <line class="tl-line tl-rule" x1="0" y1="{top:.1f}" '
                 f'x2="{tl.columns * cell:.1f}" y2="{top:.1f}"/>')

    # coarser period above, drawn once over the run of columns that share it
    if rows == 2:
        k = 0
        while k < tl.columns:
            name = major_label(tl.boundary(k), tl.interval)
            j = k
            while j < tl.columns and major_label(tl.boundary(j), tl.interval) == name:
                j += 1
            width = (j - k) * cell
            if k:
                parts.append(f'    <line class="tl-line tl-major-rule" '
                             f'x1="{k * cell:.1f}" y1="{head_top:.1f}" '
                             f'x2="{k * cell:.1f}" y2="{bottom:.1f}"/>')
            if fits(name, width, s.tl_major_size):
                parts.append(
                    f'    <text class="tl-major" x="{(k + (j - k) / 2) * cell:.1f}" '
                    f'y="{head_top + s.tl_row_h * 0.7:.1f}">{esc(name)}</text>')
            k = j

    # column names, thinned to whatever the column width will carry
    names = [minor_label(tl.boundary(k), tl.interval) for k in range(tl.columns)]
    widest = max((len(n) for n in names), default=0) * s.tl_label_size * 0.58 + 8
    every = max(1, math.ceil(widest / cell)) if cell > 0 else 1
    for k, name in enumerate(names):
        if k % every:
            continue
        parts.append(
            f'    <text class="tl-minor" x="{(k + 0.5) * cell:.1f}" '
            f'y="{minor_top + s.tl_row_h * 0.7:.1f}">{esc(name)}'
            f'<title>{tl.boundary(k).isoformat()} → '
            f'{(tl.boundary(k + 1) - timedelta(days=1)).isoformat()}</title></text>')

    return "\n".join(parts), header


# -------------------------------------------------------------- legend ----
#
# Line names never reached the drawing before: they lived in the spec and in the
# designer's side panel, and a reader had to infer what a colour meant from the
# stations it happened to touch.

# A stop can be marked as the end of the road, at three volumes. All three share
# the terminus bar, so they read as one family — the same end, more or less
# urgent — rather than as three unrelated symbols.
#   buffer  the line simply stops here. A planned retirement.
#   smoke   watch out: this is going wrong, though not yet today's problem.
#   fire    the burning platform. Get off this train.
DEAD_ENDS = ("none", "buffer", "smoke", "fire")

LEGEND_AT = ("hide", "top", "left", "bottom", "right")
DEFAULT_LEGEND = "bottom"

# The route dash patterns are written in multiples of style.stroke, which on a
# 26px swatch shows barely one dash. These are the same shapes at swatch scale.
LEGEND_DASH = {
    "live": "",
    "out-of-service": "6 4",
    "under-construction": "10 4",
    "planned": "1 3.5",
}


def legend_at(spec: dict) -> str:
    """Where a spec wants its legend. Anything unrecognised falls back."""
    at = spec.get("legend")
    if at is None:
        return DEFAULT_LEGEND
    return at if at in LEGEND_AT else DEFAULT_LEGEND


def legend_svg(entries: List[Tuple[int, dict]], at: str, style: Style,
               box: Tuple[float, float, float, float]
               ) -> Tuple[str, Tuple[float, float, float, float]]:
    """The legend group, and the drawing bounds grown to hold it.

    `entries` pairs each line with its index in spec.lines — not its position in
    this list — because the swatch borrows the `.l{i}` rule that gives the route
    its dark-mode colour, and a line with no name would otherwise shift every
    index after it onto the wrong colour.

    `box` is the drawing's extent so far; the block is laid alongside it and
    centred against it. Entries on a horizontal edge wrap into rows rather than
    running off the side.
    """
    s = style
    x0, y0, x1, y1 = box
    pad = s.legend_gap

    def entry_w(name: str) -> float:
        return s.legend_swatch + pad + len(name) * s.legend_size * 0.56 + pad * 1.6

    widths = [entry_w(ln["name"]) for _, ln in entries]

    if at in ("top", "bottom"):
        # pack greedily into rows no wider than the drawing, but never split an
        # entry that is simply longer than the map is wide
        limit = max(x1 - x0, max(widths))
        rows: List[List[int]] = [[]]
        used = 0.0
        for i, w in enumerate(widths):
            if rows[-1] and used + w > limit:
                rows.append([])
                used = 0.0
            rows[-1].append(i)
            used += w
        height = len(rows) * s.legend_row_h
        block_w = max(sum(widths[i] for i in row) for row in rows)
        left = x0 + (x1 - x0 - block_w) / 2
        top = (y0 - pad - height) if at == "top" else (y1 + pad)
    else:
        block_w = max(widths)
        height = len(entries) * s.legend_row_h
        rows = [[i] for i in range(len(entries))]
        left = (x0 - pad - block_w) if at == "left" else (x1 + pad)
        top = y0 + (y1 - y0 - height) / 2

    # the union, never just the legend's own box: the caller uses this as the
    # drawing's new extent, and a legend below the map must not shrink it
    bounds = (min(x0, left), min(y0, top),
              max(x1, left + block_w), max(y1, top + height))

    parts: List[str] = []
    for r, row in enumerate(rows):
        cy = top + r * s.legend_row_h + s.legend_row_h / 2
        cx = left
        for i in row:
            li, ln = entries[i]
            state = line_status(ln)
            dash = LEGEND_DASH.get(state, "")
            faded = ' opacity=".55"' if state == "out-of-service" else ""
            # the l{i} class carries the dark-mode colour the routes already use
            parts.append(
                f'    <g class="legend-item"{faded}>'
                f'<line class="legend-swatch l{li}" x1="{cx:.1f}" y1="{cy:.1f}" '
                f'x2="{cx + s.legend_swatch:.1f}" y2="{cy:.1f}" '
                f'stroke="{ln["color"]}"'
                + (f' stroke-dasharray="{dash}"' if dash else "")
                + f'/><text class="legend-name" '
                f'x="{cx + s.legend_swatch + pad:.1f}" '
                f'y="{cy + s.legend_size * 0.36:.1f}">{esc(ln["name"])}'
                + (f'<title>{esc(ln["name"])}{STATUS_LABEL.get(state, "")}</title>'
                   if state != "live" else "")
                + '</text></g>')
            cx += widths[i]

    return "\n".join(parts), bounds


# ----------------------------------------------------------------- svg ----

def buffer_bar(centre: Point, d: Point, style: Style, cls: str = "buffer") -> Tuple[str, Point, Point]:
    """The terminus bar across a track, and the two ends it spans."""
    arm = scale(perp(d), style.stroke * style.buffer_len)
    a, b = sub(centre, arm), add(centre, arm)
    return (f'<line class="{cls}" x1="{a[0]:.1f}" y1="{a[1]:.1f}" '
            f'x2="{b[0]:.1f}" y2="{b[1]:.1f}"/>', a, b)


def flames(centre: Point, style: Style) -> Tuple[str, float]:
    """Flames rising off a terminus bar, and how far above it they reach.

    Fire goes up the page, not along the track. Following the track was the
    obvious thing to try and it is wrong: on a horizontal line the flames fire
    sideways and read as a jet or a dart. The bar is what carries the track's
    angle — that is the platform — and the fire rises from it.

    Each tongue is wide and round at the base and tapers to a point, which is
    what makes it read as fire; a narrow one reads as a dart. Three of them
    overlap, because a single silhouette at this size reads as a triangle.
    """
    u, v = (0.0, -1.0), (1.0, 0.0)              # up the page, and across it
    # taller than it is wide, or the three tongues merge into a blob — and big
    # enough to read as fire rather than as a red speck at map scale
    h = style.stroke * 3.4 * style.flame_scale   # how tall the fire stands
    w = style.stroke * style.buffer_len * 1.15 * style.flame_scale

    def at(along: float, across: float) -> str:
        p = add(add(centre, scale(u, along)), scale(v, across))
        return f"{p[0]:.1f} {p[1]:.1f}"

    def tongue(c: float, hh: float, ww: float, lean: float = 0.0) -> str:
        """One lick: a broad rounded base narrowing to a tip at height hh."""
        t = c + lean
        return (f"M {at(0, c - ww)} "
                f"C {at(hh * 0.20, c - ww * 1.02)} {at(hh * 0.52, c - ww * 0.78)} "
                f"{at(hh * 0.66, t - ww * 0.44)} "
                f"C {at(hh * 0.82, t - ww * 0.30)} {at(hh * 0.93, t - ww * 0.16)} "
                f"{at(hh, t)} "
                f"C {at(hh * 0.93, t + ww * 0.16)} {at(hh * 0.82, t + ww * 0.30)} "
                f"{at(hh * 0.66, t + ww * 0.44)} "
                f"C {at(hh * 0.52, c + ww * 0.78)} {at(hh * 0.20, c + ww * 1.02)} "
                f"{at(0, c + ww)} Z ")

    outer = ('<path class="flame-outer" d="'
             + tongue(-w * 0.50, h * 0.48, w * 0.40, -w * 0.18)
             + tongue(w * 0.50, h * 0.56, w * 0.40, w * 0.16)
             + tongue(0.0, h, w * 0.78, -w * 0.06)
             + '"/>')
    inner = ('<path class="flame-inner" d="'
             + tongue(w * 0.02, h * 0.52, w * 0.34, -w * 0.04)
             + '"/>')
    return outer + inner, h


def smoke(centre: Point, style: Style) -> Tuple[str, float]:
    """A plume drifting up off a terminus bar, and how far above it it reaches.

    Rises up the page for the same reason the fire does. The puffs live in one
    group carrying a single opacity rather than being faded individually: at
    individual opacities their overlaps darken and the plume reads as a handful
    of separate dots instead of one cloud.

    Softer and quieter than the fire by design — smoke is the warning before the
    crisis, and must not compete with a burning platform on the same map.
    """
    h = style.stroke * 3.2 * style.flame_scale
    w = style.stroke * style.buffer_len * 1.15 * style.flame_scale
    puffs = [(0.10, 0.00, 0.52), (0.30, -0.22, 0.60), (0.50, 0.20, 0.64),
             (0.72, -0.16, 0.66), (0.92, 0.16, 0.58)]
    body = "".join(
        f'<circle cx="{centre[0] + across * w:.1f}" '
        f'cy="{centre[1] - up * h:.1f}" r="{r * w:.1f}"/>'
        for up, across, r in puffs)
    return f'<g class="plume">{body}</g>', h


def label_extent(at: Point, anchor: str, width: float, angle: float,
                 style: Style) -> Tuple[float, float, float, float]:
    """Box a label covers, rotated or not, for bounds and zone sizing."""
    if not angle:
        x0 = at[0] - (width / 2 if anchor == "middle" else (width if anchor == "end" else 0))
        return (x0, at[1] - style.label_size, x0 + width, at[1] + style.label_size * 0.3)
    rad = math.radians(angle)
    adv = (math.cos(rad), -math.sin(rad))
    reach = width * (1 if anchor == "start" else -1)
    far = add(at, scale(adv, reach))
    pad = style.label_size * 0.5
    return (min(at[0], far[0]) - pad, min(at[1], far[1]) - pad,
            max(at[0], far[0]) + pad, max(at[1], far[1]) + pad)


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def attr(s: str) -> str:
    """esc() plus the quote, for text that goes inside an attribute value."""
    return esc(s).replace('"', "&quot;")


def lighten(hex_color: str, floor: float = 0.58) -> str:
    """Lift a colour onto a dark ground, damping saturation so it doesn't go neon."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    hh, ll, ss = colorsys.rgb_to_hls(r, g, b)
    if ll < floor:
        ss *= 0.72
        ll = floor
    r, g, b = colorsys.hls_to_rgb(hh, ll, ss)
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


# A rendered map normally carries both palettes and lets the reader's own
# setting choose — that is what an exported file wants, since it may land in a
# light README or a dark slide. The designer needs to force one instead, so its
# preview obeys the toggle in its toolbar rather than the operating system.
THEMES = ("auto", "light", "dark")


def render(spec: dict, style: Style, theme: str = "auto") -> str:
    m = Map(spec, style)
    s = style
    bounds: List[Tuple[float, float, float, float]] = []

    def grow(x0: float, y0: float, x1: float, y1: float) -> None:
        bounds.append((x0, y0, x1, y1))

    # routes
    routes = []
    for i, line in enumerate(m.lines):
        state = line_status(line)
        cls = f"route{STATUS_CLASS[state]}"
        title = esc(line["name"]) + STATUS_LABEL.get(state, "")
        if len(line["stations"]) < 2:      # still being routed in the designer
            routes.append(f'    <path class="{cls} l{i}" d="" stroke="{line["color"]}">'
                          f'<title>{title}</title></path>')
            continue
        pts = m.polyline(i)
        for p in pts:
            grow(p[0] - s.stroke / 2, p[1] - s.stroke / 2,
                 p[0] + s.stroke / 2, p[1] + s.stroke / 2)
        routes.append(
            f'    <path class="{cls} l{i}" d="{rounded_path(pts, s.corner)}" '
            f'stroke="{line["color"]}"><title>{title}</title></path>'
        )

    # Dead ends. A stop marked "dead_end" says so outright, whatever its lines
    # are doing; otherwise an out-of-service line still earns a bar wherever it
    # runs out on a stop the network no longer reaches in service.
    live = {sid for ln in m.lines if line_status(ln) == "live" for sid in ln["stations"]}
    dead_ends = []
    marked: Dict[str, Tuple[str, str]] = {}          # station -> (style, why)

    why_text = {"buffer": "the end of the line",
                "smoke": "watch out — this is going wrong",
                "fire": "burning platform — get off this train"}
    for sid, st in m.stations.items():
        kind = st.get("dead_end")
        if kind in why_text:
            marked[sid] = (kind, f'{st["label"]} — {why_text[kind]}')

    for i, line in enumerate(m.lines):
        ids = line["stations"]
        if line_status(line) != "out-of-service" or len(ids) < 2:
            continue
        for sid in sorted({ids[0], ids[-1]} - live):
            marked.setdefault(sid, ("buffer", f'{line["name"]} ends here'))

    for sid, (kind, why) in marked.items():
        seg = m.end_segment(sid)
        if not seg:
            continue
        here = m.pos[sid]
        # away from the body of the line, along the track it arrives on
        away = (sub(seg["p"], seg["q"]) if dist(seg["p"], here) < dist(seg["q"], here)
                else sub(seg["q"], seg["p"]))
        d = norm(away)
        centre = add(add(here, seg["shift"]),
                     scale(d, m.marker_radius(sid) + s.stroke * 0.55))
        bar, a, b = buffer_bar(centre, d, s)
        if kind == "fire":
            body, reach = flames(centre, s)
        elif kind == "smoke":
            body, reach = smoke(centre, s)
        else:
            body, reach = "", 0.0
        dead_ends.append(
            f'    <g class="dead-end {kind}"><title>{esc(why)}</title>'
            f'{bar}{body}</g>')
        half = max(s.stroke * s.buffer_len,       # whichever of bar or fire is wider
                   s.stroke * s.buffer_len * 1.15 * s.flame_scale)
        grow(min(a[0], b[0]) - half, min(a[1], b[1]) - reach,
             max(a[0], b[0]) + half, max(a[1], b[1]) + half)

    # scenarios — a traveller riding a route from start to end, changing line
    # wherever the journey does
    travellers, scenario_css = [], []
    for si, sc in enumerate(spec.get("scenarios", []) or []):
        if not isinstance(sc, dict):
            continue
        stops = sc.get("stations") or []
        path = m.journey(stops) if len(stops) >= 2 else None
        if not path:
            continue
        d = rounded_path(path, s.corner)
        secs = sc.get("duration")
        secs = float(secs) if isinstance(secs, (int, float)) and 0 < secs <= 600 else 8.0
        colour = sc.get("color") if isinstance(sc.get("color"), str) else "#101820"
        scenario_css.append(
            f'    .t{si} {{ offset-path: path("{d}"); '
            f'animation-duration: {secs:g}s; fill: {colour}; }}')
        travellers.append(
            f'    <circle class="traveller t{si}" cx="0" cy="0" '
            f'r="{s.stop_r * 1.15:.1f}"><title>{esc(sc.get("name") or "scenario")}'
            f'</title></circle>')

    # notes riding the track between two stations — "6 weeks", "nightly batch"
    notes = []
    for i, line in enumerate(m.lines):
        for note in line.get("notes") or []:
            if not isinstance(note, dict):
                continue
            text = (note.get("text") or "").strip()
            found = m.hop_midpoint(i, note.get("at"))
            if not text or not found:
                continue
            at, d = found
            # perpendicular clearance, on the side the note asks for
            side = perp(d) if not note.get("flip") else perp((-d[0], -d[1]))
            gap = s.stroke / 2 + s.legend_gap
            anchor = add(at, scale(side, gap))
            # Read the text along the track, but never upside down: an angle
            # outside (-90, 90] is turned through 180. A leg running straight up
            # therefore becomes +90 rather than -90, so a vertical note always
            # reads top to bottom.
            angle = math.degrees(math.atan2(d[1], d[0]))
            angle = (angle + 180) % 360 - 180        # into (-180, 180] first
            if angle > 90:
                angle -= 180
            elif angle <= -90:
                angle += 180
            notes.append(
                f'    <text class="note" x="{anchor[0]:.1f}" y="{anchor[1]:.1f}" '
                f'transform="rotate({angle:g} {anchor[0]:.1f} {anchor[1]:.1f})" '
                f'text-anchor="middle">{esc(text)}</text>')
            half = len(text) * s.note_size * 0.56 / 2 + s.note_size * 0.6
            grow(anchor[0] - half, anchor[1] - half, anchor[0] + half, anchor[1] + half)

    # stations and labels
    stops, labels = [], []
    extent: Dict[str, Tuple[float, float, float, float]] = {}   # marker + label box
    cx = sum(p[0] for p in m.pos.values()) / len(m.pos)
    cy = sum(p[1] for p in m.pos.values()) / len(m.pos)

    for sid, st in m.stations.items():
        center = m.pos[sid]
        through = m.lines_through(sid)
        r = m.marker_radius(sid)

        # only a stop every line has retired from fades; one that is merely
        # under construction or planned is still drawn at full strength
        muted = " muted" if through and all(
            line_status(m.lines[li]) == "out-of-service" for li in through) else ""
        if st.get("interchange"):
            stops.append(
                f'    <circle class="interchange{muted}" data-station="{attr(sid)}" '
                f'cx="{center[0]:.1f}" cy="{center[1]:.1f}" r="{r:.1f}"/>'
            )
        else:
            li = through[0] if through else None
            seg = m.stop_seg.get((li, sid)) if li is not None else None
            c = add(center, seg["shift"]) if seg else center
            # a station on no line yet has no colour to borrow — draw it in ink
            colour = m.lines[li]["color"] if li is not None else "currentColor"
            stops.append(
                f'    <circle class="stop{muted}" data-station="{attr(sid)}" '
                f'cx="{c[0]:.1f}" cy="{c[1]:.1f}" '
                f'r="{s.stop_r:.1f}" stroke="{colour}"/>'
            )
        grow(center[0] - r, center[1] - r, center[0] + r, center[1] + r)

        outward = norm((center[0] - cx, center[1] - cy))
        if outward == (0.0, 0.0):
            outward = (0.0, -1.0)
        end = st.get("dead_end")
        if end is not None and end not in DEAD_ENDS:
            errors.append(f"{where}: dead_end must be one of " + ", ".join(DEAD_ENDS))
        side = st.get("label_at") or choose_label_side(m.directions_at(sid), outward)
        angle = st.get("label_angle") or 0
        (lx, ly), anchor = label_geometry(side, center, r, s, angle)
        spin = f' transform="rotate({-angle:g} {lx:.1f} {ly:.1f})"' if angle else ""
        labels.append(
            f'    <text class="label" data-station="{attr(sid)}" '
            f'x="{lx:.1f}" y="{ly:.1f}"{spin} '
            f'text-anchor="{anchor}">{esc(st["label"])}</text>'
        )
        w = len(st["label"]) * s.label_size * 0.56          # estimated text box
        box = label_extent((lx, ly), anchor, w, angle, s)
        extent[sid] = (min(center[0] - r, box[0]), min(center[1] - r, box[1]),
                       max(center[0] + r, box[2]), max(center[1] + r, box[3]))
        grow(*box)

    # zones — tinted bands behind the network, wrapping their stations and labels
    zones = []
    for zi, zone in enumerate(spec.get("zones", []) or []):
        boxes = [extent[sid] for sid in zone.get("stations", []) if sid in extent]
        if not boxes:
            continue                                  # nothing placed in it yet
        pad = s.zone_pad
        zx0 = min(b[0] for b in boxes) - pad
        zy0 = min(b[1] for b in boxes) - pad
        zx1 = max(b[2] for b in boxes) + pad
        zy1 = max(b[3] for b in boxes) + pad
        # the label sits just above the band: inside, it would land on the
        # station labels that hug the top edge
        ty = zy0 - 7
        zones.append(
            f'    <g class="zone z{zi}" style="--zc: {zone["color"]}">\n'
            f'      <rect class="zone-band" x="{zx0:.1f}" y="{zy0:.1f}" '
            f'width="{zx1 - zx0:.1f}" height="{zy1 - zy0:.1f}" '
            f'rx="{s.zone_radius:.1f}"><title>{esc(zone["name"])}</title></rect>\n'
            f'      <text class="zone-label" x="{zx0 + 4:.1f}" y="{ty:.1f}">'
            f'{esc(zone["name"])}</text>\n'
            f'    </g>'
        )
        grow(zx0, ty - s.zone_label_size, zx1, zy1)

    # the drawing's own extent, before the roadmap ruler claims room around it
    cx0 = min(b[0] for b in bounds)
    cy0 = min(b[1] for b in bounds)
    cx1 = max(b[2] for b in bounds)
    cy1 = max(b[3] for b in bounds)

    # the ruler spans its whole declared range, whether or not a station reaches
    # the far end, and its header is stacked above everything else
    timeline_group = timeline_css = ""
    tl = spec_timeline(spec)
    if tl:
        timeline_css = f"""    .tl-line {{ stroke: var(--ink); stroke-width: 1; opacity: .16; }}
    .tl-major-rule, .tl-rule {{ opacity: .30; }}
    .tl-band {{ fill: var(--ink); opacity: .025; }}
    .tl-minor {{ font-family: {s.font}; font-size: {s.tl_label_size}px; fill: var(--ink);
                opacity: .55; text-anchor: middle; }}
    .tl-major {{ font-family: {s.font}; font-size: {s.tl_major_size}px; font-weight: 700;
                letter-spacing: .06em; fill: var(--ink); opacity: .8; text-anchor: middle; }}
"""
        cx0 = min(cx0, 0.0)
        cx1 = max(cx1, tl.columns * s.cell)
        top, bottom = cy0 - s.margin / 2, cy1 + s.margin / 2
        body, header = timeline_svg(tl, s, top, bottom)
        cy0, cy1 = top - header, bottom
        timeline_group = f"""  <g id="timeline">
{body}
  </g>
"""

    # the legend goes outside the ruler, not between the ruler and the map, so
    # it is laid out last against everything the drawing has claimed so far
    legend_group = legend_css = ""
    at = legend_at(spec)
    named = [(i, ln) for i, ln in enumerate(m.lines)
             if (ln.get("name") or "").strip()]
    if at != "hide" and named:
        legend_css = f"""    .legend-swatch {{ fill: none; stroke-width: {max(4.0, s.stroke * 0.55):.1f};
                     stroke-linecap: round; }}
    .legend-name {{ font-family: {s.font}; font-size: {s.legend_size}px;
                   font-weight: 600; fill: var(--ink); }}
"""
        body, lbox = legend_svg(named, at, s, (cx0, cy0, cx1, cy1))
        cx0, cy0, cx1, cy1 = lbox
        legend_group = f"""  <g id="legend">
{body}
  </g>
"""

    traveller_group = traveller_css = ""
    if travellers:
        # Motion lives in the file, so a map that is sent to someone animates
        # for them too. offset-path rather than SMIL: it is the one that a
        # viewer's reduced-motion setting can switch off, and a viewer that
        # ignores it is left with the dot parked at the start of the route.
        traveller_css = f"""    .traveller {{ stroke: var(--paper); stroke-width: 2.5;
                 offset-rotate: 0deg; offset-distance: 0%;
                 animation-name: ride; animation-timing-function: linear;
                 animation-iteration-count: infinite; }}
    @keyframes ride {{ from {{ offset-distance: 0%; }} to {{ offset-distance: 100%; }} }}
    @media (prefers-reduced-motion: reduce) {{
      .traveller {{ animation: none; }}
    }}
{chr(10).join(scenario_css)}
"""
        traveller_group = f"""  <g id="scenarios">
{chr(10).join(travellers)}
  </g>
"""

    note_group = note_css = ""
    if notes:
        note_css = f"""    .note {{ font-family: {s.font}; font-size: {s.note_size}px; font-weight: 600;
            fill: var(--ink); stroke: var(--paper); stroke-width: 3.5;
            paint-order: stroke; opacity: .85; }}
"""
        note_group = f"""  <g id="notes">
{chr(10).join(notes)}
  </g>
"""

    x0, y0, x1, y1 = cx0 - s.margin, cy0 - s.margin, cx1 + s.margin, cy1 + s.margin

    dark = "\n".join(
        [f"      .l{i} {{ stroke: {lighten(ln['color'])}; }}" for i, ln in enumerate(m.lines)]
        + [f"      .z{i} {{ --zc: {lighten(zn['color'])}; }}"
           for i, zn in enumerate(spec.get("zones", []) or [])]
    )

    # "auto" ships both palettes behind a media query — the default, and what an
    # exported file gets. A named theme bakes that one in and drops the query,
    # so a forced choice is not undone by the reader's system setting.
    if theme not in THEMES:
        theme = "auto"
    if theme == "dark":
        ground = "--paper: #131b21; --ink: #e8eef3;"
        theme_block = dark.replace("      .", "    .") + ("\n" if dark else "")
    else:
        ground = "--paper: #ffffff; --ink: #101820;"
        theme_block = f"""    @media (prefers-color-scheme: dark) {{
      svg {{ --paper: #131b21; --ink: #e8eef3; }}
{dark}
    }}
""" if theme == "auto" else ""

    dead_group = f"""  <g id="dead-ends">
{chr(10).join(dead_ends)}
  </g>
""" if dead_ends else ""

    zone_group = f"""  <g id="zones">
{chr(10).join(zones)}
  </g>
""" if zones else ""

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0:.0f} {y0:.0f} {x1 - x0:.0f} {y1 - y0:.0f}" width="{x1 - x0:.0f}" height="{y1 - y0:.0f}" role="img" data-cell="{s.cell:g}" data-x0="{x0:.0f}" data-y0="{y0:.0f}">
  <style>
    svg {{ {ground} background: var(--paper); color: var(--ink); }}
    .route {{ fill: none; stroke-width: {s.stroke}; stroke-linecap: round; stroke-linejoin: round; }}
    .status-out {{ stroke-dasharray: {s.stroke * 1.8:.1f} {s.stroke * 1.2:.1f};
                  stroke-linecap: butt; opacity: {s.status_fade}; }}
    .status-build {{ stroke-dasharray: {s.stroke * 3.0:.1f} {s.stroke * 1.1:.1f};
                    stroke-linecap: butt; opacity: .85; }}
    .status-plan {{ stroke-dasharray: {s.stroke * 0.1:.1f} {s.stroke * 1.35:.1f}; opacity: .62; }}
    .buffer {{ stroke: var(--ink); stroke-width: {max(3.0, s.stroke * 0.5):.1f};
              stroke-linecap: butt; opacity: .7; }}
    .dead-end.fire .buffer {{ stroke: #7a2d0e; opacity: .9; }}
    .flame-outer {{ fill: #e1251b; }}
    .flame-inner {{ fill: #f6a821; }}
    .plume {{ fill: var(--ink); opacity: .34; }}
    .muted {{ opacity: {s.status_fade + 0.15:.2f}; }}
    .zone-band {{ fill: var(--zc); fill-opacity: {s.zone_fill}; stroke: var(--zc);
                 stroke-width: 2; stroke-dasharray: 7 6; opacity: .9; }}
    .zone-label {{ font-family: {s.font}; font-size: {s.zone_label_size}px; font-weight: 700;
                  letter-spacing: .09em; text-transform: uppercase; fill: var(--zc); }}
{timeline_css}    .stop {{ fill: var(--paper); stroke-width: {s.stop_ring}; }}
    .interchange {{ fill: var(--paper); stroke: var(--ink); stroke-width: {s.stop_ring}; }}
    .label {{ font-family: {s.font}; font-size: {s.label_size}px; font-weight: 600; fill: var(--ink); }}
{note_css}{traveller_css}{legend_css}{theme_block}  </style>
{timeline_group}{zone_group}  <g id="routes">
{chr(10).join(routes)}
  </g>
{dead_group}{note_group}  <g id="stations">
{chr(10).join(stops)}
  </g>
  <g id="labels">
{chr(10).join(labels)}
  </g>
{traveller_group}{legend_group}</svg>
"""


# ----------------------------------------------------------- spec tools ----

HEX_RE = re.compile(r"#[0-9a-fA-F]{6}$")

# metro is the abstract grid the tool started as; roadmap gives the x axis a
# calendar. Anything else about a spec means the same in both.
MODES = ("metro", "roadmap")

# A line is in service unless it says otherwise. Only "out-of-service" ends in a
# dead end: the other two are lines the network does not reach *yet*.
STATUS_CLASS = {
    "live": "",
    "out-of-service": " status-out",
    "under-construction": " status-build",
    "planned": " status-plan",
}
STATUS_LABEL = {
    "out-of-service": " · out of service",
    "under-construction": " · under construction",
    "planned": " · planned",
}


def line_status(line: dict) -> str:
    status = line.get("status") or "live"
    return status if status in STATUS_CLASS else "live"


def validate_spec(spec: object) -> List[str]:
    """Every problem that would otherwise blow up inside render(), as messages."""
    errors: List[str] = []
    if not isinstance(spec, dict):
        return ["spec must be a JSON object"]

    at = spec.get("legend")
    if at is not None and at not in LEGEND_AT:
        errors.append("spec.legend must be one of " + ", ".join(LEGEND_AT))

    mode = spec.get("mode") or "metro"
    if mode not in MODES:
        errors.append("spec.mode must be one of " + ", ".join(MODES))
    elif mode == "roadmap":
        try:
            build_timeline(spec.get("timeline"))
        except ValueError as exc:
            errors.append(f"roadmap timeline: {exc}")

    stations = spec.get("stations")
    if not isinstance(stations, dict):
        return ["spec.stations must be an object of id -> station"]
    for sid, st in stations.items():
        where = f"station '{sid}'"
        if not isinstance(st, dict):
            errors.append(f"{where}: must be an object")
            continue
        if not isinstance(st.get("label"), str) or not st["label"].strip():
            errors.append(f"{where}: needs a non-empty label")
        for axis in ("gx", "gy"):
            v = st.get(axis)
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                errors.append(f"{where}: {axis} must be a number")
        end = st.get("dead_end")
        if end is not None and end not in DEAD_ENDS:
            errors.append(f"{where}: dead_end must be one of " + ", ".join(DEAD_ENDS))
        side = st.get("label_at")
        if side is not None and side not in COMPASS:
            errors.append(f"{where}: label_at '{side}' is not a compass direction")
        spin = st.get("label_angle")
        if spin is not None and spin not in LABEL_ANGLES:
            errors.append(f"{where}: label_angle must be one of "
                          + ", ".join(str(a) for a in LABEL_ANGLES))

    zones = spec.get("zones", []) or []
    if not isinstance(zones, list):
        errors.append("spec.zones must be a list")
        zones = []
    for i, zn in enumerate(zones, 1):
        where = f"zone {i}"
        if not isinstance(zn, dict):
            errors.append(f"{where}: must be an object")
            continue
        where = f"zone {i} ('{zn.get('name', '')}')"
        if not isinstance(zn.get("name"), str) or not zn["name"].strip():
            errors.append(f"{where}: needs a non-empty name")
        if not isinstance(zn.get("color"), str) or not HEX_RE.match(zn["color"]):
            errors.append(f"{where}: colour must be #rrggbb")
        members = zn.get("stations")
        if not isinstance(members, list):
            errors.append(f"{where}: stations must be a list of ids")
            continue
        for sid in members:
            if sid not in stations:
                errors.append(f"{where}: unknown station '{sid}'")

    editor = spec.get("editor")
    if editor is not None:
        if not isinstance(editor, dict):
            errors.append("spec.editor must be an object")
        else:
            snap = editor.get("snap")
            if snap is not None and (not isinstance(snap, (int, float))
                                     or isinstance(snap, bool) or snap <= 0):
                errors.append("spec.editor.snap must be a positive number")

    scenarios = spec.get("scenarios", []) or []
    if not isinstance(scenarios, list):
        errors.append("spec.scenarios must be a list")
        scenarios = []
    for i, sc in enumerate(scenarios, 1):
        where = f"scenario {i}"
        if not isinstance(sc, dict):
            errors.append(f"{where}: must be an object")
            continue
        where = f"scenario {i} ('{sc.get('name', '')}')"
        if not isinstance(sc.get("name"), str) or not sc["name"].strip():
            errors.append(f"{where}: needs a non-empty name")
        if sc.get("color") is not None and (not isinstance(sc["color"], str)
                                            or not HEX_RE.match(sc["color"])):
            errors.append(f"{where}: colour must be #rrggbb")
        secs = sc.get("duration")
        if secs is not None and (not isinstance(secs, (int, float))
                                 or isinstance(secs, bool) or not 0 < secs <= 600):
            errors.append(f"{where}: duration must be seconds between 0 and 600")
        route = sc.get("stations")
        if not isinstance(route, list):
            errors.append(f"{where}: stations must be a list of ids")
            continue
        for sid in route:
            if sid not in stations:
                errors.append(f"{where}: unknown station '{sid}'")

    lines = spec.get("lines")
    if not isinstance(lines, list):
        return errors + ["spec.lines must be a list"]
    for i, ln in enumerate(lines, 1):
        where = f"line {i}"
        if not isinstance(ln, dict):
            errors.append(f"{where}: must be an object")
            continue
        where = f"line {i} ('{ln.get('name', '')}')"
        if not isinstance(ln.get("name"), str) or not ln["name"].strip():
            errors.append(f"{where}: needs a non-empty name")
        if not isinstance(ln.get("color"), str) or not HEX_RE.match(ln["color"]):
            errors.append(f"{where}: colour must be #rrggbb")
        if ln.get("status") is not None and ln["status"] not in STATUS_CLASS:
            errors.append(f"{where}: status must be one of "
                          + ", ".join(sorted(STATUS_CLASS)))
        route = ln.get("stations")
        if not isinstance(route, list):
            errors.append(f"{where}: stations must be a list of ids")
            continue
        for sid in route:
            if sid not in stations:
                errors.append(f"{where}: unknown station '{sid}'")

        notes = ln.get("notes")
        if notes is None:
            continue
        if not isinstance(notes, list):
            errors.append(f"{where}: notes must be a list")
            continue
        hops = len(route) - 1          # a note rides the gap between two stops
        for n, note in enumerate(notes, 1):
            if not isinstance(note, dict):
                errors.append(f"{where}: note {n} must be an object")
                continue
            if not isinstance(note.get("text"), str) or not note["text"].strip():
                errors.append(f"{where}: note {n} needs a non-empty text")
            spot = note.get("at")
            if not isinstance(spot, int) or isinstance(spot, bool):
                errors.append(f"{where}: note {n} needs an integer 'at'")
            elif hops < 1:
                errors.append(f"{where}: note {n} has nowhere to sit — "
                              "a line needs two stops before it has a gap")
            elif not 0 <= spot < hops:
                errors.append(f"{where}: note {n} sits at {spot}, outside the "
                              f"{hops} gap(s) this route has (0 to {hops - 1})")
    return errors


def spec_warnings(spec: dict) -> List[str]:
    """Things worth saying out loud that are not reasons to refuse a render."""
    out: List[str] = []
    for i, ln in enumerate(spec.get("lines", []), 1):
        route = ln.get("stations") or []
        if len(route) < 2:
            out.append(f"line {i} ('{ln.get('name', '')}'): "
                       f"{'no stops yet' if not route else 'only one stop'} — not drawn")
    for i, zn in enumerate(spec.get("zones", []) or [], 1):
        if not (zn.get("stations") or []):
            out.append(f"zone {i} ('{zn.get('name', '')}'): no stations in it — not drawn")
    used = {sid for ln in spec.get("lines", []) for sid in (ln.get("stations") or [])}
    for sid in spec.get("stations", {}):
        if sid not in used:
            out.append(f"station '{sid}': on no line — drawn without a route")
    on_a_line = {sid for ln in spec.get("lines", [])
                 for sid in (ln.get("stations") or [])}
    for sid, st in spec.get("stations", {}).items():
        if isinstance(st, dict) and st.get("dead_end") in DEAD_ENDS[1:] \
                and sid not in on_a_line:
            out.append(f"station '{sid}': marked as a dead end but on no line — "
                       "the marker needs a track to sit across")

    for i, sc in enumerate(spec.get("scenarios", []) or [], 1):
        if not isinstance(sc, dict):
            continue
        route = sc.get("stations") or []
        name = sc.get("name", "")
        if len(route) < 2:
            out.append(f"scenario {i} ('{name}'): needs at least two stops — not drawn")
            continue
        pairs = {(ln["stations"][k], ln["stations"][k + 1])
                 for ln in spec.get("lines", []) if isinstance(ln.get("stations"), list)
                 for k in range(len(ln["stations"]) - 1)}
        for a, b in zip(route, route[1:]):
            if (a, b) not in pairs and (b, a) not in pairs:
                out.append(f"scenario {i} ('{name}'): no line runs between "
                           f"'{a}' and '{b}' — the journey is not drawn")
                break

    tl = spec_timeline(spec)
    if tl:
        for sid, st in spec.get("stations", {}).items():
            gx = st.get("gx")
            if isinstance(gx, (int, float)) and not isinstance(gx, bool) \
                    and not 0 <= gx <= tl.columns:
                out.append(f"station '{sid}': gx {gx:g} is off the timeline "
                           f"(0 to {tl.columns})")
    return out


def style_from(source: object, base: Optional[Style] = None) -> Style:
    """Overlay the Style fields present in a dict onto a copy of `base`."""
    style = replace(base or Style())
    if isinstance(source, dict):
        for field in ("cell", "stroke", "corner", "bundle_gap", "stop_r",
                      "stop_ring", "inter_r", "label_size", "label_gap", "margin",
                      "zone_pad", "zone_radius", "zone_fill", "zone_label_size",
                      "status_fade", "buffer_len",
                      "tl_row_h", "tl_label_size", "tl_major_size",
                      "legend_size", "legend_row_h", "legend_swatch",
                      "legend_gap", "note_size", "flame_scale"):
            v = source.get(field)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                setattr(style, field, float(v))
        if isinstance(source.get("font"), str) and source["font"].strip():
            style.font = source["font"]
    return style


def style_to_dict(style: Style) -> dict:
    """The style fields the web UI edits, for storing alongside a spec."""
    return {f: getattr(style, f)
            for f in ("cell", "stroke", "corner", "bundle_gap", "label_size", "zone_pad")}


def auto_interchanges(spec: dict) -> int:
    """Mark every station used by two or more lines as an interchange."""
    count: Dict[str, int] = {}
    for ln in spec["lines"]:
        for sid in set(ln["stations"]):
            count[sid] = count.get(sid, 0) + 1
    changed = 0
    for sid, st in spec["stations"].items():
        want = count.get(sid, 0) > 1
        if bool(st.get("interchange")) != want:
            changed += 1
        if want:
            st["interchange"] = True
        else:
            st.pop("interchange", None)
    return changed


# ------------------------------------------------------------- palette ----

PALETTE: List[Tuple[str, str]] = [
    ("blue", "#0098d4"), ("magenta", "#9b0058"), ("orange", "#ee7c0e"),
    ("green", "#007d32"), ("red", "#e1251b"), ("purple", "#7b3fb5"),
    ("teal", "#00a4a7"), ("brown", "#8d5b2d"), ("gold", "#c8a415"),
    ("grey", "#8f9aa4"),
]

# ----------------------------------------------------------------- cli ----

def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    # Windows consoles (and PyCharm's run window) often default to cp1252, which
    # cannot print the "·" and "→" that appear in station labels.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(
        description="Render a transit-map spec to SVG. "
                    "Design one in the browser with: metro-map-designer")
    ap.add_argument("spec", nargs="?", help="JSON spec file, or - for stdin")
    ap.add_argument("-o", "--out", default="-", help="output SVG file (default: stdout)")
    ap.add_argument("--cell", type=float, help="pixels per grid cell")
    ap.add_argument("--stroke", type=float, help="route width")
    ap.add_argument("--corner", type=float, help="corner radius")
    ap.add_argument("--bundle-gap", type=float, help="spacing between parallel tracks")
    ap.add_argument("--label-size", type=float)
    ap.add_argument("--legend", choices=LEGEND_AT,
                    help="where the line-name legend goes (default: %s)" % DEFAULT_LEGEND)
    ap.add_argument("--version", action="version",
                    version=f"metro-map-tool {__version__}")
    ap.add_argument("--auto-interchange", action="store_true",
                    help="flag every stop shared by two or more lines as an interchange")
    args = ap.parse_args(argv)

    if not args.spec:
        ap.print_help()
        print("\n  no spec given — start the web designer with:  metro-map-designer",
              file=sys.stderr)
        return 2

    raw = sys.stdin.read() if args.spec == "-" else open(args.spec, encoding="utf-8").read()
    spec = json.loads(raw)
    spec.setdefault("stations", {})
    spec.setdefault("lines", [])

    errors = validate_spec(spec)
    if errors:
        print(f"{args.spec}: {len(errors)} problem(s)", file=sys.stderr)
        for e in errors:
            print(f"  ! {e}", file=sys.stderr)
        return 2

    if args.auto_interchange:
        auto_interchanges(spec)

    if args.legend:                 # a flag beats what the spec asked for
        spec["legend"] = args.legend

    # A spec saved by the web designer carries its own style; flags override it.
    style = style_from(spec.get("style"))
    for field, value in (("cell", args.cell), ("stroke", args.stroke),
                         ("corner", args.corner), ("bundle_gap", args.bundle_gap),
                         ("label_size", args.label_size)):
        if value is not None:
            setattr(style, field, value)

    svg = render(spec, style)
    if args.out == "-":
        sys.stdout.write(svg)
    else:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(svg)
        print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
