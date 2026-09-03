"""metro-map-tool — transit-map diagrams rendered to a standalone SVG.

The package holds the renderer (metro_map), the browser designer (app) and the
MCP server (mcp_server), plus the designer's templates and static files and the
maps that ship with the tool. Keeping them in a package rather than loose at the
repository root is what lets `pip install` carry the templates along; Flask
resolves them relative to app.py, which now travels with them.
"""

from pathlib import Path

REPO_URL = "https://github.com/ERP-LAB-5/metro-map-tool"


def _read_version() -> str:
    try:
        return (Path(__file__).with_name("VERSION")
                .read_text(encoding="utf-8").strip()) or "0.0.0"
    except OSError:
        return "0.0.0"


__version__ = _read_version()
