# SPDX-License-Identifier: GPL-3.0-or-later
"""The MCP server's own wiring, with no designer and no network.

These exist because of a real escape: moving the HTTP bridge into core/ took
`import urllib.parse` and `from pathlib import Path` with it, and three tools —
save_map, read_map, delete_map — raised NameError the moment they were called.
The whole suite stayed green, because nothing here had ever imported this
module. A tool that cannot be called is not covered by a test of the renderer.

So: import it, check every tool is registered, and exercise the two helpers
that use those imports. None of this needs a server; the bridge is stubbed
where a call would otherwise go out.
"""

import unittest
from unittest import mock

from metro_map_tool import mcp_server as ms

TOOLS = ("list_maps", "read_map", "save_map", "delete_map", "render_map",
         "validate_map", "list_sources", "browse_source", "import_map",
         "spec_reference", "resolve_timeline", "designer_url", "stop_designer")


class ToolSurfaceTest(unittest.TestCase):
    def test_every_tool_is_present(self):
        for name in TOOLS:
            self.assertTrue(callable(getattr(ms, name, None)), name)

    def test_the_server_is_named_for_the_tool(self):
        self.assertEqual(ms.server.name, "metro-map")


class NameQuotingTest(unittest.TestCase):
    """qualify needs urllib.parse; a map name may contain a space."""

    def test_a_name_with_a_space_is_quoted(self):
        self.assertEqual(ms.qualify("my map", ""), "/api/maps/my%20map")

    def test_a_folder_arrives_as_a_query(self):
        self.assertEqual(ms.qualify("m", "shared"), "/api/maps/m?folder=shared")


class RenderToFileTest(unittest.TestCase):
    """render_map's out_path branch needs pathlib.Path."""

    def test_writing_the_svg_returns_where_it_went(self):
        import tempfile
        from pathlib import Path as RealPath
        with tempfile.TemporaryDirectory() as tmp:
            out = RealPath(tmp) / "deep" / "map.svg"
            with mock.patch.object(ms.web, "ensure_server"), \
                 mock.patch.object(ms, "call", return_value={"svg": "<svg/>",
                                                             "warnings": []}):
                result = ms.render_map(spec={"stations": {}}, out_path=str(out))
            self.assertEqual(result["written_to"], str(out))
            self.assertEqual(out.read_text(encoding="utf-8"), "<svg/>")
            self.assertNotIn("svg", result)          # the markup stayed out

    def test_without_out_path_the_markup_comes_back(self):
        with mock.patch.object(ms.web, "ensure_server"), \
             mock.patch.object(ms, "call", return_value={"svg": "<svg/>",
                                                         "warnings": []}):
            result = ms.render_map(spec={"stations": {}})
        self.assertEqual(result["svg"], "<svg/>")


class SaveGoesThroughTheBridgeTest(unittest.TestCase):
    """save_map quotes the name and PUTs it — the call that raised NameError."""

    def test_it_puts_to_the_quoted_path(self):
        seen = {}

        def fake_call(method, path, payload=None):
            seen.update(method=method, path=path, payload=payload)
            # save_map hands back the spec as saved, so the stub has to carry
            # one: a response without it is not a response the tool can read.
            return {"name": "my map", "folder": "mymaps",
                    "spec": {"stations": {}}, "warnings": []}

        def routed(method, path, payload=None):
            if method == "GET" and path == "/api/maps":
                return []                       # a new map: nothing to replace
            return fake_call(method, path, payload)

        with mock.patch.object(ms.web, "ensure_server"), \
             mock.patch.object(ms, "call", side_effect=routed):
            ms.save_map("my map", {"stations": {}})
        self.assertEqual(seen["method"], "PUT")
        self.assertEqual(seen["path"], "/api/maps/my%20map")
        self.assertEqual(seen["payload"]["auto_interchange"], True)


MAP = {"stations": {"a": {"label": "A", "gx": 0, "gy": 0},
                    "b": {"label": "B", "gx": 2, "gy": 0}},
       "lines": [{"name": "One", "color": "#0098d4", "stations": ["a", "b"]}]}


