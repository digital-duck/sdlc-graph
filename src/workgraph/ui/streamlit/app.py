"""Streamlit app: review and query the knowledge graphs. Start it with `workgraph ui`."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd
import streamlit as st

from workgraph import paths, query
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
        return None
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


def main() -> None:
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

    t_over, t_search, t_browse, t_gaps = st.tabs(["Overview", "Search", "Browse", "Gaps"])
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
            st.bar_chart(pd.Series(s["layers"], name="nodes"))
        with b:
            st.markdown("**Artifacts per project**")
            st.bar_chart(pd.Series(s["projects"], name="artifacts"))
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

    if chosen and chosen in M:
        st.session_state["node"] = chosen
    node = st.session_state.get("node")
    if node and node in M:
        st.divider()
        node_panel(M, node)


main()
