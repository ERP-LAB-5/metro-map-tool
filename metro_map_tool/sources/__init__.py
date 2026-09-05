#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
sources — importers that turn an external system into a map spec.

A map is worth drawing once. Keeping it worth looking at means re-drawing it
every time the plan moves, which nobody does by hand. A source fetches the plan
from where it already lives — a git history, a GitHub milestone, a Jira epic —
and hands back a spec, so the map becomes something you re-sync rather than
something you redo.

Every source declares its options once. That single declaration drives the
command line, the MCP catalogue and the browser's Import dialog, so a source
gets three front doors without writing three descriptions of itself that then
drift apart.

Credentials are deliberately *not* options. A token is never a value this
machinery holds, so it cannot be written into a spec, echoed by /api/sources or
printed in an error — not because everyone remembers to redact it, but because
it was never in the pipe.
"""

from __future__ import annotations

import dataclasses
import importlib
import json
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

KINDS = ("str", "int", "bool", "date", "path", "choice", "csv")


class SourceError(RuntimeError):
    """A source could not do what was asked, with a sentence saying why."""


@dataclasses.dataclass(frozen=True)
class Option:
    """One thing a source needs to be told, said once for all three front doors."""
    name: str                                   # snake_case; the key in the dict
    help: str                                   # one line, shown everywhere
    kind: str = "str"
    default: object = None
    choices: Tuple[str, ...] = ()
    required: bool = False
    placeholder: str = ""

    def as_json(self) -> dict:
        return {"name": self.name, "help": self.help, "kind": self.kind,
                "default": self.default, "choices": list(self.choices),
                "required": self.required, "placeholder": self.placeholder}


@dataclasses.dataclass(frozen=True)
class EnvVar:
    """A credential a source reads from the environment.

    Not an Option, and that is the whole security design: an option's value
    travels through argparse, an HTTP body and a saved spec, and a token must
    travel through none of those.
    """
    name: str
    help: str
    required: bool = True


@dataclasses.dataclass(frozen=True)
class Source:
    """An importer.

    `fetch` does every network and subprocess call and returns plain JSON-able
    data. `build` is pure: data in, spec out. The split is what lets a recorded
    payload stand in for the live system, so the mapping can be exercised — by
    a test, or by someone on a train — with no credentials and no network.
    """
    name: str
    title: str
    summary: str
    options: Tuple[Option, ...]
    fetch: Callable[[dict, Optional[dict]], object]
    build: Callable[[object, dict, Optional[dict]], Tuple[dict, List[str]]]
    env: Tuple[EnvVar, ...] = ()
    # Fields a re-sync should always re-take from upstream. Empty for most
    # sources — where a stop sits is the author's business. git is the
    # exception: a commit's position *is* its place in the history, so letting
    # an old one stay put would put it in the wrong order as history grows.
    refresh_default: Tuple[str, ...] = ()

    def option(self, name: str) -> Optional[Option]:
        for opt in self.all_options():
            if opt.name == name:
                return opt
        return None

    def all_options(self) -> Tuple[Option, ...]:
        return tuple(self.options) + UNIVERSAL


# Options every source gets, implemented once here rather than five times badly.
UNIVERSAL: Tuple[Option, ...] = (
    Option("model", "a map whose lines, names, colours and layout are kept while "
                    "the import fills in the contents", kind="path"),
    Option("from_file", "read a saved payload instead of calling the system — for "
                        "working offline, and for tests", kind="path"),
    Option("to_file", "also save the payload fetched, to replay later", kind="path"),
    Option("limit", "most items to take", kind="int", default=200),
    Option("prune", "remove items this source imported before that are no longer "
                    "upstream (off by default — see the module docstring)",
           kind="bool", default=False),
    Option("refresh", "fields to re-take from upstream on an item that already "
                      "exists, e.g. label,gx — by default the import never "
                      "overwrites what you changed", kind="csv"),
)


def env_status(src: Source) -> List[dict]:
    """Which credentials a source wants, and whether they are set.

    Presence only. This is the one place in the package that reads the
    environment, and it must never return a value: the answer travels to a
    browser over HTTP.
    """
    import os                                   # local: the renderer never needs it
    return [{"name": e.name, "help": e.help, "required": e.required,
             "present": bool(os.environ.get(e.name))} for e in src.env]


# Options that are this machine's business, not the map's: a saved spec that
# named someone's home directory would leak it to everyone the map is shared with.
LOCAL_ONLY = ("model", "from_file", "to_file")


def stamp(src: Source, opts: dict) -> dict:
    """What to record on the spec about the import that made it.

    The options are here so a re-sync is one click rather than a remembered
    command line. They cannot carry a credential, because a credential is never
    an option — see the module docstring.
    """
    from datetime import datetime, timezone
    from .. import metro_map as mm
    return {"name": src.name,
            "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tool": mm.__version__,
            "options": {k: v for k, v in sorted(opts.items())
                        if k not in LOCAL_ONLY}}


def coerce(src: Source, raw: dict) -> Tuple[dict, List[str]]:
    """Options as the source wants them, plus every complaint about them.

    argparse hands us strings, a browser form hands us strings, and an agent
    hands us whatever JSON it felt like. Coercing in one place means a bad date
    is the same sentence on all three, rather than three different crashes.

    Never raises: each caller has its own way of reporting, and they all want a
    list.
    """
    known = {opt.name: opt for opt in src.all_options()}
    errors: List[str] = []
    out: dict = {}

    for key in raw:
        if key not in known:
            errors.append(f"'{key}' is not an option of the {src.name} source — "
                          "it takes " + ", ".join(sorted(known)))

    for name, opt in known.items():
        if name not in raw or raw[name] is None or raw[name] == "":
            if opt.required:
                errors.append(f"the {src.name} source needs '{name}' — {opt.help}")
            elif opt.default is not None:
                out[name] = opt.default
            continue
        value = raw[name]
        try:
            out[name] = _as_kind(opt, value)
        except ValueError as exc:
            errors.append(f"option '{name}': {exc}")
    return out, errors


def _as_kind(opt: Option, value: object) -> object:
    kind = opt.kind
    if kind == "bool":
        if isinstance(value, bool):
            return value
        said = str(value).strip().lower()
        if said in ("1", "true", "yes", "on", ""):
            return True
        if said in ("0", "false", "no", "off"):
            return False
        raise ValueError(f"'{value}' is not a yes or no")
    if kind == "int":
        try:
            return int(str(value).strip())
        except ValueError:
            raise ValueError(f"'{value}' is not a whole number") from None
    if kind == "csv":
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        return [p.strip() for p in str(value).split(",") if p.strip()]
    if kind == "choice":
        said = str(value).strip()
        if said not in opt.choices:
            raise ValueError(f"'{said}' is not one of " + ", ".join(opt.choices))
        return said
    if kind == "date":
        from datetime import date
        said = str(value).strip()
        try:
            date.fromisoformat(said)
        except ValueError:
            raise ValueError(f"'{said}' is not a yyyy-mm-dd date") from None
        return said
    return str(value)


def payload(src: Source, opts: dict, model: Optional[dict]) -> object:
    """The raw data: from a saved file when one is named, else from the system.

    Implemented here rather than in each source so that every source — including
    one somebody else wrote — can be replayed offline without having thought
    about it.
    """
    if opts.get("from_file"):
        return json.loads(Path(opts["from_file"]).read_text(encoding="utf-8"))
    data = src.fetch(opts, model)
    if opts.get("to_file"):
        Path(opts["to_file"]).write_text(json.dumps(data, indent=2, sort_keys=True),
                                         encoding="utf-8")
    return data


# ---------------------------------------------------------------- registry --

# Built-ins are named here rather than discovered, because a checkout has no
# installed distribution metadata: run.sh and the MCP server both start the tool
# as `python -m metro_map_tool.metro_map`, and entry-point-only discovery would
# leave every checkout with no importers at all. Entry points are additive.
_BUILTIN = ("git", "github", "jira")
_GROUP = "metro_map_tool.sources"

_cache: Optional[Dict[str, Source]] = None
_broken: List[str] = []


def catalogue() -> Dict[str, Source]:
    """Every source that loaded, built once, built-ins first.

    A third-party plugin that explodes on import costs the user that one source,
    not the tool: a stale importer somebody pip-installed last year is no reason
    `metro-map map.json` should stop rendering. What failed is remembered by
    name and said when the list is asked for — never printed at import time,
    because the MCP server must not write to stdout and the renderer has no
    business talking about plugins nobody asked about.
    """
    global _cache
    if _cache is not None:
        return _cache
    found: Dict[str, Source] = {}
    for mod in _BUILTIN:
        # a built-in failing to import is our own bug, and should be loud
        src = importlib.import_module(f".{mod}", __name__).SOURCE
        found[src.name] = src

    from importlib.metadata import entry_points   # local; only this path needs it
    try:
        plugins = list(entry_points(group=_GROUP))
    except Exception as exc:                      # a broken environment, not a plugin
        plugins = []
        _broken.append(f"could not read plugin entry points: {exc}")
    for ep in plugins:
        try:
            obj = ep.load()
            src = obj() if not isinstance(obj, Source) and callable(obj) else obj
            problem = _rejected(src, found)
            if problem:
                _broken.append(f"{ep.name}: {problem}")
                continue
            found[src.name] = src
        except Exception as exc:                  # deliberately broad; see above.
            # BaseException is not caught: a plugin raising KeyboardInterrupt or
            # SystemExit should still stop the program.
            _broken.append(f"{ep.name}: {type(exc).__name__}: {exc}")
    _cache = found
    return found


def _rejected(src: object, found: Dict[str, Source]) -> str:
    """Why this object cannot be trusted as a source, or "" if it can.

    Catching structural rubbish at load time beats an AttributeError three
    layers into a render, with nothing to say which plugin caused it.
    """
    if not isinstance(src, Source):
        return f"not a Source (got {type(src).__name__})"
    if not src.name or not src.name.replace("-", "_").isidentifier():
        return f"'{src.name}' is not a usable source name"
    if src.name in found:
        return f"the name '{src.name}' is already taken"
    if not callable(src.fetch) or not callable(src.build):
        return "fetch and build must both be callable"
    seen = set()
    for opt in src.all_options():
        if opt.kind not in KINDS:
            return f"option '{opt.name}' has an unknown kind '{opt.kind}'"
        if opt.name in seen:
            return f"option '{opt.name}' is declared twice"
        seen.add(opt.name)
    return ""


def broken() -> List[str]:
    """Plugins that failed to load, as sentences. Empty when all is well."""
    catalogue()
    return list(_broken)


def get(name: str) -> Source:
    """One source by name, or a SourceError that says what is available."""
    have = catalogue()
    if name in have:
        return have[name]
    said = f"no source called '{name}' — known: " + ", ".join(sorted(have))
    if _broken:
        said += f"; {len(_broken)} plugin(s) failed to load: " + "; ".join(_broken)
    raise SourceError(said)


def describe(src: Source) -> dict:
    """A source as JSON, for --sources, /api/sources and the MCP catalogue."""
    return {"name": src.name, "title": src.title, "summary": src.summary,
            "options": [o.as_json() for o in src.all_options()],
            "env": env_status(src)}
