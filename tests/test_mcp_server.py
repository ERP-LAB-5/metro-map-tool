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

        with mock.patch.object(ms.web, "ensure_server"), \
             mock.patch.object(ms, "call", side_effect=fake_call):
            ms.save_map("my map", {"stations": {}})
        self.assertEqual(seen["method"], "PUT")
        self.assertEqual(seen["path"], "/api/maps/my%20map")
        self.assertEqual(seen["payload"]["auto_interchange"], True)


if __name__ == "__main__":
    unittest.main()
