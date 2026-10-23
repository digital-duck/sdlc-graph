from __future__ import annotations

import argparse
import json
import os
from itertools import groupby
from pathlib import Path

import networkx as nx

from .analyze import find_gaps
from . import paths
from . import layers
from .domain import Domain, Workspace, available_domains, custom_domain_files, validate_pack
from .sources import workspace_from_sources
from .llm import BILLING, check_backend, resolve_backend
from .pipeline import build_workspace, stage_summary
from .progress import NullProgress, Progress
from . import query
from .refs import assign_uids, layer_summary, load_layers, merge_layers, missing_uids, stale_notes


def _workspace(args) -> Workspace:
    """--src paths/URLs, else -c FILE, else ./workspace.yaml, else the default workspace in the work-graph home.
    A relative -c that is not in the current folder is also looked for in the home folder, so `-c workspace.yaml`
    finds the default workspace from anywhere."""
    if getattr(args, "src", None):
        if args.config:
            raise ValueError("use either --src or -c, not both")
        return workspace_from_sources(args.src, args.domain)
    if args.config:
        cfg = Path(args.config)
        if not cfg.exists() and not cfg.is_absolute() and (paths.home() / cfg).exists():
            cfg = paths.home() / cfg
        where = f"{args.config!r} (looked in the current folder and in {paths.home()})"
    else:
        cfg = Path("workspace.yaml") if Path("workspace.yaml").exists() else paths.default_workspace()
        where = str(cfg)
    if not cfg.exists():
        raise FileNotFoundError(f"no workspace file {where}: pass -c FILE (the examples have one: examples/workspace.yaml) "
                                f"or --src PATH_OR_URL, or run `workgraph init` and `workgraph track <path>`")
    ws = Workspace.load(cfg)
    if not ws.projects:
        why = (f"its projects have no folder on this machine yet ({', '.join(ws.unresolved)}): "
               f"`workgraph track <folder>` binds one, `workgraph sync pull --clone-missing` clones them") if ws.unresolved \
            else "it lists no projects yet: `workgraph track <folder>` adds one, or use --src PATH_OR_URL"
        raise ValueError(f"nothing to build in {cfg}: {why}")
    return ws


def _build(args, final: bool = True):
    """Build the graph with progress on stderr. `final=False` leaves the closing timing to the caller (`build`)."""
    ws = _workspace(args)
    quiet = getattr(args, "quiet", False) or os.environ.get("WORKGRAPH_QUIET")
    args.progress = NullProgress() if quiet else Progress()
    M, communities, warnings = build_workspace(ws, semantic=args.semantic, progress=args.progress)
    if final:
        args.progress.finish_line()
    return ws, M, communities, warnings


def cmd_domains(args) -> None:
    for name in available_domains():
        d = Domain.load(name)
        mark = "  (custom)" if name in custom_domain_files() else ""
        print(f"{name:<11} {d.label}: " + " → ".join(d.stages) + mark)


def cmd_layers(args) -> None:
    """Print the layer graph: coarsest layer first, its dependencies, and the counts of the last build."""
    G = layers.layer_graph()
    out = Path(args.out) if args.out else None
    meta = layers.load_meta(out) if layers.available(out) else None
    counts = {x["id"]: x for x in meta["layers"]} if meta else {}
    print("layers, coarsest first (rank 0 = finest):")
    for n in layers.by_coarseness(G):
        c = counts.get(n)
        print(f"  {n:<9} rank {G.nodes[n]['rank']}" + (f"   {c['nodes']} nodes, {c['edges']} edges" if c else ""))
    print("dependencies (source points at the coarser target):")
    deps = {(x["source"], x["target"], x["relation"]): x["edges"] for x in meta["dependencies"]} if meta else {}
    for u, v, d in G.edges(data=True):
        for r in d["relations"]:
            n = deps.get((u, v, r))
            print(f"  {u} -{r}-> {v}" + (f"   {n} edges" if n is not None else ""))
    if meta and meta["undeclared"]:
        print("cross-layer edges outside the declared dependencies (informational):")
        for x in meta["undeclared"]:
            print(f"  {x['source']} -{x['relation']}-> {x['target']}   {x['edges']} edges")
    if not meta:
        print("(no build found: run `workgraph build` to see counts)")


