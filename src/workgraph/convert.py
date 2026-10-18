"""JSON <-> YAML for layer files. YAML comments are kept in the SQLite store (anchored by item, not line number)
and re-attached when JSON is converted back, so they survive the round trip."""
from __future__ import annotations

import io
import json
import re
from pathlib import Path

from ruamel.yaml import YAML

from .store import Store, file_key

ITEM_RE = re.compile(r"^(\s*)- ")
KEY_RE = re.compile(r"^\s*(?:- )?([\w.\-]+):(?:\s|$)")
SECTION_RE = re.compile(r"^([\w.\-]+):")
ORPHAN_RE = re.compile(r"^\[orphaned from (.+?)\] (.*)$")
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


def _split_comment(line: str) -> tuple[str, str | None]:
    """(code part, comment text without '#') for a line; '#' inside quotes does not count."""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i].rstrip(), line[i + 1:].strip()
    return line, None


def _index(lines: list[str]) -> tuple[dict[int, tuple[str, str | None]], dict[str, dict]]:
    """Locate items in YAML text. Returns ({code line: (anchor, key)}, {anchor: {"start": line, "keys": {key: line}}}).
    Anchors are node ids, `source|relation|target` for edges, or `@section:<name>` for top-level keys."""
    chunks: list[tuple[str, int, int]] = []          # (section, first line, end line) per list item
    section, indent, start = None, None, None
    for i, ln in enumerate(lines + ["end:"]):
        code = _split_comment(ln)[0]
        if not code.strip():
            continue
        if SECTION_RE.match(code):
            if start is not None:
                chunks.append((section, start, i))
                start = None
            section, indent = SECTION_RE.match(code).group(1), None
            continue
        im = ITEM_RE.match(code)
        if im and section in ("nodes", "edges"):
            indent = len(im.group(1)) if indent is None else indent
            if len(im.group(1)) == indent:
                if start is not None:
                    chunks.append((section, start, i))
                start = i
    info: dict[int, tuple[str, str | None]] = {}
    where: dict[str, dict] = {}
    loader = YAML(typ="safe")
    covered: set[int] = set()
    for sec, a, b in chunks:
        body = "\n".join(_split_comment(x)[0] for x in lines[a:b]).replace("- ", "  ", 1)
        try:
            data = loader.load(body)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        key = item_key(sec, data)
        entry = where.setdefault(key, {"start": a, "keys": {}})
        for j in range(a, b):
            code = _split_comment(lines[j])[0]
            if not code.strip():
                continue
            km = KEY_RE.match(code)
            k = km.group(1) if km and (j == a or len(code) - len(code.lstrip()) <= len(lines[a]) - len(lines[a].lstrip()) + 2) else None
            if j == a:
                info[j] = (key, None)
            else:
                info[j] = (key, k)
            if k:
                entry["keys"].setdefault(k, j)
            covered.add(j)
    section = None
    for i, ln in enumerate(lines):
        code = _split_comment(ln)[0]
        m = SECTION_RE.match(code) if code.strip() else None
        if m:
            section = m.group(1)
            where[f"@section:{section}"] = {"start": i, "keys": {}}
            info[i] = (f"@section:{section}", None)
    return info, where


def harvest(text: str) -> list[dict]:
    """Comments in YAML text -> anchored comments: {item_key, key, position, text}."""
    lines = text.splitlines()
    info, _ = _index(lines)
    out: list[dict] = []
    pending: list[str] = []
    for i, ln in enumerate(lines):
        code, comment = _split_comment(ln)
        if not code.strip():
            if comment is not None:
                pending.append(comment)
            continue
        anchor, key = info.get(i, ("@file", None))
        for c in pending:
            out.append({"item_key": anchor, "key": key, "position": "before", "text": c})
        pending = []
        if comment is not None:
            out.append({"item_key": anchor, "key": key, "position": "trailing", "text": comment})
    for c in pending:
        m = ORPHAN_RE.match(c)    # an orphan written by `attach`: keep its original anchor so it can re-attach
        out.append({"item_key": m.group(1) if m else "@file", "key": None, "position": "before",
                    "text": m.group(2) if m else c})
    return out


def attach(text: str, comments: list[dict]) -> tuple[str, list[dict]]:
    """Re-attach stored comments to freshly written YAML. Returns (text, orphans)."""
    lines = text.splitlines()
    _, where = _index(lines)
    before: dict[int, list[str]] = {}
    trailing: dict[int, list[str]] = {}
    orphans: list[dict] = []
    for c in comments:
        if c["item_key"] == "@file":
            before.setdefault(0, []).append(c["text"])
            continue
        w = where.get(c["item_key"])
        if not w:
            orphans.append(c)
            continue
        line = w["keys"].get(c["key"], w["start"]) if c["key"] else w["start"]
        (before if c["position"] == "before" else trailing).setdefault(line, []).append(c["text"])
    out: list[str] = []
    for i, ln in enumerate(lines):
        pad = ln[:len(ln) - len(ln.lstrip())]
        out += [f"{pad}# {t}".rstrip() for t in before.get(i, [])]
        out.append(ln + "".join(f"  # {t}" for t in trailing.get(i, [])))
    out += [f"# [orphaned from {c['item_key']}] {c['text']}" for c in orphans]
    return "\n".join(out) + "\n", orphans


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
