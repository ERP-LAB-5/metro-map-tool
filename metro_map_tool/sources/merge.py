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
creates — "jira:ACME-231", "github:issue/1487", "git:9f3a1c2". On a match the
*author's* id is kept, which is load-bearing: every line route, zone membership,
interchange group and scenario naming that id keeps resolving, for free.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

# Keys the import decides; everything else on a matched item is the author's.
OWNED_TOP = ("stations", "lines", "format")


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
            # fill what is missing, never replace what is there: a label the
            # author rewrote is the map's own words and outranks upstream's
            for key, value in incoming.items():
                existing.setdefault(key, value)
            existing["origin"] = origin
            said = incoming.get("label")
            if said and existing.get("label") != said and "label" not in refresh:
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

    spec: dict = {k: v for k, v in model.items() if k not in OWNED_TOP}
    spec["stations"] = kept
    spec["lines"] = _fold_lines(model.get("lines") or [], fresh.get("lines") or [],
                                rename, gone, mine, prune, notes)

    for key in ("zones", "interchanges"):
        folded = _fold_groups(spec.get(key) or [], fresh.get(key) or [],
                              rename, gone, mine, prune, key, notes)
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
        if key not in OWNED_TOP and key not in ("zones", "interchanges", "timeline"):
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


def _fold_lines(m_lines: List[dict], f_lines: List[dict], rename, gone, mine,
                prune, notes) -> List[dict]:
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
        line = {k: v for k, v in ln.items() if k not in ("stations", "notes")}
        for key, value in fresh_match.items():
            if key not in ("stations", "notes"):
                line.setdefault(key, value)
        before = ln.get("stations") or []
        line["stations"] = _repoint(fresh_match.get("stations") or [], rename, gone)
        if ln.get("notes") and before != line["stations"]:
            # a note is addressed by hop index, so once the hops move it would
            # come to mean "between two stops that happen to sit there now"
            notes.append(f"line '{line.get('name')}': {len(ln['notes'])} track "
                         "note(s) dropped — the hops they numbered have moved")
        elif ln.get("notes"):
            line["notes"] = ln["notes"]
        out.append(line)

    for fl in f_lines:
        if id(fl) not in used:
            new = dict(fl)
            new["stations"] = _repoint(new.get("stations") or [], rename, gone)
            out.append(new)
    return out


def _fold_groups(m_items: List[dict], f_items: List[dict], rename, gone, mine,
                 prune, what, notes) -> List[dict]:
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
