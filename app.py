#!/usr/bin/env python3
"""
app.py — browser front end for metro_map.py.

Serves a design workspace: place stations on a grid by dragging them on a live
canvas, then build lines by picking from the stations already placed. Maps are
plain metro_map specs, loaded from and saved to a directory of JSON files
(default ./maps), so anything designed here still renders from the command line.

    python3 app.py                       # http://127.0.0.1:8765
    python3 app.py --port 9000 --maps-dir ~/maps

The rendering, geometry and validation all live in metro_map; this module only
moves specs between the browser and disk.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import tempfile
import threading
from pathlib import Path

from flask import Flask, Response, abort, jsonify, render_template, request

import metro_map as mm

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024      # a spec is a few KB
app.config["JSON_SORT_KEYS"] = False

MAPS_DIR = Path("maps").resolve()
NAME_RE = re.compile(r"[A-Za-z0-9 _-]{1,64}$")


# ----------------------------------------------------------------- paths ----

def map_path(name: str) -> Path:
    """Resolve a map name to a file inside MAPS_DIR, or refuse it."""
    if not NAME_RE.match(name or ""):
        abort(400, "map name may only contain letters, digits, space, - and _")
    path = (MAPS_DIR / f"{name}.json").resolve()
    if path.parent != MAPS_DIR:            # belt and braces after the name check
        abort(400, "map name escapes the maps directory")
    return path


def read_spec(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        spec = json.load(fh)
    spec.setdefault("stations", {})
    spec.setdefault("lines", [])
    spec.setdefault("zones", [])
    return spec


def write_spec(path: Path, spec: dict) -> None:
    """Atomic write, so a crash mid-save never truncates an existing map."""
    MAPS_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=MAPS_DIR, prefix=".tmp-", suffix=".json")
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
    MAPS_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for path in sorted(MAPS_DIR.glob("*.json")):
        entry = {"name": path.stem, "mtime": path.stat().st_mtime}
        try:
            spec = read_spec(path)
            entry["stations"] = len(spec["stations"])
            entry["lines"] = len(spec["lines"])
            entry["zones"] = len(spec["zones"])
        except (OSError, ValueError):
            entry["error"] = "not readable as a spec"
        out.append(entry)
    return jsonify(out)


@app.get("/api/maps/<name>")
def get_map(name: str):
    path = map_path(name)
    if not path.exists():
        abort(404, f"no map called '{name}'")
    try:
        return jsonify({"name": name, "spec": read_spec(path)})
    except (OSError, ValueError) as exc:
        abort(400, f"could not read '{name}': {exc}")


@app.put("/api/maps/<name>")
def put_map(name: str):
    path = map_path(name)
    data = body()
    spec = data.get("spec")
    errors = mm.validate_spec(spec)
    if errors:
        return jsonify({"errors": errors}), 400
    if data.get("auto_interchange", True):
        mm.auto_interchanges(spec)
    try:
        write_spec(path, spec)
    except OSError as exc:
        abort(500, f"could not write '{name}': {exc}")
    return jsonify({"name": name, "spec": spec, "saved": True,
                    "warnings": mm.spec_warnings(spec)})


@app.delete("/api/maps/<name>")
def delete_map(name: str):
    path = map_path(name)
    if not path.exists():
        abort(404, f"no map called '{name}'")
    try:
        path.unlink()
    except OSError as exc:
        abort(500, f"could not delete '{name}': {exc}")
    return jsonify({"name": name, "deleted": True})


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
    return jsonify({"svg": svg, "interchanges_changed": changed,
                    "warnings": mm.spec_warnings(spec),
                    "stations": spec["stations"]})


@app.get("/api/maps/<name>/svg")
def download_svg(name: str):
    path = map_path(name)
    if not path.exists():
        abort(404, f"no map called '{name}'")
    spec = read_spec(path)
    errors = mm.validate_spec(spec)
    if errors:
        return jsonify({"errors": errors}), 400
    svg = mm.render(spec, mm.style_from(spec.get("style")))
    return Response(svg, mimetype="image/svg+xml", headers={
        "Content-Disposition": f'attachment; filename="{name}.svg"'})


@app.get("/api/templates")
def templates():
    return jsonify([
        {"id": "sap", "name": "SAP landscape",
         "note": "ECC to RISE options, BTP, data and archive branches",
         "spec": mm.SAP_TEMPLATE},
        {"id": "pipeline", "name": "Diagram pipeline",
         "note": "authoring, import, repurpose and analyse tracks",
         "spec": mm.PIPELINE_TEMPLATE},
        {"id": "empty", "name": "Empty map",
         "note": "start from nothing", "spec": mm.empty_spec()},
    ])


@app.get("/api/palette")
def palette():
    return jsonify([{"name": n, "color": c} for n, c in mm.PALETTE])


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
                    "label_angles": list(mm.LABEL_ANGLES)})


@app.get("/api/ascii")
def ascii_grid():
    """The terminal-style grid — cheap for an agent to read back."""
    name = request.args.get("map")
    if name:
        path = map_path(name)
        if not path.exists():
            abort(404, f"no map called '{name}'")
        spec = read_spec(path)
    else:
        abort(400, "pass ?map=<name>")
    errors = mm.validate_spec(spec)
    if errors:
        return jsonify({"errors": errors}), 400
    return jsonify({"name": name, "grid": mm.ascii_preview(spec),
                    "warnings": mm.spec_warnings(spec)})


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
    global MAPS_DIR
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--maps-dir", default="maps")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    MAPS_DIR = Path(args.maps_dir).expanduser().resolve()
    MAPS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"  maps directory: {MAPS_DIR}")
    print(f"  designer:       http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
