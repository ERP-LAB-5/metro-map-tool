#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
github.py — a repository's issues and milestones as a roadmap.

The mapping is the one GitHub already implies: a milestone is a date several
things are due on, a label is the stream of work a thing belongs to, and an
issue is a thing. So milestones give the axis, labels give the lines, issues
give the stations, and a milestone becomes the capsule where the lines meet.

Projects v2 is where a lot of planning now lives, but it is GraphQL-only and
needs its own token scope; milestones and labels work on any repository with
nothing set up, which is the right place to start.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from . import EnvVar, Option, Source, SourceError
from . import http
from . import plan

API = "https://api.github.com"
UNLABELLED = "Unlabelled"


def _headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "metro-map-tool"}


def _fetch(opts: dict, model: Optional[dict]) -> dict:
    repo = str(opts["repo"]).strip().strip("/")
    if repo.count("/") != 1:
        raise SourceError(f"repo should look like owner/name, not '{repo}'")
    token = http.need("GITHUB_TOKEN",
                      "create one at github.com/settings/tokens with read access "
                      "to the repository, then export GITHUB_TOKEN")
    head = _headers(token)
    limit = int(opts.get("limit") or 200)
    state = "all" if opts.get("include_closed", True) else "open"

    milestones = http.paged(
        lambda p: f"{API}/repos/{repo}/milestones?state=all&per_page=100&page={p}",
        head, 200)
    issues = http.paged(
        lambda p: f"{API}/repos/{repo}/issues?state={state}&per_page=100&page={p}",
        head, limit)
    return {"repo": repo, "milestones": milestones, "issues": issues}


def _lane(issue: dict, prefix: str) -> Optional[str]:
    for label in issue.get("labels") or []:
        name = label.get("name") if isinstance(label, dict) else str(label)
        if prefix and name and name.startswith(prefix):
            return name[len(prefix):].strip() or name
    return None


def _build(data: dict, opts: dict, model: Optional[dict]) -> Tuple[dict, List[str]]:
    repo = data.get("repo") or str(opts.get("repo") or "")
    prefix = str(opts.get("label_prefix") or "")
    skip_bare = bool(opts.get("skip_unlabelled"))
    notes: List[str] = []

    due: Dict[int, str] = {}
    for ms in data.get("milestones") or []:
        if ms.get("due_on"):
            due[ms["number"]] = ms["due_on"][:10]

    items: List[dict] = []
    members: Dict[int, List[str]] = {}
    skipped = 0
    for issue in data.get("issues") or []:
        if "pull_request" in issue:
            continue                       # the issues endpoint returns PRs as well
        lane = _lane(issue, prefix)
        if lane is None:
            if skip_bare:
                skipped += 1
                continue
            lane = UNLABELLED
        ms = issue.get("milestone") or {}
        number = issue.get("number")
        sid = f"gh-{number}"
        items.append({
            "id": sid, "label": issue.get("title") or f"#{number}", "lane": lane,
            "date": due.get(ms.get("number")) if ms else None,
            "origin": f"github:issue/{repo}#{number}",
            "done": issue.get("state") == "closed",
            "state": "done" if issue.get("state") == "closed" else "todo",
        })
        if ms.get("number") is not None:
            members.setdefault(ms["number"], []).append(sid)

    if skipped:
        notes.append(f"{skipped} issue(s) carried no '{prefix}' label and were "
                     "left out")
    if not items:
        raise SourceError(f"no issues found in {repo} to place")

    groups = [{"name": ms.get("title") or f"milestone {ms['number']}",
               "date": due.get(ms["number"]),
               "origin": f"github:milestone/{repo}/{ms['number']}",
               "members": members.get(ms["number"]) or []}
              for ms in data.get("milestones") or []]

    lane_order = [ln.get("name") for ln in (model or {}).get("lines") or []]
    try:
        spec, more = plan.build(items, groups, source="github",
                                interval=str(opts.get("interval") or ""),
                                done_mark=str(opts.get("done_mark") or ""),
                                catch_all=UNLABELLED, lane_order=lane_order)
    except ValueError as exc:
        raise SourceError(
            f"{exc} — GitHub dates come from milestone due dates, so give the "
            "milestones a due date, or the issues a milestone") from None
    return spec, notes + more


SOURCE = Source(
    name="github",
    title="GitHub issues and milestones",
    summary="labels as lines, issues as stations, milestone due dates as the axis",
    options=(
        Option("repo", "which repository, as owner/name", required=True,
               placeholder="ERP-LAB-5/metro-map-tool"),
        Option("label_prefix", "labels starting with this name the lines, with "
                               "the prefix stripped off", default="area:",
               placeholder="area:"),
        Option("skip_unlabelled", "leave out issues with no matching label, "
                                  "instead of gathering them into one line",
               kind="bool", default=False),
        Option("include_closed", "include closed issues", kind="bool", default=True),
        Option("interval", "column width; by default one is chosen to suit the span",
               kind="choice", choices=("", "day", "week", "month", "quarter", "year"),
               default=""),
        Option("done_mark", "put this in front of a closed issue's label",
               default="✓"),
    ),
    env=(EnvVar("GITHUB_TOKEN",
                "a token with read access to the repository"),),
    fetch=_fetch,
    build=_build,
)
