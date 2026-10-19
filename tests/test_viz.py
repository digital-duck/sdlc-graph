import json
import re

import networkx as nx
import pytest

from workgraph import viz
from workgraph.cli import main


def graph() -> nx.MultiDiGraph:
    M = nx.MultiDiGraph()
    M.add_node("project:p", label="p", kind="project", layer="repo")
    M.add_node("repo:a.py", label="a.py", kind="file", layer="repo", project="p")
    M.add_node("repo:b.py", label="b.py", kind="file", layer="repo", project="p")
    M.add_node("repo:c.py", label="c.py", kind="file", layer="repo", project="q", stages=["design"])
    M.add_node("sym", label="sym", kind="symbol", layer="repo", project="p")
    M.add_node("product:x", label="X", kind="product", layer="product")
    M.add_edge("repo:a.py", "repo:b.py", relation="imports", confidence="EXTRACTED")
    M.add_edge("repo:b.py", "repo:c.py", relation="imports", confidence="INFERRED")
    M.add_edge("repo:a.py", "project:p", relation="belongs_to", confidence="EXTRACTED")
    M.add_edge("repo:a.py", "sym", relation="contains", confidence="EXTRACTED")
    M.add_edge("product:x", "repo:a.py", relation="part_of", confidence="EXTRACTED")
    return M


def test_select_hides_code_and_hub_edges_by_default():
    sub, _ = viz.select(graph())
    assert "sym" not in sub and "project:p" not in sub          # hidden kind; hub with only a hub edge
    assert {"repo:a.py", "repo:b.py", "product:x"} <= set(sub)
    sub, _ = viz.select(graph(), include_code=True, hubs=True)
    assert "sym" in sub and "project:p" in sub


def test_select_filters_and_neighbourhood():
    M = graph()
    assert set(viz.select(M, layer="product")[0]) == {"product:x"}
    assert set(viz.select(M, project="q")[0]) == {"repo:c.py"}
    sub, note = viz.select(M, node="a.py", hops=1)
    assert set(sub) == {"repo:a.py", "repo:b.py", "product:x"} and "1 hop" in note
    with pytest.raises(ValueError):
        viz.select(M, node="nope")


def test_select_caps_nodes():
    sub, note = viz.select(graph(), max_nodes=2)
    assert sub.number_of_nodes() <= 2 and "best-connected" in note


def test_cytoscape_page_is_self_contained(tmp_path):
    out, note, n = viz.render(graph(), "cytoscape", tmp_path / "g.html")
    page = out.read_text(encoding="utf-8")
    assert n == 4 and "cytoscape(" in page and 'src="http' not in page      # no CDN: the library is inlined
    data = json.loads(re.search(r"const data = (\[.*?\]);\nconst dark", page, re.S).group(1).replace("<\\/", "</"))
    assert {e["data"]["id"] for e in data if "source" not in e["data"]} == {"repo:a.py", "repo:b.py", "repo:c.py", "product:x"}
    assert any(e["data"].get("confidence") == "INFERRED" for e in data)


def test_graphviz_writes_svg_or_dot(tmp_path):
    from shutil import which
    if which("dot"):
        out, _, _ = viz.render(graph(), "graphviz", tmp_path / "g.svg")
        assert out.read_text(encoding="utf-8").lstrip().startswith(("<?xml", "<svg"))
    else:
        with pytest.raises(RuntimeError):
            viz.render(graph(), "graphviz", tmp_path / "g.svg")
        assert (tmp_path / "g.dot").exists()


def test_graphify_and_pyvis_engines(tmp_path):
    out, _, _ = viz.render(graph(), "graphify", tmp_path / "g.html")
    assert out.stat().st_size > 1000
    if viz.pyvis_ready()[0]:
        out, _, _ = viz.render(graph(), "pyvis", tmp_path / "p.html")
        assert "<html" in out.read_text(encoding="utf-8")
    else:
        with pytest.raises(RuntimeError, match="pyvis"):
            viz.render(graph(), "pyvis", tmp_path / "p.html")


def test_unknown_engine_and_empty_selection(tmp_path):
    with pytest.raises(ValueError, match="unknown engine"):
        viz.render(graph(), "nope")
    with pytest.raises(ValueError, match="nothing to draw"):
        viz.render(graph(), "cytoscape", tmp_path / "x.html", project="zzz")


def test_cli_list_engines_and_viz(tmp_path, capsys):
    main(["viz", "--list-engines"])
    assert "cytoscape" in capsys.readouterr().out
    g = tmp_path / "graph.json"
    g.write_text(json.dumps(nx.node_link_data(graph(), edges="edges")), encoding="utf-8")
    main(["viz", "--graph", str(g), "--no-open", "-o", str(tmp_path / "o.html")])
    assert (tmp_path / "o.html").exists() and "4 nodes" in capsys.readouterr().out