def cmd_validate_pack(args) -> None:
    """Check domain packs (files or names) and list every problem; exit 1 if any pack has one."""
    bad = 0
    for pack in args.pack:
        errs = validate_pack(pack)
        if errs:
            bad += 1
            print(f"[!!] {pack}: {len(errs)} problem(s)")
            for e in errs:
                print(f"     - {e}")
        else:
            print(f"[ok] {pack}")
    if bad:
        raise SystemExit(1)


def cmd_llm(args) -> None:
    """Show which backend each project's LLM processors will use, and whether it looks ready."""
    ws = _workspace(args)
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
        ("workgraph", md.version("workgraph"), True),
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
    from .tracking import init_home
    init_home()      # a build writes into the home folder, so make sure all of it exists, database included
    ws, M, communities, warnings = _build(args, final=False)
    print(f"{M.number_of_nodes()} nodes, {M.number_of_edges()} edges, {len(communities)} communities")
    for dom, stages in stage_summary(M).items():
        print(f"[{dom}]")
        for s in ws.domains[dom].stages:
            print(f"  {s:<12} {stages.get(s, {})}")
    counts = layer_summary(M)
    if len(counts) > 1:
        print("layers: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    clones = M.graph.get("clones", [])
    if clones:
        print(f"cross-project: {sum(g['identical'] for g in clones)} identical, "
              f"{sum(not g['identical'] for g in clones)} variant files (see `clones`)")
    for w in warnings:
        print(f"warning: {w}")
    out = Path(args.out) if args.out else paths.out_dir()
    out.mkdir(parents=True, exist_ok=True)
    with args.progress.phase("writing graph.json", "write graph"):
        (out / "graph.json").write_text(json.dumps(nx.node_link_data(M, edges="edges"), default=str), encoding="utf-8")
    print(f"wrote {out / 'graph.json'}")
    with args.progress.phase("writing layer partitions", "write layers"):
        print(f"wrote {layers.write_partitions(M, out / 'layers')}")
    if args.progress.enabled:
        print(args.progress.table())          # where the time went


def cmd_check(args) -> None:
    """Validate the layer files and every cross-graph reference; report renames, stale YAML, missing uids."""
    from .store import Store
    ws = _workspace(args)
    if not ws.graphs_dir:
        raise SystemExit("workgraph: no graphs directory (add `graphs: <dir>` to the workspace file)")
    if args.assign_uids:
        print(f"assigned {assign_uids(ws.graphs_dir)} uids")
    with Store(paths.store_path()) as store:
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
        raise SystemExit("workgraph: convert takes a .json or .yaml file")
    to_json = src.suffix != ".json"
    dst = Path(args.out) if args.out else src.with_suffix(".json" if to_json else ".yaml")
    with Store(args.db or paths.store_path()) as store:
        info = (yaml_to_json if to_json else json_to_yaml)(src, dst, store)
    msg = f"wrote {dst} ({info['comments']} comments saved)" if to_json else \
        f"wrote {dst} ({info['comments']} comments restored" + (f", {info['orphans']} orphaned" if info["orphans"] else "") + ")"
    print(msg)


def cmd_init(args) -> None:
    from .tracking import init_home
    h = init_home()
    print(f"workgraph home: {h}")
    for name in ("workspace.yaml", "workgraph.sqlite3", "custom/ (your layer files go in custom/graphs/)", "out/", "cache/",
                 "src/ (created on first --src URL)"):
        print(f"  {name}")


def cmd_track(args) -> None:
    from .tracking import track_project
    added = track_project(args.path, args.name, args.domain)
    print(f"{'tracking' if added else 'already tracking'} {Path(args.path).resolve()} in {paths.default_workspace()}")


def cmd_import_claude(args) -> None:
    """Backfill from Claude Code: track the projects it has worked in and load session metadata."""
    from . import claude_import as ci
    from .claude_code import _project_name
    from .store import Store
    from .tracking import track_project
    root = Path(args.projects_dir).expanduser() if args.projects_dir else ci.claude_dir() / "projects"
    if not root.is_dir():
        raise SystemExit(f"workgraph: no Claude Code projects folder at {root}")
    ws_file = paths.default_workspace()
    import yaml
    tracked = set()
    if ws_file.exists():
        for p in (yaml.safe_load(ws_file.read_text()) or {}).get("projects") or []:
            if p.get("path"):
                tracked.add((ws_file.parent / str(p["path"])).resolve())
    found = ci.plan(ci.scan(root), tracked, args.include_nested)
    print(f"{len(found)} Claude Code project folders in {root}\n")
    for f in found:
        shown = str(f.path) if f.path else f"({f.slug})"
        print(f"  {f.action:<46} {shown}   [{f.memory_files} memory, {len(f.sessions)} sessions]")
    todo = sum(1 for f in found if f.action == "track")
    if not args.apply:
        print(f"\ndry run: nothing changed. `--apply` would track {todo} folders and import session metadata"
              f"{' with titles' if args.with_titles else ''}. Memory files are read at build time for tracked folders.")
        return
    with Store(paths.store_path()) as store:
        done = ci.import_claude(store, found, with_titles=args.with_titles, track=track_project)
    print(f"\ntracked {done['tracked']} folders, imported {done['sessions']} sessions. Run `workgraph build` to ingest them.")


def _sync_store():
    from .store import Store
    return Store(paths.store_path())


def _print_report(rep: dict) -> None:
    for k, v in rep.items():
        if v not in ([], 0, None, {}):
            print(f"  {k}: {', '.join(v) if isinstance(v, list) else v}")


def cmd_dump(args) -> None:
    from . import syncing
    parts = syncing.parse_parts(args.with_parts)
    with _sync_store() as store:
        counts = syncing.export_archive(Path(args.out), parts, store)
    print(f"wrote {args.out}  (parts: {', '.join(sorted(parts))})")
    _print_report(counts)
    print("The archive holds your authored graph data as text, never the database. Treat it as private.")


def cmd_load(args) -> None:
    from . import syncing
    with _sync_store() as store:
        rep = syncing.import_archive(Path(args.file), store)
    print(f"imported {args.file}")
    _print_report(rep)
    if rep.get("unresolved"):
        print("projects with no folder on this machine: " + ", ".join(rep["unresolved"])
              + "\n  bind each with `workgraph track <folder>`, or clone them with `workgraph sync pull --clone-missing`")


def cmd_sync(args) -> None:
    from . import syncing
    if args.action == "init":
        if not args.url:
            raise SystemExit("workgraph: sync init needs the URL of a private git repository")
        status = syncing.sync_init(args.url, syncing.parse_parts(args.with_parts), args.confirm_private)
        print(f"sync repository ready in {paths.sync_dir()}: {args.url} ({status})\n"
              f"parts: {', '.join(sorted(syncing.parse_parts(args.with_parts)))}. The database is never inside it.")
    elif args.action == "status":
        st = syncing.sync_status()
        print(f"remote: {st['remote']}\nparts:  {', '.join(st['parts'])}")
        print(f"ahead {st['ahead']}, behind {st['behind']}" if st["ahead"] is not None else "remote not reachable")
        print(f"uncommitted: {len(st['uncommitted'])}")
    else:
        with _sync_store() as store:
            if args.action == "push":
                res = syncing.sync_push(store, args.message)
                print("pushed" if res["pushed"] else "nothing to push")
                _print_report(res["counts"])
            else:
                rep = syncing.sync_pull(store, args.clone_missing)
                print("pulled")
                _print_report(rep)
                if rep.get("unresolved"):
                    print("projects with no folder here: " + ", ".join(rep["unresolved"])
                          + "\n  `workgraph track <folder>` binds one, `workgraph sync pull --clone-missing` clones them")


def cmd_hook(args) -> None:
    import sys
    from .claude_code import handle
    text = handle(args.event, sys.stdin.read())
    if text:
        print(text)


def cmd_claude_setup(args) -> None:
    import json
    from .claude_code import hook_settings, install_command, install_hooks
    if not args.write:
        print("Plain settings (hooks + the /workgraph command). Add this to ~/.claude/settings.json,\n"
              "or run `workgraph utils claude-setup --write` to merge it and install the command.\n"
              "(A plugin version exists in plugin/ but is not published yet; try it with `claude --plugin-dir <repo>/plugin`.\n"
              "Use the plugin or these settings, not both.)\n")
        print(json.dumps(hook_settings(), indent=2))
        return
    path = Path(args.settings).expanduser()
    added = install_hooks(path)
    print(f"added hooks {added} to {path}" if added else f"hooks already present in {path}")
    cmd = install_command(path.parent / "commands")
    print(f"installed /workgraph at {path.parent / 'commands' / 'workgraph.md'}" if cmd else "/workgraph command already present")


def _docs(args):
    M = query.load_graph(args.graph)
    try:
        roots = query.roots_from_workspace(_workspace(argparse.Namespace(src=None, config=None, domain="sdlc")))
    except (FileNotFoundError, ValueError, KeyError):
        roots = {}
    docs = query.filter_docs(query.build_docs(M, roots), layer=args.layer, project=args.project, kind=args.kind)
    return M, docs


def cmd_search(args) -> None:
    """Keyword or semantic search over the last built graph."""
    M, docs = _docs(args)
    if args.semantic:
        hits, used, note = query.semantic_search(docs, args.query, args.limit, args.backend)
        print(f"({note})")
    else:
        hits = query.keyword_search(docs, args.query, args.limit)
    if not hits:
        print("no matches")
    for score, d in hits:
        where = f"{d.project}:{d.source_file}" if d.source_file else d.project
        print(f"{score:7.2f}  [{d.layer}/{d.kind}] {d.label}   ({where})")
        print(f"         id: {d.id}")


def cmd_show(args) -> None:
    M = query.load_graph(args.graph)
    node = args.node
    if node not in M:
        want = node.casefold()
        label = lambda n: str(M.nodes[n].get("label", "")).casefold()  # noqa: E731
        matches = [n for n in M if label(n) == want] or [n for n in M if label(n).startswith(want + ":")] \
            or [n for n in M if n.casefold().endswith(want)]
        if len(matches) != 1:
            hint = "" if not matches else ": " + ", ".join(matches[:5])
            raise SystemExit(f"workgraph: {'no such node' if not matches else 'ambiguous'} {node}{hint}")
        node = matches[0]
    d = M.nodes[node]
    print(node)
    for k in ("label", "kind", "layer", "project", "domain", "stages", "source_file", "status", "origin", "summary"):
        if d.get(k):
            print(f"  {k}: {d[k]}")
    nb = query.neighbors(M, node)
    for direction, arrow in (("out", "->"), ("in", "<-")):
        for rel, other, label in nb[direction]:
            if M.nodes[other].get("kind") not in query.HIDDEN_KINDS or args.all:
                print(f"  {arrow} {rel}: {label}  [{other}]")


def cmd_status(args) -> None:
    """What the last build knows: layers, projects, kinds, and the session journal."""
    graph = Path(args.graph) if args.graph else paths.out_dir() / "graph.json"
    M = query.load_graph(graph)
    s = query.summary(M)
    import time
    print(f"graph: {graph} (built {time.strftime('%Y-%m-%d %H:%M', time.localtime(graph.stat().st_mtime))})")
    print(f"{s['nodes']} nodes, {s['edges']} edges")
    print("layers:   " + ", ".join(f"{k} {v}" for k, v in sorted(s["layers"].items())))
    print("projects: " + (", ".join(f"{k} {v}" for k, v in sorted(s["projects"].items())) or "none"))
    gaps = query.gaps_for(M)
    by_rule: dict[str, int] = {}
    for g in gaps:
        by_rule[g["message"]] = by_rule.get(g["message"], 0) + 1
    print(f"gaps:     {len(gaps)}" + (f"  ({', '.join(f'{m} {n}' for m, n in sorted(by_rule.items()))})" if gaps else ""))
    ws = paths.default_workspace()
    print(f"home:     {paths.home()}  (default workspace: {'present' if ws.exists() else 'missing, run `workgraph init`'})")


def cmd_context(args) -> None:
    """The memory summary for the project at PATH (what the Claude Code session-start hook prints)."""
    from .claude_code import _project_name, context_summary, project_root
    here = Path(args.path).resolve()
    root = project_root(str(here))
    name = _project_name(here) or (_project_name(root) if root else None)   # the folder itself, else its git root
    if not name:
        raise SystemExit(f"workgraph: {here} is not a tracked project (run `workgraph track {args.path}`)")
    print(context_summary(name))


def cmd_ui(args) -> None:
    import subprocess
    import sys
    try:
        import streamlit  # noqa: F401
    except ImportError:
        raise SystemExit("workgraph: the UI needs streamlit: python -m pip install workgraph")
    app = Path(__file__).parent / "ui" / args.framework / "app.py"
    cmd = [sys.executable, "-m", "streamlit", "run", str(app), "--server.port", str(args.port)]
    if args.no_browser:
        cmd += ["--server.headless", "true"]
    cmd += ["--", "--graph", str(Path(args.graph).resolve()) if args.graph else ""]
    raise SystemExit(subprocess.call(cmd))


def cmd_viz(args) -> None:
    from . import viz
    if args.list_engines:
        for name, spec in viz.ENGINES.items():
            ok, why = spec["ready"]()
            print(f"{name:<10} {'ok ' if ok else '!! '} {spec['about']}" + ("" if ok else f"  [{why}]")
                  + ("  (default)" if name == viz.DEFAULT_ENGINE else ""))
        return
    graph = Path(args.graph) if args.graph else paths.out_dir() / "graph.json"
    if not graph.exists():
        raise SystemExit(f"workgraph: no graph at {graph}; run `workgraph build` first")
    M = query.load_graph(graph)
    try:
        out, note, n = viz.render(M, args.engine, args.out, layer=args.layer, project=args.project, kind=args.kind,
                                  stage=args.stage, node=args.node, hops=args.hops, include_code=args.all, hubs=args.hubs,
                                  max_nodes=args.max_nodes)
    except (ValueError, RuntimeError) as e:
        raise SystemExit(f"workgraph: {e}")
    print(f"{args.engine}: {n} nodes -> {out}" + (f"\n  {note}" if note else ""))
    if not args.no_open:
        viz.open_in_browser(out)


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


def _graphify_version() -> str:
    import importlib.metadata as md
    try:
        return md.version("graphifyy")
    except md.PackageNotFoundError:
        return ""


class _Help(argparse.HelpFormatter):
    """Top-level help: list the commands with their descriptions, without the {a,b,c} line argparse adds."""

    def __init__(self, prog):
        super().__init__(prog, max_help_position=24, width=100)

    def _fill_text(self, text, width, indent):          # keep paragraph breaks in the description
        import textwrap
        return "\n\n".join(textwrap.fill(p, width, initial_indent=indent, subsequent_indent=indent)
                           for p in text.split("\n\n"))

    def _format_action(self, action):
        if isinstance(action, argparse._SubParsersAction):
            return "".join(self._format_action(a) for a in action._get_subactions())
        return super()._format_action(action)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        prog="workgraph", formatter_class=_Help,
        description="A knowledge layer beside Claude Code. workgraph turns your projects, their lifecycle, products, "
                    "teams and processes into one searchable graph, and gives your Claude Code sessions a memory of it.\n\n"
                    f"Built on graphify {_graphify_version()} (https://github.com/Graphify-Labs/graphify, PyPI: graphifyy), "
                    "which supplies code and document extraction, clustering and graph export.",
        epilog="Run `workgraph COMMAND -h` for the options of one command. Guides: docs/GUIDE in the repository.")
    sub = ap.add_subparsers(dest="cmd", required=True, title="commands", metavar="COMMAND")
    utils = sub.add_parser("utils", formatter_class=_Help,
                           help="helper commands (check, claude-setup, clones, context, convert, doctor, domains, layers, show-llm, validate-pack)",
                           description="Helper commands: diagnostics, validation, conversion and setup.")
    usub = utils.add_subparsers(dest="util", required=True, title="utils", metavar="SUBCOMMAND")
    s = usub.add_parser("check", help="validate layer files and cross-graph references")
    s.add_argument("-c", "--config", help="workspace file (default: ./workspace.yaml, else <home>/workspace.yaml)")
    s.add_argument("--assign-uids", action="store_true", help="write a uid into layer nodes that lack one")
    s.set_defaults(fn=cmd_check)
    s = usub.add_parser("layers", help="show the layer graph (layers as nodes, dependencies as edges) and build counts")
    s.add_argument("-o", "--out", help="layer partitions folder (default: <home>/out/layers)")
    s.set_defaults(fn=cmd_layers)
    s = usub.add_parser("validate-pack", help="check domain packs (files or names) and list every problem")
    s.add_argument("pack", nargs="+", help="a pack file (.yaml) or the name of a built-in or custom pack")
    s.set_defaults(fn=cmd_validate_pack)
    s = usub.add_parser("convert", help="convert a layer file between JSON and YAML, keeping YAML comments")
    s.add_argument("file")
    s.add_argument("-o", "--out")
    s.add_argument("--db", help="comment store (default: <home>/workgraph.sqlite3)")
    s.set_defaults(fn=cmd_convert)
    sub.add_parser("init", help="create the workgraph home folder and a starter workspace").set_defaults(fn=cmd_init)
    s = sub.add_parser("track", help="add a project folder to the default workspace")
    s.add_argument("path", nargs="?", default=".")
    s.add_argument("--name")
    s.add_argument("--domain")
    s.set_defaults(fn=cmd_track)
    s = sub.add_parser("import-claude", help="backfill from Claude Code: track its projects, load session metadata")
    s.add_argument("--apply", action="store_true", help="do it (the default is a dry run that only prints the plan)")
    s.add_argument("--with-titles", action="store_true", help="keep Claude's one-line session titles")
    s.add_argument("--include-nested", action="store_true", help="also track folders that sit inside another project")
    s.add_argument("--projects-dir", help="default: ~/.claude/projects")
    s.set_defaults(fn=cmd_import_claude)
    s = sub.add_parser("dump", help="pack your authored graph data into a tar.gz (for a USB stick), without the database")
    s.add_argument("-o", "--out", required=True, help="e.g. workgraph.tar.gz")
    s.add_argument("--with", dest="with_parts", help="extra parts, comma separated: org, sessions, llm-cache")
    s.set_defaults(fn=cmd_dump)
    s = sub.add_parser("load", help="merge a workgraph tar.gz into this machine")
    s.add_argument("file")
    s.set_defaults(fn=cmd_load)
    s = sub.add_parser("sync", help="share your workgraph between machines through a PRIVATE git repository")
    s.add_argument("action", choices=["init", "push", "pull", "status"])
    s.add_argument("url", nargs="?", help="init: URL of the private repository")
    s.add_argument("--with", dest="with_parts", help="init: extra parts, comma separated: org, sessions, llm-cache")
    s.add_argument("--confirm-private", action="store_true", help="init: you checked that the repository is private")
    s.add_argument("--clone-missing", action="store_true", help="pull: clone projects that have no folder here")
    s.add_argument("-m", "--message", help="push: commit message")
    s.set_defaults(fn=cmd_sync)
    s = sub.add_parser("hook")   # no help: hidden from the list; Claude Code hooks call it, so the name must stay
    s.add_argument("event", choices=["session-start", "session-end"])
    s.set_defaults(fn=cmd_hook)
    s = usub.add_parser("claude-setup", help="show, or with --write install, the Claude Code hooks that feed workgraph")
    s.add_argument("--write", action="store_true", help="merge the hooks into the user-level Claude Code settings")
    s.add_argument("--settings", default="~/.claude/settings.json")
    s.set_defaults(fn=cmd_claude_setup)
    s = sub.add_parser("search", help="keyword (default) or --semantic search over the built graph")
    s.add_argument("query")
    s.add_argument("--semantic", action="store_true", help="rank by meaning: ollama or sentence-transformers embeddings, else TF-IDF")
    s.add_argument("--backend", default="auto", choices=["auto", "ollama", "sbert", "tfidf"])
    s.add_argument("--limit", type=int, default=15)
    s.add_argument("--layer"); s.add_argument("--project"); s.add_argument("--kind")
    s.add_argument("--graph", help="graph file (default: <home>/out/graph.json)")
    s.set_defaults(fn=cmd_search)
    s = sub.add_parser("show", help="one node: attributes and its links")
    s.add_argument("node", help="node id, or an exact label")
    s.add_argument("--all", action="store_true", help="include symbol and external neighbours")
    s.add_argument("--graph")
    s.set_defaults(fn=cmd_show)
    s = sub.add_parser("status", help="summary of the last built graph")
    s.add_argument("--graph")
    s.set_defaults(fn=cmd_status)
    s = usub.add_parser("context", help="what the graph knows about the project at PATH (default: current folder)")
    s.add_argument("path", nargs="?", default=".")
    s.set_defaults(fn=cmd_context)
    s = sub.add_parser("viz", help="draw the graph as a file you can open: cytoscape (default), pyvis, graphify, graphviz")
    s.add_argument("--engine", default="cytoscape", choices=["cytoscape", "pyvis", "graphify", "graphviz"],
                   help="drawing engine (default cytoscape: interactive, bundled, works offline)")
    s.add_argument("--list-engines", action="store_true", help="show the engines and whether each is ready")
    s.add_argument("--layer"); s.add_argument("--project"); s.add_argument("--kind"); s.add_argument("--stage")
    s.add_argument("--node", help="draw this node (id or exact label) and its neighbourhood")
    s.add_argument("--hops", type=int, default=2, help="neighbourhood radius for --node (default 2)")
    s.add_argument("--all", action="store_true", help="include symbol, external and rationale nodes (large)")
    s.add_argument("--hubs", action="store_true", help="keep the in_stage / belongs_to edges to stage and project hubs")
    s.add_argument("--max-nodes", type=int, default=400, help="keep the best-connected N nodes (default 400)")
    s.add_argument("-o", "--out", help="output file (default <home>/out/graph-<engine>.html)")
    s.add_argument("--no-open", action="store_true", help="write the file but do not open a browser")
    s.add_argument("--graph", help="graph file (default: <home>/out/graph.json)")
    s.set_defaults(fn=cmd_viz)
    s = sub.add_parser("ui", help="open the web app to review and search the graph")
    s.add_argument("--framework", choices=["streamlit"], default="streamlit", help="UI framework (only streamlit so far)")
    s.add_argument("--graph", help="graph file (default: <home>/out/graph.json)")
    s.add_argument("--port", type=int, default=8501)
    s.add_argument("--no-browser", action="store_true")
    s.set_defaults(fn=cmd_ui)
    usub.add_parser("domains", help="list domain packs").set_defaults(fn=cmd_domains)
    usub.add_parser("doctor", help="show python/environment/graphify details and warn about conda base").set_defaults(fn=cmd_doctor)
    s = usub.add_parser("show-llm", help="show the resolved LLM backend per processor and check readiness")
    s.add_argument("-c", "--config", help="workspace file (default: ./workspace.yaml, else <home>/workspace.yaml)")
    s.add_argument("--adapter", help="LLM backend, e.g. cc_cli (subscription) or bedrock; overrides config")
    s.add_argument("--src", action="append", metavar="URL_OR_PATH", help="check a folder or git URL instead of a workspace file")
    s.add_argument("--domain", default="sdlc", help="domain pack for --src projects (default sdlc)")
    s.set_defaults(fn=cmd_llm)
    for group, name, fn, h in ((sub, "build", cmd_build, "build the graph"), (sub, "gaps", cmd_gaps, "report gaps per domain rules"),
                               (usub, "clones", cmd_clones, "files shared across projects"),
                               (sub, "open", cmd_open, "open work items from planning docs (use with --semantic)")):
        s = group.add_parser(name, help=h)
        s.add_argument("-c", "--config", help="workspace file (default: ./workspace.yaml, else <home>/workspace.yaml)")
        s.add_argument("--adapter", help="LLM backend, e.g. cc_cli (subscription) or bedrock; overrides config")
        s.add_argument("--semantic", action="store_true", help="also run LLM processors (needs a backend/API key)")
        s.add_argument("--src", action="append", metavar="URL_OR_PATH",
                       help="build from a local folder or a git URL instead of a workspace file (repeatable)")
        s.add_argument("--domain", default="sdlc", help="domain pack for --src projects (default sdlc)")
        s.add_argument("-o", "--out", help="output folder (default: <home>/out)")
        s.add_argument("-q", "--quiet", action="store_true", help="no progress or timing output")
        s.set_defaults(fn=fn)
    for g in (sub, usub):
        g._choices_actions.sort(key=lambda a: a.dest)    # list the commands alphabetically in --help
    args = ap.parse_args(argv)
    if getattr(args, "adapter", None):
        try:
            resolve_backend(args.adapter)                   # fail fast on a typo
        except ValueError as e:
            raise SystemExit(f"workgraph: {e}")
        os.environ["WORKGRAPH_BACKEND"] = args.adapter     # same effect as exporting it
    try:
        args.fn(args)
    except (ValueError, RuntimeError, FileNotFoundError) as e:   # SyncError is a RuntimeError
        raise SystemExit(f"workgraph: {e}")


if __name__ == "__main__":
    main()
