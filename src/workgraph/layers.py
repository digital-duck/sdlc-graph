"""The layer graph and per-layer partitions of a built graph.

Two graphs. The *layer graph* is small: its nodes are layers (repo, product, org, process) and its
edges are the dependencies between them (`repo` -belongs_to-> `product`). It is derived from the
vocabulary in `schema.py`, so there is one source of truth. The *instance graph* is the knowledge
graph; every node already carries a `layer` attribute naming a node of the layer graph.

Coarseness is relative: a layer is coarser than the layers that point at it. `rank` counts that
(finest layer 0) and is computed, never stored in a file, so adding a layer shifts the ranks.

A build also writes the instance graph in partitions (`<out>/layers/`), so a view can load the layers
it needs instead of the whole graph: `layers.json` (the layer graph with counts), `<layer>.json`
(that layer's nodes and the edges inside it) and `cross.json` (edges between layers).
"""
from __future__ import annotations

import json
from pathlib import Path

import networkx as nx

from . import paths
from .schema import BUILD_RELATIONS, LAYER_KINDS, RELATION_RULES, layer_of

FORMAT = "workgraph-layers/1"
CROSS = "cross"
META = "layers"
FINEST = "repo"      # the layer of nodes built from projects; it has no layer file


def layer_graph() -> nx.DiGraph:
    """Layers as nodes, dependencies as edges (each with the `relations` that realise it), with a computed
    `rank` (0 = finest)."""
    G = nx.DiGraph()
    for name in [FINEST, *sorted(LAYER_KINDS)]:
        G.add_node(name, kinds=sorted(LAYER_KINDS.get(name, [])), hierarchical=name != FINEST)
    for relation, rule in [*RELATION_RULES.items(), *BUILD_RELATIONS.items()]:
        if rule is None:
            continue
        for src in rule[0]:
            for tgt in rule[1]:
                if src != tgt:
                    if G.has_edge(src, tgt):
                        G.edges[src, tgt]["relations"].append(relation)
                    else:
                        G.add_edge(src, tgt, relations=[relation])
    if not nx.is_directed_acyclic_graph(G):
        raise ValueError("layer dependencies contain a cycle; coarseness would be undefined")
    for rank, generation in enumerate(nx.topological_generations(G)):
        for name in generation:
            G.nodes[name]["rank"] = rank
    return G


def coarser(G: nx.DiGraph, layer: str) -> list[str]:
    """Layers `layer` points at, i.e. the ones that contain or describe it."""
    return sorted(G.successors(layer), key=lambda n: (G.nodes[n]["rank"], n))


def finer(G: nx.DiGraph, layer: str) -> list[str]:
    """Layers that point at `layer`."""
    return sorted(G.predecessors(layer), key=lambda n: (G.nodes[n]["rank"], n))


def by_coarseness(G: nx.DiGraph) -> list[str]:
    """Coarsest first, ties by name."""
    return sorted(G.nodes, key=lambda n: (-G.nodes[n]["rank"], n))


def node_layer(M: nx.MultiDiGraph, node: str) -> str:
    return M.nodes[node].get("layer") or layer_of(node)


def layers_dir(out: str | Path | None = None) -> Path:
    return Path(out) if out else paths.out_dir() / "layers"


# ---- writing -------------------------------------------------------------------------------------

def _dump(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, default=str), encoding="utf-8")


def write_partitions(M: nx.MultiDiGraph, out: str | Path | None = None) -> Path:
    """Write the layer graph, one file per layer and the cross-layer edges. Replaces earlier output."""
    d = layers_dir(out)
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob("*.json"):
        old.unlink()
    L = layer_graph()
    members: dict[str, list[str]] = {name: [] for name in L}
    for n in M.nodes:
        members.setdefault(node_layer(M, n), []).append(n)
    cross: list[dict] = []
    cross_count: dict[tuple[str, str, str], int] = {}
    inner_count = dict.fromkeys(members, 0)
    for u, v, data in M.edges(data=True):
        lu, lv = node_layer(M, u), node_layer(M, v)
        if lu == lv:
            inner_count[lu] += 1
        else:
            cross.append({"source": u, "target": v, **data})
            k = (lu, lv, data.get("relation", ""))
            cross_count[k] = cross_count.get(k, 0) + 1
    for name, ids in members.items():
        _dump(d / f"{name}.json", nx.node_link_data(M.subgraph(ids), edges="edges"))
    _dump(d / f"{CROSS}.json", {"edges": cross})
    meta = {
        "format": FORMAT,
        "layers": [{"id": n, **{k: L.nodes[n][k] for k in ("rank", "hierarchical", "kinds")},
                    "nodes": len(members.get(n, [])), "edges": inner_count.get(n, 0)} for n in by_coarseness(L)],
        "dependencies": [{"source": u, "target": v, "relation": r, "edges": cross_count.get((u, v, r), 0)}
                         for u, v, data in L.edges(data=True) for r in data["relations"]],
        "undeclared": [{"source": a, "target": b, "relation": r, "edges": c}
                       for (a, b, r), c in sorted(cross_count.items())
                       if not (L.has_edge(a, b) and r in L.edges[a, b]["relations"])],
    }
    _dump(d / f"{META}.json", meta)
    return d


