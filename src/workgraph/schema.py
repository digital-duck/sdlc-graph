"""Layer file schema: vocabulary and validation for the product, org and process layers (JSON form)."""
from __future__ import annotations

import re

import networkx as nx

SCHEMA = "workgraph/1"

LAYER_KINDS = {
    "product": {"family", "product", "application"},
    "org": {"company", "division", "department", "group", "team", "person"},
    "process": {"process", "step"},
}

# id prefix -> layer; anything else is a repo-graph node
PREFIX_LAYER = {"org": "org", "product": "product", "process": "process", "stage": "process", "project": "repo"}

# relation -> (source layers, target layers); None means "same layer as the other end"
RELATION_RULES = {
    "part_of": None,
    "precedes": ({"process"}, {"process"}),
    "belongs_to": ({"repo"}, {"product"}),
    "owned_by": ({"product"}, {"org"}),
    "follows": ({"product"}, {"process"}),
}

UID_RE = re.compile(r"^[0-9a-f]{8}$")


def layer_of(node_id: str) -> str:
    prefix = node_id.partition(":")[0]
    return PREFIX_LAYER.get(prefix, "repo")


def check_relation(relation: str, source: str, target: str) -> str | None:
    """Error text if `relation` may not join these two IDs, else None."""
    if relation not in RELATION_RULES:
        return f"unknown relation {relation!r}; allowed: {sorted(RELATION_RULES)}"
    ls, lt = layer_of(source), layer_of(target)
    rule = RELATION_RULES[relation]
    if rule is None:
        if ls != lt or ls == "repo":
            return f"`part_of` joins nodes of one layer (product, org or process), got {ls} -> {lt}"
        return None
    if ls not in rule[0] or lt not in rule[1]:
        return f"`{relation}` must go {'/'.join(sorted(rule[0]))} -> {'/'.join(sorted(rule[1]))}, got {ls} -> {lt}"
    return None


def validate_layer(doc: dict, name: str = "layer") -> list[str]:
    """Structural validation of one layer file. References to other files are checked later, in refs."""
    errs: list[str] = []
    if not isinstance(doc, dict):
        return [f"{name}: top level must be a mapping"]
    if doc.get("schema") != SCHEMA:
        errs.append(f"{name}: `schema` must be {SCHEMA!r}")
    layer = doc.get("layer")
    if layer not in LAYER_KINDS:
        errs.append(f"{name}: `layer` must be one of {sorted(LAYER_KINDS)}, got {layer!r}")
        return errs
    for key in ("nodes", "edges"):
        if not isinstance(doc.get(key), list):
            errs.append(f"{name}: `{key}` must be a list")
    if errs:
        return errs
    ids: set[str] = set()
    uids: set[str] = set()
    for i, n in enumerate(doc["nodes"]):
        where = f"{name}: nodes[{i}]"
        if not isinstance(n, dict) or not isinstance(n.get("id"), str):
            errs.append(f"{where}: needs a string `id`")
            continue
        nid = n["id"]
        where = f"{name}: node {nid}"
        if layer_of(nid) != layer or ":" not in nid:
            errs.append(f"{where}: id must start with `{layer}:`")
        if nid in ids:
            errs.append(f"{where}: duplicate id")
        ids.add(nid)
        if n.get("kind") not in LAYER_KINDS[layer]:
            errs.append(f"{where}: kind {n.get('kind')!r} not allowed in layer {layer} ({sorted(LAYER_KINDS[layer])})")
        uid = n.get("uid")
        if uid is not None:
            if not isinstance(uid, str) or not UID_RE.match(uid):
                errs.append(f"{where}: uid must be 8 hex characters")
            elif uid in uids:
                errs.append(f"{where}: duplicate uid {uid}")
            else:
                uids.add(uid)
    for i, e in enumerate(doc["edges"]):
        where = f"{name}: edges[{i}]"
        if not isinstance(e, dict) or not all(isinstance(e.get(k), str) for k in ("source", "target", "relation")):
            errs.append(f"{where}: needs string `source`, `target` and `relation`")
            continue
        src, tgt = e["source"].removesuffix("/**"), e["target"].removesuffix("/**")
        if (msg := check_relation(e["relation"], src, tgt)):
            errs.append(f"{where} ({e['source']} -{e['relation']}-> {e['target']}): {msg}")
    parents = nx.DiGraph((e["source"], e["target"]) for e in doc["edges"]
                         if isinstance(e, dict) and e.get("relation") == "part_of")
    if parents and not nx.is_directed_acyclic_graph(parents):
        errs.append(f"{name}: `part_of` edges contain a cycle")
    return errs
