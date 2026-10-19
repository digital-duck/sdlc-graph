"""Draw the built graph. UI-free: `select` picks a readable subgraph, an engine turns it into a file.

Engines (`workgraph viz --engine NAME`):
  cytoscape  one self-contained HTML file; Cytoscape.js is bundled, so it works offline. Default.
  pyvis      interactive HTML via the pyvis package (`pip install workgraph[viz]`).
  graphify   graphify's own community-coloured HTML export.
  graphviz   static SVG via the `dot` program (DOT text if `dot` is not installed).
"""
from __future__ import annotations

import html
import json
import shutil
import subprocess
from collections import deque
from importlib import resources
from pathlib import Path

import networkx as nx

from . import paths
from .query import HIDDEN_KINDS

LAYER_COLORS = {"repo": "#4f8ef7", "product": "#2fa84f", "org": "#e08a1e", "process": "#9b59b6"}
OTHER_COLOR = "#8a8f98"
STRUCTURE_KINDS = {"stage", "project", "process"}
DEFAULT_MAX_NODES = 400
HUB_RELATIONS = {"in_stage", "belongs_to"}


def color(d: dict) -> str:
    return LAYER_COLORS.get(d.get("layer", ""), OTHER_COLOR)


def select(M: nx.MultiDiGraph, *, layer: str | None = None, project: str | None = None, kind: str | None = None,
           stage: str | None = None, node: str | None = None, hops: int = 2, include_code: bool = False,
           hubs: bool = False, max_nodes: int = DEFAULT_MAX_NODES) -> tuple[nx.MultiDiGraph, str]:
    """A readable subgraph of M and a one-line note on what was cut. Symbols, externals and rationale nodes are
    left out unless `include_code`, and stage/project hub edges unless `hubs`; past `max_nodes` the best-connected nodes are kept."""
    notes = []
    if node:
        if node not in M:
            by_label = [n for n, d in M.nodes(data=True) if d.get("label") == node]
            if len(by_label) != 1:
                raise ValueError(f"no node {node!r}" if not by_label else f"{node!r} matches {len(by_label)} nodes; use the id")
            node = by_label[0]
        seen, queue = {node: 0}, deque([node])
        while queue:
            u = queue.popleft()
            if seen[u] >= hops:
                continue
            for v in set(M.successors(u)) | set(M.predecessors(u)):
                if v not in seen and (include_code or M.nodes[v].get("kind") not in HIDDEN_KINDS):
                    seen[v] = seen[u] + 1
                    queue.append(v)
        keep = set(seen)
        notes.append(f"{hops} hop(s) around {M.nodes[node].get('label', node)}")
    else:
        keep = set(M.nodes)

    def ok(n: str) -> bool:
        d = M.nodes[n]
        if n == node:
            return True
        if not include_code and d.get("kind") in HIDDEN_KINDS:
            return False
        return ((not layer or d.get("layer") == layer) and (not project or d.get("project") == project)
                and (not kind or d.get("kind") == kind) and (not stage or stage in d.get("stages", [])))

    keep = {n for n in keep if ok(n)}
    sub: nx.MultiDiGraph = M.subgraph(keep).copy()
    if not hubs:      # in_stage / belongs_to edges fan out from every file to a few hubs and bury the real structure
        sub.remove_edges_from([(u, v, k) for u, v, k, d in sub.edges(keys=True, data=True)
                               if d.get("relation") in HUB_RELATIONS and sub.nodes[v].get("kind") in STRUCTURE_KINDS])
    if sub.number_of_nodes() > max_nodes:
        top = sorted(sub.nodes, key=lambda n: (n == node, sub.degree(n)), reverse=True)[:max_nodes]
        notes.append(f"showing the {max_nodes} best-connected of {sub.number_of_nodes()} nodes (raise --max-nodes, or filter)")
        sub = sub.subgraph(top).copy()
    sub.remove_nodes_from([n for n in list(sub) if sub.degree(n) == 0 and n != node and sub.number_of_nodes() > 1])
    return sub, "; ".join(notes)


