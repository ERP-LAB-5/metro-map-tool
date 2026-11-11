# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ERP-LAB-5
"""
metrofy — a metro map made from code, for display (ADR 0015).

Where the designer is for drawing a map by hand, Metrofy is for a map that a
program or an agent writes from something else: a process and the work moving
through it. Its input names things, never positions them:

    zones   the lanes a step sits in (which system, which team)
    steps   the stations, in process order, each in one zone
    lines   the work riding the process (stories, streams), with a status:
            planned, building, live, dropped
    trips   a traveller riding a route across the map: a scenario played
            out, or the walk-through that accepts a piece of work

and Metrofy decides the rest: grid positions (steps in order, one swimlane per
zone), the process line through every step, line order, colours. `finalize`
checks the input, places it, renders it with metro_map.render, wraps it into
one standalone page with a details panel and the trips to play, and checks
what came out. A map that would be drawn with a trip missing or a step
outside its lane is refused, not shipped half drawn.

    metrofy finalize input.json map.html     # receipt on stdout, exit 1 on failure
    metrofy check input.json                 # the input's problems only
    metrofy schema                           # the input's JSON Schema

Standard library only.
"""

from .build import FORMAT, check, finalize, page, place  # noqa: F401
