#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
client.py — a small Jira REST client, in the standard library and nothing else.

Deliberately not the `jira` package. This tool renders diagrams; taking a
dependency (and its dependencies) so that one importer can call four endpoints
would be a poor trade, and every other source here already reaches the network
with urllib.

Everything goes through one `request`, which is where a write guard belongs
when writing lands. Today that method refuses anything but GET — not because a
caller is expected to try, but because "read-only" should be a property of the
code rather than of everyone remembering.
"""

from __future__ import annotations

import json
import urllib.parse
from typing import Dict, List, Optional

from .. import SourceError
from .. import http

READ_ONLY = ("GET",)


class Jira:
    """One authenticated conversation with a Jira site."""

    def __init__(self, base_url: str, email: str, api_token: str,
                 timeout: int = http.TIMEOUT):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self._headers = self._auth(email, api_token)
        self._seen: Dict[str, object] = {}      # per-instance, per-run cache

    @staticmethod
    def _auth(email: str, api_token: str) -> Dict[str, str]:
        import base64
        pair = base64.b64encode(f"{email}:{api_token}".encode()).decode()
        # the header, never the query string: a URL ends up in logs and in
        # exception text, and a token in either is a token to rotate
        return {"Authorization": f"Basic {pair}",
                "Accept": "application/json",
                "User-Agent": "metro-map-tool"}

    def request(self, method: str, path: str, params: Optional[dict] = None):
        """The one door. Writes are not wired: see the module docstring."""
        if method not in READ_ONLY:
            raise SourceError(
                f"the Jira plugin is read-only — it will not {method} to "
                f"{self.site}. Changing tickets from a map is a separate "
                "feature with its own confirmation, and it is not built yet.")
        url = f"{self.base}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(
                {k: v for k, v in params.items() if v not in (None, "")})
        return http.get_json(url, self._headers, timeout=self.timeout)

    def get(self, path: str, params: Optional[dict] = None):
        return self.request("GET", path, params)

    def cached(self, path: str, params: Optional[dict] = None):
        """A GET whose answer does not change during one run.

        The field list and the issue-type list are asked for by several things
        that do not know about each other; asking a corporate Jira four times
        for the same 300-field list is rude and slow.
        """
        key = path + json.dumps(params or {}, sort_keys=True)
        if key not in self._seen:
            self._seen[key] = self.get(path, params)
        return self._seen[key]

    @property
    def site(self) -> str:
        """The host, for saying where something came from. Never the URL with
        credentials or a query string on it."""
        return urllib.parse.urlsplit(self.base).netloc or self.base

    # -- the handful of reads the plugin actually makes --------------------

    def myself(self) -> dict:
        return self.get("/rest/api/3/myself")

    def projects(self, query: str = "", limit: int = 2000) -> List[dict]:
        """Every project you can see, or the ones matching a search term.

        Paged to the end rather than to a fixed number of pages: a corporate
        Jira has hundreds of projects, and a list that quietly stops at a round
        number is worse than no list — it looks complete.

        `query` is Jira's own search, which matches a project's key or name and
        narrows the result server-side. It is a substring match; anything more
        particular is applied to what comes back.
        """
        out: List[dict] = []
        start = 0
        for _ in range(http.MAX_PAGES):
            page = self.get("/rest/api/3/project/search",
                            {"startAt": start, "maxResults": 50,
                             "orderBy": "key", "query": query or None})
            values = page.get("values") or []
            out.extend(values)
            start += len(values)
            if page.get("isLast", True) or not values or len(out) >= limit:
                break
        return out[:limit]

    def fields(self) -> List[dict]:
        found = self.cached("/rest/api/3/field")
        return found if isinstance(found, list) else []

    def search(self, jql: str, fields: str, limit: int = 200) -> List[dict]:
        out: List[dict] = []
        for start in range(0, limit, 100):
            page = self.get("/rest/api/3/search",
                            {"jql": jql, "startAt": start,
                             "maxResults": min(100, limit - start),
                             "fields": fields})
            batch = page.get("issues") or []
            out.extend(batch)
            if len(batch) < 100:
                break
        return out[:limit]

    def versions(self, project: str) -> List[dict]:
        found = self.get(
            f"/rest/api/3/project/{urllib.parse.quote(project)}/versions")
        return found if isinstance(found, list) else []

    def boards(self, project: str) -> List[dict]:
        """Agile boards. A site with no Jira Software has no /agile at all, and
        that is a missing feature rather than an error worth stopping for."""
        try:
            page = self.get("/rest/agile/1.0/board",
                            {"projectKeyOrId": project, "maxResults": 50})
        except SourceError:
            return []
        return page.get("values") or []

    def sprints(self, board_id: str) -> List[dict]:
        try:
            page = self.get(f"/rest/agile/1.0/board/{board_id}/sprint",
                            {"maxResults": 50})
        except SourceError:
            return []
        return page.get("values") or []


def connect(creds: Dict[str, str]) -> Jira:
    """A client from resolved credentials, whatever supplied them."""
    missing = [k for k in ("base_url", "email", "api_token") if not creds.get(k)]
    if missing:
        raise SourceError("jira is missing " + ", ".join(missing))
    return Jira(creds["base_url"], creds["email"], creds["api_token"])
