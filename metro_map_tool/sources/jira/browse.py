#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
browse.py — look at what is in Jira before importing any of it.

Importing used to need three things you could only get from somewhere else: the
project key, a JQL query, and the number of the custom field holding sprints.
This is the answer to all three — start at the project and walk down.

Two spines over the same issues, because a roadmap wants both: the hierarchy
gives the lines, and the boards give the sprint bands. Each level is fetched
only when it is opened, since a project with four thousand issues must not be
downloaded to show three rows.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .. import Node, View
from . import client as jira_client

VIEWS = (
    View("hierarchy", "By hierarchy",
         "Project, then the epics inside it, then their issues"),
    View("boards", "By board",
         "Project, then its boards and sprints — where the dates come from"),
)

# The type sitting above Epic on instances that have one. Named rather than
# assumed: most Jiras have no such level, and the browser skips it silently
# rather than showing an empty column.
ABOVE_EPIC = ("epic set", "initiative", "theme")

FIELDS = "summary,status,duedate,issuetype,parent,labels"


def _issue_node(issue: dict, kind: str, expandable: bool = False) -> Node:
    fields = issue.get("fields") or {}
    status = ((fields.get("status") or {}).get("name") or "").strip()
    due = (fields.get("duedate") or "")[:10]
    hint = " · ".join(x for x in (status, f"due {due}" if due else "") if x)
    return Node(id=issue.get("key", ""), label=fields.get("summary") or
                issue.get("key", ""), kind=kind, hint=hint,
                expandable=expandable)


def _types(client, project: str) -> List[str]:
    """The issue type names this project uses, as the instance spells them."""
    try:
        found = client.get(f"/rest/api/3/project/{project}")
    except Exception:
        return []
    return [t.get("name") or "" for t in (found.get("issueTypes") or [])]


def browse(path: List[str], opts: dict, view: str, client=None,
           creds: Optional[dict] = None) -> List[Node]:
    """The children of a path. An empty path is the list of projects."""
    if client is None:
        client = jira_client.connect(creds or {})
    view = view or VIEWS[0].name

    if not path:
        return [Node(id=p.get("key", ""), label=p.get("name") or p.get("key", ""),
                     kind="project", hint=p.get("key", ""), expandable=True,
                     selectable=True)
                for p in client.projects()]

    if view == "boards":
        return _boards(client, path)
    return _hierarchy(client, path)


def _hierarchy(client, path: List[str]) -> List[Node]:
    project = path[0]
    if len(path) == 1:
        # Where the instance has a level above Epic, show that; otherwise go
        # straight to epics rather than making the reader click through a
        # column that only ever holds one thing.
        # match case-insensitively but ask using the instance's own spelling —
        # a JQL that renames someone's issue type is asking to be rejected
        names = _types(client, project)
        above = [n for n in names if n.lower() in ABOVE_EPIC]
        if above:
            found = client.search(
                f'project = "{project}" AND issuetype = "{above[0]}" '
                "ORDER BY created ASC", FIELDS, 100)
            if found:
                return [_issue_node(i, "epicset", True) for i in found]
        return _epics(client, project)

    parent = path[-1]
    if len(path) == 2:
        kids = client.search(f'parent = "{parent}" ORDER BY duedate ASC',
                             FIELDS, 200)
        # a child that is itself an epic keeps its own children
        return [_issue_node(i, _kind_of(i), _kind_of(i) == "epic") for i in kids]

    return [_issue_node(i, "issue")
            for i in client.search(f'parent = "{parent}" ORDER BY duedate ASC',
                                   FIELDS, 200)]


def _kind_of(issue: dict) -> str:
    name = (((issue.get("fields") or {}).get("issuetype") or {})
            .get("name") or "").lower()
    if name in ABOVE_EPIC:
        return "epicset"
    return "epic" if "epic" in name else "issue"


def _epics(client, project: str) -> List[Node]:
    found = client.search(
        f'project = "{project}" AND issuetype = Epic ORDER BY duedate ASC',
        FIELDS, 200)
    return [_issue_node(i, "epic", True) for i in found]


def _boards(client, path: List[str]) -> List[Node]:
    project = path[0]
    if len(path) == 1:
        boards = client.boards(project)
        if not boards:
            return []                   # no Jira Software here, not an error
        return [Node(id=f"board:{b.get('id')}", label=b.get("name") or "board",
                     kind="board", hint=b.get("type") or "", expandable=True)
                for b in boards]
    if len(path) == 2 and path[1].startswith("board:"):
        out = []
        for sprint in client.sprints(path[1].split(":", 1)[1]):
            span = " – ".join(x[:10] for x in
                              (sprint.get("startDate") or "",
                               sprint.get("endDate") or "") if x)
            out.append(Node(id=f"sprint:{sprint.get('id')}",
                            label=sprint.get("name") or "sprint", kind="sprint",
                            hint=" · ".join(x for x in
                                            (sprint.get("state") or "", span) if x),
                            expandable=True))
        return out
    if path[-1].startswith("sprint:"):
        sid = path[-1].split(":", 1)[1]
        try:
            page = client.get(f"/rest/agile/1.0/sprint/{sid}/issue",
                              {"maxResults": 200, "fields": FIELDS})
        except Exception:
            return []
        return [_issue_node(i, "issue") for i in (page.get("issues") or [])]
    return []
