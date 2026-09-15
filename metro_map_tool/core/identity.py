# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ERP-LAB-5
"""
identity.py — who this tool is, as far as the core needs to know.

Generated from .copier-answers.yml by the D-LAB-5 tool template. Change the
answer and run `copier update` rather than editing this file: it is core-owned,
and every other module under core/ reads its names from here so that those
modules come out byte-identical in every tool.
"""

TOOL_NAME = "metro-map"
PACKAGE = "metro_map_tool"
TITLE = "Metro map designer"
DESCRIPTION = "Transit-map diagrams for landscapes, pipelines and roadmaps, rendered to a standalone SVG"
AUTHOR = "ERP-LAB-5"
COPYRIGHT = "© 2026 ERP-LAB-5"
LICENCE = "GPL-3.0-or-later"
LICENCE_URL = "https://www.gnu.org/licenses/gpl-3.0.html"
DISCLAIMER = ""
COFFEE_URL = "https://www.buymeacoffee.com/dlab5"

GITHUB_ORG = "ERP-LAB-5"
REPO_NAME = "metro-map-tool"
REPO_URL = f"https://github.com/{GITHUB_ORG}/{REPO_NAME}"
DEFAULT_BRANCH = "main"

DEFAULT_PORT = 8765

# The template this tool was generated from, and the version last applied to
# it — the same value as _commit in .copier-answers.yml, which lives at the
# repository root and is not shipped with an installed copy, so it is written
# here too. A tag like "v0.4.0" is a published template release; anything with
# a commit suffix ("v0.4.0-2-g1a2b3c4") was rendered from an unreleased commit.
TEMPLATE_NAME = "python-tool-template"
TEMPLATE_URL = "https://github.com/ERP-LAB-5/python-tool-template"
TEMPLATE_VERSION = "v0.4.1"

CLI_COMMAND = "metro-map"
CLI_MODULE = "metro_map"
WEB_COMMAND = "metro-map-designer"
MCP_COMMAND = "metro-map-mcp"
SKILL_COMMAND = "metro-map-skill"
SKILL_NAME = "metro-map"

WITH_MCP = True
WITH_WORKSPACE = False
WITH_PLUGIN = True
WORKSPACE_DIR = ""
SHARED_DIR = ""
