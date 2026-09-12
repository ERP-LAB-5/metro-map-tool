#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
archimate.py — an ArchiMate implementation-and-migration model as a roadmap.

ArchiMate already has the vocabulary a roadmap needs, so nothing here invents
one. The mapping is the one the specification implies:

    WorkPackage           a stream of work        a line
    WorkPackage (child)   a piece of that work    a station on the parent's line
    Deliverable           what the work produces  a station
    ImplementationEvent   a dated moment          a station
    Plateau               a state reached         a capsule across the lanes
    Gap                   what is still missing   said in the notes

**A plateau carries no date of its own, and must not.** A plateau is a state,
and a state is reached when the work bringing it about finishes — so the date is
computed from the work that realises it. Storing it separately creates something
that can contradict the work underneath it, silently. That rule is not ours: it
is how the model this reads is built, and copying it here is what keeps the two
from disagreeing.

Turtle in, JSON out of `fetch`, spec out of `build`. The split matters more than
usual here: `fetch` is the only part that needs to understand RDF, so the
mapping — the part that actually goes wrong — is testable from a recorded
payload with no RDF library installed at all.

    metro-map --from archimate --opt file=platform-roadmap.ttl -o roadmap.svg

The reader handles the Turtle that model generators emit: prefixes, IRIs, blank
nodes, quoted literals, `;` and `,` continuations. It is deliberately not a
complete Turtle implementation — when `rdflib` is importable it is used instead,
because a real parser beats a small one on anything hand-edited.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

from . import Option, Source, SourceError
from . import plan

ARCHIMATE = "https://purl.org/archimate#"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"

# The element types this mapping knows. Anything else in the model is left
# alone: a roadmap is not the place to draw an application component.
LANE_TYPE = "WorkPackage"
STATION_TYPES = ("WorkPackage", "Deliverable", "ImplementationEvent")

# ArchiMate says nothing about status values, so these are the ones the
# generators in this house write. An unknown value is treated as "not started",
# which is the safe way to be wrong: it draws as planned rather than claiming
# something is finished.
STATE = {"done": "done", "closed": "done", "complete": "done",
         "in-progress": "active", "active": "active", "doing": "active",
         "planned": "todo", "open": "todo", "todo": "todo"}

DATE_KEYS = ("endDate", "end_date", "due", "date", "startDate", "start_date")


# ------------------------------------------------------------- turtle ----

def _tokens(text: str) -> Iterable[Tuple[str, str]]:
    """Terms and punctuation, in order. Comments and whitespace disappear."""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
            continue
        if c == "#":                                  # to end of line
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "<":
            end = text.find(">", i)
            if end < 0:
                raise SourceError("unterminated < > in the Turtle file")
            yield ("iri", text[i + 1:end])
            i = end + 1
            continue
        if c == '"':
            if text.startswith('"""', i):
                end = text.find('"""', i + 3)
                if end < 0:
                    raise SourceError("unterminated \"\"\" in the Turtle file")
                value, i = text[i + 3:end], end + 3
            else:
                out: List[str] = []
                j = i + 1
                while j < n and text[j] != '"':
                    if text[j] == "\\" and j + 1 < n:
                        out.append({"n": "\n", "t": "\t", "r": "\r"}
                                   .get(text[j + 1], text[j + 1]))
                        j += 2
                        continue
                    out.append(text[j])
                    j += 1
                if j >= n:
                    raise SourceError("unterminated string in the Turtle file")
                value, i = "".join(out), j + 1
            # a datatype or language tag decorates the literal; neither changes
            # what the mapping does with it, so both are stepped over
            if text.startswith("^^", i):
                if i + 2 < n and text[i + 2] == "<":
                    i = text.find(">", i) + 1
                else:
                    j = i + 2
                    while j < n and (text[j].isalnum() or text[j] in ":_-."):
                        j += 1
                    i = j
            elif i < n and text[i] == "@":
                j = i + 1
                while j < n and (text[j].isalnum() or text[j] == "-"):
                    j += 1
                i = j
            yield ("lit", value)
            continue
        if c in ";,.":
            yield ("punct", c)
            i += 1
            continue
        if c == "@":
            j = i
            while j < n and not text[j].isspace():
                j += 1
            yield ("dir", text[i:j])
            i = j
            continue
        j = i
        while j < n and not text[j].isspace() and text[j] not in ';,.<>"#':
            j += 1
        if j == i:                                    # a character we do not know
            raise SourceError(f"unexpected {text[i]!r} in the Turtle file")
        yield ("name", text[i:j])
        i = j


