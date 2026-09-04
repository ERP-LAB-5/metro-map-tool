#!/usr/bin/env python3
"""
mcp_server.py — MCP access to the metro-map designer.

Exposes the designer's HTTP API as MCP tools over stdio, so an agent can list,
read, write, validate and render maps while a human has the same maps open in
the browser. Both sides talk to one Flask server and one maps directory, so a
save from either shows up in the other on the next read.

    metro-map-mcp                               # talks to 127.0.0.1:8765
    metro-map-mcp --port 9000
    metro-map-mcp --no-autostart                # fail instead of starting one
    python3 -m metro_map_tool.mcp_server        # the same, from a checkout

The designer is started automatically if it is not already answering. Register
it with an agent as a stdio server, for example in .mcp.json:

    {"mcpServers": {"metro-map": {
        "command": "/path/to/metro-map-tool/.venv/bin/python",
        "args": ["-m", "metro_map_tool.mcp_server"]}}}

Nothing may be written to stdout: that is the MCP transport. Log to stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

from mcp.server.mcpserver import MCPServer

HERE = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:8765"
AUTOSTART = True

server = MCPServer(
    name="metro-map",
    instructions=(
        "Design transit-map style diagrams — stations on a grid, lines through "
        "them, zones banding groups of stations — and render them to SVG. "
        "Work in this order: place stations first, then route lines through "
        "them, then band zones. read_map/save_map move whole specs; call "
        "validate_map before saving anything you assembled by hand. "
        "Maps live in two folders: 'mymaps' (the user's own, where saves go by "
        "default) and 'shared' (what the repo ships). Set mode to 'roadmap' "
        "with a timeline block to turn the x axis into a calendar; 'legend' "
        "names the lines on the drawing, and a line's 'notes' label the track "
        "between two of its stops."
    ),
)


# ------------------------------------------------------------------ http ----

def log(msg: str) -> None:
    print(f"  [metro-map] {msg}", file=sys.stderr, flush=True)


def call(method: str, path: str, payload: Optional[dict] = None) -> Any:
    """One request against the designer, with its error messages preserved."""
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path, data=body, method=method,
        headers={"Content-Type": "application/json"} if body else {})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.loads(res.read() or b"null")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        try:
            errors = json.loads(detail).get("errors") or [detail]
        except ValueError:
            errors = [detail]
        raise ValueError("; ".join(str(e) for e in errors)) from None
    except urllib.error.URLError as exc:
        raise ConnectionError(f"the designer at {BASE} is not answering: {exc.reason}") from None


def alive() -> bool:
    try:
        call("GET", "/api/maps")
        return True
    except (ConnectionError, ValueError):
        return False


def ensure_designer() -> None:
    """Start the designer if nothing is answering, and wait for it to come up."""
    if alive():
        return
    if not AUTOSTART:
        raise ConnectionError(f"nothing is running at {BASE} — start it with run.sh")
    port = urllib.parse.urlparse(BASE).port or 8765
    log(f"no designer on {BASE} — starting one")
    subprocess.Popen(
        [sys.executable, "-m", "metro_map_tool.app", "--port", str(port)],
        cwd=HERE.parent, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(40):
        time.sleep(0.25)
        if alive():
            log(f"designer ready at {BASE}")
            return
    raise ConnectionError(f"started a designer but {BASE} never answered")


# ----------------------------------------------------------------- tools ----

def qualify(name: str, folder: str) -> str:
    """A map URL, with the folder as a query when the caller pinned one."""
    path = f"/api/maps/{urllib.parse.quote(name)}"
    return f"{path}?folder={urllib.parse.quote(folder)}" if folder else path


@server.tool()
def list_maps() -> list[dict]:
    """Every saved map, with its folder, mode and station/line/zone counts."""
    ensure_designer()
    return call("GET", "/api/maps")


@server.tool()
def read_map(name: str, folder: str = "") -> dict:
    """Read one saved map's spec: stations, lines, zones, style.

    folder is 'mymaps' or 'shared'; left empty, mymaps is searched first.
    """
    ensure_designer()
    return call("GET", qualify(name, folder))["spec"]


@server.tool()
def save_map(name: str, spec: dict, auto_interchange: bool = True,
             folder: str = "") -> dict:
    """Write a spec to <folder>/<name>.json, replacing it if it exists.

    folder is 'mymaps' (git-ignored, the user's own work) or 'shared' (maps that
    belong to the repo). Left empty it updates the map where it already lives,
    and a name that exists in neither folder is created in mymaps — so pass a
    folder only to move a map or to put a new one somewhere other than mymaps.

    The spec is validated first and rejected with a list of problems if it will
    not draw. With auto_interchange on, any stop two or more lines share is
    flagged as an interchange. Returns the spec as saved, plus any warnings.
    """
    ensure_designer()
    payload = {"spec": spec, "auto_interchange": auto_interchange}
    if folder:
        payload["folder"] = folder
    out = call("PUT", f"/api/maps/{urllib.parse.quote(name)}", payload)
    return {"name": out["name"], "folder": out.get("folder"), "saved": True,
            "warnings": out.get("warnings", []), "spec": out["spec"]}


@server.tool()
def delete_map(name: str, folder: str = "") -> dict:
    """Delete a saved map."""
    ensure_designer()
    return call("DELETE", qualify(name, folder))


@server.tool()
def render_map(name: str = "", spec: Optional[dict] = None,
               out_path: str = "", folder: str = "") -> dict:
    """Render a saved map (by name) or a spec you pass, to SVG.

    Give out_path to write the SVG to a file and get the path back instead of
    the markup — better than carrying tens of kilobytes through the transcript.
    """
    ensure_designer()
    if not spec:
        if not name:
            raise ValueError("pass either name or spec")
        spec = read_map(name, folder)
    out = call("POST", "/api/render", {"spec": spec})
    svg = out.get("svg", "")
    result: dict[str, Any] = {"warnings": out.get("warnings", []),
                              "bytes": len(svg)}
    if out_path:
        path = Path(out_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(svg, encoding="utf-8")
        result["written_to"] = str(path)
    else:
        result["svg"] = svg
    return result


@server.tool()
def validate_map(spec: dict) -> dict:
    """Check a spec without saving it.

    errors stop it drawing at all; warnings are things worth knowing — a line
    with fewer than two stops, an empty zone, a station on no line.
    """
    ensure_designer()
    try:
        out = call("POST", "/api/render", {"spec": spec})
    except ValueError as exc:
        return {"ok": False, "errors": str(exc).split("; "), "warnings": []}
    return {"ok": True, "errors": [], "warnings": out.get("warnings", [])}


@server.tool()
def spec_reference() -> dict:
    """The vocabulary a spec may use: label sides and angles, line states, palette."""
    ensure_designer()
    defaults = call("GET", "/api/defaults")
    return {"label_sides": defaults["label_sides"],
            "label_angles": defaults["label_angles"],
            "line_statuses": defaults["line_statuses"],
            "modes": defaults["modes"],
            "intervals": defaults["intervals"],
            "folders": defaults["folders"],
            "legend_positions": defaults["legend_positions"],
            "dead_ends": defaults["dead_ends"],
            "palette": call("GET", "/api/palette"),
            "style_defaults": defaults["style"],
            "grid": "gx/gy are grid cells and may be fractional (0.5 puts two "
                    "stations half a cell apart); segments are drawn octilinear",
            "roadmap": 'set "mode": "roadmap" and a "timeline" of '
                       '{start, end, interval} to make gx a calendar: column k '
                       "covers gx in [k, k+1), so a whole gx is a period "
                       "boundary. resolve_timeline turns dates into gx",
            "legend": '"legend" names the lines on the drawing: hide, top, '
                      "left, bottom or right. It defaults to bottom, so leave "
                      'it out unless the map wants it elsewhere or off',
            "interchanges": 'a stretched interchange: {"stations": [ids], '
                            '"label"?, "label_at"?, "label_angle"?}. One capsule '
                            "covers the stops listed, replacing their own "
                            "markers — use it for a milestone that lands on "
                            "several parallel lines at once. A label on the "
                            "group replaces the labels of the stops it covers",
            "phases": 'roadmap only: {"name", "from", "to", "color"?} draws a '
                      "grey column behind everything between two dates, named "
                      "vertically — the Preparation/Test/Go-to-market bands",
            "axis": 'timeline.axis is "top" (default) or "bottom" — which side '
                    "of the map the dates run along",
            "dead_end": 'a station may carry "dead_end": "buffer" for a '
                        'terminus bar, or "fire" for that bar with flames off '
                        "it — the burning platform, for a branch that ends "
                        "badly. The marker needs the stop to be on a line",
            "notes": 'a line may carry "notes": [{"at": <hop>, "text": str, '
                     '"flip": bool?}] — a short label riding the track between '
                     "two stops. 'at' is the hop index, so 0 is the gap "
                     "between stations[0] and stations[1], and the last legal "
                     "value is len(stations) - 2"}


@server.tool()
def resolve_timeline(timeline: dict) -> dict:
    """Turn a roadmap timeline block into the columns it will draw.

    Returns the snapped start (a timeline starts at the beginning of the period
    holding its start date), the closing boundary, the column count, and the gx
    and name of every column — so a milestone can be placed on the right date
    without guessing at the arithmetic.
    """
    ensure_designer()
    return call("POST", "/api/timeline", {"timeline": timeline})


@server.tool()
def designer_url() -> str:
    """The URL to open the designer in a browser, so a person can take over."""
    ensure_designer()
    return BASE + "/"


@server.tool()
def stop_designer() -> str:
    """Shut the designer's local server down."""
    if not alive():
        return "nothing was running"
    try:
        call("POST", "/api/shutdown")
    except ConnectionError:
        pass                                  # it died mid-answer, which is the point
    return f"stopped the designer at {BASE}"


# ------------------------------------------------------------------ main ----

def main() -> int:
    global BASE, AUTOSTART
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--url", help="designer base URL (overrides --port)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-autostart", action="store_true",
                    help="fail rather than starting a designer that is not running")
    args = ap.parse_args()

    BASE = (args.url or f"http://127.0.0.1:{args.port}").rstrip("/")
    AUTOSTART = not args.no_autostart
    log(f"serving MCP over stdio, designer at {BASE}")
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
