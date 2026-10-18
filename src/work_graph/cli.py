from __future__ import annotations

import argparse
import json
import os
from itertools import groupby
from pathlib import Path

import networkx as nx

from .analyze import find_gaps
from .domain import Domain, Workspace, available_domains
from .llm import BILLING, check_backend, resolve_backend
from .pipeline import build_workspace, stage_summary
from .refs import assign_uids, layer_summary, load_layers, merge_layers, missing_uids, stale_notes


def _build(args):
    ws = Workspace.load(args.config)
    M, communities, warnings = build_workspace(ws, semantic=args.semantic)
    return ws, M, communities, warnings


def cmd_domains(args) -> None:
    for name in available_domains():
        d = Domain.load(name)
        print(f"{name:<11} {d.label}: " + " → ".join(d.stages))


def cmd_llm(args) -> None:
    """Show which backend each project's LLM processors will use, and whether it looks ready."""
    ws = Workspace.load(args.config)
    for p in ws.projects:
        for proc in p.domain.processors:
            if proc.use in ("llm", "llm_items"):
                b = resolve_backend(None, p.backend, proc.backend)
                print(f"{p.name}/{proc.name} ({proc.use}) -> {b}   billing: {BILLING.get(b, 'see provider')}")
                for name, ok, detail in check_backend(b):
                    print(f"    [{'ok' if ok else '!!'}] {name}" + (f": {detail}" if detail else ""))


def cmd_doctor(args) -> None:
    """Where is this running, and is it the environment you meant? Catches installs into conda base."""
    import importlib.metadata as md
    import sys
    from pathlib import Path

    import graphify

    prefix = Path(sys.prefix)
    in_env = sys.prefix != getattr(sys, "base_prefix", sys.prefix) or "envs" in prefix.parts
    base_like = "conda" in str(prefix).lower() and "envs" not in prefix.parts and sys.prefix == sys.base_prefix
    gpath = Path(graphify.__file__).resolve().parent
    editable = "site-packages" not in gpath.parts
    try:
        import graspologic  # noqa: F401
        cluster = "Leiden (graspologic)"
    except ImportError:
        cluster = "Louvain fallback (install the `leiden` extra for Leiden)"
    rows = [
        ("python", f"{sys.version.split()[0]} at {sys.executable}", True),
        ("environment", str(prefix), not base_like),
        ("graphify", f"{md.version('graphifyy')} ({'editable fork at ' + str(gpath.parent) if editable else 'PyPI release'})", True),
        ("work-graph", md.version("work-graph"), True),
        ("clustering", cluster, True),
        ("default LLM backend", resolve_backend(None), True),
    ]
    for name, value, ok in rows:
        print(f"[{'ok' if ok else '!!'}] {name:<20} {value}")
    if base_like:
        print("\nThis looks like your conda *base* environment. Create and activate a dedicated one "
              "(docs/GUIDE/SETUP.md, section 3) so installs do not change base packages.")
    elif not in_env:
        print("\nNot inside a virtual or conda environment.")


def cmd_build(args) -> None:
    ws, M, communities, warnings = _build(args)
    print(f"{M.number_of_nodes()} nodes, {M.number_of_edges()} edges, {len(communities)} communities")
    for dom, stages in stage_summary(M).items():
        print(f"[{dom}]")
        for s in ws.domains[dom].stages:
            print(f"  {s:<12} {stages.get(s, {})}")
    layers = layer_summary(M)
    if len(layers) > 1:
        print("layers: " + ", ".join(f"{k} {v}" for k, v in sorted(layers.items())))
    clones = M.graph.get("clones", [])
    if clones:
        print(f"cross-project: {sum(g['identical'] for g in clones)} identical, "
              f"{sum(not g['identical'] for g in clones)} variant files (see `clones`)")
    for w in warnings:
        print(f"warning: {w}")
    out = Path(args.out)
    out.mkdir(exist_ok=True)
    (out / "graph.json").write_text(json.dumps(nx.node_link_data(M, edges="edges"), default=str), encoding="utf-8")
    print(f"wrote {out / 'graph.json'}")


def cmd_check(args) -> None:
    """Validate the layer files and every cross-graph reference; report renames, stale YAML, missing uids."""
    from .store import Store
    ws = Workspace.load(args.config)
    if not ws.graphs_dir:
        raise SystemExit("work-graph: no graphs directory (add `graphs: <dir>` to the workspace file)")
    if args.assign_uids:
        print(f"assigned {assign_uids(ws.graphs_dir)} uids")
    with Store(ws.graphs_dir / "store.sqlite") as store:
        docs, errors = load_layers(ws.graphs_dir)
        for name, doc in docs.items():
            store.remember_ids(doc["layer"], doc["nodes"])
        M, _, warnings = build_workspace(ws)
        merge_problems = [w for w in warnings if w.split(":")[0].endswith(".json")]
        notes = stale_notes(ws.graphs_dir, store) + missing_uids(docs)
    problems = errors + [w for w in merge_problems if w not in errors]
    for p in problems:
        print(f"error: {p}")
    for n in dict.fromkeys(notes):
        print(f"note: {n}")
    print(f"{len(docs)} layer files, {sum(len(d['nodes']) for d in docs.values())} nodes, "
          f"{sum(len(d['edges']) for d in docs.values())} edges; {len(problems)} errors, {len(set(notes))} notes")
    if problems:
        raise SystemExit(1)