def _term(kind: str, value: str, prefixes: Dict[str, str]) -> dict:
    if kind == "iri":
        return {"iri": value}
    if kind == "lit":
        return {"lit": value}
    if value == "a":
        return {"iri": RDF_TYPE}
    if value.startswith("_:"):
        return {"bnode": value}
    if ":" in value:
        prefix, _, rest = value.partition(":")
        base = prefixes.get(prefix)
        if base is None:
            raise SourceError(f"the Turtle file uses prefix '{prefix}:' "
                              "without declaring it")
        return {"iri": base + rest}
    return {"lit": value}                             # a bare number or keyword


def read_turtle(text: str) -> List[Tuple[dict, dict, dict]]:
    """Triples from the Turtle a model generator writes.

    Not a complete implementation: no collections, no nested `[ ]`, no @base.
    Those do not appear in generated ABox files, and a file that uses them
    should be read with rdflib — which this module prefers whenever it is
    installed.
    """
    prefixes: Dict[str, str] = {}
    triples: List[Tuple[dict, dict, dict]] = []
    toks = list(_tokens(text))
    subject: Optional[dict] = None
    predicate: Optional[dict] = None
    i = 0
    while i < len(toks):
        kind, value = toks[i]
        if kind == "dir":
            if value.lower() == "@prefix":
                if i + 2 >= len(toks):
                    raise SourceError("a @prefix line is cut short")
                prefixes[toks[i + 1][1].rstrip(":")] = toks[i + 2][1]
                i += 3
                if i < len(toks) and toks[i] == ("punct", "."):
                    i += 1
            else:                                     # @base and friends: skip
                while i < len(toks) and toks[i] != ("punct", "."):
                    i += 1
                i += 1
            subject = predicate = None
            continue
        if kind == "punct":
            if value == ".":
                subject = predicate = None
            i += 1
            continue
        term = _term(kind, value, prefixes)
        i += 1
        if subject is None:
            subject = term
        elif predicate is None:
            predicate = term
        else:
            triples.append((subject, predicate, term))
            if i < len(toks) and toks[i] == ("punct", ";"):
                predicate = None
                i += 1
            elif i < len(toks) and toks[i] == ("punct", ","):
                i += 1
    return triples


def _with_rdflib(text: str) -> Optional[List[Tuple[dict, dict, dict]]]:
    """The same triples via rdflib, or None when it is not installed."""
    try:
        from rdflib import BNode, Graph, Literal   # type: ignore
    except ImportError:
        return None
    graph = Graph()
    graph.parse(data=text, format="turtle")
    out = []
    for s, p, o in graph:
        def term(node):
            if isinstance(node, Literal):
                return {"lit": str(node)}
            if isinstance(node, BNode):
                return {"bnode": f"_:{node}"}
            return {"iri": str(node)}
        out.append((term(s), term(p), term(o)))
    return out


# -------------------------------------------------------------- fetch ----

def _local(iri: str) -> str:
    """The last meaningful piece of an IRI — `…#Plateau` or `…/Plateau/p2`."""
    tail = iri.rsplit("#", 1)[-1]
    return tail.rsplit("/", 1)[-1]


