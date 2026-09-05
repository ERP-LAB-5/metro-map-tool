#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
jira — a project's epics and issues as a roadmap.

An epic is a stream of work and an issue is a thing in it, which is a line and
a station without needing to be translated. Due dates give the axis, and a fix
version — the moment several epics have to land together — is a capsule across
them.

The plugin owns everything it needs: its own settings (config.py), its own
lightweight REST client (client.py), its own way of finding out what this Jira
calls things (fields.py), and its own discovery tree (browse.py). It reaches
the rest of the tool only through the Source interface, so it can be lifted
into a package of its own later without anything else noticing.

It is read-only. client.request refuses anything but GET, and says so.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .. import EnvVar, Node, Option, Source, SourceError, credentials
from .. import plan
from . import browse as browse_mod
from . import client as jira_client
from . import config as jira_config
from . import fields as jira_fields

NO_EPIC = "Unassigned"
# Teams label a quarter in whatever dialect they settled on years ago — 25Q1,
# FY25Q1, H25Q1, Q1-25. The prefix carries no meaning worth reading, so any
# short one is allowed rather than picking one house style and ignoring the rest.
QUARTER = re.compile(r"^[A-Z]{0,4}(\d{2})Q([1-4])$", re.I)
BASE_FIELDS = ("summary", "duedate", "status", "parent", "fixVersions",
               "issuetype", "labels")


def quarter_dates(label: str) -> Tuple[Optional[str], Optional[str]]:
    """The span a label like 25Q1, FY25Q1 or H25Q1 stands for.

    A widespread habit and worth reading: an epic labelled for a quarter has
    been scheduled, even when nobody filled the date fields in, and dropping it
    for want of a duedate loses real plan.
    """
    hit = QUARTER.match(label.strip())
    if not hit:
        return None, None
    year = 2000 + int(hit.group(1))
    quarter = int(hit.group(2))
    first = 3 * (quarter - 1) + 1
    last = first + 2
    end_day = {1: 31, 3: 31, 4: 30, 5: 31, 6: 30, 7: 31, 8: 31, 9: 30,
               10: 31, 11: 30, 12: 31}.get(last, 30)
    if last == 2:
        end_day = 29 if year % 4 == 0 and (year % 100 or year % 400 == 0) else 28
    return f"{year}-{first:02d}-01", f"{year}-{last:02d}-{end_day:02d}"


# ------------------------------------------------------------------ fetch --

def _scope(opts: dict) -> str:
    """The JQL this import covers.

    A selection from the browser wins over the whole project: picking three
    epics should ask Jira for three epics, not for everything and then throw
    most of it away.
    """
    chosen = [c.strip() for c in (opts.get("select") or []) if c.strip()]
    keys = [c for c in chosen if not c.startswith(("board:", "sprint:"))]
    sprints = [c.split(":", 1)[1] for c in chosen if c.startswith("sprint:")]
    parts = []
    if keys:
        joined = ", ".join(f'"{k}"' for k in keys)
        parts.append(f"(key in ({joined}) OR parent in ({joined}))")
    if sprints:
        parts.append("sprint in (" + ", ".join(sprints) + ")")
    if parts:
        return " OR ".join(parts) + " ORDER BY duedate ASC"
    if opts.get("jql"):
        return str(opts["jql"])
    return f'project = "{opts["project"]}" ORDER BY duedate ASC'


def _fetch(opts: dict, model: Optional[dict]) -> dict:
    client = jira_client.connect(credentials(SOURCE))
    found = jira_fields.resolve(client, opts, jira_config.remembered_fields())
    jira_config.remember_fields(found)

    extra = [f for f in (found.get("sprint"), found.get("start_date"),
                         found.get("epic_link")) if f]
    fields = ",".join(BASE_FIELDS + tuple(dict.fromkeys(extra)))
    issues = client.search(_scope(opts), fields, int(opts.get("limit") or 200))

    versions: List[dict] = []
    if opts.get("project"):
        versions = client.versions(str(opts["project"]))

    return {"project": opts.get("project", ""), "site": client.site,
            "issues": issues, "versions": versions, "fields": found,
            "notes": jira_fields.summary(found)}


# ------------------------------------------------------------------ build --

def _epic_of(issue: dict, epic_field: str) -> str:
    fields = issue.get("fields") or {}
    parent = fields.get("parent") or {}
    if parent:
        return (parent.get("fields") or {}).get("summary") \
            or parent.get("key") or NO_EPIC
    if epic_field and isinstance(fields.get(epic_field), str):
        return fields[epic_field]
    return NO_EPIC


def _state(issue: dict) -> str:
    cat = (((issue.get("fields") or {}).get("status") or {})
           .get("statusCategory") or {}).get("key")
    return {"done": "done", "indeterminate": "active"}.get(cat, "todo")