class SharedEditingTest(unittest.TestCase):
    """An agent and a person on one map: the agent must not eat the person's save.

    The bridge is pointed at the real designer app, with its maps in a temp
    directory, so the version check that refuses a stale save is the server's
    own — not a stub's idea of it.
    """

    def setUp(self):
        import copy
        import json
        import tempfile
        from pathlib import Path
        from metro_map_tool import app as designer
        self.copy = copy.deepcopy
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        folders = {"mymaps": root / "mymaps", "shared": root / "shared"}
        for d in folders.values():
            d.mkdir()
        self.designer = designer
        self.patches = [mock.patch.object(designer, "FOLDERS", folders),
                        mock.patch.object(ms.web, "ensure_server"),
                        mock.patch.object(ms, "call", side_effect=self.agent),
                        mock.patch.dict(ms._seen, clear=True),
                        mock.patch.dict(ms._locked, clear=True),
                        mock.patch.dict(designer._locks, clear=True)]
        for patch in self.patches:
            patch.start()
        designer.app.config["TESTING"] = True
        self.client = designer.app.test_client()
        self.json = json

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.tmp.cleanup()

    def bridge(self, method, path, payload=None, agent=False):
        """What core's call does: JSON in, JSON out, errors as a ValueError."""
        headers = {"X-Agent": "metro-map-mcp"} if agent else {}
        res = self.client.open(path, method=method, json=payload, headers=headers,
                               environ_base={"REMOTE_ADDR": "127.0.0.1"})
        data = res.get_json(silent=True)
        if res.status_code >= 400:
            raise ValueError("; ".join((data or {}).get("errors") or [str(data)]))
        return data

    def agent(self, method, path, payload=None):
        """The MCP server's calls, which name themselves as core's bridge does."""
        return self.bridge(method, path, payload, agent=True)

    def person_saves(self, label, take_over=True):
        """The designer: take the map over if an agent holds it, then save an edit."""
        now = self.bridge("GET", "/api/maps/plan")
        if take_over and now.get("lock"):
            self.bridge("DELETE", "/api/maps/plan/lock")
        spec = self.copy(now["spec"])
        spec["stations"]["a"]["label"] = label
        return self.bridge("PUT", "/api/maps/plan",
                           {"spec": spec, "base_version": now["version"]})

    def lock_on_disk(self):
        entry = next(m for m in self.bridge("GET", "/api/maps") if m["name"] == "plan")
        return entry["lock"]

    def label_on_disk(self):
        return self.bridge("GET", "/api/maps/plan")["spec"]["stations"]["a"]["label"]

    def test_a_save_made_in_the_designer_after_the_agent_read_is_not_overwritten(self):
        ms.save_map("plan", self.copy(MAP))
        mine = ms.read_map("plan")
        self.person_saves("Edited by a person")
        mine["stations"]["b"]["label"] = "Edited by the agent"
        with self.assertRaises(ValueError) as caught:
            ms.save_map("plan", mine)
        self.assertIn("read_map", str(caught.exception))
        self.assertEqual(self.label_on_disk(), "Edited by a person")

    def test_a_person_who_saves_without_the_designer_still_wins_the_version_check(self):
        # a script or another tab that ignores the lock: the lock was released
        # some other way, and the version check is what is left
        ms.save_map("plan", self.copy(MAP))
        mine = ms.read_map("plan")
        self.bridge("DELETE", "/api/maps/plan/lock", agent=True)
        ms._locked.clear()
        self.person_saves("Edited by a person")
        with self.assertRaises(ValueError) as caught:
            ms.save_map("plan", mine)
        self.assertIn("changed after you read it", str(caught.exception))

    def test_reading_again_and_reapplying_then_goes_through(self):
        ms.save_map("plan", self.copy(MAP))
        ms.read_map("plan")
        self.person_saves("Edited by a person")
        again = ms.read_map("plan")
        again["stations"]["b"]["label"] = "Edited by the agent"
        ms.save_map("plan", again)
        spec = self.bridge("GET", "/api/maps/plan")["spec"]
        self.assertEqual(spec["stations"]["a"]["label"], "Edited by a person")
        self.assertEqual(spec["stations"]["b"]["label"], "Edited by the agent")

    def test_the_agents_own_save_is_the_base_for_its_next_one(self):
        ms.save_map("plan", self.copy(MAP))
        first = self.copy(MAP)
        first["stations"]["a"]["label"] = "one"
        ms.save_map("plan", first)
        first["stations"]["a"]["label"] = "two"
        ms.save_map("plan", first)
        self.assertEqual(self.label_on_disk(), "two")

    def test_a_map_never_read_is_not_replaced_blind(self):
        self.bridge("PUT", "/api/maps/plan", {"spec": self.copy(MAP)})
        self.person_saves("Somebody's work")
        with self.assertRaises(ValueError) as caught:
            ms.save_map("plan", self.copy(MAP))
        self.assertIn("has not been read", str(caught.exception))
        self.assertEqual(self.label_on_disk(), "Somebody's work")

    def test_replace_is_the_deliberate_way_to_throw_it_away(self):
        self.bridge("PUT", "/api/maps/plan", {"spec": self.copy(MAP)})
        self.person_saves("Somebody's work")
        ms.save_map("plan", self.copy(MAP), replace=True)
        self.assertEqual(self.label_on_disk(), "A")

    def test_a_new_map_needs_no_read(self):
        ms.save_map("fresh", self.copy(MAP))
        self.assertEqual(self.bridge("GET", "/api/maps/fresh")["name"], "fresh")

    def test_an_import_into_a_map_counts_as_reading_it(self):
        ms.save_map("plan", self.copy(MAP))
        ms._seen.clear()                        # a new session
        version = self.bridge("GET", "/api/maps/plan")["version"]
        with mock.patch.object(ms, "call", return_value={
                "spec": self.copy(MAP), "base_version": version,
                "into": {"name": "plan", "folder": "mymaps"}}):
            ms.import_map("jira", {"roots": ["ABCD-1"]}, into="plan")
        self.assertEqual(ms._seen[("plan", "mymaps")], version)

    # ------------------------------------------------------------- the lock --

    def test_reading_a_map_locks_it_and_the_designer_cannot_save(self):
        ms.save_map("plan", self.copy(MAP))
        ms.read_map("plan")
        self.assertEqual(self.lock_on_disk()["holder"], "metro-map-mcp")
        with self.assertRaises(ValueError) as caught:
            self.person_saves("Sneaking in", take_over=False)
        self.assertIn("an agent is updating", str(caught.exception))
        self.assertEqual(self.label_on_disk(), "A")

    def test_the_agents_save_hands_the_map_back(self):
        ms.save_map("plan", self.copy(MAP))
        mine = ms.read_map("plan")
        mine["stations"]["a"]["label"] = "Agent's"
        ms.save_map("plan", mine)
        self.assertIsNone(self.lock_on_disk())
        self.person_saves("And now mine", take_over=False)
        self.assertEqual(self.label_on_disk(), "And now mine")

    def test_taking_over_refuses_the_agents_save_until_it_reads_again(self):
        ms.save_map("plan", self.copy(MAP))
        mine = ms.read_map("plan")
        self.bridge("DELETE", "/api/maps/plan/lock")          # Take over
        self.assertIsNone(self.lock_on_disk())
        mine["stations"]["b"]["label"] = "Agent's"
        with self.assertRaises(ValueError) as caught:
            ms.save_map("plan", mine)
        self.assertIn("taken over", str(caught.exception))
        self.assertEqual(self.bridge("GET", "/api/maps/plan")["spec"]["stations"]["b"]["label"], "B")
        again = ms.read_map("plan")                            # asked to go on
        again["stations"]["b"]["label"] = "Agent's"
        ms.save_map("plan", again)
        self.assertEqual(self.bridge("GET", "/api/maps/plan")["spec"]["stations"]["b"]["label"],
                         "Agent's")

    def test_a_lock_nobody_comes_back_for_lapses(self):
        ms.save_map("plan", self.copy(MAP))
        ms.read_map("plan")
        with mock.patch.object(self.designer, "LOCK_FOR", -1):
            self.assertIsNone(self.lock_on_disk())
            self.person_saves("After the lapse", take_over=False)
        self.assertEqual(self.label_on_disk(), "After the lapse")

    def test_deleting_a_map_forgets_its_lock(self):
        ms.save_map("plan", self.copy(MAP))
        ms.read_map("plan")
        ms.delete_map("plan")
        self.assertNotIn(("plan", "mymaps"), self.designer._locks)

    def test_locking_is_refused_from_off_the_machine(self):
        ms.save_map("plan", self.copy(MAP))
        res = self.client.post("/api/maps/plan/lock",
                               environ_base={"REMOTE_ADDR": "10.1.2.3"})
        self.assertEqual(res.status_code, 403)

    def test_the_import_endpoint_reports_the_version_it_built_on(self):
        ms.save_map("plan", self.copy(MAP))
        version = self.bridge("GET", "/api/maps/plan")["version"]
        from metro_map_tool import sources as S
        fake = S.Source(name="jira", title="t", summary="s", options=(),
                        fetch=lambda o, m: {}, build=lambda d, o, m: (self.copy(MAP), []))
        with mock.patch.object(S, "get", return_value=fake):
            out = self.bridge("POST", "/api/import",
                              {"source": "jira", "options": {}, "into": {"name": "plan"}})
        self.assertEqual(out["base_version"], version)
        self.assertEqual(out["into"], {"name": "plan", "folder": "mymaps"})


if __name__ == "__main__":
    unittest.main()
