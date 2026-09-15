#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ERP-LAB-5
"""
app.py — browser front end for metro_map.py.

Serves a design workspace: place stations on a grid by dragging them on a live
canvas, then build lines by picking from the stations already placed. Maps are
plain metro_map specs, loaded from and saved to JSON files, so anything designed
here still renders from the command line.

They live in two folders: ./mymaps for your own work, which the repo ignores,
and ./shared-maps for the maps that ship with the tool. A map is addressed by
name plus folder; an unqualified read looks in mymaps first, so a personal copy
shadows a shared one of the same name, and a save goes back to the folder the
map came from.

    metro-map-designer                   # http://127.0.0.1:8765
    metro-map-designer --port 9000 --maps-dir ~/maps
    python3 -m metro_map_tool.app        # the same, from a checkout

The rendering, geometry and validation all live in metro_map; this module only
moves specs between the browser and disk.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Dict, Optional, Tuple

from flask import Response, abort, jsonify, render_template, request

from . import metro_map as mm
from .core import server
from .core import services
from .core.server import only_local

app = server.create_app(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024      # a spec is a few KB

# Two folders, one namespace. mymaps is yours and git-ignored; shared is what
# the repo ships. Search order matters: an unqualified name finds mymaps first.
# mymaps follows the working directory, because it is the user's own work and
# they may keep several sets. shared-maps travels with the code, so a pip
# install still opens on the map that explains the tool.
HERE = Path(__file__).resolve().parent
FOLDERS: Dict[str, Path] = {"mymaps": Path("mymaps").resolve(),
                            "shared": (HERE / "shared-maps").resolve()}
DEFAULT_FOLDER = "mymaps"
NAME_RE = re.compile(r"[A-Za-z0-9 _-]{1,64}$")


# ----------------------------------------------------------------- paths ----

def base_dir(folder: str) -> Path:
    if folder not in FOLDERS:
        abort(400, f"unknown folder '{folder}' — one of " + ", ".join(FOLDERS))
    return FOLDERS[folder]


def map_path(name: str, folder: str = DEFAULT_FOLDER) -> Path:
    """Resolve a map name to a file inside one folder, or refuse it."""
    base = base_dir(folder)
    if not NAME_RE.match(name or ""):
        abort(400, "map name may only contain letters, digits, space, - and _")
    path = (base / f"{name}.json").resolve()
    if path.parent != base:                # belt and braces after the name check
        abort(400, "map name escapes the maps directory")
    return path


def find_map(name: str, folder: Optional[str] = None) -> Tuple[Path, str]:
    """The file a name refers to, and the folder it was found in.

    With no folder given, mymaps wins over shared. When it exists in neither,
    the answer is where it *would* go, so the caller can 404 with a real path.
    """
    if folder:
        return map_path(name, folder), folder
    for candidate in FOLDERS:
        path = map_path(name, candidate)
        if path.exists():
            return path, candidate
    return map_path(name, DEFAULT_FOLDER), DEFAULT_FOLDER


def spec_version(path: Path) -> str:
    """Short content hash of a map file — the token editors compare.

    Content, not mtime: two writes inside one filesystem timestamp tick would
    share an mtime, and a restored file can carry an older one.
    """
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def read_spec(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        spec = json.load(fh)
    spec.setdefault("stations", {})
    spec.setdefault("lines", [])
    spec.setdefault("zones", [])
    mm.migrate(spec)          # older shapes come forward; a newer one is left
    return spec               # alone for validate_spec to refuse by name


def write_spec(path: Path, spec: dict) -> None:
    """Atomic write, so a crash mid-save never truncates an existing map."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(spec, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400, "expected a JSON object body")
    return data


# The About box, the update check and the version comparison all come from
# core.server. It reports the two maps folders because only this module knows
# where they ended up.
server.about_extras(lambda: {f"{label} maps": str(FOLDERS[key])
                             for key, label in (("mymaps", "Your"),
                                                ("shared", "Shared"))})


