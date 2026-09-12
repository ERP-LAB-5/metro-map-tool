# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
mcp_bridge.py — let an MCP server work through the tool's web server.

An agent and a person work through one server and one set of files, so a save
from either shows up in the other on the next read. The MCP server is therefore
a thin client of the HTTP API rather than a second implementation of it, and
this module is the client:

    from .core import mcp_bridge as web
    web.configure(port=args.port, autostart=True)
    web.call("GET", "/api/things")              # ensure_server() first
    web.add_core_tools(server)                  # server_url, stop_server

Nothing may be written to stdout here or anywhere in an MCP server: stdout is
the transport. log() goes to stderr.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from . import identity

BASE = f"http://127.0.0.1:{identity.DEFAULT_PORT}"
AUTOSTART = True


def configure(port: Optional[int] = None, url: Optional[str] = None,
              autostart: bool = True) -> None:
    global BASE, AUTOSTART
    BASE = (url or f"http://127.0.0.1:{port or identity.DEFAULT_PORT}").rstrip("/")
    AUTOSTART = autostart


def log(msg: str) -> None:
    print(f"  [{identity.TOOL_NAME}] {msg}", file=sys.stderr, flush=True)


def call(method: str, path: str, payload: Optional[dict] = None,
         timeout: float = 30) -> Any:
    """One request against the web server, with its error messages preserved."""
    body = json.dumps(payload).encode() if payload is not None else None
    # Name ourselves on every call. The web server records it, so the Agent
    # panel in the browser can say an agent is working here rather than guess:
    # a stdio server has no port to look for and no pid the page could find.
    headers = {"X-Agent": f"{identity.TOOL_NAME}-mcp"}
    if body:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=body, method=method,
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try:
            data = json.loads(detail)
            errors = data.get("errors") or [data.get("description") or detail]
        except ValueError:
            errors = [detail]
        raise ValueError("; ".join(str(e) for e in errors)) from None
    except urllib.error.URLError as exc:
        raise ConnectionError(
            f"{identity.TITLE} at {BASE} is not answering: {exc.reason}") from None


def alive() -> bool:
    """Something answers at BASE, and it is this tool rather than another one."""
    try:
        return (call("GET", "/api/health", timeout=3) or {}).get("tool") == identity.TOOL_NAME
    except (ConnectionError, ValueError, OSError):
        return False


def ensure_server() -> None:
    """Start the web server if nothing is answering, and wait for it to come up.

    It inherits this process's working directory, which is the project the
    agent was started in, so a user folder resolves there and not inside
    site-packages or a plugin cache.
    """
    if alive():
        return
    if not AUTOSTART:
        raise ConnectionError(f"nothing is running at {BASE} — start it with run.sh "
                              f"or {identity.WEB_COMMAND}")
    port = urllib.parse.urlparse(BASE).port or identity.DEFAULT_PORT
    log(f"nothing on {BASE} — starting {identity.WEB_COMMAND}")
    subprocess.Popen(
        [sys.executable, "-m", f"{identity.PACKAGE}.app", "--port", str(port)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(60):
        time.sleep(0.25)
        if alive():
            log(f"ready at {BASE}")
            return
    raise ConnectionError(f"started {identity.WEB_COMMAND} but {BASE} never answered")


def add_core_tools(server: Any) -> None:
    """The two tools every tool's MCP server has: where the page is, and stop it."""

    @server.tool()
    def server_url() -> str:
        """The URL to open the tool in a browser, so a person can take over."""
        ensure_server()
        return BASE + "/"

    @server.tool()
    def stop_server() -> str:
        """Shut the tool's local web server down."""
        if not alive():
            return "nothing was running"
        try:
            call("POST", "/api/shutdown")
        except ConnectionError:
            pass                              # it died mid-answer, which is the point
        return f"stopped {identity.TITLE} at {BASE}"
