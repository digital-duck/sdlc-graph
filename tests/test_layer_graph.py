import json
from pathlib import Path

import networkx as nx
import pytest

from workgraph import layers
from workgraph.cli import main
from workgraph.query import load_graph

EX = Path(__file__).parent.parent / "examples"


@pytest.fixture()
def built(tmp_path):
    out = tmp_path / "out"
    main(["build", "-c", str(EX / "workspace.yaml"), "--out", str(out), "--quiet"])
    return out


def test_layer_graph_is_derived_from_the_vocabulary():
    G = layers.layer_graph()
    assert set(G) == {"repo", "product", "org", "process"}
    assert {(u, v, r) for u, v, d in G.edges(data=True) for r in d["relations"]} == {
        ("repo", "product", "belongs_to"), ("product", "org", "owned_by"), ("product", "process", "follows"),
        ("repo", "process", "in_stage"), ("repo", "process", "participates_in")}
    rank = {n: d["rank"] for n, d in G.nodes(data=True)}
    assert rank == {"repo": 0, "product": 1, "org": 2, "process": 2}   # relative: org and process are siblings
    assert layers.coarser(G, "repo") == ["product", "process"] and layers.finer(G, "product") == ["repo"]
    assert layers.by_coarseness(G)[-1] == "repo"
    assert G.nodes["repo"]["hierarchical"] is False and G.nodes["org"]["hierarchical"] is True


def test_a_new_layer_shifts_the_ranks(monkeypatch):
    from workgraph import schema
    monkeypatch.setitem(schema.LAYER_KINDS, "market", {"segment"})
    rules = {**schema.RELATION_RULES, "targets": ({"org"}, {"market"})}
    monkeypatch.setattr(layers, "RELATION_RULES", rules)
    G = layers.layer_graph()
    assert G.nodes["market"]["rank"] == 3 and G.nodes["org"]["rank"] == 2


def test_a_dependency_cycle_is_rejected(monkeypatch):
    rules = {**layers.RELATION_RULES, "back": ({"org"}, {"repo"})}
    monkeypatch.setattr(layers, "RELATION_RULES", rules)
    with pytest.raises(ValueError, match="cycle"):
        layers.layer_graph()


def test_build_writes_partitions_that_add_up_to_graph_json(built):
    M = load_graph(built / "graph.json")
    d = built / "layers"
    meta = layers.load_meta(d)
    assert meta["format"] == layers.FORMAT
    assert sum(x["nodes"] for x in meta["layers"]) == M.number_of_nodes()
    seen: dict[str, str] = {}
    for x in meta["layers"]:
        for n in layers.load_layer(x["id"], d).nodes:
            assert n not in seen, f"{n} in {seen[n]} and {x['id']}"       # each node in exactly one partition
            seen[n] = x["id"]
    assert set(seen) == set(M.nodes)
    cross = layers.load_cross(d)
    inner = sum(x["edges"] for x in meta["layers"])
    assert inner + len(cross) == M.number_of_edges()
    assert all(layers.node_layer(M, e["source"]) != layers.node_layer(M, e["target"]) for e in cross)


def test_load_scope_is_the_induced_subgraph(built):
    M = load_graph(built / "graph.json")
    d = built / "layers"
    S = layers.load_scope(["repo", "product"], d)
    keep = [n for n in M if layers.node_layer(M, n) in ("repo", "product")]
    want = M.subgraph(keep)
    assert set(S.nodes) == set(want.nodes)
    assert S.number_of_edges() == want.number_of_edges()
    assert nx.is_isomorphic(nx.DiGraph(S), nx.DiGraph(want))


def test_rebuild_replaces_stale_partitions(built):
    d = built / "layers"
    (d / "ghost.json").write_text("{}", encoding="utf-8")
    layers.write_partitions(load_graph(built / "graph.json"), d)
    assert not (d / "ghost.json").exists()