# ---------------------------------------------------------------- services --
#
# The About box's service list comes from core; these are the parts only this
# tool has. Each importer is grey until it is configured — most people never
# connect one, and red for "you did not set up Jira" would be crying wolf —
# and once it is, one cheap authenticated call says whether it still works.

def _maps_folders():
    problems = []
    for key, path in FOLDERS.items():
        if not path.is_dir():
            problems.append(f"{key} is missing: {path}")
        elif not os.access(path, os.W_OK):
            problems.append(f"{key} is read-only: {path}")
    return (not problems, "; ".join(problems) or f"saving to {FOLDERS[DEFAULT_FOLDER]}")


def _importer(name: str, label: str, probe) -> None:
    def enabled():
        from . import sources as S
        missing = [e["name"] for e in S.env_status(S.get(name))
                   if e["required"] and not e["present"]]
        return (not missing, "not set up — " + ", ".join(missing)
                + " under Settings, or in the environment" if missing else "")

    def check():
        from . import sources as S
        src = S.get(name)
        try:
            return True, probe(src, S.credentials(src))
        except (S.SourceError, OSError) as exc:
            return False, str(exc)

    services.register(name, label, check=check, enabled=enabled)


def _jira_probe(src, creds) -> str:
    from .sources.jira import client as jira_client
    conn = jira_client.connect(creds)
    me = conn.myself()
    return f"{conn.site} as {me.get('displayName') or me.get('emailAddress') or 'you'}"


def _github_probe(src, creds) -> str:
    from .sources import github, http
    token = next((v for v in creds.values() if v), "")
    me = http.get_json(f"{github.API}/user", github._headers(token), timeout=5)
    return f"api.github.com as {me.get('login') or 'you'}"


services.register("maps", "Maps folders", check=_maps_folders)
_importer("jira", "Jira importer", _jira_probe)
_importer("github", "GitHub importer", _github_probe)


# ------------------------------------------------------------------ page ----

@app.get("/")
def index() -> str:
    return render_template("index.html")


@app.get("/favicon.ico")
def favicon():
    """Browsers ask for this by name whatever the page links to."""
    return app.send_static_file("favicon.svg")


# ------------------------------------------------------------------- api ----

# ------------------------------------------------------------------- locks --
#
# While an agent works on a map, the person's designer goes read-only, so the
# two cannot edit the same map at once. One user on one machine, so this is a
# dict in memory: nothing to persist, nothing to clean up after a restart.
#
# The agent takes the lock when it reads a map and gives it back when its save
# lands. It lapses by itself after LOCK_FOR seconds, so an agent that crashed
# or wandered off never leaves a map stuck. "Take over" in the designer ends it
# at once and leaves a marker behind: the agent's next save carries the lock it
# was given, finds the marker, and is refused until it reads the map again —
# otherwise it would write straight over what the person started doing.

LOCK_FOR = 120
_locks: Dict[Tuple[str, str], dict] = {}
_locks_guard = threading.Lock()


def lock_of(name: str, folder: str) -> Optional[dict]:
    """The live lock or take-over marker on a map, or None."""
    with _locks_guard:
        entry = _locks.get((name, folder))
        if entry and time.time() - entry["at"] > LOCK_FOR:
            _locks.pop((name, folder), None)
            return None
        return entry


def lock_view(entry: Optional[dict]) -> Optional[dict]:
    """What the page is told: who holds it and for how much longer."""
    if not entry or entry.get("taken_over"):
        return None
    return {"holder": entry["holder"],
            "seconds_left": max(0, int(LOCK_FOR - (time.time() - entry["at"])))}


