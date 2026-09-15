# CLAUDE.md — metro-map-tool

Transit-map and roadmap diagrams: a stdlib renderer (`metro_map_tool/metro_map.py`), a Flask
designer (`app.py`, `static/`, `templates/`), an MCP server (`mcp_server.py`) and importers
(`sources/`). `metro_map_tool/core/` belongs to python-tool-template.

## The roadmap lives here

Behind is ADRs and commits. Ahead is specs. The route between them is a map
([ADR 0001](docs/adr/0001-roadmap-as-adrs-commits-and-specs.md)).

- **Deciding.** A decision that changes behaviour, a spec or file format, a dependency or the
  licence gets an ADR in `docs/adr/` in the same change. Add it to the index, and name the
  commits under "Carried out in". Never rewrite an accepted ADR; supersede it.
- **Ideas and loose ends** go on `docs/specs/PARKING-LOT.md` (what / why parked / what we'd need
  to know). Do not build them unasked.
- **Before building a release,** move its items into `docs/specs/vX.Y.Z.md` from `TEMPLATE.md`.
  It stays living until release, then gets frozen. Unfinished items go back on the parking lot.
- **At release,** add the station to `docs/roadmap/roadmap.json` and re-draw
  `docs/roadmap/roadmap.svg` (`tests/test_docs.py` checks that they match).

## House rules

- Commits: the git-commit skill (sentence subject, prose body, `Verified:` block).
- Releases: `python3 scripts/release.py X.Y.Z -F notes.txt` (release skill). Confirm with the
  user before pushing a tag.
- `core/` changes only through `copier update` from a template release
  ([ADR 0009](docs/adr/0009-build-on-python-tool-template.md)).
- Run the full `./test.sh` before a push, not `--quick`.
- Existing maps must render byte for byte as before unless the change says otherwise. New spec
  fields are optional, and `needs_format()` says when a map needs a newer format
  ([ADR 0003](docs/adr/0003-format-stamp-is-the-oldest-format-that-can-draw.md)).
- Nothing site-specific in the repo: example Jira keys are `ABCD-123`.
