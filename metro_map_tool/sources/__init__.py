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
    """A credential a source needs, wherever it is kept.

    Not an Option, and that is the whole security design: an option's value
    travels through argparse, an HTTP body and a saved spec, and a token must
    travel through none of those.

    The environment variable and the line in the config file are two views of
    one declaration, so a source says what it needs once and both ways of
    supplying it follow.
    """
    name: str                   # JIRA_API_TOKEN
    help: str
    required: bool = True
    key: str = ""               # the key in the config file; defaults from name
    secret: bool = False        # never echoed back to anyone, ever
    placeholder: str = ""

    def config_key(self, source: str) -> str:
        if self.key:
            return self.key
        bare = self.name.lower()
        prefix = f"{source.lower()}_"
        return bare[len(prefix):] if bare.startswith(prefix) else bare


@dataclasses.dataclass(frozen=True)
class Node:
    """One row in a source's discovery tree.

    Deliberately shallow and generic: the designer renders these without
    knowing what a Jira epic is, so a source that grows a browser gets a
    working one the same way it already gets a form from its options.
    """
    id: str                     # opaque to the UI, meaningful to the source
    label: str
    kind: str = "item"          # project | epicset | epic | issue | board | sprint
    hint: str = ""              # the secondary line: a status, a date, a count
    expandable: bool = False
    selectable: bool = True

    def as_json(self) -> dict:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class View:
    """One way of walking a source's tree — the same data, a different spine."""
    name: str
    title: str
    help: str = ""

    def as_json(self) -> dict:
        return dataclasses.asdict(self)


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
    # A source may let you look before you import. Both optional: one without
    # them simply has no browser, and the dialog falls back to its form.
    views: Tuple[View, ...] = ()
    browse: Optional[Callable[[List[str], dict, str], List[Node]]] = None

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
    Option("select", "the ids to import, as the browser would have picked them "
                     "— leave empty to take everything in scope", kind="csv"),
)


# ------------------------------------------------------------- settings --
#
# Credentials come from the environment, or from a small config file per source.
# The environment wins, so a one-off override and CI keep working exactly as
# they did when it was the only way.

CONFIG_HOME = "METRO_MAP_CONFIG_HOME"


def config_path(source: str) -> "Path":
    """Where a source's settings live.

    $METRO_MAP_<SOURCE>_CONFIG points at a specific file — which is how an
    existing config.conf from somewhere else gets reused without copying a
    token about. Otherwise it is one file per source under the config home,
    because a plugin owning its own settings is what lets it be lifted out into
    its own package later without dragging a shared file with it.
    """
    import os
    named = os.environ.get(f"METRO_MAP_{source.upper()}_CONFIG")
    if named:
        return Path(named).expanduser()
    home = os.environ.get(CONFIG_HOME) or os.environ.get("XDG_CONFIG_HOME") \
        or str(Path.home() / ".config")
    return Path(home).expanduser() / "metro-map" / f"{source}.conf"


def read_config(source: str) -> Dict[str, str]:
    """A source's saved settings, or {} when there are none.

    A broken file is treated as empty rather than raised: the tool still has
    the environment to fall back on, and refusing to start because a config
    file has a stray bracket in it helps nobody.
    """
    import configparser
    path = config_path(source)
    if not path.exists():
        return {}
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except configparser.Error:
        return {}
    if not parser.has_section(source):
        return {}
    return {k: v for k, v in parser.items(source) if v != ""}


def write_config(source: str, values: Dict[str, str]) -> "Path":
    """Save settings, merged over whatever is already there.

    Written 0600 from the moment it exists — created with that mode rather than
    chmod'd afterwards, so there is no instant where a token sits in a
    world-readable file. A value given as "" clears that key rather than
    storing an empty one.
    """
    import configparser
    import os
    path = config_path(source)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass                                    # a shared config home is the
                                                # user's business, not ours
    parser = configparser.ConfigParser()
    if path.exists():
        try:
            parser.read(path, encoding="utf-8")
        except configparser.Error:
            parser = configparser.ConfigParser()
    if not parser.has_section(source):
        parser.add_section(source)
    for key, value in values.items():
        if value == "" or value is None:
            parser.remove_option(source, key)
        else:
            parser.set(source, key, str(value))

    tmp = path.with_name(path.name + ".part")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        parser.write(handle)
    os.replace(tmp, path)
    return path


def credentials(src: Source) -> Dict[str, str]:
    """Everything a source needs to authenticate, or a SourceError saying what
    is missing and both of the places it could come from."""
    import os
    saved = read_config(src.name)
    out: Dict[str, str] = {}
    missing: List[str] = []
    for item in src.env:
        key = item.config_key(src.name)
        value = (os.environ.get(item.name) or saved.get(key) or "").strip()
        if value:
            out[key] = value
        elif item.required:
            missing.append(item.name)
    if missing:
        where = config_path(src.name)
        raise SourceError(
            f"{src.name} is not configured — missing {', '.join(missing)}. "
            f"Set them in the environment, or in {where}, or fill them in under "
            "Settings in the designer.")
    return out


def env_status(src: Source) -> List[dict]:
    """Which credentials a source wants, and whether each one is set.

    Presence only, and where it came from — never the value. This answer
    travels to a browser over HTTP, so there is nothing here that could be
    worth intercepting.
    """
    import os
    saved = read_config(src.name)
    out = []
    for item in src.env:
        key = item.config_key(src.name)
        in_env = bool(os.environ.get(item.name))
        in_file = bool(saved.get(key))
        out.append({"name": item.name, "key": key, "help": item.help,
                    "required": item.required, "secret": item.secret,
                    "placeholder": item.placeholder,
                    "present": in_env or in_file,
                    "from": "environment" if in_env
                            else ("settings" if in_file else "")})
    return out


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
            "env": env_status(src),
            "views": [v.as_json() for v in src.views],
            "browsable": src.browse is not None,
            "config_path": str(config_path(src.name))}
