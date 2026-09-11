# SPDX-License-Identifier: GPL-3.0-or-later
"""The Jira plugin: its settings, its field discovery, and its browser.

None of this talks to a real Jira. The client is handed a stand-in that
answers the same shapes, which is the point of keeping fetch and build apart.
"""

import os
import pathlib
import re
import tempfile
import unittest

from metro_map_tool import sources as S
from metro_map_tool.sources import SourceError
from metro_map_tool.sources.jira import SOURCE, quarter_dates
from metro_map_tool.sources.jira import browse as jira_browse
from metro_map_tool.sources.jira import client as jira_client
from metro_map_tool.sources.jira import fields as jira_fields
from metro_map_tool.sources.jira import tree as jira_tree


class Stand_in:
    """Answers what the plugin asks, and counts what it was asked.

    `issues` is a flat list; a `parent in (...)` search is answered from each
    issue's parent field, the way Jira answers it.
    """

    FIELDS = [{"id": "customfield_10007", "name": "Sprint"},
              {"id": "customfield_10015", "name": "Start date"},
              {"id": "customfield_10014", "name": "Epic Link"},
              {"id": "summary", "name": "Summary"}]

    def __init__(self, issues=None):
        self.calls = []
        self._issues = issues or []

    def fields(self):
        self.calls.append("fields")
        return self.FIELDS

    def issue(self, key, fields):
        self.calls.append(("issue", key))
        for found in self._issues:
            if found["key"] == key:
                return found
        raise SourceError(f"there is no issue {key} — check the key, or "
                          "whether this account can see it")

    def search(self, jql, fields, limit=200):
        self.calls.append(("search", jql))
        asked = re.findall(r'"([^"]+)"', jql)
        if jql.startswith("parent in"):
            return [i for i in self._issues
                    if ((i["fields"].get("parent") or {}).get("key")) in asked
                    ][:limit]
        return list(self._issues)[:limit]


def issue(key, summary, kind="Story", due=None, parent=None, done=False,
          labels=()):
    fields = {"summary": summary, "duedate": due, "issuetype": {"name": kind},
              "labels": list(labels),
              "status": {"name": "Done" if done else "To Do",
                         "statusCategory": {"key": "done" if done else "new"}}}
    if parent:
        fields["parent"] = {"key": parent}
    return {"key": key, "fields": fields}


def initiative():
    """INIT-1 → EPIC-1 → ST-1 (open, 25Q1), ST-2 (done); EPIC-2 → ST-3 (Bug)."""
    return [issue("INIT-1", "Platform", "Initiative"),
            issue("EPIC-1", "Cutover", "Epic", parent="INIT-1"),
            issue("EPIC-2", "Hardening", "Epic", parent="INIT-1"),
            issue("ST-1", "Freeze", due="2026-03-01", parent="EPIC-1",
                  labels=["25Q1"]),
            issue("ST-2", "Rehearse", due="2026-02-01", parent="EPIC-1",
                  done=True),
            issue("ST-3", "Load test", "Bug", due="2026-04-01", parent="EPIC-2")]


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
    def test_it_starts_from_a_key_not_a_project_list(self):
        with self.assertRaises(SourceError) as caught:
            jira_browse.browse([], {}, "tree", client=Stand_in())
        self.assertIn("issue key", str(caught.exception))

    def test_an_unknown_key_says_so(self):
        with self.assertRaises(SourceError) as caught:
            jira_browse.browse(["NOPE-1"], {}, "tree", client=Stand_in(initiative()))
        self.assertIn("there is no issue NOPE-1", str(caught.exception))

    def test_something_that_is_not_a_key_is_refused_before_asking(self):
        stand = Stand_in(initiative())
        with self.assertRaises(SourceError):
            jira_browse.browse(["platform upgrade"], {}, "tree", client=stand)
        self.assertEqual(stand.calls, [])

    def test_the_whole_subtree_comes_back_with_its_shape(self):
        nodes = jira_browse.browse(["INIT-1"], {}, "tree",
                                   client=Stand_in(initiative()))
        by = {n.id: n for n in nodes}
        self.assertEqual(nodes[0].id, "INIT-1")
        self.assertEqual((by["INIT-1"].depth, by["INIT-1"].kind), (0, "root"))
        self.assertEqual((by["EPIC-1"].depth, by["EPIC-1"].parent), (1, "INIT-1"))
        self.assertEqual((by["ST-2"].depth, by["ST-2"].parent), (2, "EPIC-1"))
        self.assertTrue(by["EPIC-1"].expandable)
        self.assertFalse(by["ST-1"].expandable)
        self.assertEqual(by["ST-2"].facets["open"], False)
        self.assertEqual(by["ST-3"].facets["type"], "Bug")
        self.assertEqual(by["ST-1"].facets["labels"], ["25Q1"])

    def test_a_lowercase_key_is_understood(self):
        nodes = jira_browse.browse(["epic-1"], {}, "tree",
                                   client=Stand_in(initiative()))
        self.assertEqual([n.id for n in nodes], ["EPIC-1", "ST-1", "ST-2"])