@app.post("/api/maps/<name>/lock")
def take_lock(name: str):
    """An agent starting work on a map. Taking it again renews it."""
    only_local("locking a map")
    path, folder = find_map(name, request.args.get("folder"))
    if not path.exists():
        abort(404, f"no map called '{name}'")
    entry = {"id": uuid.uuid4().hex, "holder": request.headers.get("X-Agent") or "an agent",
             "at": time.time()}
    with _locks_guard:
        _locks[(name, folder)] = entry
    return jsonify({"name": name, "folder": folder, "lock_id": entry["id"],
                    "seconds": LOCK_FOR})


@app.delete("/api/maps/<name>/lock")
def drop_lock(name: str):
    """Give a lock back. From the designer, that is taking the map over."""
    only_local("unlocking a map")
    path, folder = find_map(name, request.args.get("folder"))
    held = lock_of(name, folder)
    with _locks_guard:
        if held and not request.headers.get("X-Agent"):
            # the person took it: remember which lock that was, so its holder
            # cannot save on as if nothing had happened
            _locks[(name, folder)] = {"id": held["id"], "holder": held["holder"],
                                      "at": time.time(), "taken_over": True}
        else:
            _locks.pop((name, folder), None)
    return jsonify({"name": name, "folder": folder, "locked": False})


@app.get("/api/maps")
def list_maps():
    out = []
    for folder, base in FOLDERS.items():        # mymaps first, then shared
        base.mkdir(parents=True, exist_ok=True)
        for path in sorted(base.glob("*.json")):
            entry = {"name": path.stem, "folder": folder,
                     "mtime": path.stat().st_mtime, "version": spec_version(path),
                     "lock": lock_view(lock_of(path.stem, folder))}
            try:
                spec = read_spec(path)
                entry["stations"] = len(spec["stations"])
                entry["lines"] = len(spec["lines"])
                entry["zones"] = len(spec["zones"])
                entry["mode"] = spec.get("mode") or "metro"
            except (OSError, ValueError):
                entry["error"] = "not readable as a spec"
            out.append(entry)
    return jsonify(out)


@app.get("/api/maps/<name>")
def get_map(name: str):
    path, folder = find_map(name, request.args.get("folder"))
    if not path.exists():
        abort(404, f"no map called '{name}'")
    try:
        return jsonify({"name": name, "folder": folder, "spec": read_spec(path),
                        "version": spec_version(path),
                        "lock": lock_view(lock_of(name, folder))})
    except (OSError, ValueError) as exc:
        abort(400, f"could not read '{name}': {exc}")


@app.put("/api/maps/<name>")
def put_map(name: str):
    data = body()
    # A save says where it goes. A save that does not — an older client, or a
    # script — lands where the map already lives, and only a genuinely new map
    # defaults to mymaps: overwriting a shared map is what the caller meant,
    # where quietly forking a mymaps copy that then shadows it is not.
    folder = data.get("folder") or find_map(name)[1]
    path = map_path(name, folder)
    spec = data.get("spec")
    errors = mm.validate_spec(spec)
    if errors:
        return jsonify({"errors": errors}), 400
    held = lock_of(name, folder)
    agent = bool(request.headers.get("X-Agent"))
    if held and not held.get("taken_over") and not agent:
        return jsonify({"locked": True, "lock": lock_view(held),
                        "errors": [f"an agent is updating '{name}' — take it over "
                                   "in the designer to edit it yourself"]}), 423
    if held and held.get("taken_over") and agent and data.get("lock_id") == held["id"]:
        return jsonify({"locked": True,
                        "errors": [f"'{name}' was taken over in the designer while "
                                   "you were working on it"]}), 423
    # Optimistic concurrency: a client that read version X may only overwrite
    # version X. Anything else means someone — the other editor, or an agent —
    # saved in between, and last-writer-wins would silently eat their work.
    base = data.get("base_version")
    if base is not None and path.exists():
        current = spec_version(path)
        if current != base:
            return jsonify({
                "conflict": True,
                "errors": [f"'{name}' changed since you loaded it — "
                           "another editor or an agent saved it"],
                "name": name, "folder": folder,
                "spec": read_spec(path), "version": current,
            }), 409

    if data.get("auto_interchange", True):
        mm.auto_interchanges(spec)
    # Stamp on the way out, so anything that saves through this server — the
    # designer, the MCP tools, a script — leaves a file that can be told apart.
    # The number is the oldest format that can still draw this map, not the
    # newest the tool knows: stamping every save with the newest would lock
    # ordinary maps out of older copies for features they never use.
    spec["format"] = mm.needs_format(spec)
    try:
        write_spec(path, spec)
    except OSError as exc:
        abort(500, f"could not write '{name}': {exc}")
    if agent and held and data.get("lock_id") == held["id"]:
        with _locks_guard:                  # the work landed: hand the map back
            _locks.pop((name, folder), None)
    return jsonify({"name": name, "folder": folder, "spec": spec, "saved": True,
                    "version": spec_version(path),
                    "warnings": mm.spec_warnings(spec)})


