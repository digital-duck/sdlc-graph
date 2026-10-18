"""Domain-rule analysis on the knowledge layer (pure NetworkX, no LLM)."""
from __future__ import annotations

import networkx as nx

from .domain import STRUCTURAL_KINDS, Workspace, glob_match


def find_gaps(M: nx.MultiDiGraph, ws: Workspace) -> list[dict]:
    """Evaluate each domain's `expectations`: artifacts in a stage that lack a required relation."""
    domains, gaps = ws.domains, []
    symbols: dict[tuple, int] = {}
    for _, d in M.nodes(data=True):
        if d.get("kind") == "symbol":
            k = (d.get("project"), d.get("source_file"))
            symbols[k] = symbols.get(k, 0) + 1
    for n, d in M.nodes(data=True):
        if d.get("kind") in ("stage", "project") or d.get("domain") not in domains:
            continue   # process nodes, and layer nodes (product, org, ...) that belong to no domain pack
        for exp in domains[d["domain"]].expectations:
            kinds = exp.get("kinds")
            if exp["stage"] not in d.get("stages", []):
                continue
            if d.get("origin") == "llm" and not exp.get("include_llm"):
                continue   # LLM-extracted items are unverified; they don't trigger gap rules by default
            if exp.get("exclude") and glob_match(d.get("source_file") or "", exp["exclude"]):
                continue
            if symbols.get((d["project"], d.get("source_file")), 0) < exp.get("min_symbols", 0):
                continue
            if (d["kind"] not in kinds) if kinds else (d["kind"] in STRUCTURAL_KINDS):
                continue
            edges = M.in_edges(n, data=True) if exp.get("direction", "in") == "in" else M.out_edges(n, data=True)
            if not any(e.get("relation") == exp["needs"] for *_, e in edges):
                gaps.append({"node": n, "label": d.get("label", n), "project": d["project"], "domain": d["domain"],
                             "stage": exp["stage"], "needs": exp["needs"], "message": exp["message"],
                             "source_file": d.get("source_file")})
    return sorted(gaps, key=lambda g: (g["domain"], g["message"], g["label"]))
