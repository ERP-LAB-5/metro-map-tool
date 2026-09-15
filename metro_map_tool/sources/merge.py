#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
merge.py — fold a freshly imported network into whatever the author drew.

One rule underlies everything here:

    the import owns what exists upstream and what it is called there;
    the author owns where it sits, what colour it is, and what the map calls it.

Which is to say: importing twice must not undo an afternoon's arranging. That
is the whole reason a map is worth keeping rather than regenerating, so when
this module is unsure it keeps what the human did and says something, rather
than taking the upstream answer quietly.

Items are matched by "origin", a flat string each source stamps on what it
creates — "jira:ABCD-231", "github:issue/1487", "git:9f3a1c2". On a match the
*author's* id is kept, which is load-bearing: every line route, zone membership,
interchange group and scenario naming that id keeps resolving, for free.

A source that also stamps an `upstream` snapshot — what it decided for a label,
a date, a position — gets a three-way merge instead of "keep what is there".
The snapshot from last time is the base, the map is mine, the new import is
theirs: a field upstream changed and nobody touched on the map follows
upstream; a field changed on both sides keeps the map's value and says so; a
field only the map changed stays as it is. That is how a date moved in Jira
moves the stop, while a stop somebody dragged stays where they put it.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

# Keys the import decides; everything else on a matched item is the author's.
OWNED_TOP = ("stations", "junctions", "lines", "format")
# Where a position lives; changes to these are the stop following its date, and
# not worth a sentence of their own unless they conflict.
QUIET = ("gx", "gy")


def three_way(existing: dict, incoming: dict, notes: List[str], who: str,
              source: str) -> bool:
    """Fold `incoming`'s upstream snapshot into `existing`, in place.

    Returns False when `incoming` carries no snapshot, so the caller falls back
    to its older rule. An `existing` with no snapshot of its own was made before
    snapshots were: its values are kept, and the new snapshot becomes the base
    for next time.
    """
    theirs = incoming.get("upstream")
    if not isinstance(theirs, dict):
        return False
    base = existing.get("upstream")
    base = base if isinstance(base, dict) else None
    conflicts = []
    for field, value in theirs.items():
        mine = existing.get(field)
        if base is None or field not in base:
            continue
        was = base[field]
        if value == was or mine == value:
            continue
        if mine == was:
            if value is None:
                existing.pop(field, None)
            else:
                existing[field] = value
            if field not in QUIET:
                notes.append(f"{who}: {field} is now {_said(value)} in {source} "
                             f"(was {_said(was)}) — updated")
        else:
            conflicts.append((field, mine, value))
    if any(field in QUIET for field, _, _ in conflicts):
        notes.append(f"{who}: moved on the map, so it stays where it is although "
                     f"{source} has moved it")
    for field, mine, value in conflicts:
        if field not in QUIET:
            notes.append(f"{who}: {field} changed both on the map and in {source} — "
                         f"the map keeps {_said(mine)}, {source} says {_said(value)}")
    existing["upstream"] = dict(theirs)
    return True


def _said(value) -> str:
    return "nothing" if value in (None, "") else f"\"{value}\""