@app.delete("/api/maps/<name>")
def delete_map(name: str):
    path, folder = find_map(name, request.args.get("folder"))
    if not path.exists():
        abort(404, f"no map called '{name}'")
    try:
        path.unlink()
    except OSError as exc:
        abort(500, f"could not delete '{name}': {exc}")
    with _locks_guard:
        _locks.pop((name, folder), None)
    return jsonify({"name": name, "folder": folder, "deleted": True})


@app.post("/api/render")
def render_map():
    data = body()
    spec = data.get("spec")
    errors = mm.validate_spec(spec)
    if errors:
        return jsonify({"errors": errors}), 400
    changed = mm.auto_interchanges(spec) if data.get("auto_interchange", True) else 0
    # the resolved ruler, so the browser can name dates without redoing the
    # maths. It rides on the empty answer too: a roadmap with no stations yet
    # still has real dates, and the Timeline panel should say so rather than
    # claim they are broken.
    tl = mm.spec_timeline(spec)
    timeline = timeline_payload(tl) if tl else None

    if not spec["stations"]:
        out = {"svg": "", "empty": True, "interchanges_changed": 0}
        if timeline:
            out["timeline"] = timeline
        return jsonify(out)

    style = mm.style_from(data.get("style") or spec.get("style"))
    # the designer previews in one theme; anything downloaded stays adaptive
    try:
        svg = mm.render(spec, style, data.get("theme") or "auto")
    except (KeyError, ValueError, ZeroDivisionError) as exc:
        return jsonify({"errors": [f"render failed: {exc}"]}), 400
    out = {"svg": svg, "interchanges_changed": changed,
           "warnings": mm.spec_warnings(spec), "stations": spec["stations"]}
    if timeline:
        out["timeline"] = timeline
    return jsonify(out)


def timeline_payload(tl: mm.Timeline) -> dict:
    """A resolved ruler as the browser and the agents want it.

    Every column is named and dated here rather than in the client, so the date
    arithmetic lives in one language.
    """
    columns = []
    for k in range(tl.columns):
        starts = tl.boundary(k)
        minor = mm.minor_label(starts, tl.interval)
        major = mm.major_label(starts, tl.interval)
        columns.append({"gx": k, "date": starts.isoformat(), "label": minor,
                        "full": f"{minor} {major}".strip(),
                        "ends": (tl.boundary(k + 1) - timedelta(days=1)).isoformat()})
    return {"columns": tl.columns, "interval": tl.interval,
            "start": tl.start.isoformat(), "end": tl.end.isoformat(),
            "columns_at": columns}


@app.post("/api/timeline")
def timeline_info():
    """Resolve a timeline block on its own, without a spec around it.

    The Timeline panel and the resolve_timeline MCP tool both need the snapped
    range and the column names before there is anything worth rendering.
    """
    data = body()
    try:
        tl = mm.build_timeline(data.get("timeline"))
    except ValueError as exc:
        return jsonify({"errors": [str(exc)]}), 400
    return jsonify(timeline_payload(tl))