# ---- reading -------------------------------------------------------------------------------------

def available(out: str | Path | None = None) -> bool:
    return (layers_dir(out) / f"{META}.json").is_file()


def load_meta(out: str | Path | None = None) -> dict:
    p = layers_dir(out) / f"{META}.json"
    if not p.is_file():
        raise FileNotFoundError(f"no layer partitions at {p.parent}: run `workgraph build` first")
    return json.loads(p.read_text(encoding="utf-8"))


def load_layer(name: str, out: str | Path | None = None) -> nx.MultiDiGraph:
    """One layer on its own: its nodes and the edges inside it."""
    p = layers_dir(out) / f"{name}.json"
    if not p.is_file():
        raise FileNotFoundError(f"no partition for layer {name!r} at {p}")
    return nx.node_link_graph(json.loads(p.read_text(encoding="utf-8")), edges="edges")


def load_cross(out: str | Path | None = None) -> list[dict]:
    p = layers_dir(out) / f"{CROSS}.json"
    return json.loads(p.read_text(encoding="utf-8"))["edges"] if p.is_file() else []


def load_scope(names: list[str], out: str | Path | None = None) -> nx.MultiDiGraph:
    """The named layers together: their partitions plus the cross edges that stay inside the scope."""
    G = nx.MultiDiGraph()
    for name in names:
        G.update(load_layer(name, out))
    for e in load_cross(out):
        if e["source"] in G and e["target"] in G:
            G.add_edge(e["source"], e["target"], **{k: v for k, v in e.items() if k not in ("source", "target")})
    return G


def context(node: str, out: str | Path | None = None) -> dict:
    """A node with its neighbours, loading only the partitions it needs: its own layer, and the layers
    its cross-layer neighbours live in. Neighbours are grouped by the neighbour's layer relative to the
    node's: `coarser` (higher rank), `finer` (lower), `same` (same layer), `beside` (other layer, equal rank)."""
    L = layer_graph()
    layer = layer_of(node)
    G = load_layer(layer, out)
    if node not in G:
        raise KeyError(f"{node!r} is not in layer {layer!r}")
    links: list[tuple[str, str, str, str]] = []            # (direction, relation, other id, other layer)
    for u, v, data in G.out_edges(node, data=True):
        links.append(("out", data.get("relation", ""), v, layer))
    for u, v, data in G.in_edges(node, data=True):
        links.append(("in", data.get("relation", ""), u, layer))
    for e in load_cross(out):
        if e["source"] == node:
            links.append(("out", e.get("relation", ""), e["target"], layer_of(e["target"])))
        elif e["target"] == node:
            links.append(("in", e.get("relation", ""), e["source"], layer_of(e["source"])))
    cache: dict[str, nx.MultiDiGraph] = {layer: G}
    rank = L.nodes[layer]["rank"] if layer in L else 0
    out_groups: dict[str, list[dict]] = {"coarser": [], "finer": [], "same": [], "beside": []}
    for direction, relation, other, other_layer in links:
        if other_layer not in cache:
            cache[other_layer] = load_layer(other_layer, out)
        attrs = cache[other_layer].nodes[other] if other in cache[other_layer] else {}
        r = L.nodes[other_layer]["rank"] if other_layer in L else rank
        group = "same" if other_layer == layer else "coarser" if r > rank else "finer" if r < rank else "beside"
        out_groups[group].append({"direction": direction, "relation": relation, "id": other,
                                  "label": attrs.get("label", other), "kind": attrs.get("kind", ""),
                                  "layer": other_layer})
    return {"id": node, "layer": layer, "attrs": dict(G.nodes[node]), "loaded": sorted(cache), **out_groups}