def merge(model: Optional[dict], fresh: dict, *, source: str,
          options: Optional[dict] = None,
          refresh: Optional[List[str]] = None,
          prune: bool = False,
          stamp: Optional[dict] = None) -> Tuple[dict, List[str]]:
    """The imported network, folded into the model. Returns (spec, notes)."""
    notes: List[str] = []
    refresh = list(refresh or [])
    if not model:
        spec = dict(fresh)
        if stamp:
            spec["source"] = stamp
        return spec, notes

    mine = f"{source}:"
    m_stations: dict = dict(model.get("stations") or {})
    f_stations: dict = dict(fresh.get("stations") or {})

    # A model is one of two quite different things, and they want opposite
    # treatment. A *sketch* — lanes and placeholder stops drawn by hand to say
    # what shape the map should be — is a template: the import replaces its
    # stops with the real ones and keeps only the names, colours and order.
    # A map this source imported *before* is a re-sync: its stops are the real
    # ones, arranged by a human, and must survive. The stamp left by the last
    # import is what tells them apart.
    stamped = model.get("source")
    resync = isinstance(stamped, dict) and stamped.get("name") == source

    # --- which model station is which imported one -------------------------
    by_origin: Dict[str, str] = {}
    if resync:
        for sid, st in m_stations.items():
            origin = isinstance(st, dict) and st.get("origin")
            if not origin:
                continue
            if origin in by_origin:
                notes.append(f"two stops both claim to be {origin} "
                             f"('{by_origin[origin]}' and '{sid}') — keeping the first")
                continue
            by_origin[origin] = sid

    rename: Dict[str, str] = {}          # imported id -> the id actually used
    kept: dict = dict(m_stations) if resync else {}
    seen_origins = set()

    for fid, incoming in f_stations.items():
        origin = incoming.get("origin")
        seen_origins.add(origin)
        sid = by_origin.get(origin)
        if sid is None:
            sid = fid if fid not in kept else _free(fid, kept)
            if sid != fid:
                notes.append(f"'{fid}' was taken, so {origin} came in as '{sid}'")
            kept[sid] = dict(incoming)
        else:
            existing = dict(kept[sid])
            synced = three_way(existing, incoming, notes, origin, source)
            # fill what is missing, never replace what is there: a label the
            # author rewrote is the map's own words and outranks upstream's
            for key, value in incoming.items():
                existing.setdefault(key, value)
            existing["origin"] = origin
            said = incoming.get("label")
            if not synced and said and existing.get("label") != said \
                    and "label" not in refresh:
                notes.append(f"{origin} is now \"{said}\" upstream; the map still "
                             f"says \"{existing.get('label')}\"")
            for key in refresh:
                if key in incoming:
                    existing[key] = incoming[key]
            kept[sid] = existing
        rename[fid] = sid

    # --- model stations this import no longer returns -----------------------
    for sid, st in list(m_stations.items()) if resync else []:
        origin = isinstance(st, dict) and st.get("origin") or ""
        if not origin.startswith(mine) or origin in seen_origins:
            continue                     # hand-drawn, or another source's, or still there
        if prune:
            kept.pop(sid, None)
            notes.append(f"removed '{sid}' ({origin}) — no longer upstream")
        else:
            notes.append(f"'{sid}' ({origin}) is no longer upstream, and was kept "
                         "— pass prune=true to remove it")

    gone = {sid for sid in m_stations if sid not in kept}

    junctions = _fold_junctions(model.get("junctions") or {},
                                fresh.get("junctions") or {}, kept, rename, gone,
                                mine, resync, source, notes)

    spec: dict = {k: v for k, v in model.items() if k not in OWNED_TOP}
    spec["stations"] = kept
    if junctions:
        spec["junctions"] = junctions
    spec["lines"] = _fold_lines(model.get("lines") or [], fresh.get("lines") or [],
                                rename, gone, mine, prune, notes, source)

    lanes = _fold_lanes(model.get("swimlanes") or [], fresh.get("swimlanes") or [],
                        mine, prune, notes, source)
    if lanes:
        spec["swimlanes"] = lanes
    else:
        spec.pop("swimlanes", None)

    for key in ("zones", "interchanges"):
        folded = _fold_groups(spec.get(key) or [], fresh.get(key) or [],
                              rename, gone, mine, prune, key, notes, source)
        if folded:
            spec[key] = folded
        else:
            spec.pop(key, None)

    if spec.get("scenarios"):
        spec["scenarios"] = [dict(sc, stations=_repoint(sc.get("stations") or [],
                                                        rename, gone))
                             for sc in spec["scenarios"]]

    # the import's own suggestions apply only where the author said nothing
    for key, value in fresh.items():
        if key not in OWNED_TOP and key not in ("zones", "interchanges", "timeline",
                                                 "swimlanes"):
            spec.setdefault(key, value)
    _widen(spec, fresh, notes)

    if stamp:
        spec["source"] = stamp
    return spec, notes


def _free(want: str, taken: dict) -> str:
    n = 2
    while f"{want}-{n}" in taken:
        n += 1
    return f"{want}-{n}"


def _repoint(ids: List[str], rename: Dict[str, str], gone: set) -> List[str]:
    """An id list rewritten onto the ids that survived the merge."""
    out = []
    for sid in ids:
        sid = rename.get(sid, sid)
        if sid not in gone:
            out.append(sid)
    return out