def _label(d: dict, n: str) -> str:
    return str(d.get("label", n))


def _title(n: str, d: dict) -> str:
    keys = ("kind", "layer", "project", "stages", "source_file", "status", "origin")
    return "\n".join([_label(d, n)] + [f"{k}: {d[k]}" for k in keys if d.get(k) not in (None, "", [])])


# ---- cytoscape -------------------------------------------------------------------------------------------------

CYTOSCAPE_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
  html,body{margin:0;height:100%;font:16px system-ui,sans-serif;background:#fff;color:#222}
  #bar{display:flex;gap:12px;align-items:center;padding:8px 12px;min-height:34px;border-bottom:1px solid #ddd;flex-wrap:wrap}
  #cy{position:absolute;top:52px;bottom:0;left:0;right:340px}
  #side{position:absolute;top:52px;bottom:0;right:0;width:340px;font-size:15px;overflow:auto;border-left:1px solid #ddd;padding:8px;box-sizing:border-box}
  .sw{display:inline-block;width:12px;height:12px;border-radius:50%;margin:0 3px 0 8px}
  button{font-size:15px;min-width:34px;padding:2px 8px;cursor:pointer}
  pre{white-space:pre-wrap;word-break:break-word;margin:4px 0}
  @media (prefers-color-scheme: dark){html,body{background:#16181d;color:#e6e6e6}#bar,#side{border-color:#333}input,select{font-size:15px;background:#222;color:#eee;border:1px solid #444}}
</style></head><body>
<div id="bar"><b>__TITLE__</b><input id="q" placeholder="find a node" size="18" style="font-size:15px">
 <select id="layout" style="font-size:15px"><option>cose</option><option>breadthfirst</option><option>concentric</option><option>circle</option><option>grid</option></select>
 <button id="zin" title="Zoom in">+</button><button id="zout" title="Zoom out">&minus;</button><button id="zfit" title="Fit the whole graph">Fit</button>
 <span>__LEGEND__</span><span style="opacity:.7">__NOTE__</span></div>
<div id="cy"></div><div id="side"><i>Click a node.</i></div>
<script>__CYTOSCAPE__</script>
<script>
const data = __DATA__;
const dark = matchMedia('(prefers-color-scheme: dark)').matches;
const cy = cytoscape({container: document.getElementById('cy'), elements: data, wheelSensitivity: 0.08, minZoom: 0.05, maxZoom: 4,
  style: [
    {selector:'node', style:{'background-color':'data(color)', label:'data(label)', 'font-size':13, 'min-zoomed-font-size':8,
      width:'data(size)', height:'data(size)', color: dark?'#ddd':'#333', 'text-valign':'bottom', 'text-margin-y':3}},
    {selector:'node[shape="diamond"]', style:{shape:'diamond'}},
    {selector:'edge', style:{width:1, 'line-color':'#aab', 'target-arrow-color':'#aab', 'target-arrow-shape':'triangle',
      'curve-style':'bezier', 'arrow-scale':0.8}},
    {selector:'edge[confidence != "EXTRACTED"]', style:{'line-style':'dashed'}},
    {selector:'.faded', style:{opacity:0.12}},
    {selector:'node:selected', style:{'border-width':3, 'border-color':'#e5484d'}}]});
function layout(){
  const l = cy.layout({name: document.getElementById('layout').value, animate:false, padding:30, nodeRepulsion: 9000, fit:true});
  l.on('layoutstop', () => cy.fit(undefined, 30));
  l.run(); cy.fit(undefined, 30);
}
layout();
window.addEventListener('resize', () => cy.fit(undefined, 30));
document.getElementById('layout').onchange = layout;
function zoomBy(f){ cy.zoom({level: cy.zoom() * f, renderedPosition: {x: cy.width() / 2, y: cy.height() / 2}}); }
document.getElementById('zin').onclick = () => zoomBy(1.3);
document.getElementById('zout').onclick = () => zoomBy(1 / 1.3);
document.getElementById('zfit').onclick = () => cy.fit(undefined, 30);
cy.on('tap','node', e => {
  const n = e.target, nb = n.closedNeighborhood();
  cy.elements().addClass('faded'); nb.removeClass('faded');
  const d = n.data(), esc = s => String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
  const rows = n.connectedEdges().map(x => (x.source().id()===n.id() ? '→ ' : '← ') + esc(x.data('relation')) + ' ' +
    esc((x.source().id()===n.id() ? x.target() : x.source()).data('label'))).sort();
  document.getElementById('side').innerHTML = '<b>' + esc(d.label) + '</b><pre>' + esc(d.info) + '</pre><b>Links</b><pre>' + rows.join('\\n') + '</pre>';
});
cy.on('tap', e => { if (e.target === cy) { cy.elements().removeClass('faded'); } });
document.getElementById('q').oninput = e => {
  const t = e.target.value.toLowerCase(); cy.elements().removeClass('faded');
  if (!t) return;
  const hit = cy.nodes().filter(n => n.data('label').toLowerCase().includes(t));
  cy.elements().not(hit).addClass('faded'); if (hit.length) cy.fit(hit, 60);
};
</script></body></html>
"""


def _legend(sub: nx.MultiDiGraph) -> str:
    layers = sorted({d.get("layer", "?") for _, d in sub.nodes(data=True)})
    return "".join(f'<span class="sw" style="background:{LAYER_COLORS.get(l, OTHER_COLOR)}"></span>{html.escape(l)}' for l in layers)


def _size(sub: nx.MultiDiGraph, n: str) -> int:
    return min(14 + 3 * int(dict(sub.degree)[n] ** 0.5 * 2), 46)


def cytoscape_html(sub: nx.MultiDiGraph, note: str = "") -> str:
    elements = [{"data": {"id": n, "label": _label(d, n), "color": color(d), "size": _size(sub, n), "info": _title(n, d),
                          "shape": "diamond" if d.get("kind") in STRUCTURE_KINDS else "ellipse"}}
                for n, d in sub.nodes(data=True)]
    elements += [{"data": {"id": f"e{i}", "source": u, "target": v, "relation": d.get("relation", ""),
                           "confidence": d.get("confidence", "EXTRACTED")}}
                 for i, (u, v, d) in enumerate(sub.edges(data=True))]
    js = (resources.files("workgraph") / "data" / "viz" / "cytoscape.min.js").read_text(encoding="utf-8")
    page = (CYTOSCAPE_PAGE.replace("__TITLE__", "workgraph").replace("__NOTE__", html.escape(note))
            .replace("__LEGEND__", _legend(sub))
            .replace("__DATA__", json.dumps(elements).replace("</", "<\\/"))
            .replace("__CYTOSCAPE__", js.replace("</script", "<\\/script")))
    return page


def to_cytoscape(sub: nx.MultiDiGraph, out: Path, note: str = "") -> None:
    out.write_text(cytoscape_html(sub, note), encoding="utf-8")


# ---- pyvis -----------------------------------------------------------------------------------------------------

def pyvis_ready() -> tuple[bool, str]:
    try:
        import pyvis  # noqa: F401
        return True, "pyvis installed"
    except ImportError:
        return False, "needs pyvis: python -m pip install 'workgraph[viz]'"


def to_pyvis(sub: nx.MultiDiGraph, out: Path, note: str = "") -> None:
    from pyvis.network import Network
    net = Network(height="100vh", width="100%", directed=True, notebook=False, cdn_resources="in_line")
    for n, d in sub.nodes(data=True):
        net.add_node(n, label=_label(d, n), title=_title(n, d), color=color(d), size=_size(sub, n) / 2,
                     shape="diamond" if d.get("kind") in STRUCTURE_KINDS else "dot")
    for u, v, d in sub.edges(data=True):
        net.add_edge(u, v, title=d.get("relation", ""), dashes=d.get("confidence", "EXTRACTED") != "EXTRACTED",
                     color="#aab", arrows="to")
    net.barnes_hut(spring_length=140)
    net.write_html(str(out), notebook=False)


# ---- graphify --------------------------------------------------------------------------------------------------

def to_graphify(sub: nx.MultiDiGraph, out: Path, note: str = "") -> None:
    from graphify.cluster import cluster
    from graphify.export import to_html
    G = nx.Graph(sub)                       # graphify's exporter wants an undirected graph and communities
    communities = cluster(G) if G.number_of_nodes() else {}
    if not to_html(G, communities, str(out)):
        raise RuntimeError("graphify declined to export this graph (too large? narrow it with --layer/--project/--node)")


# ---- graphviz --------------------------------------------------------------------------------------------------

def graphviz_ready() -> tuple[bool, str]:
    return (True, "dot found") if shutil.which("dot") else (False, "dot not found (install graphviz); DOT text is written instead")


def to_dot_full(sub: nx.MultiDiGraph) -> str:
    esc = lambda s: str(s).replace("\\", "/").replace('"', "'")[:48]  # noqa: E731
    lines = ["digraph G {", "rankdir=LR;", 'node [shape=box, style="rounded,filled", fontsize=11, fontcolor="#222"];']
    for n, d in sub.nodes(data=True):
        lines.append(f'"{esc(n)}" [label="{esc(_label(d, n))}", fillcolor="{color(d)}40", color="{color(d)}"];')
    for u, v, d in sub.edges(data=True):
        style = "" if d.get("confidence", "EXTRACTED") == "EXTRACTED" else ", style=dashed"
        lines.append(f'"{esc(u)}" -> "{esc(v)}" [label="{esc(d.get("relation", ""))}", fontsize=9{style}];')
    return "\n".join(lines + ["}"])


def to_graphviz(sub: nx.MultiDiGraph, out: Path, note: str = "") -> None:
    dot = to_dot_full(sub)
    if shutil.which("dot"):
        r = subprocess.run(["dot", "-Tsvg", "-o", str(out)], input=dot, text=True, capture_output=True)
        if r.returncode:
            raise RuntimeError(f"dot failed: {r.stderr.strip()[:200]}")
    else:
        out.with_suffix(".dot").write_text(dot, encoding="utf-8")
        raise RuntimeError(f"dot is not installed; wrote DOT text to {out.with_suffix('.dot')}")


# ---- registry --------------------------------------------------------------------------------------------------

ENGINES: dict[str, dict] = {
    "cytoscape": {"fn": to_cytoscape, "ext": ".html", "ready": lambda: (True, "bundled, works offline"),
                  "about": "interactive HTML, no extra install, scales best"},
    "pyvis": {"fn": to_pyvis, "ext": ".html", "ready": pyvis_ready, "about": "interactive HTML via pyvis (physics layout)"},
    "graphify": {"fn": to_graphify, "ext": ".html", "ready": lambda: (True, "ships with graphify"),
                 "about": "graphify's HTML export, coloured by community"},
    "graphviz": {"fn": to_graphviz, "ext": ".svg", "ready": graphviz_ready, "about": "static SVG through `dot`; small graphs"},
}
DEFAULT_ENGINE = "cytoscape"


def render(M: nx.MultiDiGraph, engine: str = DEFAULT_ENGINE, out: str | Path | None = None, **select_args) -> tuple[Path, str, int]:
    """Select a subgraph and write it with `engine`. Returns (file, note, node count)."""
    if engine not in ENGINES:
        raise ValueError(f"unknown engine {engine!r}; choose from {sorted(ENGINES)}")
    sub, note = select(M, **select_args)
    if not sub.number_of_nodes():
        raise ValueError("nothing to draw: the filters match no nodes")
    spec = ENGINES[engine]
    ok, why = spec["ready"]()
    if not ok and engine == "pyvis":
        raise RuntimeError(why)
    target = Path(out) if out else paths.out_dir() / f"graph-{engine}{spec['ext']}"
    target.parent.mkdir(parents=True, exist_ok=True)
    spec["fn"](sub, target, note)
    return target, note, sub.number_of_nodes()


def open_in_browser(path: Path) -> bool:
    import webbrowser
    return webbrowser.open(path.resolve().as_uri())
