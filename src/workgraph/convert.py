"""JSON <-> YAML for layer files. YAML comments are kept in the SQLite store (anchored by item, not line number)
and re-attached when JSON is converted back, so they survive the round trip."""
from __future__ import annotations

import io
import json
from pathlib import Path

from ruamel.yaml import YAML
from zinet.roundtrip import Layout
from zinet.roundtrip import attach as _attach
from zinet.roundtrip import harvest as _harvest

from .store import Store, file_key

NODE_KEYS = ("id", "uid", "kind", "label")
EDGE_KEYS = ("source", "relation", "target")


def _yaml() -> YAML:
    y = YAML()
    y.indent(mapping=2, sequence=4, offset=2)
    y.width = 4096
    y.default_flow_style = False
    return y


def _plain(x):
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_plain(v) for v in x]
    return x


def _ordered(item: dict, first: tuple[str, ...]) -> dict:
    return {**{k: item[k] for k in first if k in item}, **{k: v for k, v in item.items() if k not in first}}


def edge_key(e: dict) -> str:
    return f"{e.get('source')}|{e.get('relation')}|{e.get('target')}"


def item_key(section: str, item: dict) -> str:
    return edge_key(item) if section == "edges" else str(item.get("id"))


def dump_yaml(doc: dict) -> str:
    """Canonical YAML layout: fixed key order, block style, one item per list entry."""
    out = {"schema": doc.get("schema"), "layer": doc.get("layer"),
           "nodes": [_ordered(n, NODE_KEYS) for n in doc.get("nodes", [])],
           "edges": [_ordered(e, EDGE_KEYS) for e in doc.get("edges", [])]}
    out.update({k: v for k, v in doc.items() if k not in out})
    buf = io.StringIO()
    _yaml().dump(out, buf)
    return buf.getvalue()


# The comment handling (harvest / attach) is zinet's; this is the layout of a workgraph layer file: `nodes` are named by `id`,
# `edges` by `source|relation|target`.
LAYOUT = Layout(("nodes", "edges"), item_key)


def harvest(text: str) -> list[dict]:
    """Comments in YAML text -> anchored comments: {item_key, key, position, text}."""
    return _harvest(text, LAYOUT)


def attach(text: str, comments: list[dict]) -> tuple[str, list[dict]]:
    """Re-attach stored comments to freshly written YAML. Returns (text, orphans)."""
    return _attach(text, comments, LAYOUT)


def yaml_to_json(src: Path, dst: Path, store: Store) -> dict:
    text = src.read_text(encoding="utf-8")
    doc = _plain(YAML(typ="safe").load(text))
    store.replace_comments(file_key(src), harvest(text))
    dst.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    store.register(src.with_suffix(""), doc.get("layer") if isinstance(doc, dict) else None)
    return {"comments": len(store.comments(file_key(src)))}


def json_to_yaml(src: Path, dst: Path, store: Store) -> dict:
    doc = json.loads(src.read_text(encoding="utf-8"))
    base = file_key(src)
    if dst.exists():
        store.replace_comments(base, harvest(dst.read_text(encoding="utf-8")))   # keep edits made since last convert
    text, orphans = attach(dump_yaml(doc), store.comments(base))
    dst.write_text(text, encoding="utf-8")
    store.register(src.with_suffix(""), doc.get("layer"))
    store.remember_ids(doc.get("layer", ""), doc.get("nodes", []))
    return {"comments": len(store.comments(base)), "orphans": len(orphans)}