def _fold_junctions(m_junctions: dict, f_junctions: dict, stations: dict,
                    rename: Dict[str, str], gone: set, mine: str, resync: bool,
                    source: str, notes: List[str]) -> dict:
    """Junctions: the bends a source put in the track, and the ones the author did.

    A junction a source made is geometry, not work: when the import stops making
    it, it goes, with no prune to ask for — a fork with no branch leaving it is
    only a kink. The author's own junctions are theirs and stay. Where both sides
    have one, the three-way rule decides its position.
    """
    out: dict = {}
    by_origin: Dict[str, str] = {}
    for jid, jn in m_junctions.items():
        origin = isinstance(jn, dict) and jn.get("origin") or ""
        if origin.startswith(mine):
            if resync:
                by_origin.setdefault(origin, jid)
            else:
                gone.add(jid)
            continue
        out[jid] = jn                       # hand-drawn: the author's
    used = set()
    for fid, incoming in f_junctions.items():
        origin = incoming.get("origin") or ""
        jid = by_origin.get(origin)
        if jid is not None:
            existing = dict(m_junctions[jid])
            three_way(existing, incoming, notes, origin, source)
            for key, value in incoming.items():
                existing.setdefault(key, value)
            used.add(jid)
        else:
            jid = fid
            if jid in out or jid in stations:
                jid = _free(fid, {**out, **stations})
            existing = dict(incoming)
        out[jid] = existing
        rename[fid] = jid
    for origin, jid in by_origin.items():
        if jid not in used:
            gone.add(jid)
    return out


def _fold_lines(m_lines: List[dict], f_lines: List[dict], rename, gone, mine,
                prune, notes, source: str = "") -> List[dict]:
    """Model lines first, in the author's order, then anything new."""
    by_origin = {ln.get("origin"): i for i, ln in enumerate(m_lines) if ln.get("origin")}
    by_name = {ln.get("name"): i for i, ln in enumerate(m_lines) if ln.get("name")}
    used: set = set()
    out: List[dict] = []

    for ln in m_lines:
        fresh_match = None
        for fl in f_lines:
            hit = (fl.get("origin") and fl["origin"] == ln.get("origin")) or \
                  (fl.get("name") and fl["name"] == ln.get("name")
                   and ln.get("origin") is None)
            if hit and id(fl) not in used:
                fresh_match = fl
                used.add(id(fl))
                break
        if fresh_match is None:
            held = dict(ln)
            had = ln.get("stations") or []
            held["stations"] = _repoint(had, rename, gone)
            # a lane the import did not fill and whose every stop went with the
            # sketch is not a line any more — it is the shape it was drawn in
            if had and not held["stations"]:
                notes.append(f"line '{held.get('name')}' has no stops left — "
                             "not drawn")
                continue
            out.append(held)
            continue
        line = {k: v for k, v in ln.items()
                if k not in ("stations", "notes", "branches")}
        three_way(line, fresh_match, notes,
                  fresh_match.get("origin") or f"line '{ln.get('name')}'", source)
        for key, value in fresh_match.items():
            if key not in ("stations", "notes", "branches"):
                line.setdefault(key, value)
        before = ln.get("stations") or []
        line["stations"] = _repoint(fresh_match.get("stations") or [], rename, gone)
        # notes the import made are the import's to replace; the author's own
        # keep the old rule
        own = [n for n in ln.get("notes") or [] if not n.get("origin")]
        theirs = [dict(n) for n in fresh_match.get("notes") or []]
        by_note = {n.get("origin"): n for n in ln.get("notes") or [] if n.get("origin")}
        for note in theirs:
            had = by_note.get(note.get("origin"))
            if had:
                kept_note = dict(had)
                three_way(kept_note, note, notes, note["origin"], source)
                note.update(text=kept_note.get("text", note.get("text")),
                            upstream=kept_note.get("upstream"))
                if "flip" in had:
                    note["flip"] = had["flip"]
        if own and before != line["stations"]:
            # a note is addressed by hop index, so once the hops move it would
            # come to mean "between two stops that happen to sit there now"
            notes.append(f"line '{line.get('name')}': {len(own)} track "
                         "note(s) dropped — the hops they numbered have moved")
            own = []
        if own or theirs:
            line["notes"] = own + theirs
        branches = _fold_branches(ln.get("branches") or [],
                                  fresh_match.get("branches") or [],
                                  rename, gone, notes, source)
        if branches:
            line["branches"] = branches
        out.append(line)

    for fl in f_lines:
        if id(fl) not in used:
            new = dict(fl)
            new["stations"] = _repoint(new.get("stations") or [], rename, gone)
            if new.get("branches"):
                new["branches"] = [dict(br, stations=_repoint(br.get("stations") or [],
                                                              rename, gone))
                                   for br in new["branches"]]
            out.append(new)
    return out