@app.get("/api/maps/<name>/svg")
def download_svg(name: str):
    path, _ = find_map(name, request.args.get("folder"))
    if not path.exists():
        abort(404, f"no map called '{name}'")
    spec = read_spec(path)
    errors = mm.validate_spec(spec)
    if errors:
        return jsonify({"errors": errors}), 400
    svg = mm.render(spec, mm.style_from(spec.get("style")))
    return Response(svg, mimetype="image/svg+xml", headers={
        "Content-Disposition": f'attachment; filename="{name}.svg"'})


@app.get("/api/palette")
def palette():
    return jsonify([{"name": n, "color": c} for n, c in mm.PALETTE])


MODE_TITLES = {"metro": "Metro map", "roadmap": "Roadmap"}
CONTINUES_TITLES = {"none": "no", "start": "at the start",
                    "end": "at the end", "both": "at both ends"}
DEAD_END_TITLES = {"none": "none", "buffer": "end of the line — stops here",
                   "smoke": "smoke — watch out",
                   "fire": "burning platform — get off"}
FOLDER_TITLES = {"mymaps": "My maps", "shared": "Shared"}

STATUS_TITLES = {
    "live": "In service",
    "out-of-service": "Out of service — dead end",
    "under-construction": "Under construction",
    "planned": "Planned",
}


# Options that name a file on this machine. The designer is a local tool, but
# --host can put it on a network, and an importer that reads any path the
# browser names would turn it into a file-reading service. The browser gets the
# model through "into" — a map this server already owns — instead.
LOCAL_PATH_OPTIONS = ("from_file", "to_file", "model")


@app.get("/api/sources")
def sources():
    """The importers available, with what each one takes.

    Fetched when the Import dialog opens rather than at startup: whether a
    credential is set can change while the server is running, and a stale "not
    set" is a bad first impression.
    """
    from . import sources as S
    return jsonify({
        "sources": [S.describe(src) for _, src in sorted(S.catalogue().items())
                    ],
        "broken": S.broken(),
        "local_only": list(LOCAL_PATH_OPTIONS),
    })


@app.post("/api/import")
def import_map():
    """Build a spec from a source. Deliberately does not save it.

    The result goes back to the browser as an unsaved edit, so saving stays a
    deliberate act and every write keeps going through PUT — which is where the
    concurrency check, the interchange pass and the format stamp live.
    """
    from . import sources as S
    from .sources.merge import merge

    data = body()
    name = (data.get("source") or "").strip()
    try:
        src = S.get(name)
    except S.SourceError as exc:
        abort(400, str(exc))

    given = data.get("options") or {}
    if not isinstance(given, dict):
        abort(400, "options must be an object of name to value")
    for key in LOCAL_PATH_OPTIONS:
        if given.get(key):
            abort(400, f"'{key}' names a file on the machine running the "
                       "designer, so it is only available from the command "
                       "line; use 'into' to re-sync a map this server holds")

    opts, errors = S.coerce(src, given)
    if errors:
        return jsonify({"errors": errors}), 400

    model = None
    base = None
    into = data.get("into") or {}
    if into.get("name"):
        path, folder = find_map(into["name"], into.get("folder"))
        if not path.exists():
            abort(404, f"no map called '{into['name']}' to sync into")
        # read together with the version it was read at: the result is built on
        # this copy, so saving it must not replace a newer one somebody saved
        # while the import ran — the caller passes it back as base_version
        base = spec_version(path)
        model = read_spec(path)

    try:
        raw = S.payload(src, opts, model)
        fresh, notes = src.build(raw, opts, model)
    except S.SourceError as exc:
        return jsonify({"errors": [str(exc)]}), 400
    except OSError as exc:
        return jsonify({"errors": [f"{src.name}: {exc}"]}), 400

    spec, more = merge(model, fresh, source=src.name, options=opts,
                       refresh=opts.get("refresh") or list(src.refresh_default),
                       prune=bool(opts.get("prune")),
                       stamp=S.stamp(src, opts))
    out = {"spec": spec, "notes": notes + more,
           "warnings": mm.spec_warnings(spec), "errors": mm.validate_spec(spec)}
    if base is not None:
        out.update(base_version=base, into={"name": into["name"], "folder": folder})
    return jsonify(out)


