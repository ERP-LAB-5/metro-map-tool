# SPDX-License-Identifier: GPL-3.0-or-later
"""The Jira plugin: its settings, its field discovery, and its browser.

None of this talks to a real Jira. The client is handed a stand-in that
answers the same shapes, which is the point of keeping fetch and build apart.
"""

import os
import pathlib
import tempfile
import unittest

from metro_map_tool import sources as S
from metro_map_tool.sources import SourceError
from metro_map_tool.sources.jira import SOURCE, quarter_dates
from metro_map_tool.sources.jira import browse as jira_browse
from metro_map_tool.sources.jira import client as jira_client
from metro_map_tool.sources.jira import fields as jira_fields


class Stand_in:
    """Answers what the plugin asks, and counts what it was asked."""

    FIELDS = [{"id": "customfield_10007", "name": "Sprint"},
              {"id": "customfield_10015", "name": "Start date"},
              {"id": "customfield_10014", "name": "Epic Link"},
              {"id": "summary", "name": "Summary"}]

    def __init__(self, types=("Epic Set", "Epic", "Story"), issues=None):
        self.calls = []
        self._types = list(types)
        self._issues = issues or []

    def fields(self):
        self.calls.append("fields")
        return self.FIELDS

    def projects(self, limit=100):
        self.calls.append("projects")
        return [{"key": "ACME", "name": "Acme Platform"}]

    def get(self, path, params=None):
        self.calls.append(path)
        if path.startswith("/rest/api/3/project/"):
            return {"key": "ACME",
                    "issueTypes": [{"name": n} for n in self._types]}
        return {}

    def search(self, jql, fields, limit=200):
        self.calls.append(("search", jql))
        return list(self._issues)

    def boards(self, project):
        self.calls.append("boards")
        return [{"id": 7, "name": "ACME Scrum", "type": "scrum"}]

    def sprints(self, board):
        self.calls.append("sprints")
        return [{"id": 14, "name": "Sprint 14", "state": "closed",
                 "startDate": "2026-02-02T00:00:00Z",
                 "endDate": "2026-02-16T00:00:00Z"}]


def issue(key, summary, kind="Story", due=None):
    return {"key": key, "fields": {"summary": summary, "duedate": due,
                                   "issuetype": {"name": kind},
                                   "status": {"name": "To Do",
                                              "statusCategory": {"key": "new"}}}}


class SettingsTest(unittest.TestCase):
    def setUp(self):
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
        for name in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"):
            os.environ.pop(name, None)

    def test_a_saved_file_is_readable_only_by_its_owner(self):
        path = S.write_config("jira", {"api_token": "shhh"})
        self.assertEqual(oct(path.stat().st_mode)[-3:], "600")

    def test_settings_are_read_back(self):
        S.write_config("jira", {"base_url": "https://x", "email": "a@b.c",
                                "api_token": "shhh"})
        self.assertEqual(S.credentials(SOURCE)["base_url"], "https://x")

    def test_the_environment_wins(self):
        S.write_config("jira", {"base_url": "https://from-file", "email": "a@b.c",
                                "api_token": "shhh"})
        os.environ["JIRA_BASE_URL"] = "https://from-env"
        self.assertEqual(S.credentials(SOURCE)["base_url"], "https://from-env")
        self.assertEqual([f["from"] for f in S.env_status(SOURCE)][0], "environment")

    def test_missing_settings_name_both_places_to_put_them(self):
        with self.assertRaises(SourceError) as caught:
            S.credentials(SOURCE)
        said = str(caught.exception)
        self.assertIn("JIRA_API_TOKEN", said)
        self.assertIn("jira.conf", said)

    def test_presence_is_reported_but_never_the_value(self):
        S.write_config("jira", {"api_token": "SECRET-VALUE"})
        said = repr(S.env_status(SOURCE)) + repr(S.describe(SOURCE))
        self.assertNotIn("SECRET-VALUE", said)
        self.assertTrue(any(f["present"] for f in S.env_status(SOURCE)))

    def test_an_empty_value_clears_rather_than_storing_nothing(self):
        S.write_config("jira", {"email": "a@b.c"})
        S.write_config("jira", {"email": ""})
        self.assertNotIn("email", S.read_config("jira"))

    def test_a_broken_config_file_does_not_stop_the_tool(self):
        path = S.config_path("jira")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("this is not ini [[[", encoding="utf-8")
        self.assertEqual(S.read_config("jira"), {})


