# SPDX-License-Identifier: GPL-3.0-or-later
"""The endpoints that handle credentials, and who is allowed to reach them.

The designer is a local tool, but --host will put it on a network. Settings and
browsing both hold or use credentials, so they get the same loopback guard that
already protects shutdown and restart.
"""

import os
import tempfile
import unittest

from metro_map_tool import app as designer
from metro_map_tool import sources as S

FAR_AWAY = {"REMOTE_ADDR": "10.11.12.13"}


class LoopbackTest(unittest.TestCase):
    def setUp(self):
        designer.app.config["TESTING"] = True
        self.client = designer.app.test_client()

    def test_settings_are_refused_from_off_the_machine(self):
        for call in (lambda: self.client.get("/api/settings/jira",
                                             environ_base=FAR_AWAY),
                     lambda: self.client.put("/api/settings/jira", json={"values": {}},
                                             environ_base=FAR_AWAY),
                     lambda: self.client.post("/api/settings/jira/test",
                                              environ_base=FAR_AWAY)):
            self.assertEqual(call().status_code, 403)

    def test_browsing_is_refused_from_off_the_machine(self):
        got = self.client.get("/api/browse/jira", environ_base=FAR_AWAY)
        self.assertEqual(got.status_code, 403)

    def test_a_local_caller_is_allowed(self):
        got = self.client.get("/api/settings/jira",
                              environ_base={"REMOTE_ADDR": "127.0.0.1"})
        self.assertEqual(got.status_code, 200)

    def test_an_unknown_source_is_a_404_not_a_crash(self):
        got = self.client.get("/api/settings/nosuch",
                              environ_base={"REMOTE_ADDR": "127.0.0.1"})
        self.assertEqual(got.status_code, 404)


class SecrecyTest(unittest.TestCase):
    def setUp(self):
        designer.app.config["TESTING"] = True
        self.client = designer.app.test_client()
        self.home = tempfile.mkdtemp()
        self._old = os.environ.get(S.CONFIG_HOME)
        os.environ[S.CONFIG_HOME] = self.home
        for name in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"):
            os.environ.pop(name, None)

    def tearDown(self):
        if self._old is None:
            os.environ.pop(S.CONFIG_HOME, None)
        else:
            os.environ[S.CONFIG_HOME] = self._old

    def test_a_saved_token_never_comes_back_out(self):
        local = {"REMOTE_ADDR": "127.0.0.1"}
        self.client.put("/api/settings/jira", environ_base=local, json={
            "values": {"base_url": "https://x", "email": "a@b.c",
                       "api_token": "SECRET-abc123"}})
        for path in ("/api/settings/jira", "/api/sources", "/api/defaults"):
            said = self.client.get(path, environ_base=local).get_data(as_text=True)
            self.assertNotIn("SECRET-abc123", said, path)

    def test_an_unknown_setting_is_refused_rather_than_stored(self):
        got = self.client.put("/api/settings/jira",
                              environ_base={"REMOTE_ADDR": "127.0.0.1"},
                              json={"values": {"nonsense": "x"}})
        self.assertEqual(got.status_code, 400)
        self.assertNotIn("nonsense", S.read_config("jira"))

    def test_import_still_refuses_a_local_path_over_http(self):
        got = self.client.post("/api/import",
                               environ_base={"REMOTE_ADDR": "127.0.0.1"},
                               json={"source": "jira",
                                     "options": {"from_file": "/etc/hosts"}})
        self.assertEqual(got.status_code, 400)


class BrowseOptionsTest(unittest.TestCase):
    def test_the_filter_options_reach_the_source_as_options(self):
        from unittest import mock
        from metro_map_tool.sources import jira
        designer.app.config["TESTING"] = True
        seen = {}

        def browse(path, opts, view, query="", client=None, creds=None):
            seen.update(path=path, opts=opts)
            return []
        with mock.patch.object(jira, "credentials", lambda src: {}), \
                mock.patch.object(jira.browse_mod, "browse", browse):
            got = designer.app.test_client().get(
                "/api/browse/jira?path=ABCD-1&open_only=true&types=Story,Bug"
                "&roots=ABCD-1,ABCD-2&levels=junction,station"
                "&roles=ABCD-3%3Dzone&dates=ABCD-4%3D2026-10-01",
                environ_base={"REMOTE_ADDR": "127.0.0.1"})
        self.assertEqual(got.status_code, 200, got.get_data(as_text=True))
        self.assertEqual(seen["path"], ["ABCD-1"])
        self.assertIs(seen["opts"]["open_only"], True)
        self.assertEqual(seen["opts"]["types"], ["Story", "Bug"])
        self.assertEqual(seen["opts"]["roots"], ["ABCD-1", "ABCD-2"])
        self.assertEqual(seen["opts"]["levels"], ["junction", "station"])
        self.assertEqual(seen["opts"]["roles"], ["ABCD-3=zone"])
        self.assertEqual(seen["opts"]["dates"], ["ABCD-4=2026-10-01"])


if __name__ == "__main__":
    unittest.main()
