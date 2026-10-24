"""Draw the built graph. UI-free: `select` picks a readable subgraph, an engine turns it into a file.

The engines are zinet's (`zinet.vis`); this module only says how a workgraph graph looks and what to leave out:
colour by layer, circles sized by how connected a node is with the label below, a force layout, dashed edges for what was
inferred, and symbols / externals / rationale nodes and stage/project hub edges hidden unless asked for.

Engines (`workgraph viz --engine NAME`):
  cytoscape  one self-contained HTML file; Cytoscape.js is bundled in zinet, so it works offline. Default.
  graphviz   an image through the `dot` program (svg by default; DOT text if `dot` is not installed).
  mermaid    Mermaid flowchart text; ascii a plain-text outline.
  pyvis      interactive HTML via the pyvis package (`pip install workgraph[viz]`).
  graphify   graphify's own community-coloured HTML export.
"""
from __future__ import annotations

from pathlib import Path

import networkx as nx
from zinet import vis
from zinet.vis import DEFAULT_ENGINE, DEFAULT_MAX_NODES, ENGINES, Style, graphify_ready, pyvis_ready

from . import paths
from .query import HIDDEN_KINDS

LAYER_COLORS = {"repo": "#4f8ef7", "product": "#2fa84f", "org": "#e08a1e", "process": "#9b59b6"}
OTHER_COLOR = "#8a8f98"
STRUCTURE_KINDS = {"stage", "project", "process"}
HUB_RELATIONS = {"in_stage", "belongs_to"}
INFO_KEYS = ("kind", "layer", "project", "stages", "source_file", "status", "origin")

__all__ = ["DEFAULT_ENGINE", "DEFAULT_MAX_NODES", "ENGINES", "color", "cytoscape_html", "graphify_ready", "hint",
           "open_in_browser", "pyvis_ready", "render", "select", "workgraph_style"]


def color(d: dict) -> str:
    return LAYER_COLORS.get(d.get("layer", ""), OTHER_COLOR)


def _info(n: str, d: dict) -> str:
    return "\n".join([str(d.get("label", n))] + [f"{k}: {d[k]}" for k in INFO_KEYS if d.get(k) not in (None, "", [])])


def _size(G: nx.Graph, n: str, d: dict) -> int:
    return min(14 + 3 * int(dict(G.degree)[n] ** 0.5 * 2), 46)


def workgraph_style(title: str = "workgraph") -> Style:
    return Style(
        label=lambda n, d: str(d.get("label", n)), kind=lambda n, d: d.get("layer"), color=lambda n, d: color(d),
        shape=lambda n, d: "diamond" if d.get("kind") in STRUCTURE_KINDS else "ellipse", info=_info,
        edge_kind=lambda u, v, d: d.get("relation", ""), edge_dashed=lambda u, v, d: d.get("confidence", "EXTRACTED") != "EXTRACTED",
        node_size=_size, arrangement="force", title=title)


def select(M: nx.MultiDiGraph, *, layer: str | None = None, project: str | None = None, kind: str | None = None,
           stage: str | None = None, node: str | None = None, hops: int = 2, include_code: bool = False,
           hubs: bool = False, max_nodes: int = DEFAULT_MAX_NODES) -> tuple[nx.MultiDiGraph, str]:
    """A readable subgraph of M and a one-line note on what was cut. Symbols, externals and rationale nodes are
    left out unless `include_code`, and stage/project hub edges unless `hubs`; past `max_nodes` the best-connected nodes are kept."""
    return vis.select(
        M,
        keep=lambda n, d: ((not layer or d.get("layer") == layer) and (not project or d.get("project") == project)
                           and (not kind or d.get("kind") == kind) and (not stage or stage in d.get("stages", []))),
        hidden=lambda n, d: d.get("kind") in HIDDEN_KINDS, show_hidden=include_code,
        drop_edge=None if hubs else (lambda G, u, v, d: d.get("relation") in HUB_RELATIONS and G.nodes[v].get("kind") in STRUCTURE_KINDS),
        node=node, hops=hops, max_nodes=max_nodes)


def cytoscape_html(sub: nx.MultiDiGraph, note: str = "", *, mark: set[str] | None = None, title: str = "workgraph") -> str:
    """The self-contained page. `mark` ids get a red ring (the node a view is about); `title` heads the bar."""
    return vis.cytoscape_html(sub, workgraph_style(title), note, mark)


def hint(text: str) -> str:
    """zinet's install hints name zinet's extras; workgraph users install pyvis through workgraph's."""
    return text.replace("zinet[pyvis]", "workgraph[viz]")


def render(M: nx.MultiDiGraph, engine: str = DEFAULT_ENGINE, out: str | Path | None = None, **select_args) -> tuple[Path, str, int]:
    """Select a subgraph and write it with `engine`. Returns (file, note, node count)."""
    if engine not in ENGINES:
        raise ValueError(f"unknown engine {engine!r}; choose from {sorted(ENGINES)}")
    sub, note = select(M, **select_args)
    if not sub.number_of_nodes():
        raise ValueError("nothing to draw: the filters match no nodes")
    target = Path(out) if out else paths.out_dir() / f"graph-{engine}{ENGINES[engine]['ext']}"
    try:
        vis.render(sub, target, engine, workgraph_style(), note)
    except RuntimeError as e:
        raise RuntimeError(hint(str(e))) from None
    return target, note, sub.number_of_nodes()


def open_in_browser(path: Path) -> bool:
    import webbrowser
    return webbrowser.open(path.resolve().as_uri())
