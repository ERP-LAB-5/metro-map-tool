---
name: metrofy
description: Make a metro map from code and only display it — a process as stations, the work moving through it as lines (planned, building, live, dropped), zones as lanes, and scenarios as trips a traveller rides — validated, laid out automatically, and checked, into one standalone HTML page with a details panel. Use when a map must be derived from something else rather than drawn: a migration's stories riding a to-be process, what-if scenarios to compare as alternative journeys, a pipeline's work streams by status, or any "things moving through ordered steps" an agent or a program generates. Triggers on "metrofy", "make a metro map of", "show the stories on a map", "scenarios as trips", "process map from data". For a map a person draws and edits by hand, use the metro-map skill and its designer instead.
---

# Metrofy

Write **typed JSON that names things**; Metrofy places, renders and checks. Never choose
coordinates, never hand-edit the page — change the input and run `finalize` again.

```bash
metrofy schema                                   # the input's JSON Schema
metrofy check input.json                         # problems, one sentence per line
metrofy finalize input.json map.html --spec spec.json
```

`finalize` prints a receipt and exits 1 when any gate fails; a failed run writes no page.
Read the receipt's `errors`, fix the input, run it again. Do not report success on a non-zero
exit.

## The input (format 1)

```json
{
  "metrofy": 1,
  "title": "ACME shop: from the ERP to the storefront",
  "zones": [{"id": "erp", "name": "ERP"}, {"id": "shop", "name": "Storefront"}],
  "steps": [
    {"id": "present", "label": "Present", "zone": "shop"},
    {"id": "describe", "label": "Describe", "zone": "shop", "parent": "present"},
    {"id": "price", "label": "Price", "zone": "erp", "details": {"does": "list and market prices"}}
  ],
  "lines": [
    {"id": "by-maker", "name": "Browse by maker", "status": "building",
     "steps": ["present", "describe"], "details": {"story": "As a shopper I want ..."}}
  ],
  "trips": [
    {"id": "as-tag", "name": "Maker as a tag", "line": "by-maker",
     "stops": ["present", "describe"], "details": {"what if": "...", "cost": "..."}}
  ]
}
```

- **zones**: lanes, top to bottom. Every step sits in one.
- **steps**: in process order. That order is the map's left to right. A `parent` must be
  listed first, and is drawn as an interchange.
- **lines**: work riding the process. `status` is `planned` (dotted), `building` (dashed),
  `live` or `dropped`. It needs two or more steps, and is drawn through them in process order.
  A piece of work touching one step has no line: put it in that step's `details`.
- **trips**: a scenario played out. Each consecutive pair of stops must be joined by a line,
  either a story or "The process", which runs through every step unless `"spine": false`.
  Two scenarios of one story are two trips on the same line.
- **details**: name → text, shown in the panel when the item is clicked. Put the story, the
  acceptance criteria, cost and effort here, not in labels.
- **ids**: lowercase letters, digits, `-` and `_`.

## What the page does

It shows the map (light and dark) and lists the trips. Choosing a trip plays it alone and
shows its details. Clicking a station or a line shows its details. It is one file with no
network, and it changes nothing.