class WalkTest(unittest.TestCase):
    def test_a_wide_level_is_asked_for_in_chunks(self):
        wide = [issue("RT-1", "Root", "Epic")] + [
            issue(f"ST-{n}", f"Story {n}", parent="RT-1") for n in range(120)]
        stand = Stand_in(wide)
        # one level of 120 below the root: every child must be asked about,
        # and no single JQL may name all of them
        _, found, truncated = jira_tree.walk(stand, "RT-1", "summary", 500)
        self.assertEqual((len(found), truncated), (120, False))
        asked = [c[1] for c in stand.calls if c[0] == "search"]
        self.assertEqual(len(asked), 1 + 3)       # the root's children, then 3 chunks
        self.assertTrue(all(q.count('"') <= 2 * jira_tree.CHUNK for q in asked))

    def test_it_stops_at_the_limit_and_says_so(self):
        _, found, truncated = jira_tree.walk(Stand_in(initiative()), "INIT-1",
                                             "summary", 3)
        self.assertEqual((len(found), truncated), (3, True))

    def test_a_parent_loop_does_not_walk_forever(self):
        loop = [issue("AB-1", "A", "Epic", parent="AB-2"),
                issue("AB-2", "B", "Epic", parent="AB-1")]
        _, found, _ = jira_tree.walk(Stand_in(loop), "AB-1", "summary", 100)
        self.assertEqual([i["key"] for i in found], ["AB-2"])


class KeepTest(unittest.TestCase):
    """The one definition of what a filter leaves, shared by preview and import."""

    def setUp(self):
        _, self.issues, _ = jira_tree.walk(Stand_in(initiative()), "INIT-1",
                                           "summary", 100)

    def keep(self, **given):
        return jira_tree.keep(self.issues, "INIT-1", **given)

    def test_no_filter_keeps_everything(self):
        self.assertEqual(len(self.keep()), 6)

    def test_by_type_keeps_the_structure_above(self):
        self.assertEqual(self.keep(types=["bug"]), {"INIT-1", "EPIC-2", "ST-3"})

    def test_only_open_drops_what_is_done(self):
        self.assertNotIn("ST-2", self.keep(open_only=True))
        self.assertIn("ST-1", self.keep(open_only=True))

    def test_a_label_keeps_its_issue_and_what_it_hangs_from(self):
        self.assertEqual(self.keep(labels=["25q1"]), {"INIT-1", "EPIC-1", "ST-1"})

    def test_filters_combine(self):
        self.assertEqual(self.keep(types=["Story"], open_only=True),
                         {"INIT-1", "EPIC-1", "ST-1"})


def mapped(opts=None, trees=None):
    """Build a spec the way an import does, from stand-in trees.

    `trees` is a list of (root key, issues); by default the one initiative.
    """
    trees = trees or [("INIT-1", initiative())]
    roots = []
    for key, issues in trees:
        root, found, truncated = jira_tree.walk(Stand_in(issues), key, "summary", 100)
        roots.append({"root": root, "issues": found, "truncated": truncated})
    raw = dict({"roots": ",".join(k for k, _ in trees)}, **(opts or {}))
    coerced, errors = S.coerce(SOURCE, raw)
    assert not errors, errors
    data = {"roots": roots, "versions": [], "fields": {}, "notes": []}
    spec, notes = SOURCE.build(data, coerced, None)
    return spec, notes


def operations():
    """OPS-1 → OPS-2 (Alerts, Dashboards), OPS-6 (Forecast), and Runbook directly."""
    return [issue("OPS-1", "Operations", "Initiative"),
            issue("OPS-2", "Monitoring", "Epic", parent="OPS-1"),
            issue("OPS-3", "Alerts", due="2026-05-01", parent="OPS-2"),
            issue("OPS-4", "Dashboards", due="2026-06-15", parent="OPS-2"),
            issue("OPS-6", "Capacity", "Epic", parent="OPS-1"),
            issue("OPS-7", "Forecast", due="2026-05-10", parent="OPS-6"),
            issue("OPS-5", "Runbook", due="2026-03-15", parent="OPS-1")]


