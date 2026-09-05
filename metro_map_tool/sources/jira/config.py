#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
config.py — the Jira plugin's own settings.

The credentials themselves are handled by the shared machinery in
sources/__init__.py, because how a secret is stored should not be up to each
plugin. What lives here is the rest: the field ids worked out for this site,
cached so they are discovered once rather than on every run.
"""

from __future__ import annotations

from typing import Dict

from .. import read_config, write_config

NAME = "jira"
FIELD_PREFIX = "field_"


def remembered_fields() -> Dict[str, str]:
    """Field ids worked out on a previous run."""
    saved = read_config(NAME)
    return {k[len(FIELD_PREFIX):]: v for k, v in saved.items()
            if k.startswith(FIELD_PREFIX) and v}


def remember_fields(found: Dict[str, str]) -> None:
    """Save what was discovered, so the next run does not ask again.

    Written next to the credentials on purpose: it is site-specific knowledge,
    it is worthless without them, and a second file would be a second thing to
    find when something is wrong.
    """
    if not found:
        return
    if remembered_fields() == found:
        return                                  # nothing to say, nothing to write
    write_config(NAME, {f"{FIELD_PREFIX}{k}": v for k, v in found.items()})
