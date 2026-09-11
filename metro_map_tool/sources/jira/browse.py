#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
browse.py — look at what is in Jira before importing any of it.

It starts from an issue key, not from the list of projects. Walking down from a
project meant a list of hundreds, then two different spines, then a filter box
that could only ever narrow the column it happened to be in; somebody who wants
one initiative on a roadmap already knows its key, and is better served typing
it than hunting for it.

So the answer to a key is the issue and its whole subtree in one go, each row
saying where it hangs and what it can be filtered by. Filtering is then the
browser's business, on data it already has — see tree.py for why.
"""

from __future__ import annotations

from typing import List, Optional

from .. import Node, SourceError, View
from . import client as jira_client
from . import tree

VIEWS = (
    View("tree", "From an issue",
         "An initiative, epic or story, and everything beneath it"),
)

FIELDS = "summary,status,duedate,issuetype,parent,labels"
# A preview is for looking, so it may go further than an import's default limit
# — but not unboundedly: a key near the top of a big instance is thousands.
PREVIEW = 1000


def _node(issue: dict, depth: int, parent: str, has_kids: bool) -> Node:
    fields = issue.get("fields") or {}
    said = tree.facets(issue)
    due = (fields.get("duedate") or "")[:10]
    hint = " · ".join(x for x in (said["type"], said["status"],
                                  f"due {due}" if due else "") if x)
    return Node(id=issue.get("key", ""),
                label=fields.get("summary") or issue.get("key", ""),
                kind="root" if depth == 0 else ("epic" if has_kids else "issue"),
                hint=hint, expandable=has_kids, parent=parent, depth=depth,
                facets=said)


def browse(path: List[str], opts: dict, view: str, query: str = "",
           client=None, creds: Optional[dict] = None) -> List[Node]:
    """An issue and its subtree, root first. `path` is the key to start from.

    `view` and `query` are accepted for the Source interface and not used:
    there is one spine, and filtering happens on the tree once it is loaded.
    """
    if not path:
        raise SourceError("start from a Jira issue key, such as ABCD-123")
    if client is None:
        client = jira_client.connect(creds or {})
    cap = max(int(opts.get("limit") or 0), PREVIEW)
    root, issues, truncated = tree.walk(client, path[-1], FIELDS, cap)

    parents = {i.get("parent") for i in issues}
    top = _node(root, 0, "", bool(issues))
    if truncated:
        top.facets["truncated"] = True      # the node is frozen; its dict is not
    return [top] + [_node(i, i["depth"], i["parent"], i.get("key") in parents)
                    for i in issues]