class MappingOptionsTest(unittest.TestCase):
    """What the mapping is told, and the sentence for telling it wrong."""

    def test_levels_roles_and_dates_are_read(self):
        self.assertEqual(jira_tree.levels_of(["", "Junction"]), ["", "junction"])
        self.assertEqual(jira_tree.roles_of(["abcd-1=Zone"]), {"ABCD-1": "zone"})
        self.assertEqual(jira_tree.dates_of(["ABCD-2=2026-10-01"]),
                         {"ABCD-2": "2026-10-01"})

    def test_each_mistake_is_a_sentence(self):
        for call, said in ((lambda: jira_tree.levels_of(["tunnel"]), "level 1"),
                           (lambda: jira_tree.roles_of(["ABCD-1=tunnel"]), "not a role"),
                           (lambda: jira_tree.roles_of(["ABCD-1"]), "KEY=value"),
                           (lambda: jira_tree.dates_of(["ABCD-1=soon"]), "yyyy-mm-dd")):
            with self.assertRaises(SourceError) as caught:
                call()
            self.assertIn(said, str(caught.exception))

    def test_the_cli_spelling_survives_coercion(self):
        opts, errors = S.coerce(SOURCE, {"roots": "ABCD-1,ABCD-2",
                                         "dates": "ABCD-3=2026-10-01"})
        self.assertEqual(errors, [])
        self.assertEqual(opts["dates"], ["ABCD-3=2026-10-01"])


class RolesTest(unittest.TestCase):
    def setUp(self):
        _, self.issues, _ = jira_tree.walk(Stand_in(initiative()), "INIT-1",
                                           "summary", 100)
        self.kept = jira_tree.keep(self.issues, "INIT-1")

    def roles(self, levels=None, overrides=None):
        return jira_tree.roles(self.issues, "INIT-1", self.kept, levels, overrides)

    def test_by_default_a_level_that_has_children_branches(self):
        got, _ = self.roles()
        self.assertEqual((got["EPIC-1"], got["ST-1"]), ("junction", "station"))

    def test_an_issue_overrides_its_level(self):
        got, _ = self.roles(["station"], {"EPIC-2": "zone"})
        self.assertEqual((got["EPIC-1"], got["EPIC-2"]), ("station", "zone"))

    def test_skip_takes_everything_beneath_with_it(self):
        got, _ = self.roles(overrides={"EPIC-1": "skip"})
        self.assertNotIn("EPIC-1", got)
        self.assertNotIn("ST-1", got)
        self.assertIn("ST-3", got)

    def test_hide_keeps_what_is_beneath(self):
        got, _ = self.roles(["hide"])
        self.assertEqual(got["EPIC-1"], "hide")
        self.assertEqual(got["ST-1"], "station")

    def test_a_junction_with_nothing_beneath_is_a_station_and_says_so(self):
        got, said = self.roles(["junction", "junction"])
        self.assertEqual(got["ST-1"], "station")
        self.assertTrue(any("instead of a junction" in s for s in said))


