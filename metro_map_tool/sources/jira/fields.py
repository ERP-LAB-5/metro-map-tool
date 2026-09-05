#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
fields.py — find out what this Jira calls things, instead of being told.

Every Jira instance puts Sprint, Start date and Epic Link on custom fields with
different numbers, so a tool that asks for "customfield_10014" is asking the
user to go and look it up — and the way you look it up is to call the endpoint
below, which the tool could simply have called itself.

So it does. /rest/api/3/field lists every field with its id and its name; the
names are stable across instances even though the ids are not.
"""

from __future__ import annotations

from typing import Dict, List, Optional

# What each thing is called on the screen, best guess first. Matched
# case-insensitively against the field's name, then its clauseNames.
WANTED: Dict[str, tuple] = {
    "sprint": ("sprint",),
    "start_date": ("start date", "target start", "planned start"),
    "epic_link": ("epic link", "parent link"),
    "epic_name": ("epic name",),
    "story_points": ("story points", "story point estimate"),
}


def discover(client) -> Dict[str, str]:
    """This instance's ids for the fields the roadmap cares about.

    Only custom fields are worth reporting: `duedate` and `parent` are the same
    everywhere and are read by name already.
    """
    found: Dict[str, str] = {}
    catalogue = client.fields()
    by_name: Dict[str, str] = {}
    for field in catalogue:
        fid = field.get("id") or ""
        if not fid.startswith("customfield_"):
            continue
        names = [field.get("name") or ""] + list(field.get("clauseNames") or [])
        for name in names:
            key = name.strip().lower()
            if key and key not in by_name:
                by_name[key] = fid
    for what, aliases in WANTED.items():
        for alias in aliases:
            if alias in by_name:
                found[what] = by_name[alias]
                break
    return found


def resolve(client, opts: dict, saved: Optional[Dict[str, str]] = None
            ) -> Dict[str, str]:
    """The field ids to use, with an explicit option always winning.

    Order: what you passed, then what was saved from a previous run, then what
    this instance says. Asking Jira is the fallback rather than the first move
    because it costs a round trip, and because someone who has typed an id
    means it.
    """
    out: Dict[str, str] = dict(saved or {})
    asked = {"sprint": opts.get("sprint_field"),
             "start_date": opts.get("start_field"),
             "epic_link": opts.get("epic_field")}
    if any(v for v in asked.values()):
        out.update({k: v for k, v in asked.items() if v})
    if not all(out.get(k) for k in ("sprint", "start_date", "epic_link")):
        out.update({k: v for k, v in discover(client).items()
                    if not out.get(k)})
    out.update({k: v for k, v in asked.items() if v})   # an explicit one wins
    return out


def summary(found: Dict[str, str]) -> List[str]:
    """What was worked out, as lines worth printing — a wrong guess should be
    visible rather than mysterious."""
    if not found:
        return []
    return [f"jira field '{what}' resolved to {fid}"
            for what, fid in sorted(found.items())]