def _due(fields: dict, use_quarters: bool) -> Optional[str]:
    said = (fields.get("duedate") or "")[:10]
    if said:
        return said
    if not use_quarters:
        return None
    for label in fields.get("labels") or []:
        _, end = quarter_dates(str(label))
        if end:
            return end
    return None


def _build(data: dict, opts: dict, model: Optional[dict]) -> Tuple[dict, List[str]]:
    project = data.get("project") or str(opts.get("project") or "")
    found = data.get("fields") or {}
    epic_field = str(opts.get("epic_field") or found.get("epic_link") or "")
    use_quarters = opts.get("quarter_labels", True)
    notes: List[str] = list(data.get("notes") or [])

    items: List[dict] = []
    members: Dict[str, List[str]] = {}
    from_label = 0
    for issue in data.get("issues") or []:
        fields = issue.get("fields") or {}
        key = issue.get("key")
        if not key:
            continue
        due = _due(fields, use_quarters)
        if due and not (fields.get("duedate") or ""):
            from_label += 1
        state = _state(issue)
        items.append({"id": key.lower(), "label": fields.get("summary") or key,
                      "lane": _epic_of(issue, epic_field), "date": due,
                      "origin": f"jira:{key}",
                      "done": state == "done", "state": state})
        for fv in fields.get("fixVersions") or []:
            if fv.get("id"):
                members.setdefault(fv["id"], []).append(key.lower())

    if from_label:
        notes.append(f"{from_label} issue(s) had no due date and were placed "
                     "from a quarter label instead")
    if not items:
        raise SourceError(f"no issues found in {project or 'that selection'} "
                          "to place")

    groups = [{"name": v.get("name") or v.get("id"),
               "date": (v.get("releaseDate") or "")[:10] or None,
               "origin": f"jira:version/{v.get('id')}",
               "members": members.get(v.get("id")) or []}
              for v in data.get("versions") or []]

    lane_order = [ln.get("name") for ln in (model or {}).get("lines") or []]
    try:
        spec, more = plan.build(items, groups, source="jira",
                                interval=str(opts.get("interval") or ""),
                                done_mark=str(opts.get("done_mark") or ""),
                                catch_all=NO_EPIC, lane_order=lane_order)
    except ValueError as exc:
        raise SourceError(f"{exc} — Jira dates come from the Due date field, so "
                          "set one on the issues, narrow the selection to those "
                          "that have one, or label them by quarter") from None

    phases = _phases(data, opts, found)
    if phases:
        spec["phases"] = phases
    return spec, notes + more


def _phases(data: dict, opts: dict, found: dict) -> List[dict]:
    """Sprints as dated bands, from whichever field this instance keeps them on."""
    field = str(opts.get("sprint_field") or found.get("sprint") or "")
    if not field:
        return []
    seen: Dict[str, dict] = {}
    for issue in data.get("issues") or []:
        for sprint in (issue.get("fields") or {}).get(field) or []:
            if not isinstance(sprint, dict) or not sprint.get("name"):
                continue
            start = (sprint.get("startDate") or "")[:10]
            end = (sprint.get("endDate") or "")[:10]
            if start and end:
                seen[sprint["name"]] = {"name": sprint["name"],
                                        "from": start, "to": end}
    return [seen[k] for k in sorted(seen)]


def _browse(path: List[str], opts: dict, view: str) -> List[Node]:
    return browse_mod.browse(path, opts, view, creds=credentials(SOURCE))


SOURCE = Source(
    name="jira",
    title="Jira epics and issues",
    summary="epics as lines, issues as stations, due dates as the axis, fix "
            "versions as milestones across them",
    options=(
        Option("project", "the project key — browse to find it",
               placeholder="ACME"),
        Option("jql", "override the search; the default takes the whole project "
                      "ordered by due date",
               placeholder="project = ACME AND duedate <= 2027-01-01"),
        Option("epic_field", "the custom field holding the epic link; found "
                             "automatically when left empty",
               placeholder="customfield_10014"),
        Option("sprint_field", "the custom field holding sprints; found "
                               "automatically when left empty",
               placeholder="customfield_10007"),
        Option("quarter_labels", "place an undated issue from a quarter label "
                                 "like 25Q1 or FY25Q1 rather than leaving it out",
               kind="bool", default=True),
        Option("interval", "column width; by default one is chosen to suit the span",
               kind="choice", choices=("", "day", "week", "month", "quarter", "year"),
               default=""),
        Option("done_mark", "put this in front of a finished issue's label",
               default="✓"),
    ),
    env=(EnvVar("JIRA_BASE_URL", "your site, like https://yourteam.atlassian.net",
                key="base_url", placeholder="https://yourteam.atlassian.net"),
         EnvVar("JIRA_EMAIL", "the account the API token belongs to",
                key="email", placeholder="you@example.com"),
         EnvVar("JIRA_API_TOKEN", "an API token from id.atlassian.com",
                key="api_token", secret=True)),
    fetch=_fetch,
    build=_build,
    views=browse_mod.VIEWS,
    browse=_browse,
)