@app.get("/api/settings/<name>")
def get_settings(name):
    """What a source needs, and which of it is already set.

    Never a value. A secret that has been saved reports only that it is saved,
    because there is no reason for a token to travel back to a browser and
    every reason for it not to.
    """
    from . import sources as S
    only_local("settings")
    try:
        src = S.get(name)
    except S.SourceError as exc:
        abort(404, str(exc))
    return jsonify({"source": src.name, "title": src.title,
                    "fields": S.env_status(src),
                    "path": str(S.config_path(src.name))})


@app.put("/api/settings/<name>")
def put_settings(name):
    """Save a source's settings to its own config file, mode 0600.

    This is the one place a credential is typed, and it goes browser -> local
    server -> a file only this account can read. A field left out is left
    alone; a field sent empty is cleared — so "" means "forget this", and
    absent means "keep whatever is there".
    """
    from . import sources as S
    only_local("settings")
    try:
        src = S.get(name)
    except S.SourceError as exc:
        abort(404, str(exc))

    given = body().get("values")
    if not isinstance(given, dict):
        abort(400, "values must be an object of setting name to value")
    allowed = {item.config_key(src.name) for item in src.env}
    unknown = sorted(set(given) - allowed)
    if unknown:
        abort(400, f"{src.name} has no setting called " + ", ".join(unknown))

    path = S.write_config(src.name, {k: str(v) for k, v in given.items()})
    return jsonify({"saved": True, "path": str(path),
                    "fields": S.env_status(src)})


@app.post("/api/settings/<name>/test")
def test_settings(name):
    """One cheap authenticated call, to say whether the settings actually work.

    Worth its own endpoint: "is this token right" is the question somebody has
    the moment they paste one, and the alternative is finding out through a
    failed import that looks like a different problem.
    """
    from . import sources as S
    only_local("settings")
    try:
        src = S.get(name)
    except S.SourceError as exc:
        abort(404, str(exc))
    check = getattr(src, "check", None) or _source_check(src)
    if check is None:
        return jsonify({"ok": False, "said": f"{src.name} has nothing to test"})
    try:
        return jsonify({"ok": True, "said": check()})
    except S.SourceError as exc:
        return jsonify({"ok": False, "said": str(exc)})
    except OSError as exc:
        return jsonify({"ok": False, "said": str(exc)})


def _source_check(src):
    """A connection test for a source that has one, or None.

    Looked up rather than declared on Source, because only the sources that
    reach a network need it and adding a field every plugin must think about
    to serve two of them is the wrong trade.
    """
    if src.name != "jira":
        return None
    from . import sources as S
    from .sources.jira import client as jira_client

    def check():
        conn = jira_client.connect(S.credentials(src))
        me = conn.myself()
        projects = conn.projects(limit=50)
        who = me.get("displayName") or me.get("emailAddress") or "you"
        return (f"connected to {conn.site} as {who} — "
                f"{len(projects)} project(s) visible")
    return check


