#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
from_git.py — turn a repository's history into a map spec.

A commit graph is already a metro map: a branch is a line, a commit is a
station, a lane is a row, and a commit two branches share becomes an
interchange without being asked. This module only decides *which* commits go
where; the drawing is the ordinary renderer.

    metro-map --from-git .                        -o history.svg
    metro-map --from-git https://github.com/o/r   -o history.svg
    metro-map --from-git . --model branch-model.json -o history.svg

The model is the point of the third form. A branch model can be drawn before a
line of code exists — lanes named prod, preprod, beta and so on, each saying
which git branch it stands for — and syncing fills that shape in with real
commits while keeping the names, colours, order and everything else the author
chose. The map is the intent; the repository is only the evidence.
"""

from __future__ import annotations

import contextlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from . import metro_map as mm

UNIT = "\x1f"                       # git's own field separator, safe in subjects
REMOTE = re.compile(r"^(https?://|git@|ssh://|git://)")

# A lane needs a colour before it has an opinion; these follow the palette so a
# generated map looks like a drawn one.
LANE_COLOURS = [c for _, c in mm.PALETTE]


class GitError(RuntimeError):
    """git said no, with what it said."""


def _git(repo: Path, *args: str, check: bool = True) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)
    if check and done.returncode != 0:
        raise GitError((done.stderr or done.stdout).strip() or "git failed")
    return done.stdout


@contextlib.contextmanager
def open_repo(source: str) -> Iterator[Path]:
    """A path to work in, cloning first when the source is a URL.

    Blobless rather than shallow: the whole commit graph is needed to find where
    branches diverged, but not one byte of any file, so this stays fast on a
    large repository and still answers merge-base correctly.
    """
    if not REMOTE.match(source):
        path = Path(source).expanduser().resolve()
        if not (path / ".git").exists() and not (path / "HEAD").exists():
            raise GitError(f"{path} is not a git repository")
        yield path
        return

    tmp = Path(tempfile.mkdtemp(prefix="metro-map-git-"))
    try:
        done = subprocess.run(
            ["git", "clone", "--bare", "--filter=blob:none", "--quiet",
             source, str(tmp / "repo")],
            capture_output=True, text=True)
        if done.returncode != 0:
            raise GitError(f"could not clone {source}: "
                           + ((done.stderr or "").strip() or "git failed"))
        yield tmp / "repo"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def default_branch(repo: Path) -> str:
    head = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD",
                check=False).strip()
    if head:
        return head
    for guess in ("main", "master"):
        if _git(repo, "rev-parse", "--verify", "--quiet", guess, check=False).strip():
            return guess
    names = branch_names(repo)
    if not names:
        raise GitError("the repository has no branches")
    return names[0]


def branch_names(repo: Path) -> List[str]:
    out = _git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    return [n for n in out.split("\n") if n.strip()]


def exists(repo: Path, ref: str) -> bool:
    return bool(_git(repo, "rev-parse", "--verify", "--quiet", ref,
                     check=False).strip())


def _first_parent(repo: Path, ref: str, limit: int) -> List[dict]:
    """A branch's own line of development, oldest first.

    First-parent because that is the branch's own story: a merge brings a
    hundred commits with it and none of them happened *on* this branch.

    Excluding earlier lanes is deliberately not left to git. `log beta ^main`
    excludes everything main can *reach*, and once beta has been merged that is
    all of beta — the lane empties the moment the work lands, which is exactly
    when you most want to see it. Subtracting first-parent sets in Python keeps
    a merged branch on the map.
    """
    args = ["log", "--first-parent", f"--max-count={limit}",
            f"--pretty=%H{UNIT}%h{UNIT}%s{UNIT}%D{UNIT}%cI{UNIT}%P", ref]
    rows = []
    for line in _git(repo, *args).split("\n"):
        if not line.strip():
            continue
        sha, short, subject, refs, when, parents = line.split(UNIT)
        tag = next((r.strip().removeprefix("tag: ")
                    for r in refs.split(",") if "tag:" in r), None)
        rows.append({"sha": sha, "short": short, "subject": subject,
                     "tag": tag, "when": when,
                     "parents": parents.split() if parents.strip() else []})
    rows.reverse()                              # oldest first, left to right
    return rows


def topo_order(repo: Path, refs: Sequence[str], limit: int) -> Dict[str, int]:
    """Every commit's place in a parents-before-children ordering.

    Not the commit date. Dates are wall-clock times from whichever machine made
    the commit: they run backwards across a rebase, tie when a script commits
    twice in a second, and are trivially forged. Topological order is the only
    thing that guarantees a commit is drawn to the right of what it was built
    on, which is the one property a history diagram cannot get wrong.
    """
    args = ["rev-list", "--topo-order", "--reverse", f"--max-count={limit * 8}",
            *refs]
    return {sha: i for i, sha in enumerate(_git(repo, *args).split()) if sha}


def read_history(repo: Path, lanes: Sequence[Tuple[str, str]],
                 limit: int) -> Tuple[Dict[str, dict], List[dict]]:
    """Commits per lane, plus where each lane forks from and merges back into.

    `lanes` is (display name, git branch) in the order they should be drawn. A
    commit belongs to the first lane that can claim it, so an upper lane keeps
    the shared history and the lower ones show only their own work.

    The fork point is the first parent of a lane's oldest own commit — *not* the
    merge base, which for a branch that has already been merged is the branch's
    own tip, and would anchor the lane to itself. The rejoin is whichever
    claimed commit lists this lane's newest commit as a second parent: that is
    what a merge is. Both are stations other lanes already have, so the line
    forks and rejoins by simply passing through them.
    """
    seen: Dict[str, dict] = {}
    out: List[dict] = []
    claimed: set = set()

    for row, (name, branch) in enumerate(lanes):
        if not exists(repo, branch):
            out.append({"name": name, "branch": branch, "row": row,
                        "missing": True, "commits": [], "fork": None,
                        "rejoin": None})
            continue
        rows = [c for c in _first_parent(repo, branch, limit)
                if c["sha"] not in claimed]
        for c in rows:
            seen.setdefault(c["sha"], c)
            claimed.add(c["sha"])
        out.append({"name": name, "branch": branch, "row": row,
                    "missing": False, "commits": rows,
                    "fork": None, "rejoin": None})

    for lane in out:
        if not lane["commits"] or not lane["row"]:
            continue
        oldest, newest = lane["commits"][0], lane["commits"][-1]
        first_parent = (oldest["parents"] or [None])[0]
        if first_parent in seen:
            lane["fork"] = seen[first_parent]["short"]
        for c in seen.values():
            if newest["sha"] in c["parents"][1:]:
                lane["rejoin"] = c["short"]
                break
    return seen, out


def build_spec(lanes: List[dict], seen: Dict[str, dict], order: Dict[str, int],
               model: Optional[dict] = None, label_every: bool = False) -> dict:
    """Assemble the map: stations left to right in topological order."""
    ordered = sorted(seen.values(),
                     key=lambda c: (order.get(c["sha"], 1 << 30), c["when"]))
    gx = {c["sha"]: i for i, c in enumerate(ordered)}
    row_of = {c["sha"]: 0 for c in ordered}
    for lane in lanes:
        for c in lane["commits"]:
            row_of[c["sha"]] = lane["row"]

    stations: Dict[str, dict] = {}
    for c in ordered:
        sid = c["short"]
        label = c["tag"] or (c["subject"] if label_every else c["short"])
        st = {"label": label, "gx": gx[c["sha"]], "gy": row_of[c["sha"]],
              "label_at": "above" if row_of[c["sha"]] % 2 == 0 else "below"}
        if c["tag"]:
            st["interchange"] = True            # a release is a real stop
            st["label_angle"] = 45
        stations[sid] = st

    by_name = {}
    if model:
        by_name = {ln.get("name"): ln for ln in model.get("lines", [])
                   if isinstance(ln, dict)}

    lines = []
    for lane in lanes:
        route = ([lane["fork"]] if lane["fork"] else [])
        route += [c["short"] for c in lane["commits"]]
        if lane["rejoin"]:
            route.append(lane["rejoin"])
        route = [sid for i, sid in enumerate(route)      # a fork that is also
                 if i == 0 or sid != route[i - 1]]       # the first commit
        keep = by_name.get(lane["name"], {})
        # notes are addressed by hop index, and the hops are all different now —
        # a note that meant "between the cut and going live" would silently come
        # to mean "between two commits that happen to sit in that position"
        line = {k: v for k, v in keep.items()
                if k not in ("stations", "branch", "notes")}
        line.setdefault("name", lane["name"])
        line.setdefault("color", LANE_COLOURS[lane["row"] % len(LANE_COLOURS)])
        line["branch"] = lane["branch"]
        line["stations"] = route
        if len(route) >= 2 or not lane["missing"]:
            lines.append(line)

    spec: dict = {}
    if model:
        # everything the author decided stays; only the history is replaced
        spec = {k: v for k, v in model.items()
                if k not in ("stations", "lines", "format")}
    spec["stations"] = stations
    spec["lines"] = lines
    spec.setdefault("legend", "bottom")
    spec.setdefault("style", {"cell": 110, "stroke": 8, "label_size": 12})
    return spec


def lanes_from(model: Optional[dict], repo: Path,
               chosen: Optional[Sequence[str]]) -> List[Tuple[str, str]]:
    """Which lanes to draw, and in which order.

    A model decides it: its lines, in the order the author put them, each
    naming the branch it stands for. Otherwise the default branch leads and the
    rest follow alphabetically, which is arbitrary but at least stable.
    """
    if model:
        pairs = [(ln.get("name") or ln.get("branch"), ln.get("branch") or ln.get("name"))
                 for ln in model.get("lines", []) if isinstance(ln, dict)]
        pairs = [(n, b) for n, b in pairs if n and b]
        if pairs:
            return pairs
    if chosen:
        return [(b, b) for b in chosen]
    head = default_branch(repo)
    rest = sorted(b for b in branch_names(repo) if b != head)
    return [(b, b) for b in [head, *rest]]


def from_git(source: str, model_path: Optional[str] = None,
             branches: Optional[Sequence[str]] = None,
             limit: int = 200, label_every: bool = False) -> Tuple[dict, List[str]]:
    """A spec for a repository's history, plus anything worth saying about it."""
    model = None
    if model_path:
        model = json.loads(Path(model_path).read_text(encoding="utf-8"))

    notes: List[str] = []
    with open_repo(source) as repo:
        lanes = lanes_from(model, repo, branches)
        seen, built = read_history(repo, lanes, limit)
        order = topo_order(repo, [b for _, b in lanes if exists(repo, b)], limit)
        for lane in built:
            if lane["missing"]:
                notes.append(f"branch '{lane['branch']}' is not in the repository "
                             f"— lane '{lane['name']}' is drawn empty")
            elif not lane["commits"] and lane["row"]:
                notes.append(f"lane '{lane['name']}' has no commits of its own "
                             "— it is merged up to date")
        if not seen:
            raise GitError("no commits found")
        spec = build_spec(built, seen, order, model, label_every)
    return spec, notes
