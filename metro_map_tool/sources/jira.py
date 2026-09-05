#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
jira.py — a project's epics and issues as a roadmap.

An epic is a stream of work and an issue is a thing in it, which is a line and
a station without needing to be translated. Due dates give the axis, and a fix
version — the moment several epics have to land together — is a capsule across
them.

Two things vary between Jira instances and so are options rather than
assumptions: the epic link is a custom field whose id differs per site, and so
is the sprint field, which is why sprints are only drawn when you say which
field holds them.
"""

from __future__ import annotations

import json
import urllib.parse
from typing import Dict, List, Optional, Tuple

from . import EnvVar, Option, Source, SourceError
from . import http
from . import plan

FIELDS = ("summary", "duedate", "status", "parent", "fixVersions", "issuetype")
NO_EPIC = "Unassigned"


def _headers(email: str, token: str) -> Dict[str, str]:
    import base64
    pair = base64.b64encode(f"{email}:{token}".encode()).decode()
    return {"Authorization": f"Basic {pair}",     # header, never the query string
            "Accept": "application/json",
            "User-Agent": "metro-map-tool"}


def _fetch(opts: dict, model: Optional[dict]) -> dict:
    base = http.need("JIRA_BASE_URL",
                     "the site, like https://yourteam.atlassian.net").rstrip("/")
    email = http.need("JIRA_EMAIL", "the account the API token belongs to")
    token = http.need("JIRA_API_TOKEN",
                      "create one at id.atlassian.com/manage-profile/security/"
                      "api-tokens")
    head = _headers(email, token)
    project = str(opts["project"]).strip()
    limit = int(opts.get("limit") or 200)

    extra = [f for f in (opts.get("epic_field"), opts.get("sprint_field")) if f]
    jql = opts.get("jql") or f"project = {project} ORDER BY duedate ASC"
    fields = ",".join(FIELDS + tuple(extra))

    issues: List[dict] = []
    for start in range(0, limit, 100):
        query = urllib.parse.urlencode(
            {"jql": jql, "startAt": start, "maxResults": min(100, limit - start),
             "fields": fields})
        page = http.get_json(f"{base}/rest/api/3/search?{query}", head)
        batch = page.get("issues") or []
        issues.extend(batch)
        if len(batch) < 100:
            break

    versions = http.get_json(
        f"{base}/rest/api/3/project/{urllib.parse.quote(project)}/versions", head)
    return {"project": project, "issues": issues[:limit],
            "versions": versions if isinstance(versions, list) else []}


def _epic_of(issue: dict, epic_field: str) -> str:
    """Which epic an issue belongs to, however this instance records it."""
    fields = issue.get("fields") or {}
    parent = fields.get("parent") or {}
    if parent:
        pf = parent.get("fields") or {}
        return pf.get("summary") or parent.get("key") or NO_EPIC
    if epic_field and fields.get(epic_field):
        said = fields[epic_field]
        return said if isinstance(said, str) else NO_EPIC
    return NO_EPIC


def _state(issue: dict) -> str:
    cat = (((issue.get("fields") or {}).get("status") or {})
           .get("statusCategory") or {}).get("key")
    return {"done": "done", "indeterminate": "active"}.get(cat, "todo")


def _build(data: dict, opts: dict, model: Optional[dict]) -> Tuple[dict, List[str]]:
    project = data.get("project") or str(opts.get("project") or "")
    epic_field = str(opts.get("epic_field") or "")
    notes: List[str] = []

    items: List[dict] = []
    members: Dict[str, List[str]] = {}
    for issue in data.get("issues") or []:
        fields = issue.get("fields") or {}
        key = issue.get("key")
        if not key:
            continue
        sid = key.lower()
        state = _state(issue)
        items.append({
            "id": sid, "label": fields.get("summary") or key,
            "lane": _epic_of(issue, epic_field),
            "date": (fields.get("duedate") or "")[:10] or None,
            "origin": f"jira:{key}",
            "done": state == "done", "state": state,
        })
        for fv in fields.get("fixVersions") or []:
            if fv.get("id"):
                members.setdefault(fv["id"], []).append(sid)

    if not items:
        raise SourceError(f"no issues found in {project} to place")

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
                          "set one on the issues, or narrow the JQL to those "
                          "that have one") from None

    phases = _phases(data, opts)
    if phases:
        spec["phases"] = phases
    elif opts.get("sprint_field"):
        notes.append(f"no sprints found in '{opts['sprint_field']}' — check the "
                     "field id on your instance")
    return spec, notes + more


def _phases(data: dict, opts: dict) -> List[dict]:
    """Sprints as dated bands, when the instance's sprint field was named.

    Jira returns sprints as objects on a custom field whose id differs per site,
    so this stays off until someone says which field to read.
    """
    field = str(opts.get("sprint_field") or "")
    if not field:
        return []
    found: Dict[str, dict] = {}
    for issue in data.get("issues") or []:
        for sprint in (issue.get("fields") or {}).get(field) or []:
            if not isinstance(sprint, dict) or not sprint.get("name"):
                continue
            start = (sprint.get("startDate") or "")[:10]
            end = (sprint.get("endDate") or "")[:10]
            if start and end:
                found[sprint["name"]] = {"name": sprint["name"],
                                         "from": start, "to": end}
    return [found[k] for k in sorted(found)]


SOURCE = Source(
    name="jira",
    title="Jira epics and issues",
    summary="epics as lines, issues as stations, due dates as the axis, fix "
            "versions as milestones across them",
    options=(
        Option("project", "the project key", required=True, placeholder="PAY"),
        Option("jql", "override the search; the default takes the whole project "
                      "ordered by due date",
               placeholder="project = PAY AND duedate <= 2027-01-01"),
        Option("epic_field", "the custom field holding the epic link, for "
                             "instances that predate the parent field",
               placeholder="customfield_10014"),
        Option("sprint_field", "the custom field holding sprints; set it to draw "
                               "sprints as bands", placeholder="customfield_10020"),
        Option("interval", "column width; by default one is chosen to suit the span",
               kind="choice", choices=("", "day", "week", "month", "quarter", "year"),
               default=""),
        Option("done_mark", "put this in front of a finished issue's label",
               default="✓"),
    ),
    env=(EnvVar("JIRA_BASE_URL", "your site, like https://yourteam.atlassian.net"),
         EnvVar("JIRA_EMAIL", "the account the API token belongs to"),
         EnvVar("JIRA_API_TOKEN", "an API token from id.atlassian.com")),
    fetch=_fetch,
    build=_build,
)
