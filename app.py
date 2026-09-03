#!/usr/bin/env python3
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

    python3 app.py                       # http://127.0.0.1:8765
    python3 app.py --port 9000 --maps-dir ~/maps --shared-maps-dir ./shared-maps

The rendering, geometry and validation all live in metro_map; this module only
moves specs between the browser and disk.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import tempfile
import threading
from datetime import timedelta
from pathlib import Path
from typing import Dict, Optional, Tuple

from flask import Flask, Response, abort, jsonify, render_template, request

import metro_map as mm

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024      # a spec is a few KB
app.config["JSON_SORT_KEYS"] = False

# Two folders, one namespace. mymaps is yours and git-ignored; shared is what
# the repo ships. Search order matters: an unqualified name finds mymaps first.
FOLDERS: Dict[str, Path] = {"mymaps": Path("mymaps").resolve(),
                            "shared": Path("shared-maps").resolve()}
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
    return spec


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


# ------------------------------------------------------------------ page ----

@app.get("/")
def index() -> str:
    return render_template("index.html")


# ------------------------------------------------------------------- api ----

@app.get("/api/maps")
def list_maps():
    out = []
    for folder, base in FOLDERS.items():        # mymaps first, then shared
        base.mkdir(parents=True, exist_ok=True)
        for path in sorted(base.glob("*.json")):
            entry = {"name": path.stem, "folder": folder,
                     "mtime": path.stat().st_mtime, "version": spec_version(path)}
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
                        "version": spec_version(path)})
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
    try:
        write_spec(path, spec)
    except OSError as exc:
        abort(500, f"could not write '{name}': {exc}")
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
    return jsonify({"name": name, "folder": folder, "deleted": True})


@app.post("/api/render")
def render_map():
    data = body()
    spec = data.get("spec")
    errors = mm.validate_spec(spec)
    if errors:
        return jsonify({"errors": errors}), 400
    changed = mm.auto_interchanges(spec) if data.get("auto_interchange", True) else 0
    if not spec["stations"]:
        return jsonify({"svg": "", "empty": True, "interchanges_changed": 0})
    style = mm.style_from(data.get("style") or spec.get("style"))
    try:
        svg = mm.render(spec, style)
    except (KeyError, ValueError, ZeroDivisionError) as exc:
        return jsonify({"errors": [f"render failed: {exc}"]}), 400
    out = {"svg": svg, "interchanges_changed": changed,
           "warnings": mm.spec_warnings(spec), "stations": spec["stations"]}
    # the resolved ruler, so the browser can name dates without redoing the maths
    tl = mm.spec_timeline(spec)
    if tl:
        out["timeline"] = timeline_payload(tl)
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
FOLDER_TITLES = {"mymaps": "My maps", "shared": "Shared"}

STATUS_TITLES = {
    "live": "In service",
    "out-of-service": "Out of service — dead end",
    "under-construction": "Under construction",
    "planned": "Planned",
}


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
                    "folders": [{"value": k, "label": FOLDER_TITLES.get(k, k)}
                                for k in FOLDERS],
                    "default_folder": DEFAULT_FOLDER})


@app.post("/api/shutdown")
def shutdown():
    """Stop the designer — the red button in the toolbar, and `mcp stop`.

    Local tool, local kill switch: only a loopback client may do it, and only
    over POST, so a page in another tab cannot navigate the server to death.
    """
    try:
        caller = ipaddress.ip_address(request.remote_addr or "")
    except ValueError:
        abort(403, "shutdown is only available to a local client")
    if not caller.is_loopback:
        abort(403, "shutdown is only available to a local client")

    # answer first, exit a beat later; werkzeug has no in-request shutdown hook
    threading.Timer(0.4, lambda: os._exit(0)).start()
    return jsonify({"stopping": True})


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(500)
def as_json(exc):
    return jsonify({"errors": [getattr(exc, "description", str(exc))]}), exc.code


# ------------------------------------------------------------------ main ----

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--maps-dir", default="mymaps",
                    help="your own maps (default: mymaps)")
    ap.add_argument("--shared-maps-dir", default="shared-maps",
                    help="maps that ship with the tool (default: shared-maps)")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    FOLDERS["mymaps"] = Path(args.maps_dir).expanduser().resolve()
    FOLDERS["shared"] = Path(args.shared_maps_dir).expanduser().resolve()
    for folder, base in FOLDERS.items():
        base.mkdir(parents=True, exist_ok=True)
        print(f"  {folder:<7} maps: {base}")
    print(f"  designer:       http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
