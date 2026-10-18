# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`work-graph` is a thin layer over [graphify](https://github.com/Graphify-Labs/graphify) (PyPI package `graphifyy`, used as a library) that builds a NetworkX knowledge graph of the software lifecycle and surrounding knowledge. Behavior is adapted per **domain pack** (YAML). Early hackathon prototype; see README.md for vision and roadmap (`ask`, visualization, graph reload are not implemented yet).

## Commands

```bash
conda create -n graf python=3.11 pip -y && conda activate graf   # dedicated env; always use `python -m pip`
python -m pip install -e ".[dev]"      # extras: dev, bedrock, leiden, mcp
work-graph doctor                      # warns [!!] if running in conda base

python -m pytest                       # all tests (tests/)
python -m pytest tests/test_pipeline.py::test_name   # single test

work-graph domains
work-graph build -c examples/workspace.yaml          # writes work-graph-out/graph.json
work-graph gaps  -c examples/workspace.yaml
work-graph clones -c examples/workspace.yaml
work-graph check -c examples/workspace.yaml          # layer files + cross-graph references
work-graph convert examples/graphs/org.json          # JSON <-> YAML via the comment store
work-graph open --semantic --adapter cc_cli -c examples/acme-shop/workspace.yaml   # LLM work items
work-graph llm -c workspace.yaml       # resolved backend / billing / readiness per processor
```

No linter is configured. LLM processors only run with `--semantic`; the structural build needs no LLM.

## Architecture

Pipeline (`src/work_graph/pipeline.py::build_workspace`): for each project in a `Workspace` → `processors.run_processors` (per artifact type) → tag nodes with stages (`_tag`) → resolve cross-references (`_resolve_refs`) → `links.cross_project_links` → `derive_flow_edges` → graphify `build`/`cluster`. Clustering/analysis run on `knowledge_layer()` only, since stage/project hub nodes would skew it.

- **Domain packs** (`src/work_graph/domains/{sdlc,research,publishing}.yaml`, loaded by `domain.py`) have two halves: *vocabulary* (`stages`, `flow_relations`, `expectations` for gap rules, path `classifiers` → stage tags, `ignore`) and *processing* (artifact type → processor + LLM backend). Adding a domain = adding a YAML file (packaged via `package-data`). A workspace can mix domains per project.
- **Workspace files** (`workspace.yaml`, see `examples/`) list projects, each with a domain, optional `backend:` and `ignore:`.
- **Processors** (`processors.py`): `ast` (tree-sitter via graphify), `frontmatter`, `annotations` (`# verifies: REQ-1`), `llm`, `llm_items`. Relation names in frontmatter/annotations come from the pack's `flow_relations`; a test importing a code file yields an `INFERRED` `verifies` edge derived from those rules, not hardcoded. File walking honors `.gitignore`, `.graphifyignore`, the pack's `ignore`, and the project's `ignore`; symlinks leaving the project root are skipped.
- **Graph model**: process layer (`stage:<domain>:<id>`, `project:<name>` nodes) plus knowledge layer. Nodes carry `kind`, `project`, and `stages` (a *list* of tags validated against the pack). Edges carry `relation` and `confidence` (`EXTRACTED` / `INFERRED` / `AMBIGUOUS`). LLM-extracted items are `origin: llm`, `INFERRED`, and skipped by gap rules unless `include_llm`.
- **LLM adapter** (`llm.py`): backend resolution order is explicit arg → `WORK_GRAPH_BACKEND` env → project `backend:` → processor `backend:` in pack → default `cc_cli`. `cc_cli.py` is a subscription-only Claude Code CLI backend that deliberately strips `ANTHROPIC_API_KEY`/Bedrock/etc. env vars so it never bills the API; do not switch it to pass the environment through. Env knobs: `WORK_GRAPH_CC_MODEL`, `WORK_GRAPH_CC_TIMEOUT`. A `BackendLimitError` stops the LLM pass with a warning rather than failing the build; results are cached by backend + prompt hash.
- `analyze.py::find_gaps` evaluates pack `expectations` as graph queries (plus broken references); `cli.py` is the argparse entry point (`work_graph.cli:main`).

`examples/acme-shop` (sdlc) and `examples/rq-demo` (research) contain deliberate gaps that tests and demos rely on; changing them can break tests.

**Layers** (design: `docs/DEV/README-design.md`): besides the repo graph, `graphs/*.json` next to the workspace file hold org/product/process layers (`schema.py` validates; ids are `<layer>:<path>`, nodes carry a `uid` used only for rename hints). `refs.merge_layers` runs at the end of `build_workspace`: it adds `process:<domain>` nodes from the packs, resolves cross-layer edges (including `/**` sub-graph endpoints) and tags every node with `layer`. JSON is canonical and what the loader reads; YAML is for review. `convert.py` moves between them, saving YAML comments in `graphs/store.sqlite` (`store.py`) anchored by item id so they are restored on JSON -> YAML.

Further docs: `docs/GUIDE/SETUP.md` (install, backends, troubleshooting) and `docs/GUIDE/USER-GUIDE.md` (workspace files, linking conventions, customizing packs).
