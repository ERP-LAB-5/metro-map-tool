#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
tree.py — one issue and everything beneath it, and which of that to keep.

An import starts from a key rather than from a project, because a project on a
corporate Jira is hundreds of epics nobody asked about, and the thing somebody
wants on a roadmap is usually one initiative and what hangs off it.

Walking and filtering are kept apart on purpose. The walk costs round trips and
happens once; the filter is free and happens every time a box is ticked. So the
browser loads the tree, filters it locally, and the import applies the very
same rules here — which is why `keep` is the one definition of what survives.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Set, Tuple

KEY = re.compile(r"^[A-Z][A-Z0-9_]+-\d+$", re.I)
# Keys per `parent in (...)`. Enough to keep a wide level to a few requests,
# few enough that the JQL stays well inside a URL.
CHUNK = 50


def walk(client, root_key: str, fields: str, limit: int
         ) -> Tuple[dict, List[dict], bool]:
    """The root issue, its descendants breadth first, and whether it stopped short.

    Each descendant carries `depth` (1 for the root's children) and `parent`
    (its parent's key) beside Jira's own fields, so the shape survives being
    written to a payload file and read back.
    """
    key = (root_key or "").strip().upper()
    if not KEY.match(key):
        from .. import SourceError
        raise SourceError(f"'{root_key}' is not a Jira issue key — it looks "
                          "like ACME-123")
    root = client.issue(key, fields)
    root_key = root.get("key") or key
    seen: Set[str] = {root_key}
    found: List[dict] = []
    level, depth = [root_key], 0
    truncated = False
    while level and not truncated:
        depth += 1
        below: List[str] = []
        for start in range(0, len(level), CHUNK):
            chunk = level[start:start + CHUNK]
            joined = ", ".join(f'"{k}"' for k in chunk)
            room = limit - len(found)
            if room <= 0:
                truncated = True
                break
            batch = client.search(f"parent in ({joined}) ORDER BY duedate ASC",
                                  fields, room + 1)
            for issue in batch:
                ikey = issue.get("key")
                if not ikey or ikey in seen:
                    continue                    # a cycle, or asked twice
                if len(found) >= limit:
                    truncated = True
                    break
                seen.add(ikey)
                parent = (((issue.get("fields") or {}).get("parent") or {})
                          .get("key")) or chunk[0]
                found.append(dict(issue, depth=depth, parent=parent))
                below.append(ikey)
            if truncated:
                break
        level = below
    return root, found, truncated


def facets(issue: dict) -> dict:
    """What the filter looks at, in the words the filter uses."""
    fields = issue.get("fields") or {}
    status = fields.get("status") or {}
    category = (status.get("statusCategory") or {}).get("key")
    return {"type": ((fields.get("issuetype") or {}).get("name") or "").strip(),
            "status": (status.get("name") or "").strip(),
            "open": category != "done",
            "due": (fields.get("duedate") or "")[:10],
            "labels": [str(x) for x in fields.get("labels") or []]}


def keep(issues: Iterable[dict], root_key: str,
         types: Optional[Iterable[str]] = None, open_only: bool = False,
         labels: Optional[Iterable[str]] = None) -> Set[str]:
    """The keys that survive a filter, with the structure they hang from.

    An issue survives when it passes every filter that is set: its type is one
    of `types`, it is open when `open_only` is on, it carries at least one of
    `labels`. Every ancestor of a survivor is kept too — a story without its
    epic has no line to sit on — and the root is always kept.
    """
    types_ = {t.lower() for t in types or [] if t}
    labels_ = {x.lower() for x in labels or [] if x}
    parents: Dict[str, str] = {}
    out: Set[str] = {root_key}
    for issue in issues:
        key = issue.get("key") or ""
        parents[key] = issue.get("parent") or ""
        said = facets(issue)
        if types_ and said["type"].lower() not in types_:
            continue
        if open_only and not said["open"]:
            continue
        if labels_ and not labels_ & {x.lower() for x in said["labels"]}:
            continue
        out.add(key)
    for key in list(out):
        hops = 0
        up = parents.get(key)
        while up and up not in out and hops < len(parents):
            out.add(up)
            up = parents.get(up)
            hops += 1
    return out