def test_context_loads_only_the_layers_it_needs(built, monkeypatch):
    d = built / "layers"
    loaded: list[str] = []
    real = layers.load_layer
    monkeypatch.setattr(layers, "load_layer", lambda name, out=None: (loaded.append(name), real(name, out))[1])
    linked = {e["target"] for e in layers.load_cross(d) if e["relation"] == "belongs_to"}
    prod = next(iter(linked))
    loaded.clear()
    ctx = layers.context(prod, d)
    assert ctx["layer"] == "product" and ctx["finer"]
    assert set(loaded) == set(ctx["loaded"]) == {"product", "repo"}      # repo only because a project points here
    # a node with no cross-layer neighbours in repo must not pull the repo partition in
    org = next(n for n in layers.load_layer("org", d).nodes if not any(
        layers.layer_of(e["source"]) == "repo" or layers.layer_of(e["target"]) == "repo"
        for e in layers.load_cross(d) if n in (e["source"], e["target"])))
    loaded.clear()
    layers.context(org, d)
    assert "repo" not in loaded
    with pytest.raises(KeyError):
        layers.context("org:nope", d)


def test_context_groups_neighbours_by_relative_coarseness(built):
    d = built / "layers"
    M = load_graph(built / "graph.json")
    proj = next(n for n, a in M.nodes(data=True) if a.get("kind") == "project"
                and any(e["relation"] == "belongs_to" and e["source"] == n for e in layers.load_cross(d)))
    ctx = layers.context(proj, d)
    assert any(x["layer"] == "product" and x["relation"] == "belongs_to" for x in ctx["coarser"])
    prod = next(x["id"] for x in ctx["coarser"])
    assert any(x["id"] == proj for x in layers.context(prod, d)["finer"])      # the same link seen from above


def test_dot_shows_counts_and_ranks(built):
    G = layers.layer_graph()
    dot = layers.to_dot(G, layers.load_meta(built / "layers"))
    assert "rankdir=BT" in dot and "belongs_to" in dot and "nodes," in dot
    assert 'label="in_stage (' in dot and "\\nparticipates_in (" in dot      # two relations on one dependency
    assert "rank=same" in layers.to_dot(G)


def test_layers_command_prints_the_graph(built, capsys):
    main(["utils", "layers", "-o", str(built / "layers")])
    out = capsys.readouterr().out
    assert "coarsest first" in out and "repo -belongs_to-> product" in out and " nodes," in out
    assert "outside the declared" not in out                                  # every cross-layer edge is now declared
    json.loads((built / "layers" / "layers.json").read_text())


def test_hierarchy_lists_parents_before_children(built):
    G = layers.load_layer("org", built / "layers")
    rows = layers.hierarchy(G)
    assert len(rows) == G.number_of_nodes()
    depth = dict((n, d) for d, n in rows)
    for u, v, data in G.edges(data=True):
        if data["relation"] == "part_of":
            assert depth[u] == depth[v] + 1
    assert [n for d, n in rows if d == 0] == sorted(n for d, n in rows if d == 0)
    assert len(layers.hierarchy(G, limit=2)) == 2


def test_below_gives_a_product_its_repositories_files_and_relations(built):
    d = built / "layers"
    prod = "product:acme/retail/shop/checkout"
    sub = layers.below(prod, "repo", d)
    assert sub.nodes["project:acme-shop"].get("seed")                       # linked by belongs_to
    projects = {a.get("project") for _, a in sub.nodes(data=True)}
    assert projects == {"acme-shop"}                                        # nothing from rq-demo
    kinds = {a.get("kind") for _, a in sub.nodes(data=True)}
    assert {"project", "file"} <= kinds and sub.number_of_edges() > 0
    assert all(layers.node_layer(sub, n) == "repo" for n in sub)


def test_below_includes_what_is_inside_the_parent_node(built):
    d = built / "layers"
    parent = "product:acme/retail/shop"                                    # checkout is part_of shop
    inner = layers.below("product:acme/retail/shop/checkout", "repo", d)
    outer = layers.below(parent, "repo", d)
    assert set(inner.nodes) <= set(outer.nodes) and "project:acme-shop" in outer


def test_below_org_gives_products_and_loads_two_partitions(built, monkeypatch):
    d = built / "layers"
    loaded: list[str] = []
    real = layers.load_layer
    monkeypatch.setattr(layers, "load_layer", lambda name, out=None: (loaded.append(name), real(name, out))[1])
    sub = layers.below("org:acme/engineering/commerce/payments", "product", d)
    assert sub.nodes["product:acme/retail/shop"].get("seed")
    assert sorted(set(loaded)) == ["org", "product"]
    assert layers.below("org:acme/engineering/commerce/payments", "repo", d).number_of_nodes() == 0   # not adjacent
    with pytest.raises(KeyError):
        layers.below("org:nope", "product", d)
