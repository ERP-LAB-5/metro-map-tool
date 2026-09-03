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
             "label_angle": 0|45|90}                   # counter-clockwise tilt
  },
  "lines": [ {"name": str, "color": "#rrggbb", "stations": ["<id>", ...],
              "status": "live"|"out-of-service"|"under-construction"|"planned"?} ],
  "zones": [ {"name": str, "color": "#rrggbb", "stations": ["<id>", ...]} ]?,
  "mode": "metro"|"roadmap"?,                    # metro is the default
  "timeline": {"start": "yyyy-mm-dd", "end": "yyyy-mm-dd",
               "interval": "day"|"week"|"month"|"quarter"|"year"}?   # roadmap only
}

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

An optional "style" object (cell, stroke, corner, bundle_gap, label_size) may sit
beside them; the web designer writes it, and command-line flags override it.

Usage
-----
    python3 app.py                             # browser designer on :8765
    python3 metro_map.py spec.json -o map.svg
    python3 metro_map.py spec.json --cell 140 --bundle-gap 14 -o map.svg

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
            for a, b in zip(ids, ids[1:]):
                pa = self.pos[a]
                first = None
                for q in octilinear(pa, self.pos[b]):
                    seg = {"line": li, "p": pa, "q": q, "offset": 0.0, "shift": (0.0, 0.0)}
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
        legs = [(add(s["p"], s["shift"]), add(s["q"], s["shift"])) for s in segs]
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

    # -- stations ----------------------------------------------------------

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
    start = period_start(parse_date(tl.get("start")), interval)
    finish = parse_date(tl.get("end"))
    if finish <= start:
        raise ValueError("timeline.end must fall after timeline.start")
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


# ----------------------------------------------------------------- svg ----

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


def render(spec: dict, style: Style) -> str:
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

    # dead ends — a buffer-stop bar where an out-of-service line runs out, unless
    # the network still reaches that stop through a line that is in service
    live = {sid for ln in m.lines if line_status(ln) == "live" for sid in ln["stations"]}
    dead_ends = []
    for i, line in enumerate(m.lines):
        ids = line["stations"]
        if line_status(line) != "out-of-service" or len(ids) < 2:
            continue
        for sid in sorted({ids[0], ids[-1]} - live):
            seg = m.stop_seg.get((i, sid))
            if not seg:
                continue
            here = m.pos[sid]
            # away from the body of the line, along the track it arrives on
            away = (sub(seg["p"], seg["q"]) if dist(seg["p"], here) < dist(seg["q"], here)
                    else sub(seg["q"], seg["p"]))
            d = norm(away)
            centre = add(add(here, seg["shift"]),
                         scale(d, m.marker_radius(sid) + s.stroke * 0.55))
            arm = scale(perp(d), s.stroke * s.buffer_len)
            a, b = sub(centre, arm), add(centre, arm)
            dead_ends.append(
                f'    <line class="buffer" x1="{a[0]:.1f}" y1="{a[1]:.1f}" '
                f'x2="{b[0]:.1f}" y2="{b[1]:.1f}"><title>{esc(line["name"])} '
                f'ends here</title></line>'
            )
            grow(min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))

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

    x0, y0, x1, y1 = cx0 - s.margin, cy0 - s.margin, cx1 + s.margin, cy1 + s.margin

    dark = "\n".join(
        [f"      .l{i} {{ stroke: {lighten(ln['color'])}; }}" for i, ln in enumerate(m.lines)]
        + [f"      .z{i} {{ --zc: {lighten(zn['color'])}; }}"
           for i, zn in enumerate(spec.get("zones", []) or [])]
    )

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
    svg {{ --paper: #ffffff; --ink: #101820; background: var(--paper); color: var(--ink); }}
    .route {{ fill: none; stroke-width: {s.stroke}; stroke-linecap: round; stroke-linejoin: round; }}
    .status-out {{ stroke-dasharray: {s.stroke * 1.8:.1f} {s.stroke * 1.2:.1f};
                  stroke-linecap: butt; opacity: {s.status_fade}; }}
    .status-build {{ stroke-dasharray: {s.stroke * 3.0:.1f} {s.stroke * 1.1:.1f};
                    stroke-linecap: butt; opacity: .85; }}
    .status-plan {{ stroke-dasharray: {s.stroke * 0.1:.1f} {s.stroke * 1.35:.1f}; opacity: .62; }}
    .buffer {{ stroke: var(--ink); stroke-width: {max(3.0, s.stroke * 0.5):.1f};
              stroke-linecap: butt; opacity: .7; }}
    .muted {{ opacity: {s.status_fade + 0.15:.2f}; }}
    .zone-band {{ fill: var(--zc); fill-opacity: {s.zone_fill}; stroke: var(--zc);
                 stroke-width: 2; stroke-dasharray: 7 6; opacity: .9; }}
    .zone-label {{ font-family: {s.font}; font-size: {s.zone_label_size}px; font-weight: 700;
                  letter-spacing: .09em; text-transform: uppercase; fill: var(--zc); }}
{timeline_css}    .stop {{ fill: var(--paper); stroke-width: {s.stop_ring}; }}
    .interchange {{ fill: var(--paper); stroke: var(--ink); stroke-width: {s.stop_ring}; }}
    .label {{ font-family: {s.font}; font-size: {s.label_size}px; font-weight: 600; fill: var(--ink); }}
    @media (prefers-color-scheme: dark) {{
      svg {{ --paper: #131b21; --ink: #e8eef3; }}
{dark}
    }}
  </style>
{timeline_group}{zone_group}  <g id="routes">
{chr(10).join(routes)}
  </g>
{dead_group}  <g id="stations">
{chr(10).join(stops)}
  </g>
  <g id="labels">
{chr(10).join(labels)}
  </g>
</svg>
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
                      "tl_row_h", "tl_label_size", "tl_major_size"):
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
                    "Design one in the browser with: python3 app.py")
    ap.add_argument("spec", nargs="?", help="JSON spec file, or - for stdin")
    ap.add_argument("-o", "--out", default="-", help="output SVG file (default: stdout)")
    ap.add_argument("--cell", type=float, help="pixels per grid cell")
    ap.add_argument("--stroke", type=float, help="route width")
    ap.add_argument("--corner", type=float, help="corner radius")
    ap.add_argument("--bundle-gap", type=float, help="spacing between parallel tracks")
    ap.add_argument("--label-size", type=float)
    ap.add_argument("--auto-interchange", action="store_true",
                    help="flag every stop shared by two or more lines as an interchange")
    args = ap.parse_args(argv)

    if not args.spec:
        ap.print_help()
        print("\n  no spec given — start the web designer with:  python3 app.py",
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