def below(node: str, child_layer: str, out: str | Path | None = None) -> nx.MultiDiGraph:
    """The child graph of a node: what `child_layer` holds under it, with the relations among those nodes.

    Starts from the child-layer nodes linked to `node` or to anything inside it (its `part_of` descendants
    in its own layer), then adds what those contain: their own `part_of` descendants, and for a project
    node every node of that project (files, documents, requirements, symbols). Loads two partitions,
    the node's layer and `child_layer`. Seeds (the directly linked nodes) carry `seed=True`.
    Only adjacent layers are linked; a layer two steps down gives an empty graph."""
    P = load_layer(layer_of(node), out)
    if node not in P:
        raise KeyError(f"{node!r} is not in layer {layer_of(node)!r}")
    C = load_layer(child_layer, out)
    scope = {node} | _inside(P, node)
    seeds: set[str] = set()
    for e in load_cross(out):
        a, b = e["source"], e["target"]
        if a in scope and b in C:
            seeds.add(b)
        elif b in scope and a in C:
            seeds.add(a)
    members = set(seeds)
    for s in seeds:
        members |= _inside(C, s)
    projects = {C.nodes[s].get("project") for s in seeds if C.nodes[s].get("kind") == "project"} - {None, ""}
    if projects:
        members |= {n for n, d in C.nodes(data=True) if d.get("project") in projects}
    sub = C.subgraph(members).copy()
    for s in seeds:
        sub.nodes[s]["seed"] = True
    return sub


def _inside(G: nx.MultiDiGraph, node: str) -> set[str]:
    """Everything below `node` in its layer: `part_of` runs child -> parent, so those are its ancestors in the edge graph."""
    parents = nx.DiGraph((u, v) for u, v, d in G.edges(data=True) if d.get("relation") == "part_of")
    return set(nx.ancestors(parents, node)) if node in parents else set()


def hierarchy(G: nx.MultiDiGraph, limit: int = 500) -> list[tuple[int, str]]:
    """(depth, id) rows of a hierarchical layer, parents before their children, following `part_of`
    (child -> parent). Nodes with no parent in the layer are the roots. Cut off after `limit` rows."""
    children: dict[str, list[str]] = {}
    has_parent: set[str] = set()
    for u, v, data in G.edges(data=True):
        if data.get("relation") == "part_of" and u != v:
            children.setdefault(v, []).append(u)
            has_parent.add(u)
    rows: list[tuple[int, str]] = []
    stack = [(0, n) for n in sorted((n for n in G if n not in has_parent), reverse=True)]
    seen: set[str] = set()
    while stack and len(rows) < limit:
        depth, n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        rows.append((depth, n))
        stack.extend((depth + 1, c) for c in sorted(children.get(n, []), reverse=True))
    return rows


def to_dot(G: nx.DiGraph, meta: dict | None = None) -> str:
    """The layer graph as Graphviz source, coarsest layers at the top; with `meta` the counts are shown."""
    counts = {x["id"]: x for x in meta["layers"]} if meta else {}
    deps = {(x["source"], x["target"], x["relation"]): x["edges"] for x in meta["dependencies"]} if meta else {}
    lines = ["digraph layers {", "  rankdir=BT; node [shape=box, style=rounded, fontsize=12]; edge [fontsize=10];"]
    ranks: dict[int, list[str]] = {}
    for n, d in G.nodes(data=True):
        ranks.setdefault(d["rank"], []).append(n)
        c = counts.get(n)
        label = f"{n}\\n{c['nodes']} nodes, {c['edges']} edges" if c else n
        lines.append(f'  "{n}" [label="{label}"];')
    for members in ranks.values():
        lines.append("  { rank=same; " + "; ".join(f'"{m}"' for m in members) + "; }")
    for u, v, d in G.edges(data=True):
        parts = [r + (f" ({deps[u, v, r]})" if (u, v, r) in deps else "") for r in d["relations"]]
        label = "\\n".join(parts)               # a literal backslash-n: Graphviz's own line break
        lines.append(f'  "{u}" -> "{v}" [label="{label}"];')
    lines.append("}")
    return "\n".join(lines)
