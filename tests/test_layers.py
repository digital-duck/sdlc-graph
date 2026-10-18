import json
import shutil
from pathlib import Path

import pytest

from work_graph.convert import json_to_yaml, yaml_to_json
from work_graph.domain import Workspace
from work_graph.pipeline import build_workspace
from work_graph.schema import validate_layer
from work_graph.store import Store

EX = Path(__file__).parent.parent / "examples"


def _doc(layer="org", nodes=None, edges=None):
    return {"schema": "work-graph/1", "layer": layer, "nodes": nodes or [], "edges": edges or []}


def test_schema_accepts_valid_and_rejects_each_rule():
    ok = _doc(nodes=[{"id": "org:a", "kind": "company"}, {"id": "org:a/b", "kind": "team"}],
              edges=[{"source": "org:a/b", "target": "org:a", "relation": "part_of"}])
    assert validate_layer(ok) == []
    bad = {
        "wrong prefix": _doc(nodes=[{"id": "product:a", "kind": "company"}]),
        "bad kind": _doc(nodes=[{"id": "org:a", "kind": "planet"}]),
        "duplicate": _doc(nodes=[{"id": "org:a", "kind": "team"}, {"id": "org:a", "kind": "team"}]),
        "bad uid": _doc(nodes=[{"id": "org:a", "kind": "team", "uid": "xyz"}]),
        "unknown relation": _doc(edges=[{"source": "org:a", "target": "org:b", "relation": "likes"}]),
        "wrong layers": _doc(edges=[{"source": "org:a", "target": "org:b", "relation": "owned_by"}]),
        "cycle": _doc(edges=[{"source": "org:a", "target": "org:b", "relation": "part_of"},
                             {"source": "org:b", "target": "org:a", "relation": "part_of"}]),
        "bad layer": {**_doc(), "layer": "galaxy"},
    }
    for name, doc in bad.items():
        assert validate_layer(doc), name


def test_example_layers_merge_into_one_graph():
    M, _, warnings = build_workspace(Workspace.load(EX / "workspace.yaml"))
    assert {d["layer"] for _, d in M.nodes(data=True)} == {"repo", "process", "product", "org"}
    rels = {(u, d["relation"], v) for u, v, d in M.edges(data=True)}
    assert ("project:acme-shop", "belongs_to", "product:acme/retail/shop/checkout") in rels
    assert ("product:acme/retail/shop", "follows", "process:sdlc") in rels   # pack-derived process node
    assert ("stage:sdlc:design", "part_of", "process:sdlc") in rels
    assert not [w for w in warnings if ".json" in w or ".yaml" in w]


def _copy_graphs(tmp_path):
    ws = tmp_path / "ws"
    shutil.copytree(EX, ws, ignore=shutil.ignore_patterns("store.sqlite", "*.yaml", "graphs"))
    shutil.copytree(EX / "graphs", ws / "graphs", ignore=shutil.ignore_patterns("store.sqlite", "*.yaml"))
    (ws / "workspace.yaml").write_text((EX / "workspace.yaml").read_text())
    return ws


def test_broken_reference_and_rename_hint(tmp_path):
    ws = _copy_graphs(tmp_path)
    org = ws / "graphs" / "org.json"
    doc = json.loads(org.read_text())
    with Store(ws / "graphs" / "store.sqlite") as store:
        store.remember_ids("org", doc["nodes"])               # history before the rename
    old = "org:acme/engineering/commerce/payments"
    for n in doc["nodes"]:
        n["id"] = n["id"].replace(old, "org:acme/engineering/commerce/billing") if n["id"].startswith(old) else n["id"]
    for e in doc["edges"]:
        for k in ("source", "target"):
            e[k] = e[k].replace(old, "org:acme/engineering/commerce/billing")
    org.write_text(json.dumps(doc))
    _, _, warnings = build_workspace(Workspace.load(ws / "workspace.yaml"))
    hit = [w for w in warnings if old in w]
    assert hit and "renamed to org:acme/engineering/commerce/billing" in hit[0]


def test_subgraph_reference_expands_to_descendants(tmp_path):
    ws = _copy_graphs(tmp_path)
    prod = ws / "graphs" / "product.json"
    doc = json.loads(prod.read_text())
    doc["edges"].append({"source": "product:acme/retail/**", "target": "process:sdlc", "relation": "follows"})
    prod.write_text(json.dumps(doc))
    M, _, _ = build_workspace(Workspace.load(ws / "workspace.yaml"))
    followers = {u for u, v, d in M.edges(data=True) if v == "process:sdlc" and d["relation"] == "follows"}
    assert {"product:acme/retail", "product:acme/retail/shop", "product:acme/retail/shop/checkout"} <= followers


def test_json_yaml_round_trip_keeps_content_and_comments(tmp_path):
    src = tmp_path / "product.json"
    src.write_text((EX / "graphs" / "product.json").read_text())
    original = json.loads(src.read_text())
    y = tmp_path / "product.yaml"
    with Store(tmp_path / "store.sqlite") as store:
        json_to_yaml(src, y, store)
        text = y.read_text()
        text = text.replace("  - id: product:acme/retail/shop\n", "  # the flagship product\n  - id: product:acme/retail/shop\n")
        text = text.replace("    kind: application\n", "    kind: application  # deployable\n")
        y.write_text("# hand-written header\n" + text)
        yaml_to_json(y, src, store)
        assert json.loads(src.read_text()) == original          # comments never reach the JSON
        y.unlink()
        info = json_to_yaml(src, y, store)                      # regenerate YAML from JSON alone
        assert info["orphans"] == 0
    out = y.read_text()
    assert "# hand-written header" in out
    assert "# the flagship product\n  - id: product:acme/retail/shop\n" in out
    assert "kind: application  # deployable" in out


def test_comment_added_in_yaml_survives_next_convert_and_orphan_is_kept(tmp_path):
    src, y = tmp_path / "org.json", tmp_path / "org.yaml"
    src.write_text((EX / "graphs" / "org.json").read_text())
    with Store(tmp_path / "store.sqlite") as store:
        json_to_yaml(src, y, store)
        y.write_text(y.read_text().replace("    label: Research\n", "    label: Research  # to be renamed\n"))
        # regenerate without running yaml_to_json first: the edit must be harvested, not overwritten
        json_to_yaml(src, y, store)
        assert "label: Research  # to be renamed" in y.read_text()
        doc = json.loads(src.read_text())
        doc["nodes"] = [n for n in doc["nodes"] if n["id"] != "org:acme/engineering/research"]
        doc["edges"] = [e for e in doc["edges"] if "research" not in e["source"]]
        src.write_text(json.dumps(doc))
        info = json_to_yaml(src, y, store)
    assert info["orphans"] == 1
    assert "# [orphaned from org:acme/engineering/research] to be renamed" in y.read_text()
    with Store(tmp_path / "store.sqlite") as store:               # the orphan is itself preserved on the next pass
        json_to_yaml(src, y, store)
    assert "[orphaned from org:acme/engineering/research] to be renamed" in y.read_text()


def test_invalid_layer_file_is_reported_not_loaded(tmp_path):
    ws = _copy_graphs(tmp_path)
    (ws / "graphs" / "org.json").write_text(json.dumps(_doc(nodes=[{"id": "org:a", "kind": "planet"}])))
    _, _, warnings = build_workspace(Workspace.load(ws / "workspace.yaml"))
    assert any("planet" in w for w in warnings)
    assert any("broken reference org:acme" in w for w in warnings)   # product.json now points at missing org nodes