def cmd_convert(args) -> None:
    """JSON <-> YAML for a layer file; YAML comments are saved in, and restored from, the SQLite store."""
    from .convert import json_to_yaml, yaml_to_json
    from .store import Store
    src = Path(args.file)
    if src.suffix not in (".json", ".yaml", ".yml"):
        raise SystemExit("work-graph: convert takes a .json or .yaml file")
    to_json = src.suffix != ".json"
    dst = Path(args.out) if args.out else src.with_suffix(".json" if to_json else ".yaml")
    with Store(args.db or src.parent / "store.sqlite") as store:
        info = (yaml_to_json if to_json else json_to_yaml)(src, dst, store)
    msg = f"wrote {dst} ({info['comments']} comments saved)" if to_json else \
        f"wrote {dst} ({info['comments']} comments restored" + (f", {info['orphans']} orphaned" if info["orphans"] else "") + ")"
    print(msg)


def cmd_clones(args) -> None:
    ws, M, _, _ = _build(args)
    groups = M.graph.get("clones", [])
    same = [g for g in groups if g["identical"]]
    print(f"{len(same)} identical and {len(groups) - len(same)} variant files shared across projects")
    for g in groups:
        print(f"  {'=' if g['identical'] else '~'} {g['path']}  ({', '.join(g['projects'])})")


def cmd_open(args) -> None:
    """Open work items extracted by the LLM pass, grouped by source document."""
    ws, M, _, _ = _build(args)
    items = [d for _, d in M.nodes(data=True) if d.get("origin") == "llm" and d.get("status") == "open"]
    if not items:
        print("no open items (run with --semantic to extract them from planning docs)")
    for src, group in groupby(sorted(items, key=lambda d: (d["project"], d["source_file"])),
                              key=lambda d: (d["project"], d["source_file"])):
        group = list(group)
        print(f"\n{src[0]}:{src[1]} ({len(group)} open)")
        for d in group:
            print(f"  - [{d['kind']}/{','.join(d['stages'])}] {d['label']}")


def cmd_gaps(args) -> None:
    ws, M, _, warnings = _build(args)
    gaps = find_gaps(M, ws)
    for msg, items in groupby(gaps, key=lambda g: (g["domain"], g["message"])):
        items = list(items)
        print(f"\n[{msg[0]}] {msg[1]} ({len(items)})")
        for g in items:
            print(f"  - {g['label']}  ({g['project']}:{g['source_file']})")
    if warnings:
        print(f"\n[warnings] ({len(warnings)})")
        for w in warnings:
            print(f"  - {w}")
    print(f"\n{len(gaps)} gaps, {len(warnings)} warnings")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="work-graph")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("check", help="validate layer files and cross-graph references")
    s.add_argument("-c", "--config", default="workspace.yaml")
    s.add_argument("--assign-uids", action="store_true", help="write a uid into layer nodes that lack one")
    s.set_defaults(fn=cmd_check)
    s = sub.add_parser("convert", help="convert a layer file between JSON and YAML, keeping YAML comments")
    s.add_argument("file")
    s.add_argument("-o", "--out")
    s.add_argument("--db", help="comment store (default: store.sqlite next to the file)")
    s.set_defaults(fn=cmd_convert)
    sub.add_parser("domains", help="list domain packs").set_defaults(fn=cmd_domains)
    sub.add_parser("doctor", help="show python/environment/graphify details and warn about conda base").set_defaults(fn=cmd_doctor)
    s = sub.add_parser("llm", help="show the resolved LLM backend per processor and check readiness")
    s.add_argument("-c", "--config", default="workspace.yaml")
    s.add_argument("--adapter", help="LLM backend, e.g. cc_cli (subscription) or bedrock; overrides config")
    s.set_defaults(fn=cmd_llm)
    for name, fn, h in (("build", cmd_build, "build the graph"), ("gaps", cmd_gaps, "report gaps per domain rules"),
                         ("clones", cmd_clones, "files shared across projects"),
                         ("open", cmd_open, "open work items from planning docs (use with --semantic)")):
        s = sub.add_parser(name, help=h)
        s.add_argument("-c", "--config", default="workspace.yaml")
        s.add_argument("--adapter", help="LLM backend, e.g. cc_cli (subscription) or bedrock; overrides config")
        s.add_argument("--semantic", action="store_true", help="also run LLM processors (needs a backend/API key)")
        s.add_argument("-o", "--out", default="work-graph-out")
        s.set_defaults(fn=fn)
    args = ap.parse_args(argv)
    if getattr(args, "adapter", None):
        try:
            resolve_backend(args.adapter)                   # fail fast on a typo
        except ValueError as e:
            raise SystemExit(f"work-graph: {e}")
        os.environ["WORK_GRAPH_BACKEND"] = args.adapter     # same effect as exporting it
    try:
        args.fn(args)
    except (ValueError, RuntimeError, FileNotFoundError) as e:
        raise SystemExit(f"work-graph: {e}")


if __name__ == "__main__":
    main()