# ------------------------------------------------------------------ roles --
#
# What each issue becomes on the map. Chosen per level below a key, because a
# level is usually one kind of thing — epics, then stories — and overridable
# per issue, because "usually" is not "always".

ROLES = ("station", "junction", "zone", "note", "hide", "skip")
ROLE_HELP = {
    "station": "a stop at its date",
    "junction": "a branch of the line, carrying what is beneath it",
    "zone": "a band around the stops beneath it",
    "note": "a note on the track beside the stops beneath it",
    "hide": "not drawn — what is beneath it still comes",
    "skip": "not imported, and nothing beneath it either",
}


def levels_of(said: Iterable[str]) -> List[str]:
    """The `levels` option as roles, or a SourceError saying which one is wrong.

    An empty entry means "the default for that level", so `,station` leaves
    level 1 to be worked out and sets level 2.
    """
    out = []
    for n, role in enumerate(said or [], 1):
        role = str(role).strip().lower()
        if role and role not in ROLES:
            _refuse(f"level {n} is '{role}', which is not a role — use one of "
                    + ", ".join(ROLES))
        out.append(role)
    return out


def pairs_of(said: Iterable[str], what: str, check) -> Dict[str, str]:
    """`KEY=value` entries as a dict, each value passed through `check`."""
    out: Dict[str, str] = {}
    for entry in said or []:
        key, sep, value = str(entry).partition("=")
        key, value = key.strip().upper(), value.strip()
        if not sep or not KEY.match(key) or not value:
            _refuse(f"'{entry}' in {what} is not KEY=value, like ABCD-123={check.example}")
        out[key] = check(key, value)
    return out


def _role(key: str, value: str) -> str:
    value = value.lower()
    if value not in ROLES:
        _refuse(f"{key}={value}: '{value}' is not a role — use one of "
                + ", ".join(ROLES))
    return value


_role.example = "zone"


def _date(key: str, value: str) -> str:
    from datetime import date
    try:
        date.fromisoformat(value)
    except ValueError:
        _refuse(f"{key}={value}: '{value}' is not a yyyy-mm-dd date")
    return value


_date.example = "2026-10-01"


def roles_of(said) -> Dict[str, str]:
    return pairs_of(said, "roles", _role)


def dates_of(said) -> Dict[str, str]:
    return pairs_of(said, "dates", _date)


def _refuse(sentence: str) -> None:
    from .. import SourceError
    raise SourceError(sentence)


def roles(issues: List[dict], root_key: str, kept: Set[str],
          levels: Optional[List[str]] = None,
          overrides: Optional[Dict[str, str]] = None) -> Tuple[Dict[str, str], List[str]]:
    """What each kept issue under a root becomes, and anything worth saying.

    An issue marked `skip` is absent from the answer along with everything
    beneath it. `junction` on something with nothing beneath it has no branch
    to carry, so it becomes a station instead — and says so, since that is not
    what was asked for.
    """
    levels = list(levels or [])
    overrides = dict(overrides or {})
    kids: Dict[str, List[dict]] = {}
    for issue in issues:
        if issue.get("key") in kept:
            kids.setdefault(issue.get("parent") or "", []).append(issue)

    out: Dict[str, str] = {}
    said: List[str] = []
    level_default: Dict[int, str] = {}

    def default(depth: int) -> str:
        if depth not in level_default:
            at_depth = [i for i in issues if i.get("depth") == depth
                        and i.get("key") in kept]
            branching = depth == 1 and any(kids.get(i.get("key")) for i in at_depth)
            level_default[depth] = "junction" if branching else "station"
        return level_default[depth]

    def visit(parent: str) -> None:
        for issue in kids.get(parent, []):
            key = issue.get("key") or ""
            depth = int(issue.get("depth") or 1)
            role = overrides.get(key) or (levels[depth - 1] if depth <= len(levels)
                                          else "") or default(depth)
            if role == "skip":
                continue
            if role == "junction" and not kids.get(key):
                if key in overrides or depth <= len(levels):
                    flat.append(key)
                role = "station"
            out[key] = role
            visit(key)

    flat: List[str] = []
    visit(root_key)
    if flat:
        shown = ", ".join(flat[:3]) + (f" and {len(flat) - 3} more" if len(flat) > 3 else "")
        said.append(f"{shown}: nothing beneath to branch, so a station instead "
                    "of a junction")
    return out, said