class FieldDiscoveryTest(unittest.TestCase):
    def test_it_finds_this_instance_s_ids(self):
        found = jira_fields.discover(Stand_in())
        self.assertEqual(found["sprint"], "customfield_10007")
        self.assertEqual(found["start_date"], "customfield_10015")
        self.assertEqual(found["epic_link"], "customfield_10014")

    def test_what_you_typed_beats_what_it_found(self):
        found = jira_fields.resolve(Stand_in(), {"sprint_field": "customfield_99"})
        self.assertEqual(found["sprint"], "customfield_99")

    def test_a_remembered_id_saves_the_round_trip(self):
        stand = Stand_in()
        saved = {"sprint": "customfield_1", "start_date": "customfield_2",
                 "epic_link": "customfield_3"}
        jira_fields.resolve(stand, {}, saved)
        self.assertNotIn("fields", stand.calls)


class BrowseTest(unittest.TestCase):
    def test_the_top_of_the_tree_is_projects(self):
        nodes = jira_browse.browse([], {}, "hierarchy", client=Stand_in())
        self.assertEqual([n.kind for n in nodes], ["project"])
        self.assertTrue(nodes[0].expandable)

    def test_an_instance_with_an_epic_set_shows_that_level(self):
        stand = Stand_in(issues=[issue("ACME-1", "Platform Upgrade", "Epic Set")])
        nodes = jira_browse.browse(["ACME"], {}, "hierarchy", client=stand)
        self.assertEqual([n.kind for n in nodes], ["epicset"])

    def test_an_instance_without_one_skips_it_rather_than_showing_it_empty(self):
        stand = Stand_in(types=("Epic", "Story"),
                         issues=[issue("ACME-9", "Rollout", "Epic")])
        nodes = jira_browse.browse(["ACME"], {}, "hierarchy", client=stand)
        self.assertEqual([n.kind for n in nodes], ["epic"])

    def test_opening_a_project_does_not_fetch_its_issues(self):
        # a project with four thousand issues must not be downloaded to draw
        # three rows, so each level is asked for only when it is opened
        stand = Stand_in(issues=[issue("ACME-1", "A", "Epic Set")])
        jira_browse.browse(["ACME"], {}, "hierarchy", client=stand)
        searches = [c for c in stand.calls if isinstance(c, tuple)]
        self.assertEqual(len(searches), 1)
        self.assertIn("Epic Set", searches[0][1])

    def test_the_board_view_walks_boards_then_sprints(self):
        stand = Stand_in()
        boards = jira_browse.browse(["ACME"], {}, "boards", client=stand)
        self.assertEqual([n.kind for n in boards], ["board"])
        sprints = jira_browse.browse(["ACME", boards[0].id], {}, "boards",
                                     client=stand)
        self.assertEqual([n.kind for n in sprints], ["sprint"])
        self.assertIn("2026-02-02", sprints[0].hint)

    def test_a_site_without_jira_software_has_no_boards_not_an_error(self):
        stand = Stand_in()
        stand.boards = lambda project: []
        self.assertEqual(jira_browse.browse(["ACME"], {}, "boards", client=stand), [])