@app.get("/api/browse/<name>")
def browse_source(name):
    """The children of one path in a source's discovery tree.

    One level per request on purpose: a project with four thousand issues must
    not be fetched to draw three rows.
    """
    from . import sources as S
    only_local("browsing a source")
    try:
        src = S.get(name)
    except S.SourceError as exc:
        abort(404, str(exc))
    if src.browse is None:
        abort(400, f"{src.name} has nothing to browse")

    path = [p for p in (request.args.get("path") or "").split("/") if p]
    view = request.args.get("view") or (src.views[0].name if src.views else "")
    query = (request.args.get("q") or "").strip()
    opts, errors = S.coerce(src, {k: v for k, v in request.args.items()
                                  if k not in ("path", "view", "q")
                                  and src.option(k) is not None})
    if errors:
        return jsonify({"errors": errors}), 400
    try:
        nodes = src.browse(path, opts, view, query)
    except S.SourceError as exc:
        return jsonify({"errors": [str(exc)]}), 400
    except OSError as exc:
        return jsonify({"errors": [f"{src.name}: {exc}"]}), 400
    return jsonify({"path": path, "view": view, "q": query,
                    "nodes": [n.as_json() for n in nodes]})


@app.get("/api/defaults")
def defaults():
    return jsonify({"style": mm.style_to_dict(mm.Style()),
                    "label_sides": list(mm.COMPASS),
                    "line_statuses": [{"value": k, "label": STATUS_TITLES.get(k, k)}
                                      for k in mm.STATUS_CLASS],
                    "label_angles": list(mm.LABEL_ANGLES),
                    "modes": [{"value": k, "label": MODE_TITLES.get(k, k)}
                              for k in mm.MODES],
                    "intervals": list(mm.INTERVALS),
                    "legend_positions": list(mm.LEGEND_AT),
                    "dead_ends": [{"value": k, "label": DEAD_END_TITLES.get(k, k)}
                                  for k in mm.DEAD_ENDS],
                    "themes": list(mm.THEMES),
                    "spec_format": mm.SPEC_FORMAT,
                    "axes": ["top", "bottom"],
                    "continues": [{"value": k, "label": CONTINUES_TITLES.get(k, k)}
                                  for k in mm.CONTINUES],
                    "default_legend": mm.DEFAULT_LEGEND,
                    "folders": [{"value": k, "label": FOLDER_TITLES.get(k, k)}
                                for k in FOLDERS],
                    "default_folder": DEFAULT_FOLDER})


# ------------------------------------------------------------------ main ----

def warn_legacy_maps() -> None:
    """Say something if maps/ still holds specs from before the folder split.

    Before v2 every map lived in ./maps, tracked by git. Nothing reads that
    directory any more, so a personal spec left there simply stops appearing —
    and a silent auto-move is worse, because it is the user's data and mymaps
    may already hold the same name.
    """
    legacy = sorted(Path("maps").glob("*.json"))
    if not legacy:
        return
    names = ", ".join(p.stem for p in legacy[:4])
    if len(legacy) > 4:
        names += f", and {len(legacy) - 4} more"
    print(f"  ! maps/ still holds {len(legacy)} map(s) — {names}")
    print(f"  ! nothing reads that folder now; move them into "
          f"{FOLDERS[DEFAULT_FOLDER]} to see them again")


def main() -> int:
    ap = argparse.ArgumentParser(prog="metro-map-designer",
                                 description=__doc__.splitlines()[1])
    server.add_server_args(ap)
    ap.add_argument("--maps-dir", default="mymaps",
                    help="your own maps (default: mymaps)")
    ap.add_argument("--shared-maps-dir", default=str(HERE / "shared-maps"),
                    help="maps that ship with the tool (default: the installed set)")
    args = ap.parse_args()

    FOLDERS["mymaps"] = Path(args.maps_dir).expanduser().resolve()
    FOLDERS["shared"] = Path(args.shared_maps_dir).expanduser().resolve()
    lines = []
    for folder, base in FOLDERS.items():
        base.mkdir(parents=True, exist_ok=True)
        lines.append(f"  {folder:<13} {base}")
    warn_legacy_maps()
    return server.serve(
        app, args, lines=lines,
        network_note="    Anyone who can reach it can read and change your maps.\n"
                     "    Settings, browsing, shutdown and restart stay refused "
                     "to them; the maps do not.")


if __name__ == "__main__":
    raise SystemExit(main())