class LayoutTest(unittest.TestCase):
    def assertValid(self, spec):
        from metro_map_tool import metro_map as mm
        self.assertEqual(mm.validate_spec(spec), [])

    def test_each_key_is_its_own_line_on_its_own_rows(self):
        spec, _ = mapped(trees=[("INIT-1", initiative()), ("OPS-1", operations())])
        self.assertValid(spec)
        self.assertEqual([ln["name"] for ln in spec["lines"]], ["Platform", "Operations"])
        rows = lambda ln: {spec["stations"][s]["gy"] for b in ln.get("branches", [])
                           for s in b["stations"] if s in spec["stations"]}
        self.assertFalse(rows(spec["lines"][0]) & rows(spec["lines"][1]))

    def test_a_junction_is_a_branch_that_forks_and_rejoins(self):
        spec, _ = mapped()
        self.assertValid(spec)
        line = spec["lines"][0]
        cutover = next(b for b in line["branches"] if b["name"] == "Cutover")
        self.assertEqual(cutover["stations"][0], "j-epic-1-fork")
        self.assertEqual(cutover["stations"][-1], "j-epic-1-join")
        self.assertEqual(cutover["stations"][1:-1], ["st-2", "st-1"])  # by date
        self.assertIn("j-epic-1-fork", line["stations"])
        self.assertIn("j-epic-1-join", line["stations"])
        fork = spec["junctions"]["j-epic-1-fork"]
        self.assertLess(fork["gx"], spec["stations"]["st-2"]["gx"])

    def test_a_branch_can_fork_off_a_branch(self):
        deep = initiative() + [issue("ST-9", "Sub-epic", "Epic", parent="EPIC-1"),
                               issue("SU-1", "Deep", due="2026-02-15", parent="ST-9")]
        spec, _ = mapped({"levels": "junction,junction"}, [("INIT-1", deep)])
        self.assertValid(spec)
        names = [b["name"] for b in spec["lines"][0]["branches"]]
        self.assertIn("Sub-epic", names)
        sub = next(b for b in spec["lines"][0]["branches"] if b["name"] == "Sub-epic")
        cutover = next(b for b in spec["lines"][0]["branches"] if b["name"] == "Cutover")
        self.assertIn(sub["stations"][0], cutover["stations"])

    def test_a_zone_bands_exactly_what_is_beneath_it(self):
        spec, _ = mapped({"levels": "zone"})
        self.assertValid(spec)
        zones = {z["name"]: sorted(z["stations"]) for z in spec["zones"]}
        self.assertEqual(zones, {"Cutover": ["st-1", "st-2"], "Hardening": ["st-3"]})

    def test_a_note_rides_the_hop_of_its_first_stop(self):
        spec, _ = mapped({"levels": "note"})
        self.assertValid(spec)
        line = spec["lines"][0]
        texts = {n["text"]: line["stations"][n["at"]] for n in line["notes"]}
        self.assertEqual(texts["Cutover"], "st-2")

    def test_a_note_on_a_branch_is_left_out_and_says_why(self):
        deep = initiative() + [issue("ST-9", "Aside", "Task", parent="EPIC-1"),
                               issue("SU-1", "Deep", due="2026-02-15", parent="ST-9")]
        spec, notes = mapped({"levels": "junction,note"}, [("INIT-1", deep)])
        self.assertValid(spec)
        self.assertTrue(any("main route" in n for n in notes))

    def test_a_date_of_your_own_places_an_undated_issue(self):
        undated = initiative() + [issue("ST-4", "Sign-off", parent="EPIC-2")]
        spec, notes = mapped({"dates": "ST-4=2026-05-01"}, [("INIT-1", undated)])
        self.assertEqual(spec["stations"]["st-4"]["date"], "2026-05-01")

    def test_jira_s_own_date_beats_yours_and_says_so(self):
        spec, notes = mapped({"dates": "ST-1=2027-01-01"})
        self.assertEqual(spec["stations"]["st-1"]["date"], "2026-03-01")
        self.assertTrue(any("no longer used" in n for n in notes))

    def test_what_has_no_date_at_all_is_counted_as_left_out(self):
        undated = initiative() + [issue("ST-4", "Sign-off", parent="EPIC-2")]
        spec, notes = mapped(trees=[("INIT-1", undated)])
        self.assertNotIn("st-4", spec["stations"])
        self.assertTrue(any("1 issue(s)" in n and "ST-4" in n for n in notes))

    def test_a_junction_with_nothing_dated_beneath_is_a_stop_when_it_is_dated(self):
        tree = initiative() + [issue("EPIC-3", "Dated epic", "Epic", due="2026-04-15",
                                     parent="INIT-1"),
                               issue("ST-5", "Undated", parent="EPIC-3")]
        spec, notes = mapped(trees=[("INIT-1", tree)])
        self.assertIn("epic-3", spec["stations"])
        self.assertNotIn("j-epic-3-fork", spec.get("junctions", {}))
        self.assertTrue(any("no branch" in n for n in notes))

    def test_only_crowded_labels_are_tilted(self):
        spec, _ = mapped()
        # st-2 and st-1 are a month apart on a monthly ruler: level is fine
        self.assertNotIn("label_angle", spec["stations"]["st-1"])

    def test_everything_made_carries_what_jira_decided(self):
        spec, _ = mapped()
        st = spec["stations"]["st-1"]
        self.assertEqual(st["upstream"], {"label": st["label"], "date": st["date"],
                                          "gx": st["gx"], "gy": st["gy"]})
        self.assertEqual(spec["lines"][0]["upstream"]["name"], "Platform")
        self.assertTrue(all("upstream" in j for j in spec["junctions"].values()))

    def test_a_filter_that_leaves_nothing_says_so(self):
        with self.assertRaises(SourceError) as caught:
            mapped({"types": "Task"})
        self.assertIn("nothing to place", str(caught.exception))