class ScopeTest(unittest.TestCase):
    """What a browsed selection asks Jira for."""

    def test_a_selection_asks_only_for_what_was_picked(self):
        from metro_map_tool.sources.jira import _scope
        jql = _scope({"project": "ACME", "select": ["ACME-10", "ACME-20"]})
        self.assertIn("ACME-10", jql)
        self.assertIn("parent in", jql)          # its children come too
        self.assertNotIn("project =", jql)

    def test_a_sprint_selection_becomes_a_sprint_clause(self):
        from metro_map_tool.sources.jira import _scope
        self.assertIn("sprint in (14)", _scope({"project": "S", "select": ["sprint:14"]}))

    def test_no_selection_takes_the_project(self):
        from metro_map_tool.sources.jira import _scope
        self.assertIn('project = "ACME"', _scope({"project": "ACME"}))


class QuarterLabelTest(unittest.TestCase):
    def test_a_quarter_label_is_a_span(self):
        self.assertEqual(quarter_dates("25Q1"), ("2025-01-01", "2025-03-31"))
        self.assertEqual(quarter_dates("26Q4"), ("2026-10-01", "2026-12-31"))

    def test_whatever_prefix_a_team_settled_on_is_read(self):
        for said in ("FY25Q2", "H25Q2", "AB25Q2", "25Q2"):
            self.assertEqual(quarter_dates(said), ("2025-04-01", "2025-06-30"), said)

    def test_anything_else_is_not(self):
        for said in ("backend", "Q1", "2025Q1", "IT25Q5", "TOOLONG25Q1"):
            self.assertEqual(quarter_dates(said), (None, None), said)

    def test_an_undated_issue_with_one_is_placed_not_dropped(self):
        data = {"project": "P", "issues": [
            issue("P-1", "Dated", "Story", "2026-02-01"),
            {"key": "P-2", "fields": {"summary": "Only a label", "duedate": None,
                                      "labels": ["FY26Q3"],
                                      "issuetype": {"name": "Story"},
                                      "status": {"name": "To Do",
                                                 "statusCategory": {"key": "new"}}}}],
                "versions": [], "fields": {}}
        spec, notes = SOURCE.build(data, {"project": "P", "quarter_labels": True}, None)
        self.assertIn("p-2", spec["stations"])
        self.assertEqual(spec["stations"]["p-2"]["date"], "2026-09-30")
        self.assertTrue(any("quarter label" in n for n in notes))

    def test_turning_it_off_leaves_the_issue_out(self):
        data = {"project": "P", "issues": [
            issue("P-1", "Dated", "Story", "2026-02-01"),
            {"key": "P-2", "fields": {"summary": "Only a label", "duedate": None,
                                      "labels": ["FY26Q3"],
                                      "issuetype": {"name": "Story"},
                                      "status": {"name": "To Do",
                                                 "statusCategory": {"key": "new"}}}}],
                "versions": [], "fields": {}}
        spec, _ = SOURCE.build(data, {"project": "P", "quarter_labels": False}, None)
        self.assertNotIn("p-2", spec["stations"])


class ReadOnlyTest(unittest.TestCase):
    """Read-only is a property of the code, not of everyone remembering."""

    def setUp(self):
        self.conn = jira_client.Jira("https://example.atlassian.net", "a@b.c", "t")

    def test_the_client_refuses_every_verb_but_get(self):
        for verb in ("PUT", "POST", "DELETE", "PATCH"):
            with self.assertRaises(SourceError) as caught:
                self.conn.request(verb, "/rest/api/3/issue/X-1")
            self.assertIn("read-only", str(caught.exception))

    def test_it_exposes_no_method_that_would_change_a_ticket(self):
        for name in ("put", "post", "delete", "patch", "update", "create",
                     "transition", "edit"):
            self.assertFalse(hasattr(self.conn, name), name)

    def test_the_site_is_a_host_not_a_url_with_anything_on_it(self):
        self.assertEqual(self.conn.site, "example.atlassian.net")

    def test_the_token_is_not_sitting_in_the_object_s_repr(self):
        self.assertNotIn("t", jira_client.Jira("https://x", "a@b.c", "SECRET").site)
        self.assertNotIn("SECRET", repr(vars(self.conn)))


if __name__ == "__main__":
    unittest.main()
