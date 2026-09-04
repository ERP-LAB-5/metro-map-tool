#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
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
import ipaddress
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Dict, Optional, Tuple

from flask import Flask, Response, abort, jsonify, render_template, request

from . import metro_map as mm

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024      # a spec is a few KB
app.config["JSON_SORT_KEYS"] = False

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


# ---------------------------------------------------------------- version ----
#
# The About box wants to say what is installed and what is current. "Current"
# is the VERSION file on main, fetched raw rather than through the GitHub API:
# the API allows 60 unauthenticated calls an hour per address, which a few
# designer restarts could plausibly spend, and raw.githubusercontent has no
# such budget. Nothing about the map, the machine or the user is sent.

# the same file this build reads for its own version, on main — so publishing a
# release is one edit, and the check cannot disagree with what was shipped
LATEST_URL = ("https://raw.githubusercontent.com/"
              "ERP-LAB-5/metro-map-tool/main/metro_map_tool/VERSION")
UPDATE_CHECK = True                 # --no-update-check turns it off
CHECK_EVERY = 6 * 60 * 60           # seconds; the answer changes rarely
_latest: Dict[str, object] = {"version": None, "at": 0.0, "error": None}


def version_tuple(text: str) -> Tuple[int, ...]:
    """A comparable version, tolerant of a leading v and of junk after it."""
    parts = re.findall(r"\d+", (text or "").strip().lstrip("vV"))[:4]
    return tuple(int(p) for p in parts) or (0,)


def fetch_latest() -> Optional[str]:
    """The published version, or None when we could not reach it.

    Any failure is a None: no network, DNS down, a proxy in the way, GitHub
    having a bad day. None of those are worth an error in the user's face.
    """
    import urllib.request           # local: the CLI path never needs it
    try:
        req = urllib.request.Request(
            LATEST_URL, headers={"User-Agent": f"metro-map-tool/{mm.__version__}"})
        with urllib.request.urlopen(req, timeout=4) as res:
            text = res.read(64).decode("utf-8", "replace").strip()
        return text if re.match(r"v?\d+(\.\d+)*$", text) else None
    except Exception:               # noqa: BLE001 - offline is not an error here
        return None


def latest_version(force: bool = False) -> Dict[str, object]:
    """Cached view of what is published, refreshed at most every CHECK_EVERY."""
    now = time.time()
    if not UPDATE_CHECK:
        return {"version": None, "checked": False, "disabled": True}
    if force or (now - float(_latest["at"] or 0)) > CHECK_EVERY:
        found = fetch_latest()
        # keep the last good answer if this attempt failed, rather than
        # flapping between "2.6.0 available" and "offline" on a flaky link
        if found or not _latest["version"]:
            _latest["version"] = found
        _latest["at"] = now
        _latest["error"] = None if found else "could not reach github.com"
    return {"version": _latest["version"], "checked": True,
            "error": _latest["error"], "disabled": False}


@app.get("/api/version")
def version_info():
    """Installed vs published, for the About box. Never fails."""
    installed = mm.__version__
    found = latest_version(force=request.args.get("force") == "1")
    latest = found.get("version")
    behind = bool(latest and version_tuple(latest) > version_tuple(installed))
    return jsonify({
        "installed": installed,
        "latest": latest,
        "update_available": behind,
        "checked": found.get("checked"),
        "disabled": found.get("disabled", False),
        "offline": bool(found.get("error")),
        "repo": mm.REPO_URL,
        "releases": f"{mm.REPO_URL}/releases",
        "install": install_kind(),
        "coffee": "https://www.buymeacoffee.com/dlab5",
    })


# ------------------------------------------------------------------ page ----

@app.get("/")
def index() -> str:
    return render_template("index.html")


@app.get("/favicon.ico")
def favicon():
    """Browsers ask for this by name whatever the page links to."""
    return app.send_static_file("favicon.svg")


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
    # stamp on the way out, so anything that saves through this server — the
    # designer, the MCP tools, a script — leaves a file that can be told apart
    spec["format"] = mm.SPEC_FORMAT
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


