"""Workspace build: process layer + per-project processors -> tagged, linked knowledge graph."""
from __future__ import annotations

from pathlib import Path

import networkx as nx

from graphify.build import build
from graphify.cluster import cluster

from .domain import STRUCTURAL_KINDS, Project, Workspace, make_id
from . import paths
from .links import cross_project_links
from .processors import run_processors
from .refs import merge_layers
from .store import Store

STRUCTURAL_RELATIONS = {"imports", "imports_from", "calls", "uses", "references"}


def _kind(n: dict) -> str:
    if n.get("kind"):
        return n["kind"]
    if n.get("file_type") == "rationale":
        return "rationale"
    src, label = n.get("source_file") or "", n.get("label", "")
    return "file" if src and label == Path(src).name else "symbol"


def _tag(project: Project, ex: dict, warnings: list[str]) -> dict:
    """Namespace ids by project, set kind/domain, and attach validated stage tags + their edges."""
    d, pid = project.domain, f"project:{project.name}"
    pre = lambda i: f"{project.name}:{i}"  # noqa: E731
    nodes, edges = [], []
    for n in ex["nodes"]:
        src = n.get("source_file") or ""
        try:
            n["source_file"] = Path(src).resolve().relative_to(project.path).as_posix() if Path(src).is_absolute() else src
        except ValueError:
            pass
        n["id"], n["project"], n["domain"], n["kind"] = pre(n["id"]), project.name, d.name, _kind(n)
        tags = []
        # LLM items carry their own stage tags; path rules only fill in when the LLM gave none.
        # memory nodes are not lifecycle artifacts, so path rules must not tag them either
        path_tags = [] if (n.get("origin") == "llm" and n.get("stages")) or n["kind"] == "memory" else d.classify(n["source_file"] or "")
        for t in list(n.get("stages", [])) + path_tags:
            if t in d.stages:
                tags.append(t)
            else:
                warnings.append(f"{n['source_file']}: unknown stage tag {t!r} for domain {d.name!r} (ignored)")
        n["stages"] = list(dict.fromkeys(tags))
        nodes.append(n)
        for t in n["stages"]:
            edges.append({"source": n["id"], "target": d.stage_node(t), "relation": "in_stage",
                          "confidence": "EXTRACTED", "source_file": n["source_file"]})
        edges.append({"source": n["id"], "target": pid, "relation": "belongs_to",
                      "confidence": "EXTRACTED", "source_file": n["source_file"]})
    for e in ex["edges"]:
        edges.append({**e, "source": pre(e["source"]), "target": pre(e["target"])})
    used = sorted({t for n in nodes for t in n["stages"]})
    nodes.append({"id": pid, "label": project.name, "file_type": "concept", "source_file": str(project.path),
                  "kind": "project", "project": project.name, "domain": d.name, "stages": used})
    edges += [{"source": pid, "target": d.stage_node(t), "relation": "participates_in",
               "confidence": "EXTRACTED", "source_file": str(project.path)} for t in used]
    return {"nodes": nodes, "edges": edges, "hyperedges": []}


def _resolve_refs(projects: list[Project], refs: list[tuple[Project, dict]], extractions: list[dict],
                  warnings: list[str]) -> list[dict]:
    """Turn frontmatter/annotation references into edges. Unresolvable ones become warnings."""
    doc_ids: dict[str, str] = {}
    path_ids: dict[tuple[str, str], str] = {}
    for ex in extractions:
        for n in ex["nodes"]:
            if n.get("doc_id"):
                doc_ids[make_id(n["doc_id"])] = n["id"]
            if n["kind"] not in STRUCTURAL_KINDS and n.get("source_file"):
                # file-level node wins the path slot (document nodes and AST file hubs)
                if n["kind"] != "symbol":
                    path_ids.setdefault((n["project"], n["source_file"]), n["id"])
    edges = []
    for p, r in refs:
        kind, val = r["source"]
        src = f"{p.name}:{val}" if kind == "id" else path_ids.get((p.name, val))
        tgt = doc_ids.get(make_id(r["target"])) or path_ids.get((p.name, r["target"]))
        if not src or not tgt:
            warnings.append(f"{r['file']}: broken reference `{r['relation']}: {r['target']}`")
            continue
        edges.append({"source": src, "target": tgt, "relation": r["relation"],
                      "confidence": r.get("confidence", "EXTRACTED"),
                      "source_file": r["file"]})
    return edges


