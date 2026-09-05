#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
http.py — the small, careful JSON client the network sources share.

Careful about three things in particular:

  * a credential goes in a header, never in a URL — URLs end up in logs, in
    proxy records and in exception text, and a token in one of those is a token
    to rotate;
  * an error says the status and the reason and *not* the body, because a
    response body is the fastest way to paste something confidential into a
    terminal someone screenshots;
  * paging stops on a page cap as well as a count, so a mistyped option asks a
    server for a thousand pages once and never again.
"""

from __future__ import annotations

import json
from typing import Callable, Dict, List, Optional

from . import SourceError

TIMEOUT = 20
MAX_PAGES = 40


def need(name: str, why: str) -> str:
    """One environment variable, or a sentence telling the user what to do.

    The value is returned to the caller and never stored, logged or put in a
    spec — this and env_status are the only two places that read it.
    """
    import os
    value = os.environ.get(name, "").strip()
    if not value:
        raise SourceError(f"{name} is not set — {why}")
    return value


def get_json(url: str, headers: Dict[str, str], *, timeout: int = TIMEOUT):
    """One GET, decoded. Raises SourceError with a readable sentence."""
    import urllib.error                       # local: the renderer never needs it
    import urllib.request

    req = urllib.request.Request(url, headers=dict(headers))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        hint = {401: " — check the credentials in your environment",
                403: " — the token may lack the scope this needs, or you are "
                     "rate limited",
                404: " — check the project or repository name"}.get(exc.code, "")
        # deliberately not exc.read(): a body can carry more than it should
        raise SourceError(f"{_safe(url)} returned {exc.code} {exc.reason}{hint}") \
            from None
    except urllib.error.URLError as exc:
        raise SourceError(f"could not reach {_safe(url)}: {exc.reason}") from None
    except ValueError:
        raise SourceError(f"{_safe(url)} did not return JSON") from None


def paged(url_for: Callable[[int], str], headers: Dict[str, str], limit: int,
          *, per_page: int = 100) -> List[dict]:
    """Follow pages until the limit, an empty page, or the page cap."""
    out: List[dict] = []
    for page in range(1, MAX_PAGES + 1):
        batch = get_json(url_for(page), headers)
        if not isinstance(batch, list) or not batch:
            break
        out.extend(batch)
        if len(out) >= limit or len(batch) < per_page:
            break
    return out[:limit]


def _safe(url: str) -> str:
    """A URL fit to print: the query string is dropped, in case it says too much."""
    return url.split("?", 1)[0]