def only_local(what: str) -> None:
    """Local tool, local kill switch.

    Only a loopback client may stop or restart the designer, and only over POST,
    so a page in another tab cannot navigate the server to death.
    """
    try:
        caller = ipaddress.ip_address(request.remote_addr or "")
    except ValueError:
        abort(403, f"{what} is only available to a local client")
    if not caller.is_loopback:
        abort(403, f"{what} is only available to a local client")


@app.post("/api/shutdown")
def shutdown():
    """Stop the designer — the red button in the toolbar, and `mcp stop`."""
    only_local("shutdown")
    # answer first, exit a beat later; werkzeug has no in-request shutdown hook
    threading.Timer(0.4, lambda: os._exit(0)).start()
    return jsonify({"stopping": True})


def _reexec() -> None:
    """Replace this process with a fresh one, same arguments, same directory.

    execv rather than spawn-and-exit: the new server inherits the working
    directory, which is what mymaps is resolved against, and there is no window
    where two of them are alive fighting for the port.

    The listening socket has to be closed by hand first. Python marks its own
    descriptors non-inheritable, but werkzeug's does survive the exec here, and
    the replacement image then cannot bind: "Address already in use" on the port
    it just vacated. Everything above stdio goes, since the process is being
    replaced and the response was flushed a beat ago; stdout and stderr stay so
    the new server can still log.
    """
    try:
        os.closerange(3, 1024)
    except OSError:                      # nothing to close, or not permitted
        pass
    os.execv(sys.executable,
             [sys.executable, "-m", "metro_map_tool.app", *sys.argv[1:]])


def install_kind() -> str:
    """How this copy was installed, which decides how it can be updated.

    A checkout updates with git and has a working tree the user can see; an
    installed copy lives in site-packages, where pip is the only sane way in.
    """
    parts = Path(mm.__file__).resolve().parts
    return "installed" if any(
        d in parts for d in ("site-packages", "dist-packages")) else "checkout"


@app.post("/api/update")
def update():
    """Upgrade an installed copy in place. The caller restarts afterwards.

    Deliberately not automatic: pip rewrites the files this process is running
    from, so the new code only takes effect on a restart, and a restart into a
    half-finished install is worse than staying put. The output comes back
    whatever happens, because a failed upgrade is exactly when you want to read
    what pip said.
    """
    only_local("update")
    if install_kind() != "installed":
        return jsonify({
            "ok": False, "kind": "checkout",
            "output": "This is a source checkout, not an installed package. "
                      "Update it with:  git pull",
        }), 400

    want = latest_version().get("version")
    target = f"{mm.REPO_URL}@v{want}" if want else f"{mm.REPO_URL}@main"
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", f"git+{target}"]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        return jsonify({"ok": False, "kind": "installed",
                        "output": f"could not run pip: {exc}"}), 500

    tail = (done.stdout + done.stderr).strip().splitlines()
    return jsonify({"ok": done.returncode == 0, "kind": "installed",
                    "target": target,
                    "output": "\n".join(tail[-14:]) or "(pip said nothing)"})


@app.post("/api/restart")
def restart():
    """Start the designer again on the same port, picking up changed code."""
    only_local("restart")
    threading.Timer(0.4, _reexec).start()
    return jsonify({"restarting": True})


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(500)
def as_json(exc):
    return jsonify({"errors": [getattr(exc, "description", str(exc))]}), exc.code


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
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--maps-dir", default="mymaps",
                    help="your own maps (default: mymaps)")
    ap.add_argument("--shared-maps-dir", default=str(HERE / "shared-maps"),
                    help="maps that ship with the tool (default: the installed set)")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--no-update-check", action="store_true",
                    help="never contact github.com to compare versions")
    ap.add_argument("--version", action="version",
                    version=f"metro-map-tool {mm.__version__}")
    args = ap.parse_args()

    global UPDATE_CHECK
    UPDATE_CHECK = not args.no_update_check

    FOLDERS["mymaps"] = Path(args.maps_dir).expanduser().resolve()
    FOLDERS["shared"] = Path(args.shared_maps_dir).expanduser().resolve()
    for folder, base in FOLDERS.items():
        base.mkdir(parents=True, exist_ok=True)
        print(f"  {folder:<7} maps: {base}")
    warn_legacy_maps()
    print(f"  version:        {mm.__version__}"
          + ("" if UPDATE_CHECK else "  (update check off)"))
    print(f"  designer:       http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
