# Visualizing the graph

`workgraph viz` draws the built graph (`<home>/out/graph.json`) as a file you open in a browser. This note records the
options we looked at and why the command works the way it does.

```bash
workgraph viz                                    # default engine (cytoscape), readable overview, opens the browser
workgraph viz --engine pyvis                     # or graphify, graphviz
workgraph viz --list-engines                     # which engines are ready on this machine
workgraph viz --layer product                    # filters: --layer --project --kind --stage
workgraph viz --node "Checkout" --hops 2         # one node and its neighbourhood
workgraph viz --all --max-nodes 800 -o map.html  # include symbols, raise the cap, choose the file
```

## What existed before

The Streamlit app (`workgraph ui`) draws one selected node and up to 40 direct neighbours with Graphviz DOT
(`query.to_dot`). Nothing drew the whole graph or a filtered part of it.

## The problem: a graph is not a picture

A real build is large. A mid-size project is about 1,300 nodes and 5,700 edges, and about 65% of the nodes are code
symbols and rationale comments. Two things make a raw drawing useless:

- **Size.** Force layouts degrade past roughly 1,000 to 2,000 nodes.
- **Hubs.** Every file has an `in_stage` and a `belongs_to` edge to a few stage and project nodes, so a drawing of the
  whole graph is a hairball around those hubs.

So the work is mostly in choosing *what to draw*, and only then in the engine. `viz.select()` is engine-independent:

| Step | Default | Switch |
|---|---|---|
| Hide `symbol`, `external`, `rationale` nodes | on | `--all` |
| Hide `in_stage` / `belongs_to` edges into stage and project hubs | on | `--hubs` |
| Filter by layer, project, kind, stage | none | `--layer --project --kind --stage` |
| Neighbourhood of one node (label or id) | off | `--node X --hops N` (default 2) |
| Keep the best-connected N nodes | 400 | `--max-nodes N` |
| Drop nodes left with no edges | on | |

The command prints what it cut ("showing the 400 best-connected of 1,210 nodes"), so a trimmed picture is never
mistaken for the whole graph. On the 1,337-node sample graph the default view is 91 nodes and shows the Python
backend, the JavaScript frontend and the process stages as separate clusters.

## Visual encoding (all engines)

- Colour = layer: repo blue, product green, org orange, process purple.
- Size = degree.
- Diamond = structure node (stage, project, process).
- Dashed edge = `INFERRED` or `AMBIGUOUS`; solid = `EXTRACTED`.
- Tooltip / side panel: kind, layer, project, stages, source file, status, origin.

## Engines

`--engine <name>` chooses one; `--list-engines` shows readiness. Output goes to `<home>/out/graph-<engine>.html`
(`.svg` for graphviz) unless `-o` is given.

| Engine | Output | Needs | Good for | Limits |
|---|---|---|---|---|
| **cytoscape** (default) | one HTML file | nothing: Cytoscape.js 3.30.2 (MIT) is bundled in zinet | everyday use; best scaling; works offline | built-in layouts only; styling is ours |
| **pyvis** | one HTML file | `pip install 'workgraph[viz]'` | quick physics-based exploration, drag nodes | slow and messy above about 1,000 nodes; dated look |
| **graphify** | HTML | nothing (ships with graphify) | community-coloured overview; same look as graphify's own report | recomputes communities on the selected subgraph; graphify may refuse very large graphs |
| **graphviz** | SVG | the `dot` program | small, static, printable diagrams | layout is slow and unreadable for hundreds of nodes; writes `.dot` text if `dot` is missing |

### Why cytoscape is the default

- It needs no install and no network. The library is inlined into the page (about 365 KB), so the file works offline
  and nothing is fetched from a CDN. This matters for a tool whose rule is that data stays on your machine.
- It scales better than a physics-only renderer and has real layouts (`cose`, `breadthfirst`, `concentric`, `circle`,
  `grid`) that you switch in the page.
- The page has a search box (matches fade the rest), click-to-focus (neighbourhood highlighted, links listed in the
  side panel), and a dark theme that follows the system setting.

### Why keep the others

- **pyvis** is what many people already know, and it is the quickest way to drag nodes around.
- **graphify** gives graphify users the view they expect, with community colouring.
- **graphviz** is the only one that gives a static image for documents.

## Layout of the code

- `src/workgraph/viz.py`: `workgraph_style()`, `select()` (predicates over zinet's) and `render()`. No UI imports. The engines, the
  `ENGINES` registry and the bundled Cytoscape.js now live in zinet (`zinet.vis`, shared with GraphLang); see its README.
- `cli.py::cmd_viz`: the `viz` command. `ui/streamlit/app.py::map_tab`: the Map tab.
- `tests/test_viz.py`: selection rules, every engine's output, the CLI.

Adding an engine means adding one function and one registry entry; the CLI's `--engine` choices list is the only
other place to touch.

## The Map tab

`workgraph ui` has a Map tab beside Overview, Search, Browse and Gaps. It draws the same Cytoscape page, embedded in
the app (`st.iframe`, or `components.html` on older Streamlit), from `viz.select()` with:

- the sidebar filters (layer, project, kind, stage);
- "Only around the selected node" plus Hops: pick a node in Search, Browse or Gaps, then open Map;
- Max nodes, "Symbols and code" and "Stage/project links", which match `--max-nodes`, `--all` and `--hubs`.

It only offers cytoscape; the other engines write files, so use `workgraph viz --engine ...` for those.

## Not done yet


- Click-through from a node in the map to `workgraph show` or to its source file.
- Overview mode that collapses each project and each process to one node and expands on click, for graphs that stay
  too big after filtering.
- Highlighting gaps (`workgraph gaps`) on the map.
