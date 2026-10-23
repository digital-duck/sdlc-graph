"""Streamlit app: review and query the knowledge graphs. Start it with `workgraph ui`."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd
import streamlit as st

from workgraph import layers, paths, query, viz
from workgraph.domain import Workspace

st.set_page_config(page_title="Workgraph", page_icon="🕸️", layout="wide")


def _wide() -> dict:
    """Full-width keyword for this Streamlit: `width="stretch"` on new versions, `use_container_width` on old ones."""
    ver = tuple(int(x) for x in st.__version__.split(".")[:2] if x.isdigit())
    return {"width": "stretch"} if ver >= (1, 50) else {"use_container_width": True}


WIDE = _wide()


def _cli_graph() -> str:
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", default="")
    return ap.parse_known_args(sys.argv[1:])[0].graph


@st.cache_resource(show_spinner="Loading graph…")
def load(path: str, mtime: float):
    M = query.load_graph(path)
    roots = {}
    ws = paths.default_workspace()
    if ws.exists():
        try:
            roots = query.roots_from_workspace(Workspace.load(ws))
        except Exception:
            pass
    return M, query.build_docs(M, roots), query.gaps_for(M)


def table(rows: list[dict], key: str, ids: list[str]) -> str | None:
    """Show rows; return the id of the selected row (single selection)."""
    if not rows:
        st.info("Nothing to show.")
        return
    ev = st.dataframe(pd.DataFrame(rows), **WIDE, hide_index=True, on_select="rerun",
                      selection_mode="single-row", key=key)
    sel = ev.selection.rows if ev and ev.selection else []
    return ids[sel[0]] if sel else None


def node_panel(M, node: str) -> None:
    d = M.nodes[node]
    st.subheader(d.get("label", node))
    st.caption(node)
    left, right = st.columns([1, 1])
    with left:
        attrs = {k: v for k, v in d.items() if k not in ("label",) and v not in (None, "", [])}
        st.json(attrs, expanded=True)
        nb = query.neighbors(M, node)
        for title, key in (("Links out", "out"), ("Links in", "in")):
            rows = [{"relation": r, "node": label, "id": i} for r, i, label in nb[key]
                    if M.nodes[i].get("kind") not in query.HIDDEN_KINDS]
            st.markdown(f"**{title}** ({len(rows)})")
            if rows:
                st.dataframe(pd.DataFrame(rows), **WIDE, hide_index=True)
    with right:
        st.markdown("**Neighbourhood**")
        st.graphviz_chart(query.to_dot(M, node), **WIDE)


def map_tab(M, layer, project, kind, stage, chosen=None) -> None:
    """The graph as a picture: the sidebar filters plus a few controls, drawn with the bundled Cytoscape.js page."""
    c = st.columns([2, 1, 1, 1, 1])
    centre = chosen if chosen in M else st.session_state.get("node")     # this run's pick, not last run's
    around = c[0].checkbox(f"Only around the selected node ({M.nodes[centre].get('label', centre)})" if centre in M
                           else "Only around the selected node (select one in Search, Browse or Gaps first)",
                           disabled=centre not in M, key="map_around")
    hops = c[1].number_input("Hops", 1, 5, 2, disabled=not around)
    cap = c[2].number_input("Max nodes", 20, 2000, viz.DEFAULT_MAX_NODES, step=50)
    code = c[3].checkbox("Symbols and code", help="Include symbol, external and rationale nodes (many)")
    hubs = c[4].checkbox("Stage/project links", help="Keep the in_stage and belongs_to edges to the hub nodes")
    sub, note = viz.select(M, layer=layer, project=project, kind=kind, stage=stage, node=centre if around else None,
                           hops=int(hops), include_code=code, hubs=hubs, max_nodes=int(cap))
    if not sub.number_of_nodes():
        st.info("Nothing to draw with these filters.")
        return
    st.caption(f"{sub.number_of_nodes()} nodes, {sub.number_of_edges()} edges. " + note
               + " Other engines: `workgraph viz --engine pyvis|graphify|graphviz`.")
    page = viz.cytoscape_html(sub, note)
    if hasattr(st, "iframe"):                    # newer Streamlit: components.html is deprecated
        st.iframe(page, height=700)
    else:
        import streamlit.components.v1 as components
        components.html(page, height=700, scrolling=False)


@st.cache_resource(show_spinner="Loading layer…")
def load_layer(out: str, name: str, mtime: float):
    return layers.load_layer(name, out)


def canvas(sub, note: str, *, mark: set[str], title: str, height: int) -> None:
    page = viz.cytoscape_html(sub, note, mark=mark, title=title)
    if hasattr(st, "iframe"):
        st.iframe(page, height=height)
    else:
        import streamlit.components.v1 as components
        components.html(page, height=height, scrolling=False)


def layers_tab(M, gp: Path) -> None:
    """Two canvases. The parent layer is drawn on top; pick a node in it and the child graph below shows what the
    finer layer holds under that node (a product's repositories with their files and relations). Only the two
    partitions involved are loaded. Its pick is its own: it does not drive the node panel below the tabs."""
    out = gp.parent / "layers"
    if not layers.available(out):
        st.info(f"No layer partitions at {out}. `workgraph build` writes them; or create them from this graph now.")
        if st.button("Create layer partitions from this graph"):
            layers.write_partitions(M, out)
            st.rerun()
        return
    G, meta = layers.layer_graph(), layers.load_meta(out)
    counts = {x["id"]: x["nodes"] for x in meta["layers"]}
    with st.expander("Layer graph: coarse at the top; an arrow points from a layer to the coarser layer it refers to"):
        st.graphviz_chart(layers.to_dot(G, meta), **WIDE)
        if meta["undeclared"]:
            st.caption("Cross-layer edges outside the declared dependencies: "
                       + ", ".join(f"{x['source']} -{x['relation']}-> {x['target']} ({x['edges']})" for x in meta["undeclared"]))

    parents = [n for n in layers.by_coarseness(G) if layers.finer(G, n) and counts.get(n)]
    if not parents:
        st.info("No layer has a finer layer with nodes to show.")
        return
    start = min(parents, key=lambda n: (G.nodes[n]["rank"] or 99, n))      # the layer just above the finest
    c1, c2, c3 = st.columns([1, 1, 2])
    parent = c1.selectbox("Parent layer", parents, index=parents.index(start), key="layers_parent",
                          format_func=lambda n: f"{n} ({counts[n]} nodes)")
    kids = [n for n in layers.finer(G, parent) if counts.get(n)]
    if not kids:
        st.info(f"`{parent}` has no finer layer with nodes.")
        return
    child = c2.selectbox("Child layer", kids, key="layers_child", format_func=lambda n: f"{n} ({counts[n]} nodes)")
    P = load_layer(str(out), parent, (out / f"{parent}.json").stat().st_mtime)
    ids = sorted(P.nodes, key=lambda n: (P.nodes[n].get("kind", ""), P.nodes[n].get("label", n)))
    node = c3.selectbox("Node in the parent layer", ids, key=f"layers_node_{parent}",
                        format_func=lambda n: f"{P.nodes[n].get('label', n)}  [{P.nodes[n].get('kind', '')}]")

    st.markdown(f"**Parent: `{parent}`** ({P.number_of_nodes()} nodes). The ringed node is the one selected below; "
                "click nodes in the picture to inspect them.")
    psub = P if P.number_of_nodes() <= viz.DEFAULT_MAX_NODES else P.subgraph(
        sorted(P.nodes, key=lambda n: (n == node, P.degree(n)), reverse=True)[:viz.DEFAULT_MAX_NODES]).copy()
    canvas(psub, f"layer {parent}", mark={node}, title=f"parent layer: {parent}", height=420)

    sub = layers.below(node, child, out)
    code = st.checkbox("Symbols and code", key="layers_code", help="Include symbol, external and rationale nodes (many)")
    shown, note = viz.select(sub, include_code=code, hubs=False)
    seeds = {n for n, d in sub.nodes(data=True) if d.get("seed")}
    st.markdown(f"**Child: `{child}` under {P.nodes[node].get('label', node)}** "
                f"({sub.number_of_nodes()} nodes, {sub.number_of_edges()} edges; loaded `{parent}` and `{child}` only).")
    if not shown.number_of_nodes():
        st.info(f"Nothing in `{child}` is linked to this node or to anything inside it.")
    else:
        canvas(shown, note, mark=seeds, title=f"child layer: {child}", height=520)
    with st.expander("Neighbours by coarseness"):
        ctx = layers.context(node, out)
        for key, title in (("coarser", "Coarser"), ("finer", "Finer"), ("same", "Same layer"), ("beside", "Beside")):
            if ctx[key]:
                st.markdown(f"{title} ({len(ctx[key])})")
                st.dataframe(pd.DataFrame([{"relation": ("→ " if x["direction"] == "out" else "← ") + x["relation"],
                                            "layer": x["layer"], "kind": x["kind"], "node": x["label"], "id": x["id"]}
                                           for x in ctx[key]]), **WIDE, hide_index=True)


# Streamlit sizes most text in rem, so raising the root size scales the whole app (default is 16px).
FONT_CSS = "<style>html { font-size: 19px; } [data-testid='stDataFrame'] { font-size: 1rem; }</style>"


def bar(series: pd.Series, height: int = 300) -> None:
    """Bar chart with fixed-width bars (st.bar_chart stretches a few bars across the whole width)."""
    import altair as alt
    df = series.rename_axis("name").reset_index(name="count")
    chart = (alt.Chart(df).mark_bar(size=44, color="#4f8ef7")
             .encode(x=alt.X("name:N", sort="-y", title=None, axis=alt.Axis(labelAngle=0, labelLimit=160)),
                     y=alt.Y("count:Q", title=None), tooltip=["name", "count"])
             .properties(height=height))
    st.altair_chart(chart, **WIDE)


def main() -> None:
    st.markdown(FONT_CSS, unsafe_allow_html=True)
    st.title("Workgraph")
    graph = st.sidebar.text_input("Graph file", _cli_graph() or str(paths.out_dir() / "graph.json"))
    if st.sidebar.button("Reload"):
        st.cache_resource.clear()
    gp = Path(graph).expanduser()
    if not gp.exists():
        st.error(f"No graph at {gp}. Run `workgraph build` first, or point the sidebar at a graph.json.")
        return
    M, docs, gaps = load(str(gp), gp.stat().st_mtime)
    st.sidebar.caption(f"built {time.strftime('%Y-%m-%d %H:%M', time.localtime(gp.stat().st_mtime))}")

    st.sidebar.markdown("### Filters")
    pick = lambda label, vals: (lambda v: None if v == "all" else v)(  # noqa: E731
        st.sidebar.selectbox(label, ["all", *sorted({x for x in vals if x})]))
    layer = pick("Layer", [d.layer for d in docs])
    project = pick("Project", [d.project for d in docs])
    kind = pick("Kind", [d.kind for d in docs])
    stage = pick("Stage", [s for d in docs for s in d.stages])
    fdocs = query.filter_docs(docs, layer=layer, project=project, kind=kind, stage=stage)
    st.sidebar.caption(f"{len(fdocs)} of {len(docs)} nodes match")

    t_over, t_search, t_browse, t_gaps, t_map, t_layers = st.tabs(["Overview", "Search", "Browse", "Gaps", "Map", "Layers"])
    chosen = None

    with t_over:
        s = query.summary(M)
        c = st.columns(4)
        c[0].metric("Nodes", s["nodes"])
        c[1].metric("Edges", s["edges"])
        c[2].metric("Projects", len(s["projects"]))
        c[3].metric("Gaps", len(gaps))
        a, b = st.columns(2)
        with a:
            st.markdown("**Nodes per layer**")
            bar(pd.Series(s["layers"], name="nodes"))
        with b:
            st.markdown("**Artifacts per project**")
            bar(pd.Series(s["projects"], name="artifacts"))
        st.markdown("**Artifacts by kind**")
        st.dataframe(pd.DataFrame(sorted(s["kinds"].items(), key=lambda x: -x[1]), columns=["kind", "count"]),
                     **WIDE, hide_index=True)

    with t_search:
        c1, c2, c3 = st.columns([4, 1, 1])
        q = c1.text_input("Search", placeholder='e.g. checkout test   |   "discount codes"   |   how are carts stored?')
        mode = c2.radio("Mode", ["Keyword", "Semantic"], horizontal=True)
        backend = c3.selectbox("Semantic backend", ["auto", "ollama", "sbert", "tfidf"], disabled=mode == "Keyword")
        if mode == "Semantic":
            av = query.available_backends()
            st.caption("backends: " + "; ".join(f"{k} {'ready' if ok else 'unavailable'} ({d})" for k, (ok, d) in av.items()))
        if q.strip():
            try:
                if mode == "Keyword":
                    hits = query.keyword_search(fdocs, q, 50)
                else:
                    with st.spinner("Embedding (the first run indexes every node)…"):
                        hits, used, note = query.semantic_search(fdocs, q, 50, backend)
                    st.caption(note)
            except Exception as e:
                st.error(str(e))
                hits = []
            rows = [{"score": round(sc, 3), "layer": d.layer, "kind": d.kind, "label": d.label,
                     "project": d.project, "file": d.source_file} for sc, d in hits]
            st.caption(f"{len(hits)} results")
            chosen = table(rows, "search_tbl", [d.id for _, d in hits]) or chosen

    with t_browse:
        needle = st.text_input("Filter by name", key="browse_filter")
        shown = [d for d in fdocs if needle.casefold() in d.label.casefold()] if needle else fdocs
        rows = [{"layer": d.layer, "kind": d.kind, "label": d.label, "project": d.project,
                 "stages": ", ".join(d.stages), "file": d.source_file} for d in shown[:2000]]
        st.caption(f"{len(shown)} nodes" + (" (first 2000 shown)" if len(shown) > 2000 else ""))
        chosen = table(rows, "browse_tbl", [d.id for d in shown[:2000]]) or chosen

    with t_gaps:
        shown_gaps = [g for g in gaps if (not project or g["project"] == project)]
        msgs = sorted({g["message"] for g in shown_gaps})
        m = st.selectbox("Rule", ["all", *msgs])
        shown_gaps = [g for g in shown_gaps if m == "all" or g["message"] == m]
        st.caption(f"{len(shown_gaps)} gaps (gaps ignore the layer, kind and stage filters)")
        rows = [{"rule": g["message"], "artifact": g["label"], "project": g["project"], "file": g["source_file"]}
                for g in shown_gaps]
        chosen = table(rows, "gaps_tbl", [g["node"] for g in shown_gaps]) or chosen

    with t_map:
        map_tab(M, layer, project, kind, stage, chosen)

    with t_layers:
        layers_tab(M, gp)

    if chosen and chosen in M:
        st.session_state["node"] = chosen
    node = st.session_state.get("node")
    if node and node in M:
        st.divider()
        node_panel(M, node)


main()