def derive_flow_edges(M: nx.MultiDiGraph, ws: Workspace) -> int:
    """Lift structural edges (imports/calls) to file level and add the domain's flow relation as INFERRED,
    e.g. a test file importing a code file -> `verifies`. Generic: driven by flow_relations."""
    domains = ws.domains
    file_of = {(d["project"], d["source_file"]): n for n, d in M.nodes(data=True) if d.get("kind") == "file"}
    lift = lambda n: file_of.get((M.nodes[n].get("project"), M.nodes[n].get("source_file")))  # noqa: E731
    have = {(u, v, d.get("relation")) for u, v, d in M.edges(data=True)}
    new = []
    for u, v, d in M.edges(data=True):
        if d.get("relation") not in STRUCTURAL_RELATIONS:
            continue
        fu, fv = lift(u), lift(v)
        if not fu or not fv or fu == fv or M.nodes[fu]["domain"] != M.nodes[fv]["domain"]:
            continue
        dom = domains[M.nodes[fu]["domain"]]
        for r in dom.flow_relations:
            if r["from"] in M.nodes[fu]["stages"] and r["to"] in M.nodes[fv]["stages"] and (fu, fv, r["relation"]) not in have:
                have.add((fu, fv, r["relation"]))
                new.append((fu, fv, {"relation": r["relation"], "confidence": "INFERRED", "derived_from": d["relation"],
                                     "source_file": M.nodes[fu].get("source_file", "")}))
    M.add_edges_from(new)
    return len(new)


def _adopt_externals(G: nx.Graph, ws: Workspace) -> None:
    """graphify adds stub nodes for unresolved imports (os, fastapi, ...). Keep them as `external`
    dependency nodes owned by the importing project, so every node has a kind/project/domain."""
    by_name = {p.name: p for p in ws.projects}
    for n, d in G.nodes(data=True):
        if "kind" in d:
            continue
        proj, _, name = n.partition(":")
        p = by_name.get(proj)
        d.update(kind="external", label=name or n, stages=[], project=proj,
                 domain=p.domain.name if p else "unknown")


def knowledge_layer(G: nx.Graph) -> nx.Graph:
    """The graph without process nodes (stages/projects), which are hubs that would skew analysis."""
    return G.subgraph([n for n, d in G.nodes(data=True) if d.get("kind") not in ("stage", "project")])


def build_workspace(ws: Workspace, *, semantic: bool = False) -> tuple[nx.MultiDiGraph, dict, list[str]]:
    """Returns (graph, communities, warnings). Communities are computed on the knowledge layer only."""
    warnings: list[str] = []
    extractions = [d.process_extraction() for d in ws.domains.values()]
    refs: list[tuple[Project, dict]] = []
    project_exs = []
    for p in ws.projects:
        raw = run_processors(p, semantic=semantic)
        refs += [(p, r) for r in raw.pop("refs")]
        warnings += [f"{p.name}: {w}" for w in raw.pop("warnings", [])]
        project_exs.append(_tag(p, raw, warnings))
    extractions += project_exs
    extractions.append({"nodes": [], "hyperedges": [], "edges": _resolve_refs(ws.projects, refs, project_exs, warnings)})
    # dedup off: label-similarity merging could fuse distinct requirements/decisions.
    G = build(extractions, directed=True, dedup=False)
    _adopt_externals(G, ws)
    communities = cluster(knowledge_layer(G)) if knowledge_layer(G).number_of_nodes() else {}
    M = nx.MultiDiGraph(G)  # multi-edges: a structural edge and a flow edge can join the same pair
    derive_flow_edges(M, ws)
    M.graph["clones"] = cross_project_links(M, ws)
    store = Store(paths.store_path()) if ws.graphs_dir and paths.store_path().exists() else None
    warnings += merge_layers(M, ws, store)
    if store:
        store.close()
    return M, communities, warnings


def stage_summary(G: nx.Graph) -> dict[str, dict[str, dict[str, int]]]:
    """{domain: {stage: {project: artifact_count}}}; multi-tagged artifacts count under each stage."""
    out: dict = {}
    for _, d in G.nodes(data=True):
        if d.get("kind") in STRUCTURAL_KINDS:
            continue
        for s in d.get("stages", []):
            c = out.setdefault(d["domain"], {}).setdefault(s, {})
            c[d["project"]] = c.get(d["project"], 0) + 1
    return out