class SearchPagingTest(unittest.TestCase):
    """The search Jira still answers, paged the way it now pages."""

    def test_it_follows_the_page_token_on_the_new_endpoint(self):
        conn = jira_client.Jira("https://example.atlassian.net", "a@b.c", "t")
        asked = []
        pages = [{"issues": [{"key": "A-1"}, {"key": "A-2"}],
                  "nextPageToken": "p2", "isLast": False},
                 {"issues": [{"key": "A-3"}], "isLast": True}]

        def get(path, params=None):
            asked.append((path, dict(params or {})))
            return pages[len(asked) - 1]
        conn.get = get
        found = conn.search("project = A", "summary", 200)
        self.assertEqual([i["key"] for i in found], ["A-1", "A-2", "A-3"])
        self.assertEqual({p for p, _ in asked}, {"/rest/api/3/search/jql"})
        self.assertEqual(asked[1][1]["nextPageToken"], "p2")

    def test_it_stops_at_the_limit(self):
        conn = jira_client.Jira("https://example.atlassian.net", "a@b.c", "t")
        conn.get = lambda path, params=None: {
            "issues": [{"key": f"A-{n}"} for n in range(100)],
            "nextPageToken": "more", "isLast": False}
        self.assertEqual(len(conn.search("x", "summary", 150)), 150)

    def test_a_missing_issue_is_a_sentence_not_a_url(self):
        conn = jira_client.Jira("https://example.atlassian.net", "a@b.c", "t")

        def get(path, params=None):
            raise SourceError(f"https://example.atlassian.net{path} returned "
                              "404 Not Found — check the project or repository name")
        conn.get = get
        with self.assertRaises(SourceError) as caught:
            conn.issue("ABCD-9", "summary")
        self.assertEqual(str(caught.exception), "there is no issue ABCD-9 — check "
                         "the key, or whether this account can see it")


class ScopeTest(unittest.TestCase):
    """What a browsed selection asks Jira for."""

    def test_no_keys_no_project_and_no_search_says_what_to_give(self):
        from unittest import mock
        from metro_map_tool.sources import jira
        with mock.patch.object(jira, "credentials", lambda src: {}), \
                mock.patch.object(jira.jira_client, "connect", lambda creds: Stand_in()), \
                mock.patch.object(jira.jira_config, "remembered_fields", lambda: {}), \
                mock.patch.object(jira.jira_config, "remember_fields", lambda found: None):
            with self.assertRaises(SourceError) as caught:
                jira._fetch({}, None)
        self.assertIn("start from issue keys", str(caught.exception))

    def test_a_selection_asks_only_for_what_was_picked(self):
        from metro_map_tool.sources.jira import _scope
        jql = _scope({"project": "ABCD", "select": ["ABCD-10", "ABCD-20"]})
        self.assertIn("ABCD-10", jql)
        self.assertIn("parent in", jql)          # its children come too
        self.assertNotIn("project =", jql)

    def test_a_sprint_selection_becomes_a_sprint_clause(self):
        from metro_map_tool.sources.jira import _scope
        self.assertIn("sprint in (14)", _scope({"project": "S", "select": ["sprint:14"]}))

    def test_no_selection_takes_the_project(self):
        from metro_map_tool.sources.jira import _scope
        self.assertIn('project = "ABCD"', _scope({"project": "ABCD"}))


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


class OtherPeoplesConfigTest(unittest.TestCase):
    """A config file someone already keeps for their own scripts should work.

    Making them copy a token from one file to another because this tool spells
    a key differently is a worse outcome than being relaxed about key names.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = pathlib.Path(self.dir) / "config.conf"
        os.environ["METRO_MAP_JIRA_CONFIG"] = str(self.path)
        for name in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"):
            os.environ.pop(name, None)

    def tearDown(self):
        os.environ.pop("METRO_MAP_JIRA_CONFIG", None)

    def test_the_common_spellings_are_understood(self):
        self.path.write_text("[jira]\nurl = https://x\nemail = a@b.c\n"
                             "api_token = t\n", encoding="utf-8")
        self.assertEqual(S.credentials(SOURCE)["base_url"], "https://x")

    def test_token_is_another_name_for_api_token(self):
        self.path.write_text("[jira]\nurl = https://x\nuser = a@b.c\n"
                             "token = t\n", encoding="utf-8")
        got = S.credentials(SOURCE)
        self.assertEqual(got["api_token"], "t")
        self.assertEqual(got["email"], "a@b.c")

    def test_our_own_spelling_still_wins_when_both_are_present(self):
        self.path.write_text("[jira]\nbase_url = https://ours\nurl = https://theirs\n"
                             "email = a@b.c\napi_token = t\n", encoding="utf-8")
        self.assertEqual(S.credentials(SOURCE)["base_url"], "https://ours")

    def test_the_env_var_points_at_a_file_anywhere(self):
        self.path.write_text("[jira]\nurl = https://x\nemail = a@b.c\n"
                             "api_token = t\n", encoding="utf-8")
        self.assertEqual(str(S.config_path("jira")), str(self.path))
