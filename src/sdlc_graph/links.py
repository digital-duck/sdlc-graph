"""Cross-project links: files that are byte-identical (`identical_to`) or share a path but differ
(`variant_of`) across projects. Content hashes only, so no LLM and no claims about git history."""
from __future__ import annotations

import hashlib
from collections import defaultdict

import networkx as nx

from .domain import Workspace

_MIN_BYTES = 64   # ignore empty/trivial files (empty __init__.py would "match" everywhere)


def cross_project_links(M: nx.MultiDiGraph, ws: Workspace) -> list[dict]:
    """Adds edges from later projects to the first project holding the same content / path.
    Returns clone groups: [{"path", "projects", "identical": bool}]."""
    if len(ws.projects) < 2:
        return []
    order = {p.name: i for i, p in enumerate(ws.projects)}
    roots = {p.name: p.path for p in ws.projects}
    by_path: dict[str, list[tuple[str, str, str]]] = defaultdict(list)   # rel path -> [(project, node, hash)]
    for n, d in M.nodes(data=True):
        if d.get("kind") not in ("file", "document") or not d.get("source_file"):
            continue
        try:
            data = (roots[d["project"]] / d["source_file"]).read_bytes()
        except (OSError, KeyError):
            continue
        if len(data) >= _MIN_BYTES:
            by_path[d["source_file"]].append((d["project"], n, hashlib.sha256(data).hexdigest()))
    groups = []
    for path, members in by_path.items():
        if len({m[0] for m in members}) < 2:
            continue
        members.sort(key=lambda m: order[m[0]])
        canon = members[0]
        for proj, node, h in members[1:]:
            if proj == canon[0]:
                continue
            same = h == canon[2]
            M.add_edge(node, canon[1], relation="identical_to" if same else "variant_of",
                       confidence="EXTRACTED" if same else "INFERRED", source_file=path)
        groups.append({"path": path, "projects": [m[0] for m in members],
                       "identical": len({m[2] for m in members}) == 1})
    return sorted(groups, key=lambda g: (-len(g["projects"]), g["path"]))