def _fold_branches(m_branches: List[dict], f_branches: List[dict], rename, gone,
                   notes: List[str], source: str) -> List[dict]:
    """A line's branches: the import's are re-routed, the author's are kept.

    Matched by origin. The route of an imported branch is the import's — it is
    what is beneath the issue now — while its name follows the three-way rule.
    """
    by_origin = {br.get("origin"): br for br in m_branches
                 if isinstance(br, dict) and br.get("origin")}
    out: List[dict] = []
    for fb in f_branches:
        had = by_origin.get(fb.get("origin"))
        branch = dict(had) if had else {}
        if had:
            three_way(branch, fb, notes, fb.get("origin") or "a branch", source)
        for key, value in fb.items():
            if key != "stations":
                branch.setdefault(key, value)
        branch["stations"] = _repoint(fb.get("stations") or [], rename, gone)
        out.append(branch)
    for br in m_branches:
        if isinstance(br, dict) and not br.get("origin"):
            out.append(dict(br, stations=_repoint(br.get("stations") or [],
                                                  rename, gone)))
    return out


def _fold_groups(m_items: List[dict], f_items: List[dict], rename, gone, mine,
                 prune, what, notes, source: str = "") -> List[dict]:
    """Zones and interchanges: same identity rules, one implementation."""
    used: set = set()
    out: List[dict] = []
    for item in m_items:
        match = None
        for fi in f_items:
            if fi.get("origin") and fi["origin"] == item.get("origin") \
                    and id(fi) not in used:
                match = fi
                used.add(id(fi))
                break
        merged = dict(item)
        if match:
            three_way(merged, match, notes, match.get("origin") or what[:-1], source)
            for key, value in match.items():
                if key != "stations":
                    merged.setdefault(key, value)
            merged["stations"] = _repoint(match.get("stations") or [], rename, gone)
        else:
            merged["stations"] = _repoint(merged.get("stations") or [], rename, gone)
            origin = merged.get("origin") or ""
            if origin.startswith(mine) and prune:
                notes.append(f"removed {what[:-1]} '{merged.get('name') or origin}' "
                             "— no longer upstream")
                continue
        if merged.get("stations"):
            out.append(merged)
    for fi in f_items:
        if id(fi) not in used:
            new = dict(fi)
            new["stations"] = _repoint(new.get("stations") or [], rename, gone)
            if new.get("stations"):
                out.append(new)
    return out


def _fold_lanes(m_lanes: List[dict], f_lanes: List[dict], mine: str, prune: bool,
                notes: List[str], source: str) -> List[dict]:
    """Swimlanes: the author's in their order, then any new ones the import made.

    Matched by origin. A lane the import made follows the three-way rule on
    everything in its snapshot — name, rows, colour — so a lane the author
    renamed or widened keeps that, and one nobody touched follows the import.
    """
    by_origin = {fl.get("origin"): fl for fl in f_lanes if isinstance(fl, dict)
                 and fl.get("origin")}
    used = set()
    out: List[dict] = []
    for lane in m_lanes:
        if not isinstance(lane, dict):
            continue
        origin = lane.get("origin") or ""
        match = by_origin.get(origin) if origin else None
        if match is not None:
            merged = dict(lane)
            three_way(merged, match, notes, origin, source)
            for key, value in match.items():
                merged.setdefault(key, value)
            used.add(origin)
            out.append(merged)
        elif origin.startswith(mine) and prune:
            notes.append(f"removed swimlane '{lane.get('name') or origin}' — no longer "
                         "upstream")
        else:
            out.append(dict(lane))
    out.extend(dict(fl) for fl in f_lanes
               if isinstance(fl, dict) and fl.get("origin") not in used)
    return out


def _widen(spec: dict, fresh: dict, notes: List[str]) -> None:
    """A ruler grows to hold what arrived; it never shrinks.

    Resetting a hand-tuned timeline is annoying. Leaving imported stations off
    the end of it is broken, so of the two, growing is the safe direction.
    """
    want = fresh.get("timeline")
    if not isinstance(want, dict):
        return
    have = spec.get("timeline")
    if not isinstance(have, dict):
        spec["timeline"] = dict(want)
        return
    grown = dict(have)
    if want.get("start") and (not have.get("start") or want["start"] < have["start"]):
        grown["start"] = want["start"]
    if want.get("end") and (not have.get("end") or want["end"] > have["end"]):
        grown["end"] = want["end"]
    if grown != have:
        notes.append(f"the timeline was widened to {grown.get('start')} … "
                     f"{grown.get('end')} to hold what came in")
    spec["timeline"] = grown