def _fetch(opts: dict, model: Optional[dict]) -> dict:
    """Read the model file and hand back plain JSON.

    Everything RDF stops here. What `build` receives is a list of elements with
    their properties and a list of relations — which is also what `--to-file`
    records, so a mapping can be re-run against the same model with no file and
    no parser.
    """
    from pathlib import Path
    path = Path(str(opts["file"])).expanduser()
    if not path.exists():
        raise SourceError(f"no such model file: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SourceError(f"could not read {path}: {exc.strerror}") from None

    triples = _with_rdflib(text)
    if triples is None:
        triples = read_turtle(text)

    types: Dict[str, str] = {}
    names: Dict[str, str] = {}
    docs: Dict[str, str] = {}
    ids: Dict[str, str] = {}
    props: Dict[str, Dict[str, str]] = {}
    # blank nodes carry one key/value pair each, and are attached separately
    bnode_key: Dict[str, str] = {}
    bnode_value: Dict[str, str] = {}
    attached: List[Tuple[str, str]] = []
    relations: List[dict] = []

    for subj, pred, obj in triples:
        s = subj.get("iri") or subj.get("bnode") or ""
        p = pred.get("iri") or ""
        if p == RDF_TYPE and obj.get("iri", "").startswith(ARCHIMATE):
            types[s] = _local(obj["iri"])
            continue
        if not p.startswith(ARCHIMATE):
            continue
        key = _local(p)
        if key == "name":
            names[s] = obj.get("lit", "")
        elif key == "documentation":
            docs[s] = obj.get("lit", "")
        elif key == "identifier":
            ids[s] = obj.get("lit", "")
        elif key == "propertyKey":
            bnode_key[s] = obj.get("lit", "")
        elif key == "propertyValue":
            bnode_value[s] = obj.get("lit", "")
        elif key == "hasProperty":
            target = obj.get("bnode") or obj.get("iri") or ""
            attached.append((s, target))
        elif obj.get("iri"):
            relations.append({"type": key, "source": s, "target": obj["iri"]})

    for owner, node in attached:
        key = bnode_key.get(node)
        if key:
            props.setdefault(owner, {})[key] = bnode_value.get(node, "")

    elements = []
    for iri, kind in sorted(types.items()):
        if kind not in STATION_TYPES and kind not in ("Plateau", "Gap"):
            continue
        elements.append({
            "id": ids.get(iri) or _local(iri),
            "iri": iri,
            "type": kind,
            "name": names.get(iri) or ids.get(iri) or _local(iri),
            "documentation": docs.get(iri, ""),
            "properties": props.get(iri, {}),
        })

    known = {e["iri"] for e in elements}
    kept = [r for r in relations if r["source"] in known and r["target"] in known]
    return {"model": path.name, "elements": elements, "relations": kept}


# -------------------------------------------------------------- build ----

def _date_of(element: dict) -> Optional[str]:
    """The date a thing is placed on: when it ends, else when it starts."""
    values = element.get("properties") or {}
    for key in DATE_KEYS:
        said = (values.get(key) or "").strip()
        if len(said) >= 10 and said[4] == "-" and said[7] == "-":
            return said[:10]
    return None


def _state_of(element: dict) -> str:
    said = ((element.get("properties") or {}).get("status") or "").strip().lower()
    return STATE.get(said, "todo")


def _build(data: dict, opts: dict, model: Optional[dict]) -> Tuple[dict, List[str]]:
    elements = {e["iri"]: e for e in data.get("elements") or []}
    if not elements:
        raise SourceError("no ArchiMate elements found — is this an "
                          "implementation and migration model?")
    relations = data.get("relations") or []
    notes: List[str] = []

    by_type = lambda kind: [e for e in elements.values() if e["type"] == kind]

    # A work package inside another belongs to its parent's stream, however
    # deep it is nested.
    parent: Dict[str, str] = {}
    for rel in relations:
        if rel["type"] in ("composition", "aggregation"):
            if (elements[rel["source"]]["type"] == LANE_TYPE
                    and elements[rel["target"]]["type"] == LANE_TYPE):
                parent[rel["target"]] = rel["source"]

    def top(iri: str) -> str:
        seen = set()
        while iri in parent and iri not in seen:
            seen.add(iri)
            iri = parent[iri]
        return iri

    # What produces what, and what a work package triggers: both say which
    # stream a dateless thing belongs to.
    made_by: Dict[str, str] = {}
    for rel in relations:
        if rel["type"] not in ("realization", "triggering"):
            continue
        source, target = elements[rel["source"]], elements[rel["target"]]
        if source["type"] == LANE_TYPE and target["type"] in STATION_TYPES:
            made_by.setdefault(target["iri"], source["iri"])

    lanes_wanted = bool(opts.get("include_deliverables", True))
    want_events = bool(opts.get("include_events", True))

    items: List[dict] = []
    undated: List[str] = []
    for element in elements.values():
        kind = element["type"]
        if kind not in STATION_TYPES:
            continue
        if kind == "Deliverable" and not lanes_wanted:
            continue
        if kind == "ImplementationEvent" and not want_events:
            continue

        owner = element["iri"] if kind == LANE_TYPE else made_by.get(element["iri"])
        if owner is None:
            undated.append(f"{element['name']} ({kind.lower()}) is not produced "
                           "or triggered by any work package")
            continue
        lane = elements[top(owner)]["name"]

        date = _date_of(element)
        if date is None and kind != LANE_TYPE:
            # A deliverable rarely carries a date of its own; it is finished
            # when the work that produces it is. Same rule as the plateau, one
            # level down.
            date = _date_of(elements[owner])
        if date is None:
            undated.append(f"{element['name']} has no date")
            continue

        items.append({
            "id": element["id"],
            "label": element["name"],
            "lane": lane,
            "date": date,
            "origin": f"archimate:{kind}/{element['id']}",
            "state": _state_of(element),
        })

    if undated:
        shown = "; ".join(sorted(undated)[:4])
        more = f" and {len(undated) - 4} more" if len(undated) > 4 else ""
        notes.append(f"left out for want of a date or a work package: {shown}{more}")
    if not items:
        raise SourceError(
            "nothing datable found — a roadmap places work by when it is due, so "
            "give the work packages a startDate or endDate property")

    # A plateau is a state, reached when the work realising it finishes. It
    # stores no date of its own, and computing it here is what keeps this from
    # contradicting the model.
    groups: List[dict] = []
    by_id = {e["iri"]: e for e in elements.values()}
    placed = {it["id"]: it for it in items}
    for plateau in by_type("Plateau"):
        members, dates = [], []
        for rel in relations:
            if rel["type"] != "realization" or rel["target"] != plateau["iri"]:
                continue
            source = by_id.get(rel["source"])
            if source is None:
                continue
            item = placed.get(source["id"])
            if item:
                members.append(item["id"])
                dates.append(item["date"])
        if not dates:
            continue
        groups.append({"name": plateau["name"], "date": max(dates),
                       "origin": f"archimate:Plateau/{plateau['id']}",
                       "members": members})

    # A gap is what is still missing to reach a state. It is not a thing with a
    # date, so it is said rather than drawn — silently dropping it would be the
    # one loss that matters.
    for gap in by_type("Gap"):
        notes.append(f"gap — {gap['name']}")

    lane_order = [ln.get("name") for ln in (model or {}).get("lines") or []]
    try:
        spec, more = plan.build(items, groups, source="archimate",
                                interval=str(opts.get("interval") or ""),
                                done_mark=str(opts.get("done_mark") or ""),
                                lane_order=lane_order)
    except ValueError as exc:
        raise SourceError(f"{exc} — the dates come from startDate and endDate "
                          "properties on the work packages") from None
    return spec, notes + more


SOURCE = Source(
    name="archimate",
    title="ArchiMate implementation and migration model",
    summary="work packages as lines, deliverables and events as stations, "
            "plateaus as the moments the lanes meet",
    options=(
        Option("file", "the model file, in Turtle", kind="path", required=True,
               placeholder="platform-roadmap.ttl"),
        Option("include_deliverables", "place deliverables as well as the work "
                                       "packages that produce them",
               kind="bool", default=True),
        Option("include_events", "place implementation events",
               kind="bool", default=True),
        Option("interval", "column width; by default one is chosen to suit the span",
               kind="choice", choices=("", "day", "week", "month", "quarter", "year"),
               default=""),
        Option("done_mark", "put this in front of a finished item's label",
               default="✓"),
    ),
    fetch=_fetch,
    build=_build,
)
